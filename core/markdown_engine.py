"""
UBA §8.4 (Sprint A3) — aging & markdown engine, the boutique's real profit
lever. Reuses `ItemPriceHistory` (built in R2 for the margin guard's
"Sasisha bei" action) for the markdown record — one price-history model,
two producers (margin_guard, markdown), not a parallel mechanism.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Item, ItemPriceHistory, Transaction

MARKDOWN_LADDER = [
    (30, Decimal('10')), (60, Decimal('25')), (90, Decimal('40')),
]


def _bucket_and_markdown_pct(days):
    if days <= 30:
        return '0-30', Decimal('0')
    if days <= 60:
        return '31-60', Decimal('10')
    if days <= 90:
        return '61-90', Decimal('25')
    return '90+', Decimal('40')


def aging_and_markdown_report(business, store=None):
    """Per-item (or per-variant, since a variant IS an Item) days-since-
    last-sale, aging bucket, suggested markdown %, and the capital-recovery
    framing copy the spec calls for by name."""
    qs = Item.objects.filter(business=business, is_keg=False, is_produce=False, is_variant_parent=False)
    if store is not None:
        qs = qs.filter(store=store)

    today = timezone.localdate()
    rows = []
    for item in qs:
        balance = item.current_balance()
        if balance <= 0:
            continue
        last_sale = Transaction.objects.filter(
            business=business, item=item, type='Issue',
        ).exclude(payment_method='void').order_by('-date').first()
        days = (today - last_sale.date).days if last_sale else 9999
        bucket, pct = _bucket_and_markdown_pct(days)
        if pct <= 0:
            continue
        capital_tied_up = float(balance) * float(item.cost_price or 0)
        current_price = item.selling_price or Decimal('0')
        suggested_price = (current_price * (Decimal('1') - pct / Decimal('100'))).quantize(Decimal('1'))
        message = (
            f"{item.description} ({balance:g} {item.unit}) zimekaa siku {days}. "
            f"Umewekeza KES {capital_tied_up:,.0f}. Punguza hadi KES {suggested_price:,.0f} "
            f"urudishe pesa yako."
        )
        rows.append({
            'item': item, 'days_since_last_sale': days, 'bucket': bucket,
            'markdown_pct': float(pct), 'current_price': float(current_price),
            'suggested_price': float(suggested_price), 'capital_tied_up': round(capital_tied_up, 2),
            'message': message,
        })
    rows.sort(key=lambda r: -r['capital_tied_up'])
    return rows


def apply_markdown(item, new_price, applied_by):
    old_price = item.selling_price
    item.selling_price = new_price
    item.save(update_fields=['selling_price'])
    return ItemPriceHistory.objects.create(
        item=item, old_price=old_price, new_price=new_price, reason='markdown', changed_by=applied_by,
    )
