import django.db.models.deletion
from django.db import migrations, models


def backfill_store_type_from_is_kitchen(apps, schema_editor):
    """CRITICAL: without this, Store.save()'s new is_kitchen<->store_type
    sync would flip every existing kitchen store's is_kitchen to False the
    next time anything saves it (store_type defaults to 'retail' via
    AddField). Set store_type='kitchen' for every row that is_kitchen
    already flagged, so the two stay consistent from the moment this
    migration runs, before any code can observe the inconsistency.
    """
    Store = apps.get_model('core', 'Store')
    Store.objects.filter(is_kitchen=True).update(store_type='kitchen')


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0055_alter_accountdeletionlog_reason'),
        ('core', '0140_item_stock_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='store_type',
            field=models.CharField(
                choices=[
                    ('bar', 'Bar'), ('kitchen', 'Jiko / Kitchen'),
                    ('retail', 'Duka / Retail floor'), ('produce', 'Kibanda'),
                    ('salon', 'Salon'), ('rental', 'Rentals'),
                    ('warehouse', 'Godown / Store'), ('other', 'Nyingine'),
                ],
                default='retail', max_length=20,
                help_text='UBA capability model: what kind of outlet this store is.',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='code',
            field=models.CharField(
                blank=True, max_length=12,
                help_text='Short code for this outlet — used on receipts, transfers, Paybill account (e.g. "KHY01").',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='is_outlet',
            field=models.BooleanField(
                default=True,
                help_text='True = sells to customers. False = a godown/warehouse: holds stock, cannot sell.',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='manager',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='managed_stores', to='accounts.userprofile',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='is_active',
            field=models.BooleanField(
                default=True,
                help_text='Deactivating blocks new sales at this store but preserves its history.',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='opening_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='closing_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='target_daily_revenue',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='phone',
            field=models.CharField(blank=True, help_text='Store line for SMS + storefront.', max_length=20),
        ),
        migrations.AddField(
            model_name='store',
            name='address_note',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='store',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.RunPython(backfill_store_type_from_is_kitchen, noop_reverse),
    ]
