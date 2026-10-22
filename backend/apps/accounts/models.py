import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from .managers import AccountManager
from .phone import PhoneField, normalize_phone


class Account(AbstractBaseUser, PermissionsMixin):
    class AccountType(models.TextChoices):
        CUSTOMER = "customer", "Client"
        EMPLOYEE = "employee", "Employee"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        BLOCKED = "blocked", "Blocked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = PhoneField(max_length=20, unique=True)
    account_type = models.CharField(
        max_length=20, choices=AccountType.choices, default=AccountType.CUSTOMER,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
                condition=models.Q(status__in=["active", "blocked"]),
                name="account_status_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="blocked", is_active=True),
                name="account_blocked_inactive",
            ),
            models.CheckConstraint(
                condition=models.Q(phone__regex=r"^\+[1-9][0-9]{1,14}$"),
                name="account_phone_canonical",
            ),
        ]

    def clean(self):
        super().clean()
        self.phone = normalize_phone(self.phone)
        if self.status == self.Status.BLOCKED:
            self.is_active = False
        if (
            self.account_type != self.AccountType.EMPLOYEE
            and not self._state.adding
            and Employee.objects.using(self._state.db).filter(
                account_id=self.pk,
            ).exists()
        ):
            raise ValidationError({
                "account_type": "An Employee profile requires an employee account.",
            })

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=database):
            if not self._state.adding:
                # Serialize account-type changes against Employee creation.
                type(self).objects.using(database).select_for_update().filter(
                    pk=self.pk,
                ).first()
            self.full_clean()
            if kwargs.get("update_fields") is not None:
                fields = set(kwargs["update_fields"])
                if "status" in fields and self.status == self.Status.BLOCKED:
                    fields.add("is_active")
                kwargs["update_fields"] = fields
            return super().save(*args, **kwargs)

    def __str__(self):
        return self.phone


class EmployeeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if {"account", "account_id"}.intersection(kwargs):
            raise ValueError(
                "Reassign Employee accounts using save() for type validation."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if {"account", "account_id"}.intersection(fields):
            raise ValueError(
                "Reassign Employee accounts using save() for type validation."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, **kwargs):
        raise ValueError(
            "Create Employee profiles using save()/create() for type validation."
        )


class Employee(models.Model):
    id = models.BigAutoField(primary_key=True)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee",
    )
    pickup_point = models.ForeignKey(
        "pickup_points.PickupPoint", on_delete=models.PROTECT, related_name="employees",
    )

    objects = EmployeeQuerySet.as_manager()

    class Meta:
        db_table = "employee"

    def clean(self):
        super().clean()
        if self.account_id:
            account = Account.objects.using(self._state.db).filter(
                pk=self.account_id,
            ).first()
            if account and account.account_type != Account.AccountType.EMPLOYEE:
                raise ValidationError({
                    "account": "Only employee accounts can have an Employee profile.",
                })

    def save(self, *args, **kwargs):
        database = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=database):
            if self.account_id:
                Account.objects.using(database).select_for_update().filter(
                    pk=self.account_id,
                ).first()
            self.full_clean()
            return super().save(*args, **kwargs)

    def __str__(self):
        return f"Employee {self.pk} (pickup point {self.pickup_point_id})"
