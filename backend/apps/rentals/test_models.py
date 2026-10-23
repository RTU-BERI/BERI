"""PostgreSQL structure and immutable ORM boundaries, using synthetic history."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import ProductComponent

from .models import Inspection, InspectionProblem, Rental
from .test_support import FixtureMixin


HISTORY_MODELS = (Rental, Inspection, InspectionProblem)


class RentalSchemaTests(TestCase):
    def test_exact_twenty_nine_columns_types_lengths_and_nullability(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "rental": {
                "id": ("uuid", "NO", None),
                "reservation_id": ("uuid", "NO", None),
                "product_item_id": ("bigint", "NO", None),
                "tariff_id": ("bigint", "NO", None),
                "payment_method_id": ("uuid", "NO", None),
                "hourly_rate_applied": ("numeric", "NO", None),
                "started_by_employee_id": ("bigint", "NO", None),
                "ended_by_employee_id": ("bigint", "YES", None),
                "status": ("character varying", "NO", 20),
                "started_at": ("timestamp with time zone", "NO", None),
                "ended_at": ("timestamp with time zone", "YES", None),
                "final_amount": ("numeric", "YES", None),
            },
            "inspection": {
                "id": ("bigint", "NO", None),
                "product_item_id": ("bigint", "NO", None),
                "employee_id": ("bigint", "NO", None),
                "inspection_type": ("character varying", "NO", 20),
                "reservation_id": ("uuid", "YES", None),
                "rental_id": ("uuid", "YES", None),
                "condition_ok": ("boolean", "NO", None),
                "completeness_ok": ("boolean", "NO", None),
                "comment": ("text", "YES", None),
                "customer_confirmed_at": ("timestamp with time zone", "YES", None),
                "inspected_at": ("timestamp with time zone", "NO", None),
            },
            "inspection_problem": {
                "id": ("bigint", "NO", None),
                "inspection_id": ("bigint", "NO", None),
                "problem_type": ("character varying", "NO", 30),
                "description": ("text", "NO", None),
                "product_component_id": ("bigint", "YES", None),
                "missing_quantity": ("integer", "YES", None),
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
                        {r[0]: tuple(r[1:]) for r in cursor.fetchall()}, columns
                    )
            cursor.execute(
                "SELECT column_name,numeric_precision,numeric_scale "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='rental' AND data_type='numeric'"
            )
            self.assertEqual(
                {name: (precision, scale) for name, precision, scale in cursor},
                {"hourly_rate_applied": (12, 2), "final_amount": (12, 2)},
            )

    def test_primary_keys_and_all_twelve_protected_foreign_keys(self):
        expected = {
            Rental: {
                ("reservation_id", "reservation", "id"),
                ("product_item_id", "product_item", "id"),
                ("tariff_id", "product_tariff", "id"),
                ("payment_method_id", "payment_method", "id"),
                ("started_by_employee_id", "employee", "id"),
                ("ended_by_employee_id", "employee", "id"),
            },
            Inspection: {
                ("product_item_id", "product_item", "id"),
                ("employee_id", "employee", "id"),
                ("reservation_id", "reservation", "id"),
                ("rental_id", "rental", "id"),
            },
            InspectionProblem: {
                ("inspection_id", "inspection", "id"),
                ("product_component_id", "product_component", "id"),
            },
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
                    for field in model._meta.local_fields:
                        if field.is_relation:
                            self.assertIs(field.remote_field.on_delete, models.PROTECT)
        self.assertIsInstance(Rental._meta.pk, models.UUIDField)
        self.assertIsInstance(Inspection._meta.pk, models.BigAutoField)
        self.assertIsInstance(InspectionProblem._meta.pk, models.BigAutoField)

    def test_exact_check_constraints_and_two_partial_unique_indexes(self):
        expected = {
            "rental": {
                "rental_status_valid", "rental_state_dates_valid",
                "rental_rate_positive_finite", "rental_final_amount_finite_nonnegative",
            },
            "inspection": {"inspection_type_valid", "inspection_parent_pair_valid"},
            "inspection_problem": {
                "inspection_problem_type_valid",
                "inspection_problem_component_quantity_valid",
            },
        }
        with connection.cursor() as cursor:
            for table, names in expected.items():
                cursor.execute(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid=%s::regclass AND contype='c'", [table],
                )
                self.assertEqual({r[0] for r in cursor}, names)
            for table, name, column, predicate in (
                ("rental", "rental_one_active_item", "product_item_id", "active"),
                (
                    "inspection", "inspection_one_return_per_rental",
                    "rental_id", "return",
                ),
            ):
                with self.subTest(index=name):
                    cursor.execute(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname='public' AND tablename=%s AND indexname=%s",
                        [table, name],
                    )
                    row = cursor.fetchone()
                    self.assertIsNotNone(row)
                    for fragment in ("UNIQUE INDEX", column, "WHERE", predicate):
                        self.assertIn(fragment, row[0])

    def test_reservation_unique_is_not_the_rental_primary_key(self):
        field = Rental._meta.get_field("reservation")
        self.assertTrue(field.unique)
        self.assertFalse(field.primary_key)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "rental")
        self.assertEqual(
            {tuple(c["columns"]) for c in constraints.values() if c["unique"]},
            {("id",), ("reservation_id",), ("product_item_id",)},
        )

    def test_only_rental_uuid_has_a_field_default_and_dates_are_explicit(self):
        for model in HISTORY_MODELS:
            self.assertEqual(model._meta.base_manager_name, "objects")
            for field in model._meta.local_fields:
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertEqual(
                        field.has_default(), model is Rental and field.name == "id"
                    )
                    self.assertFalse(field.has_db_default())
                    self.assertFalse(getattr(field, "auto_now", False))
                    self.assertFalse(getattr(field, "auto_now_add", False))
        self.assertIs(Rental._meta.pk.default, uuid.uuid4)

    def test_exact_scope_is_nineteen_domain_tables_and_twenty_eight_migrations(self):
        domain = {
            "account", "employee", "product_category", "attribute_definition",
            "product", "category_attribute", "product_attribute_value",
            "product_component", "product_tariff", "pickup_point",
            "pickup_point_working_hours", "product_item",
            "product_item_location_history", "reservation",
            "reservation_item_assignment", "payment_method", "rental",
            "inspection", "inspection_problem",
            "payment", "debt",
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


class RentalModelTests(FixtureMixin, TestCase):
    def rental_values(self, **overrides):
        values = {
            "id": uuid.uuid4(), "reservation_id": self.reservation.pk,
            "product_item_id": self.item.pk, "tariff_id": self.tariff.pk,
            "payment_method_id": self.method.pk,
            "hourly_rate_applied": Decimal("12.00"),
            "started_by_employee_id": self.employee.pk,
            "ended_by_employee_id": None, "status": "active",
            "started_at": timezone.now(), "ended_at": None, "final_amount": None,
        }
        values.update(overrides)
        return values

    def inspection_values(self, **overrides):
        values = {
            "product_item_id": self.item.pk, "employee_id": self.employee.pk,
            "inspection_type": "pre_issue", "reservation_id": self.reservation.pk,
            "rental_id": None, "condition_ok": True, "completeness_ok": True,
            "comment": None, "customer_confirmed_at": None,
            "inspected_at": timezone.now(),
        }
        values.update(overrides)
        return values

    def problem_values(self, inspection, **overrides):
        values = {
            "inspection_id": inspection.pk, "problem_type": "damage",
            "description": "Synthetic problem", "product_component_id": None,
            "missing_quantity": None,
        }
        values.update(overrides)
        return values

    def assert_sqlstate(self, expected, operation):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                operation()
                connection.check_constraints()
        self.assertEqual(raised.exception.__cause__.sqlstate, expected)
        return raised.exception

    def completed_values(self, **overrides):
        now = timezone.now()
        return self.rental_values(
            **{
                "status": "completed", "started_at": now, "ended_at": now,
                "ended_by_employee_id": self.employee.pk,
                "final_amount": Decimal("0.00"), **overrides,
            }
        )

    def test_sql_rental_uuid_and_nullable_active_fields_round_trip(self):
        rental = self.raw_insert(Rental, **self.rental_values())
        self.assertIsInstance(rental.pk, uuid.UUID)
        self.assertEqual(rental.reservation_id, self.reservation.pk)
        self.assertEqual(rental.hourly_rate_applied, Decimal("12.00"))
        self.assertIsNone(rental.ended_by_employee_id)
        self.assertIsNone(rental.ended_at)
        self.assertIsNone(rental.final_amount)

    def test_sql_completed_allows_zero_duration_and_zero_amount(self):
        rental = self.raw_insert(Rental, **self.completed_values())
        self.assertEqual(rental.ended_at, rental.started_at)
        self.assertEqual(rental.final_amount, Decimal("0.00"))
        self.assertEqual(rental.ended_by_employee_id, self.employee.pk)

    def test_unknown_rental_statuses_are_rejected_by_sql(self):
        for status in ("cancelled", "ACTIVE", ""):
            with self.subTest(status=status):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Rental, **self.rental_values(status=status)
                ))

    def test_active_rental_rejects_each_completed_only_field(self):
        for field, value in (
            ("ended_at", timezone.now()),
            ("ended_by_employee_id", self.employee.pk),
            ("final_amount", Decimal("0.00")),
        ):
            with self.subTest(field=field):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Rental, **self.rental_values(**{field: value})
                ))

    def test_completed_null_fields_cannot_bypass_the_check_as_sql_unknown(self):
        for field in ("ended_at", "ended_by_employee_id", "final_amount"):
            with self.subTest(field=field):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Rental, **self.completed_values(**{field: None})
                ))

    def test_sql_completed_end_cannot_precede_its_start(self):
        now = timezone.now()
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            Rental, **self.completed_values(
                started_at=now, ended_at=now - timedelta(microseconds=1)
            )
        ))

    def test_sql_snapshot_rate_rejects_zero_negative_and_nan(self):
        for rate in (Decimal("0"), Decimal("-0.01"), Decimal("NaN")):
            with self.subTest(rate=rate):
                error = self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Rental, **self.rental_values(hourly_rate_applied=rate)
                ))
                self.assertEqual(
                    error.__cause__.diag.constraint_name, "rental_rate_positive_finite"
                )

    def test_sql_final_amount_rejects_negative_and_nan(self):
        for amount in (Decimal("-0.01"), Decimal("NaN")):
            with self.subTest(amount=amount):
                error = self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Rental, **self.completed_values(final_amount=amount)
                ))
                self.assertEqual(
                    error.__cause__.diag.constraint_name,
                    "rental_final_amount_finite_nonnegative",
                )

    def test_sql_maximum_money_values_are_preserved(self):
        maximum = Decimal("9999999999.99")
        rental = self.raw_insert(Rental, **self.completed_values(
            hourly_rate_applied=maximum, final_amount=maximum
        ))
        self.assertEqual(rental.hourly_rate_applied, maximum)
        self.assertEqual(rental.final_amount, maximum)

    def test_sql_reservation_can_have_only_one_rental(self):
        self.raw_insert(Rental, **self.completed_values())
        self.assert_sqlstate("23505", lambda: self.raw_insert(
            Rental, **self.completed_values()
        ))

    def test_sql_item_can_have_only_one_active_rental(self):
        self.raw_insert(Rental, **self.rental_values())
        other = self.make_reservation(item=self.make_item())
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            Rental, **self.rental_values(reservation_id=other.pk)
        ))
        self.assertEqual(error.__cause__.diag.constraint_name, "rental_one_active_item")

    def test_sql_completed_history_does_not_prevent_a_later_active_rental(self):
        self.raw_insert(Rental, **self.completed_values())
        other = self.make_reservation(item=self.make_item())
        later = self.raw_insert(Rental, **self.rental_values(reservation_id=other.pk))
        self.assertEqual(later.product_item_id, self.item.pk)
        self.assertEqual(Rental.objects.filter(product_item=self.item).count(), 2)

    def test_pre_issue_nullable_fields_and_separate_bigint_identity_round_trip(self):
        first = self.make_inspection()
        second = self.make_inspection()
        self.assertIsInstance(first.pk, int)
        self.assertNotEqual(first.pk, second.pk)
        self.assertIsNone(first.rental_id)
        self.assertIsNone(first.comment)
        self.assertIsNone(first.customer_confirmed_at)
        self.assertEqual(first.reservation_id, self.reservation.pk)

    def test_sql_rejects_all_invalid_inspection_parent_pairings(self):
        for values in (
            {"reservation_id": None},
            {"rental_id": uuid.uuid4()},
            {"inspection_type": "return", "reservation_id": None},
            {"inspection_type": "return", "rental_id": uuid.uuid4()},
            {"inspection_type": "return", "reservation_id": None,
             "rental_id": None},
        ):
            with self.subTest(fields=tuple(values)):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    Inspection, **self.inspection_values(**values)
                ))

    def test_sql_rejects_unknown_inspection_type(self):
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            Inspection, **self.inspection_values(inspection_type="maintenance")
        ))

    def test_sql_accepts_return_pairing_but_only_one_per_rental(self):
        rental = self.make_rental()
        values = self.inspection_values(
            inspection_type="return", reservation_id=None, rental_id=rental.pk
        )
        inspection = self.raw_insert(Inspection, **values)
        self.assertIsNone(inspection.reservation_id)
        self.assertEqual(inspection.rental_id, rental.pk)
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            Inspection, **values
        ))
        self.assertEqual(
            error.__cause__.diag.constraint_name, "inspection_one_return_per_rental"
        )

    def test_sql_accepts_each_known_problem_type_with_its_physical_shape(self):
        inspection = self.make_inspection(condition_ok=False, completeness_ok=False)
        for problem_type in ("damage", "malfunction", "other", "missing_component"):
            with self.subTest(problem_type=problem_type):
                missing = problem_type == "missing_component"
                problem = self.raw_insert(InspectionProblem, **self.problem_values(
                    inspection, problem_type=problem_type,
                    product_component_id=self.component.pk if missing else None,
                    missing_quantity=1 if missing else None,
                ))
                self.assertEqual(problem.problem_type, problem_type)

    def test_missing_problem_requires_both_component_and_positive_quantity(self):
        inspection = self.make_inspection(completeness_ok=False)
        for component, quantity in (
            (None, None), (None, 1), (self.component.pk, None),
            (self.component.pk, 0), (self.component.pk, -1),
        ):
            with self.subTest(component=component, quantity=quantity):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    InspectionProblem, **self.problem_values(
                        inspection, problem_type="missing_component",
                        product_component_id=component, missing_quantity=quantity,
                    )
                ))

    def test_nonmissing_problem_types_require_null_component_and_quantity(self):
        inspection = self.make_inspection(condition_ok=False)
        for problem_type in ("damage", "malfunction", "other"):
            for values in (
                {"product_component_id": self.component.pk},
                {"missing_quantity": 1},
                {"product_component_id": self.component.pk, "missing_quantity": 1},
            ):
                with self.subTest(problem_type=problem_type, fields=tuple(values)):
                    self.assert_sqlstate("23514", lambda: self.raw_insert(
                        InspectionProblem, **self.problem_values(
                            inspection, problem_type=problem_type, **values
                        )
                    ))

    def test_unknown_problem_type_is_rejected(self):
        inspection = self.make_inspection(condition_ok=False)
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            InspectionProblem, **self.problem_values(inspection, problem_type="fine")
        ))

    def test_multiple_missing_rows_are_allowed_without_an_unapproved_unique(self):
        inspection = self.make_inspection(completeness_ok=False)
        values = self.problem_values(
            inspection, problem_type="missing_component",
            product_component_id=self.component.pk, missing_quantity=1,
        )
        first = self.raw_insert(InspectionProblem, **values)
        second = self.raw_insert(InspectionProblem, **values)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(inspection.problems.count(), 2)

    def test_every_nonnullable_column_rejects_explicit_sql_null(self):
        inspection = self.make_inspection(condition_ok=False)
        samples = (
            (Rental, self.rental_values(), (
                "id", "reservation_id", "product_item_id", "tariff_id",
                "payment_method_id", "hourly_rate_applied",
                "started_by_employee_id", "status", "started_at",
            )),
            (Inspection, self.inspection_values(), (
                "id", "product_item_id", "employee_id", "inspection_type",
                "condition_ok", "completeness_ok", "inspected_at",
            )),
            (InspectionProblem, self.problem_values(inspection), (
                "id", "inspection_id", "problem_type", "description",
            )),
        )
        for model, values, names in samples:
            for name in names:
                with self.subTest(model=model.__name__, field=name):
                    self.assert_sqlstate("23502", lambda: self.raw_insert(
                        model, **{**values, name: None}
                    ))

    def test_all_twelve_foreign_keys_reject_nonexistent_parent_rows(self):
        inspection = self.make_inspection(condition_ok=False)
        cases = [
            (Rental, self.rental_values(**{field: missing}))
            for field, missing in (
                ("reservation_id", uuid.uuid4()), ("product_item_id", -1),
                ("tariff_id", -1), ("payment_method_id", uuid.uuid4()),
                ("started_by_employee_id", -1),
            )
        ]
        cases.extend([
            (Rental, self.completed_values(ended_by_employee_id=-1)),
            (Inspection, self.inspection_values(product_item_id=-1)),
            (Inspection, self.inspection_values(employee_id=-1)),
            (Inspection, self.inspection_values(reservation_id=uuid.uuid4())),
            (Inspection, self.inspection_values(
                inspection_type="return", reservation_id=None, rental_id=uuid.uuid4()
            )),
            (InspectionProblem, self.problem_values(inspection, inspection_id=-1)),
            (InspectionProblem, self.problem_values(
                inspection, problem_type="missing_component",
                product_component_id=-1, missing_quantity=1,
            )),
        ])
        self.assertEqual(len(cases), 12)
        for model, values in cases:
            with self.subTest(model=model.__name__, values=tuple(values)):
                self.assert_sqlstate("23503", lambda: self.raw_insert(model, **values))

    def test_problem_description_requires_nonblank_text_during_full_clean(self):
        inspection = self.make_inspection(condition_ok=False)
        for description in ("", " \t\n", "\u2003"):
            with self.subTest(description=repr(description)):
                problem = InspectionProblem(**self.problem_values(
                    inspection, description=description
                ))
                with self.assertRaises(ValidationError):
                    problem.full_clean()

    def test_full_clean_rejects_naive_event_times_without_writing_history(self):
        naive = timezone.now().replace(tzinfo=None)
        candidates = (
            Rental(**self.rental_values(started_at=naive)),
            Inspection(**self.inspection_values(inspected_at=naive)),
            Inspection(**self.inspection_values(customer_confirmed_at=naive)),
        )
        for candidate in candidates:
            with self.subTest(model=type(candidate).__name__):
                with self.assertRaises(ValidationError):
                    candidate.full_clean()
        self.assertEqual(Rental.objects.count(), 0)
        self.assertEqual(Inspection.objects.count(), 0)

    def test_row_checks_do_not_claim_component_ownership_or_quantity_cap(self):
        other_component = ProductComponent.objects.create(
            product=self.other_product, name="Synthetic other kit part", quantity=1
        )
        inspection = self.make_inspection(completeness_ok=False)
        problem = self.raw_insert(InspectionProblem, **self.problem_values(
            inspection, problem_type="missing_component",
            product_component_id=other_component.pk, missing_quantity=999,
        ))
        self.assertEqual(problem.missing_quantity, 999)
        self.assertNotEqual(other_component.product_id, self.item.product_id)

    def history_objects(self):
        rental = self.make_rental()
        inspection = self.make_inspection(
            inspection_type="return", reservation=None,
            rental=rental, condition_ok=False,
        )
        problem = self.make_problem(inspection=inspection)
        return rental, inspection, problem

    def test_public_instance_save_partial_save_and_delete_are_unsupported(self):
        instances = self.history_objects()
        for instance in instances:
            for operation in (
                lambda: instance.save(),
                lambda: instance.save(update_fields=["id"]),
                lambda: instance.delete(),
            ):
                with self.subTest(model=type(instance).__name__):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
            self.assertTrue(type(instance).objects.filter(pk=instance.pk).exists())

    def test_public_create_and_existing_pk_replacement_are_unsupported(self):
        instances = self.history_objects()
        for instance in instances:
            model = type(instance)
            values = {
                field.attname: getattr(instance, field.attname)
                for field in model._meta.local_fields
            }
            with self.subTest(model=model.__name__):
                with self.assertRaises(NotSupportedError):
                    model.objects.create(**values)
                with self.assertRaises(NotSupportedError):
                    model(**values).save(force_update=True)
                self.assertEqual(model.objects.count(), 1)

    def test_default_and_base_managers_block_update_delete_and_bulk_writes(self):
        for instance in self.history_objects():
            model = type(instance)
            for manager in (model.objects, model._base_manager):
                for operation in (
                    lambda: manager.filter(pk=instance.pk).update(id=instance.pk),
                    lambda: manager.filter(pk=instance.pk).delete(),
                    lambda: manager.bulk_create([instance]),
                    lambda: manager.bulk_update([instance], ["id"]),
                ):
                    with self.subTest(model=model.__name__, manager=manager.name):
                        with self.assertRaises(NotSupportedError):
                            with transaction.atomic():
                                operation()
            self.assertEqual(model.objects.count(), 1)

    def test_reverse_add_set_and_nonbulk_add_cannot_rewrite_history(self):
        rental, inspection, problem = self.history_objects()
        other_item = self.make_item()
        other_inspection = self.make_inspection(
            inspection_type="pre_issue", rental=None, condition_ok=False
        )
        for manager, instance, field, original in (
            (other_item.rentals, rental, "product_item_id", rental.product_item_id),
            (
                other_item.inspections, inspection,
                "product_item_id", inspection.product_item_id,
            ),
            (other_inspection.problems, problem, "inspection_id", inspection.pk),
        ):
            for operation in (
                lambda: manager.add(instance),
                lambda: manager.set([instance]),
                lambda: manager.add(instance, bulk=False),
            ):
                with self.subTest(model=type(instance).__name__):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
                    instance.refresh_from_db()
                    self.assertEqual(getattr(instance, field), original)

    def test_nullable_reverse_clear_remove_and_set_cannot_erase_parents(self):
        inspection = self.make_inspection()
        manager = self.reservation.inspections
        for operation in (
            manager.clear,
            lambda: manager.remove(inspection),
            lambda: manager.set([]),
        ):
            with self.assertRaises(NotSupportedError):
                with transaction.atomic():
                    operation()
            inspection.refresh_from_db()
            self.assertEqual(inspection.reservation_id, self.reservation.pk)

    def test_reverse_create_is_not_a_private_history_writer(self):
        rental = self.make_rental()
        values = self.inspection_values(
            inspection_type="return", reservation_id=None, rental_id=rental.pk
        )
        values.pop("rental_id")
        with self.assertRaises(NotSupportedError):
            with transaction.atomic():
                rental.inspections.create(**values)
        self.assertEqual(Inspection.objects.count(), 0)

    def test_base_managers_are_unfiltered_for_completed_and_problem_history(self):
        rental = self.make_rental(status="completed")
        inspection = self.make_inspection(
            inspection_type="return", reservation=None,
            rental=rental, condition_ok=False,
        )
        problem = self.make_problem(inspection=inspection)
        for instance in (rental, inspection, problem):
            self.assertEqual(
                type(instance)._base_manager.get(pk=instance.pk).pk, instance.pk
            )

    def test_collector_protects_rental_inspection_and_component_history(self):
        rental = self.make_rental()
        inspection = self.make_inspection(
            inspection_type="return", reservation=None, rental=rental,
            completeness_ok=False,
        )
        problem = self.make_problem(
            inspection=inspection, problem_type="missing_component",
            product_component=self.component, missing_quantity=1,
        )
        for parent, child in (
            (self.reservation, rental), (rental.payment_method, rental),
            (self.tariff, rental), (self.employee, rental),
            (self.item, rental), (rental, inspection),
            (inspection, problem), (self.component, problem),
        ):
            with self.subTest(parent=type(parent).__name__):
                # Deliberately invoke Collector beneath service-only delete.
                with self.assertRaises(ProtectedError) as raised:
                    models.Model.delete(parent)
                self.assertIn(child, raised.exception.protected_objects)

    def test_sql_blocks_deleting_a_referenced_rental(self):
        rental = self.make_rental()
        self.make_inspection(
            inspection_type="return", reservation=None, rental=rental
        )

        def delete_rental():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM rental WHERE id=%s", [rental.pk])

        self.assert_sqlstate("23503", delete_rental)
        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())
