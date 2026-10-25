"""Password and session authentication using the canonical Account manager.

The Account status is an opaque business value. Only the current is_active
flag controls authentication; roles and permissions are checked separately.
"""

from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

from .models import Account


class AccountBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(Account.USERNAME_FIELD)
        if username is None or not isinstance(password, str):
            return None
        try:
            account = Account.objects.get_by_natural_key(username)
        except Account.DoesNotExist:
            # Preserve Django's dummy hash for an unknown/invalid phone.
            Account().set_password(password)
            return None
        if not account.check_password(password):
            return None
        # Password verification can be slow. Re-read the identity and active
        # flag afterwards; a concurrent password/phone change denies this try.
        return Account.objects.filter(
            pk=account.pk,
            phone=account.phone,
            password=account.password,
            is_active=True,
        ).first()

    async def aauthenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(Account.USERNAME_FIELD)
        if username is None or not isinstance(password, str):
            return None
        try:
            account = await Account.objects.aget_by_natural_key(username)
        except Account.DoesNotExist:
            Account().set_password(password)
            return None
        if not await account.acheck_password(password):
            return None
        return await Account.objects.filter(
            pk=account.pk,
            phone=account.phone,
            password=account.password,
            is_active=True,
        ).afirst()

    def user_can_authenticate(self, user):
        if not getattr(user, "is_authenticated", False):
            return False
        try:
            return Account.objects.filter(pk=user.pk, is_active=True).exists()
        except (ValidationError, ValueError, TypeError, AttributeError):
            return False

    def get_user(self, user_id):
        try:
            return Account.objects.filter(pk=user_id, is_active=True).first()
        except (ValidationError, ValueError, TypeError):
            return None

    async def aget_user(self, user_id):
        try:
            return await Account.objects.filter(pk=user_id, is_active=True).afirst()
        except (ValidationError, ValueError, TypeError):
            return None
