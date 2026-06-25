from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0035_can_access_kitchen_default_false'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='keg_alerts_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Send in-app + SMS alerts when keg variance crosses the danger threshold.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='keg_alert_min_litres',
            field=models.DecimalField(
                max_digits=5, decimal_places=1, default=Decimal('5.0'),
                help_text='Minimum litres dispensed before a SPOT variance alert fires (prevents false alarms on tiny volumes).',
            ),
        ),
    ]
