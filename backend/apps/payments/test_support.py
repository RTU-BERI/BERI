"""Synthetic tokens only; confirmed fixtures do not emulate a provider."""

from itertools import count
import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from .models import PaymentMethod
from .privacy import private_queries


_numbers = count(1)


class FixtureMixin:
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.other_customer = self.make_customer()
        self.employee_account = self.make_customer(account_type="employee")
        self.method = self.make_method()

    def make_customer(self, *, account_type="customer", is_active=True):
        return get_user_model().objects.create_user(
            phone=f"+1666{next(_numbers):09d}", password=None,
            account_type=account_type, status="synthetic-account", is_active=is_active,
        )

    def make_method(self, *, customer=None, external_id=None, brand=None,
                    last4=None, expires_month=None, expires_year=None):
        # Deliberate SQL fixture only; no production registration/verification
        # service is part of the database-only delivery.
        customer = self.customer if customer is None else customer
        pk = uuid.uuid4()
        token = f"synthetic-method-{next(_numbers)}" if external_id is None else external_id
        with private_queries("default"), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO payment_method "
                "(id,customer_account_id,external_id,brand,last4,expires_month,"
                "expires_year,is_default,status,verification_status,created_at,deactivated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,'active','pending',%s,NULL)",
                [pk, customer.pk, token, brand, last4, expires_month, expires_year, timezone.now()],
            )
        return PaymentMethod.objects.get(pk=pk)

    def make_confirmed_method(self, *, is_default=False, **kwargs):
        method = self.make_method(**kwargs)
        # Deliberate synthetic SQL fixture in test_beri, NOT a provider result.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE payment_method SET verification_status='confirmed', "
                "is_default=%s WHERE id=%s", [is_default, method.pk],
            )
        method.refresh_from_db()
        return method
