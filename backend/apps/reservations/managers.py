"""Unfiltered managers that route reservation writes to internal services."""

from django.db import models
from django.db.utils import NotSupportedError


class ServiceOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Use the reservation services to update history.")

    def delete(self):
        raise NotSupportedError("Reservation history cannot be deleted.")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Use the reservation services to create history.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError("Use the reservation services to update history.")


class ServiceOnlyManager(models.Manager.from_queryset(ServiceOnlyQuerySet)):
    pass
