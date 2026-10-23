"""Managers for the phone-based Account model."""

from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError

from .validators import normalize_phone


class AccountManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, account_type, status, **extra_fields):
        if phone is None or phone == "":
            raise ValueError("A phone number is required.")
        if account_type is None or account_type == "":
            raise ValueError("An account type is required.")
        if status is None or status == "" or (
            isinstance(status, str) and not status.strip()
        ):
            raise ValueError("An account status is required.")

        user = self.model(
            phone=normalize_phone(phone),
            account_type=account_type,
            status=status,
            **extra_fields,
        )
        if password is None:
            # A deliberate password-less account cannot authenticate by password.
            user.set_unusable_password()
        else:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self,
        phone=None,
        password=None,
        *,
        account_type=None,
        status=None,
        **extra_fields,
    ):
        """Create an ordinary account; None gives it an unusable password."""
        if extra_fields.get("is_staff") or extra_fields.get("is_superuser"):
            raise ValueError("create_user cannot grant staff or superuser access.")
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(
            phone, password, account_type, status, **extra_fields
        )

    def create_superuser(
        self,
        phone=None,
        password=None,
        *,
        account_type=None,
        status=None,
        **extra_fields,
    ):
        """Create a Django administrator without a third account category."""
        if not isinstance(password, str) or not password:
            raise ValueError("A superuser requires a non-empty password.")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(
            phone, password, account_type, status, **extra_fields
        )

    def get_by_natural_key(self, username):
        try:
            phone = normalize_phone(username)
        except ValidationError:
            raise self.model.DoesNotExist("Invalid phone identifier.") from None
        return self.get(**{self.model.USERNAME_FIELD: phone})

    async def aget_by_natural_key(self, username):
        try:
            phone = normalize_phone(username)
        except ValidationError:
            raise self.model.DoesNotExist("Invalid phone identifier.") from None
        return await self.aget(**{self.model.USERNAME_FIELD: phone})
