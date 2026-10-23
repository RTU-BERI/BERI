from django.contrib import admin

from .models import ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    """Настраивает управление категориями товаров в Django Admin."""

    list_display = ("id", "name")
    search_fields = ("name",)
    ordering = ("name",)
