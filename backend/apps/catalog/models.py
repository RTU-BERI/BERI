from django.apps import apps
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateTimeRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.db.utils import NotSupportedError

from .managers import ComponentManager, TariffManager, ValidatedCatalogManager
from .validators import (
    MAX_HOURLY_RATE,
    validate_positive_hourly_rate,
    validate_product_status,
)


class ProductCategory(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "product_category"


class AttributeDefinition(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    # The data-type vocabulary is deferred; there is no product default.
    data_type = models.CharField(max_length=20)

    class Meta:
        db_table = "attribute_definition"


def _validation_database(instance):
    return instance._state.db or router.db_for_write(
        type(instance), instance=instance
    )


def _save_options(instance, args, kwargs):
    options = dict(kwargs)
    if args:
        values = instance._parse_save_params(
            *args,
            method_name="save",
            force_insert=options.get("force_insert", False),
            force_update=options.get("force_update", False),
            using=options.get("using"),
            update_fields=options.get("update_fields"),
        )
        options.update(
            zip(
                ("force_insert", "force_update", "using", "update_fields"),
                values,
            )
        )
    using = options.get("using") or _validation_database(instance)
    options["using"] = using
    if options.get("update_fields") is not None:
        options["update_fields"] = frozenset(options["update_fields"])
    return using, options


def _stored_instance(instance, using, *, lock=False):
    if instance.pk is None:
        return None
    queryset = type(instance)._base_manager.using(using)
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(pk=instance.pk).first()


def _full_clean_using(instance, using):
    previous_database = instance._state.db
    instance._state.db = using
    try:
        instance.full_clean()
    finally:
        instance._state.db = previous_database


def _reject_omitted_changes(instance, stored, update_fields, protected_fields):
    if stored is None or update_fields is None:
        return
    errors = {}
    for name in protected_fields:
        field = instance._meta.get_field(name)
        if name in update_fields or field.attname in update_fields:
            continue
        if getattr(instance, field.attname) != getattr(stored, field.attname):
            errors[name] = "Include the changed field in update_fields."
    if errors:
        raise ValidationError(errors)


class Product(models.Model):
    id = models.BigAutoField(primary_key=True)
    category = models.ForeignKey(
        ProductCategory, on_delete=models.PROTECT, related_name="products"
    )
    name = models.CharField(max_length=200)
    description = models.TextField()
    image_url = models.TextField(null=True, blank=True)
    brand = models.CharField(max_length=100, null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(
        max_length=20, validators=[validate_product_status]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ValidatedCatalogManager()

    class Meta:
        db_table = "product"
        base_manager_name = "objects"

    def clean(self):
        super().clean()
        using = _validation_database(self)
        stored = _stored_instance(self, using)
        if (
            stored is not None
            and self.category_id != stored.category_id
            and ProductAttributeValue._base_manager.using(using).filter(
                product_id=self.pk
            ).exists()
        ):
            raise ValidationError(
                {"category": "A product with values cannot change category."}
            )

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        with transaction.atomic(using=using):
            stored = _stored_instance(self, using, lock=True)
            _full_clean_using(self, using)
            _reject_omitted_changes(
                self, stored, options.get("update_fields"), ("category",)
            )
            return super().save(**options)


class CategoryAttribute(models.Model):
    id = models.BigAutoField(primary_key=True)
    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        related_name="category_attributes",
    )
    attribute_definition = models.ForeignKey(
        AttributeDefinition,
        on_delete=models.PROTECT,
        related_name="category_attributes",
    )
    unit = models.CharField(max_length=50, null=True, blank=True)
    is_filterable = models.BooleanField()

    objects = ValidatedCatalogManager()

    class Meta:
        db_table = "category_attribute"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "attribute_definition"],
                name="category_attribute_pair_unique",
            ),
        ]

    def clean(self):
        super().clean()
        using = _validation_database(self)
        stored = _stored_instance(self, using)
        if (
            stored is not None
            and self.category_id != stored.category_id
            and ProductAttributeValue._base_manager.using(using).filter(
                category_attribute_id=self.pk
            ).exists()
        ):
            raise ValidationError(
                {"category": "An assigned attribute cannot change category."}
            )

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        with transaction.atomic(using=using):
            stored = _stored_instance(self, using, lock=True)
            _full_clean_using(self, using)
            _reject_omitted_changes(
                self, stored, options.get("update_fields"), ("category",)
            )
            return super().save(**options)


class ProductAttributeValue(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="attribute_values"
    )
    category_attribute = models.ForeignKey(
        CategoryAttribute,
        on_delete=models.PROTECT,
        related_name="product_values",
    )
    value_text = models.TextField(null=True, blank=True)
    value_number = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    value_boolean = models.BooleanField(null=True, blank=True)

    objects = ValidatedCatalogManager()

    class Meta:
        db_table = "product_attribute_value"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "category_attribute"],
                name="product_attribute_value_pair_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        value_text__isnull=False,
                        value_number__isnull=True,
                        value_boolean__isnull=True,
                    )
                    | models.Q(
                        value_text__isnull=True,
                        value_number__isnull=False,
                        value_boolean__isnull=True,
                    )
                    | models.Q(
                        value_text__isnull=True,
                        value_number__isnull=True,
                        value_boolean__isnull=False,
                    )
                ),
                name="product_attribute_value_one_value",
            ),
        ]

    def _parents(self, using, *, lock=False):
        parents = []
        # All supported writes acquire Product before CategoryAttribute.
        for name in ("product", "category_attribute"):
            field = self._meta.get_field(name)
            model = field.remote_field.model
            queryset = model._base_manager.using(using)
            if lock:
                queryset = queryset.select_for_update()
            try:
                parent = queryset.get(pk=getattr(self, field.attname))
            except (
                model.DoesNotExist,
                ValidationError,
                ValueError,
                TypeError,
            ):
                raise ValidationError(
                    {name: "Choose an existing related object."}
                ) from None
            parents.append(parent)
        return parents

    def clean(self):
        super().clean()
        if sum(
            value is not None
            for value in (
                self.value_text, self.value_number, self.value_boolean
            )
        ) != 1:
            raise ValidationError(
                {"value_text": "Provide exactly one non-NULL attribute value."}
            )
        product, category_attribute = self._parents(_validation_database(self))
        if product.category_id != category_attribute.category_id:
            raise ValidationError(
                {
                    "category_attribute":
                        "The attribute must match the product category."
                }
            )

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        with transaction.atomic(using=using):
            stored = _stored_instance(self, using, lock=True)
            self._parents(using, lock=True)
            _full_clean_using(self, using)
            _reject_omitted_changes(
                self,
                stored,
                options.get("update_fields"),
                (
                    "product",
                    "category_attribute",
                    "value_text",
                    "value_number",
                    "value_boolean",
                ),
            )
            return super().save(**options)


class ProductComponent(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="components"
    )
    name = models.CharField(max_length=150)
    quantity = models.IntegerField(validators=[MinValueValidator(1)])

    objects = ComponentManager()

    class Meta:
        db_table = "product_component"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="product_component_quantity_positive",
            ),
        ]

    @staticmethod
    def _check_uninspected(product_ids, using):
        inspection = apps.get_model("rentals", "Inspection")
        if inspection._base_manager.using(using).filter(
            product_item__product_id__in=product_ids
        ).exists():
            raise ValidationError(
                "A product's component set is frozen after its first inspection."
            )

    def _locked_source(self, using, target_product=None):
        """Lock parents before this component; never follow a stale reparent."""
        previous = _stored_instance(self, using)
        product_ids = set()
        if previous is not None:
            product_ids.add(previous.product_id)
        if target_product is not None:
            try:
                product_ids.add(Product._meta.pk.to_python(target_product))
            except (ValidationError, TypeError, ValueError, OverflowError):
                raise ValidationError({"product": "Choose an existing product."}) from None
        if not product_ids or None in product_ids:
            raise ValidationError({"product": "Choose an existing product."})
        parents = list(
            Product._base_manager.using(using).select_for_update()
            .filter(pk__in=product_ids).order_by("pk")
        )
        if {parent.pk for parent in parents} != product_ids:
            raise ValidationError({"product": "Choose an existing product."})
        stored = _stored_instance(self, using, lock=True)
        if (previous is None) != (stored is None) or (
            previous is not None and previous.product_id != stored.product_id
        ):
            raise ValidationError("The component changed; reload it before writing.")
        if stored is None and not self._state.adding:
            raise ValidationError("The saved component no longer exists.")
        self._check_uninspected(product_ids, using)
        return stored

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        if options.get("update_fields") is not None and not options["update_fields"]:
            return None
        with transaction.atomic(using=using):
            stored = self._locked_source(using, self.product_id)
            _full_clean_using(self, using)
            _reject_omitted_changes(self, stored, options.get("update_fields"), ("product",))
            return super().save(**options)

    def delete(self, using=None, keep_parents=False):
        using = using or _validation_database(self)
        with transaction.atomic(using=using):
            self._locked_source(using)
            return super().delete(using=using, keep_parents=keep_parents)


class ProductTariff(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="tariffs"
    )
    hourly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_hourly_rate],
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TariffManager()

    class Meta:
        db_table = "product_tariff"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    hourly_rate__gt=0, hourly_rate__lte=MAX_HOURLY_RATE
                ),
                name="product_tariff_rate_positive_finite",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_to__isnull=True)
                    | models.Q(valid_to__gt=models.F("valid_from"))
                ),
                name="product_tariff_valid_period",
            ),
            ExclusionConstraint(
                name="product_tariff_no_overlap",
                expressions=[
                    ("product", RangeOperators.EQUAL),
                    (
                        models.Func(
                            models.F("valid_from"),
                            models.F("valid_to"),
                            RangeBoundary(),
                            function="TSTZRANGE",
                            output_field=DateTimeRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for name in ("hourly_rate", "valid_from", "valid_to"):
            try:
                field = self._meta.get_field(name)
                value = field.clean(getattr(self, name), self)
                setattr(self, name, value)
            except ValidationError as error:
                errors[name] = error
        if (
            "valid_from" not in errors
            and "valid_to" not in errors
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            errors["valid_to"] = "The end must be later than the start."
        if errors:
            # Field errors exclude invalid bounds from ExclusionConstraint SQL.
            raise ValidationError(errors)
        stored = _stored_instance(self, _validation_database(self))
        if stored is None:
            return
        for name in ("product", "hourly_rate", "valid_from", "created_at"):
            field = self._meta.get_field(name)
            if getattr(self, field.attname) != getattr(stored, field.attname):
                errors[name] = "A saved tariff version is immutable."
        if stored.valid_to is not None and self.valid_to != stored.valid_to:
            errors["valid_to"] = "A closed tariff period cannot be changed."
        if self.valid_to is not None and self.valid_to != stored.valid_to:
            rental = apps.get_model("rentals", "Rental")
            # Supported issuance locks this tariff before inserting Rental.
            # Do not lock Rental here: payment/issuance locks flow toward it.
            if rental._base_manager.using(_validation_database(self)).filter(
                tariff_id=stored.pk, started_at__gte=self.valid_to
            ).exists():
                errors["valid_to"] = "The tariff must still cover every recorded rental start."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        with transaction.atomic(using=using):
            stored = _stored_instance(self, using, lock=True)
            _full_clean_using(self, using)
            _reject_omitted_changes(
                self, stored, options.get("update_fields"), ("valid_to",)
            )
            return super().save(**options)

    def delete(self, using=None, keep_parents=False):
        raise NotSupportedError("Tariff versions cannot be deleted.")
