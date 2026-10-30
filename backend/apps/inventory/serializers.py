from rest_framework import serializers


class PickupPointAvailabilitySerializer(serializers.Serializer):
    """Количество свободных экземпляров в пункте выдачи."""

    pickup_point_id = serializers.IntegerField(source="id")
    available_count = serializers.IntegerField()


class ProductAvailabilitySerializer(serializers.Serializer):
    """Наличие товара по пунктам выдачи."""

    product_id = serializers.IntegerField()
    pickup_points = PickupPointAvailabilitySerializer(many=True)
