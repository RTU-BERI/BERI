"""Публичное API для каталога."""

from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Product, ProductCategory
from .serializers import (
    ProductCategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)


class ProductCategoryViewSet(ReadOnlyModelViewSet):
    """Публичный справочник категорий."""

    queryset = ProductCategory.objects.order_by("name", "id")
    serializer_class = ProductCategorySerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    http_method_names = ("get", "head", "options")
    pagination_class = None


class ProductListAPIView(generics.ListAPIView):
    """Список товаров с поиском и фильтром по категории."""

    serializer_class = ProductListSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    pagination_class = None

    def get_queryset(self):
        qs = Product.objects.select_related("category").all()

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search.strip())

        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)

        return qs.order_by("name", "id")


class ProductDetailAPIView(generics.RetrieveAPIView):
    """Карточка конкретного товара."""

    serializer_class = ProductDetailSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get_queryset(self):
        return Product.objects.prefetch_related(
            "attribute_values__category_attribute__attribute_definition",
            "components",
            "tariffs",
        ).select_related("category")