"""Saved provider identifiers and safe display metadata, without card data."""

import re
import uuid
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.utils import NotSupportedError
from django.utils import timezone

from . import privacy
from .managers import FinancialHistoryManager, PaymentMethodManager


class PaymentMethod(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payment_methods",
    )
    external_id = models.CharField(max_length=255)
    brand = models.CharField(max_length=50, null=True, blank=True)
    last4 = models.CharField(max_length=4, null=True, blank=True)
    expires_month = models.SmallIntegerField(null=True, blank=True)
    expires_year = models.SmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField()
    status = models.CharField(max_length=20, choices=Status.choices)
    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices
    )
    created_at = models.DateTimeField()
    deactivated_at = models.DateTimeField(null=True, blank=True)

    objects = PaymentMethodManager()

    class Meta:
        db_table = "payment_method"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "inactive"]),
                name="payment_method_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    verification_status__in=["pending", "confirmed", "failed"]
                ),
                name="payment_method_verification_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="active", deactivated_at__isnull=True)
                    | models.Q(
                        status="inactive",
                        deactivated_at__isnull=False,
                        deactivated_at__gte=models.F("created_at"),
                    )
                ),
                name="payment_method_deactivation_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_default=False)
                    | models.Q(
                        is_default=True,
                        status="active",
                        verification_status="confirmed",
                    )
                ),
                name="payment_method_default_usable",
            ),
            models.UniqueConstraint(
                fields=["customer_account"],
                condition=models.Q(is_default=True),
                name="payment_method_one_default",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(last4__isnull=True)
                    | models.Q(last4__regex=r"^[0-9]{4}$")
                ),
                name="payment_method_last4_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(expires_month__isnull=True)
                    | models.Q(expires_month__gte=1, expires_month__lte=12)
                ),
                name="payment_method_month_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(expires_year__isnull=True)
                    | models.Q(expires_year__gte=1, expires_year__lte=9999)
                ),
                name="payment_method_year_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        expires_month__isnull=True, expires_year__isnull=True
                    )
                    | models.Q(
                        expires_month__isnull=False, expires_year__isnull=False
                    )
                ),
                name="payment_method_expiry_pair_valid",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        token = self.external_id
        if not isinstance(token, str) or not token.strip() or len(token) > 255:
            errors["external_id"] = ValidationError(
                "Provide a non-empty opaque identifier of at most 255 characters.",
                code="invalid_external_id",
            )
        if self.last4 is not None and (
            not isinstance(self.last4, str)
            or re.fullmatch(r"[0-9]{4}", self.last4) is None
        ):
            errors["last4"] = "Provide four ASCII digits or leave the field unset."
        for field, maximum in (("expires_month", 12), ("expires_year", 9999)):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, int) or not 1 <= value <= maximum
            ):
                errors[field] = "Provide a value within the supported range."
        if (self.expires_month is None) != (self.expires_year is None):
            errors["expires_month"] = "Provide both expiration fields or neither."
            errors["expires_year"] = "Provide both expiration fields or neither."
        for field in ("created_at", "deactivated_at"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, datetime) or timezone.is_naive(value)
            ):
                errors[field] = "Provide a timezone-aware timestamp."
        if errors:
            raise ValidationError(errors)

    def full_clean(self, *args, **kwargs):
        return privacy.safe_full_clean(self, *args, **kwargs)

    def save(self, *args, **kwargs):
        raise NotSupportedError("Use the payment-method services to save records.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Payment-method history cannot be deleted.")

    def __str__(self):
        return str(self.pk)

    def __repr__(self):
        return f"<PaymentMethod: {self.pk}>"


_MAX_FINANCIAL_AMOUNT = Decimal("9999999999.99")


def _validate_financial_times(instance, names):
    errors = {}
    for name in names:
        value = getattr(instance, name)
        if value is not None and (
            not isinstance(value, datetime) or timezone.is_naive(value)
        ):
            errors[name] = "Provide a timezone-aware financial timestamp."
    if errors:
        raise ValidationError(errors)


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rental = models.ForeignKey(
        "rentals.Rental", on_delete=models.PROTECT, related_name="payments"
    )
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.PROTECT, related_name="payments"
    )
    external_id = models.CharField(max_length=255, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices)
    failure_code = models.CharField(max_length=100, null=True, blank=True)
    failure_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    objects = FinancialHistoryManager()

    class Meta:
        db_table = "payment"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "succeeded", "failed"]),
                name="payment_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending",
                        resolved_at__isnull=True,
                        failure_code__isnull=True,
                        failure_message__isnull=True,
                    )
                    | models.Q(
                        status="succeeded",
                        resolved_at__isnull=False,
                        resolved_at__gte=models.F("created_at"),
                        failure_code__isnull=True,
                        failure_message__isnull=True,
                    )
                    | models.Q(
                        status="failed",
                        resolved_at__isnull=False,
                        resolved_at__gte=models.F("created_at"),
                    )
                ),
                name="payment_state_dates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    amount__gt=0, amount__lte=_MAX_FINANCIAL_AMOUNT
                ),
                name="payment_amount_positive_finite",
            ),
            models.UniqueConstraint(
                fields=["rental"],
                condition=models.Q(status="pending"),
                name="payment_one_pending_per_rental",
            ),
            models.UniqueConstraint(
                fields=["rental"],
                condition=models.Q(status="succeeded"),
                name="payment_one_succeeded_per_rental",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_financial_times(self, ("created_at", "resolved_at"))
        value = self.external_id
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 255
        ):
            raise ValidationError(
                {"external_id": "Provide a nonblank opaque ID or leave it unset."}
            )

    def full_clean(self, *args, **kwargs):
        return privacy.safe_financial_full_clean(self, *args, **kwargs)

    def save(self, *args, **kwargs):
        raise NotSupportedError("Payment writes require a trusted financial service.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Payment history cannot be deleted.")

    def __str__(self):
        return str(self.pk)

    def __repr__(self):
        return f"<Payment: {self.pk}>"


class Debt(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAID = "paid", "Paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rental = models.OneToOneField(
        "rentals.Rental", on_delete=models.PROTECT, related_name="debt"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices)
    created_at = models.DateTimeField()
    settled_at = models.DateTimeField(null=True, blank=True)

    objects = FinancialHistoryManager()

    class Meta:
        db_table = "debt"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "paid"]),
                name="debt_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="open", settled_at__isnull=True)
                    | models.Q(
                        status="paid",
                        settled_at__isnull=False,
                        settled_at__gte=models.F("created_at"),
                    )
                ),
                name="debt_state_dates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    amount__gt=0, amount__lte=_MAX_FINANCIAL_AMOUNT
                ),
                name="debt_amount_positive_finite",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_financial_times(self, ("created_at", "settled_at"))

    def full_clean(self, *args, **kwargs):
        return privacy.safe_financial_full_clean(self, *args, **kwargs)

    def save(self, *args, **kwargs):
        raise NotSupportedError("Debt writes require a trusted financial service.")

    def delete(self, *args, **kwargs):
        raise NotSupportedError("Debt history cannot be deleted.")

    def __str__(self):
        return str(self.pk)

    def __repr__(self):
        return f"<Debt: {self.pk}>"
