"""S1-P3: actual Admin HTTP writes, permissions and read-after-write API."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account
from .models import (
    AttributeDefinition,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductComponent,
    ProductTariff,
)

MODELS = (
    Product,
    AttributeDefinition,
    CategoryAttribute,
    ProductAttributeValue,
    ProductComponent,
    ProductTariff,
)


def admin_url(model, action, obj=None):
    return reverse(
        f"admin:catalog_{model._meta.model_name}_{action}",
        args=[obj.pk] if obj else None,
    )


class CatalogAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="Tools")
        cls.other_category = ProductCategory.objects.create(name="Games")
        cls.product = Product.objects.create(
            category=cls.category,
            name="Drill",
            description="Synthetic product",
            status="fixture",
        )
        cls.definition = AttributeDefinition.objects.create(
            code="power", name="Power", data_type="opaque"
        )
        cls.attribute = CategoryAttribute.objects.create(
            category=cls.category,
            attribute_definition=cls.definition,
            unit="W",
            is_filterable=False,
        )
        cls.component = ProductComponent.objects.create(
            product=cls.product, name="Case", quantity=1
        )

    def setUp(self):
        self.account = Account.objects.create_superuser(
            phone="+12025550123",
            password="Synthetic9!Password",
            account_type="customer",
            status="fixture",
        )
        self.client.force_login(self.account)

    def grant(self, model, *actions, staff=True):
        self.account.is_superuser = False
        self.account.is_staff = staff
        self.account.save()
        self.account.user_permissions.clear()
        self.account.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="catalog",
                content_type__model=model._meta.model_name,
                codename__in=[
                    f"{action}_{model._meta.model_name}" for action in actions
                ],
            )
        )

    def product_data(self, **updates):
        data = dict(
            category=self.category.pk,
            name="Saw",
            description="Demo",
            status="fixture",
            image_url="",
            brand="",
            model="",
            _save="Save",
        )
        data.update(updates)
        return data

    def value_data(self, **updates):
        data = dict(
            product=self.product.pk,
            category_attribute=self.attribute.pk,
            value_kind="text",
            value_text="value",
            value_number="",
            value_boolean="unknown",
            _save="Save",
        )
        data.update(updates)
        return data

    def tariff_data(self, **updates):
        start = timezone.now().replace(microsecond=0) - timedelta(hours=1)
        data = dict(
            product=self.product.pk,
            hourly_rate="125.50",
            valid_from_0=start.strftime("%Y-%m-%d"),
            valid_from_1=start.strftime("%H:%M:%S"),
            valid_to_0="",
            valid_to_1="",
            _save="Save",
        )
        data.update(updates)
        return data

    def assert_form_error(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)

    def test_all_six_models_registered_and_superuser_can_open_add_forms(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(
                    self.client.get(admin_url(model, "changelist")).status_code, 200
                )
                self.assertEqual(
                    self.client.get(admin_url(model, "add")).status_code, 200
                )

    def test_anonymous_nonstaff_inactive_and_staff_without_permissions(self):
        self.client.logout()
        for model in MODELS:
            self.assertEqual(self.client.get(admin_url(model, "add")).status_code, 302)
        self.client.force_login(self.account)
        for staff, active, expected in (
            (False, True, 302),
            (True, False, 302),
            (True, True, 403),
        ):
            self.account.is_superuser = False
            self.account.is_staff, self.account.is_active = staff, active
            self.account.save()
            for model in MODELS:
                with self.subTest(model=model, staff=staff, active=active):
                    self.assertEqual(
                        self.client.get(admin_url(model, "add")).status_code, expected
                    )

    def test_employee_role_alone_does_not_grant_catalog_permissions(self):
        self.account.account_type = "employee"
        self.account.save()
        self.grant(Product)
        self.assertEqual(
            self.client.post(
                admin_url(Product, "add"), self.product_data()
            ).status_code,
            403,
        )

    def test_view_only_permissions_cannot_write_any_model(self):
        value = ProductAttributeValue.objects.create(
            product=self.product, category_attribute=self.attribute, value_text="x"
        )
        tariff = ProductTariff.objects.create(
            product=self.product, hourly_rate="1.00", valid_from=timezone.now()
        )
        objects = (
            self.product,
            self.definition,
            self.attribute,
            value,
            self.component,
            tariff,
        )
        for model, obj in zip(MODELS, objects):
            self.grant(model, "view")
            with self.subTest(model=model):
                self.assertEqual(
                    self.client.get(admin_url(model, "change", obj)).status_code, 200
                )
                self.assertEqual(
                    self.client.post(admin_url(model, "add"), {}).status_code, 403
                )
                self.assertEqual(
                    self.client.post(admin_url(model, "change", obj), {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        admin_url(model, "delete", obj), {"post": "yes"}
                    ).status_code,
                    403,
                )

    def test_add_and_change_permissions_are_separate_and_groups_work(self):
        from django.contrib.auth.models import Group

        self.grant(Product, "add")
        self.assertEqual(
            self.client.post(
                admin_url(Product, "add"), self.product_data()
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                admin_url(Product, "change", self.product), self.product_data()
            ).status_code,
            403,
        )
        self.grant(Product)
        group = Group.objects.create(name="Catalog editors")
        group.permissions.add(
            Permission.objects.get(
                content_type__app_label="catalog", codename="change_product"
            )
        )
        self.account.groups.add(group)
        self.assertEqual(
            self.client.post(
                admin_url(Product, "change", self.product), self.product_data()
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                admin_url(Product, "add"), self.product_data()
            ).status_code,
            403,
        )

    def test_csrf_is_required_on_every_new_admin_add_route(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.account)
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(
                    client.post(admin_url(model, "add"), {}).status_code, 403
                )

    def test_product_create_without_photo_and_update_are_visible_in_api(self):
        response = self.client.post(admin_url(Product, "add"), self.product_data())
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name="Saw")
        self.assertFalse(product.image_url)
        self.assertEqual(
            self.client.get(f"/api/products/{product.pk}/").json()["name"], "Saw"
        )
        self.assertEqual(
            self.client.post(
                admin_url(Product, "change", product), self.product_data(name="New saw")
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get("/api/products/", {"search": "New saw"}).json()[0]["id"],
            product.pk,
        )

    def test_product_required_fields_and_opaque_status(self):
        for update in (
            {"name": ""},
            {"description": ""},
            {"status": "   "},
            {"category": ""},
        ):
            with self.subTest(update=update):
                self.assert_form_error(
                    self.client.post(
                        admin_url(Product, "add"), self.product_data(**update)
                    )
                )
        self.assertEqual(Product.objects.count(), 1)

    def test_product_category_and_system_dates_cannot_be_forged(self):
        ProductAttributeValue.objects.create(
            product=self.product, category_attribute=self.attribute, value_number=0
        )
        before = self.product.created_at
        data = self.product_data(
            category=self.other_category.pk, created_at="2000-01-01"
        )
        self.assertEqual(
            self.client.post(
                admin_url(Product, "change", self.product), data
            ).status_code,
            302,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.category.pk)
        self.assertEqual(self.product.created_at, before)

    def test_product_and_dictionary_deletion_disabled_even_for_superuser(self):
        for model, obj in (
            (Product, self.product),
            (AttributeDefinition, self.definition),
            (CategoryAttribute, self.attribute),
        ):
            self.assertEqual(
                self.client.post(
                    admin_url(model, "delete", obj), {"post": "yes"}
                ).status_code,
                403,
            )

    def test_definitions_accept_opaque_data_type_but_duplicate_code_fails(self):
        data = dict(code="size", name="Size", data_type="custom-kind", _save="Save")
        self.assertEqual(
            self.client.post(admin_url(AttributeDefinition, "add"), data).status_code,
            302,
        )
        self.assert_form_error(
            self.client.post(admin_url(AttributeDefinition, "add"), data)
        )
        obj = AttributeDefinition.objects.get(code="size")
        self.assertEqual(
            self.client.post(
                admin_url(AttributeDefinition, "change", obj),
                {**data, "name": "Dimension"},
            ).status_code,
            302,
        )

    def test_used_definition_and_used_category_attribute_are_read_only(self):
        ProductAttributeValue.objects.create(
            product=self.product, category_attribute=self.attribute, value_text="x"
        )
        for model, obj in (
            (AttributeDefinition, self.definition),
            (CategoryAttribute, self.attribute),
        ):
            self.assertEqual(
                self.client.get(admin_url(model, "change", obj)).status_code, 200
            )
            self.assertEqual(
                self.client.post(admin_url(model, "change", obj), {}).status_code, 403
            )

    def test_category_attribute_create_edit_and_duplicate_pair(self):
        data = dict(
            category=self.other_category.pk,
            attribute_definition=self.definition.pk,
            unit="cm",
            is_filterable="on",
            _save="Save",
        )
        self.assertEqual(
            self.client.post(admin_url(CategoryAttribute, "add"), data).status_code, 302
        )
        self.assert_form_error(
            self.client.post(admin_url(CategoryAttribute, "add"), data)
        )
        obj = CategoryAttribute.objects.get(category=self.other_category)
        self.assertEqual(
            self.client.post(
                admin_url(CategoryAttribute, "change", obj), {**data, "unit": "mm"}
            ).status_code,
            302,
        )

    def test_typed_values_preserve_empty_text_zero_and_false_and_reach_api(self):
        for update, field, expected in (
            ({"value_text": ""}, "value_text", ""),
            (
                {"value_kind": "number", "value_text": "", "value_number": "0"},
                "value_number",
                Decimal("0"),
            ),
            (
                {"value_kind": "boolean", "value_text": "", "value_boolean": "false"},
                "value_boolean",
                False,
            ),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    admin_url(ProductAttributeValue, "add"), self.value_data(**update)
                )
                self.assertEqual(response.status_code, 302)
                value = ProductAttributeValue.objects.get()
                self.assertEqual(getattr(value, field), expected)
                self.assertEqual(
                    len(
                        self.client.get(f"/api/products/{self.product.pk}/").json()[
                            "attributes"
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    self.client.post(
                        admin_url(ProductAttributeValue, "delete", value),
                        {"post": "yes"},
                    ).status_code,
                    302,
                )

    def test_value_edit_and_rejections(self):
        value = ProductAttributeValue.objects.create(
            product=self.product, category_attribute=self.attribute, value_text="old"
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductAttributeValue, "change", value),
                self.value_data(value_text="new"),
            ).status_code,
            302,
        )
        self.assert_form_error(
            self.client.post(admin_url(ProductAttributeValue, "add"), self.value_data())
        )
        for update in (
            {"value_number": "3"},
            {"value_kind": "number", "value_text": "", "value_number": ""},
            {"value_kind": "boolean", "value_text": "", "value_boolean": "unknown"},
        ):
            self.assert_form_error(
                self.client.post(
                    admin_url(ProductAttributeValue, "change", value),
                    self.value_data(**update),
                )
            )
        value.refresh_from_db()
        self.assertEqual(value.value_text, "new")

    def test_attribute_from_another_category_is_rejected(self):
        other = CategoryAttribute.objects.create(
            category=self.other_category,
            attribute_definition=self.definition,
            is_filterable=False,
        )
        self.assert_form_error(
            self.client.post(
                admin_url(ProductAttributeValue, "add"),
                self.value_data(category_attribute=other.pk),
            )
        )
        self.assertFalse(ProductAttributeValue.objects.exists())

    def test_component_create_update_delete_and_validation(self):
        data = dict(product=self.product.pk, name="Battery", quantity="2", _save="Save")
        self.assertEqual(
            self.client.post(admin_url(ProductComponent, "add"), data).status_code, 302
        )
        obj = ProductComponent.objects.get(name="Battery")
        self.assertEqual(
            self.client.post(
                admin_url(ProductComponent, "change", obj), {**data, "quantity": "3"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get(f"/api/products/{self.product.pk}/").json()["components"][
                -1
            ]["quantity"],
            3,
        )
        self.assert_form_error(
            self.client.post(
                admin_url(ProductComponent, "change", obj), {**data, "quantity": "0"}
            )
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductComponent, "delete", obj), {"post": "yes"}
            ).status_code,
            302,
        )

    def test_no_bulk_delete_actions_even_when_forged(self):
        before = ProductComponent.objects.count()
        response = self.client.post(
            admin_url(ProductComponent, "changelist"),
            dict(
                action="delete_selected",
                _selected_action=[self.component.pk],
                post="yes",
            ),
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(ProductComponent.objects.count(), before)

    def test_late_delete_failure_rolls_back_and_redirects_with_error(self):
        original = ProductComponent.delete

        def fail_after_delete(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise ValidationError("Historical conflict")

        with patch.object(ProductComponent, "delete", fail_after_delete):
            response = self.client.post(
                admin_url(ProductComponent, "delete", self.component),
                {"post": "yes"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProductComponent.objects.filter(pk=self.component.pk).exists())
        self.assertTrue(
            any(message.level == 40 for message in response.wsgi_request._messages)
        )

    def test_tariff_creation_current_price_and_immutable_fields(self):
        data = self.tariff_data()
        self.assertEqual(
            self.client.post(admin_url(ProductTariff, "add"), data).status_code, 302
        )
        tariff = ProductTariff.objects.get()
        self.assertEqual(
            self.client.get(f"/api/products/{self.product.pk}/").json()["tariff"][
                "hourly_rate"
            ],
            "125.50",
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductTariff, "change", tariff),
                {
                    "hourly_rate": "1.00",
                    "product": "999",
                    "valid_to_0": "",
                    "valid_to_1": "",
                    "_save": "Save",
                },
            ).status_code,
            302,
        )
        tariff.refresh_from_db()
        self.assertEqual(tariff.hourly_rate, Decimal("125.50"))
        self.assertEqual(tariff.product_id, self.product.pk)
        self.assertEqual(
            self.client.post(
                admin_url(ProductTariff, "delete", tariff), {"post": "yes"}
            ).status_code,
            403,
        )

    def test_tariff_invalid_rate_and_overlapping_period_are_form_errors(self):
        for rate in ("0", "-1", "NaN", "Infinity", "10000000000.00"):
            self.assert_form_error(
                self.client.post(
                    admin_url(ProductTariff, "add"), self.tariff_data(hourly_rate=rate)
                )
            )
        self.assertEqual(
            self.client.post(
                admin_url(ProductTariff, "add"), self.tariff_data()
            ).status_code,
            302,
        )
        self.assert_form_error(
            self.client.post(admin_url(ProductTariff, "add"), self.tariff_data())
        )
        self.assertEqual(ProductTariff.objects.count(), 1)

    def test_tariff_close_once_then_create_adjacent_successor(self):
        start = timezone.now().replace(microsecond=0) - timedelta(hours=2)
        boundary = start + timedelta(hours=1)
        tariff = ProductTariff.objects.create(
            product=self.product, hourly_rate="10", valid_from=start
        )
        end = dict(
            valid_to_0=boundary.strftime("%Y-%m-%d"),
            valid_to_1=boundary.strftime("%H:%M:%S"),
            _save="Save",
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductTariff, "change", tariff), end
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                admin_url(ProductTariff, "change", tariff), end
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(admin_url(ProductTariff, "change", tariff)).status_code, 200
        )
        data = self.tariff_data(
            valid_from_0=end["valid_to_0"], valid_from_1=end["valid_to_1"]
        )
        self.assertEqual(
            self.client.post(admin_url(ProductTariff, "add"), data).status_code, 302
        )

    def test_tariff_end_before_start_is_validation_error(self):
        tariff = ProductTariff.objects.create(
            product=self.product, hourly_rate="10", valid_from=timezone.now()
        )
        self.assert_form_error(
            self.client.post(
                admin_url(ProductTariff, "change", tariff),
                dict(valid_to_0="2020-01-01", valid_to_1="12:00:00", _save="Save"),
            )
        )

    def test_late_validation_failure_rolls_back_write_and_admin_log(self):
        from django.contrib.admin.models import LogEntry

        original = Product.save

        def fail_after_write(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise ValidationError("Concurrent state changed")

        before = LogEntry.objects.count()
        with patch.object(Product, "save", fail_after_write):
            self.assert_form_error(
                self.client.post(admin_url(Product, "add"), self.product_data())
            )
        self.assertFalse(Product.objects.filter(name="Saw").exists())
        self.assertEqual(LogEntry.objects.count(), before)

    def test_integrity_race_is_safe_form_error_without_driver_diagnostics(self):
        with patch.object(
            ProductTariff, "save", side_effect=IntegrityError("private SQL detail")
        ):
            response = self.client.post(
                admin_url(ProductTariff, "add"), self.tariff_data()
            )
        self.assert_form_error(response)
        self.assertNotContains(response, "private SQL detail")
        self.assertFalse(ProductTariff.objects.exists())
