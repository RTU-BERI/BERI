"""Stage-five catalog guarantees and explicit PostgreSQL/ORM boundaries."""

from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import NotSupportedError
from django.test import TestCase
from django.utils import timezone

from .models import (
    AttributeDefinition,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductComponent,
    ProductTariff,
)


PRODUCT_STATUS = "catalog-fixture"


class CatalogPhysicalSchemaTests(TestCase):
    def test_five_tables_have_exact_erd_columns_and_numeric_precision(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected = {
            "product": {
                "id": ("bigint", "NO", None),
                "category_id": ("bigint", "NO", None),
                "name": ("character varying", "NO", 200),
                "description": ("text", "NO", None),
                "image_url": ("text", "YES", None),
                "brand": ("character varying", "YES", 100),
                "model": ("character varying", "YES", 100),
                "status": ("character varying", "NO", 20),
                "created_at": ("timestamp with time zone", "NO", None),
                "updated_at": ("timestamp with time zone", "NO", None),
            },
            "category_attribute": {
                "id": ("bigint", "NO", None),
                "category_id": ("bigint", "NO", None),
                "attribute_definition_id": ("bigint", "NO", None),
                "unit": ("character varying", "YES", 50),
                "is_filterable": ("boolean", "NO", None),
            },
            "product_attribute_value": {
                "id": ("bigint", "NO", None),
                "product_id": ("bigint", "NO", None),
                "category_attribute_id": ("bigint", "NO", None),
                "value_text": ("text", "YES", None),
                "value_number": ("numeric", "YES", None),
                "value_boolean": ("boolean", "YES", None),
            },
            "product_component": {
                "id": ("bigint", "NO", None),
                "product_id": ("bigint", "NO", None),
                "name": ("character varying", "NO", 150),
                "quantity": ("integer", "NO", None),
            },
            "product_tariff": {
                "id": ("bigint", "NO", None),
                "product_id": ("bigint", "NO", None),
                "hourly_rate": ("numeric", "NO", None),
                "valid_from": ("timestamp with time zone", "NO", None),
                "valid_to": ("timestamp with time zone", "YES", None),
                "created_at": ("timestamp with time zone", "NO", None),
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
                    name: (kind, null, length)
                    for name, kind, null, length in cursor.fetchall()
                }
                self.assertEqual(actual, columns)
        for table, field, precision in (
            ("product_attribute_value", "value_number", (20, 6)),
            ("product_tariff", "hourly_rate", (12, 2)),
        ):
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT numeric_precision, numeric_scale "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s "
                    "AND column_name=%s",
                    [table, field],
                )
                self.assertEqual(cursor.fetchone(), precision)

    def test_primary_keys_and_all_seven_foreign_keys_match_erd(self):
        expected = {
            "product": {("category_id", "product_category", "id")},
            "category_attribute": {
                ("category_id", "product_category", "id"),
                ("attribute_definition_id", "attribute_definition", "id"),
            },
            "product_attribute_value": {
                ("product_id", "product", "id"),
                ("category_attribute_id", "category_attribute", "id"),
            },
            "product_component": {("product_id", "product", "id")},
            "product_tariff": {("product_id", "product", "id")},
        }
        for table, foreign_keys in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    [
                        c["columns"]
                        for c in constraints.values()
                        if c["primary_key"]
                    ],
                    [["id"]],
                )
                actual = {
                    (c["columns"][0], *c["foreign_key"])
                    for c in constraints.values()
                    if c["foreign_key"]
                }
                self.assertEqual(actual, foreign_keys)

    def test_database_unique_pairs_match_the_two_approved_constraints(self):
        expected = {
            "category_attribute": {("category_id", "attribute_definition_id")},
            "product_attribute_value": {
                ("product_id", "category_attribute_id")
            },
            "product": set(),
            "product_component": set(),
            "product_tariff": set(),
        }
        for table, unique_pairs in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    {
                        tuple(c["columns"])
                        for c in constraints.values()
                        if c["unique"] and not c["primary_key"]
                    },
                    unique_pairs,
                )

    def test_approved_checks_and_tariff_exclusion_exist_in_postgresql(self):
        expected = {
            "product_attribute_value": {
                "product_attribute_value_one_value": "c",
            },
            "product_component": {
                "product_component_quantity_positive": "c",
            },
            "product_tariff": {
                "product_tariff_rate_positive_finite": "c",
                "product_tariff_valid_period": "c",
                "product_tariff_no_overlap": "x",
            },
        }
        for table, constraints in expected.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid=%s::regclass",
                    [table],
                )
                actual = dict(cursor.fetchall())
                for name, kind in constraints.items():
                    self.assertEqual(actual.get(name), kind)

    def test_status_and_filterability_have_no_invented_defaults_or_choices(self):
        for model, name in (
            (Product, "status"), (CategoryAttribute, "is_filterable")
        ):
            with self.subTest(model=model.__name__, field=name):
                field = model._meta.get_field(name)
                self.assertFalse(field.null)
                self.assertFalse(field.has_default())
                self.assertFalse(field.has_db_default())
                self.assertFalse(field.choices)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, column_default "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND ((table_name='product' AND column_name='status') "
                "OR (table_name='category_attribute' "
                "AND column_name='is_filterable'))"
            )
            self.assertEqual(
                dict(cursor.fetchall()),
                {"product": None, "category_attribute": None},
            )

    def test_domain_has_exactly_thirteen_tables_without_future_models(self):
        domain = {
            "account", "employee", "product_category", "attribute_definition",
            "pickup_point", "pickup_point_working_hours", "product",
            "category_attribute", "product_attribute_value",
            "product_component", "product_tariff",
            "product_item", "product_item_location_history",
            "reservation", "reservation_item_assignment",
            "payment_method",
            "rental", "inspection", "inspection_problem",
            "payment", "debt",
        }
        actual_domain = {
            model._meta.db_table
            for app in apps.get_app_configs()
            if app.name.startswith("apps.")
            for model in app.get_models()
        }
        self.assertEqual(actual_domain, domain)
        infrastructure = {
            "account_groups", "account_user_permissions", "auth_group",
            "auth_group_permissions", "auth_permission", "django_admin_log",
            "django_content_type", "django_migrations", "django_session",
        }
        self.assertEqual(
            set(connection.introspection.table_names()),
            domain | infrastructure,
        )

    def test_all_seven_new_relationships_use_protect(self):
        for model, name in (
            (Product, "category"),
            (CategoryAttribute, "category"),
            (CategoryAttribute, "attribute_definition"),
            (ProductAttributeValue, "product"),
            (ProductAttributeValue, "category_attribute"),
            (ProductComponent, "product"),
            (ProductTariff, "product"),
        ):
            with self.subTest(model=model.__name__, field=name):
                field = model._meta.get_field(name)
                self.assertIs(field.remote_field.on_delete, models.PROTECT)


class CatalogModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="Catalog fixture A")
        cls.other_category = ProductCategory.objects.create(
            name="Catalog fixture B"
        )
        cls.definition = AttributeDefinition.objects.create(
            code="catalog_fixture", name="Fixture", data_type="opaque-fixture"
        )
        cls.product = Product.objects.create(
            category=cls.category, name="Fixture product",
            description="Fixture description", status=PRODUCT_STATUS,
        )
        cls.attribute = CategoryAttribute.objects.create(
            category=cls.category, attribute_definition=cls.definition,
            is_filterable=False,
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

    def new_attribute(self, suffix, category=None):
        definition = AttributeDefinition.objects.create(
            code=f"catalog_fixture_{suffix}", name="Fixture",
            data_type="opaque-fixture",
        )
        return CategoryAttribute.objects.create(
            category=category or self.category,
            attribute_definition=definition, is_filterable=False,
        )

    def create_value(self, **overrides):
        values = {
            "product": self.product,
            "category_attribute": self.attribute,
            "value_text": "fixture value",
        }
        values.update(overrides)
        return ProductAttributeValue.objects.create(**values)

    def reverse_reassignment_cases(self):
        value = self.create_value()
        other_product = Product.objects.create(
            category=self.other_category,
            name="Reverse relation fixture",
            description="Fixture description",
            status=PRODUCT_STATUS,
        )
        other_attribute = self.new_attribute(
            "reverse_other", self.other_category
        )
        return (
            (
                self.other_category.products,
                self.product,
                "category_id",
                self.category.pk,
            ),
            (
                self.other_category.category_attributes,
                self.attribute,
                "category_id",
                self.category.pk,
            ),
            (
                other_product.attribute_values,
                value,
                "product_id",
                self.product.pk,
            ),
            (
                other_attribute.product_values,
                value,
                "category_attribute_id",
                self.attribute.pk,
            ),
        )

    def test_product_creation_keeps_a_distinct_card_and_automatic_timestamps(self):
        self.product.refresh_from_db()
        self.assertIsInstance(self.product.pk, int)
        self.assertGreater(self.product.pk, 0)
        self.assertEqual(self.product.category_id, self.category.pk)
        self.assertTrue(timezone.is_aware(self.product.created_at))
        self.assertTrue(timezone.is_aware(self.product.updated_at))
        self.assertEqual(self.product.status, PRODUCT_STATUS)

    def test_product_nullable_fields_round_trip_as_null(self):
        self.product.image_url = None
        self.product.brand = None
        self.product.model = None
        self.product.full_clean()
        self.product.save()
        self.product.refresh_from_db()
        self.assertIsNone(self.product.image_url)
        self.assertIsNone(self.product.brand)
        self.assertIsNone(self.product.model)

    def test_image_text_has_no_url_validation_or_2048_character_limit(self):
        image = "not-a-url:" + "x" * 2200
        self.product.image_url = image
        self.product.full_clean()
        self.product.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, image)

    def test_product_status_is_explicit_opaque_text(self):
        self.product.status = "opaque-other-fixture"
        self.product.full_clean()
        self.product.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, "opaque-other-fixture")
        for invalid in (None, "", "x" * 21):
            with self.subTest(status=invalid):
                self.product.status = invalid
                with self.assertRaises(ValidationError) as raised:
                    self.product.full_clean()
                self.assertIn("status", raised.exception.message_dict)

    def test_required_product_fields_are_validated(self):
        for name, invalid in (
            ("category", None), ("name", ""), ("description", ""),
            ("name", "x" * 201),
        ):
            with self.subTest(field=name):
                product = Product(
                    category=self.category, name="Fixture product",
                    description="Fixture description", status=PRODUCT_STATUS,
                )
                setattr(product, name, invalid)
                with self.assertRaises(ValidationError) as raised:
                    product.full_clean()
                self.assertIn(name, raised.exception.message_dict)

    def test_products_may_share_a_display_name(self):
        other = Product.objects.create(
            category=self.category, name=self.product.name,
            description="Another fixture", status=PRODUCT_STATUS,
        )
        self.assertNotEqual(other.pk, self.product.pk)

    def test_filterability_must_be_explicit_and_false_is_valid(self):
        definition = AttributeDefinition.objects.create(
            code="missing_filter_fixture", name="Fixture",
            data_type="opaque-fixture",
        )
        candidate = CategoryAttribute(
            category=self.category, attribute_definition=definition
        )
        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIn("is_filterable", raised.exception.message_dict)
        candidate.is_filterable = False
        candidate.full_clean()
        candidate.save()
        candidate.refresh_from_db()
        self.assertIs(candidate.is_filterable, False)

    def test_category_attribute_pair_is_unique_in_postgresql(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.insert_raw("category_attribute", {
                "category_id": self.category.pk,
                "attribute_definition_id": self.definition.pk,
                "is_filterable": True,
            })

    def test_definition_may_apply_to_another_category(self):
        other = CategoryAttribute.objects.create(
            category=self.other_category,
            attribute_definition=self.definition, is_filterable=True,
        )
        self.assertNotEqual(other.pk, self.attribute.pk)
        self.assertEqual(other.attribute_definition_id, self.definition.pk)

    def test_unit_is_nullable_and_optional(self):
        self.attribute.unit = None
        self.attribute.full_clean()
        self.attribute.save()
        self.attribute.refresh_from_db()
        self.assertIsNone(self.attribute.unit)

    def test_full_clean_rejects_zero_or_multiple_typed_values(self):
        for values in (
            {}, {"value_text": "fixture", "value_number": Decimal("0")},
            {"value_number": Decimal("0"), "value_boolean": False},
            {"value_text": "", "value_boolean": False},
            {
                "value_text": "fixture", "value_number": Decimal("1"),
                "value_boolean": True,
            },
        ):
            with self.subTest(values=values):
                candidate = ProductAttributeValue(
                    product=self.product, category_attribute=self.attribute,
                    **values,
                )
                with self.assertRaises(ValidationError):
                    candidate.full_clean()

    def test_false_zero_and_empty_text_each_count_as_a_present_value(self):
        for index, values in enumerate((
            {"value_boolean": False}, {"value_number": Decimal("0")},
            {"value_text": ""},
        )):
            with self.subTest(values=values):
                attribute = self.new_attribute(f"present_{index}")
                value = ProductAttributeValue(
                    product=self.product, category_attribute=attribute,
                    **values,
                )
                value.full_clean()
                value.save()
                value.refresh_from_db()
                for name, expected in values.items():
                    self.assertEqual(getattr(value, name), expected)

    def test_postgresql_rejects_zero_or_multiple_typed_values(self):
        for values in (
            {}, {"value_text": "fixture", "value_number": Decimal("0")},
            {"value_number": Decimal("0"), "value_boolean": False},
            {"value_text": "", "value_boolean": False},
            {
                "value_text": "fixture", "value_number": Decimal("1"),
                "value_boolean": True,
            },
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw("product_attribute_value", {
                    "product_id": self.product.pk,
                    "category_attribute_id": self.attribute.pk,
                    **values,
                })

    def test_postgresql_accepts_false_zero_and_empty_text_independently(self):
        for index, values in enumerate((
            {"value_boolean": False}, {"value_number": Decimal("0")},
            {"value_text": ""},
        )):
            with self.subTest(values=values):
                attribute = self.new_attribute(f"sql_present_{index}")
                pk = self.insert_raw("product_attribute_value", {
                    "product_id": self.product.pk,
                    "category_attribute_id": attribute.pk,
                    **values,
                })
                value = ProductAttributeValue.objects.get(pk=pk)
                for name, expected in values.items():
                    self.assertEqual(getattr(value, name), expected)

    def test_product_attribute_pair_is_unique_in_postgresql(self):
        value = self.create_value()
        self.assertIsInstance(value.pk, int)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.insert_raw("product_attribute_value", {
                "product_id": self.product.pk,
                "category_attribute_id": self.attribute.pk,
                "value_text": "another fixture",
            })

    def test_create_rejects_characteristic_from_another_category(self):
        other = self.new_attribute("other_category", self.other_category)
        with self.assertRaises(ValidationError):
            self.create_value(category_attribute=other)
        self.assertFalse(ProductAttributeValue.objects.exists())

    def test_save_rejects_reassignment_to_another_category(self):
        value = self.create_value()
        other = self.new_attribute("other_category", self.other_category)
        value.category_attribute = other
        with self.assertRaises(ValidationError):
            value.save()
        value.refresh_from_db()
        self.assertEqual(value.category_attribute_id, self.attribute.pk)

    def test_stale_product_cache_cannot_hide_a_new_persisted_category(self):
        stale = Product.objects.get(pk=self.product.pk)
        fresh = Product.objects.get(pk=self.product.pk)
        fresh.category = self.other_category
        fresh.save()
        with self.assertRaises(ValidationError):
            self.create_value(product=stale)
        self.assertFalse(ProductAttributeValue.objects.exists())

    def test_stale_attribute_cache_cannot_hide_a_new_persisted_category(self):
        stale = CategoryAttribute.objects.get(pk=self.attribute.pk)
        fresh = CategoryAttribute.objects.get(pk=self.attribute.pk)
        fresh.category = self.other_category
        fresh.save()
        with self.assertRaises(ValidationError):
            self.create_value(category_attribute=stale)
        self.assertFalse(ProductAttributeValue.objects.exists())

    def test_unsaved_related_category_edits_do_not_override_database_facts(self):
        self.product.category = self.other_category
        self.attribute.category = self.other_category
        value = self.create_value()
        value.refresh_from_db()
        self.assertEqual(value.product.category_id, self.category.pk)
        self.assertEqual(value.category_attribute.category_id, self.category.pk)

    def test_product_category_change_is_rejected_after_values_exist(self):
        self.create_value()
        self.product.category = self.other_category
        with self.assertRaises(ValidationError):
            self.product.full_clean()
        with self.assertRaises(ValidationError):
            self.product.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.category.pk)

    def test_attribute_category_change_is_rejected_after_values_exist(self):
        self.create_value()
        self.attribute.category = self.other_category
        with self.assertRaises(ValidationError):
            self.attribute.full_clean()
        with self.assertRaises(ValidationError):
            self.attribute.save()
        self.attribute.refresh_from_db()
        self.assertEqual(self.attribute.category_id, self.category.pk)

    def test_category_changes_are_allowed_before_any_values_exist(self):
        self.product.category = self.other_category
        self.product.save()
        self.attribute.category = self.other_category
        self.attribute.save()
        value = self.create_value()
        value.refresh_from_db()
        self.assertEqual(value.product.category_id, self.other_category.pk)
        self.assertEqual(
            value.category_attribute.category_id, self.other_category.pk
        )

    def test_data_type_vocabulary_remains_deferred(self):
        self.assertEqual(self.definition.data_type, "opaque-fixture")
        value = self.create_value(value_text=None, value_boolean=False)
        value.refresh_from_db()
        self.assertIs(value.value_boolean, False)
        self.assertIsNone(value.value_text)

    def test_bulk_and_queryset_updates_are_rejected_for_guarded_models(self):
        value = self.create_value()
        for instance, field, unchanged in (
            (self.product, "name", self.product.name),
            (self.attribute, "unit", self.attribute.unit),
            (value, "value_text", value.value_text),
        ):
            manager = type(instance).objects
            for operation in (
                lambda: manager.filter(pk=instance.pk).update(
                    **{field: unchanged}
                ),
                lambda: manager.bulk_create([instance]),
                lambda: manager.bulk_update([instance], [field]),
            ):
                with (
                    self.subTest(model=type(instance).__name__),
                    self.assertRaises(NotSupportedError),
                ):
                    operation()

    def test_component_quantity_must_be_positive_during_validation(self):
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                component = ProductComponent(
                    product=self.product, name="Fixture part",
                    quantity=quantity,
                )
                with self.assertRaises(ValidationError):
                    component.full_clean()

    def test_postgresql_rejects_nonpositive_component_quantity(self):
        for quantity in (0, -1):
            with (
                self.subTest(quantity=quantity),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.insert_raw("product_component", {
                    "product_id": self.product.pk, "name": "Fixture part",
                    "quantity": quantity,
                })

    def test_components_allow_duplicate_names_and_current_edit_delete_paths(self):
        first = ProductComponent.objects.create(
            product=self.product, name="Fixture part", quantity=2
        )
        second = ProductComponent.objects.create(
            product=self.product, name="Fixture part", quantity=1
        )
        self.assertNotEqual(first.pk, second.pk)
        first.quantity = 3
        first.save()
        first.refresh_from_db()
        self.assertEqual(first.quantity, 3)
        second.delete()
        self.assertEqual(ProductComponent.objects.count(), 1)

    def test_all_new_foreign_keys_reject_null_and_nonexistent_rows_in_sql(self):
        now = timezone.now()
        samples = (
            ("product", "category_id", {
                "category_id": self.category.pk, "name": "Raw fixture",
                "description": "Raw fixture", "status": PRODUCT_STATUS,
                "created_at": now, "updated_at": now,
            }),
            ("category_attribute", "category_id", {
                "category_id": self.category.pk,
                "attribute_definition_id": self.definition.pk,
                "is_filterable": False,
            }),
            ("category_attribute", "attribute_definition_id", {
                "category_id": self.category.pk,
                "attribute_definition_id": self.definition.pk,
                "is_filterable": False,
            }),
            ("product_attribute_value", "product_id", {
                "product_id": self.product.pk,
                "category_attribute_id": self.attribute.pk,
                "value_text": "Raw fixture",
            }),
            ("product_attribute_value", "category_attribute_id", {
                "product_id": self.product.pk,
                "category_attribute_id": self.attribute.pk,
                "value_text": "Raw fixture",
            }),
            ("product_component", "product_id", {
                "product_id": self.product.pk, "name": "Raw fixture",
                "quantity": 1,
            }),
            ("product_tariff", "product_id", {
                "product_id": self.product.pk, "hourly_rate": Decimal("1"),
                "valid_from": now, "created_at": now,
            }),
        )
        for table, foreign_key, values in samples:
            for invalid in (None, 9223372036854775000):
                with (
                    self.subTest(table=table, field=foreign_key, value=invalid),
                    self.assertRaises(IntegrityError),
                    transaction.atomic(),
                ):
                    self.insert_raw(table, {**values, foreign_key: invalid})
                    connection.check_constraints()

    def test_linked_categories_definitions_products_and_attributes_are_protected(self):
        self.create_value()
        for instance in (
            self.category, self.definition, self.product, self.attribute
        ):
            with (
                self.subTest(model=type(instance).__name__),
                self.assertRaises(ProtectedError),
            ):
                instance.delete()

    def test_sql_can_bypass_cross_category_validation_but_full_clean_detects_it(self):
        other = self.new_attribute("sql_cross", self.other_category)
        pk = self.insert_raw("product_attribute_value", {
            "product_id": self.product.pk,
            "category_attribute_id": other.pk,
            "value_text": "SQL fixture",
        })
        connection.check_constraints()
        value = ProductAttributeValue.objects.get(pk=pk)
        with self.assertRaises(ValidationError):
            value.full_clean()

    def test_partial_value_save_checks_the_effective_persisted_relationships(self):
        value = self.create_value()
        other_product = Product.objects.create(
            category=self.other_category, name="Other fixture product",
            description="Fixture description", status=PRODUCT_STATUS,
        )
        other_attribute = self.new_attribute(
            "partial_other", self.other_category
        )
        value.product = other_product
        value.category_attribute = other_attribute
        with self.assertRaises(ValidationError):
            value.save(update_fields=["category_attribute"])
        value.refresh_from_db()
        self.assertEqual(value.product_id, self.product.pk)
        self.assertEqual(value.category_attribute_id, self.attribute.pk)

    def test_sql_category_update_is_detected_on_subsequent_full_clean(self):
        value = self.create_value()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE category_attribute SET category_id=%s WHERE id=%s",
                [self.other_category.pk, self.attribute.pk],
            )
        connection.check_constraints()
        value.refresh_from_db()
        with self.assertRaises(ValidationError):
            value.full_clean()

    def test_reverse_add_cannot_bypass_validation_with_default_bulk_mode(self):
        for manager, instance, field, original in self.reverse_reassignment_cases():
            with self.subTest(model=type(instance).__name__, field=field):
                with self.assertRaises(NotSupportedError), transaction.atomic():
                    manager.add(instance)
                instance.refresh_from_db()
                self.assertEqual(getattr(instance, field), original)

    def test_reverse_set_cannot_bypass_validation_with_default_bulk_mode(self):
        for manager, instance, field, original in self.reverse_reassignment_cases():
            with self.subTest(model=type(instance).__name__, field=field):
                with self.assertRaises(NotSupportedError), transaction.atomic():
                    manager.set([instance])
                instance.refresh_from_db()
                self.assertEqual(getattr(instance, field), original)

    def test_reverse_add_without_bulk_still_rejects_cross_category_changes(self):
        for manager, instance, field, original in self.reverse_reassignment_cases():
            with self.subTest(model=type(instance).__name__, field=field):
                with self.assertRaises(ValidationError), transaction.atomic():
                    manager.add(instance, bulk=False)
                instance.refresh_from_db()
                self.assertEqual(getattr(instance, field), original)
