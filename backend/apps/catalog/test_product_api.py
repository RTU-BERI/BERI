"""BERI-104-A/B public HTTP contracts on the canonical PostgreSQL schema."""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import (
    AttributeDefinition, CategoryAttribute, Product, ProductAttributeValue,
    ProductCategory, ProductComponent, ProductTariff,
)

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
CATEGORY_ERROR = {"category": [
    "Укажите целый ID категории от 1 до "
    "9223372036854775807 (1–19 ASCII-цифр)."
]}
LIST_FIELDS = {"id", "name", "category", "status", "image_url", "brand", "model"}
DETAIL_FIELDS = LIST_FIELDS | {
    "description", "attributes", "components", "tariff", "created_at", "updated_at",
}


def product(category, name="Audit drill"):
    return Product.objects.create(
        category=category, name=name, description="Synthetic HTTP fixture",
        status="opaque-fixture",
    )


def domain_snapshot():
    return {
        m._meta.db_table: list(m._base_manager.order_by("pk").values())
        for m in apps.get_models()
        if m._meta.app_label in {
            "accounts", "catalog", "inventory", "pickup_points",
            "reservations", "rentals", "payments",
        }
    }


class ProductListAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tools = ProductCategory.objects.create(name="Tools")
        cls.other = ProductCategory.objects.create(name="Other")
        cls.zulu = product(cls.tools, "Zulu drill")
        cls.alpha = product(cls.tools, "Alpha drill")
        cls.twin = product(cls.other, "Alpha drill")

    def setUp(self):
        self.url = reverse("product-list")

    def ids(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        return [row["id"] for row in response.json()]

    def test_public_list_exact_fields_and_unpaginated_shape(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        rows = response.json()
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(set(row), LIST_FIELDS)
            self.assertIsInstance(row["category"], int)
            self.assertEqual(row["status"], "opaque-fixture")

    def test_missing_category_keeps_all_products(self):
        self.assertEqual(self.ids(), [self.alpha.pk, self.twin.pk, self.zulu.pk])

    def test_empty_category_keeps_all_products(self):
        self.assertEqual(self.ids(category=""), self.ids())

    def test_existing_category(self):
        self.assertEqual(self.ids(category=str(self.tools.pk)), [self.alpha.pk, self.zulu.pk])

    def test_leading_zero_category_remains_compatible(self):
        self.assertEqual(self.ids(category=f"00{self.tools.pk}"), [self.alpha.pk, self.zulu.pk])

    def test_unknown_valid_category_returns_empty_array(self):
        self.assertEqual(self.ids(category="9223372036854775807"), [])

    def test_stable_name_then_id_order_with_duplicate_names(self):
        expected = [self.alpha.pk, self.twin.pk, self.zulu.pk]
        self.assertEqual(self.ids(), expected)
        self.assertEqual(self.ids(), expected)

    def test_search_is_case_insensitive(self):
        self.assertEqual(self.ids(search="aLpHa"), [self.alpha.pk, self.twin.pk])

    def test_search_trims_surrounding_whitespace(self):
        self.assertEqual(self.ids(search="  Zulu \t"), [self.zulu.pk])

    def test_blank_search_keeps_all_products(self):
        self.assertEqual(self.ids(search="   "), self.ids())

    def test_search_and_category_are_combined_with_and(self):
        self.assertEqual(self.ids(search="Alpha", category=str(self.other.pk)), [self.twin.pk])
        self.assertEqual(self.ids(search="Zulu", category=str(self.other.pk)), [])

    def test_search_without_matches_returns_empty_array(self):
        self.assertEqual(self.ids(search="no-such-product"), [])

    def test_search_special_characters_are_literal_not_query_syntax(self):
        self.assertEqual(self.ids(search="%' OR 1=1 --"), [])

    def test_empty_database_returns_empty_array(self):
        Product.objects.all().delete()
        self.assertEqual(self.ids(), [])

    def test_invalid_category_formats_return_documented_400(self):
        before = domain_snapshot()
        for value in (
            "abc", "1.2", "1.0", "-1", "0", "00", "+1", " 1", "1 ",
            "null", "NaN", "[]", "1 OR 1=1", "1e3", "١", "１", "1\n",
        ):
            with self.subTest(category=value):
                response = self.client.get(self.url, {"category": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), CATEGORY_ERROR)
        self.assertEqual(domain_snapshot(), before)

    def test_out_of_range_and_extremely_long_categories_return_400(self):
        for value in (str(2**63), "9" * 19, "9" * 20, "9" * 5000, "0" * 20 + "1"):
            with self.subTest(length=len(value)):
                response = self.client.get(self.url, {"category": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), CATEGORY_ERROR)

    def test_nullable_optional_fields_remain_null(self):
        row = self.client.get(self.url).json()[0]
        for field in ("image_url", "brand", "model"):
            self.assertIsNone(row[field])

    def test_unsupported_write_methods_do_not_change_rows(self):
        before = domain_snapshot()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.client.generic(
                    method, self.url, json.dumps({"name": "Forged"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 405)
        self.assertEqual(domain_snapshot(), before)

    def test_safe_methods_and_get_do_not_write(self):
        before = domain_snapshot()
        with CaptureQueriesContext(connection) as queries:
            for method in ("get", "head", "options"):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 200)
        self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE)", q["sql"], re.I) for q in queries))
        self.assertEqual(domain_snapshot(), before)


class ProductDetailAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="Detail tools")
        cls.item = product(cls.category)
        for code, fields in (
            ("zero", {"value_number": Decimal("0")}),
            ("false", {"value_boolean": False}),
            ("empty", {"value_text": ""}),
            ("text", {"value_text": "Metal"}),
        ):
            definition = AttributeDefinition.objects.create(code=code, name=code, data_type="opaque")
            assignment = CategoryAttribute.objects.create(
                category=cls.category, attribute_definition=definition,
                unit="W" if code == "zero" else None, is_filterable=False,
            )
            ProductAttributeValue.objects.create(
                product=cls.item, category_attribute=assignment, **fields,
            )
        ProductComponent.objects.create(product=cls.item, name="Case", quantity=2)

    def setUp(self):
        self.url = reverse("product-detail", args=[self.item.pk])

    def detail(self, moment=NOW):
        with patch("apps.catalog.serializers.timezone.now", return_value=moment):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def tariff(self, **fields):
        return ProductTariff.objects.create(product=self.item, **fields)

    def test_public_detail_exact_fields_and_nested_category(self):
        data = self.detail()
        self.assertEqual(set(data), DETAIL_FIELDS)
        self.assertEqual(data["category"], {"id": self.category.pk, "name": self.category.name})
        self.assertEqual(data["description"], "Synthetic HTTP fixture")
        self.assertIsInstance(data["created_at"], str)
        self.assertIsInstance(data["updated_at"], str)

    def test_values_preserve_zero_false_empty_text_and_null_slots(self):
        attrs = {value["name"]: value for value in self.detail()["attributes"]}
        self.assertEqual(set(attrs), {"zero", "false", "empty", "text"})
        self.assertEqual(attrs["zero"], {
            "name": "zero", "unit": "W", "value_text": None,
            "value_number": "0.000000", "value_boolean": None,
        })
        self.assertEqual(attrs["false"], {
            "name": "false", "unit": None, "value_text": None,
            "value_number": None, "value_boolean": False,
        })
        self.assertEqual(attrs["empty"]["value_text"], "")
        self.assertIsNone(attrs["empty"]["value_number"])
        self.assertIsNone(attrs["empty"]["value_boolean"])
        self.assertEqual(attrs["text"]["value_text"], "Metal")

    def test_components_and_nullable_optional_fields(self):
        data = self.detail()
        self.assertEqual(data["components"], [{"name": "Case", "quantity": 2}])
        for field in ("image_url", "brand", "model"):
            self.assertIsNone(data[field])

    def test_product_without_attributes_or_components_returns_empty_arrays(self):
        empty = product(self.category, "Empty detail")
        data = self.client.get(reverse("product-detail", args=[empty.pk])).json()
        self.assertEqual(data["attributes"], [])
        self.assertEqual(data["components"], [])

    def test_no_tariff_returns_null(self):
        self.assertIsNone(self.detail()["tariff"])

    def test_current_open_tariff_has_exact_decimal_and_fields(self):
        self.tariff(hourly_rate="125.50", valid_from=NOW-HOUR)
        tariff = self.detail()["tariff"]
        self.assertEqual(set(tariff), {"hourly_rate", "valid_from", "valid_to"})
        self.assertEqual(tariff["hourly_rate"], "125.50")
        self.assertIsNone(tariff["valid_to"])

    def test_current_bounded_tariff(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW-HOUR, valid_to=NOW+HOUR)
        self.assertEqual(self.detail()["tariff"]["hourly_rate"], "10.00")

    def test_future_tariff_returns_null(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW+HOUR)
        self.assertIsNone(self.detail()["tariff"])

    def test_expired_tariff_returns_null(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW-2*HOUR, valid_to=NOW-HOUR)
        self.assertIsNone(self.detail()["tariff"])

    def test_tariff_start_is_inclusive_and_instant_before_is_absent(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW)
        self.assertIsNone(self.detail(NOW-timedelta(microseconds=1))["tariff"])
        self.assertEqual(self.detail(NOW)["tariff"]["hourly_rate"], "10.00")

    def test_tariff_end_is_exclusive_and_instant_before_is_present(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW-HOUR, valid_to=NOW)
        self.assertEqual(self.detail(NOW-timedelta(microseconds=1))["tariff"]["hourly_rate"], "10.00")
        self.assertIsNone(self.detail(NOW)["tariff"])

    def test_adjacent_periods_select_new_rate_at_boundary(self):
        self.tariff(hourly_rate="10.00", valid_from=NOW-HOUR, valid_to=NOW)
        self.tariff(hourly_rate="20.00", valid_from=NOW)
        self.assertEqual(self.detail(NOW-timedelta(microseconds=1))["tariff"]["hourly_rate"], "10.00")
        self.assertEqual(self.detail(NOW)["tariff"]["hourly_rate"], "20.00")

    def test_missing_and_invalid_ids_return_404(self):
        for value in ("999999999", "abc", "0", "-1", str(2**63), "9"*100):
            with self.subTest(pk=value):
                self.assertEqual(self.client.get(f"/api/products/{value}/").status_code, 404)

    def test_unsupported_methods_do_not_modify_any_domain_rows(self):
        before = domain_snapshot()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.client.generic(
                    method, self.url, json.dumps({"status": "forged"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 405)
        self.assertEqual(domain_snapshot(), before)

    def test_safe_methods_and_get_do_not_write(self):
        before = domain_snapshot()
        with CaptureQueriesContext(connection) as queries:
            for method in ("get", "head", "options"):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 200)
        self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE)", q["sql"], re.I) for q in queries))
        self.assertEqual(domain_snapshot(), before)
