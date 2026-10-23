"""PostgreSQL partial UNIQUE races using explicit test-only SQL fixtures."""

import uuid
from datetime import timedelta
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from .models import ReservationItemAssignment
from .test_support import FixtureMixin


def integrity_details(error):
    cause = error.__cause__
    state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return state, cause.diag.constraint_name


class ReservationConcurrencyTests(FixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")
        self.event_time = max(self.item.updated_at, timezone.now()) + timedelta(seconds=1)


    def another_item(self):
        item = self.make_item()
        # Fixture creation uses the real clock. Capture a later logical event
        # even if a slow fixture INSERT passed the time set in setUp().
        self.event_time = max(
            self.event_time, item.updated_at + timedelta(seconds=1)
        )
        return item

    def two_writers(self, first_action, second_action, *, mode="wait"):
        """Commit writer one only after observing a wait or writer two's exit.

        In ``finish`` mode writer two must finish before writer one commits.
        This proves SHARE compatibility or SKIP LOCKED without a timing guess.
        In ``wait`` mode pg_blocking_pids proves an actual conflicting lock.
        """
        first_done, second_ready, second_finished = Event(), Event(), Event()
        observed_wait, poll_delay = Event(), Event()
        results = Queue()
        pids = {}

        def writer(role, action):
            worker_connection = connections["default"]
            try:
                if role == "second" and not first_done.wait(timeout=5):
                    raise AssertionError("First reservation writer did not finish its write.")
                with transaction.atomic(using="default"):
                    with worker_connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                        cursor.execute("SET LOCAL statement_timeout = '8s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pids[role] = cursor.fetchone()[0]
                        if role == "second":
                            second_ready.set()
                        value = action(cursor)
                        if role == "first":
                            first_done.set()
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("Second reservation writer was not ready.")
                            if mode == "finish":
                                if not second_finished.wait(timeout=3):
                                    raise AssertionError(
                                        "Second writer could not finish while the "
                                        "first transaction retained its locks."
                                    )
                            else:
                                deadline = monotonic() + 3
                                while True:
                                    cursor.execute(
                                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                                        [pids["first"], pids["second"]],
                                    )
                                    if cursor.fetchone()[0]:
                                        observed_wait.set()
                                        break
                                    remaining = deadline - monotonic()
                                    if remaining <= 0:
                                        raise AssertionError(
                                            "The competing reservation operation "
                                            "never actually blocked."
                                        )
                                    poll_delay.wait(timeout=min(0.02, remaining))
                results.put((role, "committed", value))
            except IntegrityError as error:
                results.put((role, "integrity", integrity_details(error)))
            except ValidationError as error:
                results.put((role, "validation", type(error).__name__))
            except Exception as error:
                results.put((role, "error", repr(error)))
            finally:
                worker_connection.close()
                if role == "second":
                    second_finished.set()

        threads = [
            Thread(
                target=writer,
                args=(role, action),
                name=f"reservation-{role}",
                daemon=True,
            )
            for role, action in (("first", first_action), ("second", second_action))
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            second_ready.set()
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        outcomes = {}
        for _ in threads:
            try:
                role, kind, detail = results.get(timeout=1)
            except Empty:
                self.fail("A reservation writer exited without recording its outcome.")
            outcomes[role] = (kind, detail)
        self.assertEqual(set(outcomes), {"first", "second"})
        self.assertEqual(outcomes["first"][0], "committed", outcomes)
        self.assertEqual(len(set(pids.values())), 2)
        if mode == "wait":
            self.assertTrue(observed_wait.is_set())
        return outcomes


    def raw_reservation(self, item):
        reservation_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO reservation "
                "(id, customer_account_id, pickup_point_id, initial_product_item_id, "
                "assigned_product_item_id, status, completion_reason, created_at, "
                "expires_at, cancelled_at, completed_at) "
                "VALUES (%s, %s, %s, %s, %s, 'active', NULL, %s, %s, NULL, NULL)",
                [
                    reservation_id, self.customer.pk, self.point.pk, item.pk,
                    item.pk, self.event_time, self.event_time + timedelta(minutes=60),
                ],
            )
        return reservation_id

    def raw_assignment(self, cursor, reservation_id, item_id, sequence_no):
        cursor.execute(
            "INSERT INTO reservation_item_assignment "
            "(reservation_id, product_item_id, assigned_by_employee_id, sequence_no, "
            "reason, assigned_at, released_at) "
            "VALUES (%s, %s, NULL, %s, NULL, %s, NULL) RETURNING id",
            [reservation_id, item_id, sequence_no, self.event_time],
        )
        return cursor.fetchone()[0]

    def test_sql_partial_unique_allows_only_one_open_assignment_per_reservation(self):
        other_item = self.another_item()
        reservation_id = self.raw_reservation(self.item)
        outcomes = self.two_writers(
            lambda cursor: self.raw_assignment(cursor, reservation_id, self.item.pk, 1),
            lambda cursor: self.raw_assignment(cursor, reservation_id, other_item.pk, 2),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "reservation_one_open_assignment")
        ))
        self.assertEqual(ReservationItemAssignment.objects.count(), 1)

    def test_sql_partial_unique_allows_only_one_open_assignment_per_item(self):
        other_item = self.another_item()
        first_id = self.raw_reservation(self.item)
        second_id = self.raw_reservation(other_item)
        outcomes = self.two_writers(
            lambda cursor: self.raw_assignment(cursor, first_id, self.item.pk, 1),
            lambda cursor: self.raw_assignment(cursor, second_id, self.item.pk, 1),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "product_item_one_open_assignment")
        ))
        self.assertEqual(ReservationItemAssignment.objects.count(), 1)
