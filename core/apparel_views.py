"""
UBA §8.4 (Sprint A3) — aging/markdown report + fitting-room log endpoints.
A dedicated boutique-intelligence UI page is deferred, same discipline as
retail_board.html/payables_dashboard.html.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .accountability import variance_for
from .markdown_engine import aging_and_markdown_report, apply_markdown
from .models import FittingRoomLog, Item, Store
from .shift_views import get_active_staff_shift
from .views import owner_or_manager_required


@login_required
@owner_or_manager_required
def aging_markdown_report_view(request):
    business = request.user.userprofile.business
    store_id = request.GET.get('store')
    store = Store.objects.filter(id=store_id, business=business).first() if store_id else None
    rows = aging_and_markdown_report(business, store=store)
    return JsonResponse({
        'ok': True,
        'rows': [
            {
                'item_id': r['item'].id, 'description': r['item'].description,
                'days_since_last_sale': r['days_since_last_sale'], 'bucket': r['bucket'],
                'markdown_pct': r['markdown_pct'], 'current_price': r['current_price'],
                'suggested_price': r['suggested_price'], 'capital_tied_up': r['capital_tied_up'],
                'message': r['message'],
            }
            for r in rows
        ],
    })


@login_required
@owner_or_manager_required
@require_POST
def apply_markdown_view(request, item_id):
    up = request.user.userprofile
    item = Item.objects.filter(id=item_id, business=up.business).first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana.'}, status=404)
    try:
        new_price = Decimal(request.POST.get('new_price', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Bei si sahihi.'}, status=400)
    apply_markdown(item, new_price, up)
    return JsonResponse({'ok': True, 'message': f'Bei ya {item.description} imepunguzwa hadi KES {new_price}.'})


@login_required
@require_POST
def record_fitting_room_count(request):
    up = request.user.userprofile
    business = up.business
    shift = None
    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)
        shift = gate

    try:
        pieces_out = int(request.POST.get('pieces_out', '0'))
        pieces_back = int(request.POST.get('pieces_back', '0'))
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Idadi si sahihi.'}, status=400)

    store_id = request.POST.get('store')
    store = Store.objects.filter(id=store_id, business=business).first() if store_id else None

    log = FittingRoomLog.objects.create(
        business=business, store=store, staff=up, shift=shift,
        pieces_out=pieces_out, pieces_back=pieces_back,
        note=(request.POST.get('note') or '').strip(),
    )
    result = variance_for('fitting_room', business=business, store=log, shift=shift)
    return JsonResponse({
        'ok': True, 'log_id': log.id, 'variance': log.variance,
        'flag': result.flag if result else 'ok',
    })
