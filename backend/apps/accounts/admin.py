from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router

from .admin_access import TrustedAdminMixin, trusted_admin_transaction
from .forms import AccountChangeForm, AccountCreationForm, EmployeeForm
from .models import Account, Employee


@admin.register(Account)
class AccountAdmin(TrustedAdminMixin, UserAdmin):
    form = AccountChangeForm
    add_form = AccountCreationForm
    list_display = ("phone", "account_type", "status", "is_active", "is_staff")
    list_filter = ("account_type", "is_active", "is_staff", "is_superuser")
    search_fields = ("phone",)
    ordering = ("phone",)
    readonly_fields = ("last_login", "phone_verified_at", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Account", {"fields": ("account_type", "status", "is_active")}),
        ("Permissions", {"fields": (
            "is_staff", "is_superuser", "groups", "user_permissions",
        )}),
        ("Dates", {"fields": (
            "phone_verified_at", "last_login", "created_at", "updated_at",
        )}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": (
            "phone", "password1", "password2", "account_type", "status", "is_active",
        )}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj and Employee.objects.filter(account_id=obj.pk).exists():
            fields += ("account_type",)
        return fields

    def save_model(self, request, obj, form, change):
        using = router.db_for_write(Account)
        with trusted_admin_transaction(request, account_ids=(obj.pk,) if change else ()):
            if change:
                current = Account.objects.using(using).select_for_update().get(pk=obj.pk)
                if (
                    current.account_type != obj.account_type
                    and Employee.objects.using(using).filter(account_id=obj.pk).exists()
                ):
                    raise ValidationError(
                        "An account linked to an Employee cannot change account type."
                    )
            return super().save_model(request, obj, form, change)

    def user_change_password(self, request, id, form_url=""):
        # Django UserAdmin's password view has no atomic wrapper of its own.
        with trusted_admin_transaction(request, account_ids=(id,)):
            return super().user_change_password(request, id, form_url)


@admin.register(Employee)
class EmployeeAdmin(TrustedAdminMixin, admin.ModelAdmin):
    form = EmployeeForm
    list_display = ("id", "account", "pickup_point")
    list_select_related = ("account", "pickup_point")
    search_fields = ("account__phone",)

    def get_readonly_fields(self, request, obj=None):
        return ("account", "pickup_point") if obj else ()

    def save_model(self, request, obj, form, change):
        using = router.db_for_write(Employee)
        with trusted_admin_transaction(request, account_ids=(obj.account_id,)):
            if change:
                current = Employee.objects.using(using).select_for_update().get(pk=obj.pk)
                if (
                    obj.account_id != current.account_id
                    or obj.pickup_point_id != current.pickup_point_id
                ):
                    raise PermissionDenied(
                        "Employee account and pickup point cannot be changed here."
                    )
            return super().save_model(request, obj, form, change)


class TrustedGroupAdmin(TrustedAdminMixin, GroupAdmin):
    """Group membership can grant access; model permissions cannot delegate it."""


admin.site.unregister(Group)
admin.site.register(Group, TrustedGroupAdmin)
