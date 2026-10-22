from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class PickupPoint(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=150)
    address = models.TextField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    status = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pickup_point"


class PickupPointWorkingHours(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 1, "Monday"
        TUESDAY = 2, "Tuesday"
        WEDNESDAY = 3, "Wednesday"
        THURSDAY = 4, "Thursday"
        FRIDAY = 5, "Friday"
        SATURDAY = 6, "Saturday"
        SUNDAY = 7, "Sunday"

    id = models.BigAutoField(primary_key=True)
    pickup_point = models.ForeignKey(PickupPoint, on_delete=models.PROTECT)
    weekday = models.SmallIntegerField(
        choices=Weekday.choices,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    opens_at = models.TimeField()
    closes_at = models.TimeField()

    class Meta:
        db_table = "pickup_point_working_hours"
        constraints = [
            models.UniqueConstraint(
                fields=["pickup_point", "weekday"],
                name="pickup_point_weekday_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(weekday__gte=1, weekday__lte=7),
                name="pickup_point_weekday_valid",
            ),
        ]
