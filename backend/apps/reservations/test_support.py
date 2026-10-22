"""Test-only SQL histories; no booking, selection or admission service.

These fixtures are imported only by tests in Django's disposable database.
They establish explicit rows for model/SQL checks, without granting a supported
production write path for the read-only historical models.
"""

import uuid
from datetime import timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import Employee
from apps.catalog.models import Product, ProductCategory
from apps.inventory.services import create_item
from apps.pickup_points.models import PickupPoint

from .models import Reservation


_numbers = count(1)
_UNSET = object()
TEST_STATUS = "test_enabled"


class FixtureMixin:
    def setUp(self):
        super().setUp()
        number = next(_numbers)
        self.customer = self.make_customer()
        self.other_customer = self.make_customer()
        employee_account = self.make_customer(account_type="employee")
        category = ProductCategory.objects.create(name=f"Booking category {number}")
        self.product = Product.objects.create(
            category=category, name=f"Booking product {number}",
            description="Synthetic reservation fixture", status=TEST_STATUS,
        )
        self.other_product = Product.objects.create(
            category=category, name=f"Other product {number}",
            description="Synthetic other product", status=TEST_STATUS,
        )
        self.point = self.make_point()
        self.other_point = self.make_point()
        self.employee = Employee.objects.create(
            account=employee_account, pickup_point=self.point,
        )
        self.item = self.make_item()

    def make_customer(self, *, account_type="customer", is_active=True):
        return get_user_model().objects.create_user(
            phone=f"+1555{next(_numbers):09d}", password=None,
            account_type=account_type, status="test_account", is_active=is_active,
        )

    def make_point(self):
        number = next(_numbers)
        return PickupPoint.objects.create(
            name=f"Booking point {number}", address="Synthetic address",
            latitude="55.750000", longitude="37.610000", status=TEST_STATUS,
        )

    def make_item(self, *, product=None, pickup_point=_UNSET,
                  status="available", code=None):
        return create_item(
            product=self.product if product is None else product,
            pickup_point=self.point if pickup_point is _UNSET else pickup_point,
            inventory_code=code or f"BOOK-{next(_numbers)}", status=status,
        )

    def make_reservation(self, *, customer=None, item=_UNSET, pickup_point=None):
        """Manufacture an explicit header/assignment/item history for DB tests."""
        customer = self.customer if customer is None else customer
        item = self.item if item is _UNSET else item
        point = self.point if pickup_point is None else pickup_point
        if item is None:
            raise AssertionError("A SQL fixture requires an explicit inventory item.")
        customer_id = getattr(customer, "pk", customer)
        item_id = getattr(item, "pk", item)
        point_id = getattr(point, "pk", point)
        pk = uuid.uuid4()
        now = timezone.now()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO reservation "
                "(id,customer_account_id,pickup_point_id,initial_product_item_id,"
                "assigned_product_item_id,status,completion_reason,created_at,"
                "expires_at,cancelled_at,completed_at) "
                "VALUES (%s,%s,%s,%s,%s,'active',NULL,%s,%s,NULL,NULL)",
                [pk, customer_id, point_id, item_id, item_id,
                 now, now + timedelta(minutes=60)],
            )
            cursor.execute(
                "INSERT INTO reservation_item_assignment "
                "(reservation_id,product_item_id,assigned_by_employee_id,"
                "sequence_no,reason,assigned_at,released_at) "
                "VALUES (%s,%s,NULL,1,NULL,%s,NULL)", [pk, item_id, now],
            )
            cursor.execute(
                "UPDATE product_item SET status='reserved',status_changed_at=%s,"
                "updated_at=%s WHERE id=%s", [now, now, item_id],
            )
        return Reservation.objects.get(pk=pk)
