"""Owner Consumption — staff logs bottles/items taken by the business owner without a sale."""

import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Item, Transaction
from .shift_views import get_active_staff_shift

logger = logging.getLogger(__name__)


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def _notify_owner_consumption_change(business, actor, title, message, link_url, also_notify_user=None):
    """Shared notification fan-out for void/settle actions below — owner and
    every manager, plus the original recorder if they're not the one acting
    (mirrors this app's established correction-notification pattern, e.g.
    review_petty_cash / _notify_direct_correction)."""
    try:
        from .models import Notification
        from accounts.models import UserProfile as _UP
        recipients = set(
            _UP.objects.filter(business=business, role__in=('owner', 'manager'))
            .exclude(user=actor).values_list('user_id', flat=True)
        )
        if also_notify_user and also_notify_user.id != actor.id:
            recipients.add(also_notify_user.id)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        for user in User.objects.filter(id__in=recipients):
            Notification.objects.create(
                user=user, title=title, message=message,
                notification_type='info', link_url=link_url,
            )
    except Exception:
        logger.exception('owner_consumption: notify failed for business %s', business.id)


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

    # Server-side double-submit backstop — see core/idempotency.py. A duplicate
    # request would double-deduct stock as an owner draw with no sale to match
    # it against, creating a false stock discrepancy nobody would think to
    # trace back here (Quick-Sell-module audit finding, 2026-07-19).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

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
        payment_method='',
    )

    new_balance = item.current_balance()
    return JsonResponse({
        'ok': True,
        'item': item.description,
        'qty': str(qty),
        'unit': item.unit,
        'new_balance': str(new_balance),
    })


@login_required
@require_GET
def owner_consumption_list(request):
    """2026-08-07 live request — Roy: "no trail for mmiliki alichukuwa
    whereby ... a staff made a mistake they cannot correct or follow up on
    it." Before this, an OwnerConsumption draw only ever showed as one
    purple-badged row buried in the general Transaction History, with no
    dedicated place to review it, correct a mistake, or record that the
    owner paid back for what he took. Open to any staff (visibility to
    "follow up") — void/settle actions below are separately permission-
    gated."""
    up = _get_up(request)
    if not up or not up.business:
        return render(request, 'core/owner_consumption_list.html', {'rows': []})

    business = up.business
    txns = (
        Transaction.objects.filter(business=business, type='OwnerConsumption')
        .exclude(invoice_no='[OC-VOID]')
        .select_related('item', 'recorded_by')
        .prefetch_related('settlements')
        .order_by('-created_at')[:200]
    )
    rows = []
    for t in txns:
        settlement = t.settlements.first()
        rows.append({
            'txn': t,
            'settlement': settlement,
            'is_own': t.recorded_by_id == request.user.id,
        })
    return render(request, 'core/owner_consumption_list.html', {
        'rows': rows,
        'is_owner_or_manager': getattr(up, 'is_owner_or_manager', False),
    })


@login_required
@require_POST
def void_owner_consumption(request, txn_id):
    """Correct a mistaken Mmiliki Alichukua entry (wrong item, wrong
    quantity, or it never actually happened) — restores the stock exactly
    as it stood before the draw, same convention as remove_tab_entry's
    qty-zeroing correction. Owner/manager may void ANY entry; an ordinary
    staffer may only void their OWN mistake (matching record_owner_
    consumption's own recording gate — same shift requirement). Blocked
    once the draw has already been settled (paid for) — undoing a paid
    entry is a bigger reversal decision than a same-day typo fix, and goes
    through the owner directly rather than this endpoint."""
    up = _get_up(request)
    if not up or not up.business:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    business = up.business
    txn = get_object_or_404(
        Transaction, id=txn_id, business=business, type='OwnerConsumption',
    )
    if txn.invoice_no == '[OC-VOID]':
        return JsonResponse({'ok': True, 'already_void': True})
    if txn.settlements.exists():
        return JsonResponse(
            {'ok': False, 'error': 'Hii tayari imelipwa — mwombe mmiliki afute badiliko hili moja kwa moja.'},
            status=409,
        )

    if not up.is_owner_or_manager:
        if txn.recorded_by_id != request.user.id:
            return JsonResponse({'ok': False, 'error': 'Unaweza kufuta rekodi zako mwenyewe pekee.'}, status=403)
        shift = get_active_staff_shift(up, business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa. Fungua shift kwanza.'}, status=403)

    reason = (request.POST.get('reason') or 'Hitilafu — sababu haikuelezwa').strip()[:200]
    old_qty = txn.qty
    txn.qty = Decimal('0')
    txn.invoice_no = '[OC-VOID]'
    txn.recipient = f"{txn.recipient} — FUTWA: {reason}"[:200]
    txn.save(update_fields=['qty', 'invoice_no', 'recipient'])

    actor_name = request.user.get_full_name() or request.user.username
    new_balance = txn.item.current_balance()
    msg = (
        f"{actor_name} amefuta rekodi ya 'Mmiliki Alichukua' — {txn.item.description} "
        f"({abs(old_qty)} {txn.item.unit}) — {reason}. Stock imerudishwa (sasa: {new_balance})."
    )
    _notify_owner_consumption_change(
        business, request.user, '↺ Mmiliki Alichukua — Imefutwa', msg,
        '/stock/owner-consumption/list/', also_notify_user=txn.recorded_by,
    )
    return JsonResponse({'ok': True, 'message': msg, 'new_balance': str(new_balance)})


@login_required
@require_POST
def settle_owner_consumption(request, txn_id):
    """Record that the owner has paid the business back for a Mmiliki
    Alichukua draw — Roy's second ask: "no way for the owner to pay for
    what he was given." Owner/manager only (a real financial-settlement
    decision, same tier as every other correction that turns into real
    revenue in this app — e.g. accepted stock-take variance). Creates an
    ordinary qty=0 Issue Transaction (no additional stock leaves the
    shelf — it already left at consumption time) carrying the paid amount
    as sale_amount, so it flows through every existing cash/mpesa
    aggregate (shift reconciliation, the continuous till, daily revenue,
    P&L) exactly like any other sale, with zero new plumbing needed —
    same qty=0 re-billing pattern already used throughout this app
    (BarTabEntry.split_paid_unpaid_locked, apply_checkout_partial_credit_
    locked, etc.). settles_transaction links it back to the original draw;
    that link IS the paid flag (see Transaction.settles_transaction's own
    docstring) — settling twice is refused outright."""
    up = _get_up(request)
    if not up or not up.business or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    business = up.business
    txn = get_object_or_404(
        Transaction, id=txn_id, business=business, type='OwnerConsumption',
    )
    if txn.invoice_no == '[OC-VOID]':
        return JsonResponse({'ok': False, 'error': 'Rekodi hii imefutwa — hakuna cha kulipa.'}, status=400)
    if txn.settlements.exists():
        return JsonResponse({'ok': False, 'error': 'Tayari imelipwa.'}, status=409)

    amount_raw = (request.POST.get('amount') or '').strip()
    pay_method = (request.POST.get('payment_method') or 'cash').strip()
    if pay_method not in ('cash', 'mpesa'):
        pay_method = 'cash'
    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            return JsonResponse({'ok': False, 'error': 'Kiasi lazima kiwe zaidi ya 0.'}, status=400)
    except (InvalidOperation, ValueError):
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    settlement = Transaction.objects.create(
        business=business, item=txn.item, type='Issue',
        qty=Decimal('0'), sale_amount=amount, payment_method=pay_method,
        recipient='Mmiliki (malipo ya Alichukua)', recorded_by=request.user,
        settles_transaction=txn,
    )

    actor_name = request.user.get_full_name() or request.user.username
    msg = (
        f"{actor_name} amerekodi mmiliki amelipa KES {amount:,.0f} ({pay_method}) "
        f"kwa {txn.item.description} aliyochukua."
    )
    _notify_owner_consumption_change(
        business, request.user, '💰 Mmiliki Alichukua — Imelipwa', msg,
        '/stock/owner-consumption/list/', also_notify_user=txn.recorded_by,
    )
    return JsonResponse({
        'ok': True, 'message': msg, 'amount': str(amount), 'payment_method': pay_method,
    })
