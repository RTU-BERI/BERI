"""Privacy checks use synthetic tokens only, never provider or card data."""

import logging
import traceback
from contextlib import contextmanager
from queue import Empty, Queue
from threading import Event, Thread
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import DatabaseError, connection, connections, models
from django.db.backends.utils import CursorDebugWrapper, CursorWrapper
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import privacy
from .models import PaymentMethod
from .test_support import FixtureMixin


TOKEN = "synthetic-private-token::DoNotLog-A7x"
VISIBLE = "synthetic-public-query-marker"
_ABSENT = object()


class RecordCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def collect_sql_debug():
    """Enable/capture debugging only as a test observation, then restore it."""
    wrapper = connections["default"]
    logger = logging.getLogger("django.db.backends")
    collector = RecordCollector()
    previous_level = logger.level
    previous_force = wrapper.force_debug_cursor
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    wrapper.force_debug_cursor = True
    try:
        yield collector.records
    finally:
        wrapper.force_debug_cursor = previous_force
        logger.removeHandler(collector)
        logger.setLevel(previous_level)


def logged_payload(records):
    return "\n".join(
        repr((record.getMessage(), record.args, getattr(record, "sql", None),
              getattr(record, "params", None)))
        for record in records
    )


class SafeExceptionAssertions:
    def assert_safe_exception(self, error):
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        representations = [
            str(error), repr(error), repr(error.args), repr(error.__dict__),
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        ]
        if hasattr(error, "message_dict"):
            representations.append(repr(error.message_dict))
        if hasattr(error, "error_dict"):
            for values in error.error_dict.values():
                for inner in values:
                    self.assertIsNone(inner.__cause__)
                    self.assertIsNone(inner.__context__)
                    self.assertIsNone(getattr(inner, "params", None))
                    representations.append(repr(inner.__dict__))
        self.assertNotIn(TOKEN, "\n".join(representations))

    def assert_sql_debug_has_no_token(self, records):
        self.assertNotIn(TOKEN, logged_payload(records))
        self.assertNotIn(TOKEN, repr(list(connections["default"].queries_log)))


@override_settings(DEBUG=True)
class PaymentMethodValidationPrivacyTests(
    SafeExceptionAssertions, FixtureMixin, TestCase
):
    def candidate(self, **overrides):
        values = {
            "customer_account": self.customer,
            "external_id": TOKEN,
            "status": "active",
            "verification_status": "pending",
            "is_default": False,
            "created_at": timezone.now(),
        }
        values.update(overrides)
        return PaymentMethod(**values)

    def test_str_repr_and_model_queryset_repr_never_display_the_token(self):
        method = self.make_method(external_id=TOKEN)
        self.assertEqual(str(method), str(method.pk))
        self.assertIn(str(method.pk), repr(method))
        self.assertNotIn(TOKEN, str(method))
        self.assertNotIn(TOKEN, repr(method))
        with privacy.private_queries("default"):
            self.assertNotIn(TOKEN, repr(PaymentMethod.objects.filter(pk=method.pk)))

    def test_literal_numeric_external_id_is_rejected_without_coercion(self):
        candidate = self.candidate(external_id=1234567890123456)
        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIsInstance(candidate.external_id, int)
        self.assertIn("external_id", raised.exception.message_dict)
        self.assert_safe_exception(raised.exception)

    def test_blank_and_oversized_external_ids_have_only_safe_errors(self):
        for value in (None, "", " \t\n", TOKEN + "x" * 256):
            with self.subTest(value_type=type(value).__name__):
                with collect_sql_debug() as records:
                    with self.assertRaises(ValidationError) as raised:
                        self.candidate(external_id=value).full_clean()
                self.assertIn("external_id", raised.exception.message_dict)
                self.assert_safe_exception(raised.exception)
                self.assert_sql_debug_has_no_token(records)

    def test_invalid_metadata_does_not_retain_validator_input_params(self):
        for field, value in (
            ("last4", TOKEN), ("brand", TOKEN * 3),
            ("expires_month", TOKEN), ("expires_year", TOKEN),
        ):
            with self.subTest(field=field), collect_sql_debug() as records:
                with self.assertRaises(ValidationError) as raised:
                    self.candidate(**{field: value}).full_clean()
                self.assertIn(field, raised.exception.message_dict)
                self.assert_safe_exception(raised.exception)
                self.assert_sql_debug_has_no_token(records)

    def test_opaque_identifier_is_preserved_exactly(self):
        token = "  Synthetic::CaSe-Sensitive-ID  "
        candidate = self.candidate(external_id=token)
        candidate.full_clean()
        self.assertEqual(candidate.external_id, token)

    def test_base_full_clean_is_called_once_with_the_requested_options(self):
        candidate = self.candidate()
        with patch.object(models.Model, "full_clean", return_value=None) as base:
            candidate.full_clean(
                exclude={"brand"}, validate_unique=False, validate_constraints=False
            )
        base.assert_called_once_with(
            candidate, exclude={"brand"},
            validate_unique=False, validate_constraints=False,
        )

    def test_nested_validation_messages_params_and_unknown_keys_are_sanitized(self):
        raw = ValidationError({
            "external_id": ValidationError(
                "Already registered %(value)s", params={"value": TOKEN}, code="unique"
            ),
            TOKEN: ValidationError(TOKEN, params={"payload": {"token": TOKEN}}),
        })
        with patch.object(models.Model, "full_clean", side_effect=raw):
            with self.assertRaises(ValidationError) as raised:
                self.candidate().full_clean()
        self.assertEqual(set(raised.exception.message_dict), {"external_id", NON_FIELD_ERRORS})
        self.assert_safe_exception(raised.exception)

    def test_database_error_during_validation_has_no_raw_context_or_cause(self):
        raw = DatabaseError("Synthetic database detail: " + TOKEN)
        raw.__cause__ = RuntimeError(TOKEN)
        with patch.object(models.Model, "full_clean", side_effect=raw):
            with self.assertRaises(ValidationError) as raised:
                self.candidate().full_clean()
        self.assertEqual(set(raised.exception.message_dict), {NON_FIELD_ERRORS})
        self.assert_safe_exception(raised.exception)

    def test_complete_validation_traceback_does_not_include_the_original_error(self):
        raw = ValidationError(TOKEN, params={"value": TOKEN})
        with patch.object(models.Model, "full_clean", side_effect=raw):
            try:
                self.candidate().full_clean()
            except ValidationError as error:
                # assertRaises clears __traceback__; inspect the live exception
                # here so this really checks a complete ordinary CLI traceback.
                rendered = "".join(traceback.format_exception(
                    type(error), error, error.__traceback__
                ))
                self.assertIn("safe_full_clean", rendered)
                self.assert_safe_exception(error)
            else:
                self.fail("Invalid data unexpectedly passed validation.")
        self.assertNotIn(TOKEN, rendered)

    def test_public_full_clean_suppresses_a_real_token_bearing_constraint_query(self):
        candidate = self.candidate()
        observed_parameters = []

        def observe(execute, sql, params, many, context):
            # Deliberate test-only observer: third-party execute wrappers are
            # outside the privacy promise and can inspect bound parameters.
            observed_parameters.append(repr(params))
            return execute(sql, params, many, context)

        token_check = models.CheckConstraint(
            condition=models.Q(external_id=TOKEN),
            name="test_only_private_identifier_check",
        )
        # Runtime validation only: no DDL and no new business constraint.
        with patch.object(
            PaymentMethod._meta, "constraints",
            [*PaymentMethod._meta.constraints, token_check],
        ):
            with collect_sql_debug() as records, connection.execute_wrapper(observe):
                candidate.full_clean()
        self.assertTrue(any(TOKEN in params for params in observed_parameters))
        self.assert_sql_debug_has_no_token(records)


@override_settings(DEBUG=True)
class PrivateQueryGuardTests(SafeExceptionAssertions, TestCase):
    def test_guard_suppresses_sql_debug_without_changing_debug_configuration(self):
        wrapper = connections["default"]
        original = wrapper.__dict__.get("make_debug_cursor", _ABSENT)
        with collect_sql_debug() as records:
            with privacy.private_queries("default"):
                self.assertTrue(settings.DEBUG)
                self.assertTrue(wrapper.force_debug_cursor)
                with wrapper.cursor() as cursor:
                    self.assertIsInstance(cursor, CursorWrapper)
                    self.assertNotIsInstance(cursor, CursorDebugWrapper)
                    cursor.execute("SELECT %s", [TOKEN])
                    self.assertEqual(cursor.fetchone()[0], TOKEN)
            # SQL debugging is genuinely restored for the next new cursor.
            with wrapper.cursor() as cursor:
                self.assertIsInstance(cursor, CursorDebugWrapper)
                cursor.execute("SELECT %s", [VISIBLE])
                self.assertEqual(cursor.fetchone()[0], VISIBLE)
        self.assertIs(wrapper.__dict__.get("make_debug_cursor", _ABSENT), original)
        self.assert_sql_debug_has_no_token(records)
        self.assertIn(VISIBLE, logged_payload(records))
        self.assertIn(VISIBLE, repr(list(wrapper.queries_log)))

    def test_nested_guards_restore_each_factory_and_remove_a_temporary_override(self):
        wrapper = connections["default"]
        previous = wrapper.__dict__.get("make_debug_cursor", _ABSENT)
        with privacy.private_queries("default"):
            outer_factory = wrapper.__dict__["make_debug_cursor"]
            with privacy.private_queries("default"):
                with wrapper.cursor() as cursor:
                    self.assertNotIsInstance(cursor, CursorDebugWrapper)
            self.assertIs(wrapper.__dict__["make_debug_cursor"], outer_factory)
        self.assertIs(wrapper.__dict__.get("make_debug_cursor", _ABSENT), previous)

    def test_existing_instance_factory_is_restored_exactly_after_exception(self):
        wrapper = connections["default"]
        previous = wrapper.__dict__.get("make_debug_cursor", _ABSENT)
        custom_factory = wrapper.make_debug_cursor
        wrapper.make_debug_cursor = custom_factory
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                with privacy.private_queries("default"):
                    with privacy.private_queries("default"):
                        raise RuntimeError("synthetic failure")
            self.assertIs(wrapper.__dict__["make_debug_cursor"], custom_factory)
        finally:
            if previous is _ABSENT:
                wrapper.__dict__.pop("make_debug_cursor", None)
            else:
                wrapper.make_debug_cursor = previous

    def test_alias_selection_does_not_patch_another_connection_or_its_class(self):
        class FakeConnection:
            def validate_thread_sharing(self):
                pass

            def make_cursor(self, cursor):
                return ("plain", cursor)

            def make_debug_cursor(self, cursor):
                return ("debug", cursor)

        selected, other = FakeConnection(), FakeConnection()
        original_class_factory = FakeConnection.make_debug_cursor
        with patch.object(privacy, "connections", {"vault": selected, "other": other}):
            with privacy.private_queries("vault"):
                self.assertEqual(selected.make_debug_cursor("cursor"), ("plain", "cursor"))
                self.assertEqual(other.make_debug_cursor("cursor"), ("debug", "cursor"))
                self.assertIs(FakeConnection.make_debug_cursor, original_class_factory)
        self.assertNotIn("make_debug_cursor", selected.__dict__)
        self.assertEqual(selected.make_debug_cursor("cursor"), ("debug", "cursor"))


@override_settings(DEBUG=True)
class PrivateQueryThreadTests(SafeExceptionAssertions, TransactionTestCase):
    def test_guard_is_thread_local_and_other_connections_keep_their_debugging(self):
        protected_ready, visible_done = Event(), Event()
        results = Queue()

        def worker(protected):
            wrapper = connections["default"]
            previous_factory = wrapper.__dict__.get("make_debug_cursor", _ABSENT)
            previous_force = wrapper.force_debug_cursor
            wrapper.force_debug_cursor = True
            try:
                if protected:
                    with privacy.private_queries("default"):
                        protected_ready.set()
                        if not visible_done.wait(timeout=5):
                            raise AssertionError("The visible worker did not complete.")
                        with wrapper.cursor() as cursor:
                            if isinstance(cursor, CursorDebugWrapper):
                                raise AssertionError("Protected cursor still uses SQL debugging.")
                            cursor.execute("SELECT %s", [TOKEN])
                            cursor.fetchone()
                else:
                    if not protected_ready.wait(timeout=5):
                        raise AssertionError("The protected worker did not enter its guard.")
                    with wrapper.cursor() as cursor:
                        if not isinstance(cursor, CursorDebugWrapper):
                            raise AssertionError("Another connection lost SQL debugging.")
                        cursor.execute("SELECT %s", [VISIBLE])
                        cursor.fetchone()
                restored = (
                    wrapper.__dict__.get("make_debug_cursor", _ABSENT) is previous_factory
                )
                results.put((protected, "ok", id(wrapper), restored,
                             repr(list(wrapper.queries_log))))
            except Exception as error:
                results.put((protected, "error", repr(error)))
            finally:
                wrapper.force_debug_cursor = previous_force
                wrapper.close()
                if not protected:
                    visible_done.set()

        threads = [
            Thread(target=worker, args=(protected,), daemon=True,
                   name=f"payment-privacy-{protected}")
            for protected in (True, False)
        ]
        with collect_sql_debug() as records:
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
            finally:
                visible_done.set()
                for thread in threads:
                    if thread.is_alive():
                        thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        outcomes = {}
        for _ in threads:
            try:
                result = results.get(timeout=1)
            except Empty:
                self.fail("A privacy worker exited without recording its result.")
            outcomes[result[0]] = result
        for result in outcomes.values():
            self.assertEqual(result[1], "ok", outcomes)
            self.assertTrue(result[3])
        self.assertNotEqual(outcomes[True][2], outcomes[False][2])
        self.assertNotIn(TOKEN, outcomes[True][4])
        self.assertIn(VISIBLE, outcomes[False][4])
        self.assertNotIn(TOKEN, logged_payload(records))
        self.assertIn(VISIBLE, logged_payload(records))
