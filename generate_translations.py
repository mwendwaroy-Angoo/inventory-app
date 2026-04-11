"""
Generate .po translation files for all Kenyan languages with starter translations.
Hybrid approach: Swahili gets full translations, other languages get starter placeholders.
"""
import os
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCALE_DIR = os.path.join(BASE_DIR, 'locale')

# All translatable strings extracted from templates
MESSAGES = [
    "Dashboard",
    "Suppliers",
    "Fulfillment",
    "Notifications",
    "Browse Requests",
    "My Bids",
    "Businesses",
    "Stock List",
    "Add Transaction",
    "Quick Sell",
    "History",
    "Order Fulfillment",
    "Payments",
    "Supply Chain",
    "My Suppliers",
    "Supplier Applications",
    "My Procurement",
    "New Procurement",
    "Browse Businesses",
    "Manage",
    "Stores",
    "Items",
    "Sales & P&L",
    "Analytics",
    "Staff",
    "Add Staff",
    "Orders",
    "Feedback",
    "Payment Prompts",
    "Payment Settings",
    "Business Settings",
    "View Tutorial",
    "Change Password",
    "Language",
    "Logout",
    "Delete Account",
    "Choose Language",
    "Save Language",
    "Sign in to your account",
    "Invalid username or password.",
    "Login",
    "Forgot password?",
    "Staff members:",
    "Ask your business owner to reset your password from the Staff page.",
    "Don't have an account?",
    "Register your business",
    "Overview",
    "Inventory",
    "Sales",
    "All Orders",
    "Customers",
    "Transaction History",
    "Manage Items",
    "Deliveries",
    "Active Deliveries",
    "Delivery History",
    "Earnings",
    "My Earnings",
    "Toggle Availability",
    "Profile",
    "Procurement",
    "Clients",
    "My Clients",
    "Catalog",
    "My Products",
    "Applications",
    "Management",
    "Supplier Portal",
    "Rider Portal",
    "Owner Portal",
    "My Supply Business",
    "My Business",
]

# Languages and their translations
# sw = Kiswahili (complete translations)
# Other languages get starter translations for key terms
TRANSLATIONS = {
    'sw': {
        "Dashboard": "Dashibodi",
        "Suppliers": "Wasambazaji",
        "Fulfillment": "Utimizaji",
        "Notifications": "Arifa",
        "Browse Requests": "Tazama Maombi",
        "My Bids": "Zabuni Zangu",
        "Businesses": "Biashara",
        "Stock List": "Orodha ya Hisa",
        "Add Transaction": "Ongeza Muamala",
        "Quick Sell": "Uza Haraka",
        "History": "Historia",
        "Order Fulfillment": "Utimizaji wa Agizo",
        "Payments": "Malipo",
        "Supply Chain": "Mnyororo wa Usambazaji",
        "My Suppliers": "Wasambazaji Wangu",
        "Supplier Applications": "Maombi ya Wasambazaji",
        "My Procurement": "Ununuzi Wangu",
        "New Procurement": "Ununuzi Mpya",
        "Browse Businesses": "Tazama Biashara",
        "Manage": "Simamia",
        "Stores": "Maduka",
        "Items": "Bidhaa",
        "Sales & P&L": "Mauzo na Faida/Hasara",
        "Analytics": "Takwimu",
        "Staff": "Wafanyakazi",
        "Add Staff": "Ongeza Mfanyakazi",
        "Orders": "Maagizo",
        "Feedback": "Maoni",
        "Payment Prompts": "Vikumbusho vya Malipo",
        "Payment Settings": "Mipangilio ya Malipo",
        "Business Settings": "Mipangilio ya Biashara",
        "View Tutorial": "Tazama Mwongozo",
        "Change Password": "Badilisha Nenosiri",
        "Language": "Lugha",
        "Logout": "Ondoka",
        "Delete Account": "Futa Akaunti",
        "Choose Language": "Chagua Lugha",
        "Save Language": "Hifadhi Lugha",
        "Sign in to your account": "Ingia kwenye akaunti yako",
        "Invalid username or password.": "Jina la mtumiaji au nenosiri si sahihi.",
        "Login": "Ingia",
        "Forgot password?": "Umesahau nenosiri?",
        "Staff members:": "Wafanyakazi:",
        "Ask your business owner to reset your password from the Staff page.": "Muombe mmiliki wa biashara yako akurejeshe nenosiri kutoka ukurasa wa Wafanyakazi.",
        "Don't have an account?": "Huna akaunti?",
        "Register your business": "Sajili biashara yako",
        "Overview": "Muhtasari",
        "Inventory": "Hisa",
        "Sales": "Mauzo",
        "All Orders": "Maagizo Yote",
        "Customers": "Wateja",
        "Transaction History": "Historia ya Miamala",
        "Manage Items": "Simamia Bidhaa",
        "Deliveries": "Usafirishaji",
        "Active Deliveries": "Usafirishaji Unaoendelea",
        "Delivery History": "Historia ya Usafirishaji",
        "Earnings": "Mapato",
        "My Earnings": "Mapato Yangu",
        "Toggle Availability": "Badilisha Upatikanaji",
        "Profile": "Wasifu",
        "Procurement": "Ununuzi",
        "Clients": "Wateja",
        "My Clients": "Wateja Wangu",
        "Catalog": "Katalogi",
        "My Products": "Bidhaa Zangu",
        "Applications": "Maombi",
        "Management": "Usimamizi",
        "Supplier Portal": "Lango la Msambazaji",
        "Rider Portal": "Lango la Mpanda Baiskeli",
        "Owner Portal": "Lango la Mmiliki",
        "My Supply Business": "Biashara Yangu ya Usambazaji",
        "My Business": "Biashara Yangu",
    },
    'ki': {  # Gĩkũyũ
        "Dashboard": "Dashibodi",
        "Suppliers": "Aheani",
        "Fulfillment": "Kũhingũra",
        "Notifications": "Mahĩtia",
        "Browse Requests": "Rora Maũhĩro",
        "My Bids": "Mabidi Makwa",
        "Businesses": "Maciarwa",
        "Stock List": "Mũrangio wa Indo",
        "Add Transaction": "Ongera Mũciaro",
        "Quick Sell": "Enda Haraka",
        "History": "Ũhoro wa Tene",
        "Order Fulfillment": "Kũhingũra Maodha",
        "Payments": "Marĩba",
        "Supply Chain": "Mũnyororo wa Ũheani",
        "My Suppliers": "Aheani Akwa",
        "Manage": "Taara",
        "Stores": "Nduka",
        "Items": "Indo",
        "Staff": "Andũ a Wĩra",
        "Orders": "Maodha",
        "Login": "Toonya",
        "Logout": "Ũma",
        "Language": "Rũthiomi",
        "Choose Language": "Thagũra Rũthiomi",
        "Save Language": "Hĩthia Rũthiomi",
        "Sign in to your account": "Toonya akaunti yaku",
        "Don't have an account?": "Ndũrĩ na akaunti?",
        "Register your business": "Andĩkĩthia biashara yaku",
        "Change Password": "Cenjia Nenosiri",
        "Delete Account": "Thiira Akaunti",
        "Feedback": "Ũcokio",
        "Analytics": "Takwimu",
    },
    'luo': {  # Dholuo
        "Dashboard": "Dashboard",
        "Suppliers": "Jochiwo",
        "Notifications": "Wach manyien",
        "Businesses": "Ohala",
        "Stock List": "Ranyisi mar gik manie stoo",
        "Items": "Gik",
        "Staff": "Jotich",
        "Orders": "Chike",
        "Login": "Donji",
        "Logout": "Wuogi",
        "Language": "Dhok",
        "Choose Language": "Yier Dhok",
        "Save Language": "Kan Dhok",
        "Sign in to your account": "Donji e akaunti mari",
        "Don't have an account?": "Ionge gi akaunti?",
        "Register your business": "Ndik ohala mari",
        "Payments": "Chudo",
        "History": "Histori",
        "Manage": "Rit",
        "Stores": "Duche",
        "Feedback": "Dwoko",
    },
    'kln': {  # Kalenjin
        "Dashboard": "Dashboard",
        "Login": "Itu",
        "Logout": "Ityo",
        "Language": "Kutit",
        "Choose Language": "Til Kutit",
        "Save Language": "Somanchi Kutit",
        "Stores": "Tuguchu",
        "Items": "Ichek",
        "Staff": "Bik che kiboisiyo",
        "Payments": "Arawek",
        "Sign in to your account": "Itu ko akaunti",
        "Don't have an account?": "Momi akaunti?",
        "Register your business": "Ndike biashara",
    },
    'kam': {  # Kĩkamba
        "Dashboard": "Dashibodi",
        "Login": "Ĩngĩa",
        "Logout": "Ũma",
        "Language": "Lũkha",
        "Choose Language": "Sũa Lũkha",
        "Save Language": "Vika Lũkha",
        "Stores": "Nduka",
        "Items": "Indo",
        "Staff": "Andu a wĩa",
        "Payments": "Malĩbu",
        "Sign in to your account": "Ĩngĩa akaunti yaku",
        "Don't have an account?": "Wĩ na akaunti?",
        "Register your business": "Andĩkĩthya biashara yaku",
        "Notifications": "Ĩla",
        "History": "Mbaĩtu ya tene",
    },
    'luy': {  # Luhya
        "Dashboard": "Dashboard",
        "Login": "Inyala",
        "Logout": "Fuula",
        "Language": "Lulimi",
        "Choose Language": "Sola Lulimi",
        "Save Language": "Bika Lulimi",
        "Stores": "Tsiduka",
        "Items": "Ebindu",
        "Staff": "Abakosi",
        "Payments": "Efirigo",
        "Sign in to your account": "Inyala mu akaunti yio",
        "Don't have an account?": "Oulina akaunti?",
        "Register your business": "Andikisha biashara yio",
    },
    'guz': {  # Ekegusii
        "Dashboard": "Dashboard",
        "Login": "Tera",
        "Logout": "Oka",
        "Language": "Ekegusii",
        "Choose Language": "Tobora Omoruok",
        "Stores": "Chiduka",
        "Items": "Ebinto",
        "Staff": "Abakora",
        "Payments": "Obogambi",
        "Sign in to your account": "Tera akaunti yao",
    },
    'mer': {  # Kĩmĩrũ
        "Dashboard": "Dashibodi",
        "Login": "Toonya",
        "Logout": "Ũma",
        "Language": "Rũthiomi",
        "Choose Language": "Thagũra Rũthiomi",
        "Stores": "Nduka",
        "Items": "Indo",
        "Staff": "Andũ a wĩra",
        "Payments": "Marĩba",
        "Sign in to your account": "Toonya akaunti yaku",
    },
    'mas': {  # Maa (Maasai)
        "Dashboard": "Dashboard",
        "Login": "Iyiolo",
        "Logout": "Dupoto",
        "Language": "Engutuk",
        "Choose Language": "Aidip Engutuk",
        "Stores": "Idukani",
        "Items": "Inkera",
        "Staff": "Iltungana le nkishu",
        "Payments": "Enshillingi",
        "Sign in to your account": "Iyiolo akaunti lino",
    },
    'tuv': {  # Ng'aturkana
        "Dashboard": "Dashboard",
        "Login": "Apou",
        "Logout": "Alot",
        "Language": "Akuj",
        "Stores": "Edukani",
        "Items": "Ngikaliok",
        "Payments": "Ngishillingi",
    },
    'so': {  # Soomaali
        "Dashboard": "Dashboard",
        "Suppliers": "Bixiyeyaasha",
        "Notifications": "Ogeysiisyo",
        "Businesses": "Ganacsiyada",
        "Items": "Alaabada",
        "Staff": "Shaqaalaha",
        "Orders": "Dalbashada",
        "Login": "Soo gal",
        "Logout": "Ka bax",
        "Language": "Luqadda",
        "Choose Language": "Dooro Luqadda",
        "Save Language": "Kaydi Luqadda",
        "Stores": "Dukaanka",
        "Payments": "Lacagaha",
        "History": "Taariikhda",
        "Sign in to your account": "Soo gal akoontigaada",
        "Don't have an account?": "Ma lihid akoonti?",
        "Register your business": "Diiwaan geli ganacsigaaga",
    },
    'dav': {  # Kitaita
        "Login": "Ingira",
        "Logout": "Humuka",
        "Language": "Lugha",
        "Stores": "Maduka",
        "Items": "Vitu",
        "Payments": "Malipo",
    },
    'pko': {  # Pokot
        "Login": "Pagh",
        "Logout": "Akwaan",
        "Language": "Tukun",
        "Stores": "Dukani",
        "Items": "Koit",
        "Payments": "Silinge",
    },
    'teo': {  # Ateso
        "Login": "Aibuni",
        "Logout": "Apedor",
        "Language": "Etesot",
        "Stores": "Idukani",
        "Items": "Ikalia",
        "Payments": "Isilinge",
    },
    'saq': {  # Samburu
        "Login": "Iyiolo",
        "Logout": "Dupoto",
        "Language": "Engutuk",
        "Stores": "Idukani",
        "Items": "Inkera",
        "Payments": "Enshillingi",
    },
    'ebu': {  # Kĩembu
        "Dashboard": "Dashibodi",
        "Login": "Toonya",
        "Logout": "Ũma",
        "Language": "Rũthiomi",
        "Choose Language": "Thagũra Rũthiomi",
        "Stores": "Nduka",
        "Items": "Indo",
        "Staff": "Andũ a wĩra",
        "Payments": "Marĩba",
        "Sign in to your account": "Toonya akaunti yaku",
    },
}

# Language display names
LANG_NAMES = {
    'sw': 'Kiswahili',
    'ki': 'Gĩkũyũ',
    'luo': 'Dholuo',
    'kln': 'Kalenjin',
    'kam': 'Kĩkamba',
    'luy': 'Luhya',
    'guz': 'Ekegusii',
    'mer': 'Kĩmĩrũ',
    'mas': 'Maa (Maasai)',
    'tuv': "Ng'aturkana",
    'so': 'Soomaali',
    'dav': 'Kitaita',
    'pko': 'Pokot',
    'teo': 'Ateso',
    'saq': 'Samburu',
    'ebu': 'Kĩembu',
}


def generate_po_file(lang_code, lang_name, translations):
    """Generate a .po file for a given language."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M%z')

    header = f'''# {lang_name} translations for Duka Mwecheche
# Copyright (C) 2026 Duka Mwecheche
# This file is distributed under the same license as the Duka Mwecheche package.
#
msgid ""
msgstr ""
"Project-Id-Version: Duka Mwecheche 1.0\\n"
"Report-Msgid-Bugs-To: \\n"
"POT-Creation-Date: {now}\\n"
"PO-Revision-Date: {now}\\n"
"Last-Translator: Duka Mwecheche Team\\n"
"Language-Team: {lang_name}\\n"
"Language: {lang_code}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

'''

    entries = []
    for msg in MESSAGES:
        trans = translations.get(msg, '')
        # Escape special characters
        escaped_msg = msg.replace('\\', '\\\\').replace('"', '\\"')
        escaped_trans = trans.replace('\\', '\\\\').replace('"', '\\"') if trans else ''

        if not trans:
            entry = f'#  TODO: Translate to {lang_name}\nmsgid "{escaped_msg}"\nmsgstr ""\n'
        else:
            entry = f'msgid "{escaped_msg}"\nmsgstr "{escaped_trans}"\n'
        entries.append(entry)

    return header + '\n'.join(entries)


def main():
    for lang_code, lang_name in LANG_NAMES.items():
        lang_dir = os.path.join(LOCALE_DIR, lang_code, 'LC_MESSAGES')
        os.makedirs(lang_dir, exist_ok=True)

        translations = TRANSLATIONS.get(lang_code, {})
        po_content = generate_po_file(lang_code, lang_name, translations)

        po_path = os.path.join(lang_dir, 'django.po')
        with open(po_path, 'w', encoding='utf-8') as f:
            f.write(po_content)

        print(f'✓ Created {lang_code}/LC_MESSAGES/django.po ({len([m for m in MESSAGES if m in translations])}/{len(MESSAGES)} translated)')

    print(f'\nDone! Created .po files for {len(LANG_NAMES)} languages.')
    print('Swahili (sw) has complete translations.')
    print('Other languages have starter translations - community contributions welcome!')


if __name__ == '__main__':
    main()
