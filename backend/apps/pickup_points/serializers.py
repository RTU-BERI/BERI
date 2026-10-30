from rest_framework import serializers

from .models import PickupPoint, PickupPointWorkingHours


class PickupPointWorkingHoursSerializer(serializers.ModelSerializer):
    """Сериализатор расписания работы пункта выдачи."""

    class Meta:
        model = PickupPointWorkingHours
        fields = ("weekday", "opens_at", "closes_at")
        read_only_fields = fields


class PickupPointSerializer(serializers.ModelSerializer):
    """Сериализатор пункта выдачи с расписанием работы."""

    working_hours = PickupPointWorkingHoursSerializer(
        source="pickuppointworkinghours_set",
        many=True,
        read_only=True,
    )

    class Meta:
        model = PickupPoint
        fields = (
            "id",
            "name",
            "address",
            "latitude",
            "longitude",
            "status",
            "working_hours",
        )
        read_only_fields = fields
