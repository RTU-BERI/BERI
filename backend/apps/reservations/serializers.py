from rest_framework import serializers

from .models import Reservation


class ReservationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="initial_product_item.product_id",
        read_only=True,
    )
    pickup_point_id = serializers.IntegerField(read_only=True)
    assigned_product_item_id = serializers.IntegerField(read_only=True)
    customer_phone = serializers.CharField(
        source="customer_account.phone",
        read_only=True,
    )

    class Meta:
        model = Reservation
        fields = (
            "id",
            "product_id",
            "pickup_point_id",
            "assigned_product_item_id",
            "status",
            "created_at",
            "expires_at",
            "cancelled_at",
            "completed_at",
            "customer_phone",
        )
        read_only_fields = fields


class CreateReservationSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    pickup_point_id = serializers.IntegerField(min_value=1)
    request_id = serializers.UUIDField()