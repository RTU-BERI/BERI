"""Explicit one-process local demo launcher; never a production server."""
import ipaddress

from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import Command as Runserver
from django.core.management.base import CommandError
from django.core.servers.basehttp import WSGIServer


class LocalServer(WSGIServer):
    def server_bind(self):
        super().server_bind()
        if not ipaddress.ip_address(self.socket.getsockname()[0]).is_loopback:
            raise CommandError("The auth demo must bind a loopback address.")
        settings.BERI_LOCAL_BIND_CONFIRMED = True


class Command(Runserver):
    help = "Serve the local auth demo on loopback only, without production guarantees."
    server_cls = LocalServer

    def inner_run(self, *args, **options):
        if self.addr not in {"127.0.0.1", "::1"}:
            raise CommandError("Use run_local_demo 127.0.0.1:8000 (never 0.0.0.0).")
        if not settings.DEBUG or not settings.ALLOWED_HOSTS or not set(settings.ALLOWED_HOSTS) <= {"localhost", "127.0.0.1", "[::1]"}:
            raise CommandError("The demo requires DEBUG and loopback-only ALLOWED_HOSTS.")
        return super().inner_run(*args, **options)
