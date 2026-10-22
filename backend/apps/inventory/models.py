from django.apps import apps
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateTimeRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from .managers import LocationHistoryManager, ProductItemManager
from .validators import validate_inventory_code


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


def _location_state(item, histories, now):
    """Check existing state; never infer or repair missing location data."""
    for name in ("created_at", "updated_at", "status_changed_at"):
        value = getattr(item, name)
        if value is None or timezone.is_naive(value) or value > now:
            raise ValidationError(
                {"pickup_point": "The item has invalid source timestamps."}
            )
    opened = []
    previous = None
    for history in sorted(histories, key=lambda row: (row.valid_from, row.pk)):
        if (
            timezone.is_naive(history.valid_from)
            or history.valid_from < item.created_at
            or history.valid_from > now
            or (
                history.valid_to is not None
                and (
                    timezone.is_naive(history.valid_to)
                    or history.valid_to <= history.valid_from
                    or history.valid_to > now
                )
            )
        ):
            raise ValidationError(
                {"pickup_point": "Location history has invalid source times."}
            )
        if previous is not None and (
            previous.valid_to is None
            or previous.valid_to > history.valid_from
        ):
            raise ValidationError(
                {"pickup_point": "The source location periods overlap."}
            )
        previous = history
        if history.valid_to is None:
            opened.append(history)
    if item.pickup_point_id is None:
        if opened:
            raise ValidationError(
                {"pickup_point": "Unplaced item has an open history period."}
            )
        return None
    if len(opened) != 1 or opened[0].pickup_point_id != item.pickup_point_id:
        raise ValidationError(
            {"pickup_point": "The current point and open history disagree."}
        )
    return opened[0]


class ProductItem(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        RENTED = "rented", "Rented"
        UNAVAILABLE = "unavailable", "Unavailable"
        RETIRED = "retired", "Retired"

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="items"
    )
    pickup_point = models.ForeignKey(
        "pickup_points.PickupPoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="product_items",
    )
    inventory_code = models.CharField(
        max_length=100, unique=True, validators=[validate_inventory_code]
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    status_reason = models.TextField(null=True, blank=True)
    status_changed_at = models.DateTimeField(editable=False, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductItemManager()

    class Meta:
        db_table = "product_item"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "available", "reserved", "rented",
                        "unavailable", "retired",
                    ]
                ),
                name="product_item_status_valid",
            ),
        ]

    def clean(self):
        super().clean()
        using = _validation_database(self)
        stored = _stored_instance(self, using)
        errors = {}
        if stored is None:
            if not self._state.adding:
                raise ValidationError("The saved item no longer exists.")
            if self.pickup_point_id is not None:
                errors["pickup_point"] = "Use create_item for placement."
            if self.status not in {
                self.Status.AVAILABLE,
                self.Status.UNAVAILABLE,
                self.Status.RETIRED,
            }:
                errors["status"] = "Choose an explicit initial status."
            if self.status_changed_at is not None and not getattr(
                self, "_setting_creation_timestamp", False
            ):
                errors["status_changed_at"] = "Creation time is server-only."
        else:
            for name in (
                "inventory_code", "status", "status_changed_at", "created_at"
            ):
                if getattr(self, name) != getattr(stored, name):
                    errors[name] = "This saved inventory field is immutable."
            if self.pickup_point_id != stored.pickup_point_id:
                errors["pickup_point"] = "Use set_location for placement."
            if self.product_id != stored.product_id:
                historical_models = (
                    ("reservations", "ReservationItemAssignment"),
                    ("rentals", "Rental"),
                    ("rentals", "Inspection"),
                )
                if any(
                    apps.get_model(app_label, model_name)._base_manager.using(using)
                    .filter(product_item_id=stored.pk).exists()
                    for app_label, model_name in historical_models
                ):
                    errors["product"] = (
                        "An item with operational history cannot change product."
                    )
            histories = list(
                ProductItemLocationHistory._base_manager.using(using).filter(
                    product_item_id=stored.pk
                )
            )
            _location_state(stored, histories, timezone.now())
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        using, options = _save_options(self, args, kwargs)
        if (
            options.get("update_fields") is not None
            and not options["update_fields"]
        ):
            return None
        with transaction.atomic(using=using):
            stored = _stored_instance(self, using, lock=True)
            creating = stored is None
            if creating:
                if self.status_changed_at is not None:
                    raise ValidationError(
                        {"status_changed_at": "Creation time is server-only."}
                    )
                self.status_changed_at = timezone.now()
                self._setting_creation_timestamp = True
            try:
                _full_clean_using(self, using)
                return super().save(**options)
            except Exception:
                if creating:
                    self.status_changed_at = None
                raise
            finally:
                if creating:
                    del self._setting_creation_timestamp


class ProductItemLocationHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    product_item = models.ForeignKey(
        ProductItem,
        on_delete=models.PROTECT,
        related_name="location_history",
    )
    pickup_point = models.ForeignKey(
        "pickup_points.PickupPoint",
        on_delete=models.PROTECT,
        related_name="item_location_history",
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    objects = LocationHistoryManager()

    class Meta:
        db_table = "product_item_location_history"
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(valid_to__isnull=True)
                    | models.Q(valid_to__gt=models.F("valid_from"))
                ),
                name="product_item_location_valid_period",
            ),
            ExclusionConstraint(
                name="product_item_location_no_overlap",
                expressions=[
                    ("product_item", RangeOperators.EQUAL),
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
        for name in ("valid_from", "valid_to"):
            try:
                field = self._meta.get_field(name)
                setattr(self, name, field.clean(getattr(self, name), self))
            except ValidationError as error:
                errors[name] = error
        if (
            not errors
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            errors["valid_to"] = "The end must be later than the start."
        if errors:
            raise ValidationError(errors)
        stored = _stored_instance(self, _validation_database(self))
        if stored is None:
            return
        for name in ("product_item", "pickup_point", "valid_from"):
            field = self._meta.get_field(name)
            if getattr(self, field.attname) != getattr(stored, field.attname):
                errors[name] = "Saved location history is immutable."
        if stored.valid_to is not None and self.valid_to != stored.valid_to:
            errors["valid_to"] = "A closed location period is immutable."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        raise NotSupportedError("Write location history through its service.")

    def delete(self, using=None, keep_parents=False):
        raise NotSupportedError("Location history cannot be deleted.")
