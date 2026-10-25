"""Composable DRF role, permission and database queryset checks.

Client views use IsClientOwner AND ClientOwnedQuerysetMixin and declare an
owner_lookup field path ending in the owner's account PK. Physical views use
CanPerformPhysicalOperation AND EmployeePickupPointQuerysetMixin and declare
pickup_point_lookup plus nonempty required_permissions. Each lookup consists
only of actual ORM fields (no transforms or lookup operators).

Unsafe physical requests additionally implement get_physical_pickup_point_id()
using stored server objects. A write service must repeat authorization in its
transaction; these helpers provide fresh snapshots, not write serialization.
No production business endpoints or client-controlled role claims are added.
"""

from django.core.exceptions import FieldDoesNotExist, FieldError, ValidationError
from rest_framework.permissions import BasePermission, SAFE_METHODS

from .access import (
    get_active_account,
    get_employee,
    has_permissions,
    require_physical_access,
)
from .models import Account


def _lookup(view, attribute, model):
    """Validate a server-configured field path; malformed scopes fail closed."""
    path = getattr(view, attribute, None)
    if not isinstance(path, str) or not path or path != path.strip():
        return None
    parts = path.split("__")
    for index, part in enumerate(parts):
        if not part.isidentifier() or part.startswith("_"):
            return None
        try:
            field = model._meta.pk if part == "pk" else model._meta.get_field(part)
        except (FieldDoesNotExist, AttributeError):
            return None
        if index != len(parts) - 1:
            if not field.is_relation or field.related_model is None:
                return None
            model = field.related_model
        elif field.many_to_many or field.one_to_many:
            return None
    return path


def _view_model(view):
    return getattr(getattr(view, "queryset", None), "model", None)


def _object_matches(obj, lookup, value):
    if lookup is None or getattr(obj, "pk", None) is None:
        return False
    try:
        return obj.__class__._default_manager.filter(pk=obj.pk).filter(
            **{lookup: value}
        ).exists()
    except (FieldError, ValidationError, ValueError, TypeError, OverflowError):
        return False


class IsClient(BasePermission):
    def has_permission(self, request, view):
        account = get_active_account(request.user)
        return (
            account is not None
            and account.account_type == Account.AccountType.CUSTOMER
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsClientOwner(IsClient):
    def has_permission(self, request, view):
        return (
            isinstance(view, ClientOwnedQuerysetMixin)
            and _lookup(view, "owner_lookup", _view_model(view)) is not None
            and super().has_permission(request, view)
        )

    def has_object_permission(self, request, view, obj):
        account = get_active_account(request.user)
        return (
            isinstance(view, ClientOwnedQuerysetMixin)
            and account is not None
            and account.account_type == Account.AccountType.CUSTOMER
            and _object_matches(
                obj, _lookup(view, "owner_lookup", type(obj)), account.pk
            )
        )


class HasDjangoPermissions(BasePermission):
    def has_permission(self, request, view):
        return has_permissions(
            request.user, getattr(view, "required_permissions", None)
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class CanPerformPhysicalOperation(BasePermission):
    def has_permission(self, request, view):
        if (
            not isinstance(view, EmployeePickupPointQuerysetMixin)
            or _lookup(view, "pickup_point_lookup", _view_model(view)) is None
            or get_employee(request.user) is None
            or not has_permissions(
                request.user, getattr(view, "required_permissions", None)
            )
        ):
            return False
        if request.method not in SAFE_METHODS:
            resolve_point = getattr(view, "get_physical_pickup_point_id", None)
            if not callable(resolve_point):
                return False
            require_physical_access(
                request.user, resolve_point(), view.required_permissions
            )
        return True

    def has_object_permission(self, request, view, obj):
        employee = get_employee(request.user)
        return (
            isinstance(view, EmployeePickupPointQuerysetMixin)
            and employee is not None
            and has_permissions(
                request.user, getattr(view, "required_permissions", None)
            )
            and _object_matches(
                obj,
                _lookup(view, "pickup_point_lookup", type(obj)),
                employee.pickup_point_id,
            )
        )


class ClientOwnedQuerysetMixin:
    """Scope rows before DRF list/detail, search, filters or object mutations."""

    def get_queryset(self):
        queryset = super().get_queryset()
        account = get_active_account(self.request.user)
        lookup = _lookup(self, "owner_lookup", queryset.model)
        if (
            lookup is None
            or account is None
            or account.account_type != Account.AccountType.CUSTOMER
        ):
            return queryset.none()
        try:
            return queryset.filter(**{lookup: account.pk})
        except (FieldError, ValidationError, ValueError, TypeError, OverflowError):
            return queryset.none()


class EmployeePickupPointQuerysetMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        employee = get_employee(self.request.user)
        lookup = _lookup(self, "pickup_point_lookup", queryset.model)
        if (
            lookup is None
            or employee is None
            or not has_permissions(
                self.request.user, getattr(self, "required_permissions", None)
            )
        ):
            return queryset.none()
        try:
            return queryset.filter(**{lookup: employee.pickup_point_id})
        except (FieldError, ValidationError, ValueError, TypeError, OverflowError):
            return queryset.none()
