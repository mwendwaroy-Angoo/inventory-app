from django.db import migrations


def add_business_types(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    new_types = [
        # Transport & Vehicle Ownership
        'Ride-Hailing Vehicle Owner (Uber/Bolt/Faras)',
        'Matatu / PSV Owner',
        'Boda Boda Operator',
        'Tuk-Tuk / Three-Wheeler Services',
        'School Bus / Shuttle Owner',
        'Tour & Safari Vehicle Owner',
        'Long-haul Truck Owner',
        # Miraa / Khat Trade
        'Miraa / Khat Trader',
        'Miraa Transport Vehicle Owner',
    ]
    for name in new_types:
        BusinessType.objects.get_or_create(name=name)


def remove_business_types(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_add_missing_business_types'),
    ]

    operations = [
        migrations.RunPython(add_business_types, remove_business_types),
    ]
