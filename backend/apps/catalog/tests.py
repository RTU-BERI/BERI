from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import ProductCategory


class ProductCategoryTests(TestCase):
    """Проверяет получение категорий и ограничения их изменения."""
    def setUp(self):
        self.category = ProductCategory.objects.create(
            name="Инструменты"
        )

    def test_category_list(self):
        response = self.client.get(
            reverse("category-list")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": self.category.id,
                    "name": "Инструменты"
                }
            ]
        )

    def test_category_detail(self):
        response = self.client.get(
            reverse(
                "category-detail",
                args=[self.category.id]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["name"],
            "Инструменты"
        )

    def test_category_name_is_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductCategory.objects.create(
                    name="Инструменты"
                )

    def test_public_api_is_read_only(self):
        response = self.client.post(
            reverse("category-list"),
            {"name": "Развлечения"}
        )

        self.assertEqual(response.status_code, 405)
