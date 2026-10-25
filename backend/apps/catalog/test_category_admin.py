"""Category administration through standard Django model permissions."""

from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse

from .models import Product, ProductCategory


class ProductCategoryAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="Tools fixture")
        cls.other_category = ProductCategory.objects.create(name="Games fixture")

    def setUp(self):
        self._accounts_created = 0
        self.list_url = reverse("admin:catalog_productcategory_changelist")
        self.add_url = reverse("admin:catalog_productcategory_add")
        self.change_url = reverse(
            "admin:catalog_productcategory_change", args=[self.category.pk]
        )
        self.delete_url = reverse(
            "admin:catalog_productcategory_delete", args=[self.category.pk]
        )

    def _permission(self, action):
        return Permission.objects.get(
            content_type=ContentType.objects.get_for_model(ProductCategory),
            codename=f"{action}_productcategory",
        )

    def _login(
        self,
        *actions,
        is_staff=True,
        is_active=True,
        is_superuser=False,
        account_type="customer",
        client=None,
    ):
        self._accounts_created += 1
        account = get_user_model().objects.create_user(
            phone=f"+1555555{1000 + self._accounts_created:04d}",
            password=None,
            account_type=account_type,
            status="fixture-status",
        )
        account.is_staff = is_staff
        account.is_active = is_active
        account.is_superuser = is_superuser
        account.save(update_fields=["is_staff", "is_active", "is_superuser"])
        account.user_permissions.add(*(self._permission(action) for action in actions))
        (client or self.client).force_login(account)
        return account

    def _snapshot(self):
        return list(ProductCategory.objects.order_by("pk").values("id", "name"))

    def _assert_login_redirect(self, response, target):
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("admin:login"))
        self.assertEqual(parse_qs(parsed.query).get("next"), [target])

    def _bulk_delete(self, *categories):
        return self.client.post(
            self.list_url,
            {
                "action": "delete_selected",
                "_selected_action": [str(category.pk) for category in categories],
                "post": "yes",
            },
        )

    def _make_protected_product(self):
        return Product.objects.create(
            category=self.category,
            name="Protected product fixture",
            description="A synthetic category-protection fixture.",
            status="fixture-status",
        )

    def test_anonymous_admin_requests_redirect_to_login_without_writes(self):
        before = self._snapshot()
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url):
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self._assert_login_redirect(response, url)
        self.assertEqual(self._snapshot(), before)

    def test_nonstaff_account_is_denied_even_with_all_category_permissions(self):
        self._login("view", "add", "change", "delete", is_staff=False)
        before = self._snapshot()
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url):
            with self.subTest(url=url):
                self._assert_login_redirect(self.client.get(url), url)
        self._assert_login_redirect(
            self.client.post(self.change_url, {"name": "Denied fixture", "_save": "Save"}),
            self.change_url,
        )
        self.assertEqual(self._snapshot(), before)

    def test_inactive_staff_is_denied_even_with_all_category_permissions(self):
        self._login("view", "add", "change", "delete", is_active=False)
        before = self._snapshot()
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url):
            with self.subTest(url=url):
                self._assert_login_redirect(self.client.get(url), url)
        self._assert_login_redirect(
            self.client.post(self.delete_url, {"post": "yes"}), self.delete_url
        )
        self.assertEqual(self._snapshot(), before)

    def test_staff_without_permissions_cannot_read_or_write_categories(self):
        self._login()
        before = self._snapshot()
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for url, data in (
            (self.add_url, {"name": "Denied add fixture", "_save": "Save"}),
            (self.change_url, {"name": "Denied change fixture", "_save": "Save"}),
            (self.delete_url, {"post": "yes"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_employee_account_type_does_not_grant_category_permissions(self):
        self._login(account_type="employee")
        before = self._snapshot()

        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.change_url, {"name": "Denied employee fixture", "_save": "Save"}
            ).status_code,
            403,
        )
        self.assertEqual(self._snapshot(), before)

    def test_view_permission_allows_list_and_read_only_detail(self):
        self._login("view")

        listing = self.client.get(self.list_url)
        detail = self.client.get(self.change_url)

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(listing, self.category.name)
        self.assertContains(detail, self.category.name)
        self.assertIn("name", detail.context["adminform"].readonly_fields)

    def test_view_only_staff_cannot_post_add_change_or_delete(self):
        self._login("view")
        before = self._snapshot()
        for url, data in (
            (self.add_url, {"name": "Denied view-only add fixture", "_save": "Save"}),
            (self.change_url, {"name": "Denied view-only edit fixture", "_save": "Save"}),
            (self.delete_url, {"post": "yes"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_view_only_staff_cannot_execute_a_forged_bulk_delete_action(self):
        self._login("view")
        before = self._snapshot()

        response = self._bulk_delete(self.category, self.other_category)

        # Django may render the unchanged list or redirect when an action is absent.
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(self._snapshot(), before)

    def test_category_permissions_can_be_granted_through_a_group(self):
        account = self._login()
        group = Group.objects.create(name="Category readers fixture")
        group.permissions.add(self._permission("view"))
        account.groups.add(group)

        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(self.change_url).status_code, 200)
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 403)

    def test_add_permission_allows_creation_but_not_change_or_delete(self):
        self._login("add")
        self.assertEqual(self.client.get(self.add_url).status_code, 200)

        response = self.client.post(
            self.add_url, {"name": "Created category fixture", "_save": "Save"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProductCategory.objects.filter(name="Created category fixture").exists())
        before = self._snapshot()
        self.assertEqual(
            self.client.post(
                self.change_url, {"name": "Denied add-only edit fixture", "_save": "Save"}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_change_permission_allows_edit_but_not_creation_or_deletion(self):
        self._login("change")
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(self.change_url).status_code, 200)

        response = self.client.post(
            self.change_url, {"name": "Renamed category fixture", "_save": "Save"}
        )

        self.assertEqual(response.status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "Renamed category fixture")
        before = self._snapshot()
        self.assertEqual(
            self.client.post(
                self.add_url, {"name": "Denied change-only add fixture", "_save": "Save"}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_delete_permission_allows_direct_delete_but_not_add_or_change(self):
        self._login("delete")
        self.assertEqual(self.client.get(self.delete_url).status_code, 200)

        response = self.client.post(self.delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductCategory.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(ProductCategory.objects.filter(pk=self.other_category.pk).exists())
        before = self._snapshot()
        other_change_url = reverse(
            "admin:catalog_productcategory_change", args=[self.other_category.pk]
        )
        self.assertEqual(
            self.client.post(self.add_url, {"name": "Denied delete-only fixture"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(other_change_url, {"name": "Denied rename fixture"}).status_code,
            403,
        )
        self.assertEqual(self._snapshot(), before)

    def test_superuser_can_manage_categories_without_an_employee_profile(self):
        self._login(is_superuser=True, account_type="customer")

        added = self.client.post(
            self.add_url, {"name": "Superuser category fixture", "_save": "Save"}
        )
        self.assertEqual(added.status_code, 302)
        changed = self.client.post(
            self.change_url, {"name": "Superuser renamed fixture", "_save": "Save"}
        )
        self.assertEqual(changed.status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "Superuser renamed fixture")
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 302)
        self.assertFalse(ProductCategory.objects.filter(pk=self.category.pk).exists())

    def test_add_form_rejects_duplicate_blank_and_overlong_names(self):
        self._login("add")
        before = self._snapshot()
        for name in (self.category.name, "", "   ", "N" * 101):
            with self.subTest(name=name):
                response = self.client.post(self.add_url, {"name": name, "_save": "Save"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("name", response.context["adminform"].form.errors)
                self.assertEqual(self._snapshot(), before)

    def test_add_form_accepts_the_exact_name_length_limit(self):
        self._login("add")
        name = "N" * 100

        response = self.client.post(self.add_url, {"name": name, "_save": "Save"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProductCategory.objects.filter(name=name).exists())

    def test_change_form_rejects_duplicate_blank_and_overlong_names(self):
        self._login("change")
        before = self._snapshot()
        for name in (self.other_category.name, "", "   ", "N" * 101):
            with self.subTest(name=name):
                response = self.client.post(self.change_url, {"name": name, "_save": "Save"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("name", response.context["adminform"].form.errors)
                self.assertEqual(self._snapshot(), before)

    def test_admin_lists_in_name_order_and_searches_names(self):
        self._login("view")

        listing = self.client.get(self.list_url)
        searched = self.client.get(self.list_url, {"q": "Tools"})

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [category.pk for category in listing.context["cl"].result_list],
            [self.other_category.pk, self.category.pk],
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(
            [category.pk for category in searched.context["cl"].result_list],
            [self.category.pk],
        )

    def test_direct_delete_preserves_a_category_referenced_by_a_product(self):
        product = self._make_protected_product()
        self._login("delete")
        before = self._snapshot()

        confirmation = self.client.get(self.delete_url)
        response = self.client.post(self.delete_url, {"post": "yes"})

        self.assertEqual(confirmation.status_code, 200)
        self.assertTrue(confirmation.context["protected"])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertEqual(self._snapshot(), before)
        product.refresh_from_db()
        self.assertEqual(product.category_id, self.category.pk)

    def test_bulk_delete_removes_unreferenced_categories_with_permission(self):
        self._login("view", "delete")

        response = self._bulk_delete(self.category, self.other_category)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductCategory.objects.exists())

    def test_bulk_delete_is_all_or_nothing_when_a_category_is_protected(self):
        product = self._make_protected_product()
        self._login("view", "delete")
        before = self._snapshot()

        response = self._bulk_delete(self.category, self.other_category)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertEqual(self._snapshot(), before)
        product.refresh_from_db()
        self.assertEqual(product.category_id, self.category.pk)

    def test_admin_write_routes_enforce_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self._login("view", "add", "change", "delete", client=csrf_client)
        before = self._snapshot()
        for url, data in (
            (self.add_url, {"name": "Missing CSRF add fixture", "_save": "Save"}),
            (self.change_url, {"name": "Missing CSRF edit fixture", "_save": "Save"}),
            (self.delete_url, {"post": "yes"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(csrf_client.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)
