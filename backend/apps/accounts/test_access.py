"""Authorization tests with isolated routes, never production endpoints."""

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import serializers
from rest_framework.generics import GenericAPIView, ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from apps.pickup_points.models import PickupPoint

from .access import (
    get_active_account,
    get_employee,
    has_permissions,
    require_physical_access,
)
from .models import Account, Employee
from .permissions import (
    CanPerformPhysicalOperation,
    ClientOwnedQuerysetMixin,
    EmployeePickupPointQuerysetMixin,
    HasDjangoPermissions,
    IsClient,
    IsClientOwner,
)


class AccountIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ("id",)


class EmployeeIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ("id", "pickup_point_id")


class OwnAccountsView(ClientOwnedQuerysetMixin, ListAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountIdentitySerializer
    permission_classes = (IsClientOwner,)
    owner_lookup = "pk"
    pagination_class = None


class OwnAccountDetailView(ClientOwnedQuerysetMixin, RetrieveAPIView):
    queryset = Account.objects.all()
    serializer_class = AccountIdentitySerializer
    permission_classes = (IsClientOwner,)
    owner_lookup = "pk"


class PhysicalEmployeesView(EmployeePickupPointQuerysetMixin, ListAPIView):
    queryset = Employee.objects.all()
    serializer_class = EmployeeIdentitySerializer
    permission_classes = (CanPerformPhysicalOperation,)
    pickup_point_lookup = "pickup_point_id"
    required_permissions = ("accounts.view_employee",)
    pagination_class = None


class PhysicalEmployeeDetailView(
    EmployeePickupPointQuerysetMixin, RetrieveAPIView
):
    queryset = Employee.objects.all()
    serializer_class = EmployeeIdentitySerializer
    permission_classes = (CanPerformPhysicalOperation,)
    pickup_point_lookup = "pickup_point_id"
    required_permissions = ("accounts.view_employee",)


class PhysicalWriteView(EmployeePickupPointQuerysetMixin, GenericAPIView):
    queryset = Employee.objects.all()
    permission_classes = (CanPerformPhysicalOperation,)
    pickup_point_lookup = "pickup_point_id"
    required_permissions = ("accounts.change_employee",)

    def get_physical_pickup_point_id(self):
        # URL identifies a real stored object; neither payload nor query scope
        # is used as evidence. get_object checks the already-scoped queryset.
        return self.get_object().pickup_point_id

    def post(self, request, *args, **kwargs):
        employee = require_physical_access(
            request.user,
            self.get_physical_pickup_point_id(),
            self.required_permissions,
        )
        return Response({"employee_id": employee.pk})


class MissingWriteResolverView(PhysicalWriteView):
    get_physical_pickup_point_id = None


class AdministrativeView(APIView):
    permission_classes = (HasDjangoPermissions,)
    required_permissions = ("accounts.change_account",)

    def post(self, request):
        return Response({"allowed": True})


class MissingPermissionsView(AdministrativeView):
    required_permissions = ()


urlpatterns = [
    path("own/", OwnAccountsView.as_view()),
    path("own/<uuid:pk>/", OwnAccountDetailView.as_view()),
    path("physical/", PhysicalEmployeesView.as_view()),
    path("physical/<int:pk>/", PhysicalEmployeeDetailView.as_view()),
    path("physical/<int:pk>/operate/", PhysicalWriteView.as_view()),
    path("missing-resolver/<int:pk>/", MissingWriteResolverView.as_view()),
    path("administrative/", AdministrativeView.as_view()),
    path("missing-permissions/", MissingPermissionsView.as_view()),
]


@override_settings(ROOT_URLCONF=__name__)
class AccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.point = PickupPoint.objects.create(
            name="First point", address="First address",
            latitude="55.750000", longitude="37.610000", status="test",
        )
        cls.other_point = PickupPoint.objects.create(
            name="Second point", address="Second address",
            latitude="55.760000", longitude="37.620000", status="test",
        )
        cls.client_account = cls.make_account("+79991234567", "customer")
        cls.other_client = cls.make_account("+79991234568", "customer")
        cls.worker_account = cls.make_account("+79991234569", "employee")
        cls.other_worker = cls.make_account("+79991234570", "employee")
        cls.profileless = cls.make_account("+79991234571", "employee")
        cls.staff = cls.make_account("+79991234572", "customer", is_staff=True)
        cls.worker = Employee.objects.create(
            account=cls.worker_account, pickup_point=cls.point,
        )
        cls.foreign_worker = Employee.objects.create(
            account=cls.other_worker, pickup_point=cls.other_point,
        )
        cls.view_employee = Permission.objects.get(
            content_type__app_label="accounts", codename="view_employee"
        )
        cls.change_employee = Permission.objects.get(
            content_type__app_label="accounts", codename="change_employee"
        )
        cls.change_account = Permission.objects.get(
            content_type__app_label="accounts", codename="change_account"
        )
        cls.worker_account.user_permissions.add(
            cls.view_employee, cls.change_employee,
        )
        cls.other_worker.user_permissions.add(cls.view_employee)

    @staticmethod
    def make_account(phone, account_type, **extra):
        account = Account.objects.create_user(
            phone=phone, password="Access-test-password-538!",
            account_type=account_type, status="active",
        )
        if extra:
            Account.objects.filter(pk=account.pk).update(**extra)
            account.refresh_from_db()
        return account

    def setUp(self):
        self.api = APIClient()

    def authenticate(self, account):
        self.api.force_authenticate(account)

    def test_client_list_and_detail_are_scoped_by_database_owner(self):
        self.authenticate(self.client_account)
        response = self.api.get("/own/", {"owner": str(self.other_client.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": str(self.client_account.pk)}])
        self.assertEqual(
            self.api.get(f"/own/{self.other_client.pk}/").status_code, 404
        )
        self.assertEqual(
            self.api.get(f"/own/{self.client_account.pk}/").status_code, 200
        )

    def test_anonymous_cannot_access_personal_or_physical_data(self):
        for url in ("/own/", "/physical/", "/administrative/"):
            with self.subTest(url=url):
                response = self.api.post(url) if "admin" in url else self.api.get(url)
                self.assertIn(response.status_code, (401, 403))

    def test_employee_cannot_use_client_only_operations(self):
        self.authenticate(self.worker_account)
        self.assertEqual(self.api.get("/own/").status_code, 403)

    def test_client_cannot_use_employee_operations(self):
        self.authenticate(self.client_account)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_employee_list_is_filtered_to_current_pickup_point(self):
        self.authenticate(self.worker_account)
        response = self.api.get(
            "/physical/", {"pickup_point_id": self.other_point.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.json()], [self.worker.pk]
        )
        self.assertEqual(
            self.api.get(f"/physical/{self.foreign_worker.pk}/").status_code, 404
        )

    def test_profileless_account_is_denied_even_with_permission(self):
        self.profileless.user_permissions.add(self.view_employee)
        self.authenticate(self.profileless)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_employee_requires_business_permission(self):
        self.worker_account.user_permissions.clear()
        self.authenticate(self.worker_account)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_staff_flag_does_not_grant_administrative_permission(self):
        self.authenticate(self.staff)
        self.assertEqual(self.api.post("/administrative/").status_code, 403)

    def test_explicit_group_permission_grants_administrative_operation(self):
        group = Group.objects.create(name="Test account managers")
        group.permissions.add(self.change_account)
        self.staff.groups.add(group)
        self.authenticate(self.staff)
        self.assertEqual(self.api.post("/administrative/").status_code, 200)

    def test_payload_cannot_grant_permissions_or_change_scope(self):
        self.authenticate(self.client_account)
        response = self.api.post("/administrative/", {
            "is_staff": True, "is_superuser": True,
            "account_type": "employee", "permissions": ["change_account"],
        }, format="json")
        self.assertEqual(response.status_code, 403)
        self.client_account.refresh_from_db()
        self.assertFalse(self.client_account.is_staff)
        self.assertFalse(self.client_account.is_superuser)
        self.assertEqual(self.client_account.account_type, "customer")

    def test_superuser_without_employee_cannot_perform_physical_operation(self):
        Account.objects.filter(pk=self.staff.pk).update(is_superuser=True)
        self.authenticate(self.staff)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_superuser_with_employee_remains_bound_to_own_point(self):
        Account.objects.filter(pk=self.worker_account.pk).update(is_superuser=True)
        self.authenticate(self.worker_account)
        self.assertEqual(self.api.get("/physical/").status_code, 200)
        self.assertEqual(
            self.api.post(
                f"/physical/{self.foreign_worker.pk}/operate/",
                {"pickup_point_id": self.point.pk}, format="json",
            ).status_code, 404,
        )
        with self.assertRaises(PermissionDenied):
            require_physical_access(
                self.worker_account, self.other_point.pk,
                ("accounts.change_employee",),
            )

    def test_transfer_is_effective_with_a_stale_authenticated_account(self):
        self.authenticate(self.worker_account)
        # Populate reverse relation cache before the database changes.
        self.assertEqual(self.worker_account.employee.pickup_point_id, self.point.pk)
        Employee.objects.filter(pk=self.worker.pk).update(
            pickup_point=self.other_point
        )
        employee = get_employee(self.worker_account)
        self.assertEqual(employee.pickup_point_id, self.other_point.pk)
        response = self.api.get("/physical/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)
        self.assertTrue(all(
            row["pickup_point_id"] == self.other_point.pk for row in response.json()
        ))
        with self.assertRaises(PermissionDenied):
            require_physical_access(
                self.worker_account, self.point.pk, ("accounts.change_employee",)
            )

    def test_deleted_profile_invalidates_existing_authentication(self):
        self.worker_account.employee  # Populate Django's reverse relation cache.
        Employee.objects.filter(pk=self.worker.pk).delete()
        self.authenticate(self.worker_account)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_permission_revocation_ignores_django_instance_cache(self):
        self.worker_account.has_perm("accounts.view_employee")
        self.worker_account.user_permissions.clear()
        self.assertFalse(has_permissions(
            self.worker_account, ("accounts.view_employee",)
        ))

    def test_group_revocation_ignores_django_instance_cache(self):
        group = Group.objects.create(name="Temporary managers")
        group.permissions.add(self.change_account)
        self.staff.groups.add(group)
        self.assertTrue(self.staff.has_perm("accounts.change_account"))
        group.permissions.clear()
        self.assertFalse(has_permissions(self.staff, ("accounts.change_account",)))

    def test_blocking_invalidates_stale_request_user(self):
        Account.objects.filter(pk=self.worker_account.pk).update(
            status="blocked", is_active=False,
        )
        self.assertIsNone(get_active_account(self.worker_account))
        self.assertIsNone(get_employee(self.worker_account))
        self.authenticate(self.worker_account)
        self.assertEqual(self.api.get("/physical/").status_code, 403)

    def test_inactive_customer_is_denied_despite_stale_session(self):
        Account.objects.filter(pk=self.client_account.pk).update(is_active=False)
        self.authenticate(self.client_account)
        self.assertEqual(self.api.get("/own/").status_code, 403)

    def test_deleting_account_invalidates_stale_identity(self):
        user = self.make_account("+79991234573", "customer")
        pk = user.pk
        Account.objects.filter(pk=pk).delete()
        self.assertIsNone(get_active_account(user))

    def test_missing_permissions_fail_closed_even_for_superuser(self):
        Account.objects.filter(pk=self.staff.pk).update(is_superuser=True)
        self.authenticate(self.staff)
        self.assertEqual(self.api.post("/missing-permissions/").status_code, 403)
        for declaration in (None, (), [], "accounts.view_employee", ("invalid",)):
            with self.subTest(declaration=declaration):
                self.assertFalse(has_permissions(self.staff, declaration))

    def test_write_needs_explicit_server_resolver(self):
        self.authenticate(self.worker_account)
        self.assertEqual(
            self.api.post(f"/missing-resolver/{self.worker.pk}/").status_code, 403
        )

    def test_physical_write_uses_server_object_instead_of_payload(self):
        self.authenticate(self.worker_account)
        response = self.api.post(
            f"/physical/{self.worker.pk}/operate/",
            {"pickup_point_id": self.other_point.pk,
             "employee_id": self.foreign_worker.pk}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"employee_id": self.worker.pk})

    def test_service_rejects_missing_point_and_missing_permission(self):
        for point, permissions in (
            (None, ("accounts.view_employee",)),
            (self.point.pk, ()),
            (self.point.pk, ("accounts.delete_account",)),
        ):
            with self.subTest(point=point, permissions=permissions):
                with self.assertRaises(PermissionDenied):
                    require_physical_access(self.worker_account, point, permissions)

    def test_helpers_reject_anonymous_and_invalid_identities(self):
        for user in (
            AnonymousUser(), None,
            SimpleNamespace(is_authenticated=True, pk="invalid-uuid"),
        ):
            with self.subTest(user=user):
                self.assertIsNone(get_active_account(user))
                self.assertIsNone(get_employee(user))

    def test_permissions_require_scope_mixins_and_configuration(self):
        request = SimpleNamespace(user=self.client_account, method="GET")
        self.assertFalse(IsClientOwner().has_permission(
            request, SimpleNamespace(owner_lookup="pk")
        ))
        view = OwnAccountsView()
        view.owner_lookup = None
        self.assertFalse(IsClientOwner().has_permission(request, view))
        view.request = request
        self.assertFalse(view.get_queryset().exists())
        request.user = self.worker_account
        physical_view = PhysicalEmployeesView()
        physical_view.pickup_point_lookup = None
        physical_view.request = request
        self.assertFalse(CanPerformPhysicalOperation().has_permission(
            request, physical_view
        ))
        self.assertFalse(physical_view.get_queryset().exists())

    def test_object_permission_checks_do_not_trust_cached_object_scope(self):
        request = SimpleNamespace(user=self.worker_account, method="GET")
        obj = Employee.objects.get(pk=self.foreign_worker.pk)
        obj.pickup_point_id = self.point.pk
        self.assertFalse(CanPerformPhysicalOperation().has_object_permission(
            request, PhysicalEmployeesView(), obj
        ))
        request.user = self.client_account
        self.assertFalse(IsClientOwner().has_object_permission(
            request, OwnAccountsView(), self.other_client
        ))

    def test_is_client_uses_fresh_account_type(self):
        request = SimpleNamespace(user=self.profileless, method="GET")
        self.assertFalse(IsClient().has_permission(request, SimpleNamespace()))
        current = Account.objects.get(pk=self.profileless.pk)
        current.account_type = "customer"
        current.save(update_fields=["account_type"])
        self.assertTrue(IsClient().has_permission(request, SimpleNamespace()))
