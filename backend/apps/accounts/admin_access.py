"""Fresh, transaction-scoped authorization for the sensitive Admin models.

This policy belongs to these Admin views only. Category administration keeps
its standard Django permissions, and no canonical model write path is changed.
"""

from contextlib import contextmanager
from functools import wraps

from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router, transaction

from .models import Account, Employee


_LOCKED_AUTHORIZATION = object()


def _has_locked_authorization(request):
    context = getattr(request, "_beri_admin_authorization", None)
    return bool(
        context
        and context[0] is _LOCKED_AUTHORIZATION
        and context[1] == getattr(request.user, "pk", None)
        and transaction.get_connection(context[2]).in_atomic_block
    )


def is_trusted_admin(request):
    if _has_locked_authorization(request):
        return True
    user = request.user
    return bool(
        getattr(user, "is_authenticated", False)
        and Account.objects.filter(
            pk=user.pk, is_active=True, is_staff=True, is_superuser=True
        ).exists()
    )


def _account_id(value):
    try:
        return Account._meta.pk.to_python(unquote(str(value)))
    except (ValidationError, ValueError, TypeError):
        return None


@contextmanager
def trusted_admin_transaction(request, *, account_ids=()):
    """Lock current privileges through the write, including password and M2M.

    Account rows are locked in UUID order, including a known target. Employee
    creation uses the same Account lock as its canonical model validation.
    A concurrent deactivation/demotion therefore completes either before this
    authorization check or after the entire Admin transaction has committed.
    """
    if _has_locked_authorization(request):
        # A trusted actor may edit their own flags. The authorization preceding
        # that write remains valid only through this already locked request;
        # its next request must pass the fresh database check again.
        yield
        return
    if not getattr(request.user, "is_authenticated", False):
        raise PermissionDenied
    using = router.db_for_write(Account)
    actor_id = request.user.pk
    ids = {actor_id}
    ids.update(
        normalized
        for value in account_ids
        if (normalized := _account_id(value)) is not None
    )
    with transaction.atomic(using=using):
        accounts = {
            account.pk: account
            for account in Account.objects.using(using)
            .select_for_update()
            .filter(pk__in=ids)
            .order_by("pk")
        }
        actor = accounts.get(actor_id)
        if not (
            actor and actor.is_active and actor.is_staff and actor.is_superuser
        ):
            raise PermissionDenied
        # Do not reuse session-loaded permission caches or privilege flags.
        request.user = actor
        previous = getattr(request, "_beri_admin_authorization", None)
        request._beri_admin_authorization = (_LOCKED_AUTHORIZATION, actor_id, using)
        try:
            yield
        finally:
            if previous is None:
                del request._beri_admin_authorization
            else:
                request._beri_admin_authorization = previous


class TrustedAdminMixin:
    """All reads and writes of this Admin require a current trusted actor."""

    def has_module_permission(self, request):
        return is_trusted_admin(request)

    def has_view_permission(self, request, obj=None):
        return is_trusted_admin(request)

    def has_add_permission(self, request):
        return is_trusted_admin(request)

    def has_change_permission(self, request, obj=None):
        return is_trusted_admin(request)

    def has_delete_permission(self, request, obj=None):
        return is_trusted_admin(request)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if is_trusted_admin(request) else queryset.none()

    def _related_account_ids(self, request, kwargs):
        object_id = kwargs.get("object_id", kwargs.get("id"))
        if self.model is Account:
            ids = [_account_id(object_id)] if object_id is not None else []
            for value in request.POST.getlist("_selected_action"):
                account_id = _account_id(value)
                if account_id is None:
                    raise PermissionDenied("Invalid Account selection.")
                ids.append(account_id)
            return ids
        if self.model is Employee:
            ids = [_account_id(request.POST.get("account"))]
            employee_ids = []
            for value in request.POST.getlist("_selected_action"):
                try:
                    employee_id = int(value)
                except (ValueError, TypeError, OverflowError):
                    raise PermissionDenied("Invalid Employee selection.") from None
                if not -(1 << 63) <= employee_id < (1 << 63):
                    raise PermissionDenied("Invalid Employee selection.")
                employee_ids.append(employee_id)
            if object_id is not None:
                # Leave an invalid route ID to the Admin's ordinary 404 path.
                try:
                    employee_id = int(unquote(str(object_id)))
                except (ValueError, TypeError, OverflowError):
                    employee_id = None
                if employee_id is not None and -(1 << 63) <= employee_id < (1 << 63):
                    employee_ids.append(employee_id)
            ids.extend(
                Employee.objects.filter(pk__in=employee_ids).values_list(
                    "account_id", flat=True
                )
            )
            return ids
        return []

    def get_urls(self):
        urls = super().get_urls()
        for url in urls:
            original = url.callback

            @wraps(original)
            def guarded(request, *args, _original=original, **kwargs):
                if not getattr(request.user, "is_authenticated", False):
                    # Preserve Django's ordinary anonymous login redirect.
                    return _original(request, *args, **kwargs)
                with trusted_admin_transaction(
                    request,
                    account_ids=self._related_account_ids(request, kwargs),
                ):
                    return _original(request, *args, **kwargs)

            url.callback = guarded
        return urls

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        if obj is not None and request.method == "POST":
            using = router.db_for_write(self.model)
            if transaction.get_connection(using).in_atomic_block:
                return (
                    self.model.objects.using(using)
                    .select_for_update()
                    .get(pk=obj.pk)
                )
        return obj

    def save_model(self, request, obj, form, change):
        with trusted_admin_transaction(request):
            return super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        with trusted_admin_transaction(request):
            return super().save_related(request, form, formsets, change)

    def delete_model(self, request, obj):
        with trusted_admin_transaction(request):
            return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with trusted_admin_transaction(request):
            return super().delete_queryset(request, queryset)
