"""Reservation SQL guarantees and service-only ORM write boundaries."""

import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from .models import Reservation, ReservationItemAssignment
from .test_support import FixtureMixin


class ReservationSchemaTests(TestCase):
    def test_exact_nineteen_columns_match_erd_types_and_nullability(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "reservation": {
                "id": ("uuid", "NO", None),
                "customer_account_id": ("uuid", "NO", None),
                "pickup_point_id": ("bigint", "NO", None),
                "initial_product_item_id": ("bigint", "NO", None),
                "assigned_product_item_id": ("bigint", "YES", None),
                "status": ("character varying", "NO", 30),
                "completion_reason": ("character varying", "YES", 50),
                "created_at": ("timestamp with time zone", "NO", None),
                "expires_at": ("timestamp with time zone", "NO", None),
                "cancelled_at": ("timestamp with time zone", "YES", None),
                "completed_at": ("timestamp with time zone", "YES", None),
            },
            "reservation_item_assignment": {
                "id": ("bigint", "NO", None),
                "reservation_id": ("uuid", "NO", None),
                "product_item_id": ("bigint", "NO", None),
                "assigned_by_employee_id": ("bigint", "YES", None),
                "sequence_no": ("integer", "NO", None),
                "reason": ("character varying", "YES", 30),
                "assigned_at": ("timestamp with time zone", "NO", None),
                "released_at": ("timestamp with time zone", "YES", None),
            },
        }
        for table, columns in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type, is_nullable, "
                    "character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s",
                    [table],
                )
                self.assertEqual(
                    {name: (kind, null, size) for name, kind, null, size
                     in cursor.fetchall()},
                    columns,
                )

    def test_primary_keys_are_single_uuid_and_bigint_identity(self):
        for table in ("reservation", "reservation_item_assignment"):
            with self.subTest(table=table), connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    [c["columns"] for c in constraints.values()
                     if c["primary_key"]],
                    [["id"]],
                )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_identity FROM information_schema.columns "
                "WHERE table_schema='public' "
                "AND table_name='reservation_item_assignment' "
                "AND column_name='id'"
            )
            self.assertEqual(cursor.fetchone(), ("YES",))

    def test_seven_foreign_keys_match_the_exact_parent_columns(self):
        expected = {
            "reservation": {
                ("customer_account_id", "account", "id"),
                ("pickup_point_id", "pickup_point", "id"),
                ("initial_product_item_id", "product_item", "id"),
                ("assigned_product_item_id", "product_item", "id"),
            },
            "reservation_item_assignment": {
                ("reservation_id", "reservation", "id"),
                ("product_item_id", "product_item", "id"),
                ("assigned_by_employee_id", "employee", "id"),
            },
        }
        for table, foreign_keys in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    {
                        (c["columns"][0], *c["foreign_key"])
                        for c in constraints.values() if c["foreign_key"]
                    },
                    foreign_keys,
                )

    def test_all_seven_historical_relationships_use_protect(self):
        for model, name in (
            (Reservation, "customer_account"),
            (Reservation, "pickup_point"),
            (Reservation, "initial_product_item"),
            (Reservation, "assigned_product_item"),
            (ReservationItemAssignment, "reservation"),
            (ReservationItemAssignment, "product_item"),
            (ReservationItemAssignment, "assigned_by_employee"),
        ):
            with self.subTest(model=model.__name__, field=name):
                self.assertIs(
                    model._meta.get_field(name).remote_field.on_delete,
                    models.PROTECT,
                )

    def test_four_statuses_are_choices_without_a_default(self):
        field = Reservation._meta.get_field("status")
        self.assertEqual(
            {value for value, label in field.choices},
            {"active", "fulfilled", "cancelled", "expired"},
        )
        self.assertFalse(field.has_default())
        self.assertFalse(field.has_db_default())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='reservation' "
                "AND column_name='status'"
            )
            self.assertEqual(cursor.fetchone(), (None,))

    def test_reason_columns_do_not_introduce_dictionaries_or_defaults(self):
        for model, name in (
            (Reservation, "completion_reason"),
            (ReservationItemAssignment, "reason"),
        ):
            with self.subTest(model=model.__name__, field=name):
                field = model._meta.get_field(name)
                self.assertTrue(field.null)
                self.assertFalse(field.choices)
                self.assertFalse(field.has_default())
                self.assertFalse(field.has_db_default())

    def test_open_assignment_indexes_are_partial_and_not_full_history_exclusions(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' "
                "AND tablename='reservation_item_assignment'"
            )
            indexes = dict(cursor.fetchall())
            for name, column in (
                ("reservation_one_open_assignment", "reservation_id"),
                ("product_item_one_open_assignment", "product_item_id"),
            ):
                definition = indexes[name].lower()
                self.assertIn("unique", definition)
                self.assertIn(column, definition)
                self.assertIn("where", definition)
                self.assertIn("released_at is null", definition)
            cursor.execute(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid='reservation_item_assignment'::regclass "
                "AND contype='x'"
            )
            self.assertEqual(cursor.fetchone(), (0,))


class ReservationModelTests(FixtureMixin, TestCase):
    def raw_insert(self, table, values):
        quote = connection.ops.quote_name
        columns = ", ".join(quote(name) for name in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {quote(table)} ({columns}) "
                f"VALUES ({placeholders}) RETURNING id",
                list(values.values()),
            )
            return cursor.fetchone()[0]

    def reservation_values(self, **overrides):
        now = timezone.now()
        values = {
            "id": uuid.uuid4(),
            "customer_account_id": self.customer.pk,
            "pickup_point_id": self.point.pk,
            "initial_product_item_id": self.item.pk,
            "assigned_product_item_id": self.item.pk,
            "status": "active", "completion_reason": None,
            "created_at": now, "expires_at": now + timedelta(minutes=60),
            "cancelled_at": None, "completed_at": None,
        }
        values.update(overrides)
        return values

    def raw_reservation(self, **overrides):
        values = self.reservation_values(**overrides)
        self.raw_insert("reservation", values)
        return Reservation.objects.get(pk=values["id"])

    def assignment_values(self, reservation, **overrides):
        values = {
            "reservation_id": reservation.pk,
            "product_item_id": self.item.pk,
            "assigned_by_employee_id": None,
            "sequence_no": 1, "reason": None,
            "assigned_at": reservation.created_at,
            "released_at": None,
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

    def test_raw_sql_accepts_all_four_statuses_with_their_agreed_dates(self):
        now = timezone.now()
        expiry = now + timedelta(minutes=60)
        for status, terminal in (
            ("active", {}),
            ("cancelled", {"cancelled_at": now, "completed_at": now}),
            ("expired", {"completed_at": expiry}),
            ("fulfilled", {"completed_at": now}),
        ):
            with self.subTest(status=status):
                reservation = self.raw_reservation(
                    status=status, created_at=now, expires_at=expiry, **terminal
                )
                self.assertIsInstance(reservation.pk, uuid.UUID)
                self.assertEqual(reservation.status, status)

    def test_sql_rejects_unknown_statuses_with_check_violation(self):
        for status in ("pending", "ACTIVE", ""):
            with self.subTest(status=status):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(status=status)
                ))

    def test_sql_expiry_must_be_after_creation_without_imposing_sixty_minutes(self):
        now = timezone.now()
        for expiry in (now, now - timedelta(microseconds=1)):
            with self.subTest(expiry=expiry):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(
                        created_at=now, expires_at=expiry
                    )
                ))
        reservation = self.raw_reservation(
            created_at=now, expires_at=now + timedelta(seconds=1)
        )
        self.assertEqual(reservation.expires_at - reservation.created_at,
                         timedelta(seconds=1))

    def test_active_terminal_dates_or_reason_are_rejected_by_sql(self):
        for extra in (
            {"cancelled_at": timezone.now()},
            {"completed_at": timezone.now()},
            {"completion_reason": "opaque fixture"},
        ):
            with self.subTest(extra=extra):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(**extra)
                ))

    def test_cancelled_null_dates_cannot_pass_the_check_as_sql_unknown(self):
        now = timezone.now()
        for terminal in (
            {}, {"cancelled_at": now}, {"completed_at": now},
        ):
            with self.subTest(terminal=terminal):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(
                        status="cancelled", **terminal
                    )
                ))

    def test_cancelled_dates_must_be_equal_and_not_before_creation(self):
        now = timezone.now()
        for cancelled, completed in (
            (now, now + timedelta(seconds=1)),
            (now - timedelta(seconds=1), now - timedelta(seconds=1)),
        ):
            with self.subTest(cancelled=cancelled, completed=completed):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(
                        status="cancelled", created_at=now,
                        cancelled_at=cancelled, completed_at=completed,
                    )
                ))

    def test_expired_requires_completion_after_expiry_without_reason_or_cancel_date(
        self,
    ):
        now = timezone.now()
        expiry = now + timedelta(minutes=60)
        for invalid in (
            {"completed_at": None},
            {"completed_at": expiry - timedelta(microseconds=1)},
            {"completed_at": expiry, "cancelled_at": expiry},
            {"completed_at": expiry, "completion_reason": "opaque fixture"},
        ):
            with self.subTest(invalid=invalid):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(
                        status="expired", created_at=now, expires_at=expiry,
                        **invalid,
                    )
                ))

    def test_fulfilled_requires_nonnull_completion_without_cancel_date(self):
        now = timezone.now()
        for invalid in (
            {"completed_at": None},
            {"completed_at": now - timedelta(microseconds=1)},
            {"completed_at": now, "cancelled_at": now},
        ):
            with self.subTest(invalid=invalid):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation", self.reservation_values(
                        status="fulfilled", created_at=now, **invalid
                    )
                ))

    def test_physical_assigned_pointer_and_cancel_reason_remain_nullable(self):
        active = self.raw_reservation(assigned_product_item_id=None)
        self.assertIsNone(active.assigned_product_item_id)
        now = timezone.now()
        cancelled = self.raw_reservation(
            status="cancelled", created_at=now,
            cancelled_at=now, completed_at=now, completion_reason=None,
        )
        self.assertIsNone(cancelled.completion_reason)

    def test_assignment_nullable_employee_reason_and_end_are_preserved(self):
        reservation = self.raw_reservation()
        pk = self.raw_insert(
            "reservation_item_assignment", self.assignment_values(reservation)
        )
        assignment = ReservationItemAssignment.objects.get(pk=pk)
        self.assertIsInstance(assignment.pk, int)
        self.assertIsNone(assignment.assigned_by_employee_id)
        self.assertIsNone(assignment.reason)
        self.assertIsNone(assignment.released_at)

    def test_assignment_sequence_must_be_strictly_positive_in_sql(self):
        reservation = self.raw_reservation()
        for sequence in (0, -1):
            with self.subTest(sequence=sequence):
                self.assert_sqlstate("23514", lambda: self.raw_insert(
                    "reservation_item_assignment",
                    self.assignment_values(reservation, sequence_no=sequence),
                ))

    def test_assignment_release_cannot_precede_assignment_but_equality_is_valid(self):
        reservation = self.raw_reservation()
        assigned = reservation.created_at
        self.assert_sqlstate("23514", lambda: self.raw_insert(
            "reservation_item_assignment", self.assignment_values(
                reservation, released_at=assigned - timedelta(microseconds=1)
            )
        ))
        pk = self.raw_insert(
            "reservation_item_assignment", self.assignment_values(
                reservation, released_at=assigned
            )
        )
        assignment = ReservationItemAssignment.objects.get(pk=pk)
        self.assertEqual(assignment.released_at, assignment.assigned_at)

    def test_sequence_pair_is_unique_even_for_closed_assignments(self):
        reservation = self.raw_reservation()
        values = self.assignment_values(
            reservation, released_at=reservation.created_at
        )
        self.raw_insert("reservation_item_assignment", values)
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            "reservation_item_assignment", values
        ))
        self.assertEqual(
            error.__cause__.diag.constraint_name,
            "assignment_reservation_sequence_unique",
        )

    def test_only_one_open_assignment_is_allowed_per_reservation(self):
        reservation = self.raw_reservation()
        other_item = self.make_item()
        self.raw_insert(
            "reservation_item_assignment", self.assignment_values(reservation)
        )
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            "reservation_item_assignment", self.assignment_values(
                reservation, product_item_id=other_item.pk, sequence_no=2
            )
        ))
        self.assertEqual(
            error.__cause__.diag.constraint_name,
            "reservation_one_open_assignment",
        )

    def test_only_one_open_assignment_is_allowed_per_physical_item(self):
        first = self.raw_reservation()
        second = self.raw_reservation()
        self.raw_insert(
            "reservation_item_assignment", self.assignment_values(first)
        )
        error = self.assert_sqlstate("23505", lambda: self.raw_insert(
            "reservation_item_assignment", self.assignment_values(second)
        ))
        self.assertEqual(
            error.__cause__.diag.constraint_name,
            "product_item_one_open_assignment",
        )

    def test_closed_assignment_allows_a_later_open_assignment(self):
        reservation = self.raw_reservation()
        assigned = reservation.created_at
        self.raw_insert("reservation_item_assignment", self.assignment_values(
            reservation, released_at=assigned
        ))
        self.raw_insert("reservation_item_assignment", self.assignment_values(
            reservation, sequence_no=2, assigned_at=assigned
        ))
        self.assertEqual(reservation.assignments.count(), 2)

    def test_closed_history_overlap_is_not_falsely_claimed_as_a_sql_guarantee(self):
        reservation = self.raw_reservation()
        start = reservation.created_at
        self.raw_insert("reservation_item_assignment", self.assignment_values(
            reservation, released_at=start + timedelta(seconds=10)
        ))
        self.raw_insert("reservation_item_assignment", self.assignment_values(
            reservation, sequence_no=2,
            assigned_at=start + timedelta(seconds=1),
            released_at=start + timedelta(seconds=5),
        ))
        self.assertEqual(reservation.assignments.count(), 2)

    def test_all_seven_foreign_keys_reject_nonexistent_parents(self):
        reservation = self.raw_reservation()
        for table, name, invalid in (
            ("reservation", "customer_account_id", uuid.uuid4()),
            ("reservation", "pickup_point_id", 9223372036854775000),
            ("reservation", "initial_product_item_id", 9223372036854775000),
            ("reservation", "assigned_product_item_id", 9223372036854775000),
            ("reservation_item_assignment", "reservation_id", uuid.uuid4()),
            ("reservation_item_assignment", "product_item_id", 9223372036854775000),
            (
                "reservation_item_assignment",
                "assigned_by_employee_id",
                9223372036854775000,
            ),
        ):
            with self.subTest(table=table, field=name):
                values = (
                    self.reservation_values() if table == "reservation"
                    else self.assignment_values(reservation)
                )
                values[name] = invalid
                self.assert_sqlstate("23503", lambda: self.raw_insert(table, values))

    def test_required_columns_reject_null_with_not_null_violation(self):
        reservation = self.raw_reservation()
        for table, names in (
            ("reservation", (
                "id", "customer_account_id", "pickup_point_id",
                "initial_product_item_id", "status", "created_at", "expires_at",
            )),
            ("reservation_item_assignment", (
                "reservation_id", "product_item_id", "sequence_no", "assigned_at",
            )),
        ):
            for name in names:
                with self.subTest(table=table, field=name):
                    values = (
                        self.reservation_values() if table == "reservation"
                        else self.assignment_values(reservation)
                    )
                    values[name] = None
                    self.assert_sqlstate(
                        "23502", lambda: self.raw_insert(table, values)
                    )

    def test_new_instances_and_manager_create_cannot_write_publicly(self):
        reservation = self.raw_reservation()
        for model, values in (
            (Reservation, self.reservation_values()),
            (ReservationItemAssignment, self.assignment_values(reservation)),
        ):
            with self.subTest(model=model.__name__):
                with self.assertRaises(NotSupportedError):
                    model(**values).save()
                with self.assertRaises(NotSupportedError):
                    model.objects.create(**values)

    def test_existing_instances_cannot_be_saved_or_deleted_publicly(self):
        reservation = self.make_reservation()
        assignment = reservation.assignments.get()
        for instance in (reservation, assignment):
            for operation in (instance.save, instance.delete):
                with self.subTest(model=type(instance).__name__):
                    with self.assertRaises(NotSupportedError):
                        operation()

    def test_queryset_and_base_manager_writes_cannot_bypass_services(self):
        reservation = self.make_reservation()
        assignment = reservation.assignments.get()
        for instance, field in (
            (reservation, "completion_reason"), (assignment, "reason")
        ):
            for manager in (type(instance).objects, type(instance)._base_manager):
                for operation in (
                    lambda: manager.filter(pk=instance.pk).update(**{field: None}),
                    lambda: manager.filter(pk=instance.pk).delete(),
                    lambda: manager.bulk_create([instance]),
                    lambda: manager.bulk_update([instance], [field]),
                ):
                    with self.subTest(model=type(instance).__name__, field=field):
                        with self.assertRaises(NotSupportedError):
                            operation()

    def test_reverse_add_set_and_nonbulk_add_cannot_rewrite_history(self):
        reservation = self.make_reservation()
        assignment = reservation.assignments.get()
        other_item = self.make_item()
        other = self.make_reservation(item=other_item)
        cases = (
            (self.other_customer.reservations, reservation,
             "customer_account_id", self.customer.pk),
            (self.other_point.reservations, reservation,
             "pickup_point_id", self.point.pk),
            (other_item.initial_reservations, reservation,
             "initial_product_item_id", self.item.pk),
            (other_item.assigned_reservations, reservation,
             "assigned_product_item_id", self.item.pk),
            (other.assignments, assignment, "reservation_id", reservation.pk),
            (other_item.reservation_assignments, assignment,
             "product_item_id", self.item.pk),
            (self.employee.reservation_assignments, assignment,
             "assigned_by_employee_id", None),
        )
        for manager, instance, field, original in cases:
            for operation in (
                lambda: manager.add(instance),
                lambda: manager.set([instance]),
                lambda: manager.add(instance, bulk=False),
            ):
                with self.subTest(model=type(instance).__name__, field=field):
                    with self.assertRaises(NotSupportedError):
                        with transaction.atomic():
                            operation()
                    instance.refresh_from_db()
                    self.assertEqual(getattr(instance, field), original)

    def test_nullable_reverse_remove_and_clear_cannot_erase_last_pointers(self):
        reservation = self.raw_reservation()
        assignment_pk = self.raw_insert(
            "reservation_item_assignment", self.assignment_values(
                reservation, assigned_by_employee_id=self.employee.pk
            )
        )
        assignment = ReservationItemAssignment.objects.get(pk=assignment_pk)
        for manager, instance, field, original in (
            (self.item.assigned_reservations, reservation,
             "assigned_product_item_id", self.item.pk),
            (self.employee.reservation_assignments, assignment,
             "assigned_by_employee_id", self.employee.pk),
        ):
            for operation in (lambda: manager.remove(instance), manager.clear):
                with self.subTest(field=field):
                    with self.assertRaises(NotSupportedError):
                        operation()
                    instance.refresh_from_db()
                    self.assertEqual(getattr(instance, field), original)

    def test_customer_and_assignment_employee_are_protected_from_deletion(self):
        reservation = self.raw_reservation()
        self.raw_insert("reservation_item_assignment", self.assignment_values(
            reservation, assigned_by_employee_id=self.employee.pk
        ))
        for parent in (self.customer, self.employee):
            with self.subTest(model=type(parent).__name__):
                with self.assertRaises(ProtectedError):
                    parent.delete()
        with self.assertRaises(ProtectedError):
            models.Model.delete(reservation)

    def test_parent_point_and_items_are_protected_while_history_exists(self):
        reservation = self.make_reservation()
        for parent in (self.point, self.item):
            with self.subTest(model=type(parent).__name__):
                with self.assertRaises(ProtectedError):
                    parent.delete()
        reservation.refresh_from_db()
        self.assertEqual(reservation.initial_product_item_id, self.item.pk)
        self.assertEqual(reservation.assigned_product_item_id, self.item.pk)
