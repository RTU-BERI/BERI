"""Supported Employee writes always go through model validation."""

from django.db import models
from django.db.utils import NotSupportedError


class EmployeeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Update an Employee instance and call save().")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Create Employees individually with validation.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError("Update Employees individually with save().")


class EmployeeManager(models.Manager.from_queryset(EmployeeQuerySet)):
    def create_employee(self, *, account, pickup_point):
        return self.create(account=account, pickup_point=pickup_point)
