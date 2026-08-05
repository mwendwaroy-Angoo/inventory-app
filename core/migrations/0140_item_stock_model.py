from django.db import migrations, models


def backfill_stock_model(apps, schema_editor):
    """UBA §2.1 backfill for existing rows, using the same rule Item.save()
    applies going forward: BUNCH produce -> ENVELOPE, other produce -> UNIT,
    keg items -> MEASURE, kitchen batch items -> ENVELOPE, everything else
    keeps the column's own default (UNIT).
    """
    Item = apps.get_model('core', 'Item')
    Item.objects.filter(is_produce=True, produce_mode='BUNCH').update(stock_model='ENVELOPE')
    Item.objects.filter(is_produce=True).exclude(produce_mode='BUNCH').update(stock_model='UNIT')
    Item.objects.filter(is_produce=False, is_keg=True).update(stock_model='MEASURE')
    Item.objects.filter(is_produce=False, is_keg=False, is_kitchen_batch=True).update(stock_model='ENVELOPE')


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0139_shift_closed_by_shift_force_close_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='stock_model',
            field=models.CharField(
                choices=[
                    ('UNIT', 'Unit — countable (default)'),
                    ('MEASURE', 'Measure — weight/volume, decanted from a bulk parent'),
                    ('ENVELOPE', 'Envelope — revenue batch, no unit count'),
                    ('VARIANT', 'Variant — one product, many sellable children (size/colour)'),
                    ('SERIAL', 'Serial — each physical unit individually identified'),
                    ('LOT', 'Lot — batch with its own expiry, FIFO depletion'),
                    ('SERVICE', 'Service — time + skill, consumes supplies per a recipe'),
                    ('ASSET', 'Asset — goes out, must come back'),
                ],
                default='UNIT',
                help_text="UBA capability model: how this item's stock is counted. "
                          "Kept in sync with is_produce/produce_mode in save() for "
                          "existing items — see Item.save().",
                max_length=10,
            ),
        ),
        migrations.RunPython(backfill_stock_model, noop_reverse),
    ]
