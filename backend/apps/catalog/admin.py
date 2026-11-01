from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import router, transaction

from .admin_forms import ProductAttributeValueForm
from .admin_support import CatalogWriteMixin, NoDeleteMixin
from .models import (
    AttributeDefinition,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductComponent,
    ProductTariff,
)


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    """Manage categories with Django's existing model permissions."""

    list_display = ("id", "name")
    search_fields = ("name",)
    ordering = ("name", "id")


@admin.register(Product)
class ProductAdmin(NoDeleteMixin, CatalogWriteMixin, admin.ModelAdmin):
    list_display = ("id", "name", "category", "status", "updated_at")
    search_fields = ("name", "brand", "model")
    list_filter = ("category", "status")
    list_select_related = ("category",)
    ordering = ("name", "id")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj and obj.attribute_values.exists():
            fields += ("category",)
        return fields


@admin.register(AttributeDefinition)
class AttributeDefinitionAdmin(NoDeleteMixin, CatalogWriteMixin, admin.ModelAdmin):
    list_display = ("id", "code", "name", "data_type")
    search_fields = ("code", "name")
    ordering = ("code", "id")

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or not obj.category_attributes.exists()
        )

    def save_model(self, request, obj, form, change):
        if change and obj.category_attributes.exists():
            raise ValidationError(
                "Используемое определение характеристики доступно только для чтения."
            )
        super().save_model(request, obj, form, change)


@admin.register(CategoryAttribute)
class CategoryAttributeAdmin(NoDeleteMixin, CatalogWriteMixin, admin.ModelAdmin):
    list_display = ("id", "category", "attribute_definition", "unit", "is_filterable")
    list_select_related = ("category", "attribute_definition")
    search_fields = (
        "category__name",
        "attribute_definition__name",
        "attribute_definition__code",
    )
    list_filter = ("category", "is_filterable")
    ordering = ("id",)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or not obj.product_values.exists()
        )

    def save_model(self, request, obj, form, change):
        using = router.db_for_write(type(obj))
        with transaction.atomic(using=using):
            # Synchronize definition editing with its first assignment.
            AttributeDefinition.objects.using(using).select_for_update().get(
                pk=obj.attribute_definition_id
            )
            if change and obj.product_values.exists():
                raise ValidationError(
                    "Используемая характеристика категории доступна только для чтения."
                )
            super().save_model(request, obj, form, change)


@admin.register(ProductAttributeValue)
class ProductAttributeValueAdmin(CatalogWriteMixin, admin.ModelAdmin):
    form = ProductAttributeValueForm
    list_display = (
        "id",
        "product",
        "category_attribute",
        "value_text",
        "value_number",
        "value_boolean",
    )
    list_select_related = ("product", "category_attribute__attribute_definition")
    search_fields = ("product__name", "category_attribute__attribute_definition__name")
    ordering = ("id",)

    @admin.display(description="Тип значения")
    def value_kind(self, obj):
        for kind, label in (
            ("text", "Текст"),
            ("number", "Число"),
            ("boolean", "Да / Нет"),
        ):
            if getattr(obj, "value_" + kind) is not None:
                return label
        return "—"


@admin.register(ProductComponent)
class ProductComponentAdmin(CatalogWriteMixin, admin.ModelAdmin):
    # Component.save/delete acquire Product before Component themselves.
    lock_object_on_post = False
    list_display = ("id", "product", "name", "quantity")
    list_select_related = ("product",)
    search_fields = ("product__name", "name")
    ordering = ("product_id", "id")

    @staticmethod
    def _inspected(obj):
        return obj.product.items.filter(inspections__isnull=False).exists()

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or not self._inspected(obj)
        )

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or not self._inspected(obj)
        )


@admin.register(ProductTariff)
class ProductTariffAdmin(NoDeleteMixin, CatalogWriteMixin, admin.ModelAdmin):
    list_display = ("id", "product", "hourly_rate", "valid_from", "valid_to")
    list_select_related = ("product",)
    search_fields = ("product__name",)
    ordering = ("product_id", "-valid_from", "-id")
    readonly_fields = ("created_at",)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or obj.valid_to is None
        )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj:
            fields += ("product", "hourly_rate", "valid_from")
        return fields
