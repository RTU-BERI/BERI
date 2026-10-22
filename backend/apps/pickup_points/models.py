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

    def __str__(self):
        return self.name
