from django.urls import path, re_path

from .auth_api import CsrfView, LoginView, LogoutView, MeView, RegisterView, auth_not_found

urlpatterns = [
    path("auth/csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    re_path(r"^auth(?:/.*)?$", auth_not_found),
]
