from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_add_last_txn_sms_at_to_business'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='keg_variance_tolerance_pct',
            field=models.DecimalField(
                max_digits=4, decimal_places=1, default=Decimal('3.0'),
                help_text='Allowed % gap between weight-implied revenue and recorded keg sales before a shift is flagged.',
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='keg_default_gross_kg',
            field=models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('60.00')),
        ),
        migrations.AddField(
            model_name='business',
            name='keg_default_tare_kg',
            field=models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00')),
        ),
        migrations.AddField(
            model_name='business',
            name='keg_revenue_multiplier',
            field=models.DecimalField(
                max_digits=4, decimal_places=2, default=Decimal('1.50'),
                help_text='Suggested barrel target = cost x this. 5000 x 1.5 = 7500, matching common owner targets.',
            ),
        ),
    ]
