"""Public category lookup without catalogue or reservation writes."""

from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import ProductCategory
from .serializers import ProductCategorySerializer


class ProductCategoryViewSet(ReadOnlyModelViewSet):
    """Expose only the public category dictionary, independent of sessions."""

    queryset = ProductCategory.objects.order_by("name", "id")
    serializer_class = ProductCategorySerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    http_method_names = ("get", "head", "options")
    pagination_class = None
