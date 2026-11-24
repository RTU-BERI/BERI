from decimal import Decimal
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .adapters import (
    PaymentAdapterError,
    TestPaymentAdapter,
    get_payment_adapter,
)


class TestPaymentAdapterTests(SimpleTestCase):
    def setUp(self):
        self.operation_id = uuid4()
        self.amount = Decimal("150.00")
        self.idempotency_key = "test-operation-001"

    def test_succeeded_scenario(self):
        result = TestPaymentAdapter("succeeded").charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )

        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.external_id.startswith("test-"))
        self.assertIsNone(result.failure_code)

    def test_failed_scenario(self):
        result = TestPaymentAdapter("failed").charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "test_declined")
        self.assertIsNotNone(result.failure_message)

    def test_pending_scenario(self):
        result = TestPaymentAdapter("pending").charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )

        self.assertEqual(result.status, "pending")
        self.assertIsNone(result.failure_code)

    def test_same_idempotency_key_returns_same_external_id(self):
        adapter = TestPaymentAdapter("succeeded")

        first_result = adapter.charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )
        second_result = adapter.charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )

        self.assertEqual(first_result.external_id, second_result.external_id)

    def test_different_idempotency_keys_return_different_external_ids(self):
        adapter = TestPaymentAdapter("succeeded")

        first_result = adapter.charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key="operation-001",
        )
        second_result = adapter.charge(
            operation_id=self.operation_id,
            amount=self.amount,
            idempotency_key="operation-002",
        )

        self.assertNotEqual(first_result.external_id, second_result.external_id)

    def test_invalid_scenario_is_rejected(self):
        with self.assertRaises(PaymentAdapterError):
            TestPaymentAdapter("unknown")

    def test_invalid_amount_is_rejected(self):
        invalid_amounts = [
            100,
            Decimal("0"),
            Decimal("-10"),
            Decimal("NaN"),
            Decimal("Infinity"),
        ]

        for amount in invalid_amounts:
            with self.subTest(amount=amount):
                with self.assertRaises(PaymentAdapterError):
                    TestPaymentAdapter().charge(
                        operation_id=self.operation_id,
                        amount=amount,
                        idempotency_key=self.idempotency_key,
                    )

    def test_invalid_operation_id_is_rejected(self):
        with self.assertRaises(PaymentAdapterError):
            TestPaymentAdapter().charge(
                operation_id="not-a-uuid",
                amount=self.amount,
                idempotency_key=self.idempotency_key,
            )

    def test_invalid_idempotency_key_is_rejected(self):
        for key in ["", "   ", "x" * 256, None]:
            with self.subTest(key=key):
                with self.assertRaises(PaymentAdapterError):
                    TestPaymentAdapter().charge(
                        operation_id=self.operation_id,
                        amount=self.amount,
                        idempotency_key=key,
                    )

    def test_different_operations_return_different_external_ids(self):
        adapter = TestPaymentAdapter("succeeded")

        first_result = adapter.charge(
            operation_id=uuid4(),
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )
        second_result = adapter.charge(
            operation_id=uuid4(),
            amount=self.amount,
            idempotency_key=self.idempotency_key,
        )

        self.assertNotEqual(first_result.external_id, second_result.external_id)


class PaymentAdapterFactoryTests(SimpleTestCase):
    @override_settings(
        PAYMENT_ADAPTER="test",
        PAYMENT_TEST_SCENARIO="failed",
        DEBUG=True,
    )
    def test_returns_configured_test_adapter(self):
        adapter = get_payment_adapter()

        self.assertIsInstance(adapter, TestPaymentAdapter)
        self.assertEqual(adapter.scenario, "failed")

    @override_settings(
        PAYMENT_ADAPTER="disabled",
        DEBUG=True,
    )
    def test_rejects_disabled_adapter(self):
        with self.assertRaises(PaymentAdapterError):
            get_payment_adapter()

    @override_settings(
        PAYMENT_ADAPTER="test",
        PAYMENT_TEST_SCENARIO="succeeded",
        DEBUG=False,
    )
    def test_rejects_test_adapter_outside_debug_mode(self):
        with self.assertRaises(PaymentAdapterError):
            get_payment_adapter()


