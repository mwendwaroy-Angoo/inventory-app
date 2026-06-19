"""
Data migration: remove duplicate Customer rows that share the same (business, name).

Customer has no unique_together on (business, name), so earlier test sessions
accumulated duplicate rows. When bar_board called get_or_create(business=x, name=y)
it hit MultipleObjectsReturned -> 500. This migration:

  1. Finds every (business_id, name) pair that appears more than once.
  2. Keeps the Customer with the lowest ID (oldest).
  3. Reassigns all CustomerDebtPayment rows from duplicates to the keeper.
  4. Deletes the duplicate Customer rows (BarTab.customer FK is SET_NULL).
"""
from django.db import migrations


def deduplicate_customers(apps, schema_editor):
    Customer = apps.get_model('core', 'Customer')
    CustomerDebtPayment = apps.get_model('core', 'CustomerDebtPayment')

    seen = {}
    for cust in Customer.objects.order_by('id'):
        key = (cust.business_id, cust.name.strip().lower())
        if key not in seen:
            seen[key] = cust.id
        else:
            # Reassign any debt payments to the keeper before deleting
            keeper_id = seen[key]
            CustomerDebtPayment.objects.filter(customer_id=cust.id).update(
                customer_id=keeper_id
            )
            cust.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_alter_pendingtransactionprompt_receipt'),
    ]

    operations = [
        migrations.RunPython(deduplicate_customers, migrations.RunPython.noop),
    ]
