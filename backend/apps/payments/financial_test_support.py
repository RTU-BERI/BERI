"""Test-only financial histories, manufactured in Django's test database.

Declared final_amount is the immutable authoritative snapshot: these helpers
do not implement a provider, successful return or production financial writer.
"""
from decimal import Decimal
import uuid

from django.db import connection
from django.utils import timezone

from apps.payments.models import PaymentMethod, Payment, Debt
from apps.rentals.test_support import FixtureMixin as RentalFixtures, raw_insert


class FinancialFixtureMixin(RentalFixtures):
    def setUp(self):
        super().setUp()
        self.rental = self.make_completed_rental()
        self.payment_method = PaymentMethod.objects.get(pk=self.rental.payment_method_id)

    def make_completed_rental(self, amount="12.00", **overrides):
        item = self.make_item()
        reservation = self.make_reservation(item=item)
        return self.make_rental(reservation=reservation, status="completed",
            final_amount=Decimal(amount), **overrides)

    @staticmethod
    def _overrides(model, values, overrides):
        for name, value in overrides.items():
            try:
                field = model._meta.get_field(name)
            except Exception:
                field = next(f for f in model._meta.concrete_fields if f.attname==name)
            if field.is_relation and hasattr(value,"pk"):
                value=value.pk
            values[field.attname]=value
        return values

    def payment_values(self, rental=None, status="pending", method=None, **overrides):
        rental = self.rental if rental is None else rental
        now = timezone.now()
        values=dict(id=uuid.uuid4(), rental_id=rental.pk,
            payment_method_id=rental.payment_method_id if method is None else getattr(method,"pk",method),
            external_id=None if status=="pending" else f"synthetic-operation-{uuid.uuid4()}",
            amount=rental.final_amount,status=status, failure_code=None,failure_message=None,
            created_at=now,resolved_at=None if status=="pending" else now)
        return self._overrides(Payment,values,overrides)

    def debt_values(self, rental=None, status="open", **overrides):
        rental = self.rental if rental is None else rental
        now = timezone.now()
        return self._overrides(Debt,dict(id=uuid.uuid4(),rental_id=rental.pk,amount=rental.final_amount,
            status=status,created_at=now,settled_at=None if status=="open" else now),overrides)

    def make_payment(self, rental=None, status="pending", method=None, **overrides):
        return raw_insert(Payment,**self.payment_values(rental,status,method,**overrides))

    def make_debt(self, rental=None, status="open", **overrides):
        return raw_insert(Debt,**self.debt_values(rental,status,**overrides))

    @staticmethod
    def raw_update(model, pk, **values):
        normalized=FinancialFixtureMixin._overrides(model,{},values)
        quote=connection.ops.quote_name
        columns={f.attname:f.column for f in model._meta.concrete_fields}
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {quote(model._meta.db_table)} SET "+
                ', '.join(f'{quote(columns[k])}=%s' for k in normalized)+
                f" WHERE {quote(model._meta.pk.column)}=%s",[*normalized.values(),pk])

    def make_paid_history(self, rental=None, method=None):
        rental=self.rental if rental is None else rental
        failed=self.make_payment(rental,status="failed")
        debt=self.make_debt(rental)
        succeeded=self.make_payment(rental,status="succeeded",method=method)
        self.raw_update(Debt,debt.pk,status="paid",settled_at=timezone.now())
        debt.refresh_from_db()
        return failed,debt,succeeded

    def snapshot(self):
        result=super().snapshot()
        for model in (Payment,Debt):
            result[model._meta.db_table]=list(model.objects.order_by("pk").values())
        return result
