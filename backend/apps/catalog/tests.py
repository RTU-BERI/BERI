
# Инструменты для тестирования базы данных
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

# Импортируем модель категорий товаров
from .models import ProductCategory


# Создаём набор тестов для категорий
class ProductCategoryTests(TestCase):

    # Перед каждым тестом создаём категорию
    def setUp(self):
        self.category = ProductCategory.objects.create(
            name="Инструменты"
        )

    # Тест 1. Проверяем получение списка категорий
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

    # Тест 2. Проверяем получение одной категории
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

    # Тест 3. Проверяем запрет одинаковых названий
    def test_category_name_is_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductCategory.objects.create(
                    name="Инструменты"
                )

    # Тест 4. Проверяем запрет создания через API
    def test_public_api_is_read_only(self):
        response = self.client.post(
            reverse("category-list"),
            {"name": "Развлечения"}
        )

        self.assertEqual(response.status_code, 405)
