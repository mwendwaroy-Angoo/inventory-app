from decimal import Decimal
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_transaction_created_at'),
    ]

    operations = [
        # Option A: shift-level offline sales capture
        migrations.AddField(
            model_name='shift',
            name='offline_sales_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0'), max_digits=10,
                help_text='Cash collected offline (no app/no internet) during this shift, not yet in transactions.',
            ),
        ),
        migrations.AddField(
            model_name='shift',
            name='offline_sales_note',
            field=models.CharField(blank=True, max_length=200),
        ),
        # Option B: allow backdating Transaction.created_at
        migrations.AlterField(
            model_name='transaction',
            name='created_at',
            field=models.DateTimeField(
                blank=True, null=True,
                default=django.utils.timezone.now,
                help_text='Exact timestamp — used for shift-level reconciliation. Can be backdated for offline sales.',
            ),
        ),
    ]
