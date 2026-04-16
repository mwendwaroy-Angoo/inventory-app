"""
Analytics views — advanced reports and chart data.

Available views:
    /analytics/                  — Full analytics dashboard (HTML)
    /api/v1/analytics/trends/    — JSON: daily revenue/profit/orders for charts
"""

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone

from core.models import Item, Transaction, Order, Payment
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

    # ── Payment analytics ──
    payments = Payment.objects.filter(
        business=business,
        created_at__date__gte=start_date,
        created_at__date__lte=today,
    )
    mpesa_completed = payments.filter(method='mpesa', status='completed')
    mpesa_total = float(mpesa_completed.aggregate(s=Sum('amount'))['s'] or 0)
    mpesa_count = mpesa_completed.count()

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

    # ── Latest precomputed forecast (if any) ──
    try:
        from core.models import Forecast
        if selected_product:
            latest_fc = Forecast.objects.filter(business=business, meta__product_id=selected_product, meta__status='completed').order_by('-generated_at').first()
        else:
            latest_fc = Forecast.objects.filter(business=business, meta__status='completed').order_by('-generated_at').first()
    except Exception:
        latest_fc = None

    if latest_fc:
        fc_forecast = latest_fc.forecast or []
        forecast_chart_labels = [d.get('date') for d in fc_forecast]
        forecast_chart_values = [round(d.get('forecast', 0), 2) for d in fc_forecast]
    else:
        forecast_chart_labels = []
        forecast_chart_values = []

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
        # Charts
        'chart_labels': json.dumps(chart_labels),
        'chart_revenue': json.dumps(chart_revenue),
        'chart_profit': json.dumps(chart_profit),
        'chart_units': json.dumps(chart_units),
        'top_product_labels': json.dumps(top_product_labels),
        'top_product_revenue': json.dumps(top_product_revenue),
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
        # Payments
        'mpesa_total': round(mpesa_total, 2),
        'mpesa_count': mpesa_count,
        # Insights
        'busiest_day': busiest_day_str,
        'busiest_day_rev': busiest_day_rev,
        'avg_daily_revenue': avg_daily_revenue,
        'active_days': active_days,
        # Forecast
        'forecast_chart_labels': json.dumps(forecast_chart_labels),
        'forecast_chart_values': json.dumps(forecast_chart_values),
        # Product filter
        'items': items,
        'selected_product': selected_product,
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
