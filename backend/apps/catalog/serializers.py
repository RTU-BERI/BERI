
from rest_framework import serializers

from .models import ProductCategory


class ProductCategorySerializer(serializers.ModelSerializer):
    """Преобразует категорию товара в данные для API."""

    class Meta:
        model = ProductCategory
        fields = ["id", "name"]
