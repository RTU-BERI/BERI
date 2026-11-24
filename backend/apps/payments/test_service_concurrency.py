from unittest.mock import Mock, patch

from django.db import connection
from django.test import TransactionTestCase

from apps.payments.adapters import PaymentResult
from apps.payments.financial_test_support import FinancialFixtureMixin
from apps.payments.models import Payment
from apps.payments.services import create_payment_attempt
from apps.rentals import test_concurrency as rental_concurrency


class PaymentServiceConcurrencyTests(FinancialFixtureMixin, TransactionTestCase):
    race = rental_concurrency.RentalConcurrencyTests.race

    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")

    def test_parallel_different_keys_create_only_one_pending_payment(self):
        adapter = Mock()
        adapter.charge.return_value = PaymentResult(
            status="pending",
            external_id="test-concurrent-operation",
        )

        def attempt(key):
            return create_payment_attempt(
                rental_id=self.rental.pk,
                idempotency_key=key,
            )

        with patch(
            "apps.payments.services.get_payment_adapter",
            return_value=adapter,
        ):
            outcomes = self.race(
                lambda cursor: attempt("concurrent-first"),
                lambda cursor: attempt("concurrent-second"),
            )

        self.assertEqual(outcomes["first"][0], "committed", outcomes)
        self.assertEqual(outcomes["second"][0], "error", outcomes)
        self.assertIn("PaymentServiceError", outcomes["second"][1])
        self.assertEqual(Payment.objects.filter(rental=self.rental).count(), 1)
        adapter.charge.assert_called_once()
