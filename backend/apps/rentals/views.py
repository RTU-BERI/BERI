"""Session/CSRF-protected employee pre-issue endpoints, scoped to their PV."""

from django.core.exceptions import ValidationError as DomainValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.accounts.permissions import CanPerformPhysicalOperation, EmployeePickupPointQuerysetMixin
from apps.reservations.models import Reservation, ReservationItemAssignment
from apps.reservations.services import ReservationConflict
from .issuance import ISSUE_PERMISSIONS, start_rental
from .models import Inspection
from .serializers import (InspectionInputSerializer, InspectionSerializer, ReplacementInputSerializer,
                          RentalSerializer, RentalStartInputSerializer)
from .services import INSPECT_PERMISSIONS, REPLACE_PERMISSIONS, record_pre_issue, replace_after_inspection


class PreIssueInspectionView(EmployeePickupPointQuerysetMixin, GenericAPIView):
    queryset = Reservation.objects.all()
    pickup_point_lookup = "pickup_point_id"
    permission_classes = (CanPerformPhysicalOperation,)
    serializer_class = InspectionInputSerializer

    @property
    def required_permissions(self):
        return ("rentals.view_inspection",) if self.request.method in ("GET", "HEAD", "OPTIONS") else INSPECT_PERMISSIONS

    def get_physical_pickup_point_id(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"]).pickup_point_id

    def get(self, request, pk):
        # The UI needs the current assignment ID before its first inspection.
        # Only a reservation lock is taken by this read; no item/parent locks
        # follow it, so this does not invert the write-service lock order.
        with transaction.atomic():
            reservation = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            assignment = ReservationItemAssignment.objects.filter(
                reservation=reservation, released_at__isnull=True).first()
            rows = Inspection.objects.filter(reservation=reservation).prefetch_related(
                "problems").order_by("inspected_at", "pk")
            return Response({
                "reservation_id": str(reservation.pk),
                "reservation_status": reservation.status,
                "initial_product_item_id": reservation.initial_product_item_id,
                "assigned_product_item_id": reservation.assigned_product_item_id,
                "expires_at": reservation.expires_at.isoformat(),
                "expected_assignment_id": assignment.pk if assignment else None,
                "inspections": InspectionSerializer(rows, many=True).data,
            })

    def perform(self, request, operation):
        reservation = self.get_object()
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            inspection, reservation, assignment = operation(
                actor_account_id=request.user.pk, reservation_id=reservation.pk, **data.validated_data)
        except DomainValidationError as error:
            raise serializers.ValidationError(
                error.message_dict if hasattr(error, "message_dict") else error.messages) from error
        except ReservationConflict as error:
            return Response({"detail": str(error)}, status=409)
        return Response({
            "inspection": InspectionSerializer(inspection).data,
            "reservation_id": str(reservation.pk), "reservation_status": reservation.status,
            "initial_product_item_id": reservation.initial_product_item_id,
            "assigned_product_item_id": reservation.assigned_product_item_id,
            "assignment_id": assignment.pk, "sequence_no": assignment.sequence_no,
        })

    def post(self, request, pk):
        return self.perform(request, record_pre_issue)


class InspectionReplacementView(PreIssueInspectionView):
    serializer_class = ReplacementInputSerializer
    http_method_names = ("post", "options")

    @property
    def required_permissions(self):
        return REPLACE_PERMISSIONS

    def post(self, request, pk):
        return self.perform(request, replace_after_inspection)


# Подтверждает выдачу через существующую проверку сотрудника и scope его ПВ.
class RentalStartView(EmployeePickupPointQuerysetMixin, GenericAPIView):
    queryset = Reservation.objects.all()
    pickup_point_lookup = "pickup_point_id"
    permission_classes = (CanPerformPhysicalOperation,)
    required_permissions = ISSUE_PERMISSIONS
    serializer_class = RentalStartInputSerializer
    http_method_names = ("post", "options")

    # Определяет ПВ из сохранённой брони, не из утверждений клиента.
    def get_physical_pickup_point_id(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"]).pickup_point_id

    # Передаёт проверенный запрос сервису и различает новую выдачу и повтор.
    def post(self, request, pk):
        reservation = self.get_object()
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            rental, created = start_rental(actor_account_id=request.user.pk,
                reservation_id=reservation.pk, **data.validated_data)
        except DomainValidationError as error:
            raise serializers.ValidationError(
                error.message_dict if hasattr(error, "message_dict") else error.messages) from error
        except ReservationConflict as error:
            return Response({"detail": str(error)}, status=409)
        return Response(RentalSerializer(rental).data, status=201 if created else 200)
