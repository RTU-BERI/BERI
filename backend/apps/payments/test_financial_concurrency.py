"""Real PostgreSQL partial-unique and one-to-one arbitration.

All rows are SQL fixtures; these checks do not authenticate provider events
or implement financial workflows.
"""

import uuid

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.rentals import test_concurrency as rental_concurrency

from .models import Debt, Payment
from .financial_test_support import FinancialFixtureMixin


class FinancialConcurrencyTests(FinancialFixtureMixin, TransactionTestCase):
    # Reuse the existing bounded, two-connection harness with pg_blocking_pids.
    # Import its module rather than its TestCase class to avoid rediscovery.
    race = rental_concurrency.RentalConcurrencyTests.race

    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")

    def raw_payment(self, cursor, *, status="pending", rental=None):
        rental = self.rental if rental is None else rental
        now = timezone.now()
        pk = uuid.uuid4()
        cursor.execute(
            "INSERT INTO payment (id,rental_id,payment_method_id,external_id,amount,"
            "status,failure_code,failure_message,created_at,resolved_at) "
            "VALUES (%s,%s,%s,NULL,%s,%s,NULL,NULL,%s,%s)",
            [pk, rental.pk, rental.payment_method_id, rental.final_amount,
             status, now, None if status == "pending" else now],
        )
        return pk

    def raw_debt(self, cursor, *, rental=None):
        rental = self.rental if rental is None else rental
        pk = uuid.uuid4()
        cursor.execute(
            "INSERT INTO debt (id,rental_id,amount,status,created_at,settled_at) "
            "VALUES (%s,%s,%s,'open',%s,NULL)",
            [pk, rental.pk, rental.final_amount, timezone.now()],
        )
        return pk

    def another_rental(self):
        return self.make_completed_rental(amount="12.00")

    def test_two_pending_rows_for_one_rental_have_one_sql_winner(self):
        outcomes = self.race(
            lambda cursor: self.raw_payment(cursor),
            lambda cursor: self.raw_payment(cursor),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "payment_one_pending_per_rental"),
        ))
        self.assertEqual(Payment.objects.count(), 1)

    def test_two_succeeded_rows_for_one_rental_have_one_sql_winner(self):
        outcomes = self.race(
            lambda cursor: self.raw_payment(cursor, status="succeeded"),
            lambda cursor: self.raw_payment(cursor, status="succeeded"),
        )
        self.assertEqual(outcomes["second"], (
            "integrity", ("23505", "payment_one_succeeded_per_rental"),
        ))
        self.assertEqual(Payment.objects.count(), 1)

    def test_failed_rows_are_not_covered_by_either_partial_unique(self):
        outcomes = self.race(
            lambda cursor: self.raw_payment(cursor, status="failed"),
            lambda cursor: self.raw_payment(cursor, status="failed"), wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Payment.objects.count(), 2)

    def test_sql_allows_pending_and_succeeded_as_independent_partial_uniques(self):
        outcomes = self.race(
            lambda cursor: self.raw_payment(cursor),
            lambda cursor: self.raw_payment(cursor, status="succeeded"), wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Payment.objects.count(), 2)

    def test_pending_for_distinct_rentals_can_commit_in_parallel(self):
        other = self.another_rental()
        outcomes = self.race(
            lambda cursor: self.raw_payment(cursor),
            lambda cursor: self.raw_payment(cursor, rental=other), wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Payment.objects.count(), 2)

    def test_duplicate_debt_rental_has_one_sql_winner(self):
        outcomes = self.race(
            lambda cursor: self.raw_debt(cursor), lambda cursor: self.raw_debt(cursor),
        )
        self.assertEqual(outcomes["second"][0], "integrity", outcomes)
        self.assertEqual(outcomes["second"][1][0], "23505")
        self.assertIn("rental", outcomes["second"][1][1])
        self.assertEqual(Debt.objects.count(), 1)

    def test_debts_on_distinct_rentals_do_not_have_a_false_global_unique(self):
        other = self.another_rental()
        outcomes = self.race(
            lambda cursor: self.raw_debt(cursor),
            lambda cursor: self.raw_debt(cursor, rental=other), wait=False,
        )
        self.assertEqual(outcomes["second"][0], "committed", outcomes)
        self.assertEqual(Debt.objects.count(), 2)
