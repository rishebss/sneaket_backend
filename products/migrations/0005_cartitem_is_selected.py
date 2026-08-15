from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0004_cartitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="cartitem",
            name="is_selected",
            field=models.BooleanField(
                default=True,
                help_text="Whether this line is selected for checkout",
            ),
        ),
    ]
