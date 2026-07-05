from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0090_add_stockrequest_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='tab_entry_ids',
            field=models.JSONField(
                blank=True, null=True,
                help_text='List of BarTabEntry IDs for partial tab STK settlement. Null = FIFO full-tab.',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='qs_cart',
            field=models.JSONField(
                blank=True, null=True,
                help_text='Serialised Quick Sell cart for checkout STK push server-side settlement.',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='qs_settled',
            field=models.BooleanField(
                default=False,
                help_text='True once qs_cart has been processed (by callback or JS poll).',
            ),
        ),
    ]
