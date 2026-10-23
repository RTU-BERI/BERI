"""Catalog dictionary integrity against the approved PostgreSQL structure."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import AttributeDefinition, ProductCategory


class CatalogDictionaryTests(TestCase):
    def test_postgresql_tables_match_the_exact_erd_columns(self):
        self.assertEqual(connection.vendor, "postgresql")
        expected_tables = {
            "product_category": {
                "id": ("bigint", "NO", None),
                "name": ("character varying", "NO", 100),
            },
            "attribute_definition": {
                "id": ("bigint", "NO", None),
                "code": ("character varying", "NO", 100),
                "name": ("character varying", "NO", 150),
                "data_type": ("character varying", "NO", 20),
            },
        }
        for table, expected in expected_tables.items():
            with self.subTest(table=table), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type, is_nullable, "
                    "character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s",
                    [table],
                )
                actual = {
                    name: (kind, null, length)
                    for name, kind, null, length in cursor.fetchall()
                }
                self.assertEqual(actual, expected)
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                self.assertEqual(
                    [
                        constraint["columns"]
                        for constraint in constraints.values()
                        if constraint["primary_key"]
                    ],
                    [["id"]],
                )
                self.assertFalse(
                    any(c["foreign_key"] for c in constraints.values())
                )

    def test_primary_keys_are_generated_for_independent_dictionary_entries(self):
        category = ProductCategory.objects.create(name="Tools fixture")
        other_category = ProductCategory.objects.create(name="Games fixture")
        definition = AttributeDefinition.objects.create(
            code="fixture_size", name="Size fixture", data_type="opaque-kind"
        )
        for instance in (category, other_category, definition):
            with self.subTest(model=type(instance).__name__, pk=instance.pk):
                self.assertIsInstance(instance.pk, int)
                self.assertGreater(instance.pk, 0)
                instance.refresh_from_db()
        self.assertNotEqual(category.pk, other_category.pk)

    def test_database_rejects_duplicate_category_name(self):
        ProductCategory.objects.create(name="Tools fixture")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductCategory.objects.create(name="Tools fixture")
        self.assertEqual(ProductCategory.objects.count(), 1)

    def test_database_rejects_duplicate_attribute_code(self):
        AttributeDefinition.objects.create(
            code="fixture_size", name="Size fixture", data_type="opaque-kind"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttributeDefinition.objects.create(
                code="fixture_size",
                name="Other name fixture",
                data_type="another-kind",
            )
        self.assertEqual(AttributeDefinition.objects.count(), 1)

    def test_distinct_codes_may_share_a_display_name(self):
        first = AttributeDefinition.objects.create(
            code="fixture_size", name="Size fixture", data_type="opaque-kind"
        )
        second = AttributeDefinition.objects.create(
            code="fixture_volume", name="Size fixture", data_type="another-kind"
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.name, second.name)
        self.assertEqual(AttributeDefinition.objects.count(), 2)

    def test_category_name_is_required_and_has_the_erd_length_limit(self):
        for name in (None, "", "x" * 101):
            with self.subTest(name=name):
                category = ProductCategory(name=name)
                with self.assertRaises(ValidationError) as raised:
                    category.full_clean()
                self.assertIn("name", raised.exception.message_dict)
        self.assertEqual(ProductCategory.objects.count(), 0)

    def test_attribute_fields_are_required_and_have_the_erd_length_limits(self):
        values = {
            "code": "fixture_size",
            "name": "Size fixture",
            "data_type": "opaque-kind",
        }
        for field, maximum in (("code", 100), ("name", 150), ("data_type", 20)):
            for invalid in (None, "", "x" * (maximum + 1)):
                with self.subTest(field=field, value=invalid):
                    candidate = AttributeDefinition(
                        **{**values, field: invalid}
                    )
                    with self.assertRaises(ValidationError) as raised:
                        candidate.full_clean()
                    self.assertIn(field, raised.exception.message_dict)
        self.assertEqual(AttributeDefinition.objects.count(), 0)

    def test_missing_attribute_type_is_not_filled_with_a_product_default(self):
        field = AttributeDefinition._meta.get_field("data_type")
        self.assertFalse(field.has_default())
        self.assertFalse(field.has_db_default())
        self.assertFalse(field.choices)
        candidate = AttributeDefinition(code="fixture_size", name="Size fixture")
        with self.assertRaises(ValidationError) as raised:
            candidate.full_clean()
        self.assertIn("data_type", raised.exception.message_dict)

    def test_explicit_values_are_preserved_without_a_data_type_vocabulary(self):
        category = ProductCategory(name="Tools Fixture")
        category.full_clean()
        category.save()
        definition = AttributeDefinition(
            code="Fixture_Size",
            name="Size Fixture",
            data_type="unapproved-fixture",
        )
        definition.full_clean()
        definition.save()
        category.refresh_from_db()
        definition.refresh_from_db()
        self.assertEqual(category.name, "Tools Fixture")
        self.assertEqual(definition.code, "Fixture_Size")
        self.assertEqual(definition.data_type, "unapproved-fixture")

    def test_database_rejects_null_required_dictionary_fields(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductCategory.objects.create(name=None)
        values = {
            "code": "fixture_size",
            "name": "Size fixture",
            "data_type": "opaque-kind",
        }
        for field in values:
            with (
                self.subTest(field=field),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                AttributeDefinition.objects.create(**{**values, field: None})
        self.assertEqual(ProductCategory.objects.count(), 0)
        self.assertEqual(AttributeDefinition.objects.count(), 0)
