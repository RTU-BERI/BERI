"""Physical pickup-point schema and the approved ISO weekday constraint."""

from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from .models import PickupPoint, PickupPointWorkingHours


def create_pickup_point(**overrides):
    values = {
        "name": "Fixture pickup point",
        "address": "Fixture address",
        "latitude": Decimal("55.755800"),
        "longitude": Decimal("37.617300"),
        "status": "fixture-status",
    }
    values.update(overrides)
    return PickupPoint.objects.create(**values)


class PickupPointPostgreSQLTests(TestCase):
    def get_columns(self, table_name):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                [table_name],
            )
            return {row[0]: row[1:] for row in cursor.fetchall()}

    def get_constraints(self, table_name):
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(cursor, table_name)

    def test_pickup_point_columns_match_the_exact_erd(self):
        self.assertEqual(PickupPoint._meta.db_table, "pickup_point")
        self.assertEqual(
            self.get_columns("pickup_point"),
            {
                "id": ("bigint", "NO", None, 64, 0),
                "name": ("character varying", "NO", 150, None, None),
                "address": ("text", "NO", None, None, None),
                "latitude": ("numeric", "NO", None, 9, 6),
                "longitude": ("numeric", "NO", None, 9, 6),
                "status": ("character varying", "NO", 30, None, None),
                "created_at": (
                    "timestamp with time zone", "NO", None, None, None
                ),
                "updated_at": (
                    "timestamp with time zone", "NO", None, None, None
                ),
            },
        )
        self.assertIsInstance(PickupPoint._meta.pk, models.BigAutoField)
        self.assertTrue(
            any(
                constraint["primary_key"] and constraint["columns"] == ["id"]
                for constraint in self.get_constraints("pickup_point").values()
            )
        )

    def test_working_hours_columns_pk_fk_and_uniqueness_match_erd(self):
        self.assertEqual(
            PickupPointWorkingHours._meta.db_table,
            "pickup_point_working_hours",
        )
        self.assertEqual(
            self.get_columns("pickup_point_working_hours"),
            {
                "id": ("bigint", "NO", None, 64, 0),
                "pickup_point_id": ("bigint", "NO", None, 64, 0),
                "weekday": ("smallint", "NO", None, 16, 0),
                "opens_at": (
                    "time without time zone", "NO", None, None, None
                ),
                "closes_at": (
                    "time without time zone", "NO", None, None, None
                ),
            },
        )
        self.assertIsInstance(
            PickupPointWorkingHours._meta.pk, models.BigAutoField
        )
        constraints = self.get_constraints("pickup_point_working_hours")
        self.assertTrue(
            any(
                constraint["primary_key"] and constraint["columns"] == ["id"]
                for constraint in constraints.values()
            )
        )
        self.assertTrue(
            any(
                constraint["foreign_key"] == ("pickup_point", "id")
                and constraint["columns"] == ["pickup_point_id"]
                for constraint in constraints.values()
            )
        )
        self.assertTrue(
            any(
                constraint["unique"]
                and constraint["columns"] == ["pickup_point_id", "weekday"]
                for constraint in constraints.values()
            )
        )


class PickupPointTests(TestCase):
    def test_status_has_no_business_enum_or_default(self):
        field = PickupPoint._meta.get_field("status")
        self.assertFalse(field.choices)
        self.assertFalse(field.has_default())
        point = create_pickup_point(status="unlisted-fixture-status")
        point.full_clean()
        point.refresh_from_db()
        self.assertEqual(point.status, "unlisted-fixture-status")
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "pickup_point"
            )
        self.assertFalse(
            any(
                constraint["check"] and "status" in constraint["columns"]
                for constraint in constraints.values()
            )
        )

    def test_coordinates_keep_six_decimals_without_geographic_policy(self):
        point = create_pickup_point(
            latitude=Decimal("999.123456"), longitude=Decimal("-999.654321")
        )
        point.full_clean()
        point.refresh_from_db()
        self.assertEqual(point.latitude, Decimal("999.123456"))
        self.assertEqual(point.longitude, Decimal("-999.654321"))

    def test_created_and_updated_timestamps_are_aware_and_maintained(self):
        created_at = timezone.now() - timedelta(days=1)
        updated_at = created_at + timedelta(hours=1)
        with patch(
            "django.db.models.fields.timezone.now", return_value=created_at
        ):
            point = create_pickup_point()
        with patch(
            "django.db.models.fields.timezone.now", return_value=updated_at
        ):
            point.address = "Changed fixture address"
            point.save()
        point.refresh_from_db()
        self.assertTrue(timezone.is_aware(point.created_at))
        self.assertTrue(timezone.is_aware(point.updated_at))
        self.assertEqual(point.created_at, created_at)
        self.assertEqual(point.updated_at, updated_at)


class PickupPointWorkingHoursTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.point = create_pickup_point()

    def create_hours(self, **overrides):
        values = {
            "pickup_point": self.point,
            "weekday": PickupPointWorkingHours.Weekday.MONDAY,
            "opens_at": time(9),
            "closes_at": time(18),
        }
        values.update(overrides)
        return PickupPointWorkingHours.objects.create(**values)

    def test_same_point_and_weekday_is_unique(self):
        self.create_hours()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_hours(opens_at=time(10))

    def test_different_days_and_points_can_have_working_hours(self):
        first = self.create_hours()
        next_day = self.create_hours(
            weekday=PickupPointWorkingHours.Weekday.TUESDAY
        )
        other_point = self.create_hours(pickup_point=create_pickup_point())
        self.assertEqual(len({first.pk, next_day.pk, other_point.pk}), 3)

    def test_iso_weekday_boundaries_are_valid_and_have_no_default(self):
        weekday_field = PickupPointWorkingHours._meta.get_field("weekday")
        self.assertFalse(weekday_field.has_default())
        monday = self.create_hours(weekday=1)
        sunday = self.create_hours(weekday=7)
        monday.full_clean()
        sunday.full_clean()
        self.assertEqual(monday.weekday, 1)
        self.assertEqual(sunday.weekday, 7)

    def test_invalid_weekday_fails_model_validation(self):
        for weekday in (-1, 0, 8):
            with self.subTest(weekday=weekday):
                hours = PickupPointWorkingHours(
                    pickup_point=self.point,
                    weekday=weekday,
                    opens_at=time(9),
                    closes_at=time(18),
                )
                with self.assertRaises(ValidationError) as raised:
                    hours.full_clean()
                self.assertIn("weekday", raised.exception.message_dict)

    def test_invalid_weekday_is_rejected_by_postgresql(self):
        for weekday in (-1, 0, 8):
            with self.subTest(weekday=weekday):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_hours(weekday=weekday)

    def test_pickup_point_is_required_and_must_exist(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_hours(pickup_point=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PickupPointWorkingHours.objects.create(
                pickup_point_id=-1,
                weekday=1,
                opens_at=time(9),
                closes_at=time(18),
            )
            connection.check_constraints(
                table_names=["pickup_point_working_hours"]
            )

    def test_pickup_point_with_hours_is_protected_from_deletion(self):
        hours = self.create_hours()
        with self.assertRaises(ProtectedError):
            self.point.delete()
        self.assertTrue(PickupPoint.objects.filter(pk=self.point.pk).exists())
        self.assertTrue(
            PickupPointWorkingHours.objects.filter(pk=hours.pk).exists()
        )

    def test_equal_and_overnight_times_are_stored_without_interpretation(self):
        for weekday, opens_at, closes_at in (
            (1, time(22, 30), time(6, 15)),
            (2, time(9), time(9)),
        ):
            with self.subTest(weekday=weekday):
                hours = self.create_hours(
                    weekday=weekday, opens_at=opens_at, closes_at=closes_at
                )
                hours.full_clean()
                hours.refresh_from_db()
                self.assertEqual(hours.opens_at, opens_at)
                self.assertEqual(hours.closes_at, closes_at)
