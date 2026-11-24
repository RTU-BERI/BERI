
"""Trusted payment-attempt orchestration."""

import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.adapters import (
    PaymentAdapterError,
    get_payment_adapter,
)
from apps.payments.models import Payment, PaymentMethod
from apps.rentals.models import Rental


class PaymentServiceError(ValueError):
    """Raised when a payment attempt cannot be processed safely."""


def _validate_idempotency_key(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 255
    ):
        raise PaymentServiceError("A valid idempotency key is required.")

    return value


def _validate_payment_method(rental, payment_method):
    if payment_method.pk != rental.payment_method_id:
        raise PaymentServiceError("Payment method does not match the rental.")

    if payment_method.status != PaymentMethod.Status.ACTIVE:
        raise PaymentServiceError("Payment method is not active.")

    if (
        payment_method.verification_status
        != PaymentMethod.VerificationStatus.CONFIRMED
    ):
        raise PaymentServiceError("Payment method is not confirmed.")

    if (
        payment_method.customer_account_id
        != rental.reservation.customer_account_id
    ):
        raise PaymentServiceError("Payment method ownership is invalid.")


def _get_existing_attempt(key):
    return Payment.objects.filter(idempotency_key=key).first()


def _check_replay(payment, rental, payment_method, amount):
    if (
        payment.rental_id != rental.pk
        or payment.payment_method_id != payment_method.pk
        or payment.amount != amount
    ):
        raise PaymentServiceError(
            "Idempotency key was already used for another payment request."
        )

    return payment


def _persist_new_payment(payment):
    # Payment.save() is intentionally blocked for untrusted callers.
    from django.db import models

    models.Model.save(payment, force_insert=True)


def _persist_payment_update(payment, **values):
    # QuerySet.update() is intentionally blocked by the financial manager.
    from django.db import models

    models.QuerySet.update(
        Payment.objects.filter(pk=payment.pk),
        **values,
    )


def create_payment_attempt(*, rental_id, idempotency_key):
    """Create or safely replay a payment attempt for a completed rental."""

    key = _validate_idempotency_key(idempotency_key)

    # Resolve configuration before creating a pending financial record.
    try:
        adapter = get_payment_adapter()
    except PaymentAdapterError as exc:
        raise PaymentServiceError(
            "Payment processing is currently unavailable."
        ) from exc

    with transaction.atomic():
        try:
            rental = (
                Rental.objects.select_for_update()
                .select_related("reservation", "payment_method")
                .get(pk=rental_id)
            )
        except Rental.DoesNotExist as exc:
            raise PaymentServiceError("Rental was not found.") from exc

        payment_method = rental.payment_method

        if rental.status != Rental.Status.COMPLETED:
            raise PaymentServiceError("Rental is not completed.")

        amount = rental.final_amount
        if (
            not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount <= 0
        ):
            raise PaymentServiceError("Rental amount is not payable.")

        _validate_payment_method(rental, payment_method)

        existing = _get_existing_attempt(key)
        if existing is not None:
            payment = _check_replay(
                existing, rental, payment_method, amount
            )

            if payment.status != Payment.Status.PENDING:
                return payment

        else:
            if Payment.objects.filter(
                rental=rental,
                status=Payment.Status.SUCCEEDED,
            ).exists():
                raise PaymentServiceError(
                    "Rental already has a successful payment."
                )

            if Payment.objects.filter(
                rental=rental,
                status=Payment.Status.PENDING,
            ).exists():
                raise PaymentServiceError(
                    "Another payment attempt is already pending."
                )

            payment = Payment(
                id=uuid.uuid4(),
                rental=rental,
                payment_method=payment_method,
                idempotency_key=key,
                amount=amount,
                status=Payment.Status.PENDING,
                created_at=timezone.now(),
            )

            try:
                payment.full_clean()
                _persist_new_payment(payment)
            except IntegrityError as exc:
                raise PaymentServiceError(
                    "Payment attempt conflicts with an existing operation."
                ) from exc

    # Do not hold the database transaction open during adapter execution.
    try:
        result = adapter.charge(
            operation_id=payment.pk,
            amount=payment.amount,
            idempotency_key=key,
        )
    except PaymentAdapterError as exc:
        raise PaymentServiceError(
            "Payment adapter rejected the request."
        ) from exc

    if result.status not in {
        Payment.Status.SUCCEEDED,
        Payment.Status.FAILED,
        Payment.Status.PENDING,
    }:
        raise PaymentServiceError("Payment adapter returned an invalid result.")

    now = timezone.now()
    updates = {
        "external_id": result.external_id,
    }

    if result.status == Payment.Status.SUCCEEDED:
        updates.update(
            status=Payment.Status.SUCCEEDED,
            resolved_at=now,
            failure_code=None,
            failure_message=None,
        )
    elif result.status == Payment.Status.FAILED:
        updates.update(
            status=Payment.Status.FAILED,
            resolved_at=now,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
        )

    _persist_payment_update(payment, **updates)
    payment.refresh_from_db()
    return payment
