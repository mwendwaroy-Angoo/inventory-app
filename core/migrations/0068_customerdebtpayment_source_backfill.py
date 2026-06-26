"""
Data migration: set source='bar' on all existing CustomerDebtPayment rows.
Rationale: kitchen debt-payment recording didn't exist before Sprint K1, so all
historical payments settle against the bar/general ledger. Known limitation:
a customer whose kitchen-origin credit was settled by an old lump payment shows
that payment on the bar side until new scoped payments wash through.
"""
from django.db import migrations


def backfill_source(apps, schema_editor):
    CustomerDebtPayment = apps.get_model('core', 'CustomerDebtPayment')
    CustomerDebtPayment.objects.filter(source='').update(source='bar')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_customerdebtpayment_source'),
    ]

    operations = [
        migrations.RunPython(backfill_source, migrations.RunPython.noop),
    ]
