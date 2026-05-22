"""
Analytics views — advanced reports and chart data.

Available views:
    /analytics/                  — Full analytics dashboard (HTML)
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
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q, F
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import Item, Transaction, Order, Payment, BusinessExpense, CapitalInvestment, BusinessTypeRequirement, BusinessCompliance
from core.forms import BusinessExpenseForm, CapitalInvestmentForm
from core.views import get_user_profile, owner_required


@login_required
@owner_required
def analytics_dashboard(request):
    """Rich analytics dashboard with trends, comparisons, and insights."""
    user_profile = request.user.userprofile
    business = user_profile.business
    today = date.today()

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
    current_sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=start_date, date__lte=today,
    ).select_related('item')

    prev_sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=prev_start, date__lte=prev_end,
    ).select_related('item')

    if selected_product:
        current_sales = current_sales.filter(item_id=selected_product)
        prev_sales = prev_sales.filter(item_id=selected_product)

    # ── Revenue / Cost / Profit ──
    cur_revenue = sum(t.revenue() for t in current_sales)
    cur_cost = sum(t.cost() for t in current_sales)
    cur_profit = cur_revenue - cur_cost
    cur_units = sum(abs(t.qty) for t in current_sales)
    cur_txn_count = current_sales.count()

    prev_revenue = sum(t.revenue() for t in prev_sales)
    prev_profit = prev_revenue - sum(t.cost() for t in prev_sales)
    prev_units = sum(abs(t.qty) for t in prev_sales)

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
        daily_data[d]['units'] += abs(t.qty)
        daily_data[d]['txns'] += 1

    all_dates = []
    d = start_date
    while d <= today:
        all_dates.append(str(d))
        d += timedelta(days=1)

    chart_labels = all_dates
    chart_revenue = [round(daily_data[d]['revenue'], 2) for d in all_dates]
    chart_profit = [round(daily_data[d]['profit'], 2) for d in all_dates]
    chart_units = [daily_data[d]['units'] for d in all_dates]

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
        product_stats[key]['units'] += abs(t.qty)
        product_stats[key]['revenue'] += t.revenue()
        product_stats[key]['profit'] += t.profit()

    top_products = sorted(
        product_stats.values(), key=lambda x: x['revenue'], reverse=True
    )[:10]

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
        store_stats[sid]['units'] += abs(t.qty)

    store_list = sorted(store_stats.values(), key=lambda x: x['revenue'], reverse=True)

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
    out_of_stock = sum(1 for i in all_items if i.current_balance() <= 0)
    low_stock = sum(1 for i in all_items if 0 < i.current_balance() <= i.reorder_level)
    healthy_stock = total_items - out_of_stock - low_stock
    stock_value = sum(i.stock_value() for i in all_items)

    # ── Stock Velocity Ranking ──
    # Build per-item units sold lookup from already-evaluated current_sales
    item_units_sold = defaultdict(float)
    for t in current_sales:
        item_units_sold[t.item_id] += abs(float(t.qty or 0))

    velocity_data = []
    for item in all_items:
        balance    = float(item.current_balance())
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
            'balance':          round(balance, 1),
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

    # ── Net Profit (gross profit - expenses) ──
    total_expenses = BusinessExpense.objects.filter(
        business=business,
        date__gte=start_date,
        date__lte=today,
    ).aggregate(total=Sum('amount'))['total'] or 0
    net_profit = cur_profit - float(total_expenses)

    # ── Break-Even Analysis (all-time, not period-filtered) ──────────────────────
    total_capital = float(
        CapitalInvestment.objects.filter(business=business)
        .aggregate(total=Sum('amount'))['total'] or 0
    )

    breakeven_data = {}

    if total_capital > 0:
        # All-time sales and expenses — not period filtered
        all_sales = Transaction.objects.filter(
            business=business, type='Issue'
        ).select_related('item').order_by('date')

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

        # Expenses & Net Profit
        'total_expenses': round(float(total_expenses), 2),
        'net_profit': round(net_profit, 2),
        # Market Day Intelligence
        'market_day_labels': json.dumps(day_names),
        'market_day_totals': json.dumps(market_day_totals),
        'market_day_avgs':   json.dumps(market_day_avgs),
        'best_market_day':   best_market_day,
        'best_market_day_avg': round(best_market_day_avg, 2),
        # Product filter
        'items': items,
        'selected_product': selected_product,
        # Break-Even Analysis
        'breakeven_data': breakeven_data,
    }
    return render(request, 'core/analytics.html', context)


@login_required
def analytics_api(request):
    """JSON endpoint: daily trends for external dashboards or mobile app."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({'error': 'No profile'}, status=403)

    business = user_profile.business
    today = date.today()
    days = min(int(request.GET.get('days', 30)), 365)
    start_date = today - timedelta(days=days - 1)

    sales = Transaction.objects.filter(
        business=business, type='Issue',
        date__gte=start_date, date__lte=today,
    ).select_related('item')

    daily = defaultdict(lambda: {'revenue': 0, 'profit': 0, 'units': 0})
    for t in sales:
        d = str(t.date)
        daily[d]['revenue'] += t.revenue()
        daily[d]['profit'] += t.profit()
        daily[d]['units'] += abs(t.qty)

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
@owner_required
def expense_list(request):
    """List all business expenses for the current period."""
    user_profile = request.user.userprofile
    business = user_profile.business
    today = date.today()

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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
            compliance, _ = BusinessCompliance.objects.get_or_create(
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
        'has_requirements': len(checklist) > 0,
    })
