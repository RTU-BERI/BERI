
from decimal import Decimal

from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import Product, ProductCategory
from apps.pickup_points.models import PickupPoint

from .models import ProductItem
from .services import create_item


class ProductAvailabilityAPITests(APITestCase):
    """Тесты публичного API наличия товара по ПВ."""

    @classmethod
    def setUpTestData(cls):
        category = ProductCategory.objects.create(name="Тестовая категория")

        cls.product = Product.objects.create(
            category=category,
            name="Перфоратор",
            description="Тестовый товар",
            status="test-status",
        )
        cls.other_product = Product.objects.create(
            category=category,
            name="Дрель",
            description="Другой товар",
            status="test-status",
        )
        cls.empty_product = Product.objects.create(
            category=category,
            name="Шлифмашина",
            description="Товар без экземпляров",
            status="test-status",
        )

        cls.first_point = PickupPoint.objects.create(
            name="ПВ №1",
            address="Москва, улица 1",
            latitude=Decimal("55.755800"),
            longitude=Decimal("37.617300"),
            status="test-status",
        )
        cls.second_point = PickupPoint.objects.create(
            name="ПВ №2",
            address="Москва, улица 2",
            latitude=Decimal("55.760000"),
            longitude=Decimal("37.620000"),
            status="test-status",
        )

        for code, item_status, point in (
            ("free-1", "available", cls.first_point),
            ("free-2", "available", cls.first_point),
            ("free-3", "available", cls.second_point),
            ("unavailable", "unavailable", cls.first_point),
            ("retired", "retired", cls.second_point),
            ("unplaced", "available", None),
        ):
            create_item(
                product=cls.product,
                inventory_code=code,
                status=item_status,
                pickup_point=point,
            )

        create_item(
            product=cls.other_product,
            inventory_code="other-product",
            status="available",
            pickup_point=cls.first_point,
        )

        # Статусы reserved/rented нельзя задавать при обычном создании.
        # Формируем эти состояния только для проверки API в тестовой БД.
        for item_status in ("reserved", "rented"):
            item = create_item(
                product=cls.product,
                inventory_code=f"test-{item_status}",
                status="available",
                pickup_point=cls.first_point,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE product_item SET status = %s WHERE id = %s",
                    [item_status, item.pk],
                )

    def test_available_counts(self):
        url = reverse("product-availability", args=[self.product.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            "product_id": self.product.pk,
            "pickup_points": [
                {
                    "pickup_point_id": self.first_point.pk,
                    "available_count": 2,
                },
                {
                    "pickup_point_id": self.second_point.pk,
                    "available_count": 1,
                },
            ],
        })

    def test_product_without_items(self):
        url = reverse("product-availability", args=[self.empty_product.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [point["available_count"] for point in response.data["pickup_points"]],
            [0, 0],
        )

    def test_missing_product(self):
        url = reverse("product-availability", args=[999999999])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_read_only(self):
        url = reverse("product-availability", args=[self.product.pk])
        before = list(
            ProductItem.objects.order_by("id")
            .values_list("id", "status", "pickup_point_id")
        )

        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(
                    response.status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                )

        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        self.assertEqual(
            list(
                ProductItem.objects.order_by("id")
                .values_list("id", "status", "pickup_point_id")
            ),
            before,
        )
