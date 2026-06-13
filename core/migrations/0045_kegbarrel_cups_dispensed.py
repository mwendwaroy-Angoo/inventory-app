import re
from django.db import migrations, models


def backfill_cups_dispensed(apps, schema_editor):
    """
    Derive historical cups_dispensed from BarTabEntry descriptions (×N suffix)
    for tab sales, and count 1-per-transaction for direct sales.
    """
    KegBarrel = apps.get_model('core', 'KegBarrel')
    Transaction = apps.get_model('core', 'Transaction')
    BarTabEntry = apps.get_model('core', 'BarTabEntry')

    to_update = []
    for barrel in KegBarrel.objects.all():
        count = 0
        for txn in Transaction.objects.filter(keg_barrel=barrel, type='Issue'):
            try:
                entry = BarTabEntry.objects.get(transaction=txn)
                m = re.search(r'×(\d+)$', entry.description)
                count += int(m.group(1)) if m else 1
            except BarTabEntry.DoesNotExist:
                count += 1
        if count > 0:
            barrel.cups_dispensed = count
            to_update.append(barrel)

    if to_update:
        KegBarrel.objects.bulk_update(to_update, ['cups_dispensed'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0044_keg_type_cups_overheads'),
    ]

    operations = [
        migrations.AddField(
            model_name='kegbarrel',
            name='cups_dispensed',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Running count of servings (cups) poured. Incremented by record_sale qty.',
            ),
        ),
        migrations.RunPython(backfill_cups_dispensed, migrations.RunPython.noop),
    ]
