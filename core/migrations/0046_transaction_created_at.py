import datetime
from django.db import migrations, models
from django.utils import timezone


def backfill_created_at(apps, schema_editor):
    """Set created_at to midnight of transaction date for all existing records."""
    Transaction = apps.get_model('core', 'Transaction')
    to_update = []
    for txn in Transaction.objects.filter(created_at__isnull=True):
        txn.created_at = timezone.make_aware(
            datetime.datetime.combine(txn.date, datetime.time.min)
        )
        to_update.append(txn)
    if to_update:
        Transaction.objects.bulk_update(to_update, ['created_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0045_kegbarrel_cups_dispensed'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='created_at',
            field=models.DateTimeField(
                auto_now_add=True, null=True, blank=True,
                help_text='Exact timestamp — used for shift-level reconciliation.',
            ),
        ),
        migrations.RunPython(backfill_created_at, migrations.RunPython.noop),
    ]
