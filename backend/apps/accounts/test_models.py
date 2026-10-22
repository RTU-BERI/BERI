import io
import uuid
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings

from apps.pickup_points.models import PickupPoint

from .backends import AccountBackend
from .models import Account, Employee
from .phone import normalize_phone


PASSWORD = "Test-only-secret-327!"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class AccountTests(TestCase):
    def create_account(self, **kwargs):
        return Account.objects.create_user(kwargs.pop("phone", "+79991234567"), PASSWORD, **kwargs)

    def test_customer_and_employee_identity_and_password_hash(self):
        for account_type, phone in (("customer", "+79991234567"), ("employee", "+4915123456789")):
            with self.subTest(account_type=account_type):
                account = self.create_account(phone=phone, account_type=account_type)
                self.assertIs(get_user_model(), Account)
                self.assertIsInstance(account.pk, uuid.UUID)
                self.assertEqual(account.account_type, account_type)
                self.assertFalse(account.is_staff)
                self.assertFalse(account.is_superuser)
                self.assertIsNone(account.phone_verified_at)
                self.assertNotEqual(account.password, PASSWORD)
                self.assertEqual(identify_hasher(account.password).algorithm, "md5")
                self.assertTrue(account.check_password(PASSWORD))
                self.assertFalse(account.check_password("wrong"))
                self.assertFalse(Employee.objects.filter(account=account).exists())

    def test_normalizes_international_phones_before_save_and_lookup(self):
        for raw, canonical in (("+7 (999) 123-45-67", "+79991234567"), ("+49 151 23456789", "+4915123456789")):
            with self.subTest(raw=raw):
                account = self.create_account(phone=raw)
                account.refresh_from_db()
                self.assertEqual(account.phone, canonical)
                self.assertEqual(Account.objects.get(phone=raw).pk, account.pk)

    def test_invalid_international_and_local_phones_are_rejected(self):
        for phone in (
            "89991234567", "9991234567", "004915123456789", "+0123456789",
            "+999123456789", "+7999", "+1234567890123456", "+7abc9991234567",
            "+79991234567 ext 5", "", None, 79991234567,
        ):
            with self.subTest(phone=phone):
                with self.assertRaises(ValidationError):
                    normalize_phone(phone)
                with self.assertRaises((ValidationError, ValueError)):
                    self.create_account(phone=phone)
        self.assertEqual(Account.objects.count(), 0)

    def test_model_save_and_bulk_create_also_normalize(self):
        account = Account(phone="+7 (999) 123-45-67")
        account.set_password(PASSWORD)
        account.save()
        Account.objects.bulk_create([Account(phone="+49 151 23456789", password=account.password)])
        self.assertEqual(Account.objects.get(pk=account.pk).phone, "+79991234567")
        self.assertTrue(Account.objects.filter(phone="+4915123456789").exists())

    def test_canonical_phone_unique_across_account_types(self):
        self.create_account(account_type="customer")
        with self.assertRaises(ValidationError):
            self.create_account(phone="+7 (999) 123-45-67", account_type="employee")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.bulk_create([Account(phone="+7 (999) 123-45-67", account_type="employee")])
        self.assertEqual(Account.objects.count(), 1)

    def test_required_credentials_and_no_ordinary_admin_creation(self):
        for phone, password in ((None, PASSWORD), ("", PASSWORD), ("+79991234567", None), ("+79991234567", "")):
            with self.subTest(phone=phone, has_password=bool(password)), self.assertRaises(ValueError):
                Account.objects.create_user(phone, password)
        for flags in ({"is_staff": True}, {"is_superuser": True}, {"is_staff": True, "is_superuser": True}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.create_account(**flags)

    def test_authenticate_username_and_phone_credentials(self):
        account = self.create_account()
        self.assertEqual(authenticate(None, username="+7 (999) 123-45-67", password=PASSWORD), account)
        self.assertEqual(authenticate(None, phone=account.phone, password=PASSWORD), account)
        self.assertEqual(AccountBackend().authenticate(None, account.phone, PASSWORD), account)
        for credentials in (
            {"phone": account.phone, "password": "wrong"},
            {"phone": "+4915123456789", "password": PASSWORD},
            {"phone": "89991234567", "password": PASSWORD},
            {"phone": account.phone}, {"password": PASSWORD},
        ):
            with self.subTest(keys=tuple(credentials)):
                self.assertIsNone(authenticate(**credentials))

    def test_all_status_activity_combinations_and_authentication(self):
        for status, requested_active, saved_active in (
            ("active", True, True), ("active", False, False), ("blocked", False, False), ("blocked", True, False),
        ):
            with self.subTest(status=status, requested_active=requested_active):
                account = self.create_account(status=status, is_active=requested_active)
                account.refresh_from_db()
                self.assertEqual(account.is_active, saved_active)
                self.assertEqual(authenticate(phone=account.phone, password=PASSWORD) == account, saved_active)
                account.delete()
        self.assertFalse(AccountBackend().user_can_authenticate(Account(status="blocked", is_active=True)))

    def test_block_update_fields_and_unblock_does_not_reactivate(self):
        account = self.create_account()
        account.status = "blocked"
        account.save(update_fields=["status"])
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        account.status = "active"
        account.save(update_fields=["status"])
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertIsNone(authenticate(phone=account.phone, password=PASSWORD))
        account.is_active = True
        account.save(update_fields=["is_active"])
        self.assertEqual(authenticate(phone=account.phone, password=PASSWORD), account)

    def test_stale_password_only_save_cannot_undo_a_block(self):
        stale_account = self.create_account()
        Account.objects.filter(pk=stale_account.pk).update(status="blocked", is_active=False)
        stale_account.set_password("New-test-only-secret-546!")
        stale_account.save(update_fields=["password"])
        account = Account.objects.get(pk=stale_account.pk)
        self.assertEqual(account.status, "blocked")
        self.assertFalse(account.is_active)
        self.assertTrue(account.check_password("New-test-only-secret-546!"))
        self.assertIsNone(authenticate(phone=account.phone, password="New-test-only-secret-546!"))

    def test_database_constraints_reject_invalid_bulk_updates(self):
        account = self.create_account()
        for values in (
            {"status": "blocked", "is_active": True}, {"status": "unknown"},
            {"account_type": "admin"}, {"status": ""}, {"account_type": ""},
        ):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                # Bypass the application guard only to verify the DB constraints themselves.
                models.QuerySet.update(Account.objects.filter(pk=account.pk), **values)
        for field, value in (("status", "unknown"), ("account_type", "admin")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.create_account(phone="+4915123456789", **{field: value})

    def test_database_constraint_rejects_noncanonical_phone_expression(self):
        account = self.create_account()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.filter(pk=account.pk).update(phone=models.Value("79991234567"))
        account.refresh_from_db()
        self.assertEqual(account.phone, "+79991234567")

    def test_create_superuser_explicit_options_flags_and_no_automatic_profile(self):
        for options in ({}, {"account_type": "customer"}, {"status": "active"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Account.objects.create_superuser("+79991234567", PASSWORD, **options)
        for field in ("is_staff", "is_superuser"):
            for value in (False, None, 1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Account.objects.create_superuser(
                        "+79991234567", PASSWORD, account_type="customer", status="active", **{field: value},
                    )
        for options in ({"account_type": "admin", "status": "active"}, {"account_type": "customer", "status": "unknown"}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                Account.objects.create_superuser("+79991234567", PASSWORD, **options)
        for account_type, status in (("customer", "active"), ("employee", "blocked")):
            with self.subTest(account_type=account_type, status=status):
                account = Account.objects.create_superuser(
                    "+79991234567", PASSWORD, account_type=account_type, status=status,
                )
                self.assertTrue(account.is_staff)
                self.assertTrue(account.is_superuser)
                self.assertEqual(account.is_active, status == "active")
                self.assertTrue(account.check_password(PASSWORD))
                self.assertFalse(Employee.objects.filter(account=account).exists())
                account.delete()

    def test_standard_createsuperuser_command(self):
        with patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": PASSWORD}):
            call_command(
                "createsuperuser", interactive=False, phone="+49 151 23456789",
                account_type="employee", status="blocked", stdout=io.StringIO(),
            )
        account = Account.objects.get(phone="+4915123456789")
        self.assertTrue(account.is_superuser)
        self.assertTrue(account.is_staff)
        self.assertFalse(account.is_active)
        self.assertEqual(account.account_type, "employee")
        self.assertEqual(account.status, "blocked")
        self.assertTrue(account.check_password(PASSWORD))
        self.assertFalse(Employee.objects.exists())

    def test_standard_groups_permissions_are_explicit(self):
        account = self.create_account(account_type="employee")
        permission = Permission.objects.get(content_type__app_label="accounts", codename="change_account")
        self.assertFalse(account.has_perm("accounts.change_account"))
        Account.objects.filter(pk=account.pk).update(is_staff=True)
        account = Account.objects.get(pk=account.pk)
        self.assertFalse(account.has_perm("accounts.change_account"))
        group = Group.objects.create(name="Test account operators")
        group.permissions.add(permission)
        account.groups.add(group)
        account = Account.objects.get(pk=account.pk)
        self.assertTrue(account.has_perm("accounts.change_account"))
        account.groups.clear()
        account.user_permissions.add(permission)
        account = Account.objects.get(pk=account.pk)
        self.assertTrue(account.has_perm("accounts.change_account"))
        account.is_active = False
        account.save()
        self.assertFalse(Account.objects.get(pk=account.pk).has_perm("accounts.change_account"))


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EmployeeTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create_user("+79991234567", PASSWORD, account_type="employee")
        self.point = PickupPoint.objects.create(
            name="Test pickup point", address="Test address", latitude="55.750000",
            longitude="37.610000", status="test-fixture",
        )

    def test_bigint_identity_and_required_relations(self):
        employee = Employee.objects.create(account=self.account, pickup_point=self.point)
        self.assertIsInstance(employee.pk, int)
        self.assertIsInstance(Employee._meta.pk, models.BigAutoField)
        self.assertEqual(self.account.employee, employee)
        self.assertEqual(employee.pickup_point, self.point)
        self.assertEqual(Employee._meta.get_field("account").column, "account_id")
        self.assertEqual(Employee._meta.get_field("pickup_point").column, "pickup_point_id")
        self.assertFalse(Employee._meta.get_field("pickup_point").null)

    def test_one_employee_per_account_database_unique(self):
        Employee.objects.create(account=self.account, pickup_point=self.point)
        with self.assertRaises(ValidationError):
            Employee.objects.create(account=self.account, pickup_point=self.point)
        with self.assertRaises(IntegrityError), transaction.atomic():
            # Exercise the actual unique constraint independently of the bulk-write guard.
            models.QuerySet.bulk_create(
                Employee.objects.all(), [Employee(account=self.account, pickup_point=self.point)],
            )

    def test_bulk_operations_cannot_bypass_cross_table_type_validation(self):
        employee = Employee.objects.create(account=self.account, pickup_point=self.point)
        customer = Account.objects.create_user("+4915123456789", PASSWORD)
        with self.assertRaises(ValueError):
            Account.objects.filter(pk=self.account.pk).update(account_type="customer")
        self.account.account_type = "customer"
        with self.assertRaises(ValueError):
            Account.objects.bulk_update([self.account], ["account_type"])
        with self.assertRaises(ValueError):
            Account.objects.bulk_create(
                [Account(phone=self.account.phone, account_type="customer")],
                update_conflicts=True, update_fields=["account_type"],
                unique_fields=["phone"],
            )
        for values in ({"account": customer}, {"account_id": customer.pk}):
            with self.subTest(field=tuple(values)), self.assertRaises(ValueError):
                Employee.objects.filter(pk=employee.pk).update(**values)
        employee.account = customer
        with self.assertRaises(ValueError):
            Employee.objects.bulk_update([employee], ["account"])
        with self.assertRaises(ValueError):
            Employee.objects.bulk_create([Employee(account=customer, pickup_point=self.point)])
        employee.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(employee.account_id, self.account.pk)
        self.assertEqual(self.account.account_type, "employee")

    def test_customer_cannot_have_employee_profile_outside_forms(self):
        customer = Account.objects.create_user("+4915123456789", PASSWORD)
        with self.assertRaises(ValidationError):
            Employee.objects.create(account=customer, pickup_point=self.point)
        self.assertFalse(Employee.objects.exists())

    def test_requires_existing_account_and_pickup_point(self):
        for fields in (
            {"account": self.account}, {"pickup_point": self.point},
            {"account": self.account, "pickup_point_id": self.point.pk + 1000},
            {"account_id": uuid.uuid4(), "pickup_point": self.point},
        ):
            with self.subTest(keys=tuple(fields)), self.assertRaises(ValidationError):
                Employee.objects.create(**fields)

    def test_database_rejects_missing_and_nonexistent_relations(self):
        for fields in (
            {"account_id": self.account.pk}, {"pickup_point_id": self.point.pk},
            {"account_id": self.account.pk, "pickup_point_id": self.point.pk + 1000},
            {"account_id": uuid.uuid4(), "pickup_point_id": self.point.pk},
        ):
            with self.subTest(keys=tuple(fields)), self.assertRaises(IntegrityError), transaction.atomic():
                # Bypass model/queryset validation to verify NOT NULL and deferred FK checks.
                models.QuerySet.bulk_create(Employee.objects.all(), [Employee(**fields)])
                connection.check_constraints(table_names=[Employee._meta.db_table])

    def test_related_account_and_pickup_point_are_protected(self):
        employee = Employee.objects.create(account=self.account, pickup_point=self.point)
        for related in (self.account, self.point):
            with self.subTest(model=type(related).__name__), self.assertRaises(ProtectedError):
                related.delete()
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())

    def test_account_type_cannot_change_while_employee_exists(self):
        Employee.objects.create(account=self.account, pickup_point=self.point)
        self.account.account_type = "customer"
        with self.assertRaises(ValidationError):
            self.account.save(update_fields=["account_type"])
        self.account.refresh_from_db()
        self.assertEqual(self.account.account_type, "employee")

    def test_employee_cannot_be_reassigned_to_customer(self):
        employee = Employee.objects.create(account=self.account, pickup_point=self.point)
        customer = Account.objects.create_user("+4915123456789", PASSWORD)
        employee.account = customer
        with self.assertRaises(ValidationError):
            employee.save()
        employee.refresh_from_db()
        self.assertEqual(employee.account, self.account)
