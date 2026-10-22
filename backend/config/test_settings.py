"""Isolated SQLite checks; never connect to the developer's database."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-deployment-secret")

from .settings import *  # noqa: E402,F403

SECRET_KEY = "test-only-not-a-deployment-secret"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
