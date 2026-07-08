from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0095_payment_receipt_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='bartab',
            name='void_reason',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
