from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0072_backfill_credit_approved'),
    ]

    operations = [
        migrations.AddField(
            model_name='receipt',
            name='meta',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
