from rest_framework import serializers  # Инструменты для работы с API

# Импортируем модель категории из models.py
from .models import ProductCategory


# Сериализатор для передачи данных о категориях
class ProductCategorySerializer(serializers.ModelSerializer):

    # Указываем модель и поля для передачи
    class Meta:
        model = ProductCategory
        fields = ["id", "name"]
