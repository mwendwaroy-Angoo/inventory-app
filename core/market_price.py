"""
UBA §7.2 (Sprint R1) — the cross-tenant product dictionary and its market
price benchmark. Treat the privacy rules here as seriously as the M-Pesa/CBK
boundary elsewhere in this app (see CLAUDE.md's M-Pesa / Payments
Architecture section for that same seriousness applied to money custody):

- Reading GlobalProduct (names/barcodes/pack sizes) is always allowed —
  this is shared, anonymised reference data, not any one business's figures.
- CONTRIBUTING a name/barcode requires Business.contribute_market_data
  (default True, opt-out any time).
- MarketPriceIndex (the price benchmark) is opt-IN only
  (Business.contribute_price_data, default False) on BOTH sides: a business
  that hasn't opted in neither feeds the aggregate nor is shown it. Below
  county sample_size 5, nothing is ever returned — no "based on 2 shops".
"""
import statistics
from decimal import Decimal

from .models import GlobalProduct, Item, MarketPriceIndex

MIN_SAMPLE_SIZE = 5


def lookup_global_product(barcode):
    """Always allowed — this is shared reference data, not a single
    business's figures. Returns None on a miss (barcode never seen before)."""
    barcode = (barcode or '').strip()
    if not barcode:
        return None
    return GlobalProduct.objects.filter(barcode=barcode).first()


def record_barcode_contribution(business, barcode, name, brand='', pack_size='', unit='', category=''):
    """Called when a business adds an item by barcode (fast onboarding,
    R1-AC1). Looks up or creates the GlobalProduct row. Never touches
    confirm_count/creates a new row unless business.contribute_market_data
    is True — an opted-out business can still SEE existing entries (the
    lookup above is unconditional) but contributes nothing of its own,
    matching "shared by default, opt-out available" exactly.

    confirm_count is only incremented the first time THIS business
    contributes to THIS barcode (checked via whether it already has an Item
    with this barcode) — a plain PositiveIntegerField per the spec's own
    schema, not a M2M of confirming businesses, so this is the best
    available guard against one business inflating its own confirmation by
    re-adding/re-editing the same item repeatedly."""
    barcode = (barcode or '').strip()
    if not barcode:
        return None

    existing = GlobalProduct.objects.filter(barcode=barcode).first()

    if not getattr(business, 'contribute_market_data', True):
        return existing

    already_confirmed = Item.objects.filter(business=business, barcode=barcode).exists()

    if existing:
        if not already_confirmed:
            existing.confirm_count += 1
            if existing.confirm_count >= 3:
                existing.is_verified = True
            existing.save(update_fields=['confirm_count', 'is_verified'])
        return existing

    return GlobalProduct.objects.create(
        barcode=barcode, name=name, brand=brand, pack_size=pack_size,
        unit=unit, category=category, contributed_by=business, confirm_count=1,
    )


def recompute_market_price_index(global_product, county=None):
    """Recompute the median cost/price benchmark for one GlobalProduct in one
    county (county=None means national). Only considers Items from
    businesses with contribute_price_data=True — an opted-out business's
    prices never enter the aggregate, in either direction (contribute or
    benefit). Below MIN_SAMPLE_SIZE, deletes any existing row for this pair
    rather than showing a stale or thin benchmark — "return nothing at all
    below 5", not "show what we have anyway"."""
    items = Item.objects.filter(
        barcode=global_product.barcode, business__contribute_price_data=True,
    ).select_related('business')
    if county is not None:
        items = items.filter(business__county=county)

    costs = [i.cost_price for i in items if i.cost_price]
    prices = [i.selling_price for i in items if i.selling_price]
    sample_size = items.count()

    if sample_size < MIN_SAMPLE_SIZE:
        MarketPriceIndex.objects.filter(global_product=global_product, county=county).delete()
        return None

    median_cost = Decimal(str(statistics.median(costs))) if costs else None
    median_price = Decimal(str(statistics.median(prices))) if prices else None

    index, _created = MarketPriceIndex.objects.update_or_create(
        global_product=global_product, county=county,
        defaults={
            'median_cost': median_cost, 'median_price': median_price,
            'sample_size': sample_size,
        },
    )
    return index


def get_market_price_benchmark(business, global_product, county=None):
    """R1-AC2: a business with contribute_price_data=False neither writes to
    the aggregate (see recompute_market_price_index's own filter) nor sees a
    benchmark — this is the one read gate. Returns None below
    MIN_SAMPLE_SIZE too, as a defensive second check in case a row somehow
    lingers with a stale small sample_size."""
    if not getattr(business, 'contribute_price_data', False):
        return None
    index = MarketPriceIndex.objects.filter(global_product=global_product, county=county).first()
    if not index or index.sample_size < MIN_SAMPLE_SIZE:
        return None
    return index
