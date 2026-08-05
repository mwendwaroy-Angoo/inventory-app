"""
UBA §7.5 (Sprint R4) — retail intelligence JSON endpoints. See
core/retail_reports.py's module docstring for the deferred-UI discipline
these follow.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .models import Store
from .retail_reports import (
    basket_affinity_top10, dead_stock_report, hour_of_day_heatmap, reorder_today_list,
)
from .views import owner_or_manager_required


@login_required
@owner_or_manager_required
def dead_stock_report_view(request):
    business = request.user.userprofile.business
    days = int(request.GET.get('days', 60))
    rows = dead_stock_report(business, days=days)
    return JsonResponse({
        'ok': True,
        'rows': [
            {
                'item_id': r['item'].id, 'description': r['item'].description,
                'balance': r['balance'], 'capital_tied_up': r['capital_tied_up'],
                'last_sale_date': r['last_sale_date'].isoformat() if r['last_sale_date'] else None,
                'transfer_available': r['transfer_available'],
            }
            for r in rows
        ],
    })


@login_required
@owner_or_manager_required
def reorder_today_view(request):
    business = request.user.userprofile.business
    rows = reorder_today_list(business)
    return JsonResponse({
        'ok': True,
        'rows': [
            {
                'item_id': r['item'].id, 'description': r['item'].description,
                'recommended_qty': r['recommended_qty'], 'current_balance': r['current_balance'],
                'lead_time_days': r['lead_time_days'], 'message_draft': r['message_draft'],
            }
            for r in rows
        ],
    })


@login_required
@owner_or_manager_required
def basket_affinity_view(request):
    business = request.user.userprofile.business
    days = int(request.GET.get('days', 30))
    return JsonResponse({'ok': True, 'rows': basket_affinity_top10(business, days=days)})


@login_required
@owner_or_manager_required
def hour_heatmap_view(request):
    business = request.user.userprofile.business
    store_id = request.GET.get('store')
    store = Store.objects.filter(id=store_id, business=business).first() if store_id else None
    days = int(request.GET.get('days', 30))
    return JsonResponse({'ok': True, 'rows': hour_of_day_heatmap(business, store=store, days=days)})
