"""Isolated payment adapters used by the payments application."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5


class PaymentAdapterError(ValueError):
    """Raised when a payment request is invalid."""


@dataclass(frozen=True)
class PaymentResult:
    """Normalized result returned by a payment adapter."""

    status: str
    external_id: str
    failure_code: str | None = None
    failure_message: str | None = None


class TestPaymentAdapter:
    """Deterministic local adapter. Never performs network requests."""

    SCENARIOS = frozenset({"succeeded", "failed", "pending"})

    def __init__(self, scenario: str = "succeeded"):
        if scenario not in self.SCENARIOS:
            raise PaymentAdapterError("Unsupported test payment scenario.")

        self.scenario = scenario

    def charge(
        self,
        *,
        operation_id: UUID,
        amount: Decimal,
        idempotency_key: str,
    ) -> PaymentResult:
        """Return a controlled synthetic result without contacting a provider."""

        if not isinstance(operation_id, UUID):
            raise PaymentAdapterError("A valid operation identifier is required.")

        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise PaymentAdapterError("A positive finite Decimal amount is required.")

        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 255
        ):
            raise PaymentAdapterError("A valid idempotency key is required.")

        synthetic_id = uuid5(
            NAMESPACE_URL,
            f"beri-test-payment:{operation_id}:{idempotency_key}",
        )

        if self.scenario == "failed":
            return PaymentResult(
                status="failed",
                external_id=f"test-{synthetic_id}",
                failure_code="test_declined",
                failure_message="Synthetic payment failure.",
            )

        return PaymentResult(
            status=self.scenario,
            external_id=f"test-{synthetic_id}",
        )

def get_payment_adapter():
    """Return the configured payment adapter."""

    from django.conf import settings

    if settings.PAYMENT_ADAPTER == "disabled":
        raise PaymentAdapterError("Payment adapter is disabled.")

    if settings.PAYMENT_ADAPTER == "test":
        if not settings.DEBUG:
            raise PaymentAdapterError(
                "Test payment adapter is allowed only in DEBUG mode."
            )

        return TestPaymentAdapter(
            scenario=settings.PAYMENT_TEST_SCENARIO,
        )

    raise PaymentAdapterError("Unsupported payment adapter configuration.")

