
"""Маршруты API наличия товаров."""

from django.urls import path

from .views import ProductAvailabilityAPIView


urlpatterns = [
    path(
        "products/<int:pk>/availability/",
        ProductAvailabilityAPIView.as_view(),
        name="product-availability",
    ),
]
