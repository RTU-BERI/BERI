from django.db.models import Prefetch
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import PickupPoint, PickupPointWorkingHours
from .serializers import PickupPointSerializer


class PickupPointViewSet(ReadOnlyModelViewSet):
    """Публичный API для просмотра пунктов выдачи и их расписания."""

    serializer_class = PickupPointSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    queryset = PickupPoint.objects.order_by("id").prefetch_related(
        Prefetch(
            "pickuppointworkinghours_set",
            queryset=PickupPointWorkingHours.objects.order_by("weekday"),
        )
    )
