"""Immutable rental events; writes belong to the narrow internal services."""

import uuid
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.utils import NotSupportedError
from django.utils import timezone

from .managers import ServiceOnlyManager


MAX_AMOUNT = Decimal("9999999999.99")


def _validate_aware_times(instance, names):
    errors = {}
    for name in names:
        value = getattr(instance, name)
        if value is not None and (
            not isinstance(value, datetime) or timezone.is_naive(value)
        ):
            errors[name] = "Use a timezone-aware timestamp."
    if errors:
        raise ValidationError(errors)


class Rental(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.OneToOneField(
        "reservations.Reservation",
        on_delete=models.PROTECT,
        related_name="rental",
    )
    product_item = models.ForeignKey(
        "inventory.ProductItem", on_delete=models.PROTECT, related_name="rentals"
    )
    tariff = models.ForeignKey(
        "catalog.ProductTariff", on_delete=models.PROTECT, related_name="rentals"
    )
    payment_method = models.ForeignKey(
        "payments.PaymentMethod", on_delete=models.PROTECT, related_name="rentals"
    )
    hourly_rate_applied = models.DecimalField(max_digits=12, decimal_places=2)
    started_by_employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="started_rentals",
    )
    ended_by_employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="ended_rentals",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    final_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    objects = ServiceOnlyManager()

    class Meta:
        db_table = "rental"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "completed"]),
                name="rental_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="active",
                        ended_at__isnull=True,
                        final_amount__isnull=True,
                        ended_by_employee__isnull=True,
                    )
                    | models.Q(
                        status="completed",
                        ended_at__isnull=False,
                        final_amount__isnull=False,
                        ended_by_employee__isnull=False,
                        ended_at__gte=models.F("started_at"),
                    )
                ),
                name="rental_state_dates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    hourly_rate_applied__gt=0,
                    hourly_rate_applied__lte=MAX_AMOUNT,
                ),
                name="rental_rate_positive_finite",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(final_amount__isnull=True)
                    | models.Q(final_amount__gte=0, final_amount__lte=MAX_AMOUNT)
                ),
                name="rental_final_amount_finite_nonnegative",
            ),
            models.UniqueConstraint(
                fields=["product_item"],
                condition=models.Q(status="active"),
                name="rental_one_active_item",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_aware_times(self, ("started_at", "ended_at"))

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use the rental services to save rental history.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Rental history cannot be deleted.")


class Inspection(models.Model):
    class Type(models.TextChoices):
        PRE_ISSUE = "pre_issue", "Before issue"
        RETURN = "return", "Return"

    id = models.BigAutoField(primary_key=True)
    product_item = models.ForeignKey(
        "inventory.ProductItem",
        on_delete=models.PROTECT,
        related_name="inspections",
    )
    employee = models.ForeignKey(
        "accounts.Employee", on_delete=models.PROTECT, related_name="inspections"
    )
    inspection_type = models.CharField(max_length=20, choices=Type.choices)
    reservation = models.ForeignKey(
        "reservations.Reservation",
        on_delete=models.PROTECT,
        related_name="inspections",
        null=True,
        blank=True,
    )
    rental = models.ForeignKey(
        Rental,
        on_delete=models.PROTECT,
        related_name="inspections",
        null=True,
        blank=True,
    )
    condition_ok = models.BooleanField()
    completeness_ok = models.BooleanField()
    comment = models.TextField(null=True, blank=True)
    customer_confirmed_at = models.DateTimeField(null=True, blank=True)
    inspected_at = models.DateTimeField()

    objects = ServiceOnlyManager()

    class Meta:
        db_table = "inspection"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(inspection_type__in=["pre_issue", "return"]),
                name="inspection_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        inspection_type="pre_issue",
                        reservation__isnull=False,
                        rental__isnull=True,
                    )
                    | models.Q(
                        inspection_type="return",
                        reservation__isnull=True,
                        rental__isnull=False,
                    )
                ),
                name="inspection_parent_pair_valid",
            ),
            models.UniqueConstraint(
                fields=["rental"],
                condition=models.Q(inspection_type="return"),
                name="inspection_one_return_per_rental",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_aware_times(self, ("customer_confirmed_at", "inspected_at"))

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use the rental services to record an inspection.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Inspection history cannot be deleted.")


class InspectionProblem(models.Model):
    class Type(models.TextChoices):
        DAMAGE = "damage", "Damage"
        MALFUNCTION = "malfunction", "Malfunction"
        MISSING_COMPONENT = "missing_component", "Missing component"
        OTHER = "other", "Other"

    id = models.BigAutoField(primary_key=True)
    inspection = models.ForeignKey(
        Inspection, on_delete=models.PROTECT, related_name="problems"
    )
    problem_type = models.CharField(max_length=30, choices=Type.choices)
    description = models.TextField()
    product_component = models.ForeignKey(
        "catalog.ProductComponent",
        on_delete=models.PROTECT,
        related_name="inspection_problems",
        null=True,
        blank=True,
    )
    missing_quantity = models.IntegerField(null=True, blank=True)

    objects = ServiceOnlyManager()

    class Meta:
        db_table = "inspection_problem"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    problem_type__in=[
                        "damage", "malfunction", "missing_component", "other"
                    ]
                ),
                name="inspection_problem_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        problem_type="missing_component",
                        product_component__isnull=False,
                        missing_quantity__isnull=False,
                        missing_quantity__gt=0,
                    )
                    | models.Q(
                        problem_type__in=["damage", "malfunction", "other"],
                        product_component__isnull=True,
                        missing_quantity__isnull=True,
                    )
                ),
                name="inspection_problem_component_quantity_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValidationError(
                {"description": "Provide a non-empty problem description."}
            )

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use the rental services to record problems.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Inspection problems cannot be deleted.")
