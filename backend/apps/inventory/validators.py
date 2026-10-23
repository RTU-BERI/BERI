"""Approved inventory validation without code normalization."""

from django.core.exceptions import ValidationError


def validate_inventory_code(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Provide a non-empty inventory code.")
