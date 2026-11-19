"""Transactional creation and expiry of reservations on PostgreSQL."""

from datetime import datetime, timedelta
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.accounts.models import Account
from apps.catalog.models import Product
from apps.inventory.models import ProductItem
from apps.pickup_points.models import PickupPoint
from apps.rentals.models import Rental

from .models import Reservation, ReservationItemAssignment

RESERVATION_DURATION = timedelta(minutes=60)
MAX_BIGINT_ID = (1 << 63) - 1


class ReservationConflict(Exception):
    """The requested stock is unavailable or the request ID is already in use."""


def _validate_target(model, pk, field):
    if (
        type(pk) is not int
        or not 1 <= pk <= MAX_BIGINT_ID
        or not model.objects.filter(pk=pk).exists()
    ):
        raise ValidationError({field: "Укажите существующий объект."})


def _active_rentals_for_item(item_id):
    return Rental.objects.filter(product_item_id=item_id, status=Rental.Status.ACTIVE)


def _set_item_status(item, status, now):
    # The caller holds the item lock and owns the lifecycle transition.
    item.status = status
    item.status_reason = None
    item.status_changed_at = now
    models.Model.save(
        item,
        update_fields=(
            "status",
            "status_reason",
            "status_changed_at",
            "updated_at",
        ),
    )


def create_reservation(
    *,
    customer_account_id: UUID,
    product_id: int,
    pickup_point_id: int,
    request_id: UUID
) -> Reservation:
    """Reserve one free item. Reuse the UUID for retries of the same request.

    The account UUID must come from the authenticated caller. A retry returns
    the original record, including its current persisted status and expiry.
    """
    if not isinstance(customer_account_id, UUID):
        raise PermissionDenied("Для бронирования нужен активный аккаунт клиента.")
    if not isinstance(request_id, UUID):
        raise ValidationError({"request_id": "Укажите UUID запроса."})

    try:
        with transaction.atomic():
            # Serialize retries by the same account and pin its role and active flag.
            account = (
                Account.objects.select_for_update(no_key=True)
                .filter(
                    pk=customer_account_id,
                    is_active=True,
                    account_type=Account.AccountType.CUSTOMER,
                )
                .first()
            )
            if account is None:
                raise PermissionDenied(
                    "Для бронирования нужен активный аккаунт клиента."
                )

            _validate_target(Product, product_id, "product_id")
            _validate_target(PickupPoint, pickup_point_id, "pickup_point_id")
            existing = (
                Reservation.objects.select_related("initial_product_item")
                .filter(
                    pk=request_id,
                )
                .first()
            )
            if existing is not None:
                if (
                    existing.customer_account_id == account.pk
                    and existing.pickup_point_id == pickup_point_id
                    and existing.initial_product_item.product_id == product_id
                ):
                    return existing
                raise ReservationConflict("UUID запроса уже использован.")

            open_assignments = ReservationItemAssignment.objects.filter(
                product_item_id=models.OuterRef("pk"),
                released_at__isnull=True,
            )
            item = (
                ProductItem.objects.select_for_update()
                .filter(
                    ~models.Exists(open_assignments),
                    ~models.Exists(_active_rentals_for_item(models.OuterRef("pk"))),
                    product_id=product_id,
                    pickup_point_id=pickup_point_id,
                    status=ProductItem.Status.AVAILABLE,
                )
                .order_by("pk")
                .first()
            )
            if item is None:
                raise ReservationConflict(
                    "В этом пункте выдачи нет свободных экземпляров."
                )

            now = timezone.now()
            reservation = Reservation(
                id=request_id,
                customer_account=account,
                pickup_point_id=pickup_point_id,
                initial_product_item=item,
                assigned_product_item=item,
                status=Reservation.Status.ACTIVE,
                created_at=now,
                expires_at=now + RESERVATION_DURATION,
            )
            assignment = ReservationItemAssignment(
                reservation=reservation,
                product_item=item,
                sequence_no=1,
                assigned_at=now,
            )
            # These models allow writes only through services like this one.
            models.Model.save(reservation, force_insert=True)
            models.Model.save(assignment, force_insert=True)
            _set_item_status(item, ProductItem.Status.RESERVED, now)
            return reservation
    except IntegrityError as error:
        constraint = getattr(
            getattr(error.__cause__, "diag", None), "constraint_name", None
        )
        if constraint == "product_item_one_open_assignment":
            raise ReservationConflict(
                "Экземпляр уже закреплён за другой бронью."
            ) from error
        if constraint == "reservation_pkey":
            raise ReservationConflict("UUID запроса уже использован.") from error
        raise


def is_reservation_active(
    reservation: Reservation, *, at: datetime | None = None
) -> bool:
    """Check the status and deadline on a fresh record, under the caller's write locks."""
    at = timezone.now() if at is None else at
    return (
        reservation.status == Reservation.Status.ACTIVE and at < reservation.expires_at
    )


@transaction.atomic
def expire_reservation(reservation_id: UUID) -> bool:
    """Close a due reservation and its open assignment once, keeping their history."""
    candidate = (
        Reservation.objects.filter(
            pk=reservation_id,
            status=Reservation.Status.ACTIVE,
            expires_at__lte=timezone.now(),
        )
        .values("assigned_product_item_id")
        .first()
    )
    if candidate is None:
        return False

    # All assignment writers must lock the item before the reservation.
    item = (
        ProductItem.objects.select_for_update()
        .filter(
            pk=candidate["assigned_product_item_id"],
        )
        .first()
    )
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    now = timezone.now()
    if reservation.status != Reservation.Status.ACTIVE or is_reservation_active(
        reservation, at=now
    ):
        return False
    if item is None or reservation.assigned_product_item_id != item.pk:
        raise ValidationError("Не удалось подтвердить назначенный экземпляр.")

    assignment = (
        ReservationItemAssignment.objects.select_for_update()
        .filter(
            reservation=reservation,
            product_item=item,
            released_at__isnull=True,
        )
        .first()
    )
    if (
        assignment is None
        or assignment.assigned_at > now
        or item.pickup_point_id != reservation.pickup_point_id
        or item.status
        not in {ProductItem.Status.RESERVED, ProductItem.Status.UNAVAILABLE}
        or _active_rentals_for_item(item.pk).exists()
    ):
        raise ValidationError(
            "Состояние экземпляра или назначения расходится с бронью."
        )

    assignment.released_at = now
    models.Model.save(assignment, update_fields=("released_at",))
    reservation.status = Reservation.Status.EXPIRED
    reservation.completed_at = now
    models.Model.save(reservation, update_fields=("status", "completed_at"))
    # A damaged item stays unavailable even after its assignment is closed.
    if item.status == ProductItem.Status.RESERVED:
        _set_item_status(item, ProductItem.Status.AVAILABLE, now)
    return True

@transaction.atomic
def cancel_reservation(reservation_id: UUID) -> bool:
    """Cancel an active reservation and release its assigned item once."""
    candidate = (
        Reservation.objects.filter(
            pk=reservation_id,
            status=Reservation.Status.ACTIVE,
        )
        .values("assigned_product_item_id")
        .first()
    )
    if candidate is None:
        return False

    item_id = candidate["assigned_product_item_id"]
    if item_id is None:
        raise ValidationError("У бронирования отсутствует назначенный экземпляр.")

    # Lock order: item first, reservation second.
    item = (
        ProductItem.objects.select_for_update()
        .filter(pk=item_id)
        .first()
    )
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)

    if reservation.status != Reservation.Status.ACTIVE:
        return False

    now = timezone.now()

    if not is_reservation_active(reservation, at=now):
        raise ReservationConflict("Срок бронирования истёк.")

    if (
        item is None
        or reservation.assigned_product_item_id != item.pk
        or item.pickup_point_id != reservation.pickup_point_id
        or item.status
        not in {ProductItem.Status.RESERVED, ProductItem.Status.UNAVAILABLE}
        or _active_rentals_for_item(item.pk).exists()
    ):
        raise ValidationError(
            "Состояние экземпляра не позволяет отменить бронирование."
        )

    assignment = (
        ReservationItemAssignment.objects.select_for_update()
        .filter(
            reservation=reservation,
            product_item=item,
            released_at__isnull=True,
        )
        .first()
    )

    if assignment is None:
        raise ValidationError("Не найдена активная запись назначения экземпляра.")

    assignment.released_at = now
    models.Model.save(assignment, update_fields=("released_at",))

    reservation.status = Reservation.Status.CANCELLED
    reservation.cancelled_at = now
    reservation.completed_at = now
    models.Model.save(
        reservation,
        update_fields=("status", "cancelled_at", "completed_at"),
    )

    # Damaged items must remain unavailable.
    if item.status == ProductItem.Status.RESERVED:
        _set_item_status(item, ProductItem.Status.AVAILABLE, now)

    return True
