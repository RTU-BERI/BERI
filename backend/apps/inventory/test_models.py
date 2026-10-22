"""Inventory model boundaries and the real PostgreSQL structure."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Product, ProductCategory
from apps.pickup_points.models import PickupPoint

from .models import ProductItem, ProductItemLocationHistory
from .services import create_item


class InventorySchemaTests(TestCase):
    def test_two_tables_have_exact_fourteen_columns_and_nullability(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "product_item": {
                "id": ("bigint", "NO", None),
                "product_id": ("bigint", "NO", None),
                "pickup_point_id": ("bigint", "YES", None),
                "inventory_code": ("character varying", "NO", 100),
                "status": ("character varying", "NO", 20),
                "status_reason": ("text", "YES", None),
                "status_changed_at": ("timestamp with time zone", "NO", None),
                "created_at": ("timestamp with time zone", "NO", None),
                "updated_at": ("timestamp with time zone", "NO", None),
            },
            "product_item_location_history": {
                "id": ("bigint", "NO", None),
                "product_item_id": ("bigint", "NO", None),
                "pickup_point_id": ("bigint", "NO", None),
                "valid_from": ("timestamp with time zone", "NO", None),
                "valid_to": ("timestamp with time zone", "YES", None),
            },
        }
        for table, columns in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type, is_nullable, "
                    "character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s",
                    [table],
                )
                actual = {
                    name: (kind, nullable, length)
                    for name, kind, nullable, length in cursor.fetchall()
                }
                self.assertEqual(actual, columns)

    def test_both_primary_keys_are_independent_bigint_identity_columns(self):
        for model in (ProductItem, ProductItemLocationHistory):
            with self.subTest(model=model.__name__), connection.cursor() as cursor:
                table = model._meta.db_table
                cursor.execute(
                    "SELECT data_type, is_identity "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s "
                    "AND column_name='id'",
                    [table],
                )
                self.assertEqual(cursor.fetchone(), ("bigint", "YES"))
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    [c["columns"] for c in constraints.values() if c["primary_key"]],
                    [["id"]],
                )

    def test_all_four_foreign_keys_match_the_erd(self):
        expected = {
            "product_item": {
                ("product_id", "product", "id"),
                ("pickup_point_id", "pickup_point", "id"),
            },
            "product_item_location_history": {
                ("product_item_id", "product_item", "id"),
                ("pickup_point_id", "pickup_point", "id"),
            },
        }
        for table, foreign_keys in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                actual = {
                    (c["columns"][0], *c["foreign_key"])
                    for c in constraints.values()
                    if c["foreign_key"]
                }
                self.assertEqual(actual, foreign_keys)

    def test_inventory_code_has_a_single_column_global_unique_constraint(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "product_item"
            )
        self.assertIn(
            ["inventory_code"],
            [c["columns"] for c in constraints.values() if c["unique"]],
        )

    def test_five_statuses_are_choices_without_a_product_default_or_enum(self):
        field = ProductItem._meta.get_field("status")
        self.assertEqual(
            {value for value, label in field.choices},
            {"available", "reserved", "rented", "unavailable", "retired"},
        )
        self.assertFalse(field.has_default())
        self.assertFalse(field.has_db_default())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='product_item' "
                "AND column_name='status'"
            )
            self.assertEqual(cursor.fetchone(), ("character varying", None))

    def test_status_and_history_bounds_have_postgresql_check_constraints(self):
        for table, name in (
            ("product_item", "product_item_status_valid"),
            (
                "product_item_location_history",
                "product_item_location_valid_period",
            ),
        ):
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT contype FROM pg_constraint "
                    "WHERE conrelid=%s::regclass AND conname=%s",
                    [table, name],
                )
                self.assertEqual(cursor.fetchone(), ("c",))

    def test_history_has_the_approved_postgresql_range_exclusion(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT contype, pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conrelid='product_item_location_history'::regclass "
                "AND conname='product_item_location_no_overlap'"
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "x")
        for fragment in ("gist", "product_item_id", "tstzrange", "[)", "&&"):
            self.assertIn(fragment, row[1].lower())

    def test_all_four_relationships_use_protect_including_nullable_current_point(self):
        for model, field_name in (
            (ProductItem, "product"),
            (ProductItem, "pickup_point"),
            (ProductItemLocationHistory, "product_item"),
            (ProductItemLocationHistory, "pickup_point"),
        ):
            with self.subTest(model=model.__name__, field=field_name):
                field = model._meta.get_field(field_name)
                self.assertIs(field.remote_field.on_delete, models.PROTECT)


class InventoryModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = ProductCategory.objects.create(name="Inventory fixture")
        cls.product = Product.objects.create(
            category=category,
            name="Inventory fixture product",
            description="Synthetic model fixture",
            status="inventory-fixture",
        )
        cls.other_product = Product.objects.create(
            category=category,
            name="Other inventory fixture product",
            description="Synthetic model fixture",
            status="inventory-fixture",
        )
        cls.point = PickupPoint.objects.create(
            name="Inventory fixture point",
            address="Synthetic address",
            latitude=Decimal("55.000000"),
            longitude=Decimal("37.000000"),
            status="inventory-fixture",
        )
        cls.other_point = PickupPoint.objects.create(
            name="Other inventory fixture point",
            address="Other synthetic address",
            latitude=Decimal("56.000000"),
            longitude=Decimal("38.000000"),
            status="inventory-fixture",
        )

    def item(self, code="inventory-fixture", **overrides):
        values = {
            "product": self.product,
            "inventory_code": code,
            "status": "unavailable",
        }
        values.update(overrides)
        return ProductItem.objects.create(**values)

    def placed_item(self, code="placed-inventory-fixture"):
        return create_item(
            product=self.product,
            inventory_code=code,
            status="unavailable",
            pickup_point=self.point,
        )

    def insert_raw(self, table, values):
        quote = connection.ops.quote_name
        columns = ", ".join(quote(name) for name in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {quote(table)} ({columns}) "
                f"VALUES ({placeholders}) RETURNING id",
                list(values.values()),
            )
            return cursor.fetchone()[0]

    def raw_item_values(self, code="raw-inventory-fixture", **overrides):
        now = timezone.now()
        values = {
            "product_id": self.product.pk,
            "pickup_point_id": None,
            "inventory_code": code,
            "status": "unavailable",
            "status_changed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        values.update(overrides)
        return values

    def test_public_creation_accepts_three_initial_statuses_and_rejects_two(self):
        for status in ("available", "unavailable", "retired"):
            with self.subTest(status=status):
                item = self.item(f"initial-{status}", status=status)
                item.refresh_from_db()
                self.assertEqual(item.status, status)
                self.assertIsInstance(item.pk, int)
        for status in ("reserved", "rented"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.item(f"forbidden-initial-{status}", status=status)
        self.assertEqual(ProductItem.objects.count(), 3)

    def test_creation_requires_an_explicit_status(self):
        with self.assertRaises(ValidationError):
            ProductItem.objects.create(
                product=self.product, inventory_code="missing-status-fixture"
            )
        self.assertFalse(ProductItem.objects.exists())

    def test_status_changed_at_is_server_assigned_and_timezone_aware(self):
        before = timezone.now()
        item = self.item()
        after = timezone.now()
        item.refresh_from_db()
        for stamp in (item.status_changed_at, item.created_at, item.updated_at):
            self.assertTrue(timezone.is_aware(stamp))
        self.assertLessEqual(before, item.status_changed_at)
        self.assertLessEqual(item.status_changed_at, after)

    def test_creation_cannot_supply_its_own_status_timestamp(self):
        with self.assertRaises(ValidationError):
            self.item(status_changed_at=timezone.now() - timedelta(days=1))
        self.assertFalse(ProductItem.objects.exists())

    def test_empty_partial_save_does_not_poison_later_initial_creation(self):
        item = ProductItem(
            product=self.product, inventory_code="noop-before-create",
            status="unavailable",
        )
        item.save(update_fields=[])
        self.assertIsNone(item.pk)
        self.assertIsNone(item.status_changed_at)
        self.assertFalse(ProductItem.objects.exists())
        item.save()
        item.refresh_from_db()
        self.assertIsNotNone(item.status_changed_at)

    def test_failed_initial_validation_can_be_corrected_and_saved(self):
        item = ProductItem(
            product=self.product, inventory_code="", status="unavailable"
        )
        with self.assertRaises(ValidationError):
            item.save()
        self.assertIsNone(item.pk)
        self.assertIsNone(item.status_changed_at)
        item.inventory_code = "fixed-after-validation"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.inventory_code, "fixed-after-validation")

    def test_public_save_cannot_change_status_or_its_timestamp(self):
        item = self.item()
        original = item.status_changed_at
        for field, changed in (
            ("status", "available"),
            ("status_changed_at", original - timedelta(days=1)),
            ("created_at", item.created_at - timedelta(days=1)),
        ):
            with self.subTest(field=field):
                setattr(item, field, changed)
                with self.assertRaises(ValidationError):
                    item.save()
                item.refresh_from_db()
                self.assertEqual(item.status, "unavailable")
                self.assertEqual(item.status_changed_at, original)

    def test_public_save_cannot_change_inventory_code(self):
        item = self.item()
        item.inventory_code = "rewritten-fixture-code"
        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        self.assertEqual(item.inventory_code, "inventory-fixture")

    def test_reason_edit_preserves_status_and_its_timestamp(self):
        item = self.item(status_reason=None)
        original = item.status_changed_at
        item.status_reason = "Synthetic reason edit"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.status_reason, "Synthetic reason edit")
        self.assertEqual(item.status, "unavailable")
        self.assertEqual(item.status_changed_at, original)

    def test_partial_reason_edit_on_a_placed_item_preserves_status_timestamp(self):
        item = self.placed_item()
        original = item.status_changed_at
        item.status_reason = "Synthetic partial edit"
        item.save(update_fields=["status_reason"])
        item.refresh_from_db()
        self.assertEqual(item.status_reason, "Synthetic partial edit")
        self.assertEqual(item.status_changed_at, original)
        self.assertEqual(item.pickup_point_id, self.point.pk)

    def test_inventory_codes_are_case_sensitive_without_casefold(self):
        first = self.item("Inventory-Fixture")
        second = self.item("inventory-fixture")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.inventory_code, "Inventory-Fixture")
        self.assertEqual(second.inventory_code, "inventory-fixture")

    def test_unicode_and_surrounding_whitespace_in_nonblank_code_are_preserved(self):
        code = "  Экз\u00a0№-１２3  "
        item = self.item(code)
        item.refresh_from_db()
        self.assertEqual(item.inventory_code, code)

    def test_codes_are_nonblank_and_respect_the_hundred_character_limit(self):
        item = self.item("x" * 100)
        self.assertEqual(item.inventory_code, "x" * 100)
        for code in ("", "   ", "\t\n", None, "x" * 101):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.item(code)
        self.assertEqual(ProductItem.objects.count(), 1)

    def test_two_distinct_items_can_belong_to_one_product(self):
        first = self.item("one-product-first")
        second = self.item("one-product-second")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.product_id, second.product_id)

    def test_initial_null_current_point_has_no_location_history(self):
        item = self.item()
        item.refresh_from_db()
        self.assertIsNone(item.pickup_point_id)
        self.assertIsNone(item.status_reason)
        self.assertFalse(
            ProductItemLocationHistory.objects.filter(product_item=item).exists()
        )

    def test_public_creation_cannot_assign_a_current_point_directly(self):
        for values in (
            {"pickup_point": self.point}, {"pickup_point_id": self.point.pk},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.item(**values)
        self.assertFalse(ProductItem.objects.exists())

    def test_public_save_cannot_assign_a_point_without_the_location_service(self):
        item = self.item()
        item.pickup_point = self.point
        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        self.assertIsNone(item.pickup_point_id)
        self.assertFalse(ProductItemLocationHistory.objects.exists())

    def test_public_save_cannot_clear_a_current_point_directly(self):
        item = self.placed_item()
        item.pickup_point = None
        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        self.assertEqual(item.pickup_point_id, self.point.pk)

    def test_item_queryset_update_and_bulk_writes_are_unsupported(self):
        item = self.item()
        operations = (
            lambda: ProductItem.objects.filter(pk=item.pk).update(
                pickup_point=self.point
            ),
            lambda: ProductItem.objects.bulk_create([item]),
            lambda: ProductItem.objects.bulk_update([item], ["status_reason"]),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    operation()
        item.refresh_from_db()
        self.assertIsNone(item.pickup_point_id)

    def test_history_public_create_and_new_instance_save_are_unsupported(self):
        item = self.item()
        values = {
            "product_item": item, "pickup_point": self.point,
            "valid_from": timezone.now(),
        }
        with self.assertRaises(NotSupportedError):
            ProductItemLocationHistory.objects.create(**values)
        with self.assertRaises(NotSupportedError):
            ProductItemLocationHistory(**values).save()
        self.assertFalse(ProductItemLocationHistory.objects.exists())

    def test_history_existing_instance_save_and_delete_are_unsupported(self):
        item = self.placed_item()
        history = ProductItemLocationHistory.objects.get(product_item=item)
        for operation in (history.save, history.delete):
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    operation()
        self.assertTrue(
            ProductItemLocationHistory.objects.filter(pk=history.pk).exists()
        )

    def test_history_queryset_update_bulk_writes_and_delete_are_unsupported(self):
        item = self.placed_item()
        history = ProductItemLocationHistory.objects.get(product_item=item)
        manager = ProductItemLocationHistory.objects
        for operation in (
            lambda: manager.filter(pk=history.pk).update(pickup_point=self.other_point),
            lambda: manager.bulk_create([history]),
            lambda: manager.bulk_update([history], ["valid_to"]),
            lambda: manager.filter(pk=history.pk).delete(),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(NotSupportedError):
                    operation()
        history.refresh_from_db()
        self.assertEqual(history.pickup_point_id, self.point.pk)
        self.assertIsNone(history.valid_to)

    def test_item_without_any_history_can_be_deleted(self):
        item = self.item()
        pk = item.pk
        item.delete()
        self.assertFalse(ProductItem.objects.filter(pk=pk).exists())

    def test_item_with_history_is_protected_from_deletion(self):
        item = self.placed_item()
        for operation in (
            item.delete,
            lambda: ProductItem.objects.filter(pk=item.pk).delete(),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ProtectedError):
                    operation()
        self.assertTrue(ProductItem.objects.filter(pk=item.pk).exists())

    def test_product_and_point_are_protected_by_inventory_links(self):
        self.placed_item()
        for parent in (self.product, self.point):
            with self.subTest(model=type(parent).__name__):
                with self.assertRaises(ProtectedError):
                    parent.delete()

    def test_nullable_current_point_still_protects_its_parent(self):
        pk = self.insert_raw("product_item", self.raw_item_values(
            pickup_point_id=self.point.pk
        ))
        connection.check_constraints()
        self.assertFalse(ProductItemLocationHistory.objects.exists())
        with self.assertRaises(ProtectedError):
            self.point.delete()
        self.assertEqual(
            ProductItem.objects.get(pk=pk).pickup_point_id, self.point.pk
        )

    def test_all_four_foreign_keys_reject_nonexistent_rows_in_postgresql(self):
        item = self.item()
        now = timezone.now()
        history_values = {
            "product_item_id": item.pk,
            "pickup_point_id": self.point.pk,
            "valid_from": now, "valid_to": now + timedelta(seconds=1),
        }
        for table, field, values in (
            ("product_item", "product_id", self.raw_item_values()),
            ("product_item", "pickup_point_id", self.raw_item_values()),
            ("product_item_location_history", "product_item_id", history_values),
            ("product_item_location_history", "pickup_point_id", history_values),
        ):
            with (
                self.subTest(table=table, field=field),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw(table, {**values, field: 9223372036854775000})
                connection.check_constraints()

    def test_required_item_columns_reject_sql_nulls(self):
        for field in (
            "product_id", "inventory_code", "status", "status_changed_at",
            "created_at", "updated_at",
        ):
            with (
                self.subTest(field=field),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw("product_item", self.raw_item_values(**{field: None}))
                connection.check_constraints()

    def test_history_requires_both_parent_rows_and_a_start_timestamp(self):
        item = self.item()
        values = {
            "product_item_id": item.pk,
            "pickup_point_id": self.point.pk,
            "valid_from": timezone.now(),
        }
        for field in ("product_item_id", "pickup_point_id", "valid_from"):
            with (
                self.subTest(field=field),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw(
                    "product_item_location_history", {**values, field: None}
                )
                connection.check_constraints()

    def test_postgresql_allows_all_five_approved_statuses(self):
        for status in (
            "available", "reserved", "rented", "unavailable", "retired"
        ):
            with self.subTest(status=status):
                pk = self.insert_raw("product_item", self.raw_item_values(
                    code=f"sql-status-{status}", status=status
                ))
                connection.check_constraints()
                self.assertEqual(ProductItem.objects.get(pk=pk).status, status)

    def test_postgresql_rejects_unapproved_or_empty_statuses(self):
        for status in ("unknown-fixture", "AVAILABLE", ""):
            with (
                self.subTest(status=status),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw(
                    "product_item", self.raw_item_values(status=status)
                )

    def test_code_uniqueness_is_global_across_products_in_postgresql(self):
        item = self.item()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.insert_raw("product_item", self.raw_item_values(
                code=item.inventory_code, product_id=self.other_product.pk
            ))

    def test_reverse_default_add_and_set_cannot_bypass_guarded_writes(self):
        placed = self.placed_item()
        history = ProductItemLocationHistory.objects.get(product_item=placed)
        other_item = self.item("reverse-other-fixture")
        cases = (
            (self.other_product.items, placed, "product_id", self.product.pk),
            (self.other_point.product_items, placed, "pickup_point_id", self.point.pk),
            (other_item.location_history, history, "product_item_id", placed.pk),
            (
                self.other_point.item_location_history, history,
                "pickup_point_id", self.point.pk,
            ),
        )
        for manager, instance, field, original in cases:
            for operation in (
                lambda: manager.add(instance), lambda: manager.set([instance]),
            ):
                with self.subTest(model=type(instance).__name__, field=field):
                    with self.assertRaises(NotSupportedError), transaction.atomic():
                        operation()
                    instance.refresh_from_db()
                    self.assertEqual(getattr(instance, field), original)

    def test_reverse_nonbulk_add_still_cannot_change_current_point_or_history(self):
        item = self.placed_item()
        history = ProductItemLocationHistory.objects.get(product_item=item)
        with self.assertRaises(ValidationError), transaction.atomic():
            self.other_point.product_items.add(item, bulk=False)
        item.refresh_from_db()
        self.assertEqual(item.pickup_point_id, self.point.pk)
        with self.assertRaises(NotSupportedError), transaction.atomic():
            self.other_point.item_location_history.add(history, bulk=False)
        history.refresh_from_db()
        self.assertEqual(history.pickup_point_id, self.point.pk)

    def test_nullable_reverse_remove_and_clear_cannot_bypass_location_service(self):
        item = self.placed_item()
        manager = self.point.product_items
        for operation, error in (
            (lambda: manager.remove(item), NotSupportedError),
            (lambda: manager.clear(), NotSupportedError),
            (lambda: manager.set([], clear=True), NotSupportedError),
            (lambda: manager.remove(item, bulk=False), ValidationError),
            (lambda: manager.clear(bulk=False), ValidationError),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(error), transaction.atomic():
                    operation()
                item.refresh_from_db()
                self.assertEqual(item.pickup_point_id, self.point.pk)
                history = ProductItemLocationHistory.objects.get(
                    product_item=item, valid_to=None
                )
                self.assertEqual(history.pickup_point_id, self.point.pk)
