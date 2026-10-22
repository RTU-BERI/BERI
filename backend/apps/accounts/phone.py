"""Canonical international phone values for ORM, forms and authentication."""

import re

import phonenumbers
from django import forms
from django.core.exceptions import ValidationError
from django.db import models


def normalize_phone(value):
    if not isinstance(value, str):
        raise ValidationError(
            "Enter a valid international phone number.", code="invalid"
        )
    compact = re.sub(r"[\s()\-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{1,14}", compact):
        raise ValidationError(
            "Include an explicit country code, starting with +.", code="invalid"
        )
    try:
        number = phonenumbers.parse(compact, None)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(
            "Enter a valid international phone number.", code="invalid"
        ) from exc
    if not phonenumbers.is_valid_number(number):
        raise ValidationError(
            "Enter a valid international phone number.", code="invalid"
        )
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class PhoneFormField(forms.CharField):
    def to_python(self, value):
        value = super().to_python(value)
        return normalize_phone(value) if value else value


class PhoneField(models.CharField):
    """Normalize before max-length/unique validation and ORM writes/lookups."""

    def to_python(self, value):
        return normalize_phone(value)

    def get_prep_value(self, value):
        return self.to_python(value)

    def formfield(self, **kwargs):
        return super().formfield(form_class=PhoneFormField, **kwargs)
