"""Payment/Debt structure and write guards, using synthetic SQL history only."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from .financial_test_support import FinancialFixtureMixin
from .models import Debt, Payment


FINANCIAL_MODELS = (Payment, Debt)


class FinancialSchemaTests(TestCase):
    def test_exact_sixteen_columns_types_nullability_and_lengths(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "payment": {
                "id": ("uuid", "NO", None),
                "rental_id": ("uuid", "NO", None),
                "payment_method_id": ("uuid", "NO", None),
                "external_id": ("character varying", "YES", 255),
                "amount": ("numeric", "NO", None),
                "status": ("character varying", "NO", 20),
                "failure_code": ("character varying", "YES", 100),
                "failure_message": ("text", "YES", None),
                "created_at": ("timestamp with time zone", "NO", None),
                "resolved_at": ("timestamp with time zone", "YES", None),
            },
            "debt": {
                "id": ("uuid", "NO", None),
                "rental_id": ("uuid", "NO", None),
                "amount": ("numeric", "NO", None),
                "status": ("character varying", "NO", 20),
                "created_at": ("timestamp with time zone", "NO", None),
                "settled_at": ("timestamp with time zone", "YES", None),
            },
        }
        with connection.cursor() as cursor:
            for table, columns in expected.items():
                with self.subTest(table=table):
                    cursor.execute(
                        "SELECT column_name,data_type,is_nullable,"
                        "character_maximum_length FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s", [table],
                    )
                    self.assertEqual(
                        {row[0]: tuple(row[1:]) for row in cursor}, columns
                    )
                    cursor.execute(
                        "SELECT numeric_precision,numeric_scale "
                        "FROM information_schema.columns WHERE table_schema='public' "
                        "AND table_name=%s AND column_name='amount'", [table],
                    )
                    self.assertEqual(cursor.fetchone(), (12, 2))

    def test_uuid_primary_keys_and_exact_three_protected_foreign_keys(self):
        expected = {
            Payment: {
                ("rental_id", "rental", "id"),
                ("payment_method_id", "payment_method", "id"),
            },
            Debt: {("rental_id", "rental", "id")},
        }
        with connection.cursor() as cursor:
            for model, foreign_keys in expected.items():
                with self.subTest(model=model.__name__):
                    constraints = connection.introspection.get_constraints(
                        cursor, model._meta.db_table
                    )
                    self.assertEqual(
                        [c["columns"] for c in constraints.values()
                         if c["primary_key"]], [["id"]],
                    )
                    self.assertEqual(
                        {(c["columns"][0], *c["foreign_key"])
                         for c in constraints.values() if c["foreign_key"]},
                        foreign_keys,
                    )
                    self.assertIsInstance(model._meta.pk, models.UUIDField)
                    for field in model._meta.local_fields:
                        if field.is_relation:
                            self.assertIs(field.remote_field.on_delete, models.PROTECT)

    def test_exact_six_named_checks_and_two_partial_unique_indexes(self):
        expected = {
            "payment": {
                "payment_status_valid", "payment_state_dates_valid",
                "payment_amount_positive_finite",
            },
            "debt": {
                "debt_status_valid", "debt_state_dates_valid",
                "debt_amount_positive_finite",
            },
        }
        with connection.cursor() as cursor:
            for table, names in expected.items():
                cursor.execute(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid=%s::regclass AND contype='c'", [table],
                )
                self.assertEqual({row[0] for row in cursor}, names)
            for name, status in (
                ("payment_one_pending_per_rental", "pending"),
                ("payment_one_succeeded_per_rental", "succeeded"),
            ):
                with self.subTest(index=name):
                    cursor.execute(
                        "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                        "AND tablename='payment' AND indexname=%s", [name],
                    )
                    row = cursor.fetchone()
                    self.assertIsNotNone(row)
                    for fragment in ("UNIQUE INDEX", "rental_id", "WHERE", status):
                        self.assertIn(fragment, row[0])

    def test_debt_rental_is_unique_but_external_operation_id_is_not(self):
        field = Debt._meta.get_field("rental")
        self.assertTrue(field.unique)
        self.assertFalse(field.primary_key)
        with connection.cursor() as cursor:
            payment = connection.introspection.get_constraints(cursor, "payment")
            debt = connection.introspection.get_constraints(cursor, "debt")
        self.assertEqual(
            {name for name, c in payment.items()
             if c["unique"] and not c["primary_key"]},
            {"payment_one_pending_per_rental", "payment_one_succeeded_per_rental"},
        )
        self.assertEqual(
            [c["columns"] for c in debt.values()
             if c["unique"] and not c["primary_key"]], [["rental_id"]],
        )
        self.assertFalse(Payment._meta.get_field("external_id").unique)

    def test_only_uuid_has_a_default_and_no_sql_default_or_automatic_event_time(self):
        for model in FINANCIAL_MODELS:
            self.assertEqual(model._meta.base_manager_name, "objects")
            self.assertIs(model._meta.pk.default, uuid.uuid4)
            for field in model._meta.local_fields:
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertEqual(field.has_default(), field.name == "id")
                    self.assertFalse(field.has_db_default())
                    self.assertFalse(getattr(field, "auto_now", False))
                    self.assertFalse(getattr(field, "auto_now_add", False))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN ('payment','debt') "
                "AND column_default IS NOT NULL"
            )
            self.assertEqual(cursor.fetchall(), [])

    def test_only_approved_tables_and_the_single_new_migration_exist(self):
        domain = {
            "account", "employee", "product_category", "attribute_definition",
            "product", "category_attribute", "product_attribute_value",
            "product_component", "product_tariff", "pickup_point",
            "pickup_point_working_hours", "product_item",
            "product_item_location_history", "reservation",
            "reservation_item_assignment", "payment_method", "rental",
            "inspection", "inspection_problem", "payment", "debt",
        }
        infrastructure = {
            "account_groups", "account_user_permissions", "auth_group",
            "auth_group_permissions", "auth_permission", "django_admin_log",
            "django_content_type", "django_migrations", "django_session",
        }
        self.assertEqual(
            set(connection.introspection.table_names()), domain | infrastructure
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM django_migrations")
            self.assertEqual(cursor.fetchone()[0], 29)


class FinancialModelTests(FinancialFixtureMixin, TestCase):
    def assert_sqlstate(self, expected, operation, *, error_class=IntegrityError):
        with self.assertRaises(error_class) as raised:
            with transaction.atomic():
                operation()
                connection.check_constraints()
        self.assertEqual(raised.exception.__cause__.sqlstate, expected)
        return raised.exception

    def test_pending_nullable_metadata_and_uuid_identity_round_trip(self):
        payment = self.make_payment()
        self.assertIsInstance(payment.pk, uuid.UUID)
        self.assertEqual(payment.rental_id, self.rental.pk)
        self.assertEqual(payment.payment_method_id, self.payment_method.pk)
        self.assertEqual(payment.amount, Decimal("12.00"))
        for name in ("external_id", "failure_code", "failure_message", "resolved_at"):
            with self.subTest(field=name):
                self.assertIsNone(getattr(payment, name))

    def test_payment_and_debt_status_dictionaries_are_distinct(self):
        self.assertEqual(set(Payment.Status.values), {"pending", "succeeded", "failed"})
        self.assertEqual(set(Debt.Status.values), {"open", "paid"})
        for model, values, statuses in (
            (Payment, self.payment_values(), ("open", "PENDING", "")),
            (Debt, self.debt_values(), ("succeeded", "OPEN", "")),
        ):
            for status in statuses:
                with self.subTest(model=model.__name__, status=status):
                    self.assert_sqlstate("23514", lambda: self.raw_insert(
                        model, **{**values, "status": status}
                    ))

    def test_pending_rejects_any_resolution_or_failure_metadata(self):
        for name, value in (
            ("resolved_at", timezone.now()),
            ("failure_code", "synthetic-decline"),
            ("failure_message", "Synthetic safe failure description"),
        ):
            with self.subTest(field=name):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Payment, **self.payment_values(**{name: value})
                ))

    def test_succeeded_rejects_either_failure_field(self):
        for name, value in (
            ("failure_code", "synthetic-decline"),
            ("failure_message", "Synthetic safe failure description"),
        ):
            with self.subTest(field=name):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Payment, **self.payment_values(status="succeeded", **{name: value})
                ))

    def test_terminal_null_resolution_cannot_pass_as_sql_unknown(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Payment, **self.payment_values(status=status, resolved_at=None)
                ))

    def test_terminal_resolution_cannot_precede_attempt_creation(self):
        now = timezone.now()
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Payment, **self.payment_values(
                        status=status, created_at=now,
                        resolved_at=now - timedelta(microseconds=1),
                    )
                ))

    def test_terminal_resolution_can_equal_creation_and_external_id_can_be_null(self):
        now = timezone.now()
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                payment = self.make_payment(
                    status=status, created_at=now, resolved_at=now, external_id=None
                )
                self.assertEqual(payment.resolved_at, payment.created_at)
                self.assertIsNone(payment.external_id)

    def test_failed_code_and_message_are_independently_nullable(self):
        for code, message in (
            (None, None), ("synthetic-decline", None),
            (None, "Synthetic safe description"),
            ("synthetic-decline", "Synthetic safe description"),
        ):
            with self.subTest(
                has_code=code is not None, has_message=message is not None
            ):
                payment = self.make_payment(
                    status="failed", failure_code=code, failure_message=message
                )
                self.assertEqual(payment.failure_code, code)
                self.assertEqual(payment.failure_message, message)

    def test_only_one_pending_attempt_is_allowed_for_a_rental(self):
        self.make_payment()
        error = self.assert_sqlstate("23505", lambda: self.make_payment())
        self.assertEqual(
            error.__cause__.diag.constraint_name, "payment_one_pending_per_rental"
        )

    def test_only_one_succeeded_attempt_is_allowed_for_a_rental(self):
        self.make_payment(status="succeeded")
        error = self.assert_sqlstate(
            "23505", lambda: self.make_payment(status="succeeded")
        )
        self.assertEqual(
            error.__cause__.diag.constraint_name, "payment_one_succeeded_per_rental"
        )

    def test_two_partial_indexes_alone_allow_pending_and_succeeded_together(self):
        pending = self.make_payment()
        succeeded = self.make_payment(status="succeeded")
        self.assertNotEqual(pending.pk, succeeded.pk)
        self.assertEqual(self.rental.payments.count(), 2)
        # Financial source validation, not these indexes, rejects this history.

    def test_many_failed_attempts_do_not_consume_pending_or_succeeded_uniqueness(self):
        failed = [self.make_payment(status="failed") for _ in range(3)]
        pending = self.make_payment()
        self.assertEqual(len({payment.pk for payment in failed}), 3)
        self.assertEqual(pending.status, "pending")
        self.assertEqual(self.rental.payments.count(), 4)

    def test_different_rentals_have_independent_payment_uniqueness(self):
        other_rental = self.make_completed_rental()
        first = self.make_payment(status="succeeded")
        second = self.make_payment(rental=other_rental, status="succeeded")
        self.assertNotEqual(first.rental_id, second.rental_id)

    def test_external_operation_identifiers_are_not_unique_or_normalized(self):
        external_id = "  Synthetic-OP-ИД-MiXeD  "
        first = self.make_payment(status="failed", external_id=external_id)
        second = self.make_payment(status="failed", external_id=external_id)
        other = self.make_payment(
            rental=self.make_completed_rental(), status="failed",
            external_id=external_id,
        )
        self.assertEqual(len({first.pk, second.pk, other.pk}), 3)
        for payment in (first, second, other):
            payment.refresh_from_db()
            self.assertEqual(payment.external_id, external_id)

    def test_nullable_operation_id_full_clean_rejects_nonliteral_or_blank_values(self):
        Payment(**self.payment_values(external_id=None)).full_clean()
        for value in ("", " \t\n", 1234, b"synthetic-operation"):
            with self.subTest(input_type=type(value).__name__):
                with self.assertRaises(ValidationError):
                    Payment(**self.payment_values(external_id=value)).full_clean()

    def test_operation_id_length_is_255_and_failure_code_length_is_100(self):
        Payment(**self.payment_values(external_id="x" * 255)).full_clean()
        with self.assertRaises(ValidationError):
            Payment(**self.payment_values(external_id="x" * 256)).full_clean()
        Payment(**self.payment_values(
            status="failed", failure_code="x" * 100
        )).full_clean()
        with self.assertRaises(ValidationError):
            Payment(**self.payment_values(
                status="failed", failure_code="x" * 101
            )).full_clean()

    def test_debt_has_its_own_uuid_and_an_open_null_settlement(self):
        debt = self.make_debt()
        self.assertIsInstance(debt.pk, uuid.UUID)
        self.assertNotEqual(debt.pk, self.rental.pk)
        self.assertEqual(debt.rental_id, self.rental.pk)
        self.assertEqual(debt.amount, Decimal("12.00"))
        self.assertIsNone(debt.settled_at)

    def test_debt_rental_is_unique_across_both_debt_statuses(self):
        self.make_debt()
        self.assert_sqlstate("23505", lambda: self.make_debt(status="paid"))

    def test_open_debt_rejects_a_settlement_timestamp(self):
        self.assert_sqlstate("23514", lambda: self.make_debt(settled_at=timezone.now()))

    def test_paid_null_settlement_cannot_bypass_the_check_as_sql_unknown(self):
        self.assert_sqlstate(
            "23514", lambda: self.make_debt(status="paid", settled_at=None)
        )

    def test_paid_debt_settlement_cannot_precede_creation(self):
        now = timezone.now()
        self.assert_sqlstate("23514", lambda: self.make_debt(
            status="paid", created_at=now, settled_at=now - timedelta(microseconds=1)
        ))

    def test_paid_debt_can_settle_exactly_at_its_creation(self):
        now = timezone.now()
        debt = self.make_debt(status="paid", created_at=now, settled_at=now)
        self.assertEqual(debt.settled_at, debt.created_at)

    def test_zero_negative_and_nan_amounts_fail_the_named_checks(self):
        for model, values, constraint in (
            (Payment, self.payment_values(), "payment_amount_positive_finite"),
            (Debt, self.debt_values(), "debt_amount_positive_finite"),
        ):
            for amount in (Decimal("0.00"), Decimal("-0.01"), Decimal("NaN")):
                with self.subTest(model=model.__name__, amount=amount):
                    error = self.assert_sqlstate("23514", lambda: self.raw_insert(
                        model, **{**values, "amount": amount}
                    ))
                    self.assertEqual(error.__cause__.diag.constraint_name, constraint)

    def test_infinities_and_precision_overflow_fail_at_the_numeric_type_boundary(
        self,
    ):
        for model, values in (
            (Payment, self.payment_values()), (Debt, self.debt_values()),
        ):
            for amount in (
                Decimal("Infinity"), Decimal("-Infinity"), Decimal("10000000000.00"),
            ):
                with self.subTest(model=model.__name__, amount=amount):
                    self.assert_sqlstate("22003", lambda: self.raw_insert(
                        model, **{**values, "amount": amount}
                    ), error_class=DataError)

    def test_smallest_positive_cent_and_maximum_schema_amount_round_trip(self):
        maximum = Decimal("9999999999.99")
        payment = self.make_payment(amount=maximum)
        debt = self.make_debt(amount=Decimal("0.01"))
        self.assertEqual(payment.amount, maximum)
        self.assertEqual(debt.amount, Decimal("0.01"))

    def test_every_nonnullable_column_rejects_explicit_sql_null(self):
        for model, values, required in (
            (Payment, self.payment_values(), (
                "id", "rental_id", "payment_method_id", "amount",
                "status", "created_at",
            )),
            (Debt, self.debt_values(), (
                "id", "rental_id", "amount", "status", "created_at",
            )),
        ):
            for field in required:
                with self.subTest(model=model.__name__, field=field):
                    self.assert_sqlstate("23502", lambda: self.raw_insert(
                        model, **{**values, field: None}
                    ))

    def test_each_of_three_foreign_keys_rejects_nonexistent_uuid_parents(self):
        for model, values, field in (
            (Payment, self.payment_values(), "rental_id"),
            (Payment, self.payment_values(), "payment_method_id"),
            (Debt, self.debt_values(), "rental_id"),
        ):
            with self.subTest(model=model.__name__, field=field):
                self.assert_sqlstate("23503", lambda: self.raw_insert(
                    model, **{**values, field: uuid.uuid4()}
                ))

    def test_naive_financial_timestamps_are_rejected_by_model_validation(self):
        naive = timezone.now().replace(tzinfo=None)
        candidates = (
            Payment(**self.payment_values(created_at=naive)),
            Payment(**self.payment_values(status="failed", resolved_at=naive)),
            Debt(**self.debt_values(created_at=naive)),
            Debt(**self.debt_values(status="paid", settled_at=naive)),
        )
        for candidate in candidates:
            with self.subTest(model=type(candidate).__name__):
                with self.assertRaises(ValidationError):
                    candidate.full_clean()
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Debt.objects.count(), 0)

    def test_row_checks_do_not_claim_cross_table_amount_or_verified_payment_truth(
        self,
    ):
        payment = self.make_payment(status="failed", amount=Decimal("0.01"))
        debt = self.make_debt(status="paid", amount=Decimal("0.02"))
        self.assertNotEqual(payment.amount, self.rental.final_amount)
        self.assertNotEqual(debt.amount, self.rental.final_amount)
        self.assertFalse(self.rental.payments.filter(status="succeeded").exists())
        # The read-only financial validator must reject this synthetic history.

    def test_string_representations_are_uuid_only_without_operation_or_failure_data(
        self,
    ):
        payment = self.make_payment(
            status="failed", external_id="synthetic-operation-sentinel",
            failure_code="synthetic-code", failure_message="Synthetic private detail",
        )
        debt = self.make_debt()
        for instance in (payment, debt):
            self.assertEqual(str(instance), str(instance.pk))
            self.assertEqual(
                repr(instance), f"<{type(instance).__name__}: {instance.pk}>"
            )

    def history_objects(self):
        return self.make_payment(status="failed"), self.make_debt()

    def test_public_save_partial_save_and_delete_cannot_rewrite_history(self):
        for instance in self.history_objects():
            for operation in (
                lambda: instance.save(),
                lambda: instance.save(update_fields=["amount"]),
                lambda: instance.delete(),
            ):
                with self.subTest(model=type(instance).__name__):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
            self.assertTrue(type(instance).objects.filter(pk=instance.pk).exists())

    def test_public_create_and_existing_primary_key_overwrite_are_unsupported(self):
        for instance in self.history_objects():
            model = type(instance)
            values = {
                f.attname: getattr(instance, f.attname)
                for f in model._meta.local_fields
            }
            with self.subTest(model=model.__name__):
                with self.assertRaises(NotSupportedError):
                    model.objects.create(**values)
                values["amount"] = Decimal("0.01")
                with self.assertRaises(NotSupportedError):
                    model(**values).save(force_update=True)
                instance.refresh_from_db()
                self.assertEqual(instance.amount, Decimal("12.00"))

    def test_default_and_base_managers_block_all_mass_writes(self):
        for instance in self.history_objects():
            model = type(instance)
            for manager in (model.objects, model._base_manager):
                for operation in (
                    lambda: manager.filter(pk=instance.pk).update(
                        amount=Decimal("0.01")
                    ),
                    lambda: manager.filter(pk=instance.pk).delete(),
                    lambda: manager.bulk_create([instance]),
                    lambda: manager.bulk_update([instance], ["amount"]),
                ):
                    with self.subTest(model=model.__name__, manager=manager.name):
                        with self.assertRaises(NotSupportedError):
                            with transaction.atomic():
                                operation()
            instance.refresh_from_db()
            self.assertEqual(instance.amount, Decimal("12.00"))

    def test_reverse_add_set_and_nonbulk_add_cannot_reassign_payment_parents(self):
        payment = self.make_payment(status="failed")
        other_rental = self.make_completed_rental()
        other_method = other_rental.payment_method
        for manager, field, expected in (
            (other_rental.payments, "rental_id", self.rental.pk),
            (other_method.payments, "payment_method_id", self.payment_method.pk),
        ):
            for operation in (
                lambda: manager.add(payment),
                lambda: manager.set([payment]),
                lambda: manager.add(payment, bulk=False),
            ):
                with self.subTest(field=field):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
                    payment.refresh_from_db()
                    self.assertEqual(getattr(payment, field), expected)

    def test_reverse_create_cannot_bypass_the_payment_writer_gate(self):
        values = self.payment_values()
        values.pop("rental_id")
        with self.assertRaises(NotSupportedError):
            with transaction.atomic():
                self.rental.payments.create(**values)
        self.assertEqual(Payment.objects.count(), 0)

    def test_reverse_one_to_one_reassignment_cannot_persist_a_debt_move(self):
        debt = self.make_debt()
        other_rental = self.make_completed_rental()
        other_rental.debt = debt
        with self.assertRaises(NotSupportedError):
            debt.save(update_fields=["rental"])
        debt.refresh_from_db()
        self.assertEqual(debt.rental_id, self.rental.pk)

    def test_base_managers_preserve_read_access_to_failed_and_paid_history(self):
        payment = self.make_payment(status="failed")
        debt = self.make_debt(status="paid")
        for instance in (payment, debt):
            self.assertEqual(
                type(instance)._base_manager.get(pk=instance.pk).pk, instance.pk
            )

    def test_collector_protects_both_rental_links_and_the_payment_method(self):
        payment, debt = self.history_objects()
        for parent, children in (
            (self.rental, {payment, debt}),
            (self.payment_method, {payment}),
        ):
            with self.subTest(parent=type(parent).__name__):
                # Explicit base delete exercises Collector below public guards.
                with self.assertRaises(ProtectedError) as raised:
                    models.Model.delete(parent)
                self.assertTrue(children <= set(raised.exception.protected_objects))

    def test_postgresql_foreign_keys_prevent_deleting_referenced_parents(self):
        self.history_objects()
        for table, pk in (
            ("rental", self.rental.pk), ("payment_method", self.payment_method.pk),
        ):
            def delete_parent():
                with connection.cursor() as cursor:
                    cursor.execute(f"DELETE FROM {table} WHERE id=%s", [pk])

            with self.subTest(table=table):
                self.assert_sqlstate("23503", delete_parent)
