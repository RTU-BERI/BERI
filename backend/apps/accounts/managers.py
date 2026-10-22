from django.contrib.auth.base_user import BaseUserManager
from django.db import models

from .phone import normalize_phone


class AccountQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "account_type" in kwargs:
            raise ValueError(
                "Change account_type using Account.save() for Employee validation."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if "account_type" in fields:
            raise ValueError(
                "Change account_type using Account.save() for Employee validation."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self, objs, batch_size=None, ignore_conflicts=False,
        update_conflicts=False, update_fields=None, unique_fields=None,
    ):
        if update_conflicts and "account_type" in (update_fields or ()):
            raise ValueError(
                "Change account_type using Account.save() for Employee validation."
            )
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields,
            unique_fields=unique_fields,
        )


class AccountManager(BaseUserManager.from_queryset(AccountQuerySet)):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra_fields):
        if not phone:
            raise ValueError("A phone number is required.")
        if not password:
            raise ValueError("A password is required.")
        account = self.model(phone=normalize_phone(phone), **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account

    def create_user(self, phone, password=None, **extra_fields):
        if extra_fields.get("is_staff") or extra_fields.get("is_superuser"):
            raise ValueError("create_user cannot grant administrative flags.")
        extra_fields["is_staff"] = False
        extra_fields["is_superuser"] = False
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password=None, **extra_fields):
        for name in ("account_type", "status"):
            if not extra_fields.get(name):
                raise ValueError(f"Superuser {name} must be selected explicitly.")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(phone, password, **extra_fields)
