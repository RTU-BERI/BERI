"""Current database-backed authorization for services and DRF views.

Never derive a trusted pickup point from request data. A write service should
resolve its target from stored business objects, then call
``require_physical_access`` inside the transaction that performs the operation.
Services introducing concurrent employee transfers must lock the employee row
as part of that transaction; these read helpers do not lock across a write.
"""

from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied

from .models import Account, Employee


def get_active_account(user):
    """Return a fresh active Account, or None, ignoring token/session claims."""
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return Account.objects.filter(
            pk=getattr(user, "pk", None),
            is_active=True,
            status=Account.Status.ACTIVE,
        ).first()
    except (ValidationError, ValueError, TypeError):
        return None


def get_employee(user):
    """Return the current valid work profile, including its actual pickup point."""
    account = get_active_account(user)
    if account is None or account.account_type != Account.AccountType.EMPLOYEE:
        return None
    return Employee.objects.select_related("account", "pickup_point").filter(
        account_id=account.pk,
        account__account_type=Account.AccountType.EMPLOYEE,
        account__status=Account.Status.ACTIVE,
        account__is_active=True,
    ).first()


def has_permissions(user, permission_names):
    """Check an explicit nonempty collection using fresh Django permissions.

An absent declaration fails closed. Refreshing the Account also discards
ModelBackend's per-instance permission caches after grants or revocations.
Standard Django superuser semantics apply to permissions, never to PV scope.
"""
    if not isinstance(permission_names, (tuple, list, set, frozenset)):
        return False
    if not permission_names or any(
        not isinstance(name, str)
        or len(name.split(".")) != 2
        or not all(name.split("."))
        for name in permission_names
    ):
        return False
    account = get_active_account(user)
    return account is not None and account.has_perms(permission_names)


def require_physical_access(user, pickup_point_id, permissions):
    """Return Employee or raise PermissionDenied for a server-resolved target.

``pickup_point_id`` must come from a persisted operation target or another
trusted server source. Payload/query IDs cannot establish authorization.
Even a Django superuser needs a current employee profile in this exact point.
"""
    employee = get_employee(user)
    if (
        employee is None
        or pickup_point_id is None
        or str(employee.pickup_point_id) != str(pickup_point_id)
        or not has_permissions(user, permissions)
    ):
        raise PermissionDenied("Недостаточно прав для операции в этом ПВ.")
    return employee
