
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.pickup_points.models import PickupPoint

from .models import ProductItem
from .serializers import ProductAvailabilitySerializer


class ProductAvailabilityAPIView(APIView):
    """Публичный API наличия товара по пунктам выдачи."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request, pk):
        get_object_or_404(Product, pk=pk)

        points = PickupPoint.objects.annotate(
            available_count=Count(
                "product_items",
                filter=Q(
                    product_items__product_id=pk,
                    product_items__status=ProductItem.Status.AVAILABLE,
                ),
            )
        ).order_by("id")

        serializer = ProductAvailabilitySerializer({
            "product_id": pk,
            "pickup_points": points,
        })

        return Response(serializer.data)
