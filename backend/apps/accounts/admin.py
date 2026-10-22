from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group

from .forms import AccountChangeForm, AccountCreationForm, EmployeeForm
from .models import Account, Employee


def is_trusted_admin(request):
    """Only a current, active system superuser can assign privileged access."""
    return Account.objects.filter(
        pk=request.user.pk, is_staff=True, is_superuser=True,
        is_active=True, status=Account.Status.ACTIVE,
    ).exists()


@admin.register(Account)
class AccountAdmin(UserAdmin):
    form = AccountChangeForm
    add_form = AccountCreationForm
    list_display = ("phone", "account_type", "status", "is_active", "is_staff")
    list_filter = ("account_type", "status", "is_active", "is_staff")
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
        if not is_trusted_admin(request):
            fields += (
                "account_type", "is_staff", "is_superuser", "groups",
                "user_permissions",
            )
        return fields

    def _can_manage_target(self, request, obj):
        if is_trusted_admin(request):
            return True
        if obj is None:
            return True
        # Changing another administrator's phone/password is also privilege escalation.
        return (
            obj.pk != request.user.pk
            and obj.account_type == Account.AccountType.CUSTOMER
            and not obj.is_staff
            and not obj.is_superuser
            and not obj.groups.exists()
            and not obj.user_permissions.exists()
        )

    def has_change_permission(self, request, obj=None):
        return (
            super().has_change_permission(request, obj)
            and self._can_manage_target(request, obj)
        )

    def has_delete_permission(self, request, obj=None):
        # A bulk delete must not bypass target checks through obj=None.
        return is_trusted_admin(request) and super().has_delete_permission(request, obj)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeForm
    list_display = ("id", "account", "pickup_point")
    list_select_related = ("account", "pickup_point")
    search_fields = ("account__phone",)


class TrustedGroupAdmin(GroupAdmin):
    """Group edits can grant permissions to every current member."""

    def has_add_permission(self, request):
        return is_trusted_admin(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return is_trusted_admin(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return is_trusted_admin(request) and super().has_delete_permission(request, obj)


admin.site.unregister(Group)
admin.site.register(Group, TrustedGroupAdmin)
