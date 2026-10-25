"""Public category reads and the absence of API write operations."""

import json
import re

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import ProductCategory


class ProductCategoryAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.last = ProductCategory.objects.create(name="Zulu fixture")
        cls.first = ProductCategory.objects.create(name="Alpha fixture")
        cls.middle = ProductCategory.objects.create(name="Middle fixture")

    def setUp(self):
        self.list_url = reverse("category-list")
        self.detail_url = reverse("category-detail", args=[self.first.pk])

    def _snapshot(self):
        return list(ProductCategory.objects.order_by("pk").values("id", "name"))

    def _expected_list(self):
        return [
            {"id": category.pk, "name": category.name}
            for category in (self.first, self.middle, self.last)
        ]

    def test_anonymous_list_returns_only_id_and_name_in_name_order(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._expected_list())
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")

    def test_list_remains_unpaginated(self):
        response = self.client.get(self.list_url, {"page": 1, "page_size": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._expected_list())

    def test_anonymous_detail_returns_only_id_and_name(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"id": self.first.pk, "name": self.first.name}
        )

    def test_empty_dictionary_returns_an_empty_list(self):
        ProductCategory.objects.all().delete()

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_missing_detail_returns_404(self):
        missing_id = max(self.first.pk, self.middle.pk, self.last.pk) + 1

        response = self.client.get(reverse("category-detail", args=[missing_id]))

        self.assertEqual(response.status_code, 404)

    def test_invalid_detail_ids_return_404(self):
        for value in ("not-a-number", "-1", "0", "1 OR 1=1"):
            with self.subTest(value=value):
                response = self.client.get(reverse("category-detail", args=[value]))
                self.assertEqual(response.status_code, 404)

    def test_ids_outside_bigint_range_return_404(self):
        for value in (str(2**63), "9" * 100):
            with self.subTest(value=value):
                response = self.client.get(reverse("category-detail", args=[value]))
                self.assertEqual(response.status_code, 404)

    def test_list_rejects_every_write_method_without_changing_rows(self):
        before = self._snapshot()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.client.generic(
                    method,
                    self.list_url,
                    data=json.dumps({"name": "Attempted API write fixture"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(self._snapshot(), before)

    def test_detail_rejects_every_write_method_without_changing_rows(self):
        before = self._snapshot()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.client.generic(
                    method,
                    self.detail_url,
                    data=json.dumps({"name": "Attempted API rename fixture"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(self._snapshot(), before)

    def test_head_is_available_without_a_response_body(self):
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.head(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertEqual(
                    {method.strip() for method in response.headers["Allow"].split(",")},
                    {"GET", "HEAD", "OPTIONS"},
                )

    def test_options_advertises_only_read_methods(self):
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.options(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {method.strip() for method in response.headers["Allow"].split(",")},
                    {"GET", "HEAD", "OPTIONS"},
                )
                self.assertFalse(
                    {"POST", "PUT", "PATCH", "DELETE"}
                    & set(response.json().get("actions", {}))
                )

    def test_read_methods_do_not_write_database_rows(self):
        before = self._snapshot()
        with CaptureQueriesContext(connection) as queries:
            for method in ("get", "head", "options"):
                for url in (self.list_url, self.detail_url):
                    with self.subTest(method=method, url=url):
                        response = getattr(self.client, method)(url)
                        self.assertEqual(response.status_code, 200)

        self.assertEqual(self._snapshot(), before)
        self.assertFalse(
            any(
                re.match(r"\s*(INSERT|UPDATE|DELETE)\b", query["sql"], re.IGNORECASE)
                for query in queries.captured_queries
            ),
            "Public category reads must not issue database writes.",
        )

    def test_public_reads_ignore_authentication_headers(self):
        response = self.client.get(
            self.list_url,
            HTTP_AUTHORIZATION="Basic deliberately-invalid-synthetic-credentials",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._expected_list())
