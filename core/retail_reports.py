"""
UBA §7.5 (Sprint R4) — retail intelligence: dead stock, reorder ("Order ya
leo"), basket affinity, hour-of-day heatmap. All four are read-only report
functions; a dedicated retail_intelligence.html dashboard mirroring these
is NOT built this pass (documented deferral, same discipline as
retail_board.html) — core/retail_reports_views.py exposes them as JSON,
fully testable via direct HTTP today.
"""
import json
from collections import Counter
from datetime import timedelta
from itertools import combinations

from django.utils import timezone

from .models import Item, Receipt, Store, Transaction


def dead_stock_report(business, days=60):
    """Items with a positive on-hand balance and no Issue sale in `days`
    days, sorted by capital tied up (balance × cost_price) descending —
    R4-AC1. `transfer_available` is True per-row whenever this business has
    more than one active store (M2's StockTransfer is the actual action a
    UI would offer)."""
    cutoff = timezone.localdate() - timedelta(days=days)
    multi_store = Store.objects.filter(business=business, is_active=True).count() > 1

    rows = []
    for item in Item.objects.filter(business=business, is_keg=False, is_produce=False).select_related('store'):
        balance = item.current_balance()
        if balance <= 0:
            continue
        last_sale = Transaction.objects.filter(
            business=business, item=item, type='Issue',
        ).exclude(payment_method='void').order_by('-date').first()
        if last_sale and last_sale.date >= cutoff:
            continue
        capital_tied_up = float(balance) * float(item.cost_price or 0)
        rows.append({
            'item': item,
            'balance': float(balance),
            'capital_tied_up': round(capital_tied_up, 2),
            'last_sale_date': last_sale.date if last_sale else None,
            'transfer_available': multi_store,
        })
    rows.sort(key=lambda r: -r['capital_tied_up'])
    return rows


def reorder_today_list(business):
    """"Order ya leo" — items below reorder point, sorted by urgency
    (smallest current_balance() relative to reorder_point first), each with
    a pre-drafted WhatsApp/SMS message ready to send to a distributor.
    Most dukas order by phone — meet them there rather than forcing a
    portal, per the spec's own framing."""
    rows = []
    for item in Item.objects.filter(business=business, is_keg=False, is_produce=False):
        if not item.needs_reorder():
            continue
        qty = item.recommended_order_qty()
        if qty <= 0:
            continue
        draft = (
            f"Habari, tunahitaji {qty} {item.unit} ya {item.description}. "
            f"Tafadhali tujulishe bei na muda wa kuleta. Asante."
        )
        rows.append({
            'item': item, 'recommended_qty': qty,
            'current_balance': float(item.current_balance()),
            'lead_time_days': item.lead_time_days,
            'message_draft': draft,
        })
    rows.sort(key=lambda r: r['current_balance'])
    return rows


def basket_affinity_top10(business, days=30):
    """"Watu wanaonunua X pia hununua Y" — top-10 item-name pairs that
    co-occur on the same Receipt (Receipt.lines is already a per-sale
    basket snapshot, the natural unit for this — no new grouping mechanism
    needed). A light heuristic, per the spec's own instruction ("keep it to
    a top-10 pairs table; do not build a recommender")."""
    cutoff = timezone.localdate() - timedelta(days=days)
    pair_counts = Counter()
    receipts = Receipt.objects.filter(business=business, created_at__date__gte=cutoff)
    for receipt in receipts:
        lines = receipt.lines if isinstance(receipt.lines, list) else []
        names = sorted({str(line.get('name', line.get('description', ''))) for line in lines if line})
        names = [n for n in names if n]
        if len(names) < 2:
            continue
        for a, b in combinations(names, 2):
            pair_counts[(a, b)] += 1

    top = pair_counts.most_common(10)
    return [{'item_a': pair[0], 'item_b': pair[1], 'count': count} for pair, count in top]


def hour_of_day_heatmap(business, store=None, days=30):
    """Transaction count + revenue per hour-of-day (0-23), for staffing and
    closing-time decisions."""
    cutoff = timezone.localdate() - timedelta(days=days)
    txns = Transaction.objects.filter(
        business=business, type='Issue', date__gte=cutoff,
    ).exclude(payment_method='void').select_related('item')
    if store is not None:
        txns = txns.filter(item__store=store)

    buckets = {h: {'count': 0, 'revenue': 0.0} for h in range(24)}
    for t in txns:
        hour = timezone.localtime(t.created_at).hour if t.created_at else 0
        buckets[hour]['count'] += 1
        buckets[hour]['revenue'] += float(t.revenue())

    return [{'hour': h, 'count': buckets[h]['count'], 'revenue': round(buckets[h]['revenue'], 2)} for h in range(24)]
