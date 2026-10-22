"""Internal placement services and their PostgreSQL history guarantees.

Corruption fixtures and raw INSERTs intentionally bypass the public history
write prohibition. They distinguish database constraints from service guards.
"""

from datetime import datetime, timedelta, timezone
from queue import Empty, Queue
from threading import Event, Thread, current_thread
from time import monotonic
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    NotSupportedError,
    connection,
    connections,
    transaction,
)
from django.test import TestCase, TransactionTestCase
from django.utils.timezone import is_aware

from apps.catalog.models import Product, ProductCategory
from apps.pickup_points.models import PickupPoint

from . import services
from .models import ProductItem, ProductItemLocationHistory
from .services import create_item, set_location


BASE = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
STEP = timedelta(seconds=10)


def fixtures():
    category = ProductCategory.objects.create(name="Location fixture category")
    product = Product.objects.create(
        category=category,
        name="Location fixture product",
        description="Synthetic location test fixture",
        status="fixture-status",
    )
    points = tuple(
        PickupPoint.objects.create(
            name=f"Location fixture point {number}",
            address=f"Synthetic address {number}",
            latitude="55.750000",
            longitude="37.610000",
            status="fixture-status",
        )
        for number in (1, 2, 3)
    )
    return product, points


def new_item(product, code, point=None):
    with patch.object(services.timezone, "now", return_value=BASE):
        return create_item(
            product=product,
            inventory_code=code,
            status="unavailable",
            status_reason="Synthetic inspection reason",
            pickup_point=point,
        )


def insert_history(cursor, item_id, point_id, lower, upper):
    cursor.execute(
        "INSERT INTO product_item_location_history "
        "(product_item_id, pickup_point_id, valid_from, valid_to) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        [item_id, point_id, lower, upper],
    )
    return cursor.fetchone()[0]


def state_of(item):
    return ProductItem.objects.values().get(pk=item.pk)


def history_of(item):
    return list(
        ProductItemLocationHistory.objects.filter(product_item_id=item.pk)
        .order_by("valid_from", "pk")
        .values_list("pickup_point_id", "valid_from", "valid_to")
    )


def error_sqlstate(error):
    cause = error.__cause__
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


class LocationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product, cls.points = fixtures()
        cls.point_a, cls.point_b, cls.point_c = cls.points

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")

    def move(self, item, destination, at=BASE + STEP):
        with patch.object(services.timezone, "now", return_value=at):
            return set_location(item, destination)

    def assert_mapping(self, item):
        item.refresh_from_db()
        opened = list(ProductItemLocationHistory.objects.filter(
            product_item_id=item.pk, valid_to__isnull=True
        ))
        if item.pickup_point_id is None:
            self.assertEqual(opened, [])
        else:
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0].pickup_point_id, item.pickup_point_id)
        return item

    def test_creation_without_point_has_no_history_and_keeps_null_location(self):
        item = new_item(self.product, "location-no-point")
        self.assertIsNone(item.pickup_point_id)
        self.assertEqual(history_of(item), [])
        self.assert_mapping(item)

    def test_initial_point_creates_one_matching_open_period_with_aware_time(self):
        item = new_item(self.product, "location-initial", self.point_a)
        self.assertEqual(history_of(item), [(self.point_a.pk, BASE, None)])
        self.assertEqual(item.pickup_point_id, self.point_a.pk)
        self.assertTrue(is_aware(item.created_at))
        self.assertTrue(is_aware(item.status_changed_at))
        self.assert_mapping(item)

    def test_move_closes_old_period_and_opens_new_at_the_same_boundary(self):
        item = new_item(self.product, "location-move", self.point_a)
        result = self.move(item, self.point_b)
        self.assertEqual(result.pk, item.pk)
        self.assertEqual(result.pickup_point_id, self.point_b.pk)
        self.assertEqual(history_of(item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_b.pk, BASE + STEP, None),
        ])
        self.assert_mapping(result)

    def test_clear_closes_history_and_preserves_the_closed_record(self):
        item = new_item(self.product, "location-clear", self.point_a)
        original_id = ProductItemLocationHistory.objects.get(
            product_item_id=item.pk
        ).pk
        result = self.move(item, None)
        self.assertIsNone(result.pickup_point_id)
        self.assertEqual(history_of(item), [
            (self.point_a.pk, BASE, BASE + STEP),
        ])
        self.assertTrue(ProductItemLocationHistory.objects.filter(pk=original_id).exists())
        self.assert_mapping(result)

    def test_replacement_after_null_location_preserves_the_unlocated_gap(self):
        item = new_item(self.product, "location-gap", self.point_a)
        self.move(item, None)
        result = self.move(item, self.point_b, BASE + 2 * STEP)
        self.assertEqual(history_of(item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_b.pk, BASE + 2 * STEP, None),
        ])
        self.assert_mapping(result)

    def test_initial_null_location_can_receive_its_first_period_later(self):
        item = new_item(self.product, "location-first-later")
        result = self.move(item, self.point_b)
        self.assertEqual(history_of(item), [(self.point_b.pk, BASE + STEP, None)])
        self.assert_mapping(result)

    def test_same_point_is_a_complete_noop_even_with_a_different_point_instance(self):
        item = new_item(self.product, "location-same", self.point_a)
        before, previous_history = state_of(item), history_of(item)
        same_point = PickupPoint.objects.get(pk=self.point_a.pk)
        result = self.move(item, same_point, BASE + 2 * STEP)
        self.assertEqual(result.pickup_point_id, self.point_a.pk)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), previous_history)

    def test_clearing_null_is_a_complete_noop(self):
        item = new_item(self.product, "location-null-noop")
        before = state_of(item)
        result = self.move(item, None)
        self.assertIsNone(result.pickup_point_id)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), [])

    def test_location_changes_never_change_status_identity_or_status_timestamp(self):
        item = new_item(self.product, "location-status", self.point_a)
        before = state_of(item)
        self.move(item, self.point_b)
        self.move(item, None, BASE + 2 * STEP)
        after = state_of(item)
        for field in (
            "product_id", "inventory_code", "status", "status_reason",
            "status_changed_at", "created_at",
        ):
            with self.subTest(field=field):
                self.assertEqual(after[field], before[field])
        self.assertEqual(after["status"], "unavailable")
        self.assertGreater(after["updated_at"], before["updated_at"])

    def test_stale_input_and_cached_point_do_not_override_current_database_state(self):
        item = new_item(self.product, "location-stale", self.point_a)
        stale = ProductItem.objects.select_related("pickup_point").get(pk=item.pk)
        self.assertEqual(stale.pickup_point.pk, self.point_a.pk)
        self.move(item, self.point_b)
        stale.inventory_code = "untrusted-stale-code"
        stale.status = "available"
        stale.status_reason = "untrusted-stale-reason"
        result = self.move(stale, self.point_c, BASE + 2 * STEP)
        self.assertEqual(result.pickup_point_id, self.point_c.pk)
        self.assertEqual(result.inventory_code, "location-stale")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.status_reason, "Synthetic inspection reason")
        self.assertEqual(history_of(item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_b.pk, BASE + STEP, BASE + 2 * STEP),
            (self.point_c.pk, BASE + 2 * STEP, None),
        ])
        self.assert_mapping(result)

    def test_equal_or_earlier_clock_cannot_close_an_open_period(self):
        item = new_item(self.product, "location-clock", self.point_a)
        before, previous_history = state_of(item), history_of(item)
        for at in (BASE, BASE - STEP):
            with self.subTest(at=at), self.assertRaises(ValidationError):
                self.move(item, self.point_b, at)
            self.assertEqual(state_of(item), before)
            self.assertEqual(history_of(item), previous_history)

    def test_future_open_history_is_rejected_without_automatic_repair(self):
        item = new_item(self.product, "location-future", self.point_a)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE product_item_location_history SET valid_from=%s "
                "WHERE product_item_id=%s",
                [BASE + 10 * STEP, item.pk],
            )
        before, previous_history = state_of(item), history_of(item)
        for destination in (self.point_a, self.point_b, None):
            with self.subTest(destination=destination):
                with self.assertRaises(ValidationError):
                    self.move(item, destination)
                self.assertEqual(state_of(item), before)
                self.assertEqual(history_of(item), previous_history)

    def test_mismatched_current_and_history_points_fail_without_repair(self):
        item = new_item(self.product, "location-mismatch", self.point_a)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE product_item SET pickup_point_id=%s WHERE id=%s",
                [self.point_b.pk, item.pk],
            )
        before, previous_history = state_of(item), history_of(item)
        for destination in (self.point_b, self.point_c, None):
            with self.subTest(destination=destination):
                with self.assertRaises(ValidationError):
                    self.move(item, destination)
                self.assertEqual(state_of(item), before)
                self.assertEqual(history_of(item), previous_history)

    def test_nonnull_current_point_without_open_history_is_rejected(self):
        item = new_item(self.product, "location-missing-history", self.point_a)
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM product_item_location_history WHERE product_item_id=%s",
                [item.pk],
            )
        before = state_of(item)
        with self.assertRaises(ValidationError):
            self.move(item, self.point_b)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), [])

    def test_null_current_point_with_open_history_is_rejected(self):
        item = new_item(self.product, "location-unexpected-history")
        with connection.cursor() as cursor:
            insert_history(cursor, item.pk, self.point_a.pk, BASE, None)
        before, previous_history = state_of(item), history_of(item)
        with self.assertRaises(ValidationError):
            self.move(item, self.point_b)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), previous_history)

    def test_unsaved_or_missing_destination_cannot_change_item_or_history(self):
        item = new_item(self.product, "location-invalid-point", self.point_a)
        before, previous_history = state_of(item), history_of(item)
        for point in (PickupPoint(), PickupPoint(pk=2 ** 62)):
            with self.subTest(point_id=point.pk):
                with self.assertRaises(ValidationError):
                    self.move(item, point)
                self.assertEqual(state_of(item), before)
                self.assertEqual(history_of(item), previous_history)

    def test_unsaved_or_missing_source_item_does_not_create_history(self):
        for item in (ProductItem(), ProductItem(pk=2 ** 62)):
            with self.subTest(item_id=item.pk), self.assertRaises(ValidationError):
                self.move(item, self.point_a)
        self.assertEqual(ProductItem.objects.count(), 0)
        self.assertEqual(ProductItemLocationHistory.objects.count(), 0)

    def test_move_rolls_back_closed_old_and_inserted_new_history_on_failure(self):
        item = new_item(self.product, "location-rollback-move", self.point_a)
        before, previous_history = state_of(item), history_of(item)
        with patch.object(
            services, "_write_current_location", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.move(item, self.point_b)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), previous_history)
        self.assert_mapping(item)

    def test_clear_rolls_back_the_closed_history_on_failure(self):
        item = new_item(self.product, "location-rollback-clear", self.point_a)
        before, previous_history = state_of(item), history_of(item)
        with patch.object(
            services, "_write_current_location", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.move(item, None)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), previous_history)

    def test_initial_creation_rolls_back_the_item_if_history_insert_fails(self):
        with patch.object(
            services, "_insert_location_history", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                new_item(self.product, "location-rollback-create", self.point_a)
        self.assertFalse(ProductItem.objects.filter(
            inventory_code="location-rollback-create"
        ).exists())
        self.assertEqual(ProductItemLocationHistory.objects.count(), 0)

    def test_assignment_from_null_rolls_back_the_new_history_on_failure(self):
        item = new_item(self.product, "location-rollback-null")
        before = state_of(item)
        with patch.object(
            services, "_write_current_location", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.move(item, self.point_b)
        self.assertEqual(state_of(item), before)
        self.assertEqual(history_of(item), [])

    def test_history_public_create_change_delete_and_bulk_paths_are_forbidden(self):
        item = new_item(self.product, "location-history-guards", self.point_a)
        history = ProductItemLocationHistory.objects.get(product_item_id=item.pk)
        candidate = ProductItemLocationHistory(
            product_item=item,
            pickup_point=self.point_b,
            valid_from=BASE + STEP,
            valid_to=None,
        )
        original = history_of(item)
        operations = {
            "create": lambda: ProductItemLocationHistory.objects.create(
                product_item=item, pickup_point=self.point_b,
                valid_from=BASE + STEP, valid_to=None,
            ),
            "new-save": candidate.save,
            "existing-save": history.save,
            "instance-delete": history.delete,
            "queryset-delete": lambda: ProductItemLocationHistory.objects.filter(
                pk=history.pk
            ).delete(),
            "update": lambda: ProductItemLocationHistory.objects.filter(
                pk=history.pk
            ).update(valid_to=BASE + STEP),
            "bulk-create": lambda: ProductItemLocationHistory.objects.bulk_create(
                [candidate]
            ),
            "bulk-update": lambda: ProductItemLocationHistory.objects.bulk_update(
                [history], ["valid_to"]
            ),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                history.valid_to = BASE + STEP
                with self.assertRaises(NotSupportedError), transaction.atomic():
                    operation()
                history.refresh_from_db()
                self.assertEqual(history_of(item), original)


class LocationHistoryDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product, cls.points = fixtures()
        cls.item = new_item(cls.product, "location-db-first")
        cls.other_item = new_item(cls.product, "location-db-second")

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")

    def insert(self, lower, upper, *, item=None, point=None):
        with connection.cursor() as cursor:
            return insert_history(
                cursor, (item or self.item).pk,
                (point or self.points[0]).pk, lower, upper,
            )

    def assert_rejected(self, lower, upper, *, state, constraint):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                self.insert(lower, upper)
        self.assertEqual(error_sqlstate(raised.exception), state)
        self.assertEqual(
            raised.exception.__cause__.diag.constraint_name, constraint
        )

    def test_nonoverlapping_finite_periods_for_one_item_are_allowed(self):
        self.insert(BASE, BASE + STEP)
        self.insert(BASE + 2 * STEP, BASE + 3 * STEP)
        self.assertEqual(ProductItemLocationHistory.objects.count(), 2)

    def test_adjacent_periods_are_allowed_in_either_insert_order(self):
        intervals = [(BASE, BASE + STEP), (BASE + STEP, BASE + 2 * STEP)]
        for item, pairs in (
            (self.item, intervals), (self.other_item, list(reversed(intervals)))
        ):
            for lower, upper in pairs:
                self.insert(lower, upper, item=item)
        self.assertEqual(ProductItemLocationHistory.objects.count(), 4)

    def test_open_period_can_follow_an_exactly_adjacent_closed_period(self):
        self.insert(BASE + STEP, None)
        self.insert(BASE, BASE + STEP)
        self.assertEqual(ProductItemLocationHistory.objects.count(), 2)

    def test_overlapping_periods_for_different_items_are_allowed(self):
        self.insert(BASE, None)
        self.insert(BASE, None, item=self.other_item)
        self.assertEqual(ProductItemLocationHistory.objects.count(), 2)

    def test_database_excludes_every_finite_overlap_shape(self):
        self.insert(BASE, BASE + 4 * STEP)
        for lower, upper in (
            (BASE, BASE + 4 * STEP),
            (BASE + STEP, BASE + 2 * STEP),
            (BASE - STEP, BASE + 5 * STEP),
            (BASE - STEP, BASE + STEP),
            (BASE + 3 * STEP, BASE + 5 * STEP),
        ):
            with self.subTest(lower=lower, upper=upper):
                self.assert_rejected(
                    lower, upper, state="23P01",
                    constraint="product_item_location_no_overlap",
                )
        self.assertEqual(ProductItemLocationHistory.objects.count(), 1)

    def test_null_upper_bound_conflicts_with_any_later_finite_period(self):
        self.insert(BASE, None)
        self.assert_rejected(
            BASE + 100 * STEP, BASE + 101 * STEP, state="23P01",
            constraint="product_item_location_no_overlap",
        )

    def test_database_excludes_two_open_periods_for_the_same_item(self):
        self.insert(BASE, None)
        self.assert_rejected(
            BASE + STEP, None, state="23P01",
            constraint="product_item_location_no_overlap",
        )

    def test_database_check_rejects_empty_and_reversed_periods(self):
        for upper in (BASE, BASE - STEP):
            with self.subTest(valid_to=upper):
                self.assert_rejected(
                    BASE, upper, state="23514",
                    constraint="product_item_location_valid_period",
                )
        self.assertEqual(ProductItemLocationHistory.objects.count(), 0)


class TwoLocationWritersMixin:
    """Hold the first transaction until its real blocking of writer two is seen."""

    def two_writers(self, first_action, second_action, *, must_wait=True,
                    second_conflicts=False):
        first_written, second_ready = Event(), Event()
        observed_wait, poll_delay = Event(), Event()
        outcomes = Queue()
        backend_ids = {}

        def writer(role, action):
            worker_connection = connections["default"]
            try:
                if role == "second" and not first_written.wait(timeout=5):
                    raise AssertionError("The first writer never wrote its row.")
                with transaction.atomic(using="default"):
                    with worker_connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                        cursor.execute("SET LOCAL statement_timeout = '8s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_ids[role] = cursor.fetchone()[0]
                        if role == "second":
                            second_ready.set()
                        result = action(cursor)
                        if role == "first":
                            first_written.set()
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("The second writer was not ready.")
                            if must_wait:
                                deadline = monotonic() + 3
                                while True:
                                    cursor.execute(
                                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                                        [backend_ids["first"], backend_ids["second"]],
                                    )
                                    if cursor.fetchone()[0]:
                                        observed_wait.set()
                                        break
                                    remaining = deadline - monotonic()
                                    if remaining <= 0:
                                        raise AssertionError(
                                            "The second writer never waited for "
                                            "the uncommitted first writer."
                                        )
                                    poll_delay.wait(timeout=min(0.02, remaining))
                outcomes.put((role, "committed", result))
            except IntegrityError as error:
                outcomes.put((role, "integrity", error_sqlstate(error)))
            except Exception as error:
                outcomes.put((role, "error", repr(error)))
            finally:
                worker_connection.close()

        threads = [
            Thread(
                target=writer, args=(role, action),
                name=f"location-{role}", daemon=True,
            )
            for role, action in (("first", first_action), ("second", second_action))
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            second_ready.set()
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        results = {}
        for _ in threads:
            try:
                role, kind, detail = outcomes.get(timeout=1)
            except Empty:
                self.fail("A location writer did not record its outcome.")
            results[role] = (kind, detail)
        self.assertEqual(set(results), {"first", "second"})
        self.assertEqual(results["first"][0], "committed", results)
        if second_conflicts:
            self.assertEqual(results["second"], ("integrity", "23P01"), results)
        else:
            self.assertEqual(results["second"][0], "committed", results)
        self.assertEqual(len(set(backend_ids.values())), 2)
        if must_wait:
            self.assertTrue(observed_wait.is_set())
        return results


class LocationHistoryConcurrencyTests(TwoLocationWritersMixin, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.product, self.points = fixtures()
        self.item = new_item(self.product, "location-race-first")
        self.other_item = new_item(self.product, "location-race-second")

    def race(self, first_end, second_start, second_end, *, different_item=False,
             conflict=True):
        self.two_writers(
            lambda cursor: insert_history(
                cursor, self.item.pk, self.points[0].pk, BASE, first_end
            ),
            lambda cursor: insert_history(
                cursor, (self.other_item if different_item else self.item).pk,
                self.points[1].pk, second_start, second_end,
            ),
            must_wait=conflict, second_conflicts=conflict,
        )
        self.assertEqual(
            ProductItemLocationHistory.objects.count(), 1 if conflict else 2
        )

    def test_concurrent_finite_overlap_has_one_winner_and_sqlstate_23p01(self):
        self.race(BASE + 2 * STEP, BASE + STEP, BASE + 3 * STEP)

    def test_concurrent_open_overlap_has_one_winner_and_sqlstate_23p01(self):
        self.race(None, BASE + STEP, None)

    def test_concurrent_adjacent_periods_both_commit(self):
        self.race(BASE + STEP, BASE + STEP, None, conflict=False)

    def test_concurrent_overlaps_for_different_items_both_commit(self):
        self.race(None, BASE, None, different_item=True, conflict=False)


class LocationServiceConcurrencyTests(TwoLocationWritersMixin, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.product, self.points = fixtures()
        self.point_a, self.point_b, self.point_c = self.points
        self.item = new_item(self.product, "location-service-race", self.point_a)

    def service_race(self, first_point, second_point):
        # Both calls receive an instance from before either move. The service
        # must lock and reload, rather than trusting these cached attributes.
        stale = ProductItem.objects.select_related("pickup_point").get(pk=self.item.pk)
        first_point_id = None if first_point is None else first_point.pk
        second_clock_seen = Event()

        def clock():
            if current_thread().name == "location-second":
                # This is also an observable check that the service obtains its
                # clock only after the item lock: a pre-lock read would still
                # see the old committed location A while writer one holds B.
                if not second_clock_seen.is_set():
                    current = ProductItem.objects.get(pk=self.item.pk)
                    self.assertEqual(current.pickup_point_id, first_point_id)
                    second_clock_seen.set()
                return BASE + 2 * STEP
            if current_thread().name == "location-first":
                return BASE + STEP
            return BASE

        def action(point):
            def move(cursor):
                result = set_location(stale, point)
                return {
                    "pickup_point_id": result.pickup_point_id,
                    "updated_at": result.updated_at,
                    "status_changed_at": result.status_changed_at,
                }
            return move

        with patch.object(services.timezone, "now", side_effect=clock):
            results = self.two_writers(action(first_point), action(second_point))
        self.item.refresh_from_db()
        opened = list(ProductItemLocationHistory.objects.filter(
            product_item_id=self.item.pk, valid_to__isnull=True
        ))
        if self.item.pickup_point_id is None:
            self.assertEqual(opened, [])
        else:
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0].pickup_point_id, self.item.pickup_point_id)
        self.assertEqual(self.item.status, "unavailable")
        self.assertEqual(self.item.inventory_code, "location-service-race")
        self.assertEqual(self.item.status_changed_at, BASE)
        return results, second_clock_seen.is_set()

    def test_two_moves_on_one_item_serialize_and_preserve_all_three_periods(self):
        _, clock_seen = self.service_race(self.point_b, self.point_c)
        self.assertTrue(clock_seen)
        self.assertEqual(self.item.pickup_point_id, self.point_c.pk)
        self.assertEqual(history_of(self.item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_b.pk, BASE + STEP, BASE + 2 * STEP),
            (self.point_c.pk, BASE + 2 * STEP, None),
        ])

    def test_waiting_move_to_the_new_current_point_becomes_a_true_noop(self):
        results, _ = self.service_race(self.point_b, self.point_b)
        self.assertEqual(results["second"][1], results["first"][1])
        self.assertEqual(history_of(self.item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_b.pk, BASE + STEP, None),
        ])

    def test_waiting_assignment_after_clear_preserves_the_null_location_gap(self):
        _, clock_seen = self.service_race(None, self.point_c)
        self.assertTrue(clock_seen)
        self.assertEqual(self.item.pickup_point_id, self.point_c.pk)
        self.assertEqual(history_of(self.item), [
            (self.point_a.pk, BASE, BASE + STEP),
            (self.point_c.pk, BASE + 2 * STEP, None),
        ])
