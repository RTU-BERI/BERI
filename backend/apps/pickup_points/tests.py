from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase
from django.utils import timezone

from .models import PickupPoint


class PickupPointTests(TestCase):
    def test_erd_fields_precision_and_timezone_aware_timestamps(self):
        point = PickupPoint.objects.create(
            name="Test pickup point", address="Test address", latitude=Decimal("55.755826"),
            longitude=Decimal("37.617300"), status="test-fixture",
        )
        point.refresh_from_db()
        self.assertIsInstance(point.pk, int)
        self.assertIsInstance(PickupPoint._meta.pk, models.BigAutoField)
        self.assertEqual(PickupPoint._meta.db_table, "pickup_point")
        self.assertEqual(str(point), point.name)
        self.assertEqual(point.latitude, Decimal("55.755826"))
        self.assertEqual(point.longitude, Decimal("37.617300"))
        self.assertTrue(timezone.is_aware(point.created_at))
        self.assertTrue(timezone.is_aware(point.updated_at))
        created_at, updated_at = point.created_at, point.updated_at
        point.address = "Updated test address"
        point.save()
        self.assertEqual(point.created_at, created_at)
        self.assertGreaterEqual(point.updated_at, updated_at)
        for name in ("latitude", "longitude"):
            field = PickupPoint._meta.get_field(name)
            self.assertEqual((field.max_digits, field.decimal_places), (9, 6))
        self.assertEqual(PickupPoint._meta.get_field("name").max_length, 150)
        self.assertEqual(PickupPoint._meta.get_field("status").max_length, 30)
        self.assertFalse(PickupPoint._meta.get_field("status").choices)
        for field in PickupPoint._meta.fields:
            self.assertFalse(field.null)

    def test_required_erd_fields_and_decimal_precision_validate(self):
        point = PickupPoint(name="Test", address="Address", status="test-fixture")
        with self.assertRaises(ValidationError) as raised:
            point.full_clean()
        self.assertIn("latitude", raised.exception.message_dict)
        self.assertIn("longitude", raised.exception.message_dict)
        point.latitude = Decimal("55.1234567")
        point.longitude = Decimal("37.617300")
        with self.assertRaises(ValidationError) as raised:
            point.full_clean()
        self.assertIn("latitude", raised.exception.message_dict)
