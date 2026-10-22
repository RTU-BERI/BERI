"""Private writes; callers must follow their service's item-first protocol."""

from django.core.exceptions import ValidationError
from django.db import models

from .models import (
    ProductItemLocationHistory,
    _full_clean_using,
)


def insert_location_history(item, pickup_point, now, *, using):
    history = ProductItemLocationHistory(
        product_item=item, pickup_point=pickup_point, valid_from=now
    )
    _full_clean_using(history, using)
    models.Model.save(history, using=using, force_insert=True)
    return history


def close_location_history(history, now, *, using):
    if history.valid_to is not None:
        raise ValidationError(
            "A closed location period cannot be closed again."
        )
    history.valid_to = now
    _full_clean_using(history, using)
    models.Model.save(history, using=using, update_fields=["valid_to"])


def write_current_location(item, pickup_point, *, using):
    item.pickup_point = pickup_point
    models.Model.save(
        item, using=using, update_fields=["pickup_point", "updated_at"]
    )
