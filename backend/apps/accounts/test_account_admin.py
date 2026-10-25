"""Sensitive Account administration uses fresh superuser authorization."""

import uuid

from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from .forms import AccountCreationForm
from .models import Account


class AdminFixturesMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.actor = Account.objects.create_superuser(
            phone="+15550001001", password="Synthetic-admin-fixture-42",
            account_type="customer", status="opaque-fixture",
        )
        cls.target = Account.objects.create_user(
            phone="+15550001002", password=None,
            account_type="customer", status="opaque-fixture",
        )
        cls.staff = Account.objects.create_user(
            phone="+15550001003", password=None,
            account_type="customer", status="opaque-fixture",
        )
        cls.staff.is_staff = True
        cls.staff.save(update_fields=["is_staff"])
        cls.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label__in=("accounts", "auth")
        ))

    def login_actor(self, client=None):
        (client or self.client).force_login(self.actor)

    def account_data(self, account=None, **overrides):
        account = account or self.target
        data = {
            "phone": account.phone, "account_type": account.account_type,
            "status": account.status, "is_active": "on", "_save": "Save",
        }
        if account.is_staff:
            data["is_staff"] = "on"
        if account.is_superuser:
            data["is_superuser"] = "on"
        data.update(overrides)
        return data


class AccountAdminTests(AdminFixturesMixin, TestCase):
    def setUp(self):
        self.list_url = reverse("admin:accounts_account_changelist")
        self.add_url = reverse("admin:accounts_account_add")
        self.change_url = reverse("admin:accounts_account_change", args=[self.target.pk])
        self.delete_url = reverse("admin:accounts_account_delete", args=[self.target.pk])
        self.password_url = reverse("admin:auth_user_password_change", args=[self.target.pk])

    def test_anonymous_sensitive_routes_redirect_without_writes(self):
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url, self.password_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)

    def test_staff_with_every_model_permission_cannot_read_or_write_accounts(self):
        self.client.force_login(self.staff)
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url, self.password_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, self.account_data(is_superuser="on")).status_code, 403)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)
        self.assertFalse(self.target.has_usable_password())

    def test_nonstaff_superuser_is_denied(self):
        self.actor.is_staff = False
        self.actor.save(update_fields=["is_staff"])
        self.login_actor()
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_fresh_inactive_superuser_is_denied(self):
        self.login_actor()
        Account.objects.filter(pk=self.actor.pk).update(is_active=False)
        response = self.client.post(self.change_url, self.account_data(status="denied"))
        self.assertIn(response.status_code, (302, 403))
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "opaque-fixture")

    def test_status_does_not_define_admin_access(self):
        self.actor.status = "blocked"
        self.actor.save(update_fields=["status"])
        self.login_actor()
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_active_superuser_can_list_and_edit_unlinked_account(self):
        self.login_actor()
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        response = self.client.post(self.change_url, self.account_data(
            account_type="employee", status="arbitrary-state", phone="+1 (555) 000-1002",
        ))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.phone, "+15550001002")
        self.assertEqual(self.target.status, "arbitrary-state")
        self.assertEqual(self.target.account_type, "employee")

    def test_creation_uses_required_status_and_standard_password_hashing(self):
        self.login_actor()
        response = self.client.post(self.add_url, {
            "phone": "+1 (555) 000-1090", "account_type": "customer", "status": "custom",
            "is_active": "on", "password1": "Synthetic-new-fixture-97",
            "password2": "Synthetic-new-fixture-97", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        created = Account.objects.get(phone="+15550001090")
        self.assertTrue(created.check_password("Synthetic-new-fixture-97"))
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertEqual(created.status, "custom")

    def test_creation_rejects_missing_status_and_password_mismatch(self):
        self.login_actor()
        response = self.client.post(self.add_url, {
            "phone": "+15550001091", "account_type": "customer", "status": "",
            "is_active": "on", "password1": "Synthetic-new-fixture-97",
            "password2": "Different-synthetic-fixture-97", "_save": "Save",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(phone="+15550001091").exists())
        self.assertIn("status", response.context["adminform"].form.errors)
        self.assertIn("password2", response.context["adminform"].form.errors)

    def test_creation_form_rejects_canonical_duplicate_formatted_phone(self):
        form = AccountCreationForm(data={
            "phone": "+1 (555) 000-1002", "account_type": "customer", "status": "custom",
            "is_active": True, "password1": "Synthetic-new-fixture-97",
            "password2": "Synthetic-new-fixture-97",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_creation_form_normalizes_before_storage_length_validation(self):
        form = AccountCreationForm(data={
            "phone": "+1 (555) (000) (1092)    ", "account_type": "customer", "status": "custom",
            "is_active": True, "password1": "Synthetic-new-fixture-97",
            "password2": "Synthetic-new-fixture-97",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().phone, "+15550001092")

    def test_status_exact_value_is_preserved_and_whitespace_only_rejected(self):
        self.login_actor()
        self.assertEqual(self.client.post(self.change_url, self.account_data(status=" opaque ")).status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, " opaque ")
        response = self.client.post(self.change_url, self.account_data(status="  "))
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, " opaque ")

    def test_password_view_uses_standard_password_hashing(self):
        self.login_actor()
        response = self.client.post(self.password_url, {
            "password1": "Synthetic-replacement-fixture-97",
            "password2": "Synthetic-replacement-fixture-97",
            "usable_password": "true",
        })
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("Synthetic-replacement-fixture-97"))

    def test_forged_password_hash_in_change_form_does_not_replace_password(self):
        self.login_actor()
        original = self.target.password
        response = self.client.post(self.change_url, self.account_data(password="forged-hash"))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, original)

    def test_superuser_can_assign_groups_and_explicit_permissions(self):
        self.login_actor()
        group = Group.objects.create(name="Synthetic account group")
        permission = Permission.objects.get(codename="view_productcategory")
        response = self.client.post(self.change_url, self.account_data(
            groups=[str(group.pk)], user_permissions=[str(permission.pk)],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.target.groups.filter(pk=group.pk).exists())
        self.assertTrue(self.target.user_permissions.filter(pk=permission.pk).exists())

    def test_superuser_self_demotion_commits_but_next_request_is_denied(self):
        self.login_actor()
        url = reverse("admin:accounts_account_change", args=[self.actor.pk])
        data = self.account_data(self.actor)
        data.pop("is_superuser")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.actor.refresh_from_db()
        self.assertFalse(self.actor.is_superuser)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_admin_write_and_password_routes_enforce_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.login_actor(client)
        for url in (self.add_url, self.change_url, self.delete_url, self.password_url):
            with self.subTest(url=url):
                self.assertEqual(client.post(url, self.account_data()).status_code, 403)

    def test_missing_and_malformed_account_ids_do_not_raise_server_errors(self):
        self.login_actor()
        for value in (str(uuid.uuid4()), "not-a-uuid"):
            for name in ("accounts_account_change", "accounts_account_delete", "auth_user_password_change"):
                url = reverse(f"admin:{name}", args=[value])
                with self.subTest(url=url):
                    self.assertIn(self.client.get(url).status_code, (302, 404))

    def test_trusted_superuser_can_delete_an_unreferenced_account(self):
        self.login_actor()
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 302)
        self.assertFalse(Account.objects.filter(pk=self.target.pk).exists())

    def test_staff_cannot_bulk_delete_accounts(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.list_url, {
            "action": "delete_selected", "_selected_action": [str(self.target.pk)], "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Account.objects.filter(pk=self.target.pk).exists())

    def test_malformed_account_bulk_selection_is_denied_without_deletion(self):
        self.login_actor()
        response = self.client.post(self.list_url, {
            "action": "delete_selected", "_selected_action": ["not-a-uuid"], "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Account.objects.filter(pk=self.target.pk).exists())

    def test_account_admin_is_explicitly_registered(self):
        self.assertTrue(admin.site.is_registered(Account))
