"""Synthetic SQL fixtures exclusively for Django's disposable test database.

No helper below is imported by application code. Confirmed methods and Rentals
are deliberately manufactured to test SQL/integration guards, not a provider or
a successful production issue/return path.
"""

from datetime import timedelta
from decimal import Decimal
from itertools import count
import uuid

from django.db import connection
from django.utils import timezone

from apps.catalog.models import ProductComponent, ProductTariff
from apps.payments.models import PaymentMethod
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.test_support import FixtureMixin as ReservationFixtures
from .models import Rental, Inspection, InspectionProblem

_numbers = count(1)


def raw_insert(model, **values):
    by_column = {}
    for name, value in values.items():
        try:
            field = model._meta.get_field(name)
        except Exception:
            field = next(f for f in model._meta.concrete_fields if f.attname == name)
        if field.is_relation and hasattr(value, "pk"):
            value = value.pk
        by_column[field.column] = value
    columns, params = list(by_column), list(by_column.values())
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {quote(model._meta.db_table)} "
            f"({', '.join(quote(c) for c in columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING {quote(model._meta.pk.column)}",
            params)
        pk = cursor.fetchone()[0]
    return model.objects.get(pk=pk)


class FixtureMixin(ReservationFixtures):
    def setUp(self):
        super().setUp()
        self.actor = self.employee.account
        self.reservation = self.make_reservation()
        self.assignment = self.reservation.assignments.get(released_at__isnull=True)
        self.component = ProductComponent.objects.create(
            product=self.product, name="Synthetic expected component", quantity=2)
        self.method = self.make_method()
        self.tariff = ProductTariff.objects.create(product=self.product,
            hourly_rate=Decimal("12.00"), valid_from=timezone.now()-timedelta(days=1))

    raw_insert = staticmethod(raw_insert)

    def make_method(self, *, customer=None, confirmed=False, token=None):
        """Insert a synthetic method; confirmed is test data, never a provider."""
        return raw_insert(
            PaymentMethod, id=uuid.uuid4(),
            customer_account=self.customer if customer is None else customer,
            external_id=(
                f"synthetic-rental-token-{next(_numbers)}" if token is None else token
            ),
            brand=None, last4=None, expires_month=None, expires_year=None,
            is_default=False, status="active",
            verification_status="confirmed" if confirmed else "pending",
            created_at=timezone.now(), deactivated_at=None,
        )

    def make_inspection(self, **overrides):
        values = dict(product_item=self.item, employee=self.employee,
            inspection_type="pre_issue", reservation=self.reservation, rental=None,
            condition_ok=True, completeness_ok=True, comment=None,
            customer_confirmed_at=None, inspected_at=timezone.now())
        values.update(overrides)
        return raw_insert(Inspection, **values)

    def make_problem(self, inspection=None, **overrides):
        values = dict(inspection=self.make_inspection() if inspection is None else inspection,
            problem_type="damage", description="Synthetic observed damage",
            product_component=None, missing_quantity=None)
        values.update(overrides)
        return raw_insert(InspectionProblem, **values)

    def make_rental(self, reservation=None, method=None, status="active", **overrides):
        reservation = self.reservation if reservation is None else reservation
        method = self.make_method(confirmed=True) if method is None else method
        reservation.refresh_from_db()
        item_id = reservation.assigned_product_item_id
        now = timezone.now()
        # The deliberate SQL updates make a coherent synthetic historical source.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE reservation_item_assignment SET released_at=%s "
                           "WHERE reservation_id=%s AND released_at IS NULL", [now,reservation.pk])
            cursor.execute("UPDATE reservation SET status='fulfilled', completed_at=%s, "
                           "cancelled_at=NULL, completion_reason=NULL WHERE id=%s", [now,reservation.pk])
            cursor.execute("UPDATE product_item SET status=%s, status_changed_at=%s WHERE id=%s",
                           ["rented" if status == "active" else "available",now,item_id])
        values = dict(id=uuid.uuid4(), reservation=reservation, product_item_id=item_id,
            tariff=self.tariff, payment_method=method, hourly_rate_applied=self.tariff.hourly_rate,
            started_by_employee=self.employee, ended_by_employee=None if status=="active" else self.employee,
            status=status, started_at=now, ended_at=None if status=="active" else now,
            final_amount=None if status=="active" else Decimal("0.00"))
        values.update(overrides)
        return raw_insert(Rental, **values)

    def snapshot(self):
        from apps.inventory.models import ProductItem, ProductItemLocationHistory
        from apps.payments.models import PaymentMethod
        return {model._meta.db_table:list(model.objects.order_by("pk").values()) for model in (
            Reservation,ReservationItemAssignment,ProductItem,ProductItemLocationHistory,
            Rental,Inspection,InspectionProblem,ProductComponent,ProductTariff,PaymentMethod)}
