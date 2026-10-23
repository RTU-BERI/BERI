"""Unfiltered managers that keep immutable rental history service-only."""

from django.db import models
from django.db.utils import NotSupportedError


class ServiceOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Use the rental services to update history.")

    def delete(self):
        raise NotSupportedError("Rental history cannot be deleted.")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Use the rental services to create history.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError("Use the rental services to update history.")


class ServiceOnlyManager(models.Manager.from_queryset(ServiceOnlyQuerySet)):
    pass
