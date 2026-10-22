"""Validation shared by Account, its manager, and authentication lookups."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


PHONE_PATTERN = r"\+[1-9][0-9]{1,14}"
_PHONE_RE = re.compile(PHONE_PATTERN)


def normalize_phone(value):
    """Remove approved separators and require an explicit country prefix."""
    if not isinstance(value, str):
        raise ValidationError(
            _("Enter a phone number with + and 2–15 ASCII digits."),
            code="invalid_phone",
        )
    normalized = "".join(
        character
        for character in value
        if not character.isspace() and character not in "-()"
    )
    if _PHONE_RE.fullmatch(normalized) is None:
        raise ValidationError(
            _("Enter a phone number with + and 2–15 ASCII digits."),
            code="invalid_phone",
        )
    return normalized


def validate_phone(value):
    normalize_phone(value)


def validate_status(value):
    """Require a supplied value without defining a business status vocabulary."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            _("Provide a non-empty account status."), code="invalid_status"
        )
