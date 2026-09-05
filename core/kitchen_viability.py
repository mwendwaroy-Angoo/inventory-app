"""Kitchen Viability report (2026-08-21 live request).

Roy, in the voice of a real business owner: after a month of buying whole
raw materials (potato sacks, smokie packets, chicken pieces) at different
times, prepping and selling them the normal way — "can your system tell me
the revenue and profit from all these receipts, can it compare one sack's
profit against the previous ones, can I clearly see whether the kitchen is
collectively worth pursuing, can I truly know whether the kitchen can
survive on its own revenue without me touching my pocket?"

Investigated before writing any code (per this file's own Cause-and-Effect
protocol): revenue/cost/margin PER ITEM across a period was already
answered by analytics_dashboard()'s existing "Kitchen Performance" section
(kitchen_rows, grouped by item_id/preset_id). What was genuinely missing:
(1) per-INSTANCE history — KitchenBatch (a sack of potatoes) already
tracks its own cost_total/revenue_collected/profit/dates, but nothing
ever surfaced closed batches once they left the Kitchen Board's "open"
list; KitchenStockReceipt (a chicken-pieces delivery) already had this
per-instance data too, but only the last 10 CLOSED receipts, only on
Kitchen Board, never date-range filterable or shown on Analytics.
(2) a single "kitchen net profit after everything it actually costs to
run" figure — KitchenConsumableLog (khaki/sauce/oil) and BusinessExpense
(station='kitchen', 2026-08-09) were both already TRACKED but never
NETTED against kitchen revenue anywhere.

Deliberately pure functions (no view/HTTP concerns) so the report is
independently testable and reusable, matching the retail_reports.py /
payables.py / cycle_count.py convention already established in this app
for report logic. Every revenue/cost figure here goes through
Transaction.revenue()/.cost() — never a naive abs(qty)*cost_price formula
— per the hard lesson from the 2026-08-11 loss-formula bug (a keg pour's
qty is in ml, not per-keg units; the same class of bug is possible here
for any preset/batch-attributed kitchen sale).
"""
from decimal import Decimal

from django.db.models import Sum

from .models import BusinessExpense, Item, KitchenBatch, KitchenConsumableLog, Transaction


def kitchen_net_pnl(business, start_date, end_date):
    """One composite 'is the kitchen worth it / can it survive on its own'
    figure for [start_date, end_date] (inclusive, both dates).

    Deliberately does NOT subtract kitchen staff wages into net_profit —
    see kitchen_staff_cost_context() below for why that's surfaced
    separately as context, not baked into this hard number.
    """
    kitchen_sales = list(
        Transaction.objects.filter(
            business=business, type='Issue', item__store__is_kitchen=True,
            date__gte=start_date, date__lte=end_date,
        ).exclude(payment_method='void').select_related(
            'item', 'produce_bunch', 'kitchen_batch', 'preset',
        )
    )
    # Transaction.revenue()/.cost() both return plain float (or int 0),
    # never Decimal — sum against a float seed, not Decimal('0'), or a
    # single non-Decimal term raises TypeError.
    kitchen_revenue = sum((t.revenue() for t in kitchen_sales), 0.0)
    kitchen_cogs = sum((t.cost() for t in kitchen_sales), 0.0)
    gross_profit = kitchen_revenue - kitchen_cogs

    consumables_cost = float(KitchenConsumableLog.objects.filter(
        business=business, date__gte=start_date, date__lte=end_date,
    ).aggregate(c=Sum('total_cost'))['c'] or Decimal('0'))

    kitchen_expenses = float(BusinessExpense.objects.filter(
        business=business, station='kitchen',
        date__gte=start_date, date__lte=end_date,
    ).aggregate(c=Sum('amount'))['c'] or Decimal('0'))

    net_profit = gross_profit - consumables_cost - kitchen_expenses
    net_margin_pct = round(net_profit / kitchen_revenue * 100, 1) if kitchen_revenue > 0 else 0.0

    total_revenue = sum(
        (t.revenue() for t in Transaction.objects.filter(
            business=business, type='Issue',
            date__gte=start_date, date__lte=end_date,
        ).exclude(payment_method='void').select_related(
            'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
        )),
        0.0,
    )
    kitchen_share_pct = round(kitchen_revenue / total_revenue * 100, 1) if total_revenue > 0 else 0.0

    return {
        'revenue':           round(kitchen_revenue, 2),
        'cogs':              round(kitchen_cogs, 2),
        'gross_profit':      round(gross_profit, 2),
        'consumables_cost':  round(consumables_cost, 2),
        'kitchen_expenses':  round(kitchen_expenses, 2),
        'net_profit':        round(net_profit, 2),
        'net_margin_pct':    net_margin_pct,
        'kitchen_share_pct': kitchen_share_pct,
        'is_self_sufficient': net_profit >= 0,
    }


def kitchen_batch_history(business, start_date, end_date):
    """Every KitchenBatch (a sack of potatoes, a pot of stew — any
    is_kitchen_batch item) received within the window, newest first, with
    its own cost/revenue/profit — the literal "this sack vs the previous
    ones" comparison Roy asked for. Includes still-OPEN batches (their
    profit is simply whatever has sold so far)."""
    batches = list(
        KitchenBatch.objects.filter(
            business=business, received_on__gte=start_date, received_on__lte=end_date,
        ).select_related('item').order_by('-received_on', '-id')
    )
    return [
        {
            'id':            b.id,
            'item_name':     b.item.description,
            'cost_total':    float(b.cost_total),
            'revenue':       float(b.revenue_collected),
            'profit':        float(b.profit),
            'profit_pct':    b.profit_pct,
            'status':        b.status,
            'received_on':   b.received_on.isoformat(),
            'closed_on':     b.closed_on.isoformat() if b.closed_on else None,
        }
        for b in batches
    ]


def kitchen_receipt_history(business, start_date, end_date):
    """Every KitchenStockReceipt (a delivery of chicken pieces, smokie
    packets, etc. — any portion item received via the '🧾 Stock Receipt'
    tool) within the window, newest first, with its own cost/revenue/
    profit. Local import of the model to avoid a circular import between
    this module and kitchen_views.py (same lazy-import convention already
    used elsewhere in this app, e.g. petty_cash_views.py)."""
    from .models import Item, KitchenStockReceipt
    receipts = list(
        KitchenStockReceipt.objects.filter(
            business=business, received_on__gte=start_date, received_on__lte=end_date,
        ).select_related('business').prefetch_related('lines__item').order_by('-received_on', '-id')
    )
    # Batched once across EVERY receipt's lines in this whole date-range
    # report, instead of a `derived_batch_items.exists()` query per line
    # per receipt — a real N+1 over a period that can span up to a year of
    # receipts (2026-09-05 nav-speed audit, mirroring the identical fix in
    # kitchen_views.py's polled receipts-list endpoint).
    _all_item_ids = {l.item_id for r in receipts for l in r.lines.all()}
    _has_batch_items = set(
        Item.objects.filter(raw_material_source_id__in=_all_item_ids)
        .values_list('raw_material_source_id', flat=True).distinct()
    )
    result = []
    for r in receipts:
        line_items = list(r.lines.all())
        # 2026-08-21 live report (Roy): a receipt made entirely of raw-
        # material-source items (e.g. Raw Potatoes) always shows revenue=0/
        # profit=-100% for itself — structurally guaranteed, not a fact
        # about how the delivery performed (the item is never sold
        # directly, only drawn into a batch — see KitchenStockReceipt's
        # own raw_material_for mechanism in kitchen_views.py). Flagged so
        # the template can avoid showing a hard -100% that reads as a loss
        # when the real batches drawn from it may be strongly profitable.
        is_raw_material = bool(line_items) and all(
            l.item_id in _has_batch_items for l in line_items
        )
        # total_revenue() computed once and reused for profit/profit_pct
        # instead of reading the `.profit`/`.profit_pct` properties, each
        # of which independently re-runs the same Transaction aggregate
        # internally — tripling this genuinely-needed query per receipt
        # for no reason (2026-09-05 nav-speed audit).
        _cost = r.total_cost
        _revenue = r.total_revenue()
        _profit = _revenue - _cost
        _profit_pct = round(float(_profit) / float(_cost) * 100, 1) if _cost and _cost > 0 else None
        result.append({
            'id':            r.id,
            'supplier':      r.supplier,
            'invoice_no':    r.invoice_no,
            'items':         ', '.join(sorted({l.item.description for l in line_items})),
            'cost_total':    float(_cost),
            'revenue':       float(_revenue),
            'profit':        float(_profit),
            'profit_pct':    _profit_pct,
            'status':        r.status,
            'received_on':   r.received_on.isoformat(),
            'is_raw_material': is_raw_material,
        })
    return result


def kitchen_staff_cost_context(business):
    """Informational only — deliberately NOT subtracted into
    kitchen_net_pnl()'s net_profit. A staff member's RecurringExpense
    salary line has no station attribution (payroll is a whole-person
    cost, not cleanly divisible per counter for a cross-access staffer —
    the same reasoning this app already applies elsewhere, e.g. Business
    model field-bloat notes on why a clean per-station split was never
    built for wages). Surfaced as approximate monthly context so the
    owner can eyeball it alongside the hard net_profit number, not
    mistake one for including the other."""
    from .models import RecurringExpense

    lines = list(
        RecurringExpense.objects.filter(
            business=business, is_active=True,
            staff_profile__role='kitchen', staff_profile__user__is_active=True,
        ).select_related('staff_profile__user')
    )
    monthly_total = Decimal('0')
    for line in lines:
        if line.period == 'MONTHLY':
            monthly_total += line.amount
        elif line.period == 'QUARTERLY':
            monthly_total += line.amount / 3
        elif line.period == 'ANNUAL':
            monthly_total += line.amount / 12
    return {
        'staff_count':          len(lines),
        'approx_monthly_wages': round(float(monthly_total), 2),
    }
