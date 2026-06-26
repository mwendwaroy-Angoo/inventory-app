"""
Data migration: approve all existing customers so credit_policy_enabled=True
doesn't block them on first deploy. New customers created after this migration
will have credit_approved=False and must be explicitly approved.
"""
from django.db import migrations


def approve_existing_customers(apps, schema_editor):
    Customer = apps.get_model('core', 'Customer')
    Customer.objects.filter(credit_approved=False).update(credit_approved=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0071_customer_credit_gate_fields'),
    ]

    operations = [
        migrations.RunPython(approve_existing_customers, migrations.RunPython.noop),
    ]
