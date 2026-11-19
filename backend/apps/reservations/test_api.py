
import uuid

from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Reservation
from .test_support import FixtureMixin


class ReservationAPITests(FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.customer)

    def test_customer_can_create_reservation(self):
        request_id = uuid.uuid4()

        response = self.client_api.post(
            "/api/reservations/",
            {
                "product_id": self.product.pk,
                "pickup_point_id": self.point.pk,
                "request_id": str(request_id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Reservation.objects.filter(pk=request_id).count(), 1
        )
        self.assertEqual(response.data["status"], "active")

    def test_repeated_request_does_not_create_duplicate(self):
        request_id = uuid.uuid4()
        payload = {
            "product_id": self.product.pk,
            "pickup_point_id": self.point.pk,
            "request_id": str(request_id),
        }

        first_response = self.client_api.post(
            "/api/reservations/", payload, format="json"
        )
        second_response = self.client_api.post(
            "/api/reservations/", payload, format="json"
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(first_response.data["id"], second_response.data["id"])
        self.assertEqual(
            Reservation.objects.filter(pk=request_id).count(), 1
        )

    def test_customer_sees_only_own_reservations(self):
        own_reservation = self.make_reservation(customer=self.customer)
        self.make_reservation(
            customer=self.other_customer,
            item=self.make_item(product=self.other_product),
        )

        response = self.client_api.get("/api/reservations/")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(str(own_reservation.pk), returned_ids)
        self.assertEqual(len(returned_ids), 1)

    def test_customer_can_cancel_own_reservation(self):
        reservation = self.make_reservation(customer=self.customer)

        response = self.client_api.post(
            f"/api/reservations/{reservation.pk}/cancel/"
        )

        self.assertEqual(response.status_code, 204)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "cancelled")

    def test_customer_cannot_cancel_other_customers_reservation(self):
        reservation = self.make_reservation(customer=self.other_customer)

        response = self.client_api.post(
            f"/api/reservations/{reservation.pk}/cancel/"
        )

        self.assertEqual(response.status_code, 404)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "active")


class EmployeeReservationAPITests(FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()

        permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="view_employee",
        )
        self.employee.account.user_permissions.add(permission)

        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.employee.account)

    def test_employee_sees_only_reservations_of_own_pickup_point(self):
        own_reservation = self.make_reservation(
            customer=self.customer,
            item=self.item,
            pickup_point=self.point,
        )

        other_point_item = self.make_item(
            product=self.other_product,
            pickup_point=self.other_point,
        )
        self.make_reservation(
            customer=self.other_customer,
            item=other_point_item,
            pickup_point=self.other_point,
        )

        response = self.client_api.get("/api/employee/reservations/")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(str(own_reservation.pk), returned_ids)
        self.assertEqual(len(returned_ids), 1)

    def test_customer_cannot_access_employee_reservations(self):
        self.client_api.force_authenticate(user=self.customer)

        response = self.client_api.get("/api/employee/reservations/")

        self.assertEqual(response.status_code, 403)
