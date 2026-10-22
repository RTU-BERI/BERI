"""Reservation records and their immutable assignment journal."""

import uuid

from django.conf import settings
from django.db import models
from django.db.utils import NotSupportedError

from .managers import ServiceOnlyManager


class Reservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    pickup_point = models.ForeignKey(
        "pickup_points.PickupPoint",
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    initial_product_item = models.ForeignKey(
        "inventory.ProductItem",
        on_delete=models.PROTECT,
        related_name="initial_reservations",
    )
    assigned_product_item = models.ForeignKey(
        "inventory.ProductItem",
        on_delete=models.PROTECT,
        related_name="assigned_reservations",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=30, choices=Status.choices)
    completion_reason = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = ServiceOnlyManager()

    class Meta:
        db_table = "reservation"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["active", "fulfilled", "cancelled", "expired"]
                ),
                name="reservation_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="reservation_expiry_after_creation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="active",
                        cancelled_at__isnull=True,
                        completed_at__isnull=True,
                        completion_reason__isnull=True,
                    )
                    | models.Q(
                        status="cancelled",
                        cancelled_at__isnull=False,
                        completed_at__isnull=False,
                        cancelled_at=models.F("completed_at"),
                        completed_at__gte=models.F("created_at"),
                    )
                    | models.Q(
                        status="expired",
                        cancelled_at__isnull=True,
                        completed_at__isnull=False,
                        completed_at__gte=models.F("expires_at"),
                        completion_reason__isnull=True,
                    )
                    | models.Q(
                        status="fulfilled",
                        cancelled_at__isnull=True,
                        completed_at__isnull=False,
                        completed_at__gte=models.F("created_at"),
                    )
                ),
                name="reservation_state_dates_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use the reservation services to save a reservation.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Reservation history cannot be deleted.")


class ReservationItemAssignment(models.Model):
    id = models.BigAutoField(primary_key=True)
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    product_item = models.ForeignKey(
        "inventory.ProductItem",
        on_delete=models.PROTECT,
        related_name="reservation_assignments",
    )
    assigned_by_employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="reservation_assignments",
        null=True,
        blank=True,
    )
    sequence_no = models.IntegerField()
    reason = models.CharField(max_length=30, null=True, blank=True)
    assigned_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)

    objects = ServiceOnlyManager()

    class Meta:
        db_table = "reservation_item_assignment"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "sequence_no"],
                name="assignment_reservation_sequence_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence_no__gt=0),
                name="assignment_sequence_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(released_at__isnull=True)
                    | models.Q(
                        released_at__isnull=False,
                        released_at__gte=models.F("assigned_at"),
                    )
                ),
                name="assignment_release_not_before_assign",
            ),
            models.UniqueConstraint(
                fields=["reservation"],
                condition=models.Q(released_at__isnull=True),
                name="reservation_one_open_assignment",
            ),
            models.UniqueConstraint(
                fields=["product_item"],
                condition=models.Q(released_at__isnull=True),
                name="product_item_one_open_assignment",
            ),
        ]

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use reservation services to save an assignment.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Assignment history cannot be deleted.")
