"""
Shared day-level P&L computation — one calendar date, one business.

2026-08-19 live request (Roy): audited Analytics + the /daily/ page against
three direct questions — profit per product today, cumulative profit today,
today vs yesterday — and found none of them were actually answered anywhere.
`/daily/` (daily_sales()) tracked revenue per item but never computed cost
or profit at all; the daily summary email had the same gap plus several
independent bugs (voided sales counted, credit/confirmed revenue blended,
a keg pour's millilitre qty summed as if it were a bottle count, a kitchen-
batch item's constant qty=-1-per-sale placeholder shown as if it were a
real physical unit).

compute_day_financials() is the single place that answers "how did this
business do, in full P&L detail, on this one day" — reused by:
    - daily_sales() (the /daily/ page) — today's own detail view
    - send_daily_summary() (the cron/webhook email+SMS)
    - a today-vs-yesterday-vs-day-before comparison (call it 3 times)

Deliberately NOT used for period-level (7/30/90-day) analytics — that's
analytics_dashboard()'s own, already-correct, already-tested territory;
duplicating this for a multi-day range would just be a second, competing
implementation of the same numbers with its own drift risk. This module
is single-day only, by design.
"""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Transaction, BusinessExpense, Receipt


def compute_day_financials(business, target_date, is_kitchen_only=None):
    """Full day-level P&L rollup for one business on one calendar date.

    is_kitchen_only:
        None  -> whole business (every store) — used by the owner/manager
                 view and always by the cron email (which only ever goes
                 to the owner).
        True  -> kitchen stores only — a kitchen-only staffer's view.
        False -> non-kitchen stores only — a bar/general-only staffer's
                 view.
    Mirrors the exact station-scoping shape daily_sales() already used
    before this refactor, so a staff member's visibility is unchanged.

    Returns a dict — see the inline keys below. Every KES figure is a
    plain float, already rounded to 2dp.
    """
    txns_qs = (
        Transaction.objects
        .filter(business=business, type='Issue', date=target_date)
        .exclude(payment_method='void')
        .select_related('item', 'item__store', 'keg_barrel', 'produce_bunch',
                         'kitchen_batch', 'preset')
    )
    if is_kitchen_only is True:
        txns_qs = txns_qs.filter(item__store__is_kitchen=True)
    elif is_kitchen_only is False:
        txns_qs = txns_qs.filter(item__store__is_kitchen=False)

    txns = list(txns_qs.order_by('id'))

    cash_rev = mpesa_rev = credit_rev = bar_rev = kitchen_rev = 0.0
    total_cost = 0.0
    item_map = {}

    for txn in txns:
        rev = txn.revenue()
        cost = txn.cost()
        total_cost += cost
        pm = txn.payment_method or 'cash'

        if pm == 'mpesa':
            mpesa_rev += rev
        elif pm == 'credit':
            credit_rev += rev
        else:
            cash_rev += rev

        store = txn.item.store
        is_kitchen_item = bool(store and store.is_kitchen)
        if is_kitchen_item:
            kitchen_rev += rev
        else:
            bar_rev += rev

        iid = txn.item_id
        if iid not in item_map:
            # row_kind decides how the qty number is honestly labelled:
            #   'keg'/'bunch'   -> a serving/pour count, never a literal
            #                      physical unit (ml/envelope fraction)
            #   'batch'         -> KitchenBatch.record_sale() writes a
            #                      constant qty=-1 per sale regardless of
            #                      price point sold — this is a SALE COUNT,
            #                      never a physical packet/bucket count.
            #                      The real physical count (khaki_small_
            #                      used/khaki_large_used) lives on the
            #                      KitchenBatch itself, not per-transaction,
            #                      so it can't be safely attributed to "just
            #                      today" if a batch spans more than one day
            #                      — deliberately not attempted here.
            #   'plain'         -> abs(qty) is a real physical quantity in
            #                      the item's own configured unit (a whole
            #                      bottle = 1, a quarter-tot = 0.25, a half
            #                      chicken leg = 0.5, etc.)
            if txn.keg_barrel_id:
                row_kind = 'keg'
            elif txn.produce_bunch_id:
                row_kind = 'bunch'
            elif txn.kitchen_batch_id:
                row_kind = 'batch'
            else:
                row_kind = 'plain'
            item_map[iid] = {
                'name': txn.item.description,
                'unit': txn.item.unit,
                'is_kitchen': is_kitchen_item,
                'row_kind': row_kind,
                'qty': 0.0,
                'sale_count': 0,
                'revenue': 0.0,
                'cost': 0.0,
                'profit': 0.0,
                'cash': 0.0,
                'mpesa': 0.0,
                'credit': 0.0,
            }
        row = item_map[iid]
        row['sale_count'] += 1
        if row['row_kind'] in ('keg', 'bunch', 'batch'):
            row['qty'] += 1.0
        else:
            row['qty'] += float(abs(txn.qty or 0))
        row['revenue'] += rev
        row['cost'] += cost
        row['profit'] += (rev - cost)
        if pm == 'mpesa':
            row['mpesa'] += rev
        elif pm == 'credit':
            row['credit'] += rev
        else:
            row['cash'] += rev

    for row in item_map.values():
        for k in ('qty', 'revenue', 'cost', 'profit', 'cash', 'mpesa', 'credit'):
            row[k] = round(row[k], 2)

    confirmed_rev = cash_rev + mpesa_rev
    total_rev = confirmed_rev + credit_rev
    # Gross profit is recognised on ALL revenue (cash/mpesa/credit alike) —
    # matching analytics_dashboard()'s own established profit definition
    # (accrual, not cash-basis): a credit sale genuinely incurred its cost
    # and genuinely generated profit on paper the moment the goods left,
    # independent of whether the cash has arrived yet. confirmed_rev stays
    # a separate, cash-position concern, not a second competing profit
    # definition.
    gross_profit = total_rev - total_cost

    # ── Wastage (station-scoped, matching the sale rows above) ──
    wastage_qs = (
        Transaction.objects
        .filter(business=business, type='Wastage', date=target_date)
        .exclude(invoice_no='[ADJ-NOLOSS]')
        .select_related('item', 'item__store', 'keg_barrel', 'produce_bunch',
                         'kitchen_batch', 'preset')
    )
    if is_kitchen_only is True:
        wastage_qs = wastage_qs.filter(item__store__is_kitchen=True)
    elif is_kitchen_only is False:
        wastage_qs = wastage_qs.filter(item__store__is_kitchen=False)
    wastage_list = list(wastage_qs)
    wastage_value = round(sum(w.loss_value() for w in wastage_list), 2)

    # ── Void loss — goods served but payment cancelled/waived. Revenue is
    # already excluded from total_rev above (payment_method='void' excluded
    # from txns_qs); the COGS must still be deducted in net_profit, same
    # formula as analytics_dashboard's own net_profit. ──
    void_qs = (
        Transaction.objects
        .filter(business=business, type='Issue', payment_method='void', date=target_date)
        .select_related('item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset')
    )
    if is_kitchen_only is True:
        void_qs = void_qs.filter(item__store__is_kitchen=True)
    elif is_kitchen_only is False:
        void_qs = void_qs.filter(item__store__is_kitchen=False)
    void_loss = round(sum(t.cost() for t in void_qs), 2)

    # ── Owner consumption (drawings) ──
    owner_qs = (
        Transaction.objects
        .filter(business=business, type='OwnerConsumption', date=target_date)
        .select_related('item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset')
    )
    if is_kitchen_only is True:
        owner_qs = owner_qs.filter(item__store__is_kitchen=True)
    elif is_kitchen_only is False:
        owner_qs = owner_qs.filter(item__store__is_kitchen=False)
    owner_consumes = list(owner_qs)
    owner_drawings_cost = round(sum(t.loss_value() for t in owner_consumes), 2)

    # ── Today's expenses (station-scoped where the field allows) ──
    # A blank station (rent, recurring rules, whole-business costs) is
    # included in EITHER scoped view — it genuinely reduces the whole
    # business's profit, not one station's; only the OTHER station's own
    # specifically-tagged expense is excluded from a single-station view.
    expenses_qs = BusinessExpense.objects.filter(business=business, date=target_date)
    if is_kitchen_only is True:
        expenses_qs = expenses_qs.exclude(station='bar')
    elif is_kitchen_only is False:
        expenses_qs = expenses_qs.exclude(station='kitchen')
    expenses_total = float(expenses_qs.aggregate(total=Sum('amount'))['total'] or 0)

    net_profit = round(
        gross_profit - expenses_total - wastage_value - void_loss - owner_drawings_cost, 2,
    )

    receipt_count = (
        Receipt.objects
        .filter(business=business, created_at__date=target_date)
        .exclude(payment_method='statement')
        .count()
    )

    item_rows = sorted(item_map.values(), key=lambda x: -x['revenue'])

    return {
        'date': target_date,
        'txn_count': len(txns),
        'cash_rev': round(cash_rev, 2),
        'mpesa_rev': round(mpesa_rev, 2),
        'credit_rev': round(credit_rev, 2),
        'confirmed_rev': round(confirmed_rev, 2),
        'total_rev': round(total_rev, 2),
        'bar_rev': round(bar_rev, 2),
        'kitchen_rev': round(kitchen_rev, 2),
        'total_cost': round(total_cost, 2),
        'gross_profit': round(gross_profit, 2),
        'profit_margin': round(gross_profit / total_rev * 100, 1) if total_rev > 0 else 0.0,
        'expenses_total': round(expenses_total, 2),
        'wastage_value': wastage_value,
        'void_loss': void_loss,
        'owner_drawings_cost': owner_drawings_cost,
        'net_profit': net_profit,
        'item_rows': item_rows,
        'wastage_list': wastage_list,
        'owner_consumes': owner_consumes,
        'receipt_count': receipt_count,
    }


def compute_day_over_day(business, target_date, is_kitchen_only=None):
    """target_date's own compute_day_financials() plus the two days before
    it (yesterday, day-before-yesterday), and the plain percentage change
    vs yesterday for revenue/net profit — "how is the business performing
    today compared to yesterday or the day before" (2026-08-19 request)."""
    from datetime import timedelta

    today_data = compute_day_financials(business, target_date, is_kitchen_only)
    yesterday_data = compute_day_financials(business, target_date - timedelta(days=1), is_kitchen_only)
    day_before_data = compute_day_financials(business, target_date - timedelta(days=2), is_kitchen_only)

    def _pct_change(current, previous):
        # No baseline to compare against — a genuinely undefined "% change",
        # not a fabricated 100%. The caller shows "no profit yesterday to
        # compare" for this case rather than an invented figure.
        if previous == 0:
            return None
        return round((current - previous) / previous * 100, 1)

    return {
        'today': today_data,
        'yesterday': yesterday_data,
        'day_before': day_before_data,
        'revenue_change_pct': _pct_change(today_data['total_rev'], yesterday_data['total_rev']),
        'net_profit_change_pct': _pct_change(today_data['net_profit'], yesterday_data['net_profit']),
    }
