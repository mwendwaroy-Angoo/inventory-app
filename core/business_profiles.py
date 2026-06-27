"""
Business-type profile registry for Duka Mwecheche.

Each profile configures:
  board   — which Quick Sell board: 'bar' | 'produce' | 'grid'
  modules — feature flags: keg, tabs, shifts, produce
  catalog — item catalog powering the item-form auto-fill

Usage:
    from core.business_profiles import get_profile
    profile = get_profile(business)
    # profile['board'], profile['modules']['keg'], profile['catalog']
"""

# ── Catalog helper builders ────────────────────────────────────────────────────

def _keg(name):
    return {
        'name': name, 'unit': 'Ml', 'is_keg': True, 'is_produce': False,
        'presets': [
            {'label': 'Kikombe 300ml', 'price': None, 'qty': 300},
            {'label': 'Jug', 'price': None, 'qty': 1250},
        ],
    }


def _spirit(name, vol_ml=750):
    if vol_ml == 750:
        presets = [
            {'label': 'Single shot', 'price': None, 'qty': 0.04},
            {'label': 'Double shot', 'price': None, 'qty': 0.08},
            {'label': 'Nusu / Half', 'price': None, 'qty': 0.5},
            {'label': 'Mzima / Full', 'price': None, 'qty': 1.0},
        ]
    elif vol_ml in (350, 375):
        presets = [
            {'label': 'Half', 'price': None, 'qty': 0.5},
            {'label': 'Mzima / Full', 'price': None, 'qty': 1.0},
        ]
    else:  # 250ml quarter
        presets = [
            {'label': 'Shot', 'price': None, 'qty': 0.1},
            {'label': 'Mzima / Full', 'price': None, 'qty': 1.0},
        ]
    return {
        'name': name, 'unit': 'Btl', 'is_keg': False, 'is_produce': False,
        'volume_ml': vol_ml, 'presets': presets,
    }


def _beer(name):
    return {
        'name': name, 'unit': 'Btl', 'is_keg': False, 'is_produce': False,
        'presets': [{'label': 'Mzima', 'price': None, 'qty': 1.0}],
    }


def _soda(name):
    return {
        'name': name, 'unit': 'Btl', 'is_keg': False, 'is_produce': False,
        'presets': [{'label': 'Chupa', 'price': None, 'qty': 1.0}],
    }


def _cig(name):
    return {
        'name': name, 'unit': 'Pkt', 'is_keg': False, 'is_produce': False,
        'presets': [
            {'label': 'Per stick',  'price': None, 'qty': 0.05},
            {'label': 'Per packet', 'price': None, 'qty': 1.0},
        ],
    }


def _bunch(name):
    return {'name': name, 'unit': 'Bunch', 'is_keg': False, 'is_produce': True,
            'produce_mode': 'BUNCH', 'presets': []}


def _batch(name, unit='Gorogoro'):
    return {'name': name, 'unit': unit, 'is_keg': False, 'is_produce': True,
            'produce_mode': 'BUNCH', 'presets': []}


def _portion(name, unit='Pcs'):
    return {'name': name, 'unit': unit, 'is_keg': False, 'is_produce': True,
            'produce_mode': 'PORTION', 'presets': []}


def _kg(name):
    return {
        'name': name, 'unit': 'Kg', 'is_keg': False, 'is_produce': False,
        'presets': [
            {'label': '1 Kg',     'price': None, 'qty': 1.0},
            {'label': 'Nusu kg',  'price': None, 'qty': 0.5},
            {'label': 'Robo kg',  'price': None, 'qty': 0.25},
        ],
    }


def _pc(name):
    return {
        'name': name, 'unit': 'Pcs', 'is_keg': False, 'is_produce': False,
        'presets': [{'label': 'Kimoja', 'price': None, 'qty': 1.0}],
    }


# ── Bar / Pub catalog ─────────────────────────────────────────────────────────

BAR_CATALOG = [
    # Kegs
    _keg('Senator Keg Dark'),
    _keg('Senator Keg Lite'),
    _keg('Guinness Smooth Keg'),

    # Beers (bottles)
    _beer('Tusker Lager'),
    _beer('Tusker Malt'),
    _beer('Tusker Lite'),
    _beer('White Cap Lager'),
    _beer('Balozi'),
    _beer('Pilsner'),
    _beer('Guinness'),
    _beer('Tusker Cider'),
    _beer('Snapp'),
    _beer('Smirnoff Ice'),
    _beer('KO'),

    # Spirits 750ml (mzinga)
    _spirit('Kibao Gin 750ml', 750),
    _spirit('Kibao Vodka 750ml', 750),
    _spirit('Chrome Gin 750ml', 750),
    _spirit('Konyagi 750ml', 750),
    _spirit('Kenya Cane 750ml', 750),
    _spirit('County Gin 750ml', 750),
    _spirit('Best Gin 750ml', 750),
    _spirit('Best Whisky 750ml', 750),
    _spirit("Hunter's Choice 750ml", 750),
    _spirit('Triple Ace Gin 750ml', 750),
    _spirit('Blue Moon Gin 750ml', 750),
    _spirit('Kane Extra 750ml', 750),
    _spirit('Captain Morgan 750ml', 750),
    _spirit("Gilbey's Gin 750ml", 750),
    _spirit('Smirnoff Vodka 750ml', 750),
    _spirit('Richot Brandy 750ml', 750),
    _spirit('Viceroy Brandy 750ml', 750),
    _spirit('V&A Whisky 750ml', 750),
    _spirit('Kingfisher Gin 750ml', 750),
    _spirit('General Meakins Gin 750ml', 750),
    _spirit('4th Street Wine 750ml', 750),
    _spirit('Caprice Wine 750ml', 750),
    _spirit("Drostdy-Hof Wine 750ml", 750),

    # Spirits 375ml (nusu)
    _spirit('Kibao Gin 375ml', 375),
    _spirit('Chrome Gin 375ml', 375),
    _spirit('Konyagi 375ml', 375),
    _spirit('Kenya Cane 375ml', 375),
    _spirit('Best Gin 375ml', 375),

    # Spirits 250ml (robo/quarter)
    _spirit('Kibao Gin 250ml', 250),
    _spirit('Chrome Gin 250ml', 250),
    _spirit('Konyagi 250ml', 250),
    _spirit('Kenya Cane 250ml', 250),
    _spirit('Best Gin 250ml', 250),
    _spirit('County Gin 250ml', 250),

    # Sodas / mixers
    _soda('Coca-Cola 300ml'),
    _soda('Fanta Orange 300ml'),
    _soda('Sprite 300ml'),
    _soda('Stoney Ginger Beer 300ml'),
    _soda('Schweppes Tonic 300ml'),
    _soda('Coca-Cola 500ml'),
    _soda('Delmonte Juice'),
    _soda('Mineral Water 500ml'),
    _soda('Predator Energy Drink'),
    _soda('Red Bull'),

    # Cigarettes
    _cig('SM Cigarettes'),
    _cig('Embassy Cigarettes'),
]

# Liquor store = same as bar minus kegs
LIQUOR_CATALOG = [item for item in BAR_CATALOG if not item.get('is_keg')]

# ── Kibanda catalog ───────────────────────────────────────────────────────────

KIBANDA_CATALOG = [
    # Greens — BUNCH mode
    _bunch('Sukuma Wiki / Kale'),
    _bunch('Spinach / Mchicha'),
    _bunch('Managu'),
    _bunch('Terere'),
    _bunch('Kunde'),
    _bunch('Mrende'),
    _bunch('Saga'),

    # Sack goods — BATCH mode
    _batch('Potatoes / Viazi', 'Gorogoro'),
    _batch('Beans / Maharagwe', 'Gorogoro'),
    _batch('Ndengu / Green Grams', 'Gorogoro'),
    _batch('Maize / Mahindi', 'Gorogoro'),
    _batch('Rice / Mchele', 'Gorogoro'),
    _batch('Flour / Unga', 'Gorogoro'),
    _batch('Sugar / Sukari', 'Gorogoro'),
    _batch('Carrots / Karoti', 'Bundle'),

    # Piece-count — PORTION mode
    _portion('Tomatoes / Nyanya', 'Pcs'),
    _portion('Onions / Vitunguu', 'Pcs'),
    _portion('Cabbage / Kabichi', 'Head'),
    _portion('Mangoes / Maembe', 'Pcs'),
    _portion('Avocado / Parachichi', 'Pcs'),
    _portion('Banana / Ndizi', 'Pcs'),
    _portion('Pawpaw / Papai', 'Pcs'),
    _portion('Chilli / Pilipili', 'Heap'),
    _portion('Coriander / Dhania', 'Bundle'),
    _portion('Garlic / Kitunguu Saumu', 'Pcs'),
    _portion('Lemon / Ndimu', 'Pcs'),
    _portion('Cucumber / Tango', 'Pcs'),
    _portion('Green Pepper / Pilipili Hoho', 'Pcs'),

    # Kg-sold
    _kg('Tomatoes per Kg'),
    _kg('Onions per Kg'),
    _kg('Carrots per Kg'),
    _kg('Sugar Loose per Kg'),
    _kg('Omena per Kg'),
]

# ── Butchery catalog ──────────────────────────────────────────────────────────

BUTCHERY_CATALOG = [
    _kg("Beef / Nyama ya Ng'ombe"),
    _kg('Goat / Mbuzi'),
    _kg('Mutton / Kondoo'),
    _kg('Matumbo / Tripe'),
    _kg('Liver / Ini'),
    _kg('Bones / Supu'),
    _kg('Pork / Nguruwe'),
    _pc('Chicken Kienyeji (Whole)'),
    _kg('Chicken Kienyeji (per Kg)'),
]

# ── Cereals catalog ───────────────────────────────────────────────────────────

CEREALS_CATALOG = [
    _batch('Beans / Maharagwe', 'Gorogoro'),
    _batch('Ndengu', 'Gorogoro'),
    _batch('Njahi', 'Gorogoro'),
    _batch('Rice / Mchele', 'Gorogoro'),
    _batch('Maize / Mahindi', 'Gorogoro'),
    _batch('Sorghum / Mtama', 'Gorogoro'),
    _batch('Flour / Unga wa Ngano', 'Gorogoro'),
    _kg('Beans / Maharagwe (per Kg)'),
    _kg('Rice / Mchele (per Kg)'),
    _kg('Maize Flour / Unga wa Mahindi (per Kg)'),
]

# ── Fish catalog ──────────────────────────────────────────────────────────────

FISH_CATALOG = [
    _pc('Tilapia (Small)'),
    _pc('Tilapia (Medium)'),
    _pc('Tilapia (Large)'),
    _batch('Omena (per Gorogoro)', 'Gorogoro'),
    _kg('Omena (per Kg)'),
    _kg('Fillet'),
    _kg('Nile Perch / Sangara'),
    _kg('Catfish / Kamongo'),
]

# ── Water refilling catalog ────────────────────────────────────────────────────

WATER_CATALOG = [
    _pc('Refill 20L'),
    _pc('Refill 10L'),
    _pc('Refill 5L'),
    _pc('Bottle + Water 20L'),
]

# ── Kitchen / Grill catalog ───────────────────────────────────────────────────
# Portion items (pre-counted, fixed price per piece/serving)
def _food(name, unit='Pcs', presets=None):
    return {
        'name': name, 'unit': unit, 'is_keg': False, 'is_produce': True,
        'produce_mode': 'PORTION',
        'presets': presets or [{'label': 'Kimoja', 'price': None, 'qty': 1.0}],
    }

# Kitchen batch items — she cooks a batch (e.g. a pot of chips from 2 debe ya viazi),
# sells by price point (ya 50 / ya 100) until the batch is done.
# No mandatory target. Tracks cost vs revenue for P&L only.
def _batch_food(name, unit='Batch', presets=None, khaki_presets=None):
    """
    presets: list of {label, price, qty, khaki_type}
    khaki_type: 'NONE' | 'SMALL' (1/4 khaki) | 'LARGE' (1/2 khaki)
    """
    default_presets = [
        {'label': 'Ya 50',  'price': 50,  'qty': 1.0, 'khaki_type': 'SMALL'},
        {'label': 'Ya 100', 'price': 100, 'qty': 1.0, 'khaki_type': 'LARGE'},
    ]
    return {
        'name': name, 'unit': unit, 'is_keg': False, 'is_produce': False,
        'is_kitchen_batch': True,
        'presets': presets or khaki_presets or default_presets,
    }

# Batch/revenue-envelope items (buy a whole animal/batch, sell by price point)
def _grill_batch(name, unit='Kg'):
    return {
        'name': name, 'unit': unit, 'is_keg': False, 'is_produce': True,
        'produce_mode': 'BUNCH', 'presets': [],
    }

KITCHEN_CATALOG = [
    # ── Kitchen Batch items — sold from a batch, P&L tracked ─────────────────
    _batch_food('Chips / Chipo', 'Batch', presets=[
        {'label': 'Ya 50',  'price': 50,  'qty': 1.0, 'khaki_type': 'SMALL'},
        {'label': 'Ya 100', 'price': 100, 'qty': 1.0, 'khaki_type': 'LARGE'},
        {'label': 'Ya 150', 'price': 150, 'qty': 1.0, 'khaki_type': 'LARGE'},
    ]),
    _batch_food('Bhajia / Viazi Karai', 'Batch', presets=[
        {'label': 'Ya 30',  'price': 30,  'qty': 1.0, 'khaki_type': 'SMALL'},
        {'label': 'Ya 50',  'price': 50,  'qty': 1.0, 'khaki_type': 'SMALL'},
        {'label': 'Ya 100', 'price': 100, 'qty': 1.0, 'khaki_type': 'LARGE'},
    ]),
    _batch_food('Mchuzi wa Nyama / Beef Stew', 'Pot', presets=[
        {'label': 'Portion', 'price': 100, 'qty': 1.0, 'khaki_type': 'NONE'},
        {'label': 'Ndogo',   'price': 150, 'qty': 1.0, 'khaki_type': 'NONE'},
        {'label': 'Kubwa',   'price': 200, 'qty': 1.0, 'khaki_type': 'NONE'},
    ]),
    _batch_food('Ugali', 'Pot', presets=[
        {'label': 'Moja',   'price': 30,  'qty': 1.0, 'khaki_type': 'NONE'},
        {'label': 'Mbili',  'price': 60,  'qty': 1.0, 'khaki_type': 'NONE'},
    ]),
    _batch_food('Wali / Rice', 'Pot', presets=[
        {'label': 'Moja',   'price': 50,  'qty': 1.0, 'khaki_type': 'NONE'},
        {'label': 'Kubwa',  'price': 100, 'qty': 1.0, 'khaki_type': 'NONE'},
    ]),
    _batch_food('Maharagwe / Beans', 'Pot', presets=[
        {'label': 'Portion', 'price': 50,  'qty': 1.0, 'khaki_type': 'NONE'},
        {'label': 'Kubwa',   'price': 100, 'qty': 1.0, 'khaki_type': 'NONE'},
    ]),
    _batch_food('Sukuma Wiki / Kales', 'Pot', presets=[
        {'label': 'Portion', 'price': 30,  'qty': 1.0, 'khaki_type': 'NONE'},
    ]),

    # ── Portion items (count known upfront) ───────────────────────────────────
    _food('Chicken Wing / Bawa', 'Pcs', [
        {'label': 'Kimoja',   'price': 70,  'qty': 1.0},
        {'label': 'Mbili',    'price': 130, 'qty': 2.0},
    ]),
    _food('Chicken Drumstick / Paja', 'Pcs', [
        {'label': 'Kimoja',   'price': 150, 'qty': 1.0},
    ]),
    _food('Chicken Thigh', 'Pcs', [
        {'label': 'Kimoja',   'price': 120, 'qty': 1.0},
    ]),
    _food('Chicken Breast / Kifua', 'Pcs', [
        {'label': 'Kimoja',   'price': 150, 'qty': 1.0},
    ]),
    _food('Smokie', 'Pcs', [
        {'label': 'Kimoja',   'price': 60,  'qty': 1.0},
        {'label': 'Mbili',    'price': 110, 'qty': 2.0},
    ]),
    _food('Samosa', 'Pcs', [
        {'label': 'Kimoja',   'price': 30,  'qty': 1.0},
        {'label': 'Tatu',     'price': 80,  'qty': 3.0},
    ]),
    _food('Sausage / Sosej', 'Pcs', [
        {'label': 'Kimoja',   'price': 50,  'qty': 1.0},
    ]),
    _food('Chips + Chicken', 'Set', [
        {'label': 'Regular',  'price': 200, 'qty': 1.0},
        {'label': 'Large',    'price': 280, 'qty': 1.0},
    ]),
    _food('Chips + Smokie', 'Set', [
        {'label': 'Regular',  'price': 150, 'qty': 1.0},
    ]),
    _food('Ugali + Nyama', 'Set', [
        {'label': 'Regular',  'price': 200, 'qty': 1.0},
    ]),
    _food('Chapati', 'Pcs', [
        {'label': 'Moja',     'price': 20,  'qty': 1.0},
        {'label': 'Mbili',    'price': 40,  'qty': 2.0},
    ]),

    # Grill — BUNCH/BATCH mode (buy whole animal/large cut, track revenue envelope)
    _grill_batch('Nyama Choma / Roasted Meat', 'Kg'),
    _grill_batch('Mutura / Blood Sausage',     'Kg'),
    _grill_batch('Mbuzi / Goat',               'Kg'),
    _grill_batch('Kuku Choma / Roasted Chicken', 'Pcs'),
    _grill_batch('Ng\'ombe Choma / Roasted Beef', 'Kg'),
    _grill_batch('Pork Ribs / Mbavu za Nguruwe',  'Kg'),
    _grill_batch('Matumbo / Tripe',               'Kg'),
]


# ── Profile registry ──────────────────────────────────────────────────────────

_DEFAULT_MODULES = {'keg': False, 'tabs': False, 'shifts': False, 'produce': False, 'kitchen': False}

PROFILES = {
    'bar': {
        'match': ['Bar / Pub (Local Joint)', 'Liquor Store / Bar'],
        'board': 'bar',
        'modules': {'keg': True, 'tabs': True, 'shifts': True, 'produce': False, 'kitchen': False},
        'catalog': BAR_CATALOG,
    },
    'liquor_store': {
        'match': ['Wines & Spirits (Liquor Store)'],
        'board': 'grid',
        'modules': dict(_DEFAULT_MODULES),
        'catalog': LIQUOR_CATALOG,
    },
    'club': {
        'match': ['Club / Lounge', 'Juice Bar'],
        'board': 'grid',
        'modules': {'keg': False, 'tabs': True, 'shifts': True, 'produce': False, 'kitchen': False},
        'catalog': LIQUOR_CATALOG,
    },
    'kibanda': {
        'match': ['Kibanda / Food Stall', 'Mama Mboga / Kiosk', 'Vegetable & Produce Stall'],
        'board': 'produce',
        'modules': {'keg': False, 'tabs': False, 'shifts': False, 'produce': True, 'kitchen': False},
        'catalog': KIBANDA_CATALOG,
    },
    'butchery': {
        'match': ['Butchery', 'Butchery & Abattoir', 'Nyama Choma Joint'],
        'board': 'grid',
        'modules': dict(_DEFAULT_MODULES),
        'catalog': BUTCHERY_CATALOG,
    },
    'cereals': {
        'match': ['Cereal & Grain Shop', 'Posho Mill'],
        'board': 'produce',
        'modules': {'keg': False, 'tabs': False, 'shifts': False, 'produce': True, 'kitchen': False},
        'catalog': CEREALS_CATALOG,
    },
    'fish': {
        'match': ['Fish Monger', 'Fish Farm / Aquaculture'],
        'board': 'grid',
        'modules': dict(_DEFAULT_MODULES),
        'catalog': FISH_CATALOG,
    },
    'water': {
        'match': ['Water Refilling / Dispensing Point'],
        'board': 'grid',
        'modules': dict(_DEFAULT_MODULES),
        'catalog': WATER_CATALOG,
    },
}

DEFAULT_PROFILE = {
    'type': '',
    'board': 'grid',
    'modules': dict(_DEFAULT_MODULES),
    'catalog': [],
}


def get_profile(business):
    """Return the matching profile for business.business_type.name, or DEFAULT_PROFILE.

    The 'kitchen' module flag is set from business.has_kitchen, not from the static profile,
    so an owner can enable the kitchen module independently of the business type.
    """
    if not business or not business.business_type:
        profile = DEFAULT_PROFILE.copy()
    else:
        name = business.business_type.name
        profile = DEFAULT_PROFILE.copy()
        for profile_type, p in PROFILES.items():
            if name in p.get('match', []):
                profile = {**p, 'type': profile_type}
                break

    # Overlay the live has_kitchen flag so templates always see the current state
    modules = dict(profile.get('modules', _DEFAULT_MODULES))
    modules['kitchen'] = bool(getattr(business, 'has_kitchen', False))
    profile = dict(profile)
    profile['modules'] = modules
    return profile
