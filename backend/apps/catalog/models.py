from django.db import models  # Инструменты Django для работы с БД


# Модель категории товаров
class ProductCategory(models.Model):

    # Уникальный числовой идентификатор категории (BIGINT)
    id = models.BigAutoField(primary_key=True)

    # Название категории: максимум 100 символов, без повторений
    name = models.CharField(
        max_length=100,
        unique=True
    )

    # Дополнительные настройки модели
    class Meta:
        db_table = "product_category"  # Название таблицы в БД
        ordering = ["name"]  # Сортировка по названию
        verbose_name = "Категория товара"
        verbose_name_plural = "Категории товаров"

    # Показывает название категории вместо номера объекта
    def __str__(self):
        return self.name
