"""Model-level privacy for synthetic stored financial records."""

import logging
import traceback
from contextlib import contextmanager
from unittest.mock import patch

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import DatabaseError, connection, connections, models
from django.test import TestCase, override_settings
from django.utils import timezone

from . import privacy
from .models import Debt, Payment
from .financial_test_support import FinancialFixtureMixin


SECRET = "Synthetic-Operation-And-Failure-Secret::DoNotLog-10q"


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def forced_debug():
    wrapper = connections["default"]
    logger = logging.getLogger("django.db.backends")
    collector = _Collector()
    level, force = logger.level, wrapper.force_debug_cursor
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    wrapper.force_debug_cursor = True
    try:
        yield collector.records
    finally:
        wrapper.force_debug_cursor = force
        logger.removeHandler(collector)
        logger.setLevel(level)


@override_settings(DEBUG=True)
class FinancialPrivacyTests(FinancialFixtureMixin, TestCase):
    def payment_candidate(self, **overrides):
        values = dict(
            rental=self.rental, payment_method=self.rental.payment_method,
            external_id=None, amount=self.rental.final_amount, status="pending",
            failure_code=None, failure_message=None, created_at=timezone.now(),
            resolved_at=None,
        )
        values.update(overrides)
        return Payment(**values)

    def debt_candidate(self):
        return Debt(
            rental=self.rental, amount=self.rental.final_amount, status="open",
            created_at=timezone.now(), settled_at=None,
        )

    def assert_safe(self, error):
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        payload = [str(error), repr(error), repr(error.args), repr(error.__dict__),
                   "".join(traceback.format_exception(type(error), error, error.__traceback__))]
        for errors in getattr(error, "error_dict", {}).values():
            for nested in errors:
                self.assertIsNone(nested.__cause__)
                self.assertIsNone(nested.__context__)
                self.assertIsNone(getattr(nested, "params", None))
                payload.append(repr(nested.__dict__))
        self.assertNotIn(SECRET, "\n".join(payload))

    def assert_private_debug(self, records):
        payload = "\n".join(repr((
            record.getMessage(), record.args, getattr(record, "sql", None),
            getattr(record, "params", None),
        )) for record in records)
        self.assertNotIn(SECRET, payload)
        self.assertNotIn(SECRET, repr(list(connections["default"].queries_log)))

    def test_optional_operation_id_and_debt_without_external_field_validate(self):
        with forced_debug() as records:
            payment = self.payment_candidate()
            payment.full_clean()
            self.debt_candidate().full_clean()
        self.assertIsNone(payment.external_id)
        self.assert_private_debug(records)

    def test_literal_numeric_operation_id_is_not_coerced(self):
        candidate = self.payment_candidate(external_id=123456789)
        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIsInstance(candidate.external_id, int)
        self.assertIn("external_id", raised.exception.message_dict)
        self.assert_safe(raised.exception)

    def test_blank_oversized_and_nonstring_operation_ids_are_safe(self):
        for value in ("", " \t\n", SECRET * 8, {"sensitive": SECRET}):
            with self.subTest(value_type=type(value).__name__), forced_debug() as records:
                with self.assertRaises(ValidationError) as raised:
                    self.payment_candidate(external_id=value).full_clean()
                self.assert_safe(raised.exception)
                self.assert_private_debug(records)

    def test_opaque_operation_id_retains_case_and_surrounding_whitespace(self):
        value = "  Synthetic::CaSe-Sensitive-Operation  "
        candidate = self.payment_candidate(external_id=value)
        candidate.full_clean()
        self.assertEqual(candidate.external_id, value)

    def test_financial_validation_calls_the_base_once_with_original_options(self):
        for candidate in (self.payment_candidate(), self.debt_candidate()):
            with self.subTest(model=type(candidate).__name__):
                with patch.object(models.Model, "full_clean", return_value=None) as base:
                    candidate.full_clean(exclude={"amount"}, validate_unique=False,
                                         validate_constraints=False)
                base.assert_called_once_with(candidate, exclude={"amount"},
                    validate_unique=False, validate_constraints=False)

    def test_nested_validator_payload_and_unknown_field_name_are_sanitized(self):
        raw = ValidationError({
            "failure_message": ValidationError("Raw %(value)s", params={"value": SECRET}),
            SECRET: ValidationError(SECRET, params={"data": SECRET}),
        })
        with patch.object(models.Model, "full_clean", side_effect=raw):
            with self.assertRaises(ValidationError) as raised:
                self.payment_candidate(external_id=SECRET).full_clean()
        self.assertEqual(set(raised.exception.message_dict), {"failure_message", NON_FIELD_ERRORS})
        self.assert_safe(raised.exception)

    def test_database_and_conversion_failures_drop_original_context(self):
        for raw in (DatabaseError(SECRET), TypeError(SECRET), ValueError(SECRET)):
            with self.subTest(error_type=type(raw).__name__):
                with patch.object(models.Model, "full_clean", side_effect=raw):
                    with self.assertRaises(ValidationError) as raised:
                        self.payment_candidate(external_id=SECRET).full_clean()
                self.assert_safe(raised.exception)

    def test_actual_invalid_amounts_and_failure_metadata_have_no_input_params(self):
        for override in ({"amount": SECRET}, {"failure_code": SECRET * 8}):
            with self.subTest(field=next(iter(override))), forced_debug() as records:
                with self.assertRaises(ValidationError) as raised:
                    self.payment_candidate(external_id=SECRET, **override).full_clean()
                self.assert_safe(raised.exception)
                self.assert_private_debug(records)

    def test_runtime_constraint_binding_sensitive_text_is_private_without_ddl(self):
        now = timezone.now()
        candidate = self.payment_candidate(
            external_id=SECRET, status="failed", resolved_at=now,
            created_at=now, failure_message=SECRET,
        )
        bound_secret = []
        constraint = models.CheckConstraint(
            condition=models.Q(failure_message=SECRET), name="test_only_sensitive_binding",
        )

        def observe(execute, sql, params, many, context):
            if SECRET in repr(params):
                bound_secret.append(True)
            return execute(sql, params, many, context)

        with patch.object(Payment._meta, "constraints", [*Payment._meta.constraints, constraint]):
            with connection.execute_wrapper(observe), forced_debug() as records:
                candidate.full_clean()
        self.assertTrue(bound_secret, "The privacy probe must execute a real sensitive bind.")
        self.assert_private_debug(records)

    def test_validation_restores_a_preexisting_cursor_factory_after_failure(self):
        wrapper = connections["default"]
        absent = object()
        previous = wrapper.__dict__.get("make_debug_cursor", absent)
        custom = wrapper.make_debug_cursor
        wrapper.make_debug_cursor = custom
        try:
            with self.assertRaises(ValidationError):
                self.payment_candidate(external_id=123).full_clean()
            self.assertIs(wrapper.__dict__["make_debug_cursor"], custom)
        finally:
            if previous is absent:
                wrapper.__dict__.pop("make_debug_cursor", None)
            else:
                wrapper.make_debug_cursor = previous

    def test_payment_and_debt_repr_contain_only_local_identity(self):
        with privacy.private_queries("default"):
            payment = self.make_payment(external_id=SECRET)
            debt = self.make_debt()
        for instance in (payment, debt):
            with self.subTest(model=type(instance).__name__):
                self.assertEqual(str(instance), str(instance.pk))
                self.assertIn(str(instance.pk), repr(instance))
                self.assertNotIn(SECRET, repr(instance))
