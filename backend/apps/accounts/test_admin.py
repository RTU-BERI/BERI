from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.pickup_points.models import PickupPoint

from .forms import EmployeeForm
from .models import Account, Employee
from .test_models import FAST_HASHERS, PASSWORD


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class AccountAdminTests(TestCase):
    def setUp(self):
        self.root = Account.objects.create_superuser(
            "+79991234567", PASSWORD, account_type="customer", status="active",
        )
        self.staff = Account.objects.create_user("+4915123456789", PASSWORD)
        Account.objects.filter(pk=self.staff.pk).update(is_staff=True)
        self.staff.refresh_from_db()
        self.customer = Account.objects.create_user("+12025550123", PASSWORD)
        self.group = Group.objects.create(name="Test administrators")
        self.permission = Permission.objects.get(content_type__app_label="accounts", codename="change_account")
        self.group.permissions.add(self.permission)
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="accounts", codename__in=["add_account", "change_account", "delete_account"],
        ))
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="auth", codename__in=["add_group", "change_group", "delete_group"],
        ))

    def change_url(self, account):
        return reverse("admin:accounts_account_change", args=[account.pk])

    def password_url(self, account):
        return reverse("admin:auth_user_password_change", args=[account.pk])

    def change_data(self, account, **extra):
        data = {
            "phone": account.phone, "account_type": account.account_type,
            "status": account.status, "is_active": "on", "_save": "Save",
        }
        data.update(extra)
        return data

    def add_data(self, **extra):
        data = {
            "phone": "+44 7911 123456", "password1": PASSWORD, "password2": PASSWORD,
            "account_type": "customer", "status": "active", "is_active": "on", "_save": "Save",
        }
        data.update(extra)
        return data

    def test_superuser_adds_account_with_hashed_password(self):
        self.client.force_login(self.root)
        url = reverse("admin:accounts_account_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, self.add_data())
        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(phone="+447911123456")
        self.assertTrue(account.check_password(PASSWORD))
        self.assertFalse(account.is_superuser)
        self.assertFalse(account.is_staff)
        self.assertIsNone(account.phone_verified_at)

    def test_admin_phone_login_and_rejection_after_block(self):
        url = reverse("admin:login")
        self.assertEqual(self.client.post(url, {
            "username": "+7 (999) 123-45-67", "password": PASSWORD, "next": reverse("admin:index"),
        }).status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.client.logout()
        self.root.status = "blocked"
        self.root.save()
        self.assertEqual(self.client.post(url, {
            "username": self.root.phone, "password": PASSWORD,
        }).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_add_form_rejects_duplicate_canonical_phone_and_invalid_phone(self):
        self.client.force_login(self.root)
        url = reverse("admin:accounts_account_add")
        for phone in ("+7 (999) 123-45-67", "89991234567", "+999123456789"):
            with self.subTest(phone=phone):
                response = self.client.post(url, self.add_data(phone=phone))
                self.assertEqual(response.status_code, 200)
                self.assertIn("phone", response.context["adminform"].form.errors)
        self.assertEqual(Account.objects.count(), 3)

    def test_change_account_preserves_password_hash_and_can_assign_permissions(self):
        self.client.force_login(self.root)
        old_hash = self.customer.password
        response = self.client.post(self.change_url(self.customer), self.change_data(
            self.customer, account_type="employee", is_staff="on", is_superuser="on",
            groups=[self.group.pk], user_permissions=[self.permission.pk], password="forged-plaintext",
        ))
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.password, old_hash)
        self.assertTrue(self.customer.is_staff)
        self.assertTrue(self.customer.is_superuser)
        self.assertEqual(self.customer.account_type, "employee")
        self.assertTrue(self.customer.groups.filter(pk=self.group.pk).exists())
        self.assertTrue(self.customer.user_permissions.filter(pk=self.permission.pk).exists())

    def test_standard_admin_password_change_hashes_new_password(self):
        self.client.force_login(self.root)
        new_password = "Replacement-only-test-482!"
        url = self.password_url(self.customer)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"password1": new_password, "password2": new_password})
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password(new_password))
        self.assertFalse(self.customer.check_password(PASSWORD))

    def test_admin_block_unblock_is_not_automatic_reactivation(self):
        self.client.force_login(self.root)
        response = self.client.post(self.change_url(self.customer), self.change_data(self.customer, status="blocked"))
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        response = self.client.post(self.change_url(self.customer), {
            "phone": self.customer.phone, "account_type": "customer", "status": "active", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)

    def test_staff_with_change_permission_cannot_escalate_self(self):
        self.client.force_login(self.staff)
        original_permissions = set(self.staff.user_permissions.values_list("pk", flat=True))
        response = self.client.post(self.change_url(self.staff), self.change_data(
            self.staff, account_type="employee", is_staff="on", is_superuser="on", groups=[self.group.pk],
        ))
        self.assertEqual(response.status_code, 403)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser)
        self.assertEqual(self.staff.account_type, "customer")
        self.assertFalse(self.staff.groups.exists())
        self.assertEqual(set(self.staff.user_permissions.values_list("pk", flat=True)), original_permissions)

    def test_staff_crafted_post_cannot_assign_privileges_or_type_to_customer(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.change_url(self.customer), self.change_data(
            self.customer, account_type="employee", is_staff="on", is_superuser="on",
            groups=[self.group.pk], user_permissions=[self.permission.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.account_type, "customer")
        self.assertFalse(self.customer.is_staff)
        self.assertFalse(self.customer.is_superuser)
        self.assertFalse(self.customer.groups.exists())
        self.assertFalse(self.customer.user_permissions.exists())

    def test_staff_crafted_add_cannot_assign_privileges_or_type(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("admin:accounts_account_add"), self.add_data(
            account_type="employee", is_staff="on", is_superuser="on",
            groups=[self.group.pk], user_permissions=[self.permission.pk],
        ))
        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(phone="+447911123456")
        self.assertEqual(account.account_type, "customer")
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertFalse(account.groups.exists())
        self.assertFalse(account.user_permissions.exists())

    def test_staff_cannot_take_over_privileged_accounts_or_employee(self):
        self.client.force_login(self.staff)
        for privileges in (
            {"is_staff": True}, {"is_superuser": True}, {"account_type": "employee"},
            {"group": True}, {"direct_permission": True},
        ):
            with self.subTest(privileges=privileges):
                self.customer.is_staff = privileges.get("is_staff", False)
                self.customer.is_superuser = privileges.get("is_superuser", False)
                self.customer.account_type = privileges.get("account_type", "customer")
                self.customer.save()
                self.customer.groups.set([self.group] if privileges.get("group") else [])
                self.customer.user_permissions.set([self.permission] if privileges.get("direct_permission") else [])
                self.customer.refresh_from_db()
                response = self.client.post(self.change_url(self.customer), self.change_data(
                    self.customer, phone="+447911123456",
                ))
                self.assertEqual(response.status_code, 403)
                response = self.client.post(self.password_url(self.customer), {
                    "password1": "Takeover-test-secret-529!", "password2": "Takeover-test-secret-529!",
                })
                self.assertEqual(response.status_code, 403)
                self.customer.refresh_from_db()
                self.assertEqual(self.customer.phone, "+12025550123")
                self.assertTrue(self.customer.check_password(PASSWORD))

    def test_staff_cannot_change_own_password_through_privileged_target_route(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.password_url(self.staff), {
            "password1": "Replacement-test-secret-529!", "password2": "Replacement-test-secret-529!",
        })
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_escalate_through_group_edits_or_group_creation(self):
        self.staff.groups.add(self.group)
        self.client.force_login(self.staff)
        for url, payload in (
            (reverse("admin:auth_group_change", args=[self.group.pk]), {
                "name": "Injected", "permissions": [self.permission.pk], "_save": "Save",
            }),
            (reverse("admin:auth_group_add"), {"name": "Injected", "permissions": [self.permission.pk], "_save": "Save"}),
            (reverse("admin:auth_group_delete", args=[self.group.pk]), {"post": "yes"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Test administrators")
        self.assertFalse(Group.objects.filter(name="Injected").exists())

    def test_staff_cannot_bulk_delete_privileged_accounts(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("admin:accounts_account_changelist"), {
            "action": "delete_selected", "_selected_action": [str(self.root.pk)], "post": "yes",
        })
        self.assertIn(response.status_code, (200, 403))
        self.assertTrue(Account.objects.filter(pk=self.root.pk).exists())

    def test_staff_flag_without_permission_is_insufficient(self):
        self.staff.user_permissions.clear()
        self.client.force_login(self.staff)
        response = self.client.post(self.change_url(self.customer), self.change_data(self.customer))
        self.assertEqual(response.status_code, 403)

    def test_customer_and_employee_cannot_open_admin(self):
        for account_type in ("customer", "employee"):
            with self.subTest(account_type=account_type):
                self.customer.account_type = account_type
                self.customer.save()
                self.client.force_login(self.customer)
                self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_blocked_or_inactive_staff_cannot_keep_admin_session(self):
        for status in ("active", "blocked"):
            with self.subTest(status=status):
                self.client.force_login(self.root)
                Account.objects.filter(pk=self.root.pk).update(status=status, is_active=False)
                self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
                Account.objects.filter(pk=self.root.pk).update(status="active", is_active=True)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EmployeeAdminTests(TestCase):
    def setUp(self):
        self.root = Account.objects.create_superuser(
            "+79991234567", PASSWORD, account_type="customer", status="active",
        )
        self.employee_account = Account.objects.create_user("+4915123456789", PASSWORD, account_type="employee")
        self.point = PickupPoint.objects.create(
            name="Test pickup point", address="Test address", latitude="55.750000",
            longitude="37.610000", status="test-fixture",
        )

    def test_employee_form_choices_and_crafted_customer_rejection(self):
        form = EmployeeForm()
        self.assertIn(self.employee_account, form.fields["account"].queryset)
        self.assertNotIn(self.root, form.fields["account"].queryset)
        form = EmployeeForm(data={"account": self.root.pk, "pickup_point": self.point.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("account", form.errors)

    def test_admin_add_and_reassign_employee_pickup_point(self):
        self.client.force_login(self.root)
        url = reverse("admin:accounts_employee_add")
        response = self.client.post(url, {
            "account": self.employee_account.pk, "pickup_point": self.point.pk, "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(account=self.employee_account)
        another = PickupPoint.objects.create(
            name="Second test point", address="Test address", latitude="55.760000",
            longitude="37.620000", status="test-fixture",
        )
        response = self.client.post(reverse("admin:accounts_employee_change", args=[employee.pk]), {
            "account": self.employee_account.pk, "pickup_point": another.pk, "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertEqual(employee.pickup_point, another)

    def test_admin_employee_add_rejects_customer_and_missing_point(self):
        self.client.force_login(self.root)
        for data in (
            {"account": self.root.pk, "pickup_point": self.point.pk},
            {"account": self.employee_account.pk},
        ):
            with self.subTest(fields=tuple(data)):
                response = self.client.post(reverse("admin:accounts_employee_add"), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
        self.assertFalse(Employee.objects.exists())
