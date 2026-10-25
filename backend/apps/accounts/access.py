"""Fresh database-backed authorization snapshots for internal code and DRF.

These reads do not hold locks across a business write. A future write service
must resolve its target from stored objects, recheck access in its transaction,
and coordinate locks with account/profile changes. Request payload/query IDs
are never evidence of ownership or a trusted pickup point.
"""

import re

from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied

from .models import Account, Employee


_PERMISSION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")


def get_active_account(user):
    """Discard session/token claims and Django's per-instance permission cache."""
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return Account.objects.filter(
            pk=getattr(user, "pk", None), is_active=True
        ).first()
    except (ValidationError, ValueError, TypeError):
        return None


def get_employee(user):
    """Resolve the current employee role, profile and its actual pickup point."""
    account = get_active_account(user)
    if account is None or account.account_type != Account.AccountType.EMPLOYEE:
        return None
    return Employee.objects.select_related("account", "pickup_point").filter(
        account_id=account.pk,
        account__account_type=Account.AccountType.EMPLOYEE,
        account__is_active=True,
    ).first()


def has_permissions(user, permission_names):
    """Require an explicit, nonempty collection of Django permission names.

    No is_staff requirement applies to API code. Normal Django superuser
    semantics apply to permissions only, never to ownership or employee scope.
    """
    if not isinstance(permission_names, (tuple, list, set, frozenset)):
        return False
    if not permission_names or any(
        not isinstance(name, str) or _PERMISSION_NAME.fullmatch(name) is None
        for name in permission_names
    ):
        return False
    account = get_active_account(user)
    return account is not None and account.has_perms(permission_names)


def require_physical_access(user, pickup_point_id, permissions):
    """Require a current Employee, explicit rights and a server-resolved PV.

    Even a Django superuser needs the employee role and a profile in this point.
    The caller, not this ID comparison, establishes that the target is trusted.
    """
    employee = get_employee(user)
    try:
        point_id = Employee._meta.get_field("pickup_point").target_field.to_python(
            pickup_point_id
        )
    except (ValidationError, ValueError, TypeError, OverflowError):
        point_id = None
    if (
        employee is None
        or isinstance(pickup_point_id, bool)
        or not isinstance(pickup_point_id, (int, str))
        or point_id is None
        or employee.pickup_point_id != point_id
        or not has_permissions(user, permissions)
    ):
        raise PermissionDenied("Недостаточно прав для операции в этом ПВ.")
    return employee
