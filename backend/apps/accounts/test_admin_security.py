"""Negative Admin tests and real PostgreSQL lock-boundary checks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from apps.pickup_points.models import PickupPoint

from .admin_access import is_trusted_admin, trusted_admin_transaction
from .models import Account, Employee
from .test_account_admin import AdminFixturesMixin


class AdminSecurityTests(AdminFixturesMixin, TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Synthetic sensitive group")
        self.group_list = reverse("admin:auth_group_changelist")
        self.group_add = reverse("admin:auth_group_add")
        self.group_change = reverse("admin:auth_group_change", args=[self.group.pk])
        self.group_delete = reverse("admin:auth_group_delete", args=[self.group.pk])

    def test_group_model_permissions_do_not_delegate_group_administration(self):
        self.client.force_login(self.staff)
        permission = Permission.objects.get(codename="change_account")
        for url in (self.group_list, self.group_add, self.group_change, self.group_delete):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {
                    "name": "Forged group", "permissions": [permission.pk], "post": "yes",
                }).status_code, 403)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Synthetic sensitive group")
        self.assertFalse(self.group.permissions.exists())

    def test_staff_cannot_delete_groups_through_bulk_action(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.group_list, {
            "action": "delete_selected", "_selected_action": [str(self.group.pk)], "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_superuser_can_manage_group_and_its_permissions(self):
        self.login_actor()
        permission = Permission.objects.get(codename="view_productcategory")
        self.assertEqual(self.client.get(self.group_list).status_code, 200)
        response = self.client.post(self.group_change, {
            "name": "Authorized synthetic group", "permissions": [str(permission.pk)], "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Authorized synthetic group")
        self.assertTrue(self.group.permissions.filter(pk=permission.pk).exists())
        self.assertEqual(self.client.post(self.group_delete, {"post": "yes"}).status_code, 302)

    def test_group_write_and_bulk_routes_enforce_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.login_actor(client)
        for url in (self.group_add, self.group_change, self.group_delete, self.group_list):
            with self.subTest(url=url):
                self.assertEqual(client.post(url, {
                    "name": "Missing CSRF", "post": "yes", "action": "delete_selected",
                    "_selected_action": [str(self.group.pk)],
                }).status_code, 403)

    def test_stale_privileged_actor_does_not_pass_direct_permission_checks(self):
        request = RequestFactory().get("/admin/")
        request.user = Account.objects.get(pk=self.actor.pk)
        Account.objects.filter(pk=self.actor.pk).update(is_superuser=False)
        self.assertFalse(is_trusted_admin(request))
        for model in (Account, Employee, Group):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_module_permission(request))
            self.assertFalse(model_admin.has_view_permission(request))
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertFalse(model_admin.get_queryset(request).exists())

    def test_direct_save_and_delete_hooks_reject_stale_actor(self):
        request = RequestFactory().post("/admin/")
        request.user = Account.objects.get(pk=self.actor.pk)
        Account.objects.filter(pk=self.actor.pk).update(is_active=False)
        model_admin = admin.site._registry[Account]
        self.target.status = "forged-stale-write"
        for callback in (
            lambda: model_admin.save_model(request, self.target, None, True),
            lambda: model_admin.delete_model(request, self.target),
            lambda: model_admin.delete_queryset(request, Account.objects.filter(pk=self.target.pk)),
            lambda: model_admin.save_related(request, None, [], True),
            lambda: model_admin.user_change_password(request, str(self.target.pk)),
        ):
            with self.assertRaises(PermissionDenied):
                callback()
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "opaque-fixture")

    def test_direct_account_save_rechecks_employee_before_changing_type(self):
        point = PickupPoint.objects.create(
            name="Synthetic point", address="Synthetic address",
            latitude="55.000000", longitude="37.000000", status="fixture",
        )
        self.target.account_type = "employee"
        self.target.save(update_fields=["account_type"])
        stale = Account.objects.get(pk=self.target.pk)
        Employee.objects.create(account=self.target, pickup_point=point)
        stale.account_type = "customer"
        request = RequestFactory().post("/admin/")
        request.user = self.actor
        with self.assertRaises(ValidationError):
            admin.site._registry[Account].save_model(request, stale, None, True)
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_type, "employee")

    def test_direct_employee_save_cannot_restore_stale_or_forged_binding(self):
        point = PickupPoint.objects.create(
            name="Synthetic point", address="Synthetic address",
            latitude="55.000000", longitude="37.000000", status="fixture",
        )
        other_point = PickupPoint.objects.create(
            name="Other point", address="Other synthetic address",
            latitude="56.000000", longitude="38.000000", status="fixture",
        )
        self.target.account_type = "employee"
        self.target.save(update_fields=["account_type"])
        employee = Employee.objects.create(account=self.target, pickup_point=point)
        employee.pickup_point = other_point
        request = RequestFactory().post("/admin/")
        request.user = self.actor
        with self.assertRaises(PermissionDenied):
            admin.site._registry[Employee].save_model(request, employee, None, True)
        employee.refresh_from_db()
        self.assertEqual(employee.pickup_point_id, point.pk)

    def test_group_name_and_permissions_rollback_together_on_late_failure(self):
        self.login_actor()
        permission = Permission.objects.get(codename="change_account")
        with patch("apps.accounts.admin.TrustedGroupAdmin.log_change", side_effect=RuntimeError("synthetic rollback")):
            with self.assertRaisesMessage(RuntimeError, "synthetic rollback"):
                self.client.post(self.group_change, {
                    "name": "Must roll back", "permissions": [str(permission.pk)], "_save": "Save",
                })
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Synthetic sensitive group")
        self.assertFalse(self.group.permissions.exists())


class AdminLockConcurrencyTests(TransactionTestCase):
    """Coordinate real lock waits; no timing-based assumptions about a race."""

    def setUp(self):
        self.actor = Account.objects.create_superuser(
            phone="+15550002001", password="Synthetic-concurrent-admin-42",
            account_type="customer", status="opaque-fixture",
        )
        self.target = Account.objects.create_user(
            phone="+15550002002", password=None,
            account_type="employee", status="opaque-fixture",
        )
        self.point = PickupPoint.objects.create(
            name="Synthetic lock point", address="Synthetic address",
            latitude="55.000000", longitude="37.000000", status="fixture",
        )

    def _worker(self, attempted, state, callback):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SELECT pg_backend_pid()")
                state["pid"] = cursor.fetchone()[0]

            def detect(execute, sql, params, many, context):
                if "FOR UPDATE" in sql or sql.startswith('UPDATE "account"'):
                    attempted.set()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(detect):
                return callback()
        finally:
            connection.close()

    def _assert_blocked(self, attempted, state):
        self.assertTrue(attempted.wait(5), "Worker did not attempt the Account lock.")
        deadline = monotonic() + 4
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [state["pid"]])
                if cursor.fetchone()[0]:
                    return
            Event().wait(0.01)
        self.fail("PostgreSQL did not confirm the expected row-lock wait.")

    def test_actor_revocation_before_waiting_admin_save_prevents_write(self):
        stale_actor = Account.objects.get(pk=self.actor.pk)
        edited = Account.objects.get(pk=self.target.pk)
        edited.status = "must-not-persist"
        attempted, state = Event(), {}

        def save():
            request = RequestFactory().post("/admin/")
            request.user = stale_actor
            try:
                admin.site._registry[Account].save_model(request, edited, None, True)
            except PermissionDenied:
                return "denied"
            return "written"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Account.objects.select_for_update().get(pk=self.actor.pk)
                future = executor.submit(self._worker, attempted, state, save)
                self._assert_blocked(attempted, state)
                Account.objects.filter(pk=self.actor.pk).update(is_superuser=False)
            self.assertEqual(future.result(timeout=10), "denied")
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "opaque-fixture")

    def test_employee_creation_serializes_with_admin_account_type_change(self):
        stale = Account.objects.get(pk=self.target.pk)
        stale.account_type = "customer"
        attempted, state = Event(), {}

        def change_type():
            request = RequestFactory().post("/admin/")
            request.user = Account.objects.get(pk=self.actor.pk)
            try:
                admin.site._registry[Account].save_model(request, stale, None, True)
            except ValidationError:
                return "denied"
            return "written"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Account.objects.select_for_update().get(pk=self.target.pk)
                Employee.objects.create(account=self.target, pickup_point=self.point)
                future = executor.submit(self._worker, attempted, state, change_type)
                self._assert_blocked(attempted, state)
            self.assertEqual(future.result(timeout=10), "denied")
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_type, "employee")
        self.assertTrue(Employee.objects.filter(account=self.target).exists())

    def test_admin_type_change_committed_first_rejects_waiting_employee_creation(self):
        attempted, state = Event(), {}

        def create_employee():
            try:
                Employee.objects.create(account_id=self.target.pk, pickup_point_id=self.point.pk)
            except ValidationError:
                return "denied"
            return "created"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                target = Account.objects.select_for_update().get(pk=self.target.pk)
                target.account_type = "customer"
                request = RequestFactory().post("/admin/")
                request.user = self.actor
                admin.site._registry[Account].save_model(request, target, None, True)
                future = executor.submit(self._worker, attempted, state, create_employee)
                self._assert_blocked(attempted, state)
            self.assertEqual(future.result(timeout=10), "denied")
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_type, "customer")
        self.assertFalse(Employee.objects.filter(account=self.target).exists())

    def test_authorized_route_holds_actor_lock_through_its_write(self):
        attempted, state = Event(), {}

        def revoke():
            Account.objects.filter(pk=self.actor.pk).update(is_superuser=False)
            return "revoked"

        request = RequestFactory().post("/admin/")
        request.user = self.actor
        with ThreadPoolExecutor(max_workers=1) as executor:
            with trusted_admin_transaction(request, account_ids=(self.target.pk,)):
                future = executor.submit(self._worker, attempted, state, revoke)
                self._assert_blocked(attempted, state)
                self.target.status = "authorized-write"
                admin.site._registry[Account].save_model(request, self.target, None, True)
            self.assertEqual(future.result(timeout=10), "revoked")
        self.actor.refresh_from_db()
        self.target.refresh_from_db()
        self.assertFalse(self.actor.is_superuser)
        self.assertEqual(self.target.status, "authorized-write")
        self.assertFalse(is_trusted_admin(request))
