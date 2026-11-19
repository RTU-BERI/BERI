from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework.generics import GenericAPIView

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from apps.accounts.access import get_active_account
from apps.accounts.permissions import (
    CanPerformPhysicalOperation,
    ClientOwnedQuerysetMixin,
    EmployeePickupPointQuerysetMixin,
    IsClientOwner,
)
from apps.accounts.models import Account
from apps.reservations.models import Reservation
from apps.reservations.services import (
    ReservationConflict,
    cancel_reservation,
    create_reservation,
)

from .serializers import (
    CreateReservationSerializer,
    ReservationSerializer,
)


class ReservationConflictAPI(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Операция конфликтует с текущим состоянием бронирования."
    default_code = "reservation_conflict"


class ReservationListCreateAPIView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        account = get_active_account(request.user)
        if account is None or account.account_type != Account.AccountType.CUSTOMER:
            raise PermissionDenied()

        reservations = (
            Reservation.objects.filter(customer_account_id=account.pk)
            .select_related(
                "initial_product_item",
                "customer_account",
            )
            .order_by("-created_at")
        )
        return Response(ReservationSerializer(reservations, many=True).data)

    def post(self, request):
        account = get_active_account(request.user)
        if account is None or account.account_type != Account.AccountType.CUSTOMER:
            raise PermissionDenied()

        serializer = CreateReservationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            reservation = create_reservation(
                customer_account_id=account.pk,
                **serializer.validated_data,
            )
        except ReservationConflict as error:
            raise ReservationConflictAPI(str(error)) from error
        except ValidationError as error:
            raise ReservationConflictAPI(str(error)) from error

        reservation = Reservation.objects.select_related(
            "initial_product_item",
            "customer_account",
        ).get(pk=reservation.pk)

        return Response(
            ReservationSerializer(reservation).data,
            status=status.HTTP_201_CREATED,
        )


class ReservationCancelAPIView(ClientOwnedQuerysetMixin, GenericAPIView):
    permission_classes = (IsClientOwner,)
    queryset = Reservation.objects.all()
    owner_lookup = "customer_account_id"

    def post(self, request, pk):
        reservation = get_object_or_404(self.get_queryset(), pk=pk)

        try:
            cancelled = cancel_reservation(reservation.pk)
        except ReservationConflict as error:
            raise ReservationConflictAPI(str(error)) from error
        except ValidationError as error:
            raise ReservationConflictAPI(str(error)) from error

        if not cancelled:
            raise ReservationConflictAPI(
                "Бронирование уже завершено или отменено."
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeReservationListAPIView(
    EmployeePickupPointQuerysetMixin,
    ListAPIView,
):
    queryset = Reservation.objects.select_related(
        "initial_product_item",
        "assigned_product_item",
        "customer_account",
    ).order_by("-created_at")

    serializer_class = ReservationSerializer
    permission_classes = (CanPerformPhysicalOperation,)
    pickup_point_lookup = "pickup_point_id"
    required_permissions = ("accounts.view_employee",)