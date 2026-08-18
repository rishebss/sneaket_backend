from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0004_alter_order_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="delivery_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
