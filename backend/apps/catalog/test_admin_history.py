"""Admin history protection against existing synthetic operational records."""

from datetime import timedelta

from django.test import TestCase

from apps.rentals.test_support import FixtureMixin
from .models import ProductComponent, ProductTariff
from .test_product_admin import admin_url


class CatalogHistoryAdminTests(FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.actor.is_staff = self.actor.is_superuser = True
        self.actor.save()
        self.client.force_login(self.actor)

    def test_inspected_components_are_read_only_and_cannot_be_deleted(self):
        self.make_inspection()
        url = admin_url(ProductComponent, "change", self.component)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.post(url, {"name": "Forged", "quantity": "9"}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductComponent, "delete", self.component), {"post": "yes"}
            ).status_code,
            403,
        )
        self.component.refresh_from_db()
        self.assertEqual(self.component.quantity, 2)

    def test_new_component_after_inspection_is_form_error_not_server_error(self):
        self.make_inspection()
        before = ProductComponent.objects.count()
        response = self.client.post(
            admin_url(ProductComponent, "add"),
            dict(product=self.product.pk, name="Extra", quantity="1", _save="Save"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.non_field_errors())
        self.assertEqual(ProductComponent.objects.count(), before)

    def test_tariff_close_cannot_exclude_existing_rental_start(self):
        rental = self.make_rental()
        end = rental.started_at - timedelta(hours=1)
        response = self.client.post(
            admin_url(ProductTariff, "change", self.tariff),
            dict(
                valid_to_0=end.strftime("%Y-%m-%d"),
                valid_to_1=end.strftime("%H:%M:%S"),
                _save="Save",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("valid_to", response.context["adminform"].form.errors)
        self.tariff.refresh_from_db()
        rental.refresh_from_db()
        self.assertIsNone(self.tariff.valid_to)
        self.assertEqual(rental.hourly_rate_applied, self.tariff.hourly_rate)

    def test_closing_after_rental_start_preserves_historical_rate(self):
        rental = self.make_rental()
        end = rental.started_at + timedelta(hours=1)
        response = self.client.post(
            admin_url(ProductTariff, "change", self.tariff),
            dict(
                valid_to_0=end.strftime("%Y-%m-%d"),
                valid_to_1=end.strftime("%H:%M:%S"),
                _save="Save",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.tariff.refresh_from_db()
        rental.refresh_from_db()
        self.assertIsNotNone(self.tariff.valid_to)
        self.assertEqual(rental.hourly_rate_applied, self.tariff.hourly_rate)
