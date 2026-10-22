"""Real PostgreSQL partial uniqueness for stored payment methods.

All tokens are synthetic. Confirmed methods come only from the test fixture's
raw SQL; these tests never contact or impersonate a payment provider.
"""

from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase

from .models import PaymentMethod
from .test_support import FixtureMixin


class PaymentMethodConcurrencyTests(FixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")

    def two_writers(self, first_action, second_action, *, mode="wait"):
        """Retain the first transaction until a proven wait or second commit.

        ``finish`` proves that independent customers can finish concurrently.
        ``wait`` requires pg_blocking_pids to name the first writer, rather
        than inferring locking from how long a request happened to take.
        """
        first_written, second_ready, second_finished = Event(), Event(), Event()
        observed_wait, poll_delay = Event(), Event()
        outcomes_queue = Queue()
        pids = {}

        def writer(role, action):
            worker_connection = connections["default"]
            try:
                if role == "second" and not first_written.wait(timeout=5):
                    raise AssertionError("The first payment-method writer was not ready.")
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
                            first_written.set()
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("The second payment-method writer was not ready.")
                            if mode == "finish":
                                if not second_finished.wait(timeout=3):
                                    raise AssertionError(
                                        "Independent payment-method operations did not "
                                        "finish while the first transaction held its locks."
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
                                            "The competing payment-method writer never "
                                            "actually waited for the first transaction."
                                        )
                                    poll_delay.wait(timeout=min(0.02, remaining))
                outcomes_queue.put((role, "committed", value))
            except IntegrityError as error:
                cause = error.__cause__
                outcomes_queue.put((role, "integrity", (
                    getattr(cause, "sqlstate", None),
                    getattr(getattr(cause, "diag", None), "constraint_name", None),
                )))
            except ValidationError as error:
                # Do not retain/log exception params or provider tokens.
                outcomes_queue.put((role, "validation", type(error).__name__))
            except Exception as error:
                detail = str(error) if isinstance(error, AssertionError) else type(error).__name__
                outcomes_queue.put((role, "error", detail))
            finally:
                worker_connection.close()
                if role == "second":
                    second_finished.set()

        threads = [
            Thread(
                target=writer, args=(role, action),
                name=f"payment-method-{role}", daemon=True,
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
                role, kind, detail = outcomes_queue.get(timeout=1)
            except Empty:
                self.fail("A payment-method writer did not record an outcome.")
            outcomes[role] = (kind, detail)
        self.assertEqual(set(outcomes), {"first", "second"})
        self.assertEqual(outcomes["first"][0], "committed", outcomes)
        self.assertEqual(len(set(pids.values())), 2)
        if mode == "wait":
            self.assertTrue(observed_wait.is_set())
        return outcomes


    def assert_default(self, customer, expected):
        defaults = list(PaymentMethod.objects.filter(
            customer_account_id=customer.pk, is_default=True,
        ))
        self.assertEqual([method.pk for method in defaults], [expected.pk])
        self.assertEqual(defaults[0].status, "active")
        self.assertEqual(defaults[0].verification_status, "confirmed")


    def test_sql_partial_unique_has_one_winner_with_real_23505(self):
        first = self.make_confirmed_method()
        second = self.make_confirmed_method()

        def raw_default(cursor, method):
            cursor.execute(
                "UPDATE payment_method SET is_default=TRUE "
                "WHERE id=%s RETURNING id", [method.pk],
            )
            return cursor.fetchone()[0]

        outcomes = self.two_writers(
            lambda cursor: raw_default(cursor, first),
            lambda cursor: raw_default(cursor, second),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "payment_method_one_default"),
        ))
        self.assert_default(self.customer, first)
        second.refresh_from_db()
        self.assertFalse(second.is_default)
