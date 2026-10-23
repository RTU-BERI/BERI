from django.db import models


class ProductCategory(models.Model):
    """Хранит категорию товаров с уникальным названием."""

    id = models.BigAutoField(primary_key=True)

    name = models.CharField(
        max_length=100,
        unique=True
    )

    class Meta:
        db_table = "product_category"
        ordering = ["name"]
        verbose_name = "Категория товара"
        verbose_name_plural = "Категории товаров"

    def __str__(self):
        """Возвращает название категории для отображения."""
        return self.name
