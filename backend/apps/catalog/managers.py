"""Write paths that preserve catalog validation and tariff history."""

from django.db import models
from django.db.utils import NotSupportedError


class ValidatedCatalogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Update a catalog instance and call save().")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError(
            "Create catalog objects individually with save()."
        )

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError(
            "Update catalog objects individually with save()."
        )


class ValidatedCatalogManager(
    models.Manager.from_queryset(ValidatedCatalogQuerySet)
):
    pass


class ComponentQuerySet(ValidatedCatalogQuerySet):
    def delete(self):
        raise NotSupportedError("Delete individual components before inspection.")


class ComponentManager(models.Manager.from_queryset(ComponentQuerySet)):
    pass


class TariffQuerySet(ValidatedCatalogQuerySet):
    def delete(self):
        raise NotSupportedError("Tariff versions cannot be deleted.")


class TariffManager(models.Manager.from_queryset(TariffQuerySet)):
    pass
