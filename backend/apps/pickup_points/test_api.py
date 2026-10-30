from datetime import time
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import PickupPoint, PickupPointWorkingHours


class PickupPointAPITests(APITestCase):
    """Тесты публичного API пунктов выдачи."""

    @classmethod
    def setUpTestData(cls):
        cls.point = PickupPoint.objects.create(
            name="Тестовый пункт выдачи",
            address="Москва, Тестовая улица, 10",
            latitude=Decimal("55.755800"),
            longitude=Decimal("37.617300"),
            status="test-status",
        )

        # Создаём расписание не по порядку для проверки сортировки.
        for weekday, opens, closes in (
            (2, time(10), time(19)),
            (1, time(9), time(18)),
        ):
            PickupPointWorkingHours.objects.create(
                pickup_point=cls.point,
                weekday=weekday,
                opens_at=opens,
                closes_at=closes,
            )

    def test_empty_list(self):
        PickupPointWorkingHours.objects.all().delete()
        PickupPoint.objects.all().delete()

        response = self.client.get(reverse("pickup-point-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_missing_point(self):
        url = reverse("pickup-point-detail", args=[999999999])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_and_detail(self):
        second = PickupPoint.objects.create(
            name="Второй пункт выдачи",
            address="Москва, Другая улица, 20",
            latitude=Decimal("55.760000"),
            longitude=Decimal("37.620000"),
            status="test-status",
        )

        response = self.client.get(reverse("pickup-point-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [point["id"] for point in response.data],
            [self.point.pk, second.pk],
        )
        self.assertEqual(response.data[1]["working_hours"], [])

        detail_url = reverse("pickup-point-detail", args=[self.point.pk])
        detail = self.client.get(detail_url)

        expected = {
            "id": self.point.pk,
            "name": "Тестовый пункт выдачи",
            "address": "Москва, Тестовая улица, 10",
            "latitude": "55.755800",
            "longitude": "37.617300",
            "status": "test-status",
            "working_hours": [
                {
                    "weekday": 1,
                    "opens_at": "09:00:00",
                    "closes_at": "18:00:00",
                },
                {
                    "weekday": 2,
                    "opens_at": "10:00:00",
                    "closes_at": "19:00:00",
                },
            ],
        }

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data, expected)
        self.assertEqual(response.data[0], expected)

    def test_write_methods_not_allowed(self):
        list_url = reverse("pickup-point-list")
        detail_url = reverse("pickup-point-detail", args=[self.point.pk])

        before = (
            PickupPoint.objects.count(),
            PickupPointWorkingHours.objects.count(),
        )

        for url in (list_url, detail_url):
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(
                        url,
                        {"name": "Изменённый ПВ"},
                        format="json",
                    )
                    self.assertEqual(
                        response.status_code,
                        status.HTTP_405_METHOD_NOT_ALLOWED,
                    )

        self.assertEqual(
            (
                PickupPoint.objects.count(),
                PickupPointWorkingHours.objects.count(),
            ),
            before,
        )
        self.point.refresh_from_db()
        self.assertEqual(self.point.name, "Тестовый пункт выдачи")
