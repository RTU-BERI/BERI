from django.urls import path

from .views import (
    EmployeeReservationListAPIView,
    ReservationCancelAPIView,
    ReservationListCreateAPIView,
)

urlpatterns = [
    path(
        "reservations/",
        ReservationListCreateAPIView.as_view(),
        name="reservation-list-create",
    ),
    path(
        "reservations/<uuid:pk>/cancel/",
        ReservationCancelAPIView.as_view(),
        name="reservation-cancel",
    ),
    path(
        "employee/reservations/",
        EmployeeReservationListAPIView.as_view(),
        name="employee-reservation-list",
    ),
]