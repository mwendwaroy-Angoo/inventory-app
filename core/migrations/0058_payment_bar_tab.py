from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0057_shift_stock_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='bar_tab',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='stk_payments',
                to='core.bartab',
            ),
        ),
    ]
