"""Tariff history and PostgreSQL exclusion, including concurrent writers.

Raw INSERTs deliberately bypass model validation and its parent-row locks so
the database constraints are tested independently of the public ORM guards.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    NotSupportedError,
    connection,
    connections,
    transaction,
)
from django.test import TestCase, TransactionTestCase

from .models import Product, ProductCategory, ProductTariff


START = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
RATE = Decimal("125.50")


def insert_tariff(cursor, product_id, valid_from, valid_to, rate=RATE):
    """Insert only approved ERD columns, bypassing all Python model hooks."""
    cursor.execute(
        "INSERT INTO product_tariff "
        "(product_id, hourly_rate, valid_from, valid_to, created_at) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        [product_id, rate, valid_from, valid_to, START],
    )
    return cursor.fetchone()[0]


def sqlstate(error):
    cause = error.__cause__
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


def create_products():
    category = ProductCategory.objects.create(name="Tariff test category")
    return tuple(
        Product.objects.create(
            category=category,
            name=f"Tariff test product {number}",
            description="Synthetic tariff test fixture",
            status="fixture-status",
        )
        for number in (1, 2)
    )


class ProductTariffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product, cls.other_product = create_products()

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")

    def create_tariff(self, **overrides):
        values = {
            "product": self.product,
            "hourly_rate": RATE,
            "valid_from": START,
            "valid_to": START + HOUR,
        }
        values.update(overrides)
        return ProductTariff.objects.create(**values)

    def assert_database_rejects(self, *, constraint, state, **overrides):
        values = {
            "product_id": self.product.pk,
            "valid_from": START,
            "valid_to": START + HOUR,
            "rate": RATE,
        }
        values.update(overrides)
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic(), connection.cursor() as cursor:
                insert_tariff(cursor, **values)
        self.assertEqual(sqlstate(raised.exception), state)
        self.assertEqual(
            raised.exception.__cause__.diag.constraint_name, constraint
        )

    def test_erd_columns_and_numeric_precision_are_preserved(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, numeric_precision, "
                "numeric_scale FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='product_tariff'"
            )
            columns = {row[0]: row[1:] for row in cursor.fetchall()}
        self.assertEqual(set(columns), {
            "id", "product_id", "hourly_rate", "valid_from", "valid_to",
            "created_at",
        })
        self.assertEqual(columns["id"][:2], ("bigint", "NO"))
        self.assertEqual(columns["product_id"][:2], ("bigint", "NO"))
        self.assertEqual(columns["hourly_rate"], ("numeric", "NO", 12, 2))
        for name in ("valid_from", "created_at"):
            self.assertEqual(
                columns[name][:2], ("timestamp with time zone", "NO")
            )
        self.assertEqual(
            columns["valid_to"][:2], ("timestamp with time zone", "YES")
        )

    def test_positive_finite_rate_is_stored_without_float_conversion(self):
        tariff = self.create_tariff(hourly_rate=Decimal("9999999999.99"))
        tariff.refresh_from_db()
        self.assertIsInstance(tariff.pk, int)
        self.assertEqual(tariff.hourly_rate, Decimal("9999999999.99"))
        self.assertEqual(tariff.product_id, self.product.pk)
        self.assertEqual(tariff.valid_from, START)
        self.assertEqual(tariff.valid_to, START + HOUR)
        self.assertIsNotNone(tariff.created_at)

    def test_adjacent_finite_periods_are_allowed_in_both_insert_orders(self):
        for product, reverse in ((self.product, False), (self.other_product, True)):
            intervals = [(START, START + HOUR), (START + HOUR, START + 2 * HOUR)]
            for lower, upper in reversed(intervals) if reverse else intervals:
                self.create_tariff(product=product, valid_from=lower, valid_to=upper)
        self.assertEqual(ProductTariff.objects.count(), 4)

    def test_open_period_can_follow_a_closed_period_at_exact_boundary(self):
        self.create_tariff()
        current = self.create_tariff(valid_from=START + HOUR, valid_to=None)
        current.refresh_from_db()
        self.assertIsNone(current.valid_to)
        self.assertEqual(ProductTariff.objects.count(), 2)

    def test_overlapping_period_shapes_are_rejected_by_public_creation(self):
        self.create_tariff(valid_to=START + 4 * HOUR)
        intervals = [
            (START, START + 4 * HOUR),
            (START + HOUR, START + 2 * HOUR),
            (START - HOUR, START + 5 * HOUR),
            (START - HOUR, START + HOUR),
            (START + 3 * HOUR, START + 5 * HOUR),
        ]
        for lower, upper in intervals:
            with self.subTest(lower=lower, upper=upper):
                with self.assertRaises(ValidationError):
                    self.create_tariff(valid_from=lower, valid_to=upper)
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_open_period_rejects_future_finite_and_open_successors(self):
        self.create_tariff(valid_to=None)
        for upper in (START + 3 * HOUR, None):
            with self.subTest(valid_to=upper), self.assertRaises(ValidationError):
                self.create_tariff(valid_from=START + 2 * HOUR, valid_to=upper)
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_overlapping_periods_for_different_products_are_allowed(self):
        first = self.create_tariff(valid_to=None)
        second = self.create_tariff(product=self.other_product, valid_to=None)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(ProductTariff.objects.count(), 2)

    def test_public_creation_rejects_zero_negative_and_nonfinite_rates(self):
        for value in ("0", "-0.01", "NaN", "Infinity", "-Infinity"):
            with self.subTest(rate=value), self.assertRaises(ValidationError):
                self.create_tariff(hourly_rate=Decimal(value))
        self.assertEqual(ProductTariff.objects.count(), 0)

    def test_public_creation_rejects_empty_and_reversed_periods(self):
        for upper in (START, START - HOUR):
            with self.subTest(valid_to=upper), self.assertRaises(ValidationError):
                self.create_tariff(valid_to=upper)
        self.assertEqual(ProductTariff.objects.count(), 0)

    def test_database_independently_rejects_zero_negative_and_nan_rates(self):
        for value in ("0", "-0.01", "NaN"):
            with self.subTest(rate=value):
                self.assert_database_rejects(
                    rate=Decimal(value),
                    state="23514",
                    constraint="product_tariff_rate_positive_finite",
                )
        self.assertEqual(ProductTariff.objects.count(), 0)

    def test_database_independently_rejects_empty_and_reversed_periods(self):
        for upper in (START, START - HOUR):
            with self.subTest(valid_to=upper):
                self.assert_database_rejects(
                    valid_to=upper,
                    state="23514",
                    constraint="product_tariff_valid_period",
                )
        self.assertEqual(ProductTariff.objects.count(), 0)

    def test_database_independently_rejects_finite_overlap(self):
        self.create_tariff(valid_to=START + 2 * HOUR)
        self.assert_database_rejects(
            valid_from=START + HOUR,
            valid_to=START + 3 * HOUR,
            state="23P01",
            constraint="product_tariff_no_overlap",
        )
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_database_independently_rejects_two_unbounded_periods(self):
        self.create_tariff(valid_to=None)
        self.assert_database_rejects(
            valid_from=START + HOUR,
            valid_to=None,
            state="23P01",
            constraint="product_tariff_no_overlap",
        )
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_existing_product_rate_start_and_creation_time_are_immutable(self):
        tariff = self.create_tariff()
        original = {
            "product_id": tariff.product_id,
            "hourly_rate": tariff.hourly_rate,
            "valid_from": tariff.valid_from,
            "created_at": tariff.created_at,
        }
        changed = {
            "product_id": self.other_product.pk,
            "hourly_rate": RATE + Decimal("1.00"),
            "valid_from": START - HOUR,
            "created_at": tariff.created_at + HOUR,
        }
        for field, value in changed.items():
            with self.subTest(field=field):
                setattr(tariff, field, value)
                with self.assertRaises(ValidationError):
                    tariff.save(update_fields=[field])
                tariff.refresh_from_db()
                for name, previous in original.items():
                    self.assertEqual(getattr(tariff, name), previous)

    def test_open_period_can_be_closed_once_then_successor_created(self):
        tariff = self.create_tariff(valid_to=None)
        tariff.valid_to = START + HOUR
        tariff.save(update_fields=["valid_to"])
        tariff.refresh_from_db()
        self.assertEqual(tariff.valid_to, START + HOUR)
        self.create_tariff(valid_from=START + HOUR, valid_to=None)
        self.assertEqual(ProductTariff.objects.count(), 2)

    def test_closed_period_cannot_be_reopened_shortened_or_extended(self):
        tariff = self.create_tariff(valid_to=START + 2 * HOUR)
        for upper in (None, START + HOUR, START + 3 * HOUR):
            with self.subTest(valid_to=upper):
                tariff.valid_to = upper
                with self.assertRaises(ValidationError):
                    tariff.save(update_fields=["valid_to"])
                tariff.refresh_from_db()
                self.assertEqual(tariff.valid_to, START + 2 * HOUR)

    def test_invalid_first_close_does_not_change_an_open_period(self):
        tariff = self.create_tariff(valid_to=None)
        for upper in (START, START - HOUR):
            with self.subTest(valid_to=upper):
                tariff.valid_to = upper
                with self.assertRaises(ValidationError):
                    tariff.save(update_fields=["valid_to"])
                tariff.refresh_from_db()
                self.assertIsNone(tariff.valid_to)

    def test_instance_and_queryset_deletion_cannot_remove_tariff_history(self):
        tariff = self.create_tariff()
        for delete in (tariff.delete, ProductTariff.objects.filter(pk=tariff.pk).delete):
            with self.subTest(operation=delete), self.assertRaises(NotSupportedError):
                delete()
            self.assertTrue(ProductTariff.objects.filter(pk=tariff.pk).exists())

    def test_update_and_bulk_methods_cannot_bypass_tariff_guards(self):
        tariff = self.create_tariff()
        tariff.hourly_rate = Decimal("1.00")
        candidate = ProductTariff(
            product=self.product,
            hourly_rate=RATE,
            valid_from=START + HOUR,
            valid_to=START + 2 * HOUR,
        )
        operations = {
            "update": lambda: ProductTariff.objects.filter(pk=tariff.pk).update(
                hourly_rate=Decimal("1.00")
            ),
            "bulk_create": lambda: ProductTariff.objects.bulk_create([candidate]),
            "bulk_update": lambda: ProductTariff.objects.bulk_update(
                [tariff], ["hourly_rate"]
            ),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name), self.assertRaises(NotSupportedError):
                operation()
        tariff.refresh_from_db()
        self.assertEqual(tariff.hourly_rate, RATE)
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_reverse_related_writes_cannot_reassign_a_tariff_version(self):
        tariff = self.create_tariff()
        original_product_id = tariff.product_id
        operations = (
            (
                "add-default-bulk",
                lambda: self.other_product.tariffs.add(tariff),
                NotSupportedError,
            ),
            (
                "set-default-bulk",
                lambda: self.other_product.tariffs.set([tariff]),
                NotSupportedError,
            ),
            (
                "add-through-save",
                lambda: self.other_product.tariffs.add(tariff, bulk=False),
                ValidationError,
            ),
        )
        for name, operation, expected_error in operations:
            with self.subTest(operation=name):
                with self.assertRaises(expected_error), transaction.atomic():
                    operation()
                tariff.refresh_from_db()
                self.assertEqual(tariff.product_id, original_product_id)
                self.assertEqual(ProductTariff.objects.count(), 1)


class ProductTariffConcurrencyTests(TransactionTestCase):
    """Committed fixtures and separate connections exercise the actual GiST lock."""

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.product, self.other_product = create_products()

    def assert_concurrent_inserts(self, *, first_end, second_start, second_end,
                                  different_product=False, conflict=True):
        first_inserted = Event()
        second_ready = Event()
        observed_blocking = Event()
        poll_delay = Event()
        results = Queue()
        backend_ids = {}
        second_product_id = (
            self.other_product.pk if different_product else self.product.pk
        )

        def worker(role):
            worker_connection = connections["default"]
            try:
                if role == "second" and not first_inserted.wait(timeout=5):
                    raise AssertionError("First writer did not insert in time.")
                with transaction.atomic(using="default"):
                    with worker_connection.cursor() as cursor:
                        cursor.execute("SET LOCAL statement_timeout = '8s'")
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_ids[role] = cursor.fetchone()[0]
                        if role == "first":
                            row_id = insert_tariff(
                                cursor, self.product.pk, START, first_end
                            )
                            first_inserted.set()
                            # A barrier after INSERT would deadlock the losing
                            # writer, whose GiST check may await this commit.
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("Second writer was not ready.")
                            if conflict:
                                deadline = monotonic() + 3
                                while True:
                                    cursor.execute(
                                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                                        [backend_ids["first"], backend_ids["second"]],
                                    )
                                    if cursor.fetchone()[0]:
                                        observed_blocking.set()
                                        break
                                    remaining = deadline - monotonic()
                                    if remaining <= 0:
                                        raise AssertionError(
                                            "Second writer never blocked on the "
                                            "uncommitted first writer."
                                        )
                                    poll_delay.wait(timeout=min(0.02, remaining))
                        else:
                            # Publish our PID before the first writer begins
                            # observing the competing INSERT's actual lock wait.
                            second_ready.set()
                            row_id = insert_tariff(
                                cursor, second_product_id, second_start, second_end
                            )
                results.put((role, "committed", row_id))
            except IntegrityError as error:
                results.put((role, "integrity", sqlstate(error)))
            except Exception as error:
                results.put((role, "error", repr(error)))
            finally:
                worker_connection.close()

        threads = [
            Thread(target=worker, args=(role,), name=f"tariff-{role}", daemon=True)
            for role in ("first", "second")
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            # Release a coordinator even if a test assertion/worker failed.
            second_ready.set()
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        outcomes = {}
        for _ in threads:
            try:
                role, result, detail = results.get(timeout=1)
            except Empty:
                self.fail("A tariff worker exited without recording its outcome.")
            outcomes[role] = (result, detail)
        self.assertEqual(set(outcomes), {"first", "second"})
        self.assertEqual(outcomes["first"][0], "committed", outcomes)
        pids = list(backend_ids.values())
        self.assertEqual(len(set(pids)), 2, "Workers must use separate connections.")
        if conflict:
            self.assertTrue(
                observed_blocking.is_set(),
                "The first writer must observe the actual competing lock wait.",
            )
            self.assertEqual(outcomes["second"], ("integrity", "23P01"), outcomes)
            self.assertEqual(ProductTariff.objects.count(), 1)
        else:
            self.assertEqual(outcomes["second"][0], "committed", outcomes)
            self.assertEqual(ProductTariff.objects.count(), 2)

    def test_concurrent_finite_overlap_has_exactly_one_winner(self):
        self.assert_concurrent_inserts(
            first_end=START + 2 * HOUR,
            second_start=START + HOUR,
            second_end=START + 3 * HOUR,
        )

    def test_concurrent_unbounded_overlap_has_exactly_one_winner(self):
        self.assert_concurrent_inserts(
            first_end=None, second_start=START + HOUR, second_end=None
        )

    def test_concurrent_adjacent_periods_both_commit(self):
        self.assert_concurrent_inserts(
            first_end=START + HOUR,
            second_start=START + HOUR,
            second_end=None,
            conflict=False,
        )

    def test_concurrent_overlaps_for_different_products_both_commit(self):
        self.assert_concurrent_inserts(
            first_end=None,
            second_start=START,
            second_end=None,
            different_product=True,
            conflict=False,
        )
