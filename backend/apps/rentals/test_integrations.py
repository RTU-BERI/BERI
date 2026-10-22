"""Catalog/Inventory history guards using test-only SQL event fixtures.

The explicit fixture locks below exercise the compatible persistence protocol.
They do not implement a production observation, issue, return or payment flow.
"""

from datetime import timedelta
from decimal import Decimal
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.db.utils import NotSupportedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.catalog.models import ProductComponent, ProductTariff
from apps.inventory.models import ProductItem
from apps.inventory.services import clear_location, set_location

from .models import Inspection, Rental
from .test_support import FixtureMixin


def raw_update(model, pk, **values):
    columns = {field.column for field in model._meta.concrete_fields}
    if not values or not set(values).issubset(columns):
        raise AssertionError("Unexpected test-fixture column")
    quote = connection.ops.quote_name
    assignments = ", ".join(f"{quote(name)}=%s" for name in values)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {quote(model._meta.db_table)} SET {assignments} "
            f"WHERE {quote(model._meta.pk.column)}=%s", [*values.values(), pk],
        )


class IntegrationGuardTests(FixtureMixin, TestCase):
    def reject_unchanged(self, action, error=ValidationError):
        before = self.snapshot()
        with self.assertRaises(error):
            with transaction.atomic():
                action()
        self.assertEqual(self.snapshot(), before)

    def test_components_keep_individual_create_edit_reparent_delete_before_inspection(self):
        component = ProductComponent.objects.create(
            product=self.product, name=self.component.name, quantity=1,
        )
        component.name = "Changed synthetic component"
        component.quantity = 3
        component.product = self.other_product
        component.save()
        component.refresh_from_db()
        self.assertEqual(component.quantity, 3)
        self.assertEqual(component.product_id, self.other_product.pk)
        component.delete()
        self.assertIsNone(component.pk)

    def test_healthy_inspection_freezes_the_whole_component_set(self):
        self.make_inspection()
        self.reject_unchanged(lambda: ProductComponent.objects.create(
            product=self.product, name="Late component", quantity=1,
        ))
        for field, value in (("name", "Late rename"), ("quantity", 3),
                             ("product", self.other_product)):
            component = ProductComponent.objects.get(pk=self.component.pk)
            setattr(component, field, value)
            with self.subTest(field=field):
                self.reject_unchanged(component.save)
        self.reject_unchanged(self.component.delete)

    def test_freeze_does_not_require_a_problem_referring_to_the_component(self):
        inspection = self.make_inspection()
        self.assertFalse(inspection.problems.exists())
        self.assertFalse(self.component.inspection_problems.exists())
        self.reject_unchanged(self.component.delete)

    def test_empty_component_set_is_also_frozen_after_first_inspection(self):
        self.component.delete()
        self.make_inspection()
        self.reject_unchanged(lambda: ProductComponent.objects.create(
            product=self.product, name="Invented after observation", quantity=1,
        ))

    def test_component_cannot_enter_an_inspected_product_from_another_product(self):
        other = ProductComponent.objects.create(
            product=self.other_product, name="Other component", quantity=1,
        )
        self.make_inspection()
        other.product = self.product
        self.reject_unchanged(other.save)

    def test_stale_component_and_existing_pk_constructor_cannot_bypass_freeze(self):
        stale = ProductComponent.objects.get(pk=self.component.pk)
        self.make_inspection()
        stale.quantity = 3
        self.reject_unchanged(stale.save)
        replacement = ProductComponent(
            pk=self.component.pk, product=self.product,
            name=self.component.name, quantity=4,
        )
        self.reject_unchanged(replacement.save)

    def test_partial_component_save_cannot_escape_frozen_source_product(self):
        self.make_inspection()
        self.component.product = self.other_product
        self.component.name = "Dirty partial rename"
        self.reject_unchanged(lambda: self.component.save(update_fields=["name"]))

    def test_component_bulk_queryset_delete_and_base_manager_writes_are_closed(self):
        self.component.quantity = 3
        attempts = (
            lambda: ProductComponent.objects.filter(pk=self.component.pk).update(quantity=3),
            lambda: ProductComponent._base_manager.filter(pk=self.component.pk).update(quantity=3),
            lambda: ProductComponent.objects.bulk_create([
                ProductComponent(product=self.product, name="Bulk component", quantity=1)
            ]),
            lambda: ProductComponent.objects.bulk_update([self.component], ["quantity"]),
            lambda: ProductComponent.objects.filter(pk=self.component.pk).delete(),
        )
        for operation in attempts:
            self.reject_unchanged(operation, NotSupportedError)

    def test_reverse_component_add_is_guarded_for_bulk_and_individual_paths(self):
        self.make_inspection()
        self.reject_unchanged(
            lambda: self.other_product.components.add(self.component), NotSupportedError,
        )
        self.component.refresh_from_db()
        self.reject_unchanged(
            lambda: self.other_product.components.add(self.component, bulk=False),
        )

    def test_unrelated_uninspected_product_remains_editable(self):
        self.make_inspection()
        component = ProductComponent.objects.create(
            product=self.other_product, name="Independent component", quantity=1,
        )
        component.quantity = 2
        component.save()
        component.delete()
        self.assertFalse(ProductComponent.objects.filter(product=self.other_product).exists())

    def test_tariff_cannot_close_at_or_before_a_recorded_rental_start(self):
        rental = self.make_rental()
        for end in (rental.started_at, rental.started_at - timedelta(microseconds=1)):
            tariff = ProductTariff.objects.get(pk=self.tariff.pk)
            tariff.valid_to = end
            with self.subTest(end=end):
                self.reject_unchanged(lambda: tariff.save(update_fields=["valid_to"]))

    def test_tariff_can_close_strictly_after_all_recorded_starts(self):
        rental = self.make_rental(status="completed")
        self.tariff.refresh_from_db()
        self.tariff.valid_to = rental.started_at + timedelta(microseconds=1)
        self.tariff.save(update_fields=["valid_to"])
        self.tariff.refresh_from_db()
        self.assertGreater(self.tariff.valid_to, rental.started_at)
        self.assertEqual(rental.hourly_rate_applied, Decimal("12.00"))

    def test_dirty_partial_tariff_close_cannot_bypass_historical_coverage(self):
        rental = self.make_rental()
        self.tariff.valid_to = rental.started_at
        self.reject_unchanged(lambda: self.tariff.save(update_fields=["hourly_rate"]))

    def test_rented_status_blocks_actual_move_and_clear_without_an_open_assignment(self):
        raw_update(ProductItem, self.item.pk, status="rented")
        for destination in (self.other_point, None):
            with self.subTest(destination=destination):
                self.reject_unchanged(lambda: set_location(self.item, destination))

    def test_active_rental_blocks_move_even_if_item_status_was_corrupted(self):
        self.make_rental()
        raw_update(ProductItem, self.item.pk, status="available")
        for destination in (self.other_point, None):
            with self.subTest(destination=destination):
                self.reject_unchanged(lambda: set_location(self.item, destination))

    def test_same_point_noop_is_preserved_for_a_rented_item(self):
        self.make_rental()
        before = self.snapshot()
        result = set_location(self.item, self.point)
        self.assertEqual(result.status, "rented")
        self.assertEqual(self.snapshot(), before)

    def test_completed_rental_does_not_forbid_later_placement(self):
        self.make_rental(status="completed")
        moved = set_location(self.item, self.other_point)
        self.assertEqual(moved.pickup_point_id, self.other_point.pk)
        cleared = clear_location(moved)
        self.assertIsNone(cleared.pickup_point_id)

    def test_inspection_reference_alone_prevents_item_product_rewrite(self):
        item = self.make_item()
        # Isolate the historical FK guard from Assignment. This deliberately
        # synthetic inspection is not a successful business inspection service.
        self.make_inspection(product_item=item)
        item.product = self.other_product
        self.reject_unchanged(lambda: item.save(update_fields=["product"]))

    def test_rental_reference_alone_prevents_item_product_rewrite(self):
        item = self.make_item()
        # Deliberate SQL fixture isolates Rental history from Assignment history.
        self.make_rental(product_item=item)
        item.product = self.other_product
        self.reject_unchanged(lambda: item.save(update_fields=["product"]))


class IntegrationLockTests(FixtureMixin, TransactionTestCase):
    def insert_locked_inspection_fixture(self, cursor):
        """Test-only first event; serialize with component Product UPDATE."""
        cursor.execute(
            "SELECT id FROM product WHERE id=%s FOR SHARE", [self.product.pk]
        )
        self.assertEqual(cursor.fetchone(), (self.product.pk,))
        return self.make_inspection().pk

    def two_writers(self, first_action, second_action):
        first_written, second_ready, observed_wait = Event(), Event(), Event()
        delay, outcomes, pids = Event(), Queue(), {}

        def writer(role, action):
            worker = connections["default"]
            try:
                if role == "second" and not first_written.wait(timeout=5):
                    raise AssertionError("First integration writer was not ready.")
                with transaction.atomic():
                    with worker.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout='5s'")
                        cursor.execute("SET LOCAL statement_timeout='8s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pids[role] = cursor.fetchone()[0]
                        if role == "second":
                            second_ready.set()
                        value = action(cursor)
                        if role == "first":
                            first_written.set()
                            if not second_ready.wait(timeout=5):
                                raise AssertionError("Second integration writer was not ready.")
                            deadline = monotonic() + 3
                            while True:
                                cursor.execute("SELECT %s=ANY(pg_blocking_pids(%s))",
                                               [pids["first"], pids["second"]])
                                if cursor.fetchone()[0]:
                                    observed_wait.set()
                                    break
                                left = deadline - monotonic()
                                if left <= 0:
                                    raise AssertionError("Expected integration lock was not observed.")
                                delay.wait(min(0.02, left))
                outcomes.put((role, "committed", value))
            except ValidationError as error:
                outcomes.put((role, "validation", type(error).__name__))
            except Exception as error:
                outcomes.put((role, "error", type(error).__name__))
            finally:
                worker.close()

        threads = [Thread(target=writer, args=(role, action), daemon=True)
                   for role, action in (("first", first_action), ("second", second_action))]
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
        result = {}
        for _ in threads:
            try:
                role, kind, value = outcomes.get(timeout=1)
            except Empty:
                self.fail("An integration writer did not publish its outcome.")
            result[role] = (kind, value)
        self.assertEqual(result["first"][0], "committed", result)
        self.assertEqual(len(set(pids.values())), 2)
        self.assertTrue(observed_wait.is_set())
        return result

    def test_first_inspection_holds_product_against_component_edit_and_freezes_after_commit(self):
        def edit(cursor):
            component = ProductComponent.objects.get(pk=self.component.pk)
            component.quantity = 3
            component.save()
            return component.pk

        result = self.two_writers(self.insert_locked_inspection_fixture, edit)
        self.assertEqual(result["second"], ("validation", "ValidationError"))
        self.component.refresh_from_db()
        self.assertEqual(self.component.quantity, 2)

    def test_component_edit_commits_before_waiting_first_inspection(self):
        def edit(cursor):
            component = ProductComponent.objects.get(pk=self.component.pk)
            component.quantity = 3
            component.save()
            return component.pk

        result = self.two_writers(edit, self.insert_locked_inspection_fixture)
        self.assertEqual(result["second"][0], "committed", result)
        self.component.refresh_from_db()
        self.assertEqual(self.component.quantity, 3)
        self.assertEqual(Inspection.objects.count(), 1)

    def test_tariff_close_waits_for_synthetic_rental_and_preserves_historical_coverage(self):
        method = self.make_method(confirmed=True)
        event_time = timezone.now() + timedelta(seconds=1)

        def synthetic_rental(cursor):
            ProductTariff.objects.select_for_update().get(pk=self.tariff.pk)
            return self.make_rental(method=method).pk

        def close_tariff(cursor):
            tariff = ProductTariff.objects.get(pk=self.tariff.pk)
            tariff.valid_to = event_time
            tariff.save(update_fields=["valid_to"])
            return tariff.pk

        with patch.object(timezone, "now", return_value=event_time):
            result = self.two_writers(synthetic_rental, close_tariff)
        self.assertEqual(result["second"], ("validation", "ValidationError"))
        self.tariff.refresh_from_db()
        self.assertIsNone(self.tariff.valid_to)
        self.assertEqual(Rental.objects.count(), 1)
