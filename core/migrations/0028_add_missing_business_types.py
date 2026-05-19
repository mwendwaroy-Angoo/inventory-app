from django.db import migrations


def add_business_types(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    new_types = [
        # Original additions
        'Water Refilling / Dispensing Point',
        'Cereal & Grain Shop',
        'Kibanda / Food Stall',
        'Pool & Snooker Joint',
        'Car Wash',
        'Auto Garage / Mechanic',
        'Cyber Cafe / Printing Shop',
        'M-Pesa Agent / Mobile Money',
        'Liquor Store / Bar',
        'Posho Mill',
        'Laundry / Dry Cleaning',
        'Vegetable & Produce Stall',
        'Mitumba / Second-Hand Clothes',
        'Mkokoteni / Wholesale Distributor',
        'Fish Monger',
        'Butchery & Abattoir',
        'Cosmetics & Beauty Supply',
        'Agro-Vet Shop',
        'Spare Parts Shop',
        'Events & Catering',
        'Printing & Signage',
        'Tailoring & Alterations',
        'Mama Mboga / Kiosk',
        'Sand & Ballast Supplier',
        'Poultry Farm',
        'Dairy Farm',
        # Real Estate & Property
        'Real Estate Agency / Realtor',
        'Rental Properties / Landlord',
        'Property Management',
        'Short-term Rentals / Airbnb',
        'Real Estate Developer',
        # Construction (more specific)
        'Plumbing Services',
        'Electrical Contractor',
        'Painting & Decorating',
        'Tiling & Flooring',
        # Financial Services
        'Betting Shop / Gaming',
        'Forex Bureau',
        'Insurance Agency',
        'Microfinance / SACCO',
        # Food & Beverage (more specific)
        'Coffee Shop / Café',
        'Juice Bar',
        'Nyama Choma Joint',
        'Fast Food / Chips Mwitu',
        # Additional Services
        'Driving School',
        'Security Services',
        'Cleaning Services',
        'Photography / Videography',
        'Gym / Fitness Center',
        'Courier / Delivery Services',
        'Airtime & Accessories Shop',
        # Agriculture (more specific)
        'Greenhouse / Horticulture',
        'Fish Farm / Aquaculture',
        'Flower Farm',
        'Beekeeping / Honey Production',
        # Healthcare (more specific)
        'Dental Clinic',
        'Optical / Eye Clinic',
        'Traditional Medicine / Herbalist',
        # Water & Infrastructure Services
        'Borehole Drilling Services',
        'Water Tank Installation & Supply',
        'Solar Panel Installation',
        'Irrigation Services & Equipment',
        # Other high-CapEx trades
        'Welding & Fabrication Workshop',
        'Tyre Shop / Vulcanizer',
        'Generator Sales & Repair',
        'Roofing & Waterproofing',
    ]
    for name in new_types:
        BusinessType.objects.get_or_create(name=name)


def remove_business_types(apps, schema_editor):
    pass  # intentionally irreversible — don't delete on rollback


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_add_payment_method_to_transaction'),
    ]

    operations = [
        migrations.RunPython(add_business_types, remove_business_types),
    ]
