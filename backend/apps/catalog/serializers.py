"""Сериализаторы для каталога: категории и товары."""

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductComponent,
    ProductTariff,
)


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("id", "name")
        read_only_fields = fields


class ProductListSerializer(serializers.ModelSerializer):
    """Краткая карточка для списка товаров."""

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "category",
            "status",
            "image_url",
            "brand",
            "model",
        )
        read_only_fields = fields


class ProductAttributeValueSerializer(serializers.ModelSerializer):
    """Значение характеристики."""

    name = serializers.CharField(
        source="category_attribute.attribute_definition.name",
        read_only=True,
    )
    unit = serializers.CharField(
        source="category_attribute.unit",
        read_only=True,
    )

    class Meta:
        model = ProductAttributeValue
        fields = ("name", "unit", "value_text", "value_number", "value_boolean")
        read_only_fields = fields


class ProductComponentSerializer(serializers.ModelSerializer):
    """Элемент комплектации."""

    class Meta:
        model = ProductComponent
        fields = ("name", "quantity")
        read_only_fields = fields


class ProductTariffSerializer(serializers.ModelSerializer):
    """Действующий тариф."""

    class Meta:
        model = ProductTariff
        fields = ("hourly_rate", "valid_from", "valid_to")
        read_only_fields = fields


class ProductDetailSerializer(serializers.ModelSerializer):
    """Полная карточка товара."""

    category = ProductCategorySerializer(read_only=True)
    attributes = ProductAttributeValueSerializer(
        source="attribute_values", many=True, read_only=True
    )
    components = ProductComponentSerializer(many=True, read_only=True)
    tariff = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "description",
            "image_url",
            "brand",
            "model",
            "status",
            "category",
            "attributes",
            "components",
            "tariff",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_tariff(self, obj):
        """Возвращает актуальный тариф или None, если его нет."""
        now = timezone.now()
        tariff = obj.tariffs.filter(valid_from__lte=now).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gt=now)
        ).first()

        if tariff is None:
            return None
        return ProductTariffSerializer(tariff).data