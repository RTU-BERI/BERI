"""Real CSRF/session contracts on PostgreSQL, including a UNIQUE race."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.core.cache import caches
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Account


PASSWORD = "Synthetic-local-passphrase9!Guitar"
LOCAL = dict(DEBUG=True, ALLOWED_HOSTS=["localhost", "127.0.0.1"],
             BERI_DEMO_REGISTRATION_ENABLED=True, BERI_LOCAL_BIND_CONFIRMED=True,
             SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
             PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


def browser(ip="127.0.0.1"):
    return Client(enforce_csrf_checks=True, HTTP_HOST="localhost", SERVER_NAME="localhost", REMOTE_ADDR=ip)


def post(client, endpoint, data, **extra):
    client.get("/api/auth/csrf/")
    return client.post("/api/auth/" + endpoint + "/", data, content_type="application/json",
                       HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value, **extra)


@override_settings(**LOCAL)
class AuthHTTPTests(TestCase):
    def setUp(self):
        caches["auth_demo"].clear()
        self.client = browser()
        self.account = Account.objects.create_user(phone="+12025550101", password=PASSWORD,
                                                  account_type="customer", status="opaque")
        self.credentials = {"phone": self.account.phone, "password": PASSWORD}
        self.registration = {"phone": "+1 (202) 555-0103", "password": PASSWORD, "password_confirm": PASSWORD}

    def test_csrf_bootstrap_and_anonymous_me_exact_shapes(self):
        response = self.client.get("/api/auth/csrf/")
        self.assertEqual(response.json(), {"detail": "csrf_ready"})
        self.assertIn("csrftoken", response.cookies)
        self.assertFalse(response.cookies["csrftoken"]["httponly"])
        self.assertEqual(self.client.get("/api/auth/me/").json(), {"authenticated": False, "user": None})

    def test_registration_customer_only_normalized_unverified_no_session(self):
        response = post(self.client, "register", self.registration)
        self.assertEqual(response.status_code, 201)
        account = Account.objects.get(phone="+12025550103")
        self.assertEqual(account.account_type, "customer")
        self.assertEqual(account.status, "registered")
        self.assertTrue(account.is_active)
        self.assertFalse(account.is_staff or account.is_superuser)
        self.assertIsNone(account.phone_verified_at)
        self.assertFalse(account.groups.exists() or account.user_permissions.exists())
        self.assertTrue(account.check_password(PASSWORD))
        self.assertNotEqual(account.password, PASSWORD)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(set(response.json()), {"user"})
        self.assertEqual(set(response.json()["user"]), {"id", "phone", "account_type", "phone_verified"})
        self.assertFalse(response.json()["user"]["phone_verified"])

    def test_service_and_unknown_registration_fields_are_rejected(self):
        for key, value in (("account_type", "employee"), ("is_staff", True), ("is_superuser", True),
                           ("groups", []), ("user_permissions", []), ("status", "active"),
                           ("phone_verified_at", "2026-01-01"), ("unrelated", "value")):
            caches["auth_demo"].clear()
            with self.subTest(key=key):
                response = post(self.client, "register", {**self.registration, key: value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(Account.objects.count(), 1)

    def test_registration_validation_password_confirm_phone_and_types(self):
        for update in ({"password_confirm": "different"}, {"phone": "8999"}, {"phone": 123},
                       {"password": "short", "password_confirm": "short"},
                       {"password": "password", "password_confirm": "password"}):
            caches["auth_demo"].clear()
            with self.subTest(fields=list(update)):
                self.assertEqual(post(self.client, "register", {**self.registration, **update}).status_code, 400)
        self.assertEqual(Account.objects.count(), 1)

    def test_registration_duplicate_canonical_phone_is_409(self):
        response = post(self.client, "register", {**self.registration, "phone": "+1 (202) 555-0101"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "phone_taken")

    def test_demo_requires_all_flags_and_safe_server_configuration(self):
        for change in ({"BERI_DEMO_REGISTRATION_ENABLED": False}, {"DEBUG": False},
                       {"BERI_LOCAL_BIND_CONFIRMED": False}, {"ALLOWED_HOSTS": ["*"]},
                       {"ALLOWED_HOSTS": ["localhost", "example.test"]}, {"ALLOWED_HOSTS": []}):
            caches["auth_demo"].clear()
            with self.subTest(change=change), override_settings(**change):
                response = post(self.client, "register", self.registration)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "registration_unavailable")
        self.assertEqual(Account.objects.count(), 1)

    def test_fake_host_or_forwarded_ip_does_not_prove_loopback(self):
        response = post(browser("203.0.113.10"), "register", self.registration, HTTP_X_FORWARDED_FOR="127.0.0.1")
        self.assertEqual(response.status_code, 503)

    def test_anonymous_unsafe_requests_require_real_csrf(self):
        for endpoint in ("register", "login", "logout"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post("/api/auth/" + endpoint + "/", {}, content_type="application/json")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "csrf_failed")

    def test_disabled_registration_still_checks_csrf_and_json(self):
        with override_settings(BERI_DEMO_REGISTRATION_ENABLED=False):
            self.assertEqual(self.client.post("/api/auth/register/", {}, content_type="application/json").status_code, 403)
            self.client.get("/api/auth/csrf/")
            response = self.client.post("/api/auth/register/", "{broken", content_type="application/json",
                                        HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
            self.assertEqual(response.status_code, 400)

    def test_bad_origin_and_bad_token_are_denied(self):
        self.assertEqual(post(self.client, "login", self.credentials, HTTP_ORIGIN="https://evil.example").status_code, 403)
        response = self.client.post("/api/auth/login/", self.credentials, content_type="application/json", HTTP_X_CSRFTOKEN="bad")
        self.assertEqual(response.status_code, 403)

    def test_login_me_logout_and_csrf_rotation(self):
        self.client.get("/api/auth/csrf/")
        before = self.client.cookies["csrftoken"].value
        response = self.client.post("/api/auth/login/", self.credentials, content_type="application/json", HTTP_X_CSRFTOKEN=before)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        self.assertEqual(response.json(), self.client.get("/api/auth/me/").json())
        self.assertNotEqual(before, self.client.cookies["csrftoken"].value)
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertEqual(response.cookies["sessionid"]["samesite"], "Lax")
        self.assertEqual(self.client.post("/api/auth/logout/", {}, content_type="application/json", HTTP_X_CSRFTOKEN=before).status_code, 403)
        response = post(self.client, "logout", {})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertFalse(self.client.get("/api/auth/me/").json()["authenticated"])
        self.assertEqual(post(self.client, "logout", {}).status_code, 204)

    def test_login_unknown_inactive_and_bad_password_have_same_response(self):
        responses = [post(self.client, "login", {**self.credentials, "password": "wrong"}),
                     post(self.client, "login", {**self.credentials, "phone": "+12025550109"}),
                     post(self.client, "login", {**self.credentials, "phone": "invalid"})]
        Account.objects.filter(pk=self.account.pk).update(is_active=False)
        responses.append(post(self.client, "login", self.credentials))
        self.assertTrue(all(r.status_code == 401 for r in responses))
        self.assertTrue(all(r.json() == responses[0].json() for r in responses))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_unverified_customer_denied_outside_demo_after_password_check(self):
        with override_settings(BERI_DEMO_REGISTRATION_ENABLED=False, DEBUG=False):
            response = post(self.client, "login", self.credentials)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "phone_unverified")
            self.assertNotIn(SESSION_KEY, self.client.session)
            response = post(self.client, "login", {**self.credentials, "password": "wrong"})
            self.assertEqual(response.status_code, 401)

    def test_verified_customer_and_existing_employee_can_login_without_demo(self):
        self.account.phone_verified_at = timezone.now()
        self.account.save(update_fields=["phone_verified_at"])
        worker = Account.objects.create_user(phone="+12025550102", password=PASSWORD, account_type="employee", status="arbitrary")
        with override_settings(BERI_DEMO_REGISTRATION_ENABLED=False, DEBUG=False):
            self.assertEqual(post(self.client, "login", self.credentials).status_code, 200)
            response = post(browser(), "login", {"phone": worker.phone, "password": PASSWORD})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["account_type"], "employee")

    def test_status_is_opaque_and_no_basic_auth_is_accepted(self):
        self.account.status = "blocked"
        self.account.save(update_fields=["status"])
        self.assertEqual(post(self.client, "login", self.credentials).status_code, 200)
        import base64
        basic = base64.b64encode((self.account.phone + ":" + PASSWORD).encode()).decode()
        response = browser().get("/api/auth/me/", HTTP_AUTHORIZATION="Basic " + basic)
        self.assertFalse(response.json()["authenticated"])

    def test_session_cannot_keep_access_after_deactivation_or_password_change(self):
        for field in ("is_active", "password"):
            with self.subTest(field=field):
                caches["auth_demo"].clear()
                self.account.is_active = True
                self.account.set_password(PASSWORD)
                self.account.save()
                client = browser()
                self.assertEqual(post(client, "login", self.credentials).status_code, 200)
                if field == "is_active":
                    Account.objects.filter(pk=self.account.pk).update(is_active=False)
                else:
                    self.account.set_password("Changed-synthetic-password9!")
                    self.account.save(update_fields=["password"])
                self.assertFalse(client.get("/api/auth/me/").json()["authenticated"])

    def test_logout_only_invalidates_current_device_session(self):
        other = browser()
        self.assertEqual(post(self.client, "login", self.credentials).status_code, 200)
        self.assertEqual(post(other, "login", self.credentials).status_code, 200)
        post(self.client, "logout", {})
        self.assertTrue(other.get("/api/auth/me/").json()["authenticated"])

    def test_exact_methods_and_slashes_are_local_json_errors(self):
        for url, method in (("csrf", "post"), ("me", "post"), ("login", "get"), ("register", "get"), ("logout", "get")):
            response = getattr(self.client, method)("/api/auth/" + url + "/")
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
        for url in ("/api/auth/me", "/api/auth/unknown/", "/api/auth"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_gets_do_not_mutate_accounts_and_do_not_expose_secrets(self):
        before = list(Account.objects.values())
        for url in ("/api/auth/csrf/", "/api/auth/me/"):
            response = self.client.get(url)
            self.assertNotIn(PASSWORD, response.content.decode())
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(before, list(Account.objects.values()))
        self.assertEqual(self.client.get("/api/categories/").status_code, 200)

    def test_login_ip_limit_counts_failed_attempts_and_expires_at_minute(self):
        with patch("apps.accounts.auth_api.time.time", return_value=1800000000) as clock:
            for _ in range(10):
                self.assertEqual(post(self.client, "login", {**self.credentials, "password": "wrong"}).status_code, 401)
            response = post(self.client, "login", self.credentials)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response["Retry-After"], "60")
            clock.return_value += 60
            # IP window expired, but the canonical phone's hour window remains.
            response = post(self.client, "login", self.credentials)
            self.assertEqual(response.status_code, 429)
            clock.return_value += 3540
            self.assertEqual(post(self.client, "login", self.credentials).status_code, 200)

    def test_phone_limit_normalizes_variants_across_different_ips(self):
        for i in range(10):
            client = browser("127.0.0." + str(i + 1))
            self.assertEqual(post(client, "login", {"phone": "+1 (202) 555-0101", "password": "wrong"}).status_code, 401)
        response = post(browser("127.0.0.20"), "login", self.credentials)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_registration_ip_limit_counts_validation_errors_and_expires(self):
        with patch("apps.accounts.auth_api.time.time", return_value=1800000000) as clock:
            for _ in range(3):
                self.assertEqual(post(self.client, "register", {}).status_code, 400)
            response = post(self.client, "register", self.registration)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response["Retry-After"], "3600")
            clock.return_value += 3600
            self.assertEqual(post(self.client, "register", self.registration).status_code, 201)

    def test_malformed_json_arrays_and_unknown_login_fields(self):
        for data in (["not", "object"], {**self.credentials, "is_staff": True}, {"phone": self.account.phone, "password": 123}):
            self.assertEqual(post(self.client, "login", data).status_code, 400)

    def test_login_success_counts_towards_the_limit(self):
        for _ in range(10):
            self.assertEqual(post(self.client, "login", self.credentials).status_code, 200)
        self.assertEqual(post(self.client, "login", self.credentials).status_code, 429)

    def test_response_user_never_exposes_internal_flags_or_password(self):
        data = post(self.client, "login", self.credentials).json()
        self.assertEqual(set(data), {"authenticated", "user"})
        self.assertEqual(set(data["user"]), {"id", "phone", "account_type", "phone_verified"})
        self.assertNotIn(PASSWORD, str(data))

    def test_local_launcher_rejects_public_binding_without_starting_server(self):
        from django.core.management.base import CommandError
        from .management.commands.run_local_demo import Command
        command = Command()
        command.addr = "0.0.0.0"
        with self.assertRaises(CommandError):
            command.inner_run()

    def test_local_launcher_rejects_nonlocal_configuration(self):
        from django.core.management.base import CommandError
        from .management.commands.run_local_demo import Command
        command = Command()
        command.addr = "127.0.0.1"
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            command.inner_run()

    def test_json_response_and_strict_head_options_without_browsable_api(self):
        response = self.client.get("/api/auth/me/", HTTP_ACCEPT="text/html")
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"authenticated": False, "user": None})
        for verb in ("head", "options"):
            self.assertEqual(getattr(self.client, verb)("/api/auth/me/").status_code, 405)

    def test_logout_checks_json_even_without_an_authenticated_session(self):
        self.client.get("/api/auth/csrf/")
        response = self.client.post("/api/auth/logout/", "{broken", content_type="application/json",
                                    HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_untrusted_host_is_rejected_even_on_csrf_get(self):
        for url in ("/api/auth/csrf/", "/api/auth/me/"):
            self.assertEqual(self.client.get(url, HTTP_HOST="attacker.example").status_code, 400)


@override_settings(**LOCAL)
class RegistrationRaceTests(TransactionTestCase):
    def test_same_canonical_phone_race_has_201_and_409_without_500(self):
        caches["auth_demo"].clear()
        barrier = Barrier(2)
        original_filter = Account.objects.filter
        phone = "+12025550108"

        def synchronize(**kwargs):
            queryset = original_filter(**kwargs)
            if kwargs == {"phone": phone}:
                original_exists = queryset.exists

                def exists():
                    answer = original_exists()
                    barrier.wait(timeout=10)
                    return answer

                queryset.exists = exists
            return queryset

        def attempt(raw):
            close_old_connections()
            try:
                return post(browser(), "register", {"phone": raw, "password": PASSWORD, "password_confirm": PASSWORD}).status_code
            finally:
                connection.close()

        with patch.object(Account.objects, "filter", side_effect=synchronize), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, raw) for raw in (phone, "+1 (202) 555-0108")]
            self.assertEqual(sorted(f.result(timeout=20) for f in futures), [201, 409])
        self.assertEqual(Account.objects.filter(phone=phone).count(), 1)
