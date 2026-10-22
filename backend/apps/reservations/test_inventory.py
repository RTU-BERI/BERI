"""Inventory historical guards against synthetic assignment rows."""

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import ProductItem
from apps.inventory.services import clear_location, set_location

from .models import ReservationItemAssignment
from .test_support import FixtureMixin


class InventoryReservationGuardTests(FixtureMixin, TestCase):
    def closed_assignment_fixture(self, reservation):
        """Test-only closed history, without a cancellation service or rights."""
        now = timezone.now()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE reservation_item_assignment SET released_at=%s "
                "WHERE reservation_id=%s AND released_at IS NULL",
                [now, reservation.pk],
            )
            cursor.execute(
                "UPDATE reservation SET status='cancelled',cancelled_at=%s,"
                "completed_at=%s,completion_reason=%s WHERE id=%s",
                [now, now, "Synthetic closed history", reservation.pk],
            )
            cursor.execute(
                "UPDATE product_item SET status='available',status_changed_at=%s,"
                "updated_at=%s WHERE id=%s",
                [now, now, reservation.assigned_product_item_id],
            )

    def item_values(self, item=None):
        return ProductItem.objects.filter(
            pk=(item or self.item).pk
        ).values().get()

    def assert_product_edit_rejected(self, item, *, update_fields=None):
        before = self.item_values(item)
        item.product = self.other_product
        with self.assertRaises(ValidationError) as caught:
            item.save(update_fields=update_fields)
        self.assertIn("product", caught.exception.message_dict)
        self.assertEqual(self.item_values(item), before)

    def test_product_can_still_change_before_any_assignment(self):
        self.item.product = self.other_product
        self.item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.product_id, self.other_product.pk)

    def test_open_assignment_protects_product_in_full_clean_and_save(self):
        self.make_reservation()
        self.item.refresh_from_db()
        self.item.product = self.other_product
        with self.assertRaises(ValidationError) as caught:
            self.item.full_clean()
        self.assertIn("product", caught.exception.message_dict)
        self.assert_product_edit_rejected(self.item)

    def test_closed_assignment_keeps_product_immutable(self):
        reservation = self.make_reservation()
        self.closed_assignment_fixture(reservation)
        self.assertIsNotNone(
            ReservationItemAssignment.objects.get(
                reservation=reservation
            ).released_at
        )
        self.item.refresh_from_db()
        self.assert_product_edit_rejected(self.item)

    def test_partial_save_cannot_omit_a_changed_historical_product(self):
        self.make_reservation()
        self.item.refresh_from_db()
        self.item.status_reason = "This edit must roll back with the rejection."
        self.assert_product_edit_rejected(
            self.item, update_fields=["status_reason"]
        )

    def test_constructor_with_existing_pk_cannot_rewrite_historical_product(self):
        self.make_reservation()
        values = self.item_values()
        values["product_id"] = self.other_product.pk
        replacement = ProductItem(**values)
        with self.assertRaises(ValidationError) as caught:
            replacement.save()
        self.assertIn("product", caught.exception.message_dict)
        self.assertEqual(self.item_values()["product_id"], self.product.pk)

    def test_assignment_guard_reads_database_despite_stale_item(self):
        stale = ProductItem.objects.get(pk=self.item.pk)
        self.make_reservation()
        self.assert_product_edit_rejected(stale)

    def test_reverse_product_updates_cannot_bypass_history_guard(self):
        self.make_reservation()
        self.item.refresh_from_db()
        with self.assertRaises(NotSupportedError):
            with transaction.atomic():
                self.other_product.items.add(self.item)
        self.assertEqual(self.item_values()["product_id"], self.product.pk)
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                self.other_product.items.add(self.item, bulk=False)
        self.assertEqual(self.item_values()["product_id"], self.product.pk)

    def test_reserved_item_cannot_move_or_clear(self):
        self.make_reservation()
        before = self.item_values()
        history = list(self.item.location_history.values())
        for destination in (self.other_point, None):
            with self.subTest(destination=destination), self.assertRaises(
                ValidationError
            ):
                set_location(self.item, destination)
        self.assertEqual(self.item_values(), before)
        self.assertEqual(list(self.item.location_history.values()), history)

    def test_open_assignment_blocks_move_even_when_status_was_corrupted(self):
        self.make_reservation()
        # Deliberately bypass the public API to check fail-closed source handling.
        self.raw_item_update(status=ProductItem.Status.AVAILABLE)
        before = self.item_values()
        for destination in (self.other_point, None):
            with self.subTest(destination=destination), self.assertRaises(
                ValidationError
            ):
                set_location(self.item, destination)
        self.assertEqual(self.item_values(), before)
        self.assertTrue(
            ReservationItemAssignment.objects.filter(
                product_item=self.item, released_at__isnull=True
            ).exists()
        )

    def test_reserved_same_point_is_a_full_noop_with_fresh_state(self):
        self.make_reservation()
        before = self.item_values()
        history = list(self.item.location_history.values())
        self.item.status_reason = "Unsaved stale caller metadata"
        current = set_location(self.item, self.point)
        self.assertEqual(current.status, ProductItem.Status.RESERVED)
        self.assertEqual(current.status_reason, before["status_reason"])
        self.assertEqual(self.item_values(), before)
        self.assertEqual(list(self.item.location_history.values()), history)

    def test_repeat_clear_keeps_the_existing_null_location_noop(self):
        item = self.make_item(pickup_point=None)
        self.raw_item_update(item=item, status=ProductItem.Status.RESERVED)
        before = self.item_values(item)
        current = clear_location(item)
        self.assertIsNone(current.pickup_point_id)
        self.assertEqual(self.item_values(item), before)
        self.assertFalse(item.location_history.exists())

    def test_same_point_noop_still_validates_location_history(self):
        self.make_reservation()
        self.raw_item_update(pickup_point_id=self.other_point.pk)
        before = self.item_values()
        with self.assertRaises(ValidationError):
            set_location(self.item, self.other_point)
        self.assertEqual(self.item_values(), before)

    def test_closed_assignment_does_not_block_location_changes(self):
        reservation = self.make_reservation()
        self.closed_assignment_fixture(reservation)
        moved = set_location(self.item, self.other_point)
        self.assertEqual(moved.pickup_point_id, self.other_point.pk)
        cleared = clear_location(moved)
        self.assertIsNone(cleared.pickup_point_id)
        self.assertFalse(cleared.location_history.filter(valid_to=None).exists())

    def test_unavailable_and_retired_placement_contract_is_preserved(self):
        for status in (
            ProductItem.Status.UNAVAILABLE, ProductItem.Status.RETIRED
        ):
            with self.subTest(status=status):
                item = self.make_item(status=status)
                moved = set_location(item, self.other_point)
                self.assertEqual(moved.pickup_point_id, self.other_point.pk)
                self.assertEqual(moved.status, status)


    def test_public_status_change_remains_forbidden_after_booking(self):
        self.make_reservation()
        self.item.refresh_from_db()
        self.item.status = ProductItem.Status.AVAILABLE
        with self.assertRaises(ValidationError) as caught:
            self.item.save(update_fields=["status"])
        self.assertIn("status", caught.exception.message_dict)
        self.assertEqual(self.item_values()["status"], ProductItem.Status.RESERVED)


    def raw_item_update(self, *, item=None, **values):
        names = {field.column for field in ProductItem._meta.concrete_fields}
        if not values or not set(values).issubset(names):
            raise AssertionError("Unknown test-fixture column")
        assignments = ", ".join(
            f"{connection.ops.quote_name(name)} = %s" for name in values
        )
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE product_item SET {assignments} WHERE id = %s",
                [*values.values(), (item or self.item).pk],
            )
