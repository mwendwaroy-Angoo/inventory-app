"""
Shared, pure-function classification engine for turning a raw supplier
price-list row (product name + price) into a catalog entry matching the
existing business_profiles.py schema. Single source of truth used by both
the one-time enrich_liquor_catalog management command and the reusable
per-business catalog-upload feature (core/catalog_views.py) — no Django
model/view dependency, so it's fast to test in isolation.

Category keyword vocabulary is intentionally a Python port of
BAR_CAT_CONFIG in templates/core/item_form.html — JS and Python can't
share a literal source across the two runtimes, so keep both in sync by
hand if either changes.
"""
import re
from decimal import Decimal, InvalidOperation

from core.business_profiles import _spirit, _beer, _soda, _cig


# ── Column detection ─────────────────────────────────────────────────────

def _looks_numeric(value):
    if value is None:
        return False
    try:
        Decimal(str(value).replace(',', '').strip())
        return True
    except (InvalidOperation, ValueError):
        return False


def _looks_text(value):
    if value is None:
        return False
    s = str(value).strip()
    return bool(s) and not _looks_numeric(s)


_PRICE_HEADER_KEYWORDS = ('price', 'cost', 'rate', 'ksh', 'kes', 'amount')


def detect_name_price_columns(rows, max_header_scan=10):
    """Given a 2D list of raw spreadsheet rows, find the header row and
    which columns hold the product name and the price. Scores each column
    by text-ratio vs numeric-ratio over the rows below the header, rather
    than assuming a fixed layout — so a re-labelled or re-ordered supplier
    sheet still parses correctly.

    Returns (header_row_idx, name_col_idx, price_col_idx), or (None, None,
    None) if no usable header/data could be found.
    """
    if not rows:
        return None, None, None

    scan_limit = min(max_header_scan, len(rows))
    best_header_idx = None
    best_score = -1
    for i in range(scan_limit):
        row = rows[i]
        non_empty = [c for c in row if c is not None and str(c).strip() != '']
        text_cells = [c for c in non_empty if _looks_text(c) and len(str(c).strip()) <= 40]
        score = len(text_cells)
        if len(non_empty) >= 2 and score >= 2 and score > best_score:
            best_score = score
            best_header_idx = i

    if best_header_idx is None:
        return None, None, None

    header = rows[best_header_idx]
    data_rows = rows[best_header_idx + 1:]
    if not data_rows:
        return best_header_idx, None, None

    n_cols = len(header)
    text_ratio = [0.0] * n_cols
    numeric_ratio = [0.0] * n_cols
    avg_len = [0.0] * n_cols

    for col in range(n_cols):
        values = [r[col] for r in data_rows if col < len(r)]
        values = [v for v in values if v is not None and str(v).strip() != '']
        if not values:
            continue
        text_count = sum(1 for v in values if _looks_text(v))
        numeric_count = sum(1 for v in values if _looks_numeric(v))
        text_ratio[col] = text_count / len(values)
        numeric_ratio[col] = numeric_count / len(values)
        lengths = [len(str(v).strip()) for v in values if _looks_text(v)]
        avg_len[col] = sum(lengths) / len(lengths) if lengths else 0.0

    # Name column: highest text ratio among columns with a meaningful
    # average string length (favors descriptive names over short codes).
    name_candidates = [
        (text_ratio[c], c) for c in range(n_cols) if avg_len[c] > 5
    ]
    name_col = max(name_candidates)[1] if name_candidates else None

    # Price column: highest numeric ratio; prefer a header with a
    # price-like keyword, else the numeric column bordering the name column.
    numeric_candidates = [c for c in range(n_cols) if numeric_ratio[c] > 0.5]
    price_col = None
    if numeric_candidates:
        header_matches = [
            c for c in numeric_candidates
            if c < len(header) and header[c] and
            any(kw in str(header[c]).lower() for kw in _PRICE_HEADER_KEYWORDS)
        ]
        if header_matches:
            price_col = header_matches[0]
        elif name_col is not None:
            bordering = sorted(numeric_candidates, key=lambda c: abs(c - name_col))
            price_col = bordering[0]
        else:
            price_col = max(numeric_candidates, key=lambda c: numeric_ratio[c])

    return best_header_idx, name_col, price_col


# ── Volume extraction ────────────────────────────────────────────────────

_VOLUME_PATTERNS = [
    # 3/4 must be checked before the generic "X/4" rule below, or "3/4"
    # would match the /4 pattern first and be wrongly read as a quarter.
    (re.compile(r'3\s*/\s*4\b'), lambda m: 500),
    (re.compile(r'(\d+)\s*/\s*4\b'), lambda m: 250),
    (re.compile(r'(\d+)\s*/\s*2\b'), lambda m: 375),
    (re.compile(r'(\d+(?:\.\d+)?)\s*ML\b', re.I), lambda m: round(float(m.group(1)))),
    (re.compile(r'\b(\d{3})M\b'), lambda m: int(m.group(1))),  # typo tolerance: "750M"
    (re.compile(r'(\d+(?:\.\d+)?)\s*CL\b', re.I), lambda m: round(float(m.group(1)) * 10)),
    (re.compile(r'(\d+(?:\.\d+)?)\s*L(?:T|TR)?\b', re.I), lambda m: round(float(m.group(1)) * 1000)),
    (re.compile(r'\bLITRE\b', re.I), lambda m: 1000),
]


def extract_volume_ml(raw_name):
    """Best-effort bottle/can volume extraction in millilitres from a messy
    supplier product name. Returns None (never a guess) if nothing matches."""
    if not raw_name:
        return None
    # Strip parenthetical distributor tags first, e.g. "700ML(BMC)" -> "700ML".
    name = re.sub(r'\([^)]*\)', '', str(raw_name)).strip()
    for pattern, extractor in _VOLUME_PATTERNS:
        m = pattern.search(name)
        if m:
            try:
                return extractor(m)
            except (ValueError, TypeError):
                continue
    return None


# ── Category classification (ported from BAR_CAT_CONFIG in item_form.html) ──

_CATEGORY_KEYWORDS = {
    'spirit': [
        'spirit', 'spirits', 'liquor', 'alcohol', 'gin', 'vodka', 'rum', 'bourbon',
        'whiskey', 'whisky', 'tequila', 'brandy', 'cognac', 'schnapps', 'jenever',
        'absinthe', 'mezcal',
    ],
    'liqueur': ['liqueur', 'baileys', 'amarula', 'kahlua'],
    'wine': ['wine', 'champagne', 'sparkling', 'prosecco'],
    'beer': ['beer'],
    'cider': ['cider'],
    'cigarette': ['cigarette', 'cigarettes'],
    'non_alcoholic': [
        'non alcoholic', 'non-alcoholic', 'punch', 'brees', 'alvaro', 'malta',
        'sting', 'novida', 'fruit drink', 'mocktail', 'juice drink', 'ceres',
        'minute maid', 'delmonte', 'tropical',
    ],
    'energy_drink': [
        'energy drink', 'energy', 'redbull', 'red bull', 'monster', 'power horse',
        'burn', 'adrenaline', 'kabisa',
    ],
    'soft_drink': [
        'soda', 'soft drink', 'coke', 'cola', 'fanta', 'sprite', 'stoney', 'tonic',
        'ginger beer', 'lemonade', 'lemon soda', 'lemon', 'ribena', 'juice',
        'water', 'squash',
    ],
}

# Checked in this order — spirit/liqueur/wine/beer/cider/cigarette first
# (specific), general drink categories last (broad keywords like "juice"
# would otherwise shadow more specific matches).
_CATEGORY_ORDER = [
    'spirit', 'liqueur', 'wine', 'beer', 'cider', 'cigarette',
    'non_alcoholic', 'energy_drink', 'soft_drink',
]


def _keyword_pattern(keyword):
    # Word-boundary match, not a naive substring check — "gin" must not
    # match inside "original", nor "rum" inside "forum".
    return re.compile(r'\b' + re.escape(keyword) + r'\b', re.I)


_CATEGORY_PATTERNS = {
    category: [_keyword_pattern(kw) for kw in keywords]
    for category, keywords in _CATEGORY_KEYWORDS.items()
}


def classify_category(raw_name):
    """Returns a category slug from _CATEGORY_ORDER, or 'other' if nothing
    matches. Never raises."""
    if not raw_name:
        return 'other'
    name = str(raw_name)
    for category in _CATEGORY_ORDER:
        for pattern in _CATEGORY_PATTERNS[category]:
            if pattern.search(name):
                return category
    return 'other'


# ── Reorder-level heuristic ──────────────────────────────────────────────
# Cheap, high-turnover local-joint brands (quarters like Dallas, Blue Ice,
# Chrome) sell in much higher volume than expensive slow-moving premium
# spirits — bigger reorder buffers for cheaper tiers.
_REORDER_TIERS = [
    (300, 12, 24),
    (800, 6, 12),
    (2000, 3, 6),
    (5000, 2, 3),
]
_REORDER_DEFAULT = (1, 2)  # > 5000


def infer_reorder_defaults(cost_price):
    try:
        price = float(cost_price)
    except (TypeError, ValueError):
        return _REORDER_DEFAULT
    for ceiling, level, qty in _REORDER_TIERS:
        if price <= ceiling:
            return level, qty
    return _REORDER_DEFAULT


# ── Row classification ───────────────────────────────────────────────────

def classify_row(raw_name, raw_price):
    """Combines volume/category/reorder inference into one catalog entry,
    schema-identical to the hand-curated BAR_CATALOG (built via the same
    _spirit()/_beer()/_soda()/_cig() helpers). Returns None for an empty
    name or a non-positive/unparseable price — skipped, never guessed."""
    name = (str(raw_name).strip() if raw_name is not None else '')
    if not name:
        return None
    try:
        price = Decimal(str(raw_price).replace(',', '').strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if price <= 0:
        return None

    volume_ml = extract_volume_ml(name)
    category = classify_category(name)
    reorder_level, reorder_qty = infer_reorder_defaults(price)

    if category in ('spirit', 'liqueur', 'wine'):
        entry = _spirit(name, volume_ml or 750)
    elif category == 'beer':
        entry = _beer(name)
        if volume_ml:
            entry['volume_ml'] = volume_ml
    elif category == 'cigarette':
        entry = _cig(name)
    else:
        entry = _soda(name)
        if volume_ml:
            entry['volume_ml'] = volume_ml
        if 'CAN' in name.upper():
            entry['unit'] = 'Can'

    entry['category'] = category
    entry['cost_price'] = float(price)
    entry['default_reorder_level'] = reorder_level
    entry['default_reorder_quantity'] = reorder_qty
    entry['raw_name'] = name
    return entry
