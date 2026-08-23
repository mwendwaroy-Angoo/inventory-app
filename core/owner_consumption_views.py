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
    price_str = request.POST.get('price', '').strip()

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

    # 2026-08-12 live request (Roy) — "he gets to see every item, the price,
    # the quantities": a draw previously captured NO price at all. Defaults
    # to the item's own selling_price × qty (editable — the modal sends
    # whatever the staffer confirmed/edited), stored on sale_amount the same
    # way every other sale records its value. Never touches revenue() —
    # that gates on type != 'Issue' first, so this stays invisible to every
    # revenue/analytics aggregate exactly as before.
    try:
        price = Decimal(price_str) if price_str else (item.selling_price or Decimal('0')) * qty
        if price < 0:
            price = Decimal('0')
    except Exception:
        price = (item.selling_price or Decimal('0')) * qty

    # 2026-08-16 live request (Roy): "bar board has no backdating, same as
    # mmiliki alichukua modal" — a draw recorded today for something the
    # owner actually took on an earlier day (a catch-up entry, same class
    # of gap already fixed for Quick Sell/Kitchen/Bar checkout) had no way
    # to land on the right day. Mirrors those same surfaces' own parsing.
    backdated_at = None
    _bd_raw = (request.POST.get('backdated_at') or '').strip()
    if _bd_raw:
        try:
            from datetime import datetime as _dt
            _naive = _dt.strptime(_bd_raw, '%Y-%m-%dT%H:%M')
            backdated_at = timezone.make_aware(_naive, timezone.get_current_timezone())
        except Exception:
            backdated_at = None

    # 2026-08-23 live request (Roy): "set an amount and a window period limit
    # setting to enhance owner discipline ... if the limit is reached and a
    # customer takes a drink in his name the system rejects automatically."
    # A HARD block, per his explicit choice — not a warning. Only ever bites
    # when that owner has actually configured a ceiling; with none set (the
    # default) nothing changes at all.
    from core.owner_limits import (
        check_owner_consumption_limit, notify_owner_limit_reached, resolve_draw_owner,
    )
    draw_owner = resolve_draw_owner(business, request.POST.get('owner_profile_id'))
    allowed, limit_info = check_owner_consumption_limit(business, draw_owner, price)
    if not allowed:
        notify_owner_limit_reached(business, draw_owner, limit_info, item_name=item.description)
        return JsonResponse({
            'ok': False,
            'limit_reached': True,
            'error': (
                f"Kikomo cha matumizi kimefikiwa. Kikomo ({limit_info['window_label']}): "
                f"KES {limit_info['limit']:,.0f} — umeshatumia KES {limit_info['used']:,.0f}, "
                f"iliyobaki KES {limit_info['remaining']:,.0f}. "
                f"Lipa deni la ulichochukua ili kikomo kifunguke tena."
            ),
        }, status=403)

    Transaction.objects.create(
        business=business,
        item=item,
        type='OwnerConsumption',
        qty=-qty,
        sale_amount=price,
        date=(timezone.localtime(backdated_at).date() if backdated_at else timezone.localdate()),
        recorded_by=request.user,
        consumed_by=draw_owner,
        recipient=note or 'Mmiliki',
        payment_method='',
        **({'created_at': backdated_at} if backdated_at else {}),
    )

    new_balance = item.current_balance()
    return JsonResponse({
        'ok': True,
        'item': item.description,
        'qty': str(qty),
        'price': str(price),
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
    unpaid_rows = []
    paid_rows = []
    total_unpaid = Decimal('0')
    total_paid_this_month = Decimal('0')
    this_month = timezone.localdate().replace(day=1)
    for t in txns:
        settlement = t.settlements.first()
        row = {
            'txn': t,
            'settlement': settlement,
            'is_own': t.recorded_by_id == request.user.id,
        }
        rows.append(row)
        if settlement:
            paid_rows.append(row)
            if timezone.localtime(settlement.created_at).date() >= this_month:
                total_paid_this_month += settlement.sale_amount or Decimal('0')
        else:
            unpaid_rows.append(row)
            total_unpaid += t.sale_amount or Decimal('0')

    # 2026-08-12 live request (Roy) — "the ability to accept or reject" for
    # a tab item/debt balance a staff member (or the owner himself) has
    # proposed reclassifying as the owner's own draw. Owner/manager only,
    # since this is a claim about who's actually financially responsible.
    from .models import OwnerConsumptionTransferRequest as _OCTR
    pending_to_owner = []
    linked_customers = []
    if getattr(up, 'is_owner_or_manager', False):
        pending_to_owner = list(
            _OCTR.objects.filter(
                business=business, direction=_OCTR.DIRECTION_TO_OWNER, status='PENDING',
            ).select_related('source_txn__item', 'requested_by').order_by('-requested_at')
        )
        # 2026-08-13 live request (Roy) — "give it more detailing and
        # practicality": surface which Customer records are currently
        # linked as the owner right from this page too, not only on each
        # customer's own debt profile, with their live outstanding figure
        # so a resync-worthy customer is obvious without hunting for them.
        from .models import Customer
        from .debt_views import _get_customer_debt_data
        for cust in Customer.objects.filter(business=business, is_owner_alias=True).order_by('name'):
            data = _get_customer_debt_data(cust, business, scope='all')
            linked_customers.append({'customer': cust, 'outstanding': data['outstanding']})

    return render(request, 'core/owner_consumption_list.html', {
        'rows': rows,
        'unpaid_rows': unpaid_rows,
        'paid_rows': paid_rows,
        'total_unpaid': total_unpaid,
        'total_paid_this_month': total_paid_this_month,
        'pending_to_owner': pending_to_owner,
        'linked_customers': linked_customers,
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


# ── Transfers between a customer's tab/debt and the owner's own ledger ─────
# 2026-08-12 live request (Roy): the owner needs his own accountability
# section where an item/bill can move to or from a real customer's tab/debt,
# with an accept/reject step — see OwnerConsumptionTransferRequest's own
# docstring in core/models.py for the full design reasoning.

@login_required
@require_POST
def propose_transfer_to_owner(request):
    """Staff (or the owner himself) proposes that a customer's unpaid tab
    item(s) or debt balance is actually the owner's own — needs the
    owner's accept before anything moves. Any staff with an open shift may
    propose (matches Roy's "initiated by the staffs or him of course");
    owner/manager exempt from the shift requirement, same as recording a
    draw.

    2026-08-13: widened to accept multiple items in one call (txn_ids,
    comma-separated) alongside the original single txn_id — used by the
    customer-link/resync action and the cross-customer search/bulk-transfer
    page, on top of the original one-row-at-a-time button this endpoint
    already served."""
    up = _get_up(request)
    if not up or not up.business:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)
    if not up.is_owner_or_manager:
        shift = get_active_staff_shift(up, up.business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa. Fungua shift kwanza.'}, status=403)

    txn_ids_raw = (request.POST.get('txn_ids') or request.POST.get('txn_id') or '').strip()
    txn_ids = [t.strip() for t in txn_ids_raw.split(',') if t.strip()]
    note = (request.POST.get('note') or '').strip()
    if not txn_ids:
        return JsonResponse({'ok': False, 'error': 'Taja transaction.'}, status=400)

    from .models import OwnerConsumptionTransferRequest as _OCTR
    try:
        reqs = _OCTR.propose_to_owner_locked(txn_ids, up.business, request.user, note=note)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    actor_name = request.user.get_full_name() or request.user.username
    total = sum(float(r.source_txn.sale_amount or 0) for r in reqs)

    # 2026-08-23 (Roy): "the same mail notifications should be applicable to
    # tab transfers to the owner's name" — the owner must never find out
    # after the fact that a bill landed against him. Fire-and-forget; a
    # notification failing must not affect the proposal itself.
    try:
        from core.owner_limits import notify_owner_of_transfer_to_their_name
        _source_name = reqs[0].source_txn.recipient if reqs else ''
        for _owner in up.business.users.filter(role='owner').select_related('user'):
            notify_owner_of_transfer_to_their_name(
                up.business, _owner, _source_name or actor_name, total,
            )
    except Exception:
        logger.exception('Owner transfer email fan-out failed')

    if len(reqs) == 1:
        req = reqs[0]
        item_name = req.source_txn.item.description if req.source_txn.item_id else ''
        msg = (
            f"{actor_name} anapendekeza kwamba {req.source_txn.recipient} — "
            f"{item_name} (KES {req.source_txn.sale_amount or 0:,.0f}) ni ya mmiliki. "
            f"Inasubiri uamuzi wako."
        )
    else:
        names = sorted({r.source_txn.recipient for r in reqs if r.source_txn.recipient})
        who = ', '.join(names[:3]) + (f' na wengine {len(names) - 3}' if len(names) > 3 else '')
        msg = (
            f"{actor_name} anapendekeza vitu {len(reqs)} (KES {total:,.0f}) — kutoka {who} — "
            f"ni vya mmiliki. Inasubiri uamuzi wako."
        )
    _notify_owner_consumption_change(
        up.business, request.user, '🏠 Ombi — Hamishia kwa Mmiliki', msg,
        '/stock/owner-consumption/list/',
    )
    ok_msg = 'Ombi limetumwa kwa mmiliki.' if len(reqs) == 1 else f'Ombi la vitu {len(reqs)} (KES {total:,.0f}) limetumwa kwa mmiliki.'
    return JsonResponse({'ok': True, 'message': ok_msg, 'request_ids': [r.id for r in reqs]})


@login_required
@require_POST
def propose_transfer_to_owner_partial(request):
    """2026-08-16 live request (Roy): customer pays PART of an item/debt
    themselves right now, remainder goes to the owner's ledger — needs his
    accept before anything moves. Same permission tier as the whole-item
    version (any staff with an open shift; owner/manager exempt)."""
    up = _get_up(request)
    if not up or not up.business:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)
    if not up.is_owner_or_manager:
        shift = get_active_staff_shift(up, up.business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa. Fungua shift kwanza.'}, status=403)

    txn_id = (request.POST.get('txn_id') or '').strip()
    paid_method = (request.POST.get('paid_method') or '').strip()
    note = (request.POST.get('note') or '').strip()
    if not txn_id:
        return JsonResponse({'ok': False, 'error': 'Taja transaction.'}, status=400)
    try:
        paid_amount = Decimal(str(request.POST.get('paid_amount') or '0'))
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    from .models import OwnerConsumptionTransferRequest as _OCTR
    try:
        req = _OCTR.propose_to_owner_partial_locked(
            txn_id, up.business, paid_amount, paid_method, request.user, note=note,
        )
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    actor_name = request.user.get_full_name() or request.user.username
    remainder = float(req.source_txn.sale_amount or 0)
    item_name = req.source_txn.item.description if req.source_txn.item_id else ''
    msg = (
        f"{actor_name} anapendekeza kwamba {req.source_txn.recipient} — {item_name} "
        f"— alishalipa sehemu (KES {float(paid_amount):,.0f}), na iliyobaki "
        f"(KES {remainder:,.0f}) ni ya mmiliki. Inasubiri uamuzi wako."
    )
    _notify_owner_consumption_change(
        up.business, request.user, '🏠 Ombi — Sehemu Hamishia kwa Mmiliki', msg,
        '/stock/owner-consumption/list/',
    )
    return JsonResponse({
        'ok': True,
        'message': f'Amelipa KES {float(paid_amount):,.0f} — KES {remainder:,.0f} iliyobaki imetumwa kwa mmiliki.',
        'request_id': req.id,
    })


@login_required
@require_POST
def propose_transfer_from_owner(request):
    """Owner hands an item (or whole outstanding bill) to a customer
    willing to cover it — creates a PENDING request per item, batched for
    a whole-bill transfer. Any staff with an open shift may propose on the
    owner's behalf (he confirms verbally, matching the to-owner direction's
    same permission tier); accept/reject happens via respond_owner_transfer."""
    up = _get_up(request)
    if not up or not up.business:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)
    if not up.is_owner_or_manager:
        shift = get_active_staff_shift(up, up.business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa. Fungua shift kwanza.'}, status=403)

    dest_name = (request.POST.get('dest_customer_name') or '').strip()
    if not dest_name:
        return JsonResponse({'ok': False, 'error': 'Taja jina la mteja.'}, status=400)
    note = (request.POST.get('note') or '').strip()

    txn_ids_raw = (request.POST.get('txn_ids') or request.POST.get('txn_id') or '').strip()
    txn_ids = [t.strip() for t in txn_ids_raw.split(',') if t.strip()]
    if not txn_ids:
        return JsonResponse({'ok': False, 'error': 'Taja item(s) za kuhamisha.'}, status=400)

    from .models import OwnerConsumptionTransferRequest as _OCTR
    try:
        requests_created = _OCTR.propose_from_owner_locked(
            txn_ids, up.business, dest_name, request.user, note=note,
        )
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    actor_name = request.user.get_full_name() or request.user.username
    total = sum(float(r.source_txn.sale_amount or 0) for r in requests_created)
    msg = (
        f"{actor_name} anapendekeza {dest_name} alipe KES {total:,.0f} "
        f"iliyokuwa ya mmiliki. Inasubiri uamuzi."
    )
    _notify_owner_consumption_change(
        up.business, request.user, f'🔀 Ombi — Hamishia kwa {dest_name}', msg,
        '/stock/owner-consumption/list/',
    )
    return JsonResponse({
        'ok': True, 'message': f'Ombi limetumwa — linasubiri uthibitisho wa {dest_name}.',
        'request_ids': [r.id for r in requests_created],
    })


@login_required
@require_POST
def respond_owner_transfer(request, request_id):
    """Owner/manager accepts or rejects a pending transfer, either
    direction. (The destination customer's own public accept/reject page
    for a from_owner transfer is a documented fast-follow — for now staff/
    owner confirm on the customer's behalf, same as the split-transfer
    feature already allows.)"""
    up = _get_up(request)
    if not up or not up.business or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    from .models import OwnerConsumptionTransferRequest as _OCTR
    req = get_object_or_404(_OCTR, id=request_id, business=up.business, status='PENDING')
    action = (request.POST.get('action') or '').strip()

    if action == 'accept':
        req.accept()
        msg = 'Ombi limekubaliwa.'
    elif action == 'reject':
        req.reject()
        msg = 'Ombi limekataliwa — hakuna kilichobadilika.'
    else:
        return JsonResponse({'ok': False, 'error': 'Invalid action.'}, status=400)

    actor_name = request.user.get_full_name() or request.user.username
    _notify_owner_consumption_change(
        up.business, request.user, '🏠 Uamuzi wa Uhamisho', f"{actor_name}: {msg}",
        '/stock/owner-consumption/list/',
    )
    return JsonResponse({'ok': True, 'message': msg})
