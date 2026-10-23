"""Catalog validation without status or attribute-type vocabularies."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


# The largest finite value representable by the physical NUMERIC(12,2) column.
MAX_HOURLY_RATE = Decimal("9999999999.99")


def validate_product_status(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Provide a non-empty product status.")


def validate_positive_hourly_rate(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(
            "Provide a finite positive hourly rate."
        ) from None
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Provide a finite positive hourly rate.")
