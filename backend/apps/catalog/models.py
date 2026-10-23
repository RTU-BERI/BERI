import uuid

from django.db import models


class ProductCategory(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "product_category"
        ordering = ["name"]
        verbose_name = "Категория товара"
        verbose_name_plural = "Категории товаров"

    def __str__(self):
        return self.name


class Product(models.Model):
    """Карточка товара (таблица product)."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    category = models.ForeignKey(
        "catalog.ProductCategory",
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Категория",
    )

    name = models.CharField("Название", max_length=200)

    hourly_rate = models.DecimalField(
        "Почасовой тариф",
        max_digits=12,
        decimal_places=2,
    )

    valid_from = models.DateTimeField("Действует с")

    valid_to = models.DateTimeField(
        "Действует до",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        db_table = "product"
        ordering = ["name"]
        verbose_name = "Товар"
        verbose_name_plural = "Товары"

    def __str__(self):
        return self.name