"""Admin can create Employees, but cannot reassign their historical identity."""

from django.test import Client, TestCase
from django.urls import reverse

from apps.pickup_points.models import PickupPoint
from apps.rentals.test_support import FixtureMixin as RentalFixtures

from .forms import EmployeeForm
from .models import Account, Employee
from .test_account_admin import AdminFixturesMixin


class EmployeeAdminTests(AdminFixturesMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target.account_type = "employee"
        cls.target.save(update_fields=["account_type"])
        cls.other_service = Account.objects.create_user(
            phone="+15550001004", password=None,
            account_type="employee", status="opaque-fixture",
        )
        cls.point = PickupPoint.objects.create(
            name="Synthetic employee point", address="Synthetic address",
            latitude="55.000000", longitude="37.000000", status="fixture",
        )
        cls.other_point = PickupPoint.objects.create(
            name="Other synthetic point", address="Other synthetic address",
            latitude="56.000000", longitude="38.000000", status="fixture",
        )
        cls.employee = Employee.objects.create(account=cls.target, pickup_point=cls.point)

    def setUp(self):
        self.list_url = reverse("admin:accounts_employee_changelist")
        self.add_url = reverse("admin:accounts_employee_add")
        self.change_url = reverse("admin:accounts_employee_change", args=[self.employee.pk])
        self.delete_url = reverse("admin:accounts_employee_delete", args=[self.employee.pk])

    def test_staff_with_employee_permissions_cannot_read_or_manage_profiles(self):
        self.client.force_login(self.staff)
        for url in (self.list_url, self.add_url, self.change_url, self.delete_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {
                    "account": str(self.other_service.pk), "pickup_point": self.other_point.pk,
                    "post": "yes", "_save": "Save",
                }).status_code, 403)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.pickup_point_id, self.point.pk)

    def test_superuser_can_create_employee_for_existing_service_account(self):
        self.login_actor()
        response = self.client.post(self.add_url, {
            "account": str(self.other_service.pk), "pickup_point": self.point.pk, "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Employee.objects.filter(account=self.other_service, pickup_point=self.point).exists())

    def test_customer_account_is_not_selectable_or_accepted(self):
        self.login_actor()
        response = self.client.get(self.add_url)
        form = response.context["adminform"].form
        self.assertFalse(form.fields["account"].queryset.filter(pk=self.actor.pk).exists())
        response = self.client.post(self.add_url, {
            "account": str(self.actor.pk), "pickup_point": self.point.pk, "_save": "Save",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("account", response.context["adminform"].form.errors)
        self.assertFalse(Employee.objects.filter(account=self.actor).exists())

    def test_existing_bindings_are_readonly_and_noop_post_preserves_them(self):
        self.login_actor()
        response = self.client.get(self.change_url)
        self.assertIn("account", response.context["adminform"].readonly_fields)
        self.assertIn("pickup_point", response.context["adminform"].readonly_fields)
        self.assertEqual(self.client.post(self.change_url, {"_save": "Save"}).status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.account_id, self.target.pk)
        self.assertEqual(self.employee.pickup_point_id, self.point.pk)

    def test_forged_account_or_pickup_point_changes_are_rejected(self):
        self.login_actor()
        for data in (
            {"account": str(self.other_service.pk)},
            {"pickup_point": str(self.other_point.pk)},
        ):
            with self.subTest(data=data):
                response = self.client.post(self.change_url, {**data, "_save": "Save"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.non_field_errors())
                self.employee.refresh_from_db()
                self.assertEqual(self.employee.account_id, self.target.pk)
                self.assertEqual(self.employee.pickup_point_id, self.point.pk)

    def test_direct_employee_form_also_rejects_binding_changes(self):
        form = EmployeeForm(instance=self.employee, data={
            "account": str(self.other_service.pk), "pickup_point": str(self.other_point.pk),
        })
        self.assertFalse(form.is_valid())

    def test_employee_account_type_is_readonly_and_forged_change_rejected(self):
        self.login_actor()
        url = reverse("admin:accounts_account_change", args=[self.target.pk])
        response = self.client.get(url)
        self.assertIn("account_type", response.context["adminform"].readonly_fields)
        response = self.client.post(url, self.account_data(account_type="customer"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.non_field_errors())
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_type, "employee")

    def test_other_fields_of_employee_account_remain_editable_by_superuser(self):
        self.login_actor()
        url = reverse("admin:accounts_account_change", args=[self.target.pk])
        response = self.client.post(url, self.account_data(status="new-opaque-value"))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "new-opaque-value")
        self.assertEqual(self.target.account_type, "employee")

    def test_account_delete_retains_existing_employee_protect(self):
        self.login_actor()
        url = reverse("admin:accounts_account_delete", args=[self.target.pk])
        response = self.client.post(url, {"post": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertTrue(Account.objects.filter(pk=self.target.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_unreferenced_employee_can_be_deleted_without_deleting_account(self):
        self.login_actor()
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 302)
        self.assertFalse(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertTrue(Account.objects.filter(pk=self.target.pk).exists())

    def test_staff_cannot_forge_employee_bulk_delete(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.list_url, {
            "action": "delete_selected", "_selected_action": [str(self.employee.pk)], "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_employee_admin_enforces_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.login_actor(client)
        for url in (self.add_url, self.change_url, self.delete_url):
            with self.subTest(url=url):
                self.assertEqual(client.post(url, {"post": "yes", "_save": "Save"}).status_code, 403)

    def test_malformed_employee_route_ids_do_not_raise_server_errors(self):
        self.login_actor()
        for value in ("not-an-integer", "²", "①", str(1 << 65)):
            url = reverse("admin:accounts_employee_change", args=[value])
            with self.subTest(value=value):
                self.assertIn(self.client.get(url).status_code, (302, 404))

    def test_malformed_employee_bulk_selection_is_denied_without_deletion(self):
        self.login_actor()
        for value in ("not-an-integer", "²", "①", str(1 << 65), "1" * 4400):
            with self.subTest(value=value[:20]):
                response = self.client.post(self.list_url, {
                    "action": "delete_selected", "_selected_action": [value], "post": "yes",
                })
                self.assertEqual(response.status_code, 403)
                self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())


class EmployeeHistoryAdminTests(RentalFixtures, TestCase):
    def test_employee_with_inspection_cannot_be_deleted_from_admin(self):
        inspection = self.make_inspection()
        actor = Account.objects.create_superuser(
            phone="+15550001999", password="Synthetic-history-fixture-42",
            account_type="customer", status="opaque-fixture",
        )
        self.client.force_login(actor)
        url = reverse("admin:accounts_employee_delete", args=[self.employee.pk])
        response = self.client.post(url, {"post": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        inspection.refresh_from_db()
        self.assertEqual(inspection.employee_id, self.employee.pk)
