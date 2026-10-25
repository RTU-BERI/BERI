"""Account integrity and default Django authentication on PostgreSQL."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import uuid

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .managers import AccountManager
from .models import Account
from .validators import normalize_phone, validate_phone, validate_status


# Synthetic test credentials/status, not product defaults or real secrets.
TEST_PASSWORD = "Test-only-Account-password!4826"
TEST_STATUS = "fixture-status"


def create_account(phone="+12025550101", **overrides):
    values = {
        "account_type": Account.AccountType.CUSTOMER,
        "status": TEST_STATUS,
        "password": TEST_PASSWORD,
    }
    values.update(overrides)
    return Account.objects.create_user(phone=phone, **values)


class PhoneNormalizationTests(SimpleTestCase):
    def test_formatted_international_number_is_canonical(self):
        self.assertEqual(
            normalize_phone(" +1 (202) 555-0101 "), "+12025550101"
        )

    def test_long_formatted_input_is_checked_after_normalization(self):
        raw = "+1 (234) 567-890-123-45"
        self.assertGreater(len(raw), 20)
        self.assertEqual(normalize_phone(raw), "+123456789012345")

    def test_invalid_numbers_and_non_ascii_digits_are_rejected(self):
        for raw in (
            "",
            "89995550101",
            "2025550101",
            "+0123",
            "+1",
            "+1234567890123456",
            "+1/2025550101",
            "++12025550101",
            "+１２３４",
            None,
            12025550101,
        ):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                normalize_phone(raw)


class AccountManagerTests(TestCase):
    def test_custom_user_identity_defaults_and_password_hash(self):
        account = create_account(" +1 (202) 555-0101 ")
        account.refresh_from_db()
        self.assertIs(get_user_model(), Account)
        self.assertIsInstance(account.pk, uuid.UUID)
        self.assertEqual(Account.USERNAME_FIELD, "phone")
        self.assertEqual(
            set(Account.REQUIRED_FIELDS), {"account_type", "status"}
        )
        self.assertEqual(account.phone, "+12025550101")
        self.assertEqual(account._state.db, "default")
        self.assertTrue(account.is_active)
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertIsNone(account.phone_verified_at)
        self.assertIsNone(account.last_login)
        self.assertIsNotNone(account.created_at)
        self.assertIsNotNone(account.updated_at)
        self.assertNotEqual(account.password, TEST_PASSWORD)
        self.assertTrue(account.check_password(TEST_PASSWORD))
        self.assertFalse(account.check_password("incorrect-test-password"))
        self.assertEqual(
            identify_hasher(account.password).algorithm, "pbkdf2_sha256"
        )

    def test_status_has_no_business_default_or_choices(self):
        field = Account._meta.get_field("status")
        self.assertFalse(field.has_default())
        self.assertFalse(field.null)
        self.assertFalse(field.blank)
        self.assertFalse(field.choices)
        self.assertEqual(field.max_length, 20)
        account = create_account(status="opaque-test-value")
        self.assertEqual(account.status, "opaque-test-value")

    def test_required_manager_arguments(self):
        for kwargs in (
            {"phone": None, "account_type": "customer", "status": TEST_STATUS},
            {"phone": "+12025550101", "status": TEST_STATUS},
            {"phone": "+12025550101", "account_type": "customer"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Account.objects.create_user(password=TEST_PASSWORD, **kwargs)

    def test_empty_or_too_long_status_is_rejected(self):
        for status in ("", "   ", "x" * 21):
            with (
                self.subTest(status=status),
                self.assertRaises((ValueError, ValidationError)),
            ):
                create_account(status=status)
        self.assertEqual(Account.objects.count(), 0)

    def test_only_customer_and_employee_types(self):
        employee = create_account(account_type=Account.AccountType.EMPLOYEE)
        self.assertFalse(employee.is_staff)
        with self.assertRaises(ValidationError):
            create_account("+12025550102", account_type="admin")

    def test_ordinary_creation_cannot_grant_staff_or_superuser(self):
        for flag in ("is_staff", "is_superuser"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                create_account(**{flag: True})
        self.assertEqual(Account.objects.count(), 0)

    def test_superuser_requires_and_preserves_both_flags(self):
        values = {"account_type": "employee", "status": TEST_STATUS}
        account = Account.objects.create_superuser(
            "+12025550101", TEST_PASSWORD, **values
        )
        self.assertTrue(account.is_staff)
        self.assertTrue(account.is_superuser)
        self.assertTrue(account.check_password(TEST_PASSWORD))
        self.assertEqual(account.account_type, "employee")
        for flag in ("is_staff", "is_superuser"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                Account.objects.create_superuser(
                    "+12025550102", TEST_PASSWORD, **values, **{flag: False}
                )

    def test_no_password_means_unusable_password_for_ordinary_user(self):
        account = create_account(password=None)
        self.assertFalse(account.has_usable_password())
        self.assertNotEqual(account.password, "")

    def test_superuser_without_password_is_rejected(self):
        with self.assertRaises(ValueError):
            Account.objects.create_superuser(
                "+12025550101",
                None,
                account_type="employee",
                status=TEST_STATUS,
            )

    def test_direct_save_and_full_clean_use_the_same_normalization(self):
        account = Account(
            phone="+1 (234) 567-890-123-45",
            account_type="customer",
            status=TEST_STATUS,
        )
        account.set_password(TEST_PASSWORD)
        account.full_clean()
        account.save()
        account.refresh_from_db()
        self.assertEqual(account.phone, "+123456789012345")

    def test_formatted_natural_key_and_authentication(self):
        account = create_account()
        self.assertEqual(
            Account.objects.get_by_natural_key("+1 (202) 555-0101"), account
        )
        self.assertEqual(
            authenticate(username="+1 (202) 555-0101", password=TEST_PASSWORD),
            account,
        )
        self.assertIsNone(
            authenticate(username="invalid-number", password=TEST_PASSWORD)
        )

    def test_inactive_account_cannot_authenticate(self):
        account = create_account(is_active=False)
        self.assertIsNone(
            authenticate(username=account.phone, password=TEST_PASSWORD)
        )

    def test_groups_and_permissions_use_standard_django_infrastructure(self):
        account = create_account()
        group = Group.objects.create(name="Account test viewers")
        permission = Permission.objects.get(
            content_type__app_label="accounts", codename="view_account"
        )
        group.permissions.add(permission)
        account.groups.add(group)
        account = Account.objects.get(pk=account.pk)
        self.assertTrue(account.has_perm("accounts.view_account"))
        self.assertFalse(account.has_perm("accounts.change_account"))
        account.is_active = False
        account.save(update_fields=["is_active"])
        account = Account.objects.get(pk=account.pk)
        self.assertFalse(account.has_perm("accounts.view_account"))


class AccountPostgreSQLTests(TestCase):
    def test_actual_postgresql_columns_match_erd(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(Account._meta.db_table, "account")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='account'"
            )
            actual = {
                name: (kind, null, length)
                for name, kind, null, length in cursor.fetchall()
            }
        expected = {
            "id": ("uuid", "NO", None),
            "phone": ("character varying", "NO", 20),
            "password": ("character varying", "NO", 128),
            "account_type": ("character varying", "NO", 20),
            "status": ("character varying", "NO", 20),
            "is_active": ("boolean", "NO", None),
            "is_staff": ("boolean", "NO", None),
            "is_superuser": ("boolean", "NO", None),
            "last_login": ("timestamp with time zone", "YES", None),
            "phone_verified_at": ("timestamp with time zone", "YES", None),
            "created_at": ("timestamp with time zone", "NO", None),
            "updated_at": ("timestamp with time zone", "NO", None),
        }
        self.assertEqual(actual, expected)

    def test_database_enforces_global_phone_uniqueness_across_types(self):
        first = create_account()
        other = Account(
            phone=first.phone, account_type="employee", status=TEST_STATUS
        )
        other.set_password(TEST_PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.bulk_create([other])
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "account"
            )
        self.assertTrue(
            any(
                c["unique"] and c["columns"] == ["phone"]
                for c in constraints.values()
            )
        )

    def test_database_rejects_admin_type_and_noncanonical_phone(self):
        for phone, kind in (
            ("+12025550101", "admin"),
            ("+1 (202) 555-0101", "customer"),
        ):
            account = Account(
                phone=phone, account_type=kind, status=TEST_STATUS
            )
            account.set_password(TEST_PASSWORD)
            with (
                self.subTest(phone=phone, kind=kind),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Account.objects.bulk_create([account])

    def test_migration_graph_and_infrastructure_match_stage_six_domain_models(
        self,
    ):
        loader = MigrationLoader(connection)
        self.assertIn(("accounts", "0001_initial"), loader.applied_migrations)
        self.assertEqual(
            loader.graph.leaf_nodes("accounts"), [("accounts", "0002_employee")]
        )
        tables = set(connection.introspection.table_names())
        self.assertTrue(
            {
                "account",
                "account_groups",
                "account_user_permissions",
                "auth_group",
                "auth_permission",
            }.issubset(tables)
        )
        self.assertNotIn("auth_user", tables)
        self.assertNotIn("accounts_account", tables)
        domain_models = [
            m
            for app in apps.get_app_configs()
            if app.name.startswith("apps.")
            for m in app.get_models()
        ]
        self.assertEqual(
            {model._meta.db_table for model in domain_models},
            {
                "account",
                "employee",
                "product_category",
                "attribute_definition",
                "pickup_point",
                "pickup_point_working_hours",
                "product",
                "category_attribute",
                "product_attribute_value",
                "product_component",
                "product_tariff",
                "product_item",
                "product_item_location_history",
                "reservation",
                "reservation_item_assignment",
                "payment_method",
                "rental",
                "inspection",
                "inspection_problem",
                "payment",
                "debt",
            },
        )
        self.assertEqual(len(domain_models), 21)


class DatabaseLayerCompatibilityTests(TestCase):
    def test_all_domain_models_and_admin_discovery_import_without_business_modules(self):
        # A fresh interpreter prevents an already imported workflow module from
        # hiding a remaining dependency. This child may not connect to any DB.
        script = textwrap.dedent(
            """
            import importlib
            import importlib.abc
            import sys

            forbidden = {
                'apps.payments._internal',
                'apps.payments.errors',
                'apps.payments.finance',
                'apps.payments.services',
                'apps.rentals._internal',
                'apps.rentals.errors',
                'apps.rentals.money',
                'apps.rentals.services',
                'apps.reservations._internal',
                'apps.reservations.services',
            }

            class NoBusinessImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + '.')
                           for name in forbidden):
                        raise AssertionError('DB layer imported a removed workflow module')
                    return None

            sys.meta_path.insert(0, NoBusinessImports())
            from django.db.backends.base.base import BaseDatabaseWrapper

            def no_database_connection(*args, **kwargs):
                raise AssertionError('Model import must not connect to a database')

            BaseDatabaseWrapper.ensure_connection = no_database_connection
            import django
            django.setup()
            from django.apps import apps
            from django.contrib import admin
            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Group
            from apps.accounts.models import Account, Employee
            from apps.accounts.admin import AccountAdmin, EmployeeAdmin, TrustedGroupAdmin
            from apps.accounts.admin_access import TrustedAdminMixin

            domain = []
            for config in apps.get_app_configs():
                if config.name.startswith('apps.'):
                    importlib.import_module(config.name + '.models')
                    importlib.import_module(config.name + '.admin')
                    domain.extend(config.get_models())
            admin.autodiscover()
            assert len(domain) == 21
            assert get_user_model() is Account
            assert admin.site.is_registered(Account)
            assert admin.site.is_registered(Employee)
            assert admin.site.is_registered(Group)
            for model, expected in (
                (Account, AccountAdmin), (Employee, EmployeeAdmin),
                (Group, TrustedGroupAdmin),
            ):
                assert isinstance(admin.site._registry[model], expected)
                assert isinstance(admin.site._registry[model], TrustedAdminMixin)
            assert admin.site.login_form is None
            assert not (forbidden & set(sys.modules))
            print('db-layer-imports-ok')
            """
        )
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "db-layer-imports-ok")

    def test_historical_migration_state_preserves_models_validators_and_account_manager(self):
        loader = MigrationLoader(connection)
        state = loader.project_state()
        labels = {
            config.label for config in apps.get_app_configs()
            if config.name.startswith("apps.")
        }
        current = {
            (model._meta.label_lower, model._meta.db_table)
            for config in apps.get_app_configs()
            if config.label in labels
            for model in config.get_models()
        }
        historical = {
            (model._meta.label_lower, model._meta.db_table)
            for model in state.apps.get_models()
            if model._meta.app_label in labels
        }
        self.assertEqual(len(historical), 21)
        self.assertEqual(historical, current)
        historical_account = state.apps.get_model("accounts", "Account")
        manager = historical_account._default_manager
        self.assertIsInstance(manager, AccountManager)
        self.assertIs(manager.model, historical_account)
        self.assertTrue(manager.use_in_migrations)
        self.assertEqual(manager.deconstruct(), Account.objects.deconstruct())
        self.assertTrue(callable(manager.create_user))
        self.assertTrue(callable(manager.create_superuser))
        self.assertIn(
            validate_phone, historical_account._meta.get_field("phone").validators
        )
        self.assertIn(
            validate_status, historical_account._meta.get_field("status").validators
        )
        validate_phone("+12025550171")
        validate_status(TEST_STATUS)
        with self.assertRaises(ValidationError):
            validate_phone("not-a-phone")
        with self.assertRaises(ValidationError):
            validate_status("   ")

    def test_standard_admin_login_accepts_canonical_custom_account(self):
        account = Account.objects.create_superuser(
            "+12025550171", TEST_PASSWORD,
            account_type="employee", status=TEST_STATUS,
        )
        self.assertTrue(account.check_password(TEST_PASSWORD))
        from .admin import AccountAdmin, EmployeeAdmin, TrustedGroupAdmin
        from .admin_access import TrustedAdminMixin
        from .models import Employee

        for model, expected in (
            (Account, AccountAdmin), (Employee, EmployeeAdmin),
            (Group, TrustedGroupAdmin),
        ):
            self.assertTrue(admin.site.is_registered(model))
            self.assertIsInstance(admin.site._registry[model], expected)
            self.assertIsInstance(admin.site._registry[model], TrustedAdminMixin)
        self.assertIsNone(admin.site.login_form)
        response = self.client.post(
            reverse("admin:login"),
            {"username": account.phone, "password": TEST_PASSWORD,
             "next": reverse("admin:index")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(account.pk))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
