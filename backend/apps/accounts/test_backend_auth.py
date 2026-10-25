"""Password authentication and real sync/async session invalidation."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, aauthenticate, authenticate
from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse
from django.test import TestCase, override_settings
from django.urls import path

from .backends import AccountBackend
from .models import Account


def session_identity(request):
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=401)
    return JsonResponse({"id": str(user.pk), "phone": user.phone})


async def async_session_identity(request):
    user = await request.auser()
    if not user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=401)
    return JsonResponse({"id": str(user.pk), "phone": user.phone})


urlpatterns = [
    path("session/", session_identity),
    path("async-session/", async_session_identity),
]


@override_settings(
    ROOT_URLCONF=__name__,
    AUTHENTICATION_BACKENDS=["apps.accounts.backends.AccountBackend"],
)
class AccountBackendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-backend-password-729!"
        cls.account = Account.objects.create_user(
            phone="+12025550181", password=cls.password,
            account_type=Account.AccountType.CUSTOMER, status="opaque-fixture",
        )

    def test_username_and_phone_keyword_use_canonical_normalization(self):
        for credentials in (
            {"username": "+1 (202) 555-0181"},
            {"phone": "+1 202 555 0181"},
        ):
            with self.subTest(credentials=credentials):
                user = authenticate(password=self.password, **credentials)
                self.assertEqual(user.pk, self.account.pk)
                self.assertEqual(user.phone, self.account.phone)

    def test_invalid_unknown_implicit_country_and_unicode_phones_fail(self):
        for phone in ("2025550181", "12025550181", "+02025550181", "+１２３", "+12x", "+12025550999", 123):
            with self.subTest(phone=phone):
                self.assertIsNone(authenticate(username=phone, password=self.password))

    def test_missing_credentials_and_wrong_password_are_denied(self):
        for credentials in (
            {}, {"username": self.account.phone},
            {"password": self.password},
            {"username": self.account.phone, "password": "wrong-password"},
            {"username": self.account.phone, "password": 123},
        ):
            with self.subTest(credentials=credentials):
                self.assertIsNone(authenticate(**credentials))

    def test_opaque_status_does_not_disable_active_account_or_gain_privileges(self):
        for value in ("blocked", "retired", "any-business-value"):
            with self.subTest(value=value):
                Account.objects.filter(pk=self.account.pk).update(status=value)
                user = authenticate(username=self.account.phone, password=self.password)
                self.assertEqual(user.pk, self.account.pk)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
        self.assertFalse(hasattr(Account, "Status"))

    def test_fresh_inactive_flag_denies_password_and_stale_backend_identity(self):
        Account.objects.filter(pk=self.account.pk).update(is_active=False)
        self.assertTrue(self.account.is_active)
        self.assertIsNone(authenticate(username=self.account.phone, password=self.password))
        self.assertFalse(AccountBackend().user_can_authenticate(self.account))
        self.assertIsNone(AccountBackend().get_user(self.account.pk))

    def test_passwordless_account_cannot_authenticate(self):
        self.account.set_unusable_password()
        self.account.save(update_fields=["password"])
        self.assertIsNone(authenticate(username=self.account.phone, password=self.password))

    def test_deleted_and_malformed_session_identities_fail_closed(self):
        pk = self.account.pk
        Account.objects.filter(pk=pk).delete()
        backend = AccountBackend()
        for identifier in (pk, None, "invalid-uuid"):
            with self.subTest(identifier=identifier):
                self.assertIsNone(backend.get_user(identifier))
        for user in (self.account, AnonymousUser(), None, SimpleNamespace(is_authenticated=True, pk="invalid-uuid")):
            with self.subTest(user=user):
                self.assertFalse(backend.user_can_authenticate(user))

    def test_password_change_during_hash_check_denies_login(self):
        original = Account.check_password

        def change_after_check(instance, raw_password):
            result = original(instance, raw_password)
            current = Account.objects.get(pk=instance.pk)
            current.set_password("Another-synthetic-password-992!")
            current.save(update_fields=["password"])
            return result

        with patch.object(Account, "check_password", change_after_check):
            self.assertIsNone(authenticate(username=self.account.phone, password=self.password))

    def test_phone_change_during_hash_check_denies_login(self):
        original = Account.check_password

        def change_after_check(instance, raw_password):
            result = original(instance, raw_password)
            Account.objects.filter(pk=instance.pk).update(phone="+12025550182")
            return result

        with patch.object(Account, "check_password", change_after_check):
            self.assertIsNone(authenticate(username=self.account.phone, password=self.password))

    def test_blocking_during_hash_check_denies_login(self):
        original = Account.check_password

        def change_after_check(instance, raw_password):
            result = original(instance, raw_password)
            Account.objects.filter(pk=instance.pk).update(is_active=False)
            return result

        with patch.object(Account, "check_password", change_after_check):
            self.assertIsNone(authenticate(username=self.account.phone, password=self.password))

    def test_real_session_reads_current_phone_and_rejects_inactive_account(self):
        self.assertTrue(self.client.login(username=self.account.phone, password=self.password))
        Account.objects.filter(pk=self.account.pk).update(phone="+12025550182")
        response = self.client.get("/session/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phone"], "+12025550182")
        Account.objects.filter(pk=self.account.pk).update(is_active=False)
        self.assertEqual(self.client.get("/session/").status_code, 401)

    def test_real_session_rejects_deleted_account(self):
        self.client.force_login(self.account)
        Account.objects.filter(pk=self.account.pk).delete()
        self.assertEqual(self.client.get("/session/").status_code, 401)

    def test_real_session_password_change_invalidates_existing_hash(self):
        self.client.force_login(self.account)
        self.account.set_password("Changed-synthetic-password-553!")
        self.account.save(update_fields=["password"])
        self.assertEqual(self.client.get("/session/").status_code, 401)

    def test_legacy_default_backend_session_requires_relogin(self):
        self.client.force_login(self.account, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get("/session/").status_code, 401)
        self.assertTrue(self.client.login(username=self.account.phone, password=self.password))
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], "apps.accounts.backends.AccountBackend")
        self.assertEqual(self.client.session[SESSION_KEY], str(self.account.pk))
        self.assertEqual(self.client.get("/session/").status_code, 200)

    async def test_async_password_authentication_uses_same_normalization(self):
        for credentials in (
            {"username": "+1 (202) 555-0181"},
            {"phone": "+1 202 555 0181"},
        ):
            user = await aauthenticate(password=self.password, **credentials)
            self.assertEqual(user.pk, self.account.pk)

    async def test_async_authentication_rejects_invalid_wrong_and_unusable_password(self):
        self.assertIsNone(await aauthenticate(username="2025550181", password=self.password))
        self.assertIsNone(await aauthenticate(username=self.account.phone, password="wrong"))
        self.assertIsNone(await aauthenticate(username=self.account.phone, password=None))
        self.account.set_unusable_password()
        await self.account.asave(update_fields=["password"])
        self.assertIsNone(await aauthenticate(username=self.account.phone, password=self.password))

    async def test_async_inactive_and_deleted_session_lookup_fail_closed(self):
        backend = AccountBackend()
        self.assertEqual((await backend.aget_user(self.account.pk)).pk, self.account.pk)
        await Account.objects.filter(pk=self.account.pk).aupdate(is_active=False)
        self.assertIsNone(await aauthenticate(username=self.account.phone, password=self.password))
        self.assertIsNone(await backend.aget_user(self.account.pk))
        await Account.objects.filter(pk=self.account.pk).adelete()
        self.assertIsNone(await backend.aget_user(self.account.pk))
        self.assertIsNone(await backend.aget_user("not-a-uuid"))

    async def test_real_async_session_rejects_fresh_inactive_flag(self):
        self.assertTrue(await self.async_client.alogin(username=self.account.phone, password=self.password))
        response = await self.async_client.get("/async-session/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(self.account.pk))
        await Account.objects.filter(pk=self.account.pk).aupdate(is_active=False)
        self.assertEqual((await self.async_client.get("/async-session/")).status_code, 401)
