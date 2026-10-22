"""Reusable permissions and queryset scopes for future business endpoints.

Client object views use ``IsClientOwner`` AND ``ClientOwnedQuerysetMixin``;
set ``owner_lookup`` to an ORM path ending in the owner's account PK (for
example ``customer_account_id`` or ``rental__reservation__customer_account_id``).

Physical object views use ``CanPerformPhysicalOperation`` AND
``EmployeePickupPointQuerysetMixin``; declare ``pickup_point_lookup`` (for
example ``pickup_point_id``) and nonempty ``required_permissions``. Unsafe
requests also require ``get_physical_pickup_point_id()`` to resolve the target
from stored objects on the server. Read-only list access is limited by the
mixin; object access is checked independently. Write services must additionally
call ``require_physical_access`` in their transaction. No helper trusts payload
or query parameters as evidence of a user's role, owner, or point.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from .access import (
    get_active_account,
    get_employee,
    has_permissions,
    require_physical_access,
)
from .models import Account


def _lookup(view, attribute):
    value = getattr(view, attribute, None)
    return value if isinstance(value, str) and value.strip() else None


def _object_matches(obj, lookup, value):
    """Check stored values rather than potentially stale object attributes."""
    return (
        lookup is not None
        and getattr(obj, "pk", None) is not None
        and obj.__class__._default_manager.filter(pk=obj.pk).filter(
            **{lookup: value}
        ).exists()
    )


class IsClient(BasePermission):
    def has_permission(self, request, view):
        account = get_active_account(request.user)
        return (
            account is not None
            and account.account_type == Account.AccountType.CUSTOMER
        )


class IsClientOwner(IsClient):
    def has_permission(self, request, view):
        return (
            _lookup(view, "owner_lookup") is not None
            and isinstance(view, ClientOwnedQuerysetMixin)
            and super().has_permission(request, view)
        )

    def has_object_permission(self, request, view, obj):
        account = get_active_account(request.user)
        return (
            account is not None
            and account.account_type == Account.AccountType.CUSTOMER
            and _object_matches(obj, _lookup(view, "owner_lookup"), account.pk)
        )


class HasDjangoPermissions(BasePermission):
    def has_permission(self, request, view):
        return has_permissions(
            request.user, getattr(view, "required_permissions", None)
        )


class CanPerformPhysicalOperation(BasePermission):
    def has_permission(self, request, view):
        if (
            _lookup(view, "pickup_point_lookup") is None
            or not isinstance(view, EmployeePickupPointQuerysetMixin)
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
                request.user,
                resolve_point(),
                view.required_permissions,
            )
        return True

    def has_object_permission(self, request, view, obj):
        employee = get_employee(request.user)
        return (
            employee is not None
            and has_permissions(
                request.user, getattr(view, "required_permissions", None)
            )
            and _object_matches(
                obj, _lookup(view, "pickup_point_lookup"), employee.pickup_point_id
            )
        )


class ClientOwnedQuerysetMixin:
    """Scope get_queryset before list, detail, filter backends, or mutations."""

    def get_queryset(self):
        queryset = super().get_queryset()
        account = get_active_account(self.request.user)
        lookup = _lookup(self, "owner_lookup")
        if (
            lookup is None
            or account is None
            or account.account_type != Account.AccountType.CUSTOMER
        ):
            return queryset.none()
        return queryset.filter(**{lookup: account.pk})


class EmployeePickupPointQuerysetMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        employee = get_employee(self.request.user)
        lookup = _lookup(self, "pickup_point_lookup")
        if (
            lookup is None
            or employee is None
            or not has_permissions(
                self.request.user, getattr(self, "required_permissions", None)
            )
        ):
            return queryset.none()
        return queryset.filter(**{lookup: employee.pickup_point_id})
