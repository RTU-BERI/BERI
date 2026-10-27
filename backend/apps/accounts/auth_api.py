"""S1-P4 session HTTP contract. No model, role or verification-policy changes."""

import ipaddress
import math
from threading import RLock
import time

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import caches
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers
from rest_framework.authentication import CSRFCheck, SessionAuthentication
from rest_framework.exceptions import APIException, MethodNotAllowed, ParseError, UnsupportedMediaType, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import get_active_account
from .models import Account
from .validators import normalize_phone


_RATE_LOCK = RLock()
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def _loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except (ValueError, TypeError):
        return False


def demo_available(request):
    """Bind confirmation is set by our actual loopback server, never HTTP/env."""
    hosts = set(settings.ALLOWED_HOSTS)
    return bool(
        settings.BERI_DEMO_REGISTRATION_ENABLED
        and settings.DEBUG
        and settings.BERI_LOCAL_BIND_CONFIRMED
        and hosts and hosts <= _LOOPBACK_HOSTS
        and _loopback(request.META.get("REMOTE_ADDR"))
    )


class ContractError(APIException):
    def __init__(self, code, message, status=400, fields=None, retry_after=None):
        self.status_code = status
        self.payload = {"error": {"code": code, "message": message}}
        if fields:
            self.payload["error"]["fields"] = fields
        self.retry_after = retry_after
        super().__init__(message, code)


def rate_limit(request, kind, count, seconds, identity=None):
    """One-process demo limiter. Shared counters/edge protection are deferred."""
    identity = request.META.get("REMOTE_ADDR", "unknown") if identity is None else identity
    digest = salted_hmac("beri.s1p4." + kind, str(identity), algorithm="sha256").hexdigest()
    key = "auth:" + kind + ":" + digest
    now = time.time()
    cache = caches["auth_demo"]
    with _RATE_LOCK:
        history = [stamp for stamp in cache.get(key, []) if stamp > now - seconds]
        if len(history) >= count:
            raise ContractError("rate_limited", "Слишком много попыток. Попробуйте позже.", 429,
                                retry_after=max(1, math.ceil(history[0] + seconds - now)))
        cache.set(key, [*history, now], timeout=seconds)


class StrictString(serializers.CharField):
    def to_internal_value(self, value):
        if not isinstance(value, str):
            self.fail("invalid")
        return super().to_internal_value(value)


class Credentials(serializers.Serializer):
    phone = StrictString(trim_whitespace=False)
    password = StrictString(trim_whitespace=False, write_only=True)

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise ValidationError({"non_field_errors": ["invalid_object"]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise ValidationError({"non_field_errors": ["unknown_fields"]})
        return super().to_internal_value(data)


class Registration(Credentials):
    password_confirm = StrictString(trim_whitespace=False, write_only=True)

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except DjangoValidationError:
            raise ValidationError("invalid_phone", code="invalid_phone") from None

    @sensitive_variables("attrs", "candidate")
    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise ValidationError({"password_confirm": ["password_mismatch"]})
        candidate = Account(phone=attrs["phone"], account_type="customer", status="registered")
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as error:
            raise ValidationError({"password": [e.code or "invalid_password" for e in error.error_list]}) from None
        return attrs


def public_user(account):
    return {"id": str(account.pk), "phone": account.phone, "account_type": account.account_type,
            "phone_verified": account.phone_verified_at is not None}


def _field_codes(detail):
    if isinstance(detail, dict):
        return {key: _field_codes(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [_field_codes(value) for value in detail]
    code = getattr(detail, "code", None)
    return str(detail) if code in (None, "invalid") else code


class AuthAPIView(APIView):
    authentication_classes = (SessionAuthentication,)
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)
    rate_kind = None

    def perform_content_negotiation(self, request, force=False):
        # The auth contract is JSON even for clients asking for HTML.
        return JSONRenderer(), "application/json"

    def initial(self, request, *args, **kwargs):
        request.get_host()  # enforce ALLOWED_HOSTS on reads as well as CSRF POSTs
        if request.method == "POST" and "post" in self.http_method_names:
            # DRF SessionAuthentication alone skips CSRF for anonymous callers.
            check = CSRFCheck(lambda req: None)
            check.process_request(request._request)
            reason = check.process_view(request._request, lambda req: None, (), {})
            if reason:
                raise ContractError("csrf_failed", "Обновите страницу и повторите запрос.", 403)
            if self.rate_kind:
                limit, window = (10, 60) if self.rate_kind == "login" else (3, 3600)
                rate_limit(request, self.rate_kind + "_ip", limit, window)
        super().initial(request, *args, **kwargs)
        if request.method == "POST" and "post" in self.http_method_names:
            # Logout must not silently accept malformed/non-JSON bodies either.
            _ = request.data

    def handle_exception(self, exc):
        if isinstance(exc, ContractError):
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
            return Response(exc.payload, status=exc.status_code, headers=headers)
        if isinstance(exc, ValidationError):
            return Response({"error": {"code": "validation_error", "message": "Проверьте введённые данные",
                                       "fields": _field_codes(exc.detail)}}, status=400)
        if isinstance(exc, (ParseError, UnsupportedMediaType)):
            return Response({"error": {"code": "validation_error", "message": "Ожидается JSON-объект"}}, status=400)
        if isinstance(exc, MethodNotAllowed):
            return Response({"error": {"code": "method_not_allowed", "message": "Метод не поддерживается"}}, status=405)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class CsrfView(AuthAPIView):
    http_method_names = ["get"]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "csrf_ready"})


class RegisterView(AuthAPIView):
    http_method_names = ["post"]
    rate_kind = "register"

    @sensitive_variables("request", "serializer", "data", "account")
    def post(self, request):
        # Parse JSON even when disabled; CSRF has already run before this gate.
        data = request.data
        if not demo_available(request):
            raise ContractError("registration_unavailable", "Демонстрационная регистрация недоступна.", 503)
        serializer = Registration(data=data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if Account.objects.filter(phone=data["phone"]).exists():
            raise ContractError("phone_taken", "Этот номер уже зарегистрирован.", 409)
        try:
            with transaction.atomic():
                account = Account.objects.create_user(
                    phone=data["phone"], password=data["password"], account_type="customer",
                    status="registered", is_active=True, is_staff=False, is_superuser=False,
                    phone_verified_at=None,
                )
        except IntegrityError as error:
            cause = error.__cause__
            if (getattr(cause, "sqlstate", None) == "23505"
                    and getattr(getattr(cause, "diag", None), "constraint_name", None) == "account_phone_key"):
                raise ContractError("phone_taken", "Этот номер уже зарегистрирован.", 409) from None
            raise
        return Response({"user": public_user(account)}, status=201)


class LoginView(AuthAPIView):
    http_method_names = ["post"]
    rate_kind = "login"

    @sensitive_variables("request", "serializer", "data", "account", "phone")
    def post(self, request):
        serializer = Credentials(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            phone = normalize_phone(data["phone"])
        except DjangoValidationError:
            phone = "invalid"
        rate_limit(request, "login_phone", 10, 3600, identity=phone)
        account = authenticate(request=request._request, phone=phone, password=data["password"])
        if account is None:
            raise ContractError("invalid_credentials", "Неверный номер телефона или пароль.", 401)
        if account.account_type == "customer" and account.phone_verified_at is None and not demo_available(request):
            raise ContractError("phone_unverified", "Подтверждение телефона пока недоступно.", 403)
        login(request._request, account)
        return Response({"authenticated": True, "user": public_user(account)})


class LogoutView(AuthAPIView):
    http_method_names = ["post"]

    def post(self, request):
        logout(request._request)
        return Response(status=204)


class MeView(AuthAPIView):
    http_method_names = ["get"]

    def get(self, request):
        account = get_active_account(request.user)
        return Response({"authenticated": account is not None, "user": public_user(account) if account else None})


@csrf_exempt
def auth_not_found(request):
    return JsonResponse({"error": {"code": "not_found", "message": "Адрес не найден"}}, status=404,
                        headers={"Cache-Control": "no-store"})
