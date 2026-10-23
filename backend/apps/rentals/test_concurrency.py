"""PostgreSQL constraints and waits using explicit SQL-only rental fixtures."""

import uuid
from datetime import timedelta
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from .models import Inspection, Rental
from .test_support import FixtureMixin


class RentalConcurrencyTests(FixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")
        self.event_time = max(timezone.now(), self.item.updated_at) + timedelta(seconds=2)

    def race(self, first_action, second_action, *, wait=True, after_wait=None):
        """Prove a database wait, or require the second transaction to finish.

        The first writer never commits merely because the second thread says it
        is ready: pg_blocking_pids must expose the actual lock relationship.
        """
        first_written, second_ready, second_finished = Event(), Event(), Event()
        observed_wait, poll_pause = Event(), Event()
        pids, results = {}, Queue()

        def writer(role, action):
            worker = connections["default"]
            try:
                if role == "second" and not first_written.wait(timeout=5):
                    raise AssertionError("The first rental writer did not finish its write.")
                with transaction.atomic(using="default"):
                    with worker.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                        cursor.execute("SET LOCAL statement_timeout = '8s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pids[role] = cursor.fetchone()[0]
                        if role == "second":
                            second_ready.set()
                        value = action(cursor)
                        if role == "first":
                            first_written.set()
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("The second rental writer was not ready.")
                            if wait:
                                deadline = monotonic() + 3
                                while True:
                                    cursor.execute(
                                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                                        [pids["first"], pids["second"]],
                                    )
                                    if cursor.fetchone()[0]:
                                        observed_wait.set()
                                        if after_wait is not None:
                                            after_wait(cursor)
                                        break
                                    remaining = deadline - monotonic()
                                    if remaining <= 0:
                                        raise AssertionError("The second operation never blocked on the first.")
                                    poll_pause.wait(timeout=min(0.02, remaining))
                            elif not second_finished.wait(timeout=3):
                                raise AssertionError("The independent operation could not finish before commit.")
                results.put((role, "committed", value))
            except IntegrityError as error:
                cause = error.__cause__
                results.put((role, "integrity", (
                    getattr(cause, "sqlstate", None),
                    getattr(getattr(cause, "diag", None), "constraint_name", None),
                )))
            except ValidationError as error:
                results.put((role, "validation", type(error).__name__))
            except Exception as error:
                results.put((role, "error", repr(error)))
            finally:
                worker.close()
                if role == "second":
                    second_finished.set()

        threads = [Thread(
            target=writer, args=(role, action), name=f"rental-{role}", daemon=True,
        ) for role, action in (("first", first_action), ("second", second_action))]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            first_written.set()
            second_ready.set()
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        outcomes = {}
        for _ in threads:
            try:
                role, kind, value = results.get(timeout=1)
            except Empty:
                self.fail("A rental writer did not report its outcome.")
            outcomes[role] = (kind, value)
        self.assertEqual(set(outcomes), {"first", "second"})
        self.assertEqual(outcomes["first"][0], "committed", outcomes)
        self.assertEqual(len(set(pids.values())), 2)
        if wait:
            self.assertTrue(observed_wait.is_set())
        return outcomes

    def raw_rental(self, cursor, *, reservation=None, item=None, completed=False):
        reservation = self.reservation if reservation is None else reservation
        item = self.item if item is None else item
        pk = uuid.uuid4()
        cursor.execute(
            "INSERT INTO rental "
            "(id, reservation_id, product_item_id, tariff_id, payment_method_id, "
            "hourly_rate_applied, started_by_employee_id, ended_by_employee_id, "
            "status, started_at, ended_at, final_amount) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [pk, reservation.pk, item.pk, self.tariff.pk, self.method.pk,
             "12.00", self.employee.pk, self.employee.pk if completed else None,
             "completed" if completed else "active", self.event_time,
             self.event_time if completed else None, "0.00" if completed else None],
        )
        return pk

    def raw_inspection(self, cursor, *, rental=None):
        cursor.execute(
            "INSERT INTO inspection "
            "(product_item_id, employee_id, inspection_type, reservation_id, "
            "rental_id, condition_ok, completeness_ok, comment, "
            "customer_confirmed_at, inspected_at) "
            "VALUES (%s, %s, %s, %s, %s, TRUE, TRUE, NULL, NULL, %s) RETURNING id",
            [self.item.pk if rental is None else rental.product_item_id,
             self.employee.pk, "pre_issue" if rental is None else "return",
             self.reservation.pk if rental is None else None,
             None if rental is None else rental.pk, self.event_time],
        )
        return cursor.fetchone()[0]


    def test_sql_active_rentals_compete_for_the_same_item(self):
        other_item = self.make_item()
        other_reservation = self.make_reservation(item=other_item)
        outcomes = self.race(
            lambda cursor: self.raw_rental(cursor),
            lambda cursor: self.raw_rental(cursor, reservation=other_reservation),
        )
        self.assertEqual(outcomes["second"], ("integrity", ("23505", "rental_one_active_item")))
        self.assertEqual(Rental.objects.count(), 1)

    def test_sql_reservation_cannot_create_two_rentals_even_for_different_items(self):
        other_item = self.make_item()
        outcomes = self.race(
            lambda cursor: self.raw_rental(cursor),
            lambda cursor: self.raw_rental(cursor, item=other_item),
        )
        self.assertEqual(outcomes["second"][0], "integrity", outcomes)
        self.assertEqual(outcomes["second"][1][0], "23505")
        self.assertIn("reservation", outcomes["second"][1][1])
        self.assertEqual(Rental.objects.count(), 1)

    def test_sql_different_items_and_reservations_allow_parallel_active_rows(self):
        other_item = self.make_item()
        other_reservation = self.make_reservation(item=other_item)
        outcomes = self.race(
            lambda cursor: self.raw_rental(cursor),
            lambda cursor: self.raw_rental(cursor, reservation=other_reservation, item=other_item),
            wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Rental.objects.filter(status="active").count(), 2)

    def test_sql_completed_history_does_not_occupy_the_active_item_slot(self):
        other_item = self.make_item()
        other_reservation = self.make_reservation(item=other_item)
        outcomes = self.race(
            lambda cursor: self.raw_rental(cursor, completed=True),
            lambda cursor: self.raw_rental(cursor, reservation=other_reservation),
            wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Rental.objects.filter(status="active").count(), 1)
        self.assertEqual(Rental.objects.count(), 2)

    def test_sql_only_one_return_inspection_wins_for_a_rental(self):
        rental = self.make_rental()
        outcomes = self.race(
            lambda cursor: self.raw_inspection(cursor, rental=rental),
            lambda cursor: self.raw_inspection(cursor, rental=rental),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "inspection_one_return_per_rental"),
        ))
        self.assertEqual(Inspection.objects.filter(rental=rental).count(), 1)

    def test_sql_pre_issue_observations_do_not_have_a_false_singleton_constraint(self):
        outcomes = self.race(
            lambda cursor: self.raw_inspection(cursor),
            lambda cursor: self.raw_inspection(cursor), wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Inspection.objects.filter(reservation=self.reservation).count(), 2)
