"""Owner Consumption — staff logs bottles/items taken by the business owner without a sale."""

import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, Transaction
from .shift_views import get_active_staff_shift

logger = logging.getLogger(__name__)


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


@login_required
@require_POST
def record_owner_consumption(request):
    """
    Log that the business owner took an item without paying (owner's personal draw).
    Any logged-in staff or manager can record this during an active shift.
    Owner can record it any time (shift-exempt).
    Returns JSON — consumed from Quick Sell and Bar Board modals.
    """
    up = _get_up(request)
    if not up or not up.business:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    business = up.business

    # Shift gate for non-owners
    if not up.is_owner_or_manager:
        shift = get_active_staff_shift(up, business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa. Fungua shift kwanza.'}, status=403)

    item_id = request.POST.get('item_id')
    qty_str  = request.POST.get('qty', '').strip()
    note     = request.POST.get('note', '').strip()

    if not item_id or not qty_str:
        return JsonResponse({'ok': False, 'error': 'Taja item na idadi.'}, status=400)

    try:
        qty = Decimal(qty_str)
        if qty <= 0:
            return JsonResponse({'ok': False, 'error': 'Idadi lazima iwe zaidi ya 0.'}, status=400)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Idadi si sahihi.'}, status=400)

    item = Item.objects.filter(id=item_id, business=business).select_related('store').first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Item haikupatikana.'}, status=404)

    Transaction.objects.create(
        business=business,
        item=item,
        type='OwnerConsumption',
        qty=-qty,
        date=timezone.localdate(),
        recorded_by=request.user,
        recipient=note or 'Mmiliki',
        payment_method=None,
    )

    new_balance = item.current_balance()
    return JsonResponse({
        'ok': True,
        'item': item.description,
        'qty': str(qty),
        'unit': item.unit,
        'new_balance': str(new_balance),
    })
