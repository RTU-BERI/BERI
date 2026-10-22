"""The Account table from the approved BERI physical data model."""

import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils.translation import gettext_lazy as _

from .managers import AccountManager
from .employee_managers import EmployeeManager
from .validators import (
    PHONE_PATTERN,
    normalize_phone,
    validate_phone,
    validate_status,
)


class Account(AbstractBaseUser, PermissionsMixin):
    class AccountType(models.TextChoices):
        CUSTOMER = "customer", _("Customer")
        EMPLOYEE = "employee", _("Employee")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, unique=True, validators=[validate_phone])
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    status = models.CharField(max_length=20, validators=[validate_status])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # password, last_login, and is_superuser are standard inherited fields.
    objects = AccountManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["account_type", "status"]

    class Meta:
        db_table = "account"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(account_type__in=["customer", "employee"]),
                name="account_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(phone__regex=rf"^{PHONE_PATTERN}$"),
                name="account_phone_canonical",
            ),
        ]

    def _normalize_and_validate_identity(self):
        errors = {}
        try:
            self.phone = normalize_phone(self.phone)
        except ValidationError as error:
            errors["phone"] = error
        try:
            self._meta.get_field("account_type").clean(self.account_type, self)
        except ValidationError as error:
            errors["account_type"] = error
        try:
            validate_status(self.status)
            self._meta.get_field("status").clean(self.status, self)
        except ValidationError as error:
            errors["status"] = error
        if errors:
            raise ValidationError(errors)

    def clean(self):
        # Validate ASCII before AbstractBaseUser performs NFKC normalization.
        self._normalize_and_validate_identity()
        super().clean()

    def full_clean(
        self, exclude=None, validate_unique=True, validate_constraints=True
    ):
        # Separators may make valid input longer than the storage field limit.
        self._normalize_and_validate_identity()
        return super().full_clean(
            exclude=exclude,
            validate_unique=validate_unique,
            validate_constraints=validate_constraints,
        )

    def save(self, *args, **kwargs):
        original_phone = self.phone
        self._normalize_and_validate_identity()
        if self.phone != original_phone:
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"phone"}
            elif len(args) >= 4 and args[3]:
                args = (*args[:3], set(args[3]) | {"phone"}, *args[4:])
        return super().save(*args, **kwargs)


class Employee(models.Model):
    id = models.BigAutoField(primary_key=True)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee",
    )
    pickup_point = models.ForeignKey(
        "pickup_points.PickupPoint",
        on_delete=models.PROTECT,
        related_name="employees",
    )

    objects = EmployeeManager()

    class Meta:
        db_table = "employee"

    def _validate_service_account(self, using, *, lock=False):
        account_model = self._meta.get_field("account").remote_field.model
        queryset = account_model._base_manager.using(using)
        if lock:
            queryset = queryset.select_for_update()
        try:
            # Read the database, not a possibly stale self.account cache.
            account_type = queryset.values_list("account_type", flat=True).get(
                pk=self.account_id
            )
        except (account_model.DoesNotExist, ValidationError, ValueError, TypeError):
            raise ValidationError(
                {"account": "Choose an existing service account."}
            ) from None
        if account_type != Account.AccountType.EMPLOYEE:
            raise ValidationError(
                {"account": "Employee requires account_type='employee'."}
            )

    def clean(self):
        super().clean()
        using = self._state.db or router.db_for_read(type(self), instance=self)
        self._validate_service_account(using)

    def save(self, *args, **kwargs):
        self.full_clean()
        using = kwargs.get("using") or self._state.db or router.db_for_write(
            type(self), instance=self
        )
        with transaction.atomic(using=using):
            self._validate_service_account(using, lock=True)
            return super().save(*args, **kwargs)
