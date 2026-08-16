"""
UBA §7.3 (Sprint R2) — the returns/refunds primitive. Deliberately a small,
separate view module (mirrors staff_request_views.py's own precedent) rather
than folding into quick_sell()'s already-large checkout view. A dedicated
"process a return" UI page is NOT built this pass (documented deferral,
same discipline as retail_board.html itself) — these are JSON endpoints a
future retail_board return flow would call, fully testable via direct HTTP
today.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import Exchange, ItemPriceHistory, Item, Return, Transaction
from .shift_views import get_active_staff_shift
from .views import owner_or_manager_required


@login_required
@require_POST
def process_return(request):
    """Any staff with an open shift may process a return — same permission
    tier as an ordinary sale (a return is a real, everyday counter action,
    not an owner-only correction); owner/manager bypass the shift gate as
    usual."""
    up = request.user.userprofile
    business = up.business

    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)

    txn_id = request.POST.get('transaction_id')
    qty_returned = request.POST.get('qty_returned', '').strip()
    reason = (request.POST.get('reason') or '').strip()

    if not reason:
        return JsonResponse({'ok': False, 'error': 'Sababu ya kurudisha inahitajika.'}, status=400)

    try:
        ret = Return.process_locked(
            original_transaction_id=txn_id, business=business, qty_returned=qty_returned,
            reason=reason, processed_by=up,
        )
    except (Transaction.DoesNotExist, ValueError) as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Muamala haukupatikana.'}, status=400)

    if ret.status == Return.STATUS_PENDING:
        return JsonResponse({
            'ok': True, 'needs_approval': True, 'return_id': ret.id,
            'message': f'Kurudisha kwa KES {ret.refund_amount} kunahitaji idhini ya mmiliki.',
        })
    return JsonResponse({
        'ok': True, 'needs_approval': False, 'return_id': ret.id,
        'message': f'Kurudishwa kwa {ret.item.description} kumekamilika. Salio na mauzo vimerekebishwa.',
    })


@login_required
@owner_or_manager_required
@require_POST
def approve_return(request, return_id):
    up = request.user.userprofile
    ret = Return.objects.filter(id=return_id, business=up.business).first()
    if not ret:
        return JsonResponse({'ok': False, 'error': 'Ombi halikupatikana.'}, status=404)
    try:
        ret.approve(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'message': 'Kurudisha kumeidhinishwa.'})


@login_required
@owner_or_manager_required
@require_POST
def reject_return(request, return_id):
    up = request.user.userprofile
    ret = Return.objects.filter(id=return_id, business=up.business).first()
    if not ret:
        return JsonResponse({'ok': False, 'error': 'Ombi halikupatikana.'}, status=404)
    try:
        ret.reject(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'message': 'Kurudisha kumekataliwa.'})


@login_required
@require_GET
def exchangeable_items_api(request):
    """Feeds the "🔁 Badilisha" (exchange) modal's item picker — plain
    non-keg items across the WHOLE business (bar or kitchen; a keg pour is
    deliberately excluded, same reasoning as bar_board()'s own non_keg_
    items — a keg exchange doesn't fit the simple qty×value model this
    uses). Shared by all three counters (Bar Board, Kitchen Board, Quick
    Sell) rather than each rendering its own differently-scoped list."""
    up = request.user.userprofile
    items = list(
        Item.objects.filter(store__business=up.business, is_keg=False)
        .order_by('description')
        .values('id', 'description', 'unit')
    )
    return JsonResponse({'items': items})


@login_required
@require_POST
def process_exchange(request):
    """2026-08-16 live request (Roy): a customer swaps an already-paid-for
    item for a different one of the same value (e.g. an unopened Guinness
    for a Chrome Gin, both KES 300) — no cash moves, so this is always
    immediate, same permission tier as an ordinary counter sale/return
    (any staff with an open shift; owner/manager exempt from the shift
    gate). See Exchange.process_locked()'s own docstring for the full
    mechanism."""
    up = request.user.userprofile
    business = up.business

    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)

    txn_id = request.POST.get('transaction_id')
    qty_returned = (request.POST.get('qty_returned') or '').strip()
    new_item_id = request.POST.get('new_item_id')
    new_qty = (request.POST.get('new_qty') or '').strip()
    reason = (request.POST.get('reason') or '').strip()

    if not new_item_id or not new_qty:
        return JsonResponse({'ok': False, 'error': 'Chagua bidhaa mpya na idadi.'}, status=400)

    try:
        exch = Exchange.process_locked(
            original_transaction_id=txn_id, business=business, qty_returned=qty_returned,
            new_item_id=new_item_id, new_qty=new_qty, reason=reason, processed_by=up,
        )
    except (Transaction.DoesNotExist, ValueError) as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Muamala haukupatikana.'}, status=400)

    return JsonResponse({
        'ok': True,
        'exchange_id': exch.id,
        'message': (
            f'{exch.return_record.item.description} imebadilishwa na '
            f'{exch.new_qty:g} {exch.new_item.description} — KES {exch.return_record.refund_amount} '
            f'(hakuna malipo mapya). Salio na mauzo vimerekebishwa.'
        ),
    })


@login_required
@owner_or_manager_required
@require_POST
def apply_suggested_price(request, item_id):
    """The margin guard's one-tap "Sasisha bei" action."""
    up = request.user.userprofile
    item = Item.objects.filter(id=item_id, store__business=up.business).first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana.'}, status=404)

    try:
        new_price = Decimal(request.POST.get('new_price', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Bei si sahihi.'}, status=400)

    old_price = item.selling_price
    item.selling_price = new_price
    item.save(update_fields=['selling_price'])
    ItemPriceHistory.objects.create(
        item=item, old_price=old_price, new_price=new_price,
        reason='margin_guard', changed_by=up,
    )
    return JsonResponse({'ok': True, 'message': f'Bei ya {item.description} imesasishwa hadi KES {new_price}.'})
