"""Public inventory writes cannot bypass validation or location services."""

from django.db import models
from django.db.utils import NotSupportedError


class ProductItemQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Update a ProductItem instance with save().")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Create ProductItems individually.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError(
            "Update ProductItems individually with save()."
        )


class ProductItemManager(models.Manager.from_queryset(ProductItemQuerySet)):
    pass


class LocationHistoryQuerySet(ProductItemQuerySet):
    def delete(self):
        raise NotSupportedError("Location history cannot be deleted.")


class LocationHistoryManager(
    models.Manager.from_queryset(LocationHistoryQuerySet)
):
    pass
