
# Подключаем административную панель Django
from django.contrib import admin

# Импортируем модель категорий товаров
from .models import ProductCategory


# Регистрируем категории в административной панели
@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):

    # Отображаем ID и название категории
    list_display = ("id", "name")

    # Позволяем искать категории по названию
    search_fields = ("name",)

    # Сортируем категории по алфавиту
    ordering = ("name",)
