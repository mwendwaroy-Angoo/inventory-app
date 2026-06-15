from django.db import migrations


def add_business_types(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    for name in [
        'Bar / Pub (Local Joint)',
        'Club / Lounge',
        'Wines & Spirits (Liquor Store)',
        'Nyama Choma Joint',
        'Posho Mill',
    ]:
        BusinessType.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_recurring_expense'),
    ]

    operations = [
        migrations.RunPython(add_business_types, migrations.RunPython.noop),
    ]
