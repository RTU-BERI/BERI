from django.contrib import admin

from .models import ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    """Manage categories with Django's existing model permissions."""

    list_display = ("id", "name")
    search_fields = ("name",)
    ordering = ("name", "id")
