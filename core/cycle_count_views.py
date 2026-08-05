"""
UBA §7.4 (Sprint R3) — cycle counting JSON endpoints. A dedicated "today's
count list" UI page is NOT built this pass (documented deferral, same
discipline as retail_board.html) — these endpoints are what such a page
would call, fully testable via direct HTTP today.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .cycle_count import (
    close_session, record_count_line, select_items_for_cycle_count,
    start_cycle_count_session,
)
from .models import Store
from .shift_views import get_active_staff_shift
from .views import owner_or_manager_required


@login_required
def todays_cycle_count_list(request):
    """GET — the "today's N items" list, before a session is even started,
    so staff can see what's coming up."""
    up = request.user.userprofile
    store_id = request.GET.get('store')
    store = Store.objects.filter(id=store_id, business=up.business).first() if store_id else None
    items = select_items_for_cycle_count(up.business, store=store)
    return JsonResponse({
        'ok': True,
        'items': [
            {'id': i.id, 'description': i.description, 'abc_class': i.abc_class,
             'is_high_risk': i.is_high_risk, 'current_balance': float(i.current_balance())}
            for i in items
        ],
    })


@login_required
@require_POST
def start_cycle_count(request):
    up = request.user.userprofile
    business = up.business

    shift = None
    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)
        shift = gate

    store_id = request.POST.get('store')
    store = Store.objects.filter(id=store_id, business=business).first() if store_id else None
    session = start_cycle_count_session(business, store, started_by=up, shift=shift)
    return JsonResponse({
        'ok': True, 'session_id': session.id,
        'lines': [{'line_id': l.id, 'item_id': l.item_id, 'description': l.item.description,
                   'book_qty': float(l.book_qty)} for l in session.lines.select_related('item')],
    })


@login_required
@require_POST
def submit_cycle_count_line(request, line_id):
    up = request.user.userprofile
    counted_qty = request.POST.get('counted_qty', '').strip()
    reason = (request.POST.get('reason') or '').strip()
    if not counted_qty:
        return JsonResponse({'ok': False, 'error': 'Idadi iliyohesabiwa inahitajika.'}, status=400)

    shift = None
    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, up.business)
        shift = gate if gate else None

    try:
        line = record_count_line(line_id, up.business, counted_qty, up, reason=reason, current_shift=shift)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Kosa limetokea.'}, status=400)

    return JsonResponse({
        'ok': True, 'variance_qty': float(line.variance_qty), 'variance_kes': float(line.variance_kes),
        'attributed_shift_id': line.attributed_shift_id,
    })


@login_required
@owner_or_manager_required
@require_POST
def close_cycle_count_session(request, session_id):
    up = request.user.userprofile
    session = close_session(session_id, up.business)
    return JsonResponse({'ok': True, 'status': session.status})
