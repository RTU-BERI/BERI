from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from apps.inventory.models import ProductItem

from .models import Reservation, ReservationItemAssignment
from .services import (
    ReservationConflict,
    create_reservation,
    expire_reservation,
    is_reservation_active,
)
from .test_support import FixtureMixin


class BookingFixtures(FixtureMixin):
    def booking_args(self, **overrides):
        return {
            "customer_account_id": self.customer.pk,
            "product_id": self.product.pk,
            "pickup_point_id": self.point.pk,
            "request_id": uuid4(),
            **overrides,
        }


class ReservationServiceTests(BookingFixtures, TestCase):
    def test_create_reservation_and_first_assignment(self):
        args = self.booking_args()
        reservation = create_reservation(**args)
        reservation.refresh_from_db()
        assignment = reservation.assignments.get()
        self.item.refresh_from_db()

        self.assertEqual(reservation.pk, args["request_id"])
        self.assertEqual(reservation.customer_account_id, self.customer.pk)
        self.assertEqual(reservation.pickup_point_id, self.point.pk)
        self.assertEqual(reservation.initial_product_item_id, self.item.pk)
        self.assertEqual(reservation.assigned_product_item_id, self.item.pk)
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)
        self.assertEqual(
            reservation.expires_at - reservation.created_at, timedelta(hours=1)
        )
        self.assertIsNone(reservation.completed_at)
        self.assertIsNone(reservation.cancelled_at)
        self.assertEqual(assignment.product_item_id, self.item.pk)
        self.assertEqual(assignment.sequence_no, 1)
        self.assertEqual(assignment.assigned_at, reservation.created_at)
        self.assertIsNone(assignment.released_at)
        self.assertIsNone(assignment.assigned_by_employee_id)
        self.assertEqual(self.item.status, ProductItem.Status.RESERVED)
        self.assertEqual(self.item.status_changed_at, reservation.created_at)

    def test_deleted_unknown_and_ineligible_accounts_cannot_book(self):
        inactive = self.make_customer(is_active=False)
        for account_id in (None, uuid4(), self.employee.account_id, inactive.pk):
            with self.subTest(account_id=account_id), self.assertRaises(
                PermissionDenied
            ):
                create_reservation(**self.booking_args(customer_account_id=account_id))
        self.assertFalse(Reservation.objects.exists())

    def test_target_ids_must_exist_and_have_valid_types(self):
        for field in ("product_id", "pickup_point_id"):
            for value in (True, 0, "bad", 2**63, 2**63 - 1):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError) as error:
                        create_reservation(**self.booking_args(**{field: value}))
                    self.assertIn(field, error.exception.message_dict)
        with self.assertRaises(ValidationError):
            create_reservation(**self.booking_args(request_id="bad"))
        self.assertFalse(Reservation.objects.exists())

    def test_other_products_points_and_nonfree_items_are_excluded(self):
        self.make_item(product=self.other_product)
        self.make_item(pickup_point=self.other_point)
        self.make_item(pickup_point=None)
        self.make_item(status=ProductItem.Status.UNAVAILABLE)
        self.make_item(status=ProductItem.Status.RETIRED)
        create_reservation(**self.booking_args())

        with self.assertRaises(ReservationConflict):
            create_reservation(
                **self.booking_args(customer_account_id=self.other_customer.pk)
            )
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationItemAssignment.objects.count(), 1)

    def test_open_assignment_blocks_booking_even_if_stock_status_disagrees(self):
        self.make_reservation()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE product_item SET status='available' WHERE id=%s", [self.item.pk]
            )

        with self.assertRaises(ReservationConflict):
            create_reservation(**self.booking_args())
        self.assertEqual(Reservation.objects.count(), 1)

    def test_retry_uses_same_record_but_new_request_can_book_another_item(self):
        second_item = self.make_item()
        args = self.booking_args()
        first = create_reservation(**args)
        repeated = create_reservation(**args)

        self.assertEqual(repeated.pk, first.pk)
        self.assertEqual(repeated.expires_at, first.expires_at)
        self.assertEqual(ReservationItemAssignment.objects.count(), 1)
        second_item.refresh_from_db()
        self.assertEqual(second_item.status, ProductItem.Status.AVAILABLE)

        second = create_reservation(**self.booking_args())
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.assigned_product_item_id, second_item.pk)
        self.assertEqual(ReservationItemAssignment.objects.count(), 2)

    def test_request_id_cannot_be_reused_for_a_different_operation(self):
        args = self.booking_args()
        create_reservation(**args)
        for overrides in (
            {"customer_account_id": self.other_customer.pk},
            {"product_id": self.other_product.pk},
            {"pickup_point_id": self.other_point.pk},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(
                ReservationConflict
            ):
                create_reservation(**{**args, **overrides})
        self.assertEqual(Reservation.objects.count(), 1)

    def test_write_failure_rolls_back_reservation_and_assignment(self):
        with patch(
            "apps.reservations.services._set_item_status", side_effect=RuntimeError
        ):
            with self.assertRaises(RuntimeError):
                create_reservation(**self.booking_args())
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(ReservationItemAssignment.objects.exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ProductItem.Status.AVAILABLE)

    def test_deadline_and_expiry_command_are_idempotent(self):
        args = self.booking_args()
        reservation = create_reservation(**args)
        deadline = reservation.expires_at
        before = deadline - timedelta(microseconds=1)
        self.assertTrue(is_reservation_active(reservation, at=before))
        self.assertFalse(is_reservation_active(reservation, at=deadline))
        with patch("apps.reservations.services.timezone.now", return_value=before):
            self.assertFalse(expire_reservation(reservation.pk))

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ProductItem.Status.RESERVED)
        with patch("apps.reservations.services.timezone.now", return_value=deadline):
            self.assertFalse(is_reservation_active(create_reservation(**args)))
            first_run, second_run = StringIO(), StringIO()
            call_command("expire_reservations", stdout=first_run)
            call_command("expire_reservations", stdout=second_run)
            self.assertIn("Expired reservations: 1", first_run.getvalue())
            self.assertIn("Expired reservations: 0", second_run.getvalue())
            replay = create_reservation(**args)
            self.assertEqual(replay.status, Reservation.Status.EXPIRED)
            self.assertEqual(replay.expires_at, deadline)
            self.assertEqual(replay.completed_at, deadline)
            self.assertEqual(replay.assigned_product_item_id, self.item.pk)
            self.assertEqual(replay.assignments.get().released_at, deadline)
            self.item.refresh_from_db()
            self.assertEqual(self.item.status, ProductItem.Status.AVAILABLE)
            self.assertEqual(self.item.status_changed_at, deadline)
            replacement = create_reservation(**self.booking_args())

        self.assertNotEqual(replacement.pk, reservation.pk)
        self.assertEqual(replacement.assigned_product_item_id, self.item.pk)
        self.assertEqual(replacement.expires_at, deadline + timedelta(hours=1))

    def test_expiry_does_not_release_damaged_item_into_free_stock(self):
        reservation = create_reservation(**self.booking_args())
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE product_item SET status='unavailable',status_reason=%s WHERE id=%s",
                ["Fixture: damaged item", self.item.pk],
            )
        with patch(
            "apps.reservations.services.timezone.now",
            return_value=reservation.expires_at,
        ):
            self.assertTrue(expire_reservation(reservation.pk))

        reservation.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertEqual(
            reservation.assignments.get().released_at, reservation.expires_at
        )
        self.assertEqual(self.item.status, ProductItem.Status.UNAVAILABLE)
        self.assertEqual(self.item.status_reason, "Fixture: damaged item")


class ReservationServiceConcurrencyTests(BookingFixtures, TransactionTestCase):
    def race(self, actions):
        self.assertEqual(connection.vendor, "postgresql")
        barrier = Barrier(2)

        def worker(action):
            close_old_connections()
            try:
                connection.ensure_connection()
                barrier.wait(timeout=10)
                return action()
            except ReservationConflict:
                return "conflict"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_two_customers_competing_for_one_item_get_one_booking_and_one_conflict(
        self,
    ):
        first = self.booking_args()
        second = self.booking_args(customer_account_id=self.other_customer.pk)
        results = self.race(
            (lambda: create_reservation(**first), lambda: create_reservation(**second))
        )

        self.assertIn("conflict", results)
        winner = next(result for result in results if isinstance(result, Reservation))
        self.assertEqual(winner.assigned_product_item_id, self.item.pk)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(
            ReservationItemAssignment.objects.filter(released_at__isnull=True).count(),
            1,
        )

    def test_concurrent_retry_returns_same_record_even_with_another_item(self):
        second_item = self.make_item()
        args = self.booking_args()

        def book():
            return create_reservation(**args)

        first, second = self.race((book, book))

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.expires_at, second.expires_at)
        self.assertEqual(ReservationItemAssignment.objects.count(), 1)
        second_item.refresh_from_db()
        self.assertEqual(second_item.status, ProductItem.Status.AVAILABLE)

    def test_concurrent_expiry_releases_inventory_only_once(self):
        reservation = create_reservation(**self.booking_args())

        def expire():
            return expire_reservation(reservation.pk)

        with patch(
            "apps.reservations.services.timezone.now",
            return_value=reservation.expires_at,
        ):
            self.assertCountEqual(self.race((expire, expire)), [True, False])

        self.item.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(self.item.status, ProductItem.Status.AVAILABLE)
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertEqual(
            reservation.assignments.get().released_at, reservation.expires_at
        )

    def test_two_customers_can_book_distinct_items_in_the_same_point(self):
        second_item = self.make_item()
        first = self.booking_args()
        second = self.booking_args(customer_account_id=self.other_customer.pk)
        results = self.race(
            (lambda: create_reservation(**first), lambda: create_reservation(**second))
        )

        self.assertCountEqual(
            [reservation.assigned_product_item_id for reservation in results],
            [self.item.pk, second_item.pk],
        )
