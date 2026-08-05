"""
UBA §7.4 (Sprint R3) — cycle counting (ABC) + retail shrinkage. A full stock
take is impossible for a retail duka; a partial one, done daily on a
rotating subset, is easy.

Scope of this pass (documented deferral, same discipline as every prior UBA
sprint): the "weighted across ALL shifts since the item's last count" half
of the spec's attribution requirement is NOT built here — `record_count_line()`
reuses `shift_views.attribute_variance_shift()` as-is, which attributes to
ONE shift (the current shift if it has activity on the item, else the most
recent prior shift) rather than splitting a variance proportionally across
every shift spanning the whole since-last-count window. That single-shift
attribution is already real, tested, and correctly avoids blaming whoever
happens to be counting — a full multi-shift weighted split is a genuinely
separate, more elaborate mechanism, flagged here for a future pass rather
than rushed.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Item, StockCountLine, StockCountSession, Transaction


def classify_abc_all(business):
    """Rank items by 90-day revenue: top 80% of value = A (count weekly),
    next 15% = B (monthly), rest = C (quarterly). Items with zero 90-day
    revenue keep whatever class they last had (never force-reclassified to
    C just for being dormant this window — that's what R4's dead-stock
    report is for, a separate concern). Returns the number of items
    (re)classified."""
    since = timezone.localdate() - timedelta(days=90)
    items = list(Item.objects.filter(business=business, is_keg=False, is_produce=False))
    revenues = []
    for item in items:
        txns = Transaction.objects.filter(
            business=business, item=item, type='Issue', date__gte=since,
        ).exclude(payment_method='void')
        rev = sum(float(t.revenue()) for t in txns)
        revenues.append((item, rev))

    revenues.sort(key=lambda pair: pair[1], reverse=True)
    total_revenue = sum(r for _, r in revenues) or 1.0

    cumulative = 0.0
    updated = 0
    for item, rev in revenues:
        if rev <= 0:
            continue
        # Classify on where this item STARTS in the cumulative curve, not
        # where it ends — a single dominant item (e.g. 96% of all revenue
        # alone) is still class A, even though adding it pushes the running
        # total past every threshold; it's the item building up to that
        # top 80/95% mass, not one sitting past it.
        pct_before = cumulative / total_revenue
        cumulative += rev
        if pct_before < 0.80:
            new_class = 'A'
        elif pct_before < 0.95:
            new_class = 'B'
        else:
            new_class = 'C'
        if item.abc_class != new_class:
            item.abc_class = new_class
            item.save(update_fields=['abc_class'])
            updated += 1
    return updated


def _due_for_count(item, today):
    """A's counted weekly, B's monthly, C's quarterly — "due" means no
    StockCountLine with a real counted_qty exists within that window."""
    interval_days = {'A': 7, 'B': 30, 'C': 90}.get(item.abc_class, 30)
    cutoff = today - timedelta(days=interval_days)
    return not StockCountLine.objects.filter(
        item=item, counted_at__gte=cutoff, counted_qty__isnull=False,
    ).exists()


def select_items_for_cycle_count(business, store=None, limit=10):
    """Today's rotating subset — "today's N items", not a Sunday-closing
    job. High-risk items are force-included regardless of ABC due-ness or
    class."""
    qs = Item.objects.filter(business=business, is_keg=False, is_produce=False)
    if store is not None:
        qs = qs.filter(store=store)
    today = timezone.localdate()

    high_risk = list(qs.filter(is_high_risk=True))
    due = [i for i in qs.exclude(is_high_risk=True) if _due_for_count(i, today)]
    due.sort(key=lambda i: {'A': 0, 'B': 1, 'C': 2}.get(i.abc_class, 1))
    return (high_risk + due)[:limit] if limit else (high_risk + due)


def start_cycle_count_session(business, store, started_by, kind=StockCountSession.KIND_CYCLE,
                               item_ids=None, limit=10, shift=None):
    """Creates the session and one StockCountLine per item, book_qty
    snapshotted NOW (never recomputed later, even if more sales happen
    before the line is actually counted)."""
    if item_ids:
        items = list(Item.objects.filter(business=business, id__in=item_ids))
    else:
        items = select_items_for_cycle_count(business, store=store, limit=limit)

    session = StockCountSession.objects.create(
        business=business, store=store, kind=kind, started_by=started_by, shift=shift,
        scope_note=f"{kind} — {len(items)} items",
    )
    for item in items:
        StockCountLine.objects.create(session=session, item=item, book_qty=item.current_balance())
    return session


def record_count_line(line_id, business, counted_qty, counted_by, reason='', current_shift=None):
    """Records the physical count for one line, computes variance, and
    attributes it (single-shift, see module docstring). Also stamps
    Item.balance_confirmed_at (R1 §7.2 — a cycle count IS a real physical
    count, same as Rekebisha)."""
    from .shift_views import attribute_variance_shift

    line = StockCountLine.objects.select_related('item', 'session').get(
        id=line_id, session__business=business,
    )
    counted_qty = Decimal(str(counted_qty))
    variance_qty = counted_qty - line.book_qty
    variance_kes = variance_qty * (line.item.cost_price or Decimal('0'))

    line.counted_qty = counted_qty
    line.variance_qty = variance_qty
    line.variance_kes = variance_kes
    line.reason = reason
    line.counted_by = counted_by
    line.counted_at = timezone.now()
    if variance_qty != 0:
        line.attributed_shift = attribute_variance_shift(business, current_shift, item=line.item)
    line.save(update_fields=[
        'counted_qty', 'variance_qty', 'variance_kes', 'reason', 'counted_by',
        'counted_at', 'attributed_shift',
    ])

    line.item.balance_confirmed_at = timezone.now()
    line.item.save(update_fields=['balance_confirmed_at'])
    return line


def close_session(session_id, business):
    session = StockCountSession.objects.get(id=session_id, business=business)
    session.status = StockCountSession.STATUS_CLOSED
    session.closed_at = timezone.now()
    session.save(update_fields=['status', 'closed_at'])
    return session
