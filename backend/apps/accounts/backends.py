from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

from .models import Account
from .phone import normalize_phone


class AccountBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        phone = username if username is not None else kwargs.get(Account.USERNAME_FIELD)
        if phone is None or password is None:
            return None
        try:
            phone = normalize_phone(phone)
        except ValidationError:
            Account().set_password(password)
            return None
        return super().authenticate(request, username=phone, password=password)

    def user_can_authenticate(self, user):
        return user.is_active and user.status == Account.Status.ACTIVE
