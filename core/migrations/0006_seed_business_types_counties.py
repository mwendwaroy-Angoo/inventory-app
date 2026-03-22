from django.db import migrations

BUSINESS_TYPES = [
    "Retail Shop", "Wholesale", "Supermarket", "Hardware Store",
    "Pharmacy", "Restaurant / Hotel", "Salon & Barbershop",
    "Clinic / Hospital", "School / College", "Construction",
    "Agriculture / Farm", "Transport & Logistics", "Manufacturing",
    "Electronics & IT", "Clothing & Apparel", "Fuel Station",
    "Bakery", "Butchery", "Stationery & Bookshop", "Other",
]

# Kenya Counties and their Sub-locations
KENYA_LOCATIONS = {
    "Nairobi": [
        "Westlands", "Dagoretti", "Langata", "Kibra", "Roysambu",
        "Kasarani", "Ruaraka", "Embakasi South", "Embakasi North",
        "Embakasi Central", "Embakasi East", "Embakasi West",
        "Makadara", "Kamukunji", "Starehe", "Mathare",
    ],
    "Mombasa": [
        "Changamwe", "Jomvu", "Kisauni", "Nyali", "Likoni", "Mvita",
    ],
    "Kwale": [
        "Msambweni", "Lungalunga", "Matuga", "Kinango",
    ],
    "Kilifi": [
        "Kilifi North", "Kilifi South", "Kaloleni", "Rabai",
        "Ganze", "Malindi", "Magarini",
    ],
    "Tana River": ["Garsen", "Galole", "Bura"],
    "Lamu": ["Lamu East", "Lamu West"],
    "Taita Taveta": ["Taveta", "Wundanyi", "Mwatate", "Voi"],
    "Garissa": [
        "Garissa Township", "Balambala", "Lagdera", "Dadaab",
        "Fafi", "Ijara",
    ],
    "Wajir": ["Wajir North", "Wajir East", "Tarbaj", "Wajir West", "Eldas", "Wajir South"],
    "Mandera": ["Mandera East", "Banissa", "Mandera North", "Mandera South", "Mandera West", "Lafey"],
    "Marsabit": ["Moyale", "North Horr", "Saku", "Laisamis"],
    "Isiolo": ["Isiolo North", "Isiolo South"],
    "Meru": ["Igembe South", "Igembe Central", "Igembe North", "Tigania West", "Tigania East", "Central Imenti", "North Imenti", "South Imenti", "Buuri"],
    "Tharaka Nithi": ["Maara", "Chuka / Igambang'ombe", "Tharaka"],
    "Embu": ["Manyatta", "Runyenjes", "Mbeere South", "Mbeere North"],
    "Kitui": ["Mwingi North", "Mwingi West", "Mwingi Central", "Kitui West", "Kitui Rural", "Kitui Central", "Kitui East", "Kitui South"],
    "Machakos": ["Masinga", "Yatta", "Kangundo", "Matungulu", "Kathiani", "Mavoko", "Machakos Town", "Mwala"],
    "Makueni": ["Mbooni", "Kilome", "Kaiti", "Makueni", "Kibwezi West", "Kibwezi East"],
    "Nyandarua": ["Kinangop", "Kipipiri", "Ol Kalou", "Ol Joro Orok", "Ndaragwa"],
    "Nyeri": ["Tetu", "Kieni", "Mathira", "Othaya", "Mukurweini", "Nyeri Town"],
    "Kirinyaga": ["Mwea", "Gichugu", "Ndia", "Kirinyaga Central"],
    "Murang'a": ["Kiharu", "Kigumo", "Maragwa", "Kandara", "Gatanga", "Kahuro", "Mathioya"],
    "Kiambu": ["Gatundu South", "Gatundu North", "Juja", "Thika Town", "Ruiru", "Githunguri", "Kiambu", "Kiambaa", "Kabete", "Kikuyu", "Limuru", "Lari"],
    "Turkana": ["Turkana North", "Turkana West", "Turkana Central", "Loima", "Turkana South", "Turkana East"],
    "West Pokot": ["Kapenguria", "Sigor", "Kacheliba", "Pokot South"],
    "Samburu": ["Samburu West", "Samburu North", "Samburu East"],
    "Trans Nzoia": ["Kwanza", "Endebess", "Saboti", "Kiminini", "Cherangany"],
    "Uasin Gishu": ["Soy", "Turbo", "Moiben", "Ainabkoi", "Kapseret", "Kesses"],
    "Elgeyo Marakwet": ["Marakwet East", "Marakwet West", "Keiyo North", "Keiyo South"],
    "Nandi": ["Tinderet", "Aldai", "Nandi Hills", "Chesumei", "Emgwen", "Mosop"],
    "Baringo": ["Tiaty", "Baringo North", "Baringo Central", "Baringo South", "Eldama Ravine", "Mogotio"],
    "Laikipia": ["Laikipia West", "Laikipia East", "Laikipia North"],
    "Nakuru": ["Molo", "Njoro", "Naivasha", "Gilgil", "Kuresoi South", "Kuresoi North", "Subukia", "Rongai", "Bahati", "Nakuru Town West", "Nakuru Town East"],
    "Narok": ["Kilgoris", "Emurua Dikirr", "Narok North", "Narok East", "Narok South", "Narok West"],
    "Kajiado": ["Kajiado North", "Kajiado Central", "Kajiado East", "Kajiado West", "Kajiado South"],
    "Kericho": ["Kipkelion East", "Kipkelion West", "Ainamoi", "Bureti", "Belgut", "Sigowet / Soin"],
    "Bomet": ["Sotik", "Chepalungu", "Bomet East", "Bomet Central", "Konoin"],
    "Kakamega": ["Lugari", "Likuyani", "Malava", "Lurambi", "Navakholo", "Mumias West", "Mumias East", "Matungu", "Butere", "Khwisero", "Shinyalu", "Ikolomani"],
    "Vihiga": ["Vihiga", "Sabatia", "Hamisi", "Luanda", "Emuhaya"],
    "Bungoma": ["Mt. Elgon", "Sirisia", "Kabuchai", "Bumula", "Kanduyi", "Webuye East", "Webuye West", "Kimilili", "Tongaren"],
    "Busia": ["Teso North", "Teso South", "Nambale", "Matayos", "Butula", "Funyula", "Budalangi"],
    "Siaya": ["Ugenya", "Ugunja", "Alego Usonga", "Gem", "Bondo", "Rarieda"],
    "Kisumu": ["Kisumu East", "Kisumu West", "Kisumu Central", "Seme", "Nyando", "Muhoroni", "Nyakach"],
    "Homa Bay": ["Kasipul", "Kabondo Kasipul", "Karachuonyo", "Rangwe", "Homa Bay Town", "Ndhiwa", "Suba North", "Suba South"],
    "Migori": ["Rongo", "Awendo", "Suna East", "Suna West", "Uriri", "Nyatike", "Kuria West", "Kuria East"],
    "Kisii": ["Bonchari", "South Mugirango", "Bomachoge Borabu", "Bobasi", "Bomachoge Chache", "Nyaribari Masaba", "Nyaribari Chache", "Kitutu Chache North", "Kitutu Chache South"],
    "Nyamira": ["Kitutu Masaba", "West Mugirango", "North Mugirango", "Borabu"],
}


def seed_data(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    County = apps.get_model('core', 'County')
    SubLocation = apps.get_model('core', 'SubLocation')

    # Seed Business Types
    for name in BUSINESS_TYPES:
        BusinessType.objects.get_or_create(name=name)

    # Seed Counties and Sub-locations
    for county_name, sublocations in KENYA_LOCATIONS.items():
        county, _ = County.objects.get_or_create(name=county_name)
        for sub_name in sublocations:
            SubLocation.objects.get_or_create(county=county, name=sub_name)


def unseed_data(apps, schema_editor):
    BusinessType = apps.get_model('core', 'BusinessType')
    County = apps.get_model('core', 'County')
    SubLocation = apps.get_model('core', 'SubLocation')
    SubLocation.objects.all().delete()
    County.objects.all().delete()
    BusinessType.objects.filter(name__in=BUSINESS_TYPES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_alter_store_business'),  # ← update this to your latest migration
    ]

    operations = [
        migrations.RunPython(seed_data, unseed_data),
    ]