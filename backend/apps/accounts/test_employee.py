"""Employee write boundaries and physical constraints on PostgreSQL."""

import uuid
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase

from apps.pickup_points.models import PickupPoint

from .models import Account, Employee


class EmployeeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.point = PickupPoint.objects.create(
            name="Employee fixture point",
            address="Fixture address",
            latitude=Decimal("55.755800"),
            longitude=Decimal("37.617300"),
            status="fixture-status",
        )
        cls.service_account = Account.objects.create_user(
            phone="+12025550161",
            account_type=Account.AccountType.EMPLOYEE,
            status="fixture-status",
        )
        cls.customer = Account.objects.create_user(
            phone="+12025550162",
            account_type=Account.AccountType.CUSTOMER,
            status="fixture-status",
        )

    def create_employee(self):
        return Employee.objects.create_employee(
            account=self.service_account, pickup_point=self.point
        )

    def create_other_point(self):
        return PickupPoint.objects.create(
            name="Other employee fixture point",
            address="Other fixture address",
            latitude=Decimal("55.700000"),
            longitude=Decimal("37.600000"),
            status="fixture-status",
        )

    def test_exact_postgresql_columns_pk_unique_and_foreign_keys(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(Employee._meta.db_table, "employee")
        self.assertIsInstance(Employee._meta.pk, models.BigAutoField)
        self.assertFalse(Employee._meta.get_field("account").primary_key)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, numeric_precision "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='employee'"
            )
            columns = {row[0]: row[1:] for row in cursor.fetchall()}
            constraints = connection.introspection.get_constraints(
                cursor, "employee"
            )
        self.assertEqual(
            columns,
            {
                "id": ("bigint", "NO", 64),
                "account_id": ("uuid", "NO", None),
                "pickup_point_id": ("bigint", "NO", 64),
            },
        )
        self.assertTrue(
            any(
                constraint["primary_key"] and constraint["columns"] == ["id"]
                for constraint in constraints.values()
            )
        )
        self.assertTrue(
            any(
                constraint["unique"]
                and constraint["columns"] == ["account_id"]
                for constraint in constraints.values()
            )
        )
        for column, parent in (
            ("account_id", "account"),
            ("pickup_point_id", "pickup_point"),
        ):
            with self.subTest(column=column):
                self.assertTrue(
                    any(
                        constraint["columns"] == [column]
                        and constraint["foreign_key"] == (parent, "id")
                        for constraint in constraints.values()
                    )
                )

    def test_named_manager_creation_accepts_service_account(self):
        employee = self.create_employee()
        employee.refresh_from_db()
        self.assertIsInstance(employee.pk, int)
        self.assertEqual(employee.account_id, self.service_account.pk)
        self.assertEqual(employee.pickup_point_id, self.point.pk)
        self.assertEqual(self.service_account.employee.pk, employee.pk)

    def test_standard_objects_create_accepts_service_account(self):
        employee = Employee.objects.create(
            account=self.service_account, pickup_point=self.point
        )
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())

    def test_direct_save_accepts_service_account_without_admin_flags(self):
        employee = Employee(
            account=self.service_account, pickup_point=self.point
        )
        employee.save()
        self.assertIsNotNone(employee.pk)
        self.service_account.refresh_from_db()
        self.assertFalse(self.service_account.is_staff)
        self.assertFalse(self.service_account.is_superuser)

    def test_full_clean_accepts_service_and_rejects_customer(self):
        Employee(
            account=self.service_account, pickup_point=self.point
        ).full_clean()
        with self.assertRaises(ValidationError) as raised:
            Employee(account=self.customer, pickup_point=self.point).full_clean()
        self.assertIn("account", raised.exception.message_dict)
        self.assertEqual(Employee.objects.count(), 0)

    def test_supported_customer_creation_paths_are_rejected(self):
        operations = (
            lambda: Employee.objects.create_employee(
                account=self.customer, pickup_point=self.point
            ),
            lambda: Employee.objects.create(
                account=self.customer, pickup_point=self.point
            ),
            lambda: Employee(
                account=self.customer, pickup_point=self.point
            ).save(),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ValidationError) as raised:
                    operation()
                self.assertIn("account", raised.exception.message_dict)
        self.assertEqual(Employee.objects.count(), 0)

    def test_customer_admin_flags_groups_and_permissions_do_not_allow_profile(
        self,
    ):
        self.customer.is_staff = True
        self.customer.is_superuser = True
        self.customer.save(update_fields=["is_staff", "is_superuser"])
        permission = Permission.objects.get(
            content_type__app_label="accounts", codename="add_employee"
        )
        group = Group.objects.create(name="Fixture privileged group")
        group.permissions.add(permission)
        self.customer.groups.add(group)
        self.customer.user_permissions.add(permission)
        with self.assertRaises(ValidationError):
            Employee.objects.create_employee(
                account=self.customer, pickup_point=self.point
            )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.account_type, Account.AccountType.CUSTOMER)
        self.assertEqual(Employee.objects.count(), 0)

    def test_profile_creation_does_not_modify_account_or_permissions(self):
        group = Group.objects.create(name="Fixture existing group")
        permission = Permission.objects.get(
            content_type__app_label="accounts", codename="view_account"
        )
        self.service_account.groups.add(group)
        self.service_account.user_permissions.add(permission)
        before = Account.objects.values().get(pk=self.service_account.pk)
        before_groups = set(
            self.service_account.groups.values_list("pk", flat=True)
        )
        before_permissions = set(
            self.service_account.user_permissions.values_list("pk", flat=True)
        )
        self.create_employee()
        self.assertEqual(
            Account.objects.values().get(pk=self.service_account.pk), before
        )
        self.assertEqual(
            set(self.service_account.groups.values_list("pk", flat=True)),
            before_groups,
        )
        self.assertEqual(
            set(
                self.service_account.user_permissions.values_list(
                    "pk", flat=True
                )
            ),
            before_permissions,
        )

    def test_pickup_point_is_required_and_existing_in_supported_writes(self):
        for pickup_point_id in (None, -1):
            with self.subTest(pickup_point_id=pickup_point_id):
                employee = Employee(
                    account=self.service_account,
                    pickup_point_id=pickup_point_id,
                )
                with self.assertRaises(ValidationError) as raised:
                    employee.save()
                self.assertIn("pickup_point", raised.exception.message_dict)
        self.assertEqual(Employee.objects.count(), 0)

    def test_account_uniqueness_is_checked_by_supported_creation(self):
        self.create_employee()
        with self.assertRaises(ValidationError) as raised:
            Employee.objects.create_employee(
                account=self.service_account,
                pickup_point=self.create_other_point(),
            )
        self.assertIn("account", raised.exception.message_dict)
        self.assertEqual(Employee.objects.count(), 1)

    def test_postgresql_enforces_account_uniqueness_for_direct_sql(self):
        self.create_employee()
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO employee (account_id, pickup_point_id) "
                    "VALUES (%s, %s)",
                    [self.service_account.pk, self.point.pk],
                )

    def test_postgresql_enforces_required_and_existing_foreign_keys(self):
        for account_id, pickup_point_id in (
            (None, self.point.pk),
            (self.service_account.pk, None),
            (uuid.uuid4(), self.point.pk),
            (self.service_account.pk, -1),
        ):
            with self.subTest(
                account_id=account_id, pickup_point_id=pickup_point_id
            ):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO employee "
                            "(account_id, pickup_point_id) VALUES (%s, %s)",
                            [account_id, pickup_point_id],
                        )
                    connection.check_constraints(table_names=["employee"])

    def test_profile_cannot_be_reassigned_to_customer(self):
        employee = self.create_employee()
        employee.account = self.customer
        with self.assertRaises(ValidationError):
            employee.save(update_fields=["account"])
        employee.refresh_from_db()
        self.assertEqual(employee.account_id, self.service_account.pk)

    def test_profile_can_move_to_another_existing_pickup_point(self):
        employee = self.create_employee()
        other_point = self.create_other_point()
        employee.pickup_point = other_point
        employee.save(update_fields=["pickup_point"])
        employee.refresh_from_db()
        self.assertEqual(employee.pickup_point_id, other_point.pk)
        self.assertEqual(employee.account_id, self.service_account.pk)

    def test_stale_related_account_cache_cannot_bypass_current_type_check(self):
        stale_account = Account.objects.get(pk=self.service_account.pk)
        employee = Employee(account=stale_account, pickup_point=self.point)
        Account.objects.filter(pk=stale_account.pk).update(
            account_type=Account.AccountType.CUSTOMER
        )
        self.assertEqual(employee.account.account_type, Account.AccountType.EMPLOYEE)
        with self.assertRaises(ValidationError):
            employee.full_clean()
        with self.assertRaises(ValidationError):
            employee.save()
        self.assertEqual(Employee.objects.count(), 0)

    def test_existing_profile_exposes_deferred_reverse_account_update_boundary(
        self,
    ):
        employee = self.create_employee()
        self.service_account.account_type = Account.AccountType.CUSTOMER
        self.service_account.save(update_fields=["account_type"])
        employee.refresh_from_db()
        # Account writes remain unchanged; Employee validation is not a DB CHECK.
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
        with self.assertRaises(ValidationError):
            employee.full_clean()

    def test_both_parent_relations_protect_against_deletion(self):
        employee = self.create_employee()
        for parent in (self.service_account, self.point):
            with self.subTest(parent=type(parent).__name__):
                with self.assertRaises(ProtectedError):
                    parent.delete()
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
        self.assertTrue(
            Account.objects.filter(pk=self.service_account.pk).exists()
        )
        self.assertTrue(PickupPoint.objects.filter(pk=self.point.pk).exists())

    def test_unvalidated_queryset_and_bulk_writes_are_explicitly_unsupported(
        self,
    ):
        employee = self.create_employee()
        other_point = self.create_other_point()
        employee.pickup_point = other_point
        operations = (
            lambda: Employee.objects.filter(pk=employee.pk).update(
                pickup_point=other_point
            ),
            lambda: Employee.objects.bulk_create(
                [Employee(account=self.customer, pickup_point=self.point)]
            ),
            lambda: Employee.objects.bulk_update([employee], ["pickup_point"]),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    operation()
        employee.refresh_from_db()
        self.assertEqual(employee.pickup_point_id, self.point.pk)
        self.assertEqual(Employee.objects.count(), 1)
