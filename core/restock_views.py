"""Restock notification module — staff flags empty items, owner gets SMS, receipt closes the loop."""

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, Notification, StockRequest
from .notifications import normalize_ke_phone, send_sms_notification

logger = logging.getLogger(__name__)


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


@login_required
@require_POST
def request_restock(request):
    """Staff raises a restock request for an empty item → owner gets SMS + in-app notification."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    business = up.business

    # Shift gate — staff must have an open shift; owners are always exempt
    if not up.is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift yako kwanza.'}, status=403)

    item_id = request.POST.get('item_id')
    note    = (request.POST.get('note') or '').strip()[:200]

    try:
        item = Item.objects.get(id=item_id, store__business=business)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Item not found.'}, status=404)

    # Station scope — staff can only request items from their accessible station
    from core.views import _station_scope
    _show_bar, _show_kitchen = _station_scope(up)
    if item.store:
        if item.store.is_kitchen and not _show_kitchen:
            return JsonResponse({'ok': False, 'error': 'Access denied.'}, status=403)
        if not item.store.is_kitchen and not _show_bar:
            return JsonResponse({'ok': False, 'error': 'Access denied.'}, status=403)

    sr = StockRequest.objects.create(
        business=business,
        item=item,
        requested_by=request.user,
        note=note,
    )

    # Notify all owners — in-app + SMS (Pattern A: direct, no router)
    staff_name = request.user.get_full_name() or request.user.username
    store_name = item.store.name if item.store else business.name
    sms_msg = (
        f"\U0001f534 {staff_name} anaripoti: {item.description} imekwisha "
        f"({store_name}). Tafadhali panga restock."
    )
    notif_title = f"\U0001f4e6 Restock Needed: {item.description}"

    owner_profiles = business.users.filter(role='owner')
    for op in owner_profiles:
        try:
            Notification.objects.create(
                user=op.user,
                title=notif_title,
                message=sms_msg,
                notification_type='warning',
            )
        except Exception as exc:
            logger.error('Restock in-app notification failed: %s', exc)

        owner_phone = getattr(op, 'phone', '') or business.phone or ''
        if owner_phone:
            try:
                send_sms_notification(sms_msg, normalize_ke_phone(owner_phone))
            except Exception as exc:
                logger.error('Restock SMS failed: %s', exc)

    return JsonResponse({'ok': True, 'request_id': sr.id})


@login_required
@require_POST
def restock_mark_ordered(request, request_id):
    """Owner marks a pending request as ordered (middle state)."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Owner or manager only.'}, status=403)

    sr = get_object_or_404(StockRequest, id=request_id, business=up.business, status=StockRequest.STATUS_PENDING)
    sr.status = StockRequest.STATUS_ORDERED
    sr.save(update_fields=['status'])
    return JsonResponse({'ok': True})


@login_required
def restock_list(request):
    """Owner/manager page: pending, ordered, and recently received StockRequests."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return redirect('home')

    business = up.business
    fourteen_days_ago = timezone.now() - timezone.timedelta(days=14)

    pending  = StockRequest.objects.filter(business=business, status=StockRequest.STATUS_PENDING).select_related('item__store', 'requested_by')
    ordered  = StockRequest.objects.filter(business=business, status=StockRequest.STATUS_ORDERED).select_related('item__store', 'requested_by')
    received = StockRequest.objects.filter(business=business, status=StockRequest.STATUS_RECEIVED, received_at__gte=fourteen_days_ago).select_related('item__store', 'requested_by', 'received_by', 'resolved_txn')

    return render(request, 'core/restock/restock_list.html', {
        'pending':  pending,
        'ordered':  ordered,
        'received': received,
        'is_owner': True,
    })
