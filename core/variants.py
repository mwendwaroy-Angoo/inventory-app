"""
UBA §8.2 (Sprint A1) — variants (the boutique half). Recommended
implementation per the spec: parent/child Items, NOT a new ItemVariant
table. Rationale (the spec's own, confirmed correct against this
codebase's history): a separate ItemVariant FK on Transaction would require
auditing every balance reader, analytics query, receipt line, stock list,
reorder table, Quick Sell tile, debt line and shrinkage calculation — the
exact class of change that has burned this project repeatedly (see
CLAUDE.md's own regression-sweep war stories). Parent/child Items reuse
100% of existing machinery for free — each variant IS an Item.

The "variant matrix creator" UI screen (enter product name, tick sizes/
colours, bulk quantity grid) is NOT built this pass — documented deferral,
same discipline as retail_board.html. `create_variant_matrix()` here is the
backend function such a screen would call; core/variants_views.py exposes
it as one JSON endpoint, fully testable via direct HTTP today.
"""
import re
from decimal import Decimal

from .models import Item


def _sku_fragment(text):
    return re.sub(r'[^A-Z0-9]', '', text.upper())[:4] or 'X'


def create_variant_matrix(business, store, product_name, sizes, colours, base_price,
                           material_no_prefix=None, opening_qty=0):
    """Creates the parent (display-grouping-only, is_variant_parent=True,
    never sold directly) plus one child Item per size×colour combination
    (or per size alone, or per colour alone, if only one dimension is
    given), each with an auto SKU (e.g. 'SHRT-NVY-M') guaranteed unique via
    a numeric suffix fallback on collision."""
    sizes = sizes or ['']
    colours = colours or ['']
    prefix = material_no_prefix or _sku_fragment(product_name)

    parent = Item.objects.create(
        business=business, store=store, description=product_name,
        material_no=_unique_material_no(f'{prefix}-PARENT'),
        unit='pcs', selling_price=base_price, is_variant_parent=True,
        opening_bin_balance=0,
    )

    children = []
    for size in sizes:
        for colour in colours:
            label_parts = [p for p in (size, colour) if p]
            variant_label = ' / '.join(label_parts) if label_parts else product_name
            sku_parts = [prefix] + [_sku_fragment(p) for p in (colour, size) if p]
            material_no = _unique_material_no('-'.join(sku_parts))
            child = Item.objects.create(
                business=business, store=store,
                description=f'{product_name} ({variant_label})' if variant_label != product_name else product_name,
                material_no=material_no, unit='pcs', selling_price=base_price,
                parent=parent, variant_label=variant_label,
                variant_attrs={k: v for k, v in (('size', size), ('colour', colour)) if v},
                opening_bin_balance=opening_qty,
            )
            children.append(child)
    return parent, children


def _unique_material_no(base):
    candidate = base
    n = 1
    while Item.objects.filter(material_no=candidate).exists():
        n += 1
        candidate = f'{base}-{n}'
    return candidate
