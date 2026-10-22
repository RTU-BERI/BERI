"""PaymentMethod SQL structure and service-only model write boundaries."""

import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from .models import PaymentMethod
from .test_support import FixtureMixin


class PaymentMethodSchemaTests(TestCase):
    def test_exact_twelve_columns_match_erd_types_and_nullability(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "id": ("uuid", "NO", None),
            "customer_account_id": ("uuid", "NO", None),
            "external_id": ("character varying", "NO", 255),
            "brand": ("character varying", "YES", 50),
            "last4": ("character varying", "YES", 4),
            "expires_month": ("smallint", "YES", None),
            "expires_year": ("smallint", "YES", None),
            "is_default": ("boolean", "NO", None),
            "status": ("character varying", "NO", 20),
            "verification_status": ("character varying", "NO", 20),
            "created_at": ("timestamp with time zone", "NO", None),
            "deactivated_at": ("timestamp with time zone", "YES", None),
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='payment_method'"
            )
            actual = {
                name: (kind, nullable, length)
                for name, kind, nullable, length in cursor.fetchall()
            }
        self.assertEqual(actual, expected)

    def test_uuid_primary_key_and_single_account_foreign_key(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "payment_method"
            )
        self.assertEqual(
            [c["columns"] for c in constraints.values() if c["primary_key"]],
            [["id"]],
        )
        self.assertEqual(
            {
                (c["columns"][0], *c["foreign_key"])
                for c in constraints.values() if c["foreign_key"]
            },
            {("customer_account_id", "account", "id")},
        )
        field = PaymentMethod._meta.get_field("customer_account")
        self.assertIs(field.remote_field.on_delete, models.PROTECT)
        self.assertEqual(field.remote_field.related_name, "payment_methods")

    def test_eight_approved_check_constraints_exist_in_postgresql(self):
        expected = {
            "payment_method_status_valid",
            "payment_method_verification_valid",
            "payment_method_deactivation_valid",
            "payment_method_default_usable",
            "payment_method_last4_valid",
            "payment_method_month_valid",
            "payment_method_year_valid",
            "payment_method_expiry_pair_valid",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='payment_method'::regclass AND contype='c'"
            )
            self.assertEqual({row[0] for row in cursor.fetchall()}, expected)

    def test_default_is_the_only_nonprimary_unique_and_is_partial(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "payment_method"
            )
            self.assertEqual(
                [c["columns"] for c in constraints.values()
                 if c["unique"] and not c["primary_key"]],
                [["customer_account_id"]],
            )
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='payment_method' "
                "AND indexname='payment_method_one_default'"
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        definition = row[0].lower()
        for fragment in ("unique", "customer_account_id", "where", "is_default"):
            self.assertIn(fragment, definition)
        self.assertNotIn("external_id", definition)

    def test_only_uuid_pk_has_a_python_default_and_no_column_has_a_sql_default(self):
        for field in PaymentMethod._meta.local_fields:
            with self.subTest(field=field.name):
                self.assertEqual(field.has_default(), field.name == "id")
                self.assertFalse(field.has_db_default())
        self.assertIs(PaymentMethod._meta.pk.default, uuid.uuid4)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='payment_method' "
                "AND column_default IS NOT NULL"
            )
            self.assertEqual(cursor.fetchall(), [])

    def test_two_independent_status_dictionaries_match_the_agreed_values(self):
        self.assertEqual(
            {value for value, label in PaymentMethod.Status.choices},
            {"active", "inactive"},
        )
        self.assertEqual(
            {value for value, label in PaymentMethod.VerificationStatus.choices},
            {"pending", "confirmed", "failed"},
        )
        self.assertFalse(PaymentMethod._meta.get_field("brand").choices)


class PaymentMethodModelTests(FixtureMixin, TestCase):
    def raw_values(self, **overrides):
        values = {
            "id": uuid.uuid4(),
            "customer_account_id": self.customer.pk,
            "external_id": "synthetic-model-identifier",
            "brand": None, "last4": None,
            "expires_month": None, "expires_year": None,
            "is_default": False,
            "status": "active", "verification_status": "pending",
            "created_at": timezone.now(), "deactivated_at": None,
        }
        values.update(overrides)
        return values

    def raw_insert(self, **overrides):
        values = self.raw_values(**overrides)
        quote = connection.ops.quote_name
        columns = ", ".join(quote(name) for name in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO payment_method ({columns}) "
                f"VALUES ({placeholders}) RETURNING id",
                list(values.values()),
            )
            pk = cursor.fetchone()[0]
        return PaymentMethod.objects.get(pk=pk)

    def candidate(self, **overrides):
        return PaymentMethod(**self.raw_values(**overrides))

    def assert_sqlstate(self, expected, operation):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                operation()
                connection.check_constraints()
        self.assertEqual(raised.exception.__cause__.sqlstate, expected)
        return raised.exception

    def test_uuid_identity_and_nullable_metadata_round_trip(self):
        self.method.refresh_from_db()
        self.assertIsInstance(self.method.pk, uuid.UUID)
        self.assertEqual(self.method.customer_account_id, self.customer.pk)
        for field in (
            "brand", "last4", "expires_month", "expires_year", "deactivated_at"
        ):
            with self.subTest(field=field):
                self.assertIsNone(getattr(self.method, field))
        self.assertTrue(timezone.is_aware(self.method.created_at))

    def test_sql_accepts_all_six_combinations_of_the_independent_statuses(self):
        now = timezone.now()
        for status in ("active", "inactive"):
            for verification in ("pending", "confirmed", "failed"):
                with self.subTest(status=status, verification=verification):
                    method = self.raw_insert(
                        status=status, verification_status=verification,
                        created_at=now,
                        deactivated_at=now if status == "inactive" else None,
                    )
                    self.assertEqual(method.status, status)
                    self.assertEqual(method.verification_status, verification)

    def test_sql_rejects_unknown_values_in_either_status_dictionary(self):
        for values in (
            {"status": "pending"}, {"status": "ACTIVE"}, {"status": ""},
            {"verification_status": "active"},
            {"verification_status": "CONFIRMED"},
            {"verification_status": ""},
        ):
            with self.subTest(values=values):
                self.assert_sqlstate("23514", lambda: self.raw_insert(**values))

    def test_active_status_rejects_a_deactivation_timestamp_in_sql(self):
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            deactivated_at=timezone.now()
        ))

    def test_inactive_null_date_cannot_bypass_the_check_as_sql_unknown(self):
        self.assert_sqlstate("23514", lambda: self.raw_insert(status="inactive"))

    def test_inactive_date_must_not_precede_creation_and_equality_is_valid(self):
        now = timezone.now()
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            status="inactive", created_at=now,
            deactivated_at=now - timedelta(microseconds=1),
        ))
        method = self.raw_insert(
            status="inactive", created_at=now, deactivated_at=now
        )
        self.assertEqual(method.deactivated_at, method.created_at)

    def test_default_requires_both_active_and_confirmed_in_sql(self):
        now = timezone.now()
        for status, verification in (
            ("active", "pending"), ("active", "failed"),
            ("inactive", "pending"), ("inactive", "confirmed"),
            ("inactive", "failed"),
        ):
            with self.subTest(status=status, verification=verification):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    is_default=True, status=status,
                    verification_status=verification, created_at=now,
                    deactivated_at=now if status == "inactive" else None,
                ))

    def test_sql_allows_only_one_default_for_a_customer(self):
        self.make_confirmed_method(is_default=True)
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            is_default=True, verification_status="confirmed"
        ))
        self.assertEqual(
            error.__cause__.diag.constraint_name, "payment_method_one_default"
        )

    def test_different_customers_can_each_have_a_default(self):
        first = self.make_confirmed_method(is_default=True)
        second = self.make_confirmed_method(
            customer=self.other_customer, is_default=True
        )
        self.assertTrue(first.is_default)
        self.assertTrue(second.is_default)
        self.assertNotEqual(first.customer_account_id, second.customer_account_id)

    def test_multiple_nondefault_methods_are_not_limited_by_the_unique_index(self):
        first = self.make_confirmed_method(is_default=True)
        others = [self.make_method(), self.make_method(), self.make_method()]
        self.assertTrue(first.is_default)
        self.assertTrue(all(not method.is_default for method in others))
        self.assertEqual(self.customer.payment_methods.count(), 5)

    def test_literal_external_id_duplicates_are_allowed_for_one_or_multiple_owners(
        self,
    ):
        external_id = "synthetic-repeat-identifier"
        methods = [
            self.raw_insert(external_id=external_id),
            self.raw_insert(external_id=external_id),
            self.raw_insert(
                external_id=external_id,
                customer_account_id=self.other_customer.pk,
            ),
        ]
        self.assertEqual(len({method.pk for method in methods}), 3)
        self.assertEqual({method.external_id for method in methods}, {external_id})

    def test_required_columns_reject_sql_nulls(self):
        for field in (
            "id", "customer_account_id", "external_id", "is_default",
            "status", "verification_status", "created_at",
        ):
            with self.subTest(field=field):
                self.assert_sqlstate("23502", lambda: self.raw_insert(
                    **{field: None}
                ))

    def test_uuid_foreign_key_rejects_a_nonexistent_account(self):
        self.assert_sqlstate("23503", lambda: self.raw_insert(
            customer_account_id=uuid.uuid4()
        ))

    def test_sql_rejects_empty_short_or_non_ascii_last4(self):
        for last4 in ("", "123", "abcd", "１２３４", "١٢٣٤"):
            with self.subTest(last4=last4):
                self.assert_sqlstate("23514", lambda: self.raw_insert(last4=last4))

    def test_four_ascii_digits_including_leading_zeroes_are_preserved(self):
        for last4 in ("0000", "0123", "9999"):
            with self.subTest(last4=last4):
                method = self.raw_insert(last4=last4)
                self.assertEqual(method.last4, last4)

    def test_month_and_year_ranges_are_independently_checked_in_sql(self):
        for field, invalid in (
            ("expires_month", -1), ("expires_month", 0), ("expires_month", 13),
            ("expires_year", -1), ("expires_year", 0), ("expires_year", 10000),
        ):
            values = {"expires_month": 1, "expires_year": 2000, field: invalid}
            with self.subTest(field=field, invalid=invalid):
                self.assert_sqlstate("23514", lambda: self.raw_insert(**values))

    def test_month_and_year_must_be_both_unset_or_both_present_in_sql(self):
        for values in (
            {"expires_month": 1}, {"expires_year": 2000},
        ):
            with self.subTest(values=values):
                self.assert_sqlstate("23514", lambda: self.raw_insert(**values))

    def test_calendar_boundaries_and_old_metadata_have_no_expiry_date_gate(self):
        for month, year in ((1, 1), (12, 9999)):
            with self.subTest(month=month, year=year):
                method = self.make_method(expires_month=month, expires_year=year)
                method.full_clean()
                self.assertEqual(method.expires_month, month)
                self.assertEqual(method.expires_year, year)

    def test_external_identifier_is_a_required_literal_string_without_coercion(self):
        for value in (None, "", " \t\n", 1234, b"synthetic-identifier"):
            with self.subTest(input_type=type(value).__name__):
                with self.assertRaises(ValidationError):
                    self.candidate(external_id=value).full_clean()

    def test_identifier_preserves_whitespace_unicode_and_case(self):
        external_id = "  Synthetic-ИД-ＭiXeD  "
        method = self.make_method(external_id=external_id)
        method.refresh_from_db()
        self.assertEqual(method.external_id, external_id)

    def test_external_identifier_has_the_erd_length_limit(self):
        candidate = self.candidate(external_id="x" * 255)
        candidate.full_clean()
        self.assertEqual(candidate.external_id, "x" * 255)
        with self.assertRaises(ValidationError):
            self.candidate(external_id="x" * 256).full_clean()

    def test_model_validation_enforces_metadata_shape_with_nullable_fields(self):
        for values in (
            {"brand": "x" * 51}, {"last4": ""}, {"last4": "１２３４"},
            {"expires_month": 1}, {"expires_year": 2000},
            {"expires_month": 13, "expires_year": 2000},
            {"expires_month": 1, "expires_year": 10000},
        ):
            with self.subTest(fields=tuple(values)):
                with self.assertRaises(ValidationError):
                    self.candidate(**values).full_clean()
        self.candidate().full_clean()

    def test_model_validation_requires_aware_timestamps(self):
        naive = timezone.now().replace(tzinfo=None)
        for values in (
            {"created_at": naive},
            {"status": "inactive", "deactivated_at": naive},
        ):
            with self.subTest(fields=tuple(values)):
                with self.assertRaises(ValidationError):
                    self.candidate(**values).full_clean()

    def test_string_representations_contain_only_the_type_and_uuid(self):
        method = self.candidate(
            brand="Synthetic-display-brand", last4="0123",
            external_id="synthetic-representation-identifier",
        )
        self.assertEqual(str(method), str(method.pk))
        self.assertEqual(repr(method), f"<PaymentMethod: {method.pk}>")

    def test_public_new_instance_save_and_manager_create_are_unsupported(self):
        values = self.raw_values()
        with self.assertRaises(NotSupportedError):
            PaymentMethod(**values).save()
        with self.assertRaises(NotSupportedError):
            PaymentMethod.objects.create(**values)
        self.assertEqual(PaymentMethod.objects.count(), 1)

    def test_public_existing_instance_save_and_delete_are_unsupported(self):
        for operation in (self.method.save, self.method.delete):
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    operation()
        self.assertTrue(PaymentMethod.objects.filter(pk=self.method.pk).exists())

    def test_queryset_and_guarded_base_manager_writes_are_unsupported(self):
        for manager in (PaymentMethod.objects, PaymentMethod._base_manager):
            for operation in (
                lambda: manager.filter(pk=self.method.pk).update(is_default=True),
                lambda: manager.filter(pk=self.method.pk).delete(),
                lambda: manager.bulk_create([self.method]),
                lambda: manager.bulk_update([self.method], ["is_default"]),
            ):
                with self.subTest(manager=manager.name):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
        self.method.refresh_from_db()
        self.assertFalse(self.method.is_default)
        self.assertEqual(self.method.status, "active")
        self.assertEqual(self.method.verification_status, "pending")

    def test_partial_save_cannot_rewrite_owner_identifier_or_metadata(self):
        original = PaymentMethod.objects.get(pk=self.method.pk)
        for name, changed in (
            ("customer_account", self.other_customer),
            ("external_id", "synthetic-rewrite-identifier"),
            ("created_at", self.method.created_at - timedelta(days=1)),
            ("brand", "Synthetic new brand"),
            ("verification_status", "confirmed"),
        ):
            with self.subTest(field=name):
                setattr(self.method, name, changed)
                with self.assertRaises(NotSupportedError):
                    self.method.save(update_fields=[name])
                self.method.refresh_from_db()
                self.assertEqual(
                    self.method.customer_account_id, original.customer_account_id
                )
                self.assertEqual(self.method.external_id, original.external_id)
                self.assertEqual(self.method.created_at, original.created_at)
                self.assertEqual(self.method.brand, original.brand)
                self.assertEqual(
                    self.method.verification_status, original.verification_status
                )

    def test_constructor_with_existing_primary_key_cannot_overwrite_a_record(self):
        candidate = self.candidate(
            id=self.method.pk, customer_account_id=self.other_customer.pk,
            external_id="synthetic-overwrite-identifier",
        )
        with self.assertRaises(NotSupportedError):
            candidate.save()
        self.method.refresh_from_db()
        self.assertEqual(self.method.customer_account_id, self.customer.pk)
        self.assertNotEqual(self.method.external_id, candidate.external_id)

    def test_reverse_add_set_and_nonbulk_add_cannot_reassign_ownership(self):
        manager = self.other_customer.payment_methods
        for operation in (
            lambda: manager.add(self.method),
            lambda: manager.set([self.method]),
            lambda: manager.add(self.method, bulk=False),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    with transaction.atomic():
                        operation()
                self.method.refresh_from_db()
                self.assertEqual(self.method.customer_account_id, self.customer.pk)

    def test_reverse_create_cannot_introduce_a_method_outside_services(self):
        values = self.raw_values()
        values.pop("customer_account_id")
        with self.assertRaises(NotSupportedError):
            with transaction.atomic():
                self.other_customer.payment_methods.create(**values)
        self.assertEqual(PaymentMethod.objects.count(), 1)

    def test_base_manager_remains_unfiltered_for_inactive_or_failed_history(self):
        now = timezone.now()
        method = self.raw_insert(
            status="inactive", verification_status="failed",
            created_at=now, deactivated_at=now,
        )
        self.assertEqual(PaymentMethod._base_manager.get(pk=method.pk).pk, method.pk)

    def test_customer_deletion_is_protected_by_payment_method_history(self):
        with self.assertRaises(ProtectedError):
            self.customer.delete()
        self.assertTrue(PaymentMethod.objects.filter(pk=self.method.pk).exists())

    def test_postgresql_foreign_key_blocks_raw_account_deletion(self):
        def delete_customer():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM account WHERE id=%s", [self.customer.pk])

        self.assert_sqlstate("23503", delete_customer)
        self.method.refresh_from_db()
        self.assertEqual(self.method.customer_account_id, self.customer.pk)
