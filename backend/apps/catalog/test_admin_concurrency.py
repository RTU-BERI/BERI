"""Real PostgreSQL exclusion race through two independent Admin sessions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase

from apps.accounts.models import Account
from .models import Product, ProductCategory, ProductTariff
from .test_product_admin import admin_url


class TariffAdminConcurrencyTests(TransactionTestCase):
    def test_overlapping_admin_posts_have_one_commit_and_one_form_error(self):
        self.assertEqual(connection.vendor, "postgresql")
        category = ProductCategory.objects.create(name="Concurrency")
        product = Product.objects.create(
            category=category,
            name="Race product",
            description="Synthetic",
            status="fixture",
        )
        actor = Account.objects.create_user(
            phone="+12025550124",
            password=None,
            account_type="customer",
            status="fixture",
        )
        actor.is_staff = actor.is_superuser = True
        actor.save()
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(actor)
        data = dict(
            product=product.pk,
            hourly_rate="20.00",
            valid_from_0="2026-10-01",
            valid_from_1="12:00:00",
            valid_to_0="",
            valid_to_1="",
            _save="Save",
        )
        barrier = Barrier(2)
        original = ProductTariff.save

        def synchronized_save(instance, *args, **kwargs):
            barrier.wait(timeout=10)
            return original(instance, *args, **kwargs)

        def post(client):
            close_old_connections()
            try:
                response = client.post(admin_url(ProductTariff, "add"), data)
                if response.status_code == 200:
                    self.assertTrue(response.context["adminform"].form.errors)
                return response.status_code
            finally:
                connection.close()

        with patch.object(ProductTariff, "save", synchronized_save), ThreadPoolExecutor(
            max_workers=2
        ) as pool:
            futures = [pool.submit(post, client) for client in clients]
            self.assertEqual(sorted(f.result(timeout=30) for f in futures), [200, 302])
        self.assertEqual(ProductTariff.objects.count(), 1)
