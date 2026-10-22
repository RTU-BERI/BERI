import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("pickup_points", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("account", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="employee", to=settings.AUTH_USER_MODEL,
                )),
                ("pickup_point", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="employees", to="pickup_points.pickuppoint",
                )),
            ],
            options={"db_table": "employee"},
        ),
    ]
