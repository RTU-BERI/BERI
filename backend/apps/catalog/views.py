
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import ProductCategory
from .serializers import ProductCategorySerializer


class ProductCategoryViewSet(ReadOnlyModelViewSet):
    """Позволяет получать список категорий и отдельную категорию через API."""

    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
