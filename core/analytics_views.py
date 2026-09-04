"""
Analytics views — advanced reports and chart data.

Available views:
    /analytics/                  — Full analytics dashboard (HTML)
    /analytics/heatmap/          — County-level sales heatmap (Leaflet choropleth)
    /api/v1/analytics/trends/    — JSON: daily revenue/profit/orders for charts
    /analytics/expenses/         — List business expenses
    /analytics/expenses/add/     — Add a business expense
    /analytics/expenses/<id>/edit/ — Edit a business expense
    /analytics/expenses/<id>/delete/ — Delete a business expense
"""

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q, F, Avg
from django.db.models.functions import TruncDate, TruncMonth
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import Item, Transaction, Order, Payment, BusinessExpense, CapitalInvestment, BusinessTypeRequirement, BusinessCompliance, Customer, County, RevenueTarget, Store, ProduceBunch, KegBarrel, BarCupLog, BarTab, Receipt
from core.forms import BusinessExpenseForm, CapitalInvestmentForm
from core.views import get_user_profile, owner_required, owner_or_manager_required, _station_scope


def _units(t):
    """Unit count for a transaction.

    Bunch-mode greens: qty is a fractional bundle deduction — count as 1 customer portion.
    Keg barrel pours: qty is stored in ml (e.g. -500 for a pint) — count as 1 serving.
    Regular items: return abs(qty).
    """
    if getattr(t, 'produce_bunch_id', None) is not None:
        return 1.0
    if getattr(t, 'keg_barrel_id', None) is not None:
        return 1.0
    return float(abs(t.qty or 0))


@login_required
@owner_or_manager_required
def analytics_dashboard(request):
    """Rich analytics dashboard with trends, comparisons, and insights."""
    user_profile = request.user.userprofile
    business = user_profile.business
    today = timezone.localdate()

    # ── Period selection ──
    period = request.GET.get('period', '30')
    try:
        days = int(period)
    except ValueError:
        days = 30
    days = min(days, 365)

    start_date = today - timedelta(days=days - 1)
    prev_start = start_date - timedelta(days=days)
    prev_end = start_date - timedelta(days=1)

    # UBA §M0-6 — additive analytics section registry (core/analytics_sections.py).
    # analytics.html does not read this key yet; computed here so it's available
    # once a future sprint rewires the template to consume it. Never lets a
    # section-builder error break the rest of the analytics page.
    try:
        from .business_profiles import get_profile as _get_profile_sections
        from .analytics_sections import build_sections as _build_uba_sections
        _capability = _get_profile_sections(business).get('capability')
        uba_analytics_sections = _build_uba_sections(business, _capability, start_date, today)
    except Exception:
        uba_analytics_sections = []

    # Optional product filter for per-product analytics/forecast
    product_id = request.GET.get('product')
    items = list(Item.objects.filter(business=business).order_by('description').values('id', 'description'))
    selected_product = None
    if product_id:
        try:
            selected_product = int(product_id)
        except Exception:
            selected_product = None

    # ── Current period sales ──
    # 2026-08-19 live report (Roy, correlated with a real health-check-timeout
    # 502): the period filter "not responding" for anything longer than 30
    # days. Root cause — select_related here was missing kitchen_batch and
    # preset, both of which Transaction.cost()'s proportional keg/bunch/
    # batch/preset-aware logic (_stock_movement_cost()) lazily fetches for
    # any kitchen-batch or preset-attributed sale (e.g. every Chipo sale,
    # every Kuku-cut sale). Since current_sales/prev_sales are iterated many
    # times over this function (revenue/cost/profit rollups, daily trends,
    # day-of-week, top products, margin alerts), each MISSING relation was
    # one extra synchronous DB round-trip per affected transaction on first
    # access — genuine N+1, growing directly with period length (more days
    # = more transactions), matching the report exactly.
    current_sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=start_date, date__lte=today,
    ).exclude(payment_method='void').select_related(
        'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
    )

    prev_sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=prev_start, date__lte=prev_end,
    ).exclude(payment_method='void').select_related(
        'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
    )

    if selected_product:
        current_sales = current_sales.filter(item_id=selected_product)
        prev_sales = prev_sales.filter(item_id=selected_product)

    # ── Revenue / Cost / Profit ──
    cur_revenue = sum(t.revenue() for t in current_sales)
    cur_cost = sum(t.cost() for t in current_sales)
    cur_profit = cur_revenue - cur_cost
    cur_units = sum(_units(t) for t in current_sales)
    cur_txn_count = current_sales.count()

    prev_revenue = sum(t.revenue() for t in prev_sales)
    prev_profit = prev_revenue - sum(t.cost() for t in prev_sales)
    prev_units = sum(_units(t) for t in prev_sales)

    def pct_change(current, previous):
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round((current - previous) / previous * 100, 1)

    def format_pct_change(value):
        """Return a human-readable change string.
        Values >= 100% display as a multiplier (e.g. 2.0×) since
        '100%+ vs prev' is confusing for non-technical users.
        """
        if value >= 100:
            multiplier = round((value / 100) + 1, 1)
            return f"{multiplier}×"
        elif value <= -100:
            return "~100%"
        else:
            return f"{abs(round(value, 1))}%"

    revenue_change = pct_change(cur_revenue, prev_revenue)
    profit_change = pct_change(cur_profit, prev_profit)
    units_change = pct_change(cur_units, prev_units)

    # ── Daily trends ──
    daily_data = defaultdict(lambda: {'revenue': 0, 'profit': 0, 'units': 0, 'txns': 0})
    for t in current_sales:
        d = str(t.date)
        daily_data[d]['revenue'] += t.revenue()
        daily_data[d]['profit'] += t.profit()
        daily_data[d]['units'] += _units(t)
        daily_data[d]['txns'] += 1

    all_dates = []
    d = start_date
    while d <= today:
        all_dates.append(str(d))
        d += timedelta(days=1)

    chart_labels = all_dates
    chart_revenue = [round(daily_data[d]['revenue'], 2) for d in all_dates]
    chart_profit = [round(daily_data[d]['profit'], 2) for d in all_dates]
    chart_units = [float(daily_data[d]['units']) for d in all_dates]

    # ── Market Day Intelligence ──
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    dow_revenue = defaultdict(float)
    dow_occurrences = defaultdict(int)

    # Count how many of each weekday fall in the current period
    d = start_date
    while d <= today:
        dow_occurrences[d.weekday()] += 1
        d += timedelta(days=1)

    # Sum revenue per weekday from actual sales
    for t in current_sales:
        dow = t.date.weekday()
        dow_revenue[dow] += t.revenue()

    market_day_totals = [round(dow_revenue[i], 2) for i in range(7)]
    market_day_avgs = [
        round(dow_revenue[i] / dow_occurrences[i], 2) if dow_occurrences[i] > 0 else 0.0
        for i in range(7)
    ]
    best_dow_idx = max(range(7), key=lambda i: dow_revenue[i]) if any(dow_revenue.values()) else 0
    best_market_day = day_names[best_dow_idx]
    best_market_day_avg = market_day_avgs[best_dow_idx]

    # ── Top 10 products by revenue ──
    product_stats = defaultdict(lambda: {
        'name': '', 'units': 0, 'revenue': 0.0, 'profit': 0.0
    })
    for t in current_sales:
        key = t.item.id
        product_stats[key]['name'] = t.item.description
        product_stats[key]['units'] += _units(t)
        product_stats[key]['revenue'] += t.revenue()
        product_stats[key]['profit'] += t.profit()

    top_products = sorted(
        product_stats.values(), key=lambda x: x['revenue'], reverse=True
    )[:10]
    # Round the accumulated float here — 2026-08-11 live report (Roy):
    # "Blue Ice 32.69999999999997 units" — ordinary binary-float summation
    # noise from adding many small fractional preset amounts (quarter/half/
    # three-quarter bottle sales) never got rounded for display, unlike
    # every other accumulated-units figure on this page. 2 decimals (not 1)
    # to keep a genuine quarter-bottle (0.25) showing exactly, not rounded
    # away to 0.3.
    for p in top_products:
        p['units'] = round(p['units'], 2)
        p['revenue'] = round(p['revenue'], 2)
        p['profit'] = round(p['profit'], 2)

    top_product_labels = [p['name'][:20] for p in top_products]
    top_product_revenue = [round(p['revenue'], 2) for p in top_products]

    # ── Margin Erosion Alerts ──
    # Build per-product revenue and cost for current and previous periods
    cur_product_data  = defaultdict(lambda: {'name': '', 'revenue': 0.0, 'cost': 0.0})
    prev_product_data = defaultdict(lambda: {'revenue': 0.0, 'cost': 0.0})

    for t in current_sales:
        cur_product_data[t.item_id]['name']    = t.item.description
        cur_product_data[t.item_id]['revenue'] += t.revenue()
        cur_product_data[t.item_id]['cost']    += t.cost()

    for t in prev_sales:
        prev_product_data[t.item_id]['revenue'] += t.revenue()
        prev_product_data[t.item_id]['cost']    += t.cost()

    margin_alerts = []
    for item_id, cur in cur_product_data.items():
        if cur['revenue'] <= 0:
            continue
        cur_margin = (cur['revenue'] - cur['cost']) / cur['revenue'] * 100

        prev = prev_product_data.get(item_id)
        if not prev or prev['revenue'] <= 0:
            continue
        prev_margin = (prev['revenue'] - prev['cost']) / prev['revenue'] * 100

        drop = prev_margin - cur_margin
        if drop >= 5:
            margin_alerts.append({
                'name':           cur['name'],
                'cur_margin':     round(cur_margin, 1),
                'prev_margin':    round(prev_margin, 1),
                'drop':           round(drop, 1),
                'severity':       'critical' if drop >= 10 else 'warning',
                'cur_revenue':    round(cur['revenue'], 2),
            })

    margin_alerts.sort(key=lambda x: x['drop'], reverse=True)
    margin_alerts = margin_alerts[:5]  # show top 5 worst

    # ── Store performance ──
    store_stats = defaultdict(lambda: {'name': '', 'revenue': 0.0, 'units': 0})
    for t in current_sales:
        sid = t.item.store_id
        store_stats[sid]['name'] = t.item.store.name if t.item.store else 'Unknown'
        store_stats[sid]['revenue'] += t.revenue()
        store_stats[sid]['units'] += _units(t)

    store_list = sorted(store_stats.values(), key=lambda x: x['revenue'], reverse=True)
    # Same float-summation-noise fix as top_products above ("Liquor Store
    # 700.2000000000004 units").
    for _s in store_list:
        _s['units'] = round(_s['units'], 2)
        _s['revenue'] = round(_s['revenue'], 2)

    # ── Order analytics ──
    orders = Order.objects.filter(
        business=business,
        created_at__date__gte=start_date,
        created_at__date__lte=today,
    )
    total_orders = orders.count()
    pending_orders = orders.filter(status='pending').count()
    completed_orders = orders.filter(status='completed').count()
    order_revenue = float(
        orders.filter(status__in=['paid', 'ready', 'completed']).aggregate(
            total=Sum('total_amount')
        )['total'] or 0
    )

    # ── Stock health ──
    all_items = list(Item.objects.filter(business=business))
    total_items = len(all_items)
    # 2026-08-21 live request (Roy, about to travel to Monsoon Inn — "audit
    # the speed of responsiveness"): this section called current_balance()
    # up to 3 times per item (out_of_stock check, low_stock check, velocity
    # loop below), each its own DB round-trip — same N+1 shape already
    # fixed for stock_list()/home()/kitchen_board()/stock_take_api() on
    # this exact date. Reuses the same proven batch helper; item.stock_
    # balance is byte-identical to current_balance() (StockListBatchMetricsTest).
    from .views import _batch_stock_metrics
    _batch_stock_metrics(all_items)
    # 2026-08-19 — was two separately-duplicated is_keg/BUNCH-produce checks
    # that never included is_kitchen_batch (Chipo's own class of item) —
    # unified onto Item.uses_envelope_stock_tracking(), the same helper now
    # used by needs_reorder()/stock_value()/_batch_stock_metrics(). Their
    # "stock" lives in a separate envelope model (KegBarrel/ProduceBunch/
    # KitchenBatch), not this item's own unit balance — current_balance()
    # would otherwise register them as permanently out-of-stock/low-stock.
    out_of_stock = sum(
        1 for i in all_items
        if i.stock_balance <= 0 and not i.uses_envelope_stock_tracking()
    )
    low_stock = sum(
        1 for i in all_items
        if 0 < i.stock_balance <= i.reorder_level and not i.uses_envelope_stock_tracking()
    )
    healthy_stock = total_items - out_of_stock - low_stock
    stock_value = sum(i.stock_value() for i in all_items)

    # ── Stock Velocity Ranking ──
    # Build per-item units sold lookup from already-evaluated current_sales
    item_units_sold = defaultdict(float)
    for t in current_sales:
        item_units_sold[t.item_id] += _units(t)

    velocity_data = []
    for item in all_items:
        # 2026-08-19 — unified onto uses_envelope_stock_tracking(); now also
        # excludes is_kitchen_batch (e.g. Chipo) for the same reason keg and
        # BUNCH produce already were.
        if item.uses_envelope_stock_tracking():
            continue
        balance    = float(item.stock_balance)
        units_sold = item_units_sold.get(item.id, 0.0)
        daily_rate = units_sold / days if days > 0 else 0.0

        if balance <= 0:
            days_left       = 0
            velocity_status = 'out'
        elif daily_rate == 0:
            days_left       = None
            velocity_status = 'no_movement'
        else:
            days_left = round(balance / daily_rate)
            if days_left <= 7:
                velocity_status = 'critical'
            elif days_left <= 14:
                velocity_status = 'warning'
            else:
                velocity_status = 'healthy'

        velocity_data.append({
            'name':             item.description,
            'balance':          max(0.0, round(balance, 1)),  # floor at 0; negatives mean oversold — corrected via Receipt
            'units_sold':       round(units_sold, 1),
            'daily_rate':       round(daily_rate, 2),
            'days_left':        days_left,
            'velocity_status':  velocity_status,
        })

    # Sort: Out of Stock → Critical → Warning → Healthy → No Movement
    STATUS_ORDER = {'out': 0, 'critical': 1, 'warning': 2, 'healthy': 3, 'no_movement': 4}
    velocity_data.sort(
        key=lambda x: (STATUS_ORDER[x['velocity_status']], x['days_left'] or 9999)
    )

    # ── Payment analytics ──
    payments = Payment.objects.filter(
        business=business,
        created_at__date__gte=start_date,
        created_at__date__lte=today,
    )
    mpesa_completed = payments.filter(method='mpesa', status='completed')
    mpesa_total = float(mpesa_completed.aggregate(s=Sum('amount'))['s'] or 0)
    mpesa_count = mpesa_completed.count()

    # ── Payment method split (Transactions primary + Orders secondary) ──
    METHOD_DISPLAY = {
        'cash':   'Cash',
        'mpesa':  'M-Pesa',
        'credit': 'Credit / Tab',
        'bank':   'Bank Transfer',
        'card':   'Card',
    }

    txn_by_method = defaultdict(lambda: {'total': 0.0, 'count': 0})

    # Primary: counter sales recorded through Quick Sell / Add Transaction
    for t in current_sales:
        method = t.payment_method or 'cash'
        txn_by_method[method]['total'] += t.revenue()
        txn_by_method[method]['count'] += 1

    # Secondary: online order payments (M-Pesa STK push, etc.)
    order_payment_raw = (
        payments.filter(status='completed')
        .values('method')
        .annotate(total=Sum('amount'), count=Count('id'))
    )
    for item in order_payment_raw:
        method = item['method']
        txn_by_method[method]['total'] += float(item['total'] or 0)
        txn_by_method[method]['count'] += item['count']

    # Sort by total descending
    split_items = sorted(
        txn_by_method.items(),
        key=lambda x: x[1]['total'],
        reverse=True
    )

    split_labels = [METHOD_DISPLAY.get(m, m.title()) for m, _ in split_items]
    split_totals  = [round(v['total'], 2) for _, v in split_items]
    split_counts  = [v['count'] for _, v in split_items]

    if not split_labels:
        split_labels = ['No payments recorded']
        split_totals  = [0.0]
        split_counts  = [0]

    # ── Busiest day ──
    if daily_data:
        busiest_day = max(daily_data.items(), key=lambda x: x[1]['revenue'])
        busiest_day_str = busiest_day[0]
        busiest_day_rev = round(busiest_day[1]['revenue'], 0)
    else:
        busiest_day_str = '—'
        busiest_day_rev = 0

    # ── Average daily revenue ──
    active_days = sum(1 for d in all_dates if daily_data[d]['revenue'] > 0)
    avg_daily_revenue = round(cur_revenue / active_days, 0) if active_days > 0 else 0

    profit_margin = round(cur_profit / cur_revenue * 100, 1) if cur_revenue > 0 else 0

    # ── Net Profit (gross profit - expenses - drawings - wastage - void losses) ──
    total_expenses = BusinessExpense.objects.filter(
        business=business,
        date__gte=start_date,
        date__lte=today,
    ).aggregate(total=Sum('amount'))['total'] or 0

    owner_drawing_txns = list(Transaction.objects.filter(
        business=business,
        type='OwnerConsumption',
        date__gte=start_date,
        date__lte=today,
    ).select_related('item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset'))
    owner_drawings_cost = round(sum(
        t.loss_value() for t in owner_drawing_txns
    ), 2)
    # 2026-08-11 live request (Roy): tap-to-expand breakdown for the Owner
    # Drawings tile — same "show the real line items" transparency pattern
    # as home.html's till-breakdown disclosure, so a figure can be checked
    # against reality instead of taken on faith.
    owner_drawing_items = sorted(
        [
            {'date': t.date, 'name': t.item.description, 'qty': abs(t.qty or 0), 'value': round(t.loss_value(), 2)}
            for t in owner_drawing_txns
        ],
        key=lambda r: r['date'], reverse=True,
    )

    # Wastage loss: cost of stock discarded, broken, or adjusted out — no revenue received.
    # Uses Transaction.loss_value() per Wastage transaction — NOT a raw
    # abs(qty) * item.cost_price (2026-08-11 live report, Roy: the Hasara/
    # Losses tile showed a Voids figure in the tens of millions on a business
    # doing ~KES 150k of revenue for the period). The raw formula is only
    # correct for a plain item with no keg/bunch/batch/preset linkage — for a
    # keg pour specifically, qty is stored in ml while item.cost_price is
    # priced per WHOLE KEG, so pricing a voided pour that way inflates the
    # loss by roughly 1000x. loss_value() reuses cost()'s own keg/bunch/
    # batch/preset-aware proportional logic instead of reimplementing a
    # simplified formula — see Transaction._stock_movement_cost()'s
    # docstring for the full root-cause trace.
    # invoice_no='[ADJ-NOLOSS]' excludes a Rekebisha shortage correction the
    # owner/manager explicitly marked as NOT a real loss (2026-07-31 live
    # report — reversing a duplicate-receipt bug via Rekebisha showed up as
    # real "Wastage — cost lost" for phantom units that were never actually
    # received; no real money was ever spent on them). A genuine shortage
    # correction (real breakage/theft discovered late, still plain '[ADJ]')
    # correctly still counts here.
    wastage_txns = list(Transaction.objects.filter(
        business=business,
        type='Wastage',
        date__gte=start_date,
        date__lte=today,
    ).exclude(invoice_no='[ADJ-NOLOSS]').select_related(
        'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
    ))
    wastage_loss = round(sum(t.loss_value() for t in wastage_txns), 2)

    # Void/write-off loss: stock was served but payment was cancelled/waived.
    # Revenue was already excluded from cur_revenue (payment_method='void' excluded from
    # current_sales). But the COGS of those goods is also excluded, overstating gross profit.
    # Add back the cost here so net_profit reflects the true position. Uses
    # Transaction.cost() directly (not a raw formula) — same 2026-08-11 fix as
    # wastage_loss above; a voided transaction is still type='Issue', so
    # cost() already has the correct keg/bunch/batch/preset-aware logic, the
    # bug was only ever that this call site never used it.
    void_txns = list(Transaction.objects.filter(
        business=business,
        type='Issue',
        payment_method='void',
        date__gte=start_date,
        date__lte=today,
    ).select_related('item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset'))
    void_loss = round(sum(t.cost() for t in void_txns), 2)

    # 2026-08-11 live request (Roy): tap-to-expand breakdown for the Hasara/
    # Losses tile — every wastage AND void line, so a suspicious total can be
    # checked against the actual transactions behind it, same transparency
    # pattern as owner_drawing_items above.
    loss_items = sorted(
        [
            {'date': t.date, 'name': t.item.description, 'qty': abs(t.qty or 0),
             'value': round(t.loss_value(), 2), 'kind': 'Wastage', 'reason': t.recipient or ''}
            for t in wastage_txns
        ] + [
            {'date': t.date, 'name': t.item.description, 'qty': abs(t.qty or 0),
             'value': round(t.cost(), 2), 'kind': 'Void', 'reason': t.recipient or ''}
            for t in void_txns
        ],
        key=lambda r: r['date'], reverse=True,
    )

    total_losses = round(wastage_loss + void_loss, 2)

    net_profit = cur_profit - float(total_expenses) - owner_drawings_cost - total_losses

    # 2026-08-11 live request (Roy): tap-to-expand breakdown for the Total
    # Expenses tile.
    expense_items = list(
        BusinessExpense.objects.filter(
            business=business, date__gte=start_date, date__lte=today,
        ).order_by('-date').values('date', 'category', 'description', 'amount')[:50]
    )

    # 2026-08-11 live request (Roy, relaying a doubt from Bosco: "whatever
    # is being shown as revenue must be extremely exaggerated") — day-by-day
    # revenue/profit breakdown for the Revenue and Gross Profit tiles, so the
    # "X vs prev" figure can be verified against the real daily numbers
    # instead of taken on faith. Reuses daily_data (already computed above
    # for the chart) rather than re-querying.
    revenue_daily_breakdown = [
        {'date': d, 'revenue': round(daily_data[d]['revenue'], 2), 'profit': round(daily_data[d]['profit'], 2)}
        for d in sorted(all_dates, reverse=True)
        if daily_data[d]['revenue'] > 0 or daily_data[d]['profit'] != 0
    ]

    # ── Break-Even Analysis (all-time, not period-filtered) ──────────────────────
    total_capital = float(
        CapitalInvestment.objects.filter(business=business)
        .aggregate(total=Sum('amount'))['total'] or 0
    )

    breakeven_data = {}

    if total_capital > 0:
        # All-time sales and expenses — not period filtered
        # Same 2026-08-19 select_related gap as current_sales/prev_sales
        # above — worse here specifically, since this query has NO date
        # filter at all (the break-even section is deliberately all-time)
        # and re-scans the business's entire transaction history on every
        # single analytics page load, regardless of which period is
        # selected.
        all_sales = Transaction.objects.filter(
            business=business, type='Issue'
        ).exclude(payment_method='void').select_related(
            'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
        ).order_by('date')

        all_expenses = BusinessExpense.objects.filter(
            business=business
        ).order_by('date')

        # Aggregate by month
        monthly_rev  = defaultdict(float)
        monthly_cogs = defaultdict(float)
        monthly_exp  = defaultdict(float)

        for t in all_sales:
            mk = t.date.strftime('%Y-%m')
            monthly_rev[mk]  += t.revenue()
            monthly_cogs[mk] += t.cost()

        for e in all_expenses:
            mk = e.date.strftime('%Y-%m')
            monthly_exp[mk] += float(e.amount)

        all_months = sorted(
            set(list(monthly_rev.keys()) + list(monthly_exp.keys()))
        )

        # Build cumulative profit series
        pre_app_profit  = float(business.pre_app_cumulative_profit or 0)
        cumulative      = pre_app_profit
        be_labels       = []
        be_values       = []
        breakeven_month = None

        for mk in all_months:
            prev        = cumulative
            monthly_pnl = monthly_rev[mk] - monthly_cogs[mk] - monthly_exp[mk]
            cumulative += monthly_pnl
            be_labels.append(mk)
            be_values.append(round(cumulative, 2))

            if breakeven_month is None and prev < total_capital <= cumulative:
                breakeven_month = mk

        # Recovery stats
        amount_recovered = round(cumulative, 2)
        amount_remaining = round(max(0.0, total_capital - cumulative), 2)
        recovery_pct     = round(
            min(100.0, (cumulative / total_capital) * 100), 1
        ) if total_capital > 0 else 0

        # Project break-even date (using last 3 months avg monthly profit)
        projected_breakeven = None
        if cumulative < total_capital and len(all_months) >= 1:
            recent  = all_months[-3:]
            avg_pnl = sum(
                monthly_rev[m] - monthly_cogs[m] - monthly_exp[m]
                for m in recent
            ) / len(recent)

            if avg_pnl > 0:
                months_needed   = math.ceil(amount_remaining / avg_pnl)
                today_d         = today
                total_m         = today_d.month + months_needed
                proj_year       = today_d.year + (total_m - 1) // 12
                proj_month      = (total_m - 1) % 12 + 1
                month_names     = [
                    'Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec'
                ]
                projected_breakeven = f"{month_names[proj_month-1]} {proj_year}"

        breakeven_data = {
            'total_capital':      round(total_capital, 2),
            'amount_recovered':   amount_recovered,
            'amount_remaining':   amount_remaining,
            'recovery_pct':       recovery_pct,
            'breakeven_month':    breakeven_month,
            'projected_breakeven': projected_breakeven,
            'chart_labels':       json.dumps(be_labels),
            'chart_values':       json.dumps(be_values),
            'capital_line':       json.dumps(
                [round(total_capital, 2)] * len(be_labels)
            ),
            'has_broken_even':    breakeven_month is not None,
            'pre_app_profit':     round(pre_app_profit, 2),
            'business_start_date': business.business_start_date.strftime('%d %b %Y') if business.business_start_date else None,
        }

    # ── Greens (Kibanda Produce Module) Analytics ──────────────────────────────
    # Per-item: bunches received in period, revenue collected, cost, wastage
    greens_items = list(
        ProduceBunch.objects
        .filter(business=business, received_on__gte=start_date, received_on__lte=today,
                item__store__is_kitchen=False)
        .values('item__description')
        .annotate(
            bunches_in=Count('id'),
            bunches_done=Count('id', filter=Q(status__in=['DEPLETED', 'DISCARDED'])),
            total_revenue=Sum('revenue_collected'),
            total_cost=Sum('cost_price'),
            total_target=Sum('target_revenue'),
        )
        .filter(total_cost__gt=0)
        .order_by('-total_revenue')
    )
    for g in greens_items:
        rev = float(g['total_revenue'] or 0)
        cost = float(g['total_cost'] or 1)
        target = float(g['total_target'] or 0)
        g['revenue'] = round(rev, 2)
        g['cost'] = round(float(g['total_cost'] or 0), 2)
        g['markup'] = round(rev / cost, 2) if cost > 0 else 0
        g['wastage'] = round(max(0, target - rev), 2)

    total_greens_revenue = round(sum(g['revenue'] for g in greens_items), 2)
    greens_share = round(100 * total_greens_revenue / float(cur_revenue), 1) if cur_revenue > 0 else 0

    # Daily greens revenue for sparkline (use Transaction.sale_amount which is set for all bunch sales)
    greens_daily_raw = list(
        Transaction.objects
        .filter(
            business=business, type='Issue',
            produce_bunch__isnull=False,   # only BUNCH greens have a produce_bunch FK
            date__gte=start_date, date__lte=today,
            item__store__is_kitchen=False,
        )
        .annotate(day=TruncDate('date'))
        .values('day')
        .annotate(revenue=Sum('sale_amount'))
        .order_by('day')
    )
    greens_daily_labels = json.dumps([str(r['day']) for r in greens_daily_raw])
    greens_daily_values = json.dumps([float(r['revenue'] or 0) for r in greens_daily_raw])

    # ── PORTION produce analytics (onions, tomatoes, potatoes, etc.) ──────────────
    # UBA §5.2: a stock transfer's dispatch/receive legs are type='Transfer',
    # never type='Issue' — so they're excluded from this query by
    # construction, with no explicit transfer_id filter needed here.
    portion_txns = list(
        Transaction.objects
        .filter(
            business=business, type='Issue',
            item__is_produce=True, item__produce_mode='PORTION',
            item__store__is_kitchen=False,
            date__gte=start_date, date__lte=today,
        )
        .select_related('item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset')
    )
    from collections import defaultdict as _dd
    _pm = _dd(lambda: {'description': '', 'units': 0.0, 'revenue': 0.0, 'cost': 0.0, 'unit': ''})
    for t in portion_txns:
        k = t.item_id
        _pm[k]['description'] = t.item.description
        _pm[k]['unit'] = t.item.unit or 'Pcs'
        qty_abs = abs(float(t.qty or 0))
        # revenue()/cost() (not a raw reimplementation) — 2026-08-11 fix,
        # same as analytics_dashboard's wastage_loss/void_loss; correct for a
        # plain PORTION item either way, but also correctly honors a
        # preset-specific cost_price if one is ever set for a portion preset.
        _pm[k]['units']   += qty_abs
        _pm[k]['revenue'] += float(t.revenue())
        _pm[k]['cost']    += float(t.cost())
    portion_produce = sorted(_pm.values(), key=lambda x: -x['revenue'])
    for p in portion_produce:
        p['revenue'] = round(p['revenue'], 2)
        p['cost']    = round(p['cost'], 2)
        p['units']   = round(p['units'], 1)
        p['margin']  = (round((p['revenue'] - p['cost']) / p['revenue'] * 100, 1)
                        if p['revenue'] > 0 else 0)

    total_portion_revenue = round(sum(p['revenue'] for p in portion_produce), 2)
    total_produce_revenue = round(total_greens_revenue + total_portion_revenue, 2)
    produce_share = round(100 * total_produce_revenue / float(cur_revenue), 1) if cur_revenue > 0 else 0

    # ── Bar / Keg Analytics — by keg type ──────────────────────────────────────
    keg_type_labels = {
        'REGULAR': 'Regular (Lager)',
        'DARK':    'Dark / Stout',
        'GOLD':    'Gold (Premium)',
        '':        'Aina haijawekwa',
    }
    keg_barrels_period = (
        KegBarrel.objects
        .filter(
            business=business,
            received_on__gte=start_date,
            received_on__lte=today,
        )
        # Discriminator consistency: the Staff Pouring League section below
        # (over the same conceptual "this business's keg barrels this
        # period" data) already excludes kitchen via
        # item__store__is_kitchen — this query fed the Bar/Keg Analytics
        # table and Per-barrel P&L table without the same exclusion, so a
        # barrel under a kitchen store (nothing prevents Item.is_keg=True
        # there) would silently double-count into both the "Bar Performance"
        # section here and the separately-computed Kitchen Performance
        # section, corrupting the owner's own Bar-vs-Kitchen split.
        .exclude(item__store__is_kitchen=True)
        .select_related('item')
        .prefetch_related('cup_logs', 'weight_readings')
    )

    # Per-item keg breakdown with status breakdown per barrel
    _ki_map = {}
    for barrel in keg_barrels_period:
        iid = barrel.item_id
        if iid not in _ki_map:
            _ki_map[iid] = {
                'name':      barrel.item.description,
                'keg_type':  barrel.item.keg_type or '',
                'barrels':   0,
                'active':    0,    # TAPPED or SEALED (still selling)
                'returned':  0,    # RETURNED (write-off)
                'completed': 0,    # DEPLETED (fully sold)
                'revenue':   0.0,
                'cost':      0.0,
                'cup_cost':  0.0,
                'target':    0.0,
                'wastage':   0.0,  # only from RETURNED/DEPLETED barrels
            }
        _ki_map[iid]['barrels'] += 1
        _ki_map[iid]['revenue'] += float(barrel.revenue_collected or 0)
        _ki_map[iid]['cost']    += float(barrel.cost_price or 0)
        _ki_map[iid]['target']  += float(barrel.target_revenue or 0)
        for log in barrel.cup_logs.all():
            _ki_map[iid]['cup_cost'] += float(log.total_cost or 0)
        if barrel.status in ('TAPPED', 'SEALED'):
            _ki_map[iid]['active'] += 1
        elif barrel.status == 'RETURNED':
            _ki_map[iid]['returned'] += 1
            # Revenue shortfall on a returned barrel = real business loss
            _ki_map[iid]['wastage'] += max(
                0, float(barrel.target_revenue or 0) - float(barrel.revenue_collected or 0)
            )
        elif barrel.status == 'DEPLETED':
            _ki_map[iid]['completed'] += 1
            # Rare: depleted but below target
            _ki_map[iid]['wastage'] += max(
                0, float(barrel.target_revenue or 0) - float(barrel.revenue_collected or 0)
            )

    keg_item_rows = []
    for iid, row in sorted(_ki_map.items(), key=lambda x: -x[1]['revenue']):
        rev  = row['revenue']
        cost = row['cost']
        cup  = row['cup_cost']
        net  = rev - cost - cup
        # Build a human-readable barrel status label
        parts = []
        if row['active'] > 0:
            parts.append(f"{row['active']} selling")
        if row['returned'] > 0:
            parts.append(f"{row['returned']} returned")
        if row['completed'] > 0:
            parts.append(f"{row['completed']} done")
        barrels_label = f"{row['barrels']} ({', '.join(parts)})" if parts else str(row['barrels'])
        keg_item_rows.append({
            'name':          row['name'],
            'keg_type':      keg_type_labels.get(row['keg_type'], row['keg_type'] or '—'),
            'barrels':       row['barrels'],
            'barrels_label': barrels_label,
            'has_active':    row['active'] > 0,
            'has_returned':  row['returned'] > 0,
            'revenue':       round(rev, 2),
            'cost':          round(cost, 2),
            'cup_cost':      round(cup, 2),
            'net_margin':    round(net, 2),
            'markup':        round(rev / cost, 2) if cost > 0 else 0,
            'wastage':       round(row['wastage'], 2),
        })

    total_keg_revenue  = round(sum(r['revenue']  for r in keg_item_rows), 2)
    total_keg_cup_cost = round(sum(r['cup_cost'] for r in keg_item_rows), 2)
    total_keg_barrels  = sum(r['barrels'] for r in keg_item_rows)
    keg_type_rows = []  # kept for backward compat — template no longer uses it
    keg_share = round(100 * total_keg_revenue / float(cur_revenue), 1) if cur_revenue > 0 else 0

    # ── Per-barrel P&L with book-vs-scale shrinkage ───────────────────────────
    barrel_rows = []
    for barrel in keg_barrels_period:
        readings = sorted(barrel.weight_readings.all(), key=lambda r: r.recorded_at)
        latest_kg = float(readings[-1].weight_kg) if readings else float(barrel.gross_weight_kg)
        net_vol_ml = (float(barrel.gross_weight_kg) - float(barrel.tare_weight_kg)) * 1000.0
        scale_ml = max(0.0, (float(barrel.gross_weight_kg) - latest_kg) * 1000.0)
        book_ml = float(barrel.volume_dispensed_ml)
        shrinkage_ml = scale_ml - book_ml
        shrinkage_pct = round(shrinkage_ml / scale_ml * 100, 1) if scale_ml > 0 else 0.0
        markup = round(float(barrel.revenue_collected) / float(barrel.cost_price), 2) if barrel.cost_price else 0
        if barrel.tapped_at and barrel.closed_at:
            days_open = (barrel.closed_at.date() - barrel.tapped_at.date()).days
        elif barrel.tapped_at:
            days_open = (today - barrel.tapped_at.date()).days
        else:
            days_open = 0
        barrel_rows.append({
            'id':            barrel.id,
            'item':          barrel.item.description,
            'received_on':   barrel.received_on,
            'status_code':   barrel.status,
            'status_label':  barrel.get_status_display(),
            'cost':          round(float(barrel.cost_price), 2),
            'target':        round(float(barrel.target_revenue), 2),
            'collected':     round(float(barrel.revenue_collected), 2),
            'markup':        markup,
            'book_ml':       round(book_ml),
            'scale_ml':      round(scale_ml),
            'shrinkage_ml':  round(shrinkage_ml),
            'shrinkage_pct': shrinkage_pct,
            'days_open':     days_open,
            'has_readings':  bool(readings),
        })
    barrel_rows.sort(key=lambda x: x['received_on'], reverse=True)

    # ── Staff keg pouring league — shift-window attribution ──────────────────────
    # Attributes ALL bar Issue transactions during each staff shift (tab + walk-up),
    # so bartenders who serve mostly cash/mpesa walk-ups are no longer invisible.
    from django.db.models import Case, DecimalField as _DecF, Value, When
    from django.db.models.functions import Abs as _Abs, Coalesce as _Coal
    from .models import Shift as _Shift
    from .shift_views import _station_q

    _rev_expr = Case(
        When(sale_amount__isnull=False, then=F('sale_amount')),
        default=_Abs(F('qty')) * _Coal(F('item__selling_price'), Value(0)),
        output_field=_DecF(max_digits=12, decimal_places=2),
    )

    # 2026-08-02 live report (Roy, using the Bosco/Monsoon Inn account):
    # kitchen staff who never poured a drink were appearing in the Staff
    # Pouring League with real revenue attributed to them. Root cause: this
    # used to filter on Shift.store — a documented broken proxy that is
    # ALWAYS business.stores.first() regardless of which counter the shift
    # actually ran on (see the 2026-07-27/28 "Fix Shift station
    # misattribution" sprint and the same-day keg_barrel_detail fix above).
    # A kitchen shift slipping into bar_shifts here isn't just cosmetic —
    # this section deliberately attributes ALL bar Issue transactions during
    # a shift's time window to that shift's staff (by design, so cash/mpesa
    # walk-up sales aren't invisible), so a misattributed kitchen shift
    # pulls in real bar revenue sold by someone else during that same
    # window and credits it to the kitchen staffer. Fixed with the same
    # explicit-station-first, role-fallback-for-blank-rows discriminator
    # already established for every other shift/station surface in this app.
    bar_shifts = list(
        _Shift.objects.filter(
            business=business,
            started_at__date__gte=start_date,
            started_at__date__lte=today,
            status__in=['OPEN', 'CLOSED', 'CONFIRMED'],
        ).filter(_station_q(is_kitchen=False)).select_related('staff')
    )

    _staff_acc = {}
    for _shift in bar_shifts:
        _shift_end = _shift.ended_at or timezone.now()
        # UBA §5.2: a stock transfer's dispatch leg is type='Transfer', never
        # type='Issue' — excluded from this shift-window revenue attribution
        # by construction, with no explicit transfer_id filter needed here.
        _agg = Transaction.objects.filter(
            business=business,
            type='Issue',
            created_at__gte=_shift.started_at,
            created_at__lte=_shift_end,
            item__store__is_kitchen=False,
        ).exclude(payment_method='void').aggregate(
            rev=Sum(_rev_expr),
            cnt=Count('id'),
        )
        _rev = float(_agg['rev'] or 0)
        _cnt = _agg['cnt'] or 0
        if _cnt == 0:
            continue
        _sid = _shift.staff_id
        _sname = _shift.staff.get_full_name() or _shift.staff.username
        if _sid not in _staff_acc:
            _staff_acc[_sid] = {'name': _sname, 'revenue': 0.0, 'servings': 0}
        _staff_acc[_sid]['revenue'] += _rev
        _staff_acc[_sid]['servings'] += _cnt

    staff_keg_rows = sorted(
        [
            {
                'name':            v['name'],
                'revenue':         round(v['revenue'], 2),
                'servings':        v['servings'],
                'avg_per_serving': round(v['revenue'] / v['servings'], 2) if v['servings'] > 0 else 0,
            }
            for v in _staff_acc.values()
        ],
        key=lambda x: -x['revenue'],
    )

    # ── Tabs aging buckets (open tabs only) ───────────────────────────────────
    open_tabs = (
        BarTab.objects
        .filter(business=business, status='OPEN')
        .prefetch_related('entries')
    )
    _aging_buckets = [
        {'label': 'Same day',  'count': 0, 'total': 0.0, 'color': '#6fae4f'},
        {'label': '1–3 days',  'count': 0, 'total': 0.0, 'color': '#c9a84c'},
        {'label': '4–7 days',  'count': 0, 'total': 0.0, 'color': '#ffb74d'},
        {'label': '7+ days',   'count': 0, 'total': 0.0, 'color': '#c0395a'},
    ]
    total_open_tabs = 0
    total_tabs_owed = 0.0
    for tab in open_tabs:
        age_days = (today - tab.opened_at.date()).days
        # 2026-08-24 — remaining_amount(), not full amount: an unpaid entry
        # can carry a pre-existing amount_paid from a transferred, part-paid item.
        unpaid = sum(float(e.remaining_amount()) for e in tab.entries.all() if not e.is_paid)
        idx = 0 if age_days == 0 else (1 if age_days <= 3 else (2 if age_days <= 7 else 3))
        _aging_buckets[idx]['count'] += 1
        _aging_buckets[idx]['total'] += unpaid
        total_open_tabs += 1
        total_tabs_owed += unpaid
    tabs_aging = [b for b in _aging_buckets if b['count'] > 0]
    total_tabs_owed = round(total_tabs_owed, 2)

    # ── Kitchen Performance Analytics ─────────────────────────────────────────
    # 2026-08-01 live request — Roy explicitly asked for BOTH: one shared "Kuku"
    # item (so stock/selling/general reporting stays unified across suppliers)
    # AND per-supplier accountability/profit tracking (so a new cut/supplier's
    # true margin is never blended into another's). Grouping used to be by
    # item_id alone, silently averaging every preset sold under one item into a
    # single row — e.g. Meatco-costed wings and a new supplier's legs, both
    # tagged "Kuku", would report one blended margin, hiding exactly the
    # per-supplier discrepancy this business is trying to watch for. Now groups
    # by (item_id, preset_id): a preset-attributed sale (Transaction.preset,
    # 2026-07-28) gets its own row named "Item — Preset", so each cut/supplier
    # shows its own units/revenue/cost/margin; a plain item sale with no preset
    # keeps exactly the old single-row-per-item behaviour.
    kitchen_rows = []
    total_kitchen_revenue = 0.0
    kitchen_share = 0.0
    if business.has_kitchen:
        # Same 2026-08-19 select_related gap — missing kitchen_batch
        # specifically, which is exactly the FK every Chipo/batch-item sale
        # in this kitchen-only section needs for cost().
        _kitchen_txns = list(
            Transaction.objects.filter(
                business=business, type='Issue',
                item__store__is_kitchen=True,
                date__gte=start_date, date__lte=today,
            ).select_related('item', 'produce_bunch', 'preset', 'kitchen_batch')
        )
        _km = {}
        for t in _kitchen_txns:
            k = (t.item_id, t.preset_id)
            if k not in _km:
                name = t.item.description
                if t.preset_id and t.preset:
                    name = f'{name} — {t.preset.label}'
                _km[k] = {
                    'name':    name,
                    'units':   0.0,
                    'revenue': 0.0,
                    'cost':    0.0,
                    'is_batch': t.produce_bunch_id is not None,
                }
            rev = float(t.revenue())
            qty = abs(float(t.qty or 0))
            _km[k]['revenue'] += rev
            _km[k]['cost']    += float(t.cost())
            _km[k]['units']   += qty
        for row in sorted(_km.values(), key=lambda x: -x['revenue']):
            margin = (round((row['revenue'] - row['cost']) / row['revenue'] * 100, 1)
                      if row['revenue'] > 0 else 0)
            kitchen_rows.append({
                'name':    row['name'],
                'units':   round(row['units'], 1),
                'revenue': round(row['revenue'], 2),
                'cost':    round(row['cost'], 2),
                'margin':  margin,
                'is_batch': row['is_batch'],
            })
        total_kitchen_revenue = round(sum(r['revenue'] for r in kitchen_rows), 2)
        kitchen_share = round(100 * total_kitchen_revenue / float(cur_revenue), 1) if cur_revenue > 0 else 0

    # ── Financial Health: liquidity + efficiency (2026-08-19 request) ──
    # Roy's own audit question — does this app track profitability,
    # liquidity/cash flow, efficiency, and leverage metrics. Profitability
    # was already well covered above (Revenue/Gross Profit/Net Profit/
    # Margin/per-product profit). This section adds the other three,
    # reusing figures already computed above wherever possible rather than
    # re-deriving them — cur_cost/stock_value/breakeven_data all already
    # exist by this point in the function.
    from .payables import cash_position as _cash_position_fn, payables_aging_summary as _payables_aging_fn

    try:
        financial_cash_position = _cash_position_fn(business)
    except Exception:
        financial_cash_position = None

    # Inventory Turnover — how many times over the CURRENT stock value's
    # worth of goods was sold through during this period. Deliberately
    # labelled as an estimate against current stock value, not a true
    # textbook average-inventory ratio — this app has no daily stock-value
    # snapshot history to average against (a real limitation, not hidden).
    inventory_turnover = round(cur_cost / stock_value, 2) if stock_value > 0 else None

    # Days Sales Outstanding / Days Payable Outstanding — the standard
    # small-business formula: Outstanding ÷ Period Activity × Period Days.
    # Deliberately does NOT try to pin an individual payment to an
    # individual sale/invoice — this app's own debt/payables ledgers are
    # FIFO and dynamically reassigned by design (see
    # _get_customer_debt_data()'s own docstring), so a per-transaction
    # aging calculation would fight that design rather than use it.
    from .models import SupplierInvoice
    from .payables import total_receivables as _total_receivables_fn
    try:
        receivables_outstanding = _total_receivables_fn(business)
    except Exception:
        receivables_outstanding = 0.0

    period_credit_issued = 0.0
    _credit_txns = (
        Transaction.objects.filter(
            business=business, type='Issue',
            date__gte=start_date, date__lte=today,
        )
        .filter(Q(payment_method='credit') | Q(was_credit=True))
        .exclude(payment_method='void')
        .select_related('item')
    )
    for _t in _credit_txns:
        period_credit_issued += _t.revenue()

    days_sales_outstanding = (
        round(receivables_outstanding / period_credit_issued * days, 1)
        if period_credit_issued > 0 else None
    )

    try:
        payables_outstanding = _payables_aging_fn(business)['total_outstanding']
    except Exception:
        payables_outstanding = 0.0

    period_credit_purchased = float(
        SupplierInvoice.objects.filter(
            business=business, invoice_date__gte=start_date, invoice_date__lte=today,
        ).aggregate(total=Sum('amount'))['total'] or 0
    )
    days_payable_outstanding = (
        round(payables_outstanding / period_credit_purchased * days, 1)
        if period_credit_purchased > 0 else None
    )

    financial_health = {
        'cash_position': financial_cash_position,
        'inventory_turnover': inventory_turnover,
        'days_sales_outstanding': days_sales_outstanding,
        'days_payable_outstanding': days_payable_outstanding,
        'capital_recovery_pct': breakeven_data.get('recovery_pct') if breakeven_data else None,
        'receivables_outstanding': round(receivables_outstanding, 2),
        'payables_outstanding': round(payables_outstanding, 2),
    }

    context = {
        'period': days,
        'start_date': start_date,
        'end_date': today,
        # Summary
        'cur_revenue': round(cur_revenue, 2),
        'cur_cost': round(cur_cost, 2),
        'cur_profit': round(cur_profit, 2),
        'cur_units': cur_units,
        'cur_txn_count': cur_txn_count,
        'profit_margin': profit_margin,
        # Comparisons
        'revenue_change': revenue_change,
        'profit_change': profit_change,
        'units_change': units_change,
        'prev_revenue': round(prev_revenue, 2),
        'prev_profit': round(prev_profit, 2),
        'prev_start': prev_start,
        'prev_end': prev_end,
        # Tap-to-expand breakdowns (2026-08-11 live request) — the real line
        # items behind each headline figure, so any number can be checked
        # against reality instead of taken on faith.
        'revenue_daily_breakdown': revenue_daily_breakdown,
        'owner_drawing_items': owner_drawing_items,
        'loss_items': loss_items,
        'expense_items': expense_items,
        # Human-readable display versions
        'revenue_change_display': format_pct_change(revenue_change),
        'profit_change_display':  format_pct_change(profit_change),
        'units_change_display':   format_pct_change(units_change),
        # Charts
        'chart_labels': json.dumps(chart_labels),
        'chart_revenue': json.dumps(chart_revenue),
        'chart_profit': json.dumps(chart_profit),
        'chart_units': json.dumps(chart_units),
        'top_product_labels': json.dumps(top_product_labels),
        'top_product_revenue': json.dumps(top_product_revenue),
        # Margin Erosion Alerts
        'margin_alerts': margin_alerts,
        # Products
        'top_products': top_products,
        'store_list': store_list,
        # Orders
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'order_revenue': round(order_revenue, 2),
        # Stock health
        'total_items': total_items,
        'out_of_stock': out_of_stock,
        'low_stock': low_stock,
        'healthy_stock': healthy_stock,
        'stock_value': round(stock_value, 2),
        'velocity_data': velocity_data,
        # Payments
        'mpesa_total': round(mpesa_total, 2),
        'mpesa_count': mpesa_count,
        # Payment split
        'split_labels': json.dumps(split_labels),
        'split_totals': json.dumps(split_totals),
        'split_counts': json.dumps(split_counts),
        'split_zip': zip(split_labels, split_totals, split_counts),
        # Insights
        'busiest_day': busiest_day_str,
        'busiest_day_rev': busiest_day_rev,
        'avg_daily_revenue': avg_daily_revenue,
        'active_days': active_days,

        # Expenses, Drawings, Losses & Net Profit
        'total_expenses': round(float(total_expenses), 2),
        'owner_drawings_cost': owner_drawings_cost,
        'wastage_loss': wastage_loss,
        'void_loss': void_loss,
        'total_losses': total_losses,
        'net_profit': round(net_profit, 2),
        # Market Day Intelligence
        'market_day_labels': json.dumps(day_names),
        'market_day_totals': json.dumps(market_day_totals),
        'market_day_avgs':   json.dumps(market_day_avgs),
        'best_market_day':   best_market_day,
        'best_market_day_avg': round(best_market_day_avg, 2),
        # Product filter
        'items': items,
        # Greens (produce) analytics
        'greens_items': greens_items,
        'total_greens_revenue': total_greens_revenue,
        'greens_share': greens_share,
        'greens_daily_labels': greens_daily_labels,
        'greens_daily_values': greens_daily_values,
        'portion_produce': portion_produce,
        'total_portion_revenue': total_portion_revenue,
        'total_produce_revenue': total_produce_revenue,
        'produce_share': produce_share,
        'selected_product': selected_product,
        # Bar / Keg analytics
        'keg_item_rows':      keg_item_rows,
        'total_keg_revenue':  total_keg_revenue,
        'total_keg_cup_cost': total_keg_cup_cost,
        'total_keg_barrels':  total_keg_barrels,
        'keg_share':          keg_share,
        'barrel_rows':        barrel_rows,
        'staff_keg_rows':     staff_keg_rows,
        'tabs_aging':         tabs_aging,
        'total_open_tabs':    total_open_tabs,
        'total_tabs_owed':    total_tabs_owed,
        # Break-Even Analysis
        'breakeven_data': breakeven_data,
        # Kitchen performance
        'kitchen_rows':           kitchen_rows,
        'total_kitchen_revenue':  total_kitchen_revenue,
        'kitchen_share':          kitchen_share,
        # UBA §M0-6 — additive section registry, not read by analytics.html yet
        'uba_analytics_sections': uba_analytics_sections,
        # Financial Health (2026-08-19)
        'financial_health': financial_health,
    }
    return render(request, 'core/analytics.html', context)


@login_required
def analytics_api(request):
    """JSON endpoint: daily trends for external dashboards or mobile app."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({'error': 'No profile'}, status=403)

    # The page this data class belongs to (analytics_dashboard) requires
    # owner_or_manager_required; this JSON sibling had no gate of its own —
    # any staff member could pull full revenue+profit trends, unscoped by
    # station, for the whole business.
    if not user_profile.is_owner_or_manager:
        return JsonResponse({'error': 'Owner or manager only'}, status=403)

    business = user_profile.business
    today = timezone.localdate()
    days = min(int(request.GET.get('days', 30)), 365)
    start_date = today - timedelta(days=days - 1)

    sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=start_date, date__lte=today,
    ).exclude(payment_method='void').select_related('item')

    daily = defaultdict(lambda: {'revenue': 0, 'profit': 0, 'units': 0})
    for t in sales:
        d = str(t.date)
        daily[d]['revenue'] += t.revenue()
        daily[d]['profit'] += t.profit()
        daily[d]['units'] += _units(t)

    data = []
    d = start_date
    while d <= today:
        ds = str(d)
        data.append({
            'date': ds,
            'revenue': round(daily[ds]['revenue'], 2),
            'profit': round(daily[ds]['profit'], 2),
            'units': daily[ds]['units'],
        })
        d += timedelta(days=1)

    return JsonResponse({'trends': data, 'period_days': days})


# ── BUSINESS EXPENSES CRUD ────────────────────────────────────────────────────


@login_required
@owner_or_manager_required
def expense_list(request):
    """List all business expenses for the current period."""
    user_profile = request.user.userprofile
    business = user_profile.business
    today = timezone.localdate()

    period = request.GET.get('period', '30')
    try:
        days = int(period)
    except ValueError:
        days = 30
    days = min(days, 365)
    start_date = today - timedelta(days=days - 1)

    expenses = BusinessExpense.objects.filter(
        business=business,
        date__gte=start_date,
        date__lte=today,
    ).order_by('-date', '-created_at')

    total_expenses = expenses.aggregate(total=Sum('amount'))['total'] or 0

    # Group by category for breakdown
    category_totals = expenses.values('category').annotate(
        total=Sum('amount')
    ).order_by('-total')

    CATEGORY_LABELS = dict(BusinessExpense.CATEGORY_CHOICES)
    category_breakdown = [
        {'category': CATEGORY_LABELS.get(c['category'], c['category']),
         'total': float(c['total'])}
        for c in category_totals
    ]

    return render(request, 'core/expense_list.html', {
        'expenses': expenses,
        'total_expenses': float(total_expenses),
        'category_breakdown': category_breakdown,
        'period': days,
        'start_date': start_date,
        'end_date': today,
    })


@login_required
@owner_or_manager_required
def expense_add(request):
    """Add a new business expense."""
    user_profile = request.user.userprofile
    business = user_profile.business

    if request.method == 'POST':
        form = BusinessExpenseForm(request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.business = business
            expense.save()
            messages.success(request, _('Expense recorded successfully.'))
            return redirect('expense_list')
    else:
        form = BusinessExpenseForm()

    return render(request, 'core/expense_form.html', {
        'form': form,
        'title': _('Add Expense'),
    })


@login_required
@owner_or_manager_required
def expense_edit(request, expense_id):
    """Edit an existing business expense."""
    user_profile = request.user.userprofile
    business = user_profile.business
    expense = get_object_or_404(BusinessExpense, id=expense_id, business=business)

    if request.method == 'POST':
        form = BusinessExpenseForm(request.POST, instance=expense)
        if form.is_valid():
            form.save()
            messages.success(request, _('Expense updated successfully.'))
            return redirect('expense_list')
    else:
        form = BusinessExpenseForm(instance=expense)

    return render(request, 'core/expense_form.html', {
        'form': form,
        'title': _('Edit Expense'),
    })


@login_required
@owner_or_manager_required
def expense_delete(request, expense_id):
    """Delete a business expense."""
    user_profile = request.user.userprofile
    business = user_profile.business
    expense = get_object_or_404(BusinessExpense, id=expense_id, business=business)

    if request.method == 'POST':
        expense.delete()
        messages.success(request, _('Expense deleted.'))
        return redirect('expense_list')

    return render(request, 'core/expense_confirm_delete.html', {
        'expense': expense,
    })


# ── CAPITAL INVESTMENTS CRUD ──────────────────────────────────────────────────


@login_required
@owner_or_manager_required
def capital_investment_list(request):
    """List and add capital investments (one-time startup/asset costs)."""
    user_profile = request.user.userprofile
    business     = user_profile.business

    if request.method == 'POST':
        form = CapitalInvestmentForm(request.POST)
        if form.is_valid():
            inv = form.save(commit=False)
            inv.business = business
            inv.save()
            messages.success(request, _('Capital investment recorded successfully.'))
            return redirect('capital_investment_list')
    else:
        form = CapitalInvestmentForm()

    investments   = CapitalInvestment.objects.filter(business=business)
    total_invested = float(
        investments.aggregate(total=Sum('amount'))['total'] or 0
    )

    category_totals = investments.values('category').annotate(
        total=Sum('amount')
    ).order_by('-total')

    CATEGORY_LABELS = dict(CapitalInvestment.CATEGORY_CHOICES)
    category_breakdown = [
        {
            'category': CATEGORY_LABELS.get(c['category'], c['category']),
            'total':    float(c['total']),
        }
        for c in category_totals
    ]

    return render(request, 'core/capital_investments.html', {
        'investments':        investments,
        'total_invested':     round(total_invested, 2),
        'category_breakdown': category_breakdown,
        'form':               form,
    })


@login_required
@owner_or_manager_required
def capital_investment_edit(request, investment_id):
    user_profile = request.user.userprofile
    investment   = get_object_or_404(
        CapitalInvestment, id=investment_id, business=user_profile.business
    )

    if request.method == 'POST':
        form = CapitalInvestmentForm(request.POST, instance=investment)
        if form.is_valid():
            form.save()
            messages.success(request, _('Investment updated successfully.'))
            return redirect('capital_investment_list')
    else:
        form = CapitalInvestmentForm(instance=investment)

    return render(request, 'core/capital_investment_form.html', {
        'form':       form,
        'investment': investment,
        'title':      _('Edit Investment'),
    })


@login_required
@owner_or_manager_required
def capital_investment_delete(request, investment_id):
    user_profile = request.user.userprofile
    investment   = get_object_or_404(
        CapitalInvestment, id=investment_id, business=user_profile.business
    )

    if request.method == 'POST':
        investment.delete()
        messages.success(request, _('Investment deleted.'))
        return redirect('capital_investment_list')

    return render(request, 'core/capital_investment_confirm_delete.html', {
        'investment': investment,
    })


# ── COMPLIANCE & LICENSING ────────────────────────────────────────────────────

@login_required
@owner_or_manager_required
def compliance_checklist(request):
    """Display and update the business compliance/licensing checklist."""
    user_profile = request.user.userprofile
    business     = user_profile.business

    business_type = business.business_type if hasattr(business, 'business_type') else None

    # Get all requirements for this business type
    requirements = BusinessTypeRequirement.objects.filter(
        business_type=business_type
    ).order_by('display_order', 'name') if business_type else []

    if request.method == 'POST':
        from django.utils import timezone as tz
        declared_ids = set(
            int(x) for x in request.POST.getlist('declared') if x.isdigit()
        )
        for req in requirements:
            compliance, __ = BusinessCompliance.objects.get_or_create(
                business=business,
                requirement=req,
            )
            was_declared = compliance.is_declared
            compliance.is_declared = req.id in declared_ids
            compliance.notes = request.POST.get(f'notes_{req.id}', '').strip()
            if not was_declared and compliance.is_declared:
                compliance.declared_at = tz.now()
            elif not compliance.is_declared:
                compliance.declared_at = None
            compliance.save()
        messages.success(request, _('Compliance records updated successfully.'))
        return redirect('compliance_checklist')

    # Build compliance map
    existing = {
        c.requirement_id: c
        for c in BusinessCompliance.objects.filter(
            business=business,
            requirement__in=requirements,
        )
    }

    checklist = []
    for req in requirements:
        compliance = existing.get(req.id)
        checklist.append({
            'requirement':  req,
            'is_declared':  compliance.is_declared if compliance else False,
            'declared_at':  compliance.declared_at if compliance else None,
            'notes':        compliance.notes if compliance else '',
        })

    # Compliance score (mandatory only)
    mandatory     = [c for c in checklist if c['requirement'].is_mandatory]
    declared_mand = [c for c in mandatory if c['is_declared']]
    total_mand    = len(mandatory)
    score         = round(len(declared_mand) / total_mand * 100) if total_mand else 0

    if score == 100:
        badge_label = 'Fully Compliant'
        badge_color = 'var(--success)'
    elif score >= 50:
        badge_label = 'Partially Compliant'
        badge_color = 'var(--warning)'
    elif score > 0:
        badge_label = 'Getting Started'
        badge_color = 'var(--danger)'
    else:
        badge_label = 'Not Started'
        badge_color = 'var(--muted)'

    return render(request, 'core/compliance_checklist.html', {
        'checklist':       checklist,
        'business_type':   business_type,
        'score':           score,
        'badge_label':     badge_label,
        'badge_color':     badge_color,
        'total_mand':      total_mand,
        'declared_mand':   len(declared_mand),
        'remaining_mand':  total_mand - len(declared_mand),
        'has_requirements': len(checklist) > 0,
    })


# ── COUNTY SALES HEATMAP ──────────────────────────────────────────────────────

@login_required
@owner_or_manager_required
def county_heatmap(request):
    """
    Choropleth map of sales revenue by Kenya county.

    Join path: Transaction.recipient (text) → Customer.name → Customer.county
    Only Issue transactions where the recipient matches a Customer record with a county
    set will appear on the map.
    """
    user_profile = request.user.userprofile
    business = user_profile.business

    # Build name → county mapping for customers of this business with county set
    customer_county = {
        c.name: c.county
        for c in Customer.objects.filter(
            business=business,
            county__isnull=False,
        ).select_related('county')
    }

    # Aggregate Issue transactions by county
    county_revenue = defaultdict(lambda: {'county': None, 'revenue': 0.0, 'count': 0})

    issue_txns = Transaction.objects.filter(
        business=business,
        type='Issue',
    ).select_related('item')

    for txn in issue_txns:
        county = customer_county.get(txn.recipient)
        if not county:
            continue
        key = county.name
        county_revenue[key]['county'] = county
        county_revenue[key]['revenue'] += txn.revenue()
        county_revenue[key]['count'] += 1

    # Sort by revenue descending
    sorted_data = sorted(
        [
            {
                'county_name': name,
                'county_name_upper': name.upper(),
                'total_revenue': round(data['revenue'], 2),
                'transaction_count': data['count'],
            }
            for name, data in county_revenue.items()
        ],
        key=lambda x: x['total_revenue'],
        reverse=True,
    )

    # Build a lookup dict keyed by uppercase county name for fast JS matching
    heatmap_lookup = {row['county_name_upper']: row for row in sorted_data}

    return render(request, 'core/county_heatmap.html', {
        'heatmap_json': json.dumps(heatmap_lookup),
        'top_counties': sorted_data[:10],
        'total_mapped_revenue': sum(r['total_revenue'] for r in sorted_data),
        'counties_with_sales': len(sorted_data),
        'today': timezone.localdate().strftime('%B %d, %Y'),
    })


# ── REVENUE TARGETS ───────────────────────────────────────────────────────────

@login_required
@owner_or_manager_required
def revenue_target_settings(request):
    """
    Set revenue targets per period (daily / weekly / monthly).
    Multi-store businesses can also set per-store targets.
    One target per (business, period, store) — upsert on save.
    """
    user_profile = request.user.userprofile
    business = user_profile.business
    stores = list(Store.objects.filter(business=business))

    if request.method == 'POST':
        updated = 0
        for target_type in ('daily', 'weekly', 'monthly'):
            amount_raw = request.POST.get(f'target_{target_type}', '').strip()
            if amount_raw:
                try:
                    amount = Decimal(amount_raw)
                    if amount > 0:
                        RevenueTarget.objects.update_or_create(
                            business=business,
                            target_type=target_type,
                            store=None,
                            defaults={'amount': amount},
                        )
                        updated += 1
                except (InvalidOperation, ValueError):
                    pass

            for store in stores:
                store_amount_raw = request.POST.get(f'target_{target_type}_store_{store.id}', '').strip()
                if store_amount_raw:
                    try:
                        store_amount = Decimal(store_amount_raw)
                        if store_amount > 0:
                            RevenueTarget.objects.update_or_create(
                                business=business,
                                target_type=target_type,
                                store=store,
                                defaults={'amount': store_amount},
                            )
                            updated += 1
                    except (InvalidOperation, ValueError):
                        pass

        credit_window_raw = request.POST.get('credit_window_days', '').strip()
        if credit_window_raw:
            try:
                cw = int(credit_window_raw)
                if cw > 0:
                    business.credit_window_days = cw
                    business.save(update_fields=['credit_window_days'])
            except ValueError:
                pass

        messages.success(request, _('Targets updated successfully.'))
        return redirect('revenue_target_settings')

    existing = {
        (t.target_type, t.store_id): t
        for t in RevenueTarget.objects.filter(business=business)
    }

    def get_target(ttype, store_id=None):
        t = existing.get((ttype, store_id))
        return f'{float(t.amount):,.0f}' if t else ''

    # Flat dict for template lookup: 'daily', 'weekly', 'monthly',
    # 'daily_store_1', 'weekly_store_2', etc.
    target_lookup = {}
    for ttype in ('daily', 'weekly', 'monthly'):
        target_lookup[ttype] = get_target(ttype)
        for store in stores:
            target_lookup[f'{ttype}_store_{store.id}'] = get_target(ttype, store.id)

    context = {
        'stores': stores,
        'target_lookup': target_lookup,
        'target_types': [
            ('daily',   _('Daily')),
            ('weekly',  _('Weekly')),
            ('monthly', _('Monthly')),
        ],
        'credit_window': business.credit_window_days or 30,
        'today': timezone.localdate().strftime('%B %d, %Y'),
    }
    return render(request, 'core/revenue_target_settings.html', context)


@login_required
@owner_or_manager_required
def revenue_target_progress(request):
    """
    JSON endpoint — returns today's / this week's / this month's revenue
    vs the set targets. Used by the dashboard widget.

    GET /analytics/targets/progress/
    """
    user_profile = request.user.userprofile
    business = user_profile.business
    today = timezone.localdate()

    week_start  = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def period_revenue(start, end):
        sales = Transaction.objects.filter(
            business=business,
            type='Issue',
            date__gte=start,
            date__lte=end,
        ).exclude(payment_method='void').select_related('item')
        return sum(t.revenue() for t in sales)

    actual_daily   = period_revenue(today, today)
    actual_weekly  = period_revenue(week_start, today)
    actual_monthly = period_revenue(month_start, today)

    def get_target_amount(ttype):
        t = RevenueTarget.objects.filter(
            business=business, target_type=ttype, store__isnull=True
        ).first()
        return float(t.amount) if t else 0

    target_daily   = get_target_amount('daily')
    target_weekly  = get_target_amount('weekly')
    target_monthly = get_target_amount('monthly')

    def pct(actual, target):
        if target <= 0:
            return None
        return min(100, round(actual / target * 100, 1))

    stores = Store.objects.filter(business=business)
    store_breakdown = []
    for store in stores:
        store_sales = Transaction.objects.filter(
            business=business,
            type='Issue',
            date=today,
            item__store=store,
        ).exclude(payment_method='void').select_related('item')
        store_actual = sum(t.revenue() for t in store_sales)
        store_target_obj = RevenueTarget.objects.filter(
            business=business, target_type='daily', store=store
        ).first()
        store_target = float(store_target_obj.amount) if store_target_obj else 0

        store_breakdown.append({
            'store': store.name,
            'actual': round(store_actual, 2),
            'target': store_target,
            'pct': pct(store_actual, store_target),
        })

    return JsonResponse({
        'daily': {
            'target': target_daily,
            'actual': round(actual_daily, 2),
            'pct': pct(actual_daily, target_daily),
            'store_breakdown': store_breakdown,
        },
        'weekly': {
            'target': target_weekly,
            'actual': round(actual_weekly, 2),
            'pct': pct(actual_weekly, target_weekly),
        },
        'monthly': {
            'target': target_monthly,
            'actual': round(actual_monthly, 2),
            'pct': pct(actual_monthly, target_monthly),
        },
    })


# ── Expense Intelligence (12-month trend + impact) ─────────────────────────

@login_required
@owner_or_manager_required
def expense_report(request):
    """12-month expense intelligence — trends, per-line history, revenue impact, flags."""
    business = get_user_profile(request).business
    today = timezone.localdate()

    # Build list of 12 month-start dates, oldest first
    month_start = today.replace(day=1)
    months = []
    m = month_start
    for _month_idx in range(12):
        months.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    months.reverse()
    twelve_months_ago = months[0]

    CATEGORY_LABELS = dict(BusinessExpense.CATEGORY_CHOICES)

    # Monthly expense totals per category
    monthly_cat_qs = (
        BusinessExpense.objects
        .filter(business=business, date__gte=twelve_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month', 'category')
        .annotate(total=Sum('amount'))
        .order_by('month', 'category')
    )

    cat_month_data = defaultdict(lambda: defaultdict(float))
    monthly_expense_totals = defaultdict(float)
    all_categories = set()

    for row in monthly_cat_qs:
        raw_m = row['month']
        m = raw_m.date() if hasattr(raw_m, 'date') else raw_m
        m = m.replace(day=1)
        cat = row['category']
        cat_month_data[cat][m] = float(row['total'])
        monthly_expense_totals[m] += float(row['total'])
        all_categories.add(cat)

    # Monthly expense totals per description line
    monthly_line_qs = (
        BusinessExpense.objects
        .filter(business=business, date__gte=twelve_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month', 'description', 'category')
        .annotate(total=Sum('amount'))
        .order_by('description', 'month')
    )

    line_data = {}
    for row in monthly_line_qs:
        raw_m = row['month']
        m = raw_m.date() if hasattr(raw_m, 'date') else raw_m
        m = m.replace(day=1)
        desc = row['description']
        if desc not in line_data:
            line_data[desc] = {'category': row['category'], 'months': defaultdict(float)}
        line_data[desc]['months'][m] = float(row['total'])

    # Monthly revenue — iterate Issue transactions
    monthly_revenue = defaultdict(float)
    sales_qs = (
        Transaction.objects
        .filter(business=business, type='Issue', date__gte=twelve_months_ago)
        .exclude(payment_method='void')
        .select_related('item', 'produce_bunch', 'keg_barrel')
    )
    for t in sales_qs.iterator(chunk_size=500):
        monthly_revenue[t.date.replace(day=1)] += t.revenue()

    # Chart series
    month_labels = [m.strftime('%b %Y') for m in months]
    revenue_series = [round(monthly_revenue.get(m, 0), 2) for m in months]
    expense_series = [round(monthly_expense_totals.get(m, 0), 2) for m in months]

    CATEGORY_COLORS = {
        'rent':        '#c9a84c',
        'labor':       '#81c784',
        'electricity': '#4fc3f7',
        'utilities':   '#29b6f6',
        'transport':   '#ffb74d',
        'marketing':   '#ce93d8',
        'maintenance': '#a5d6a7',
        'supplies':    '#80deea',
        'tax':         '#ef5350',
        'other':       '#9e9e9e',
    }

    categories_sorted = sorted(all_categories)
    cat_datasets = [
        {
            'label': str(CATEGORY_LABELS.get(cat, cat)),
            'data': [round(cat_month_data[cat].get(m, 0), 2) for m in months],
            'backgroundColor': CATEGORY_COLORS.get(cat, '#888'),
        }
        for cat in categories_sorted
    ]

    # Per-line table rows
    lines = []
    for desc, info in sorted(line_data.items()):
        month_totals = [info['months'].get(m, 0.0) for m in months]
        grand_total = sum(month_totals)

        impacts = []
        for i, m in enumerate(months):
            rev = monthly_revenue.get(m, 0)
            exp = month_totals[i]
            if rev > 0 and exp > 0:
                impacts.append(exp / rev * 100)
        avg_impact_pct = round(sum(impacts) / len(impacts), 1) if impacts else 0.0

        last3_avg = sum(month_totals[-3:]) / 3
        prev3_avg = sum(month_totals[-6:-3]) / 3
        if prev3_avg > 0 and last3_avg > 0:
            trend_pct = round((last3_avg - prev3_avg) / prev3_avg * 100, 1)
        elif last3_avg > 0 and prev3_avg == 0:
            trend_pct = None  # new expense — no prior data
        else:
            trend_pct = 0.0

        lines.append({
            'description': desc,
            'category': CATEGORY_LABELS.get(info['category'], info['category']),
            'category_key': info['category'],
            'month_totals': [round(v, 0) for v in month_totals],
            'grand_total': round(grand_total, 0),
            'avg_impact_pct': avg_impact_pct,
            'trend_pct': trend_pct,
        })

    lines.sort(key=lambda x: x['grand_total'], reverse=True)

    # Flags / recommendations
    total_12m_revenue = sum(monthly_revenue.values())
    total_12m_expense = sum(monthly_expense_totals.values())
    overall_expense_pct = round(total_12m_expense / total_12m_revenue * 100, 1) if total_12m_revenue else 0.0

    flags = []
    for line in lines:
        if line['trend_pct'] is None:
            flags.append({
                'type': 'info',
                'icon': '🆕',
                'msg': f"<strong>{line['description']}</strong> — new expense with no prior-period data to compare.",
            })
        elif line['trend_pct'] >= 20:
            flags.append({
                'type': 'warning',
                'icon': '📈',
                'msg': (
                    f"<strong>{line['description']}</strong> increased "
                    f"<strong>{line['trend_pct']:.0f}%</strong> in the last 3 months "
                    f"vs the previous 3 months."
                ),
            })
        if line['avg_impact_pct'] >= 30 and total_12m_revenue > 0:
            flags.append({
                'type': 'danger',
                'icon': '⚠️',
                'msg': (
                    f"<strong>{line['description']}</strong> consumes "
                    f"<strong>{line['avg_impact_pct']:.0f}%</strong> of monthly revenue on average. "
                    f"Review whether this cost level is sustainable."
                ),
            })

    labor_total = sum(cat_month_data.get('labor', {}).values())
    if total_12m_revenue > 0 and labor_total / total_12m_revenue > 0.45:
        flags.append({
            'type': 'warning',
            'icon': '👥',
            'msg': (
                f"Labor costs are <strong>{labor_total / total_12m_revenue * 100:.0f}%</strong> "
                f"of 12-month revenue. Retail guideline is under 30%."
            ),
        })

    if overall_expense_pct > 60 and total_12m_revenue > 0:
        flags.append({
            'type': 'danger',
            'icon': '🔴',
            'msg': (
                f"Total expenses are <strong>{overall_expense_pct:.0f}%</strong> of 12-month revenue. "
                f"This leaves very little room for profit — consider which costs can be reduced."
            ),
        })

    if not flags:
        flags.append({
            'type': 'success',
            'icon': '✅',
            'msg': 'No major expense concerns. Costs are within healthy ranges.',
        })

    return render(request, 'core/expense_report.html', {
        'month_labels_json':   json.dumps(month_labels),
        'revenue_series_json': json.dumps(revenue_series),
        'expense_series_json': json.dumps(expense_series),
        'cat_datasets_json':   json.dumps(cat_datasets),
        'lines':               lines,
        'months':              months,
        'flags':               flags,
        'total_12m_expense':   round(total_12m_expense, 0),
        'total_12m_revenue':   round(total_12m_revenue, 0),
        'overall_expense_pct': overall_expense_pct,
        'today':               today,
    })


# ── Daily Sales Summary ────────────────────────────────────────────────────────

@login_required
def daily_sales(request):
    """Pick any date and see all sales, revenue by channel, item breakdown,
    full P&L (cost/profit — 2026-08-19), and a today-vs-yesterday-vs-day-
    before comparison. Built on core.daily_financials.compute_day_over_day()
    — see that module's own docstring for why this single-day computation
    is deliberately factored out and shared with the daily summary email."""
    from core.daily_financials import compute_day_over_day

    user_profile = request.user.userprofile
    business     = user_profile.business
    is_owner     = user_profile.is_owner_or_manager
    today        = timezone.localdate()

    # ── Date selection ──
    date_str = request.GET.get('date', '')
    try:
        selected_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        selected_date = today

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    is_today  = (selected_date == today)

    # ── Station scoping — same shape as before this refactor: a pure
    # single-station staffer's whole day view (revenue, cost, profit, item
    # rows) is scoped to their own station; anyone with combined/owner
    # access sees the whole business. ──
    show_bar, show_kitchen = _station_scope(user_profile)
    has_kitchen = getattr(business, 'has_kitchen', False)
    if show_bar and show_kitchen:
        station_filter = None
    elif show_kitchen:
        station_filter = True
    else:
        station_filter = False

    comparison = compute_day_over_day(business, selected_date, station_filter)
    day = comparison['today']

    return render(request, 'core/daily_summary.html', {
        'selected_date':  selected_date,
        'prev_date':      prev_date,
        'next_date':      next_date,
        'is_today':       is_today,
        'today_str':      today.isoformat(),
        'is_owner':       is_owner,
        'has_kitchen':    has_kitchen,
        'show_bar':       show_bar,
        'show_kitchen':   show_kitchen,
        'total_rev':      day['total_rev'],
        'confirmed_rev':  day['confirmed_rev'],
        'cash_rev':       day['cash_rev'],
        'mpesa_rev':      day['mpesa_rev'],
        'credit_rev':     day['credit_rev'],
        'bar_rev':        day['bar_rev'],
        'kitchen_rev':    day['kitchen_rev'],
        'total_cost':     day['total_cost'],
        'gross_profit':   day['gross_profit'],
        'profit_margin':  day['profit_margin'],
        'expenses_total': day['expenses_total'],
        'net_profit':     day['net_profit'],
        'item_rows':      day['item_rows'],
        'txn_count':      day['txn_count'],
        'wastage_list':   day['wastage_list'],
        'wastage_value':  day['wastage_value'],
        'void_loss':      day['void_loss'],
        # Owner Drawings stays owner/manager-only — matches the pre-refactor
        # behaviour exactly (compute_day_financials() itself always computes
        # this, since it's a shared, role-agnostic helper; visibility is a
        # view-layer decision).
        'owner_consumes': day['owner_consumes'] if is_owner else [],
        'owner_drawings_cost': day['owner_drawings_cost'],
        'receipt_count':  day['receipt_count'],
        # Today vs yesterday vs the day before (2026-08-19 request)
        'yesterday':              comparison['yesterday'],
        'day_before':             comparison['day_before'],
        'revenue_change_pct':     comparison['revenue_change_pct'],
        'net_profit_change_pct':  comparison['net_profit_change_pct'],
    })
