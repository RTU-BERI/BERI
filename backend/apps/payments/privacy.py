"""Local privacy boundaries for saved payment-method validation and services.

This protects newly created Django cursors inside the guard. It does not mask
raw SQL, pre-existing cursors, third-party execute wrappers or PostgreSQL logs.
"""

from contextlib import contextmanager
from threading import Lock, RLock
from weakref import WeakKeyDictionary

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import DatabaseError, connections, models, router
from django.views.decorators.debug import sensitive_variables


_MISSING = object()
_connection_locks = WeakKeyDictionary()
_registry_lock = Lock()


def _lock_for(connection):
    with _registry_lock:
        lock = _connection_locks.get(connection)
        if lock is None:
            lock = RLock()
            _connection_locks[connection] = lock
        return lock


@contextmanager
def private_queries(using):
    """Suppress Django SQL debugging only for this connection and scope.

    DEBUG and force_debug_cursor remain unchanged. An instance-level factory
    override bypasses CursorDebugWrapper, including its queries_log recording.
    The reentrant connection lock preserves nested restoration and also avoids
    non-LIFO restoration if a wrapper explicitly permits thread sharing.
    """
    connection = connections[using]
    connection.validate_thread_sharing()
    with _lock_for(connection):
        previous = connection.__dict__.get("make_debug_cursor", _MISSING)
        connection.make_debug_cursor = connection.make_cursor
        try:
            yield
        finally:
            if previous is _MISSING:
                connection.__dict__.pop("make_debug_cursor", None)
            else:
                connection.make_debug_cursor = previous


def _safe_error_fields(instance, error):
    allowed = {field.name for field in instance._meta.get_fields()}
    allowed.update(field.attname for field in instance._meta.concrete_fields)
    allowed.add(NON_FIELD_ERRORS)
    if not hasattr(error, "error_dict"):
        return (NON_FIELD_ERRORS,)
    return tuple(dict.fromkeys(
        name if isinstance(name, str) and name in allowed else NON_FIELD_ERRORS
        for name in error.error_dict
    )) or (NON_FIELD_ERRORS,)


@sensitive_variables("instance", "value", "error")
def safe_full_clean(instance, *args, **kwargs):
    """Run base-model validation without returning token-bearing exceptions.

    Field names survive for normal form/error handling; original messages,
    params, exception instances and driver diagnostics never leave this call.
    A new exception is raised after the handler has exited, so the discarded
    validation/driver exception is not retained as its context or cause.
    """
    using = instance._state.db or router.db_for_write(
        type(instance), instance=instance
    )
    failure_fields = None
    with private_queries(using):
        # CharField.to_python otherwise turns numeric provider IDs into strings.
        # Inspect the literal input first and preserve valid opaque strings.
        value = instance.external_id
        if not isinstance(value, str) or not value.strip() or len(value) > 255:
            failure_fields = ("external_id",)
        else:
            try:
                result = models.Model.full_clean(instance, *args, **kwargs)
            except ValidationError as error:
                failure_fields = _safe_error_fields(instance, error)
            except DatabaseError:
                failure_fields = (NON_FIELD_ERRORS,)
            else:
                return result

    # Deliberately outside both except handlers: no raw exception chaining.
    raise ValidationError({
        name: ValidationError(
            "Invalid payment method data.", code="invalid_payment_method"
        )
        for name in failure_fields
    }) from None


@sensitive_variables("instance", "value", "error")
def safe_financial_full_clean(instance, *args, **kwargs):
    """Validate Payment/Debt without exposing operation IDs or failure details.

    Payment has an optional opaque operation ID; Debt has no external ID. This
    separate entrypoint leaves PaymentMethod's required literal-token contract
    intact. It never calls the model override recursively.
    """
    using = instance._state.db or router.db_for_write(
        type(instance), instance=instance
    )
    failure_fields = None
    with private_queries(using):
        has_external_id = any(
            field.attname == "external_id" for field in instance._meta.concrete_fields
        )
        if has_external_id:
            value = instance.external_id
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 255
            ):
                failure_fields = ("external_id",)
        if failure_fields is None:
            try:
                result = models.Model.full_clean(instance, *args, **kwargs)
            except ValidationError as error:
                failure_fields = _safe_error_fields(instance, error)
            except (DatabaseError, TypeError, ValueError, OverflowError):
                failure_fields = (NON_FIELD_ERRORS,)
            else:
                return result

    # Outside the handlers so raw input-bearing exceptions are not retained.
    raise ValidationError({
        name: ValidationError(
            "Invalid financial data.", code="invalid_financial_data"
        )
        for name in failure_fields
    }) from None
