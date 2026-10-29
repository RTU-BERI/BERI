"""Маршруты API каталога."""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ProductCategoryViewSet,
    ProductDetailAPIView,
    ProductListAPIView,
)

router = DefaultRouter()
router.register("categories", ProductCategoryViewSet, basename="category")

urlpatterns = [
    path("products/", ProductListAPIView.as_view(), name="product-list"),
    path("products/<int:pk>/", ProductDetailAPIView.as_view(), name="product-detail"),
    *router.urls,
]