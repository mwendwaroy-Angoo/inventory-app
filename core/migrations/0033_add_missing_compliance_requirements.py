from django.db import migrations


MISSING_REQUIREMENTS = {
    'Agriculture / Farm': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit for agricultural businesses.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'County Agricultural Office Registration',
                'description': 'Registration with the county agricultural office for commercial farming.',
                'issuing_authority': 'County Agricultural Office',
                'approximate_cost': 'Minimal fee',
                'order': 2,
            },
            {
                'name': 'KEPHIS Certification (for produce export/sale)',
                'description': 'Kenya Plant Health Inspectorate Service certification required for selling or exporting fresh produce.',
                'issuing_authority': 'Kenya Plant Health Inspectorate Service (KEPHIS)',
                'approximate_cost': 'Varies per consignment',
                'order': 3,
                'mandatory': False,
            },
            {
                'name': 'KRA Agricultural Income Compliance',
                'description': 'Agricultural income above threshold must be declared to KRA.',
                'issuing_authority': 'Kenya Revenue Authority (KRA)',
                'approximate_cost': 'No direct cost (compliance)',
                'order': 4,
            },
        ],
    },
    'Clothing & Apparel': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Mitumba / Second-Hand Clothes': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'Import Documentation (if importing bales)',
                'description': 'Customs clearance documents for imported second-hand clothing bales.',
                'issuing_authority': 'Kenya Revenue Authority (KRA) / Kenya Ports Authority',
                'approximate_cost': 'Varies per consignment',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Electronics & IT': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'Communications Authority Type Approval',
                'description': 'Electronic devices sold must have CA type approval.',
                'issuing_authority': 'Communications Authority of Kenya (CA)',
                'approximate_cost': 'Per device type',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Spare Parts Shop': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Hardware Store': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KEBS Standards Compliance',
                'description': 'Construction materials (cement, steel, paint) must meet KEBS standards.',
                'issuing_authority': 'Kenya Bureau of Standards (KEBS)',
                'approximate_cost': 'Varies per product category',
                'order': 2,
            },
        ],
    },
    'Supermarket': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000–50,000 annually',
                'order': 1,
            },
            {
                'name': 'County Food Hygiene Certificate',
                'description': 'Food safety certification for retail food handling.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 2,
            },
            {
                'name': 'Food Handler Medical Certificates',
                'description': 'All staff handling food must have current medical certificates.',
                'issuing_authority': 'County Health Department',
                'approximate_cost': 'KES 500–1,000 per staff',
                'order': 3,
            },
            {
                'name': 'Fire Safety Certificate',
                'description': 'Annual fire safety inspection for the premises.',
                'issuing_authority': 'County Fire Department',
                'approximate_cost': 'KES 5,000–20,000',
                'order': 4,
            },
            {
                'name': 'KEBS Weights & Measures Compliance',
                'description': 'All weighing scales must be KEBS-certified.',
                'issuing_authority': 'KEBS',
                'approximate_cost': 'KES 2,000–5,000',
                'order': 5,
            },
        ],
    },
    'Wholesale': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KEBS Weights & Measures Compliance',
                'description': 'Weighing equipment must be KEBS-certified for wholesale trading.',
                'issuing_authority': 'KEBS',
                'approximate_cost': 'KES 2,000–5,000',
                'order': 2,
            },
        ],
    },
    'Transport & Logistics': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'NTSA Goods Vehicle Licence',
                'description': 'Licence for commercial goods transport vehicles.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'Annual fee per vehicle',
                'order': 1,
            },
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 2,
            },
            {
                'name': 'Commercial Vehicle Insurance',
                'description': 'Comprehensive insurance for commercial transport vehicles.',
                'issuing_authority': 'Licensed Insurance Provider',
                'approximate_cost': 'Varies by fleet size',
                'order': 3,
            },
        ],
    },
    'Manufacturing': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KEBS Product Standards Certification',
                'description': 'All manufactured products must meet KEBS standards before sale.',
                'issuing_authority': 'Kenya Bureau of Standards (KEBS)',
                'approximate_cost': 'Varies by product category',
                'order': 2,
            },
            {
                'name': 'NEMA Environmental Compliance',
                'description': 'Environmental impact assessment for manufacturing operations.',
                'issuing_authority': 'NEMA',
                'approximate_cost': 'KES 50,000–200,000',
                'order': 3,
            },
            {
                'name': 'Fire Safety Certificate',
                'description': 'Annual fire safety inspection for manufacturing premises.',
                'issuing_authority': 'County Fire Department',
                'approximate_cost': 'KES 5,000–20,000',
                'order': 4,
            },
        ],
    },
    'Events & Catering': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'County Food Hygiene Certificate',
                'description': 'Food safety certification for catering operations.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 2,
            },
            {
                'name': 'Food Handler Medical Certificates',
                'description': 'All food-handling staff require medical certificates.',
                'issuing_authority': 'County Health Department',
                'approximate_cost': 'KES 500–1,000 per staff',
                'order': 3,
            },
            {
                'name': 'Temporary Event Permit (per event)',
                'description': 'County permit required for each event venue.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'Varies per event',
                'order': 4,
            },
        ],
    },
    'Fast Food / Chips Mwitu': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'County Food Hygiene Certificate',
                'description': 'Food safety certification for food preparation premises.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 2,
            },
            {
                'name': 'Food Handler Medical Certificates',
                'description': 'All food-handling staff.',
                'issuing_authority': 'County Health Department',
                'approximate_cost': 'KES 500–1,000 per staff',
                'order': 3,
            },
            {
                'name': 'Fire Safety Certificate',
                'description': 'Required for cooking operations.',
                'issuing_authority': 'County Fire Department',
                'approximate_cost': 'KES 2,000–5,000',
                'order': 4,
            },
        ],
    },
    'Juice Bar': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'County Food Hygiene Certificate',
                'description': 'Food safety certification for beverage preparation.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 2,
            },
            {
                'name': 'Food Handler Medical Certificates',
                'description': 'All staff preparing food and beverages.',
                'issuing_authority': 'County Health Department',
                'approximate_cost': 'KES 500–1,000 per staff',
                'order': 3,
            },
        ],
    },
    'Printing & Signage': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Tailoring & Alterations': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Painting & Decorating': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'NCA Registration (for large contracts)',
                'description': 'NCA registration required when painting is part of a construction project.',
                'issuing_authority': 'National Construction Authority (NCA)',
                'approximate_cost': 'Varies by grade',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Cleaning Services': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Photography / Videography': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'Kenya Film Classification Board (KFCB) Compliance',
                'description': 'Required for commercial video production and distribution.',
                'issuing_authority': 'Kenya Film Classification Board (KFCB)',
                'approximate_cost': 'Varies by project',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Courier / Delivery Services': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'NTSA Vehicle Licences',
                'description': 'All delivery vehicles must have valid NTSA licences.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'Per vehicle annually',
                'order': 2,
            },
            {
                'name': 'Commercial Vehicle Insurance',
                'description': 'Insurance covering goods in transit.',
                'issuing_authority': 'Licensed Insurance Provider',
                'approximate_cost': 'Varies',
                'order': 3,
            },
        ],
    },
    'Posho Mill': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KEBS Weights & Measures Compliance',
                'description': 'Weighing equipment and flour quality must meet KEBS standards.',
                'issuing_authority': 'KEBS',
                'approximate_cost': 'KES 2,000–5,000',
                'order': 2,
            },
            {
                'name': 'County Food Hygiene Certificate',
                'description': 'Food safety certification for grain milling operations.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 3,
            },
        ],
    },
    'Stationery & Bookshop': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
        ],
    },
    'Short-term Rentals / Airbnb': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'Kenya Tourism Regulatory Authority (KTRA) Registration',
                'description': 'Short-term rentals are classified as tourism accommodation.',
                'issuing_authority': 'Kenya Tourism Regulatory Authority (KTRA)',
                'approximate_cost': 'Annual fee',
                'order': 2,
            },
            {
                'name': 'County Rates Clearance Certificate',
                'description': 'Land rates must be current for legal rental operations.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'Varies by property',
                'order': 3,
            },
            {
                'name': 'KRA Rental Income Tax Compliance',
                'description': 'Short-term rental income must be declared to KRA.',
                'issuing_authority': 'KRA',
                'approximate_cost': 'No direct cost (tax compliance)',
                'order': 4,
            },
        ],
    },
    'Beekeeping / Honey Production': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Kenya Honey Council Registration',
                'description': 'Registration with the Kenya Honey Council for commercial honey production.',
                'issuing_authority': 'Kenya Honey Council',
                'approximate_cost': 'Annual membership fee',
                'order': 1,
            },
            {
                'name': 'KEBS Honey Quality Standards',
                'description': 'Honey must meet KEBS quality and labelling standards.',
                'issuing_authority': 'KEBS',
                'approximate_cost': 'Testing fees',
                'order': 2,
            },
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 3,
            },
        ],
    },
    'Traditional Medicine / Herbalist': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KEBS Herbal Product Certification (packaged products)',
                'description': 'Required for packaged and labelled herbal products.',
                'issuing_authority': 'KEBS',
                'approximate_cost': 'Varies per product',
                'order': 2,
                'mandatory': False,
            },
            {
                'name': 'County Health Certificate',
                'description': 'Health certification for premises.',
                'issuing_authority': 'County Public Health Department',
                'approximate_cost': 'Included in SBP or separate',
                'order': 3,
            },
        ],
    },
    'Tyre Shop / Vulcanizer': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'NEMA Compliance (Tyre Waste)',
                'description': 'Proper disposal of used tyres required under NEMA regulations.',
                'issuing_authority': 'NEMA',
                'approximate_cost': 'Varies',
                'order': 2,
            },
        ],
    },
    'Generator Sales & Repair': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'EPRA Compliance (for fuel-handling)',
                'description': 'EPRA compliance if storing or handling fuel for generators.',
                'issuing_authority': 'EPRA',
                'approximate_cost': 'Varies',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Water Tank Installation & Supply': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'NCA Registration (for installation)',
                'description': 'NCA registration for water infrastructure installation.',
                'issuing_authority': 'NCA',
                'approximate_cost': 'Varies by grade',
                'order': 2,
                'mandatory': False,
            },
        ],
    },
    'Irrigation Services & Equipment': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'Water Resources Authority Permit',
                'description': 'WRA permit for water abstraction for irrigation.',
                'issuing_authority': 'Water Resources Authority (WRA)',
                'approximate_cost': 'Annual fee',
                'order': 2,
            },
        ],
    },
    'Miraa / Khat Trader': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'County Trading Permit',
                'description': 'Miraa trading is regulated at county level — especially in producing counties (Meru, Tharaka-Nithi, Embu).',
                'issuing_authority': 'County Government',
                'approximate_cost': 'Varies by county',
                'order': 2,
            },
        ],
    },
    'Miraa Transport Vehicle Owner': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'NTSA Vehicle Licence',
                'description': 'Valid NTSA licence for the transport vehicle.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'Annual fee',
                'order': 1,
            },
            {
                'name': 'County Transport Permit',
                'description': 'County permit for miraa transport operations.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'Varies by county',
                'order': 2,
            },
            {
                'name': 'Commercial Vehicle Insurance',
                'description': 'Insurance for commercial goods transport.',
                'issuing_authority': 'Licensed Insurance Provider',
                'approximate_cost': 'Varies',
                'order': 3,
            },
        ],
    },
    'Long-haul Truck Owner': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'NTSA Goods Vehicle Licence',
                'description': 'Licence for commercial long-haul transport.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'Annual fee per vehicle',
                'order': 1,
            },
            {
                'name': 'Axle Load Compliance Certificate',
                'description': 'Vehicles must comply with KEBS axle load limits.',
                'issuing_authority': 'NTSA / KEBS',
                'approximate_cost': 'Inspection fee',
                'order': 2,
            },
            {
                'name': 'Commercial Vehicle Insurance',
                'description': 'Comprehensive insurance with goods-in-transit cover.',
                'issuing_authority': 'Licensed Insurance Provider',
                'approximate_cost': 'Varies by vehicle value',
                'order': 3,
            },
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 4,
            },
        ],
    },
    'School Bus / Shuttle Owner': {
        'tier': 'formal',
        'requirements': [
            {
                'name': 'NTSA PSV Licence',
                'description': 'Public Service Vehicle licence for school transport.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'Annual fee',
                'order': 1,
            },
            {
                'name': 'PSV Insurance',
                'description': 'Comprehensive PSV insurance for school transport.',
                'issuing_authority': 'Licensed Insurance Provider',
                'approximate_cost': 'Varies',
                'order': 2,
            },
            {
                'name': 'Ministry of Education Approval',
                'description': 'School transport must be approved by the school and relevant education authorities.',
                'issuing_authority': 'Ministry of Education / School Administration',
                'approximate_cost': 'No direct cost',
                'order': 3,
            },
            {
                'name': 'Annual Vehicle Inspection Certificate',
                'description': 'Annual NTSA roadworthiness inspection.',
                'issuing_authority': 'NTSA',
                'approximate_cost': 'KES 1,000–3,000',
                'order': 4,
            },
        ],
    },
    'Other': {
        'tier': 'semi',
        'requirements': [
            {
                'name': 'Single Business Permit (SBP)',
                'description': 'Annual county business operating permit — required for all formal businesses.',
                'issuing_authority': 'County Government',
                'approximate_cost': 'KES 10,000 annually',
                'order': 1,
            },
            {
                'name': 'KRA PIN Certificate',
                'description': 'Kenya Revenue Authority PIN for tax compliance.',
                'issuing_authority': 'Kenya Revenue Authority (KRA)',
                'approximate_cost': 'Free (online registration)',
                'order': 2,
            },
        ],
    },
}


def populate_missing_requirements(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    BusinessTypeRequirement = apps.get_model('core', 'BusinessTypeRequirement')

    for bt_name, data in MISSING_REQUIREMENTS.items():
        try:
            bt = BusinessType.objects.get(name=bt_name)
        except BusinessType.DoesNotExist:
            continue

        tier = data.get('tier', 'formal')
        for i, req in enumerate(data.get('requirements', [])):
            BusinessTypeRequirement.objects.get_or_create(
                business_type=bt,
                name=req['name'],
                defaults={
                    'tier':               tier,
                    'description':        req.get('description', ''),
                    'issuing_authority':  req.get('issuing_authority', ''),
                    'approximate_cost':   req.get('approximate_cost', ''),
                    'is_mandatory':       req.get('mandatory', True),
                    'display_order':      req.get('order', i),
                },
            )


def remove_missing_requirements(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_populate_business_type_requirements'),
    ]

    operations = [
        migrations.RunPython(
            populate_missing_requirements,
            remove_missing_requirements,
        ),
    ]
