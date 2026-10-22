"""Atomic placement; SQL/private writes can bypass synchronization."""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import router, transaction
from django.utils import timezone

from apps.catalog.models import Product
from apps.pickup_points.models import PickupPoint

from ._internal import (
    close_location_history as _close_location_history,
    insert_location_history as _insert_location_history,
    write_current_location as _write_current_location,
)
from .models import ProductItem, ProductItemLocationHistory, _location_state


def _database(using, instance=None):
    return (
        using
        or getattr(getattr(instance, "_state", None), "db", None)
        or router.db_for_write(ProductItem)
    )


def _existing(model, value, using, field_name):
    pk = value.pk if isinstance(value, model) else value
    try:
        return model._base_manager.using(using).get(pk=pk)
    except (model.DoesNotExist, ValidationError, ValueError, TypeError):
        raise ValidationError(
            {field_name: "Choose an existing related object."}
        ) from None


def create_item(
    *, product, inventory_code, status, pickup_point=None,
    status_reason=None, using=None
):
    using = _database(using, product)
    with transaction.atomic(using=using):
        product = _existing(Product, product, using, "product")
        item = ProductItem.objects.using(using).create(
            product=product,
            inventory_code=inventory_code,
            status=status,
            status_reason=status_reason,
        )
        if pickup_point is not None:
            return set_location(item, pickup_point, using=using)
        return item


def set_location(item, pickup_point, *, using=None):
    using = _database(using, item)
    pk = item.pk if isinstance(item, ProductItem) else item
    with transaction.atomic(using=using):
        try:
            current = (
                ProductItem._base_manager.using(using)
                .select_for_update()
                .get(pk=pk)
            )
        except (
            ProductItem.DoesNotExist, ValidationError, ValueError, TypeError
        ):
            raise ValidationError(
                {"product_item": "Choose an existing inventory item."}
            ) from None
        histories = list(
            ProductItemLocationHistory._base_manager.using(using)
            .select_for_update()
            .filter(product_item_id=current.pk)
            .order_by("valid_from", "pk")
        )
        destination = (
            None if pickup_point is None
            else _existing(PickupPoint, pickup_point, using, "pickup_point")
        )
        now = timezone.now()
        opened = _location_state(current, histories, now)
        destination_id = None if destination is None else destination.pk
        if current.pickup_point_id == destination_id:
            return current
        assignment_model = apps.get_model(
            "reservations", "ReservationItemAssignment"
        )
        rental_model = apps.get_model("rentals", "Rental")
        # All supported assignment writers first lock this same item. Do not
        # lock Reservation here: close operations lock Item before Reservation.
        if (
            current.status in {ProductItem.Status.RESERVED, ProductItem.Status.RENTED}
            or assignment_model._base_manager.using(using).filter(
                product_item_id=current.pk, released_at__isnull=True
            ).exists()
            or rental_model._base_manager.using(using).filter(
                product_item_id=current.pk, status="active"
            ).exists()
        ):
            raise ValidationError(
                {"pickup_point": "An assigned or reserved item cannot move."}
            )
        if opened is not None:
            if now <= opened.valid_from:
                raise ValidationError(
                    {"valid_to": "The new event must be later than placement."}
                )
            _close_location_history(opened, now, using=using)
        if destination is not None:
            _insert_location_history(current, destination, now, using=using)
        _write_current_location(current, destination, using=using)
        current.refresh_from_db(using=using)
        histories = list(
            ProductItemLocationHistory._base_manager.using(using).filter(
                product_item_id=current.pk
            )
        )
        _location_state(current, histories, timezone.now())
        return current


def clear_location(item, *, using=None):
    return set_location(item, None, using=using)
