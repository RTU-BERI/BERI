"""Unfiltered managers that reserve payment-method writes for services."""

from django.db import models
from django.db.utils import NotSupportedError


class PaymentMethodQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Use the payment-method services to update records.")

    def delete(self):
        raise NotSupportedError("Payment-method history cannot be deleted.")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Use the payment-method services to create records.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError("Use the payment-method services to update records.")


class PaymentMethodManager(models.Manager.from_queryset(PaymentMethodQuerySet)):
    pass


class FinancialHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise NotSupportedError("Financial history cannot be updated publicly.")

    def delete(self):
        raise NotSupportedError("Financial history cannot be deleted.")

    def bulk_create(self, *args, **kwargs):
        raise NotSupportedError("Financial history cannot be created publicly.")

    def bulk_update(self, *args, **kwargs):
        raise NotSupportedError("Financial history cannot be updated publicly.")


class FinancialHistoryManager(
    models.Manager.from_queryset(FinancialHistoryQuerySet)
):
    pass
