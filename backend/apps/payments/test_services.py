
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.payments.adapters import PaymentResult
from apps.payments.models import Payment
from apps.payments.financial_test_support import FinancialFixtureMixin
from apps.payments.services import (
    PaymentServiceError,
    create_payment_attempt,
)


class PaymentServiceTests(FinancialFixtureMixin, TestCase):
    def make_adapter(self, status):
        adapter = Mock()
        adapter.charge.return_value = PaymentResult(
            status=status,
            external_id="test-provider-operation",
            failure_code="test_declined" if status == "failed" else None,
            failure_message="Synthetic failure" if status == "failed" else None,
        )
        return adapter

    def create_attempt(self, key="test-idempotency-key", status="succeeded"):
        adapter = self.make_adapter(status)
        with patch(
            "apps.payments.services.get_payment_adapter",
            return_value=adapter,
        ):
            payment = create_payment_attempt(
                rental_id=self.rental.pk,
                idempotency_key=key,
            )
        return payment, adapter

    def test_succeeded_payment_is_saved(self):
        payment, adapter = self.create_attempt(status="succeeded")

        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.amount, self.rental.final_amount)
        self.assertEqual(payment.external_id, "test-provider-operation")
        self.assertIsNotNone(payment.resolved_at)
        adapter.charge.assert_called_once()

    def test_failed_payment_is_saved(self):
        payment, adapter = self.create_attempt(status="failed")

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(payment.failure_code, "test_declined")
        self.assertEqual(payment.failure_message, "Synthetic failure")
        self.assertIsNotNone(payment.resolved_at)
        adapter.charge.assert_called_once()

    def test_pending_payment_remains_unresolved(self):
        payment, adapter = self.create_attempt(status="pending")

        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.resolved_at)
        self.assertIsNone(payment.failure_code)
        self.assertIsNone(payment.failure_message)
        adapter.charge.assert_called_once()

    def test_same_idempotency_key_reuses_existing_payment(self):
        adapter = self.make_adapter("succeeded")

        with patch(
            "apps.payments.services.get_payment_adapter",
            return_value=adapter,
        ):
            first = create_payment_attempt(
                rental_id=self.rental.pk,
                idempotency_key="replay-key",
            )
            second = create_payment_attempt(
                rental_id=self.rental.pk,
                idempotency_key="replay-key",
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            Payment.objects.filter(idempotency_key="replay-key").count(),
            1,
        )
        adapter.charge.assert_called_once()

    def test_failed_payment_can_be_retried_with_new_key(self):
        first, _ = self.create_attempt(
            key="first-attempt",
            status="failed",
        )

        adapter = self.make_adapter("succeeded")
        with patch(
            "apps.payments.services.get_payment_adapter",
            return_value=adapter,
        ):
            second = create_payment_attempt(
                rental_id=self.rental.pk,
                idempotency_key="second-attempt",
            )

        self.assertEqual(first.status, Payment.Status.FAILED)
        self.assertEqual(second.status, Payment.Status.SUCCEEDED)
        self.assertNotEqual(first.pk, second.pk)

    def test_successful_payment_blocks_new_attempt(self):
        self.create_attempt(
            key="successful-attempt",
            status="succeeded",
        )

        with self.assertRaises(PaymentServiceError):
            self.create_attempt(
                key="another-attempt",
                status="succeeded",
            )

        self.assertEqual(
            Payment.objects.filter(rental=self.rental).count(),
            1,
        )

    def test_different_key_is_blocked_while_payment_is_pending(self):
        self.create_attempt(
            key="pending-attempt",
            status="pending",
        )

        with self.assertRaises(PaymentServiceError):
            self.create_attempt(
                key="another-pending-attempt",
                status="succeeded",
            )

        self.assertEqual(
            Payment.objects.filter(rental=self.rental).count(),
            1,
        )

    def test_same_key_cannot_be_reused_for_another_rental(self):
        self.create_attempt(
            key="shared-key",
            status="failed",
        )
        another_rental = self.make_completed_rental()

        with self.assertRaises(PaymentServiceError):
            create_payment_attempt(
                rental_id=another_rental.pk,
                idempotency_key="shared-key",
            )

    def test_adapter_failure_does_not_mark_payment_resolved(self):
        adapter = Mock()
        adapter.charge.side_effect = RuntimeError("synthetic adapter error")

        with patch(
            "apps.payments.services.get_payment_adapter",
            return_value=adapter,
        ):
            with self.assertRaises(RuntimeError):
                create_payment_attempt(
                    rental_id=self.rental.pk,
                    idempotency_key="uncertain-attempt",
                )

        payment = Payment.objects.get(
            idempotency_key="uncertain-attempt"
        )
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.resolved_at)
