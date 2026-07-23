"""
Bar & Club Module — Keg lifecycle views and Bar Board.

Sprint 2: board API, receive/tap/weigh/discard, Bar Board HTML + sell (cash/mpesa).
Sprint 3: tab sell path — BarTab CRUD, tabs drawer, tick-to-pay, convert-to-debt.
Sprint 4: shift handover.
"""
import json
import logging
from datetime import date as date_type, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import keg_metrics
from .models import BarCupLog, BarTab, BarTabEntry, Customer, Item, ItemPortionPreset, KegBarrel, KegWeightReading, Payment, PettyCash, Receipt, Shift, TabTransferRequest, Transaction

logger = logging.getLogger(__name__)


def _get_up(request):
    from .views import get_user_profile
    return get_user_profile(request)


def _allowed_tab_sources(up):
    """Set of BarTab.source values this staffer may act on, per the Station
    Scoping Principle (CLAUDE.md).

    tabs_list() (the read/GET side) already scopes correctly via this same
    _station_scope() helper — bar-audit finding, 2026-07-19: every WRITE
    endpoint on tabs (tick_entry, settle_tab, void_tab, convert_tab_to_debt,
    bulk_convert_tabs_to_debt, update_tab_name, update_tab_phone) filtered
    tabs by business only, with no station check at all. A kitchen-only
    staffer (no can_access_bar) could act directly on a bar tab via the API —
    settle it, void it, convert it to debt — even though the UI never shows
    them a bar tab, because "the template doesn't render the button" is not
    the same as "the endpoint is gated." Owner/manager/cross-access staff see
    both.

    'qs' (Quick Sell) is ALWAYS included, unconditionally — found 2026-07-23
    from a live report ("Geuza Deni gives a network error"): tabs_list()'s
    qs context branch already returns 'qs' tabs completely unrestricted (no
    bar/kitchen station filtering applied — qs tabs have no station concept
    at all), but this helper originally excluded 'qs' from the allowed set
    entirely rather than granting it unconditionally. Since convert_tab_to_
    debt, update_tab_name, update_tab_phone, and tick_entry all filter their
    object lookup directly on tab.source (not per-entry, unlike settle_tab)
    against this set, excluding 'qs' meant EVERY Quick Sell tab 404'd on
    every one of those endpoints, for every user including the owner — not
    an edge case, a universal failure of "→ Deni" / rename / save-phone for
    every Quick Sell tab. The original comment's reasoning ("no station
    concept") was right, but the conclusion should have been "always allow",
    not "always deny".
    """
    from .views import _station_scope
    show_bar, show_kitchen = _station_scope(up)
    allowed = {'qs'}
    if show_bar:
        allowed.add('bar')
    if show_kitchen:
        allowed.add('kitchen')
    return allowed


def _cancel_pending_transfers_for_tab(tab):
    """Auto-cancel any PENDING TabTransferRequest whose entry lives on `tab`,
    when that tab is about to leave the ordinary open-tab lifecycle (voided
    or converted to debt) — a pending split-bill request against an entry
    that's no longer sitting on a normal open tab doesn't make sense anymore.
    Inverse-action safeguard for BarTabEntry.split_and_transfer_locked()."""
    for tfr in TabTransferRequest.objects.filter(source_tab=tab, status='PENDING'):
        try:
            tfr.cancel()
        except Exception:
            logger.exception('Failed to auto-cancel TabTransferRequest %s', tfr.id)


# ── Board API ─────────────────────────────────────────────────────────────────

@login_required
def bar_board_api(request):
    """JSON: keg tile data for the bar board."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'kegs': [], 'can_receive': False})

    business = up.business

    keg_items = (
        Item.objects
        .filter(store__business=business, is_keg=True)
        .prefetch_related('portion_presets', 'keg_barrels')
        .order_by('description')
    )

    kegs = []
    for it in keg_items:
        all_barrels = list(it.keg_barrels.filter(business=business))
        tapped = [b for b in all_barrels if b.status == 'TAPPED']
        sealed = [b for b in all_barrels if b.status == 'SEALED']

        primary = tapped[0] if tapped else None
        # ordering on KegBarrel is -received_on, -id so last element is oldest
        next_sealed = sealed[-1] if sealed else None

        presets = [
            {
                'id': p.id,
                'label': p.label,
                'price': float(p.price),
                'quantity_consumed': float(p.quantity_consumed),
            }
            for p in it.portion_presets.all().order_by('display_order', 'id')
        ]

        kegs.append({
            'item_id': it.id,
            'name': it.description,
            'unit': it.unit or 'Ml',
            'keg_type': it.keg_type or '',
            'presets': presets,
            'open_barrels': len(tapped),
            'sealed_barrels': len(sealed),
            'tapped_barrel_id': primary.id if primary else None,
            'next_sealed_barrel_id': next_sealed.id if next_sealed else None,
            'remaining': float(primary.remaining_envelope()) if primary else 0.0,
            'target_open': float(primary.target_revenue) if primary else 0.0,
            'revenue_collected': float(primary.revenue_collected) if primary else 0.0,
            'latest_weight_kg': round(primary.latest_weight(), 2) if primary else 0.0,
            'days_tapped': primary.age_days() if primary else 0,
            'stale': primary.is_stale() if primary else False,
            'has_history': bool(all_barrels),
            'cost_price': float(it.cost_price or 0),
            # Per-barrel data for the edit modal
            'net_liters':           round(primary.net_volume_l, 1) if primary else 0.0,
            'tapped_barrel_tare':   float(primary.tare_weight_kg) if primary else 0.0,
            'tapped_barrel_cost':   float(primary.cost_price) if primary else 0.0,
            'tapped_barrel_target': float(primary.target_revenue) if primary else 0.0,
            'next_sealed_cost':     float(next_sealed.cost_price) if next_sealed else 0.0,
            'next_sealed_target':   float(next_sealed.target_revenue) if next_sealed else 0.0,
            'next_sealed_gross':    float(next_sealed.gross_weight_kg) if next_sealed else 0.0,
            'next_sealed_tare':     float(next_sealed.tare_weight_kg) if next_sealed else 0.0,
            # K5.A — envelope + depletion control flags
            'envelope_reached':    (float(primary.remaining_envelope()) <= 0) if primary else False,
        })

    open_tabs_qs = BarTab.objects.filter(business=business, status='OPEN')

    # Active waitresses — those who placed at least one order today
    from .models import TableOrder as _TO
    today = timezone.localdate()
    active_w = []
    seen_ids = set()
    for order in _TO.objects.filter(
        business=business, created_at__date=today
    ).select_related('waitress').order_by('waitress_id'):
        uid = order.waitress_id
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        pending = _TO.objects.filter(
            business=business, waitress_id=uid,
            created_at__date=today, status__in=['PENDING', 'ACCEPTED', 'READY']
        ).count()
        total = _TO.objects.filter(
            business=business, waitress_id=uid, created_at__date=today
        ).count()
        w = order.waitress
        active_w.append({
            'name':    w.get_full_name() or w.username,
            'pending': pending,
            'total':   total,
        })

    cup_pool = keg_metrics.business_cup_pool(business)

    return JsonResponse({
        'kegs': kegs,
        'can_receive': bool(getattr(up, 'is_owner_or_manager', False)),
        'open_tabs': open_tabs_qs.count(),
        'open_tab_names': list(open_tabs_qs.values_list('customer_name', flat=True).distinct()),
        'active_waitresses': active_w,
        'weighs_kegs': bool(getattr(business, 'weighs_kegs', False)),
        'block_sales_past_target': bool(getattr(business, 'block_sales_past_target', False)),
        'cup_pool': cup_pool,
    })


# ── Bar Board HTML + sell ─────────────────────────────────────────────────────

@login_required
def bar_board(request):
    """Main bar board — GET renders the page, POST processes a keg cart sale."""
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    is_owner = bool(getattr(up, 'is_owner_or_manager', False))

    # Kitchen staff are bar-board-blocked unless the owner has granted access
    if not is_owner and getattr(up, 'is_kitchen_staff', False):
        if not getattr(up, 'can_access_bar', False):
            return redirect('kitchen_board')

    success_data = None

    # Pass shift status to template so tiles can be greyed out immediately on load
    if is_owner:
        has_my_shift = True
    else:
        from .models import Shift as _ShiftCheck
        has_my_shift = _ShiftCheck.objects.filter(
            business=business, status='OPEN', staff=request.user
        ).exists()

    if request.method == 'POST':
        # Shift enforcement — staff must have personally opened an active shift
        if not is_owner:
            from .models import Shift as _Shift
            from django.contrib import messages as _msg
            my_shift = _Shift.objects.filter(
                business=business,
                status='OPEN',
                staff=request.user,
            ).first()
            if not my_shift:
                # Check if there's any open shift so we can give a specific message
                any_shift = _Shift.objects.filter(
                    business=business, status='OPEN'
                ).first()
                if any_shift:
                    owner_name = any_shift.staff.get_full_name() or any_shift.staff.username
                    _msg.error(
                        request,
                        f'Shift imefunguliwa na {owner_name}. '
                        f'Fungua shift yako mwenyewe kwanza kabla ya kuuza.'
                    )
                else:
                    _msg.error(
                        request,
                        'Hakuna shift iliyofunguliwa. Fungua shift kwanza kabla ya kuuza.'
                    )
                return redirect('bar_board')

        # Server-side double-submit backstop — see core/idempotency.py. Client-side
        # guards only cover a second click on the same live page; this catches real
        # duplicate requests (slow-network retry, back-button resubmission of the
        # real <form> this view is posted from).
        from core.idempotency import claim_checkout_token
        idem_token = (request.POST.get('idempotency_token') or '').strip()
        if not claim_checkout_token(business.id, idem_token):
            from django.contrib import messages as _msg
            _msg.info(request, 'Mauzo haya tayari yamehifadhiwa.')
            return redirect('bar_board')

        cart_json = request.POST.get('keg_cart', '[]')
        payment_method = request.POST.get('payment_method', 'cash')
        tab_customer = (request.POST.get('tab_customer') or '').strip()
        tab_server = (request.POST.get('tab_server') or '').strip()
        merge_tab_id_raw = (request.POST.get('merge_tab_id') or '').strip()
        merge_tab_id = int(merge_tab_id_raw) if merge_tab_id_raw.isdigit() else None

        try:
            cart = json.loads(cart_json)
        except Exception:
            cart = []

        # Resolve tab for tab-payment sales
        active_tab = None
        linked_customer = None  # always initialised — avoids NameError in merge-tab receipt block
        tab_phone = (request.POST.get('tab_phone') or '').strip()
        if payment_method == 'tab':
            if merge_tab_id:
                # Cross-counter merge: bar items added to an existing kitchen food tab
                try:
                    active_tab = BarTab.objects.get(id=merge_tab_id, business=business, status='OPEN')
                    tab_customer = active_tab.customer_name
                    linked_customer = active_tab.customer  # carry FK into receipt meta
                except BarTab.DoesNotExist:
                    from django.http import JsonResponse as _JR
                    return _JR({'ok': False, 'error': 'Tab haikupatikana au imefungwa tayari.'}, status=400)
            elif tab_customer:
                # Resolve or create the Customer first (filter().first() — no unique_together on
                # Customer(business, name), get_or_create raises MultipleObjectsReturned in prod).
                linked_customer = Customer.objects.filter(
                    business=business, name__iexact=tab_customer,
                ).first()
                if linked_customer is None:
                    linked_customer = Customer.objects.create(
                        business=business, name=tab_customer, phone=tab_phone,
                        credit_approved=True,
                    )
                elif tab_phone and not (linked_customer.phone or '').strip():
                    linked_customer.phone = tab_phone
                    linked_customer.save(update_fields=['phone'])

                active_tab = BarTab.objects.filter(
                    business=business,
                    customer_name__iexact=tab_customer,
                    status='OPEN',
                    source='bar',  # bar board only manages bar tabs; cross-counter uses merge_tab_id
                ).first()
                if not active_tab:
                    first_barrel = None
                    for entry in cart:
                        try:
                            bid = int(entry.get('barrel_id', 0))
                            first_barrel = KegBarrel.objects.filter(id=bid, business=business).first()
                            if first_barrel:
                                break
                        except (TypeError, ValueError):
                            pass
                    active_tab = BarTab.create_with_credentials(
                        business=business,
                        store=first_barrel.store if first_barrel else None,
                        customer_name=tab_customer,
                        customer=linked_customer,
                        server_name=tab_server,
                        served_by=request.user if not tab_server else None,
                    )
            else:
                # Anonymous tab — the busy-counter case: staff has no time to type a
                # customer name during peak demand. The wall-QR + PIN system exists
                # specifically so the customer can identify themselves later, so the
                # tab must still open. Always create a NEW tab here — never search
                # for an existing tab by blank name, which would silently merge two
                # unrelated anonymous customers' bills together (bar-audit follow-up
                # finding, 2026-07-19 — the previous `and tab_customer` gate meant
                # this whole branch never ran, so the sale fell through with
                # payment_method='tab' literally saved onto the Transaction — not a
                # recognized value — and no tab, no PIN, no way to ever collect or
                # look the sale up again).
                first_barrel = None
                for entry in cart:
                    try:
                        bid = int(entry.get('barrel_id', 0))
                        first_barrel = KegBarrel.objects.filter(id=bid, business=business).first()
                        if first_barrel:
                            break
                    except (TypeError, ValueError):
                        pass
                active_tab = BarTab.create_with_credentials(
                    business=business,
                    store=first_barrel.store if first_barrel else None,
                    customer_name='',
                    server_name=tab_server,
                    served_by=request.user if not tab_server else None,
                )
                active_tab.customer_name = f'Tab #{active_tab.id}'
                active_tab.save(update_fields=['customer_name'])
                tab_customer = active_tab.customer_name

        receipt_lines = []
        total_revenue = Decimal('0')

        for entry in cart:
            try:
                barrel_id = int(entry.get('barrel_id', 0))
                preset_id = int(entry.get('preset_id', 0))
                qty = max(1, int(entry.get('qty', 1)))
            except (TypeError, ValueError):
                continue

            barrel = (
                KegBarrel.objects
                .filter(id=barrel_id, business=business, status='TAPPED')
                .select_related('item')
                .first()
            )
            if not barrel:
                continue

            preset = ItemPortionPreset.objects.filter(
                id=preset_id, item=barrel.item
            ).first()
            if not preset:
                continue

            try:
                KegBarrel.record_sale_locked(
                    barrel.id, business, preset, qty, payment_method,
                    request.user, tab=active_tab,
                )
            except KegBarrel.DoesNotExist:
                continue  # depleted between fetch and lock
            amount = Decimal(str(float(preset.price) * qty))
            total_revenue += amount
            receipt_lines.append({
                'name': f"{barrel.item.description} — {preset.label} ×{qty}",
                'subtotal': float(amount),
                'barrel_id': barrel.id,
            })

        if receipt_lines:
            receipt_token = None
            receipt_number = None
            receipt_id = None
            master_rcpt = None  # tracked outside try so SMS logic can read it
            try:
                receipt_pm = payment_method
                rcpt_meta = {}
                if payment_method == 'tab' and active_tab:
                    if tab_customer and linked_customer:
                        try:
                            from core.debt_views import _build_credit_receipt_meta
                            rcpt_meta = _build_credit_receipt_meta(business, linked_customer, 'bar')
                            # Tab entries aren't debt yet — suppress the outstanding block
                            rcpt_meta['outstanding'] = 0.0
                        except Exception:
                            pass
                    # Always embed tab_id regardless of whether a Customer record exists.
                    # /r/<token>/ uses this to serve live tab updates to the customer —
                    # they scan once and see all subsequent rounds in real-time.
                    rcpt_meta['tab_id'] = active_tab.id

                # For tab sales: resolve the master receipt so the customer keeps one URL,
                # regardless of which counter (bar/kitchen/Quick Sell) rings up their next
                # item. Single source of truth — see core/tab_receipts.py.
                master_rcpt = None
                _is_freshly_linked = False
                if payment_method == 'tab' and active_tab:
                    try:
                        from core.tab_receipts import resolve_master_receipt
                        master_rcpt, _is_freshly_linked = resolve_master_receipt(business, active_tab)
                    except Exception:
                        logger.exception(
                            'bar_board: master receipt resolution failed business=%s',
                            business.id,
                        )

                if master_rcpt:
                    # Reuse existing master receipt — customer's QR stays the same
                    receipt_token = master_rcpt.token
                    receipt_number = master_rcpt.receipt_number
                    receipt_id = master_rcpt.id
                else:
                    rcpt = Receipt.issue(
                        business=business,
                        lines=receipt_lines,
                        payment_method=receipt_pm,
                        user=request.user,
                        customer_name=tab_customer if payment_method == 'tab' else '',
                        meta=rcpt_meta,
                    )
                    receipt_token = rcpt.token
                    receipt_number = rcpt.receipt_number
                    receipt_id = rcpt.id
            except Exception:
                logger.exception(
                    "Receipt.issue failed in bar_board (user=%s business=%s payment=%s)",
                    request.user.username, business.id, payment_method,
                )

            receipt_url = (
                request.build_absolute_uri(f'/r/{receipt_token}/')
                if receipt_token else None
            )
            success_data = {
                'lines': receipt_lines,
                'total': float(total_revenue),
                'payment_method': payment_method,
                'tab_customer': tab_customer if active_tab else '',
                'timestamp': timezone.localtime(timezone.now()).strftime('%H:%M'),
                'receipt_token': receipt_token,
                'receipt_number': receipt_number,
                'receipt_url': receipt_url,
                'receipt_id': receipt_id,
            }

            # SMS: brand-new bar tab receipt (customer has no receipt yet)
            if payment_method == 'tab' and master_rcpt is None and receipt_url and active_tab:
                try:
                    from .notifications import normalize_ke_phone, send_sms_notification
                    _sms_phone_raw = tab_phone or (linked_customer.phone if linked_customer else '')
                    _sms_phone = normalize_ke_phone(_sms_phone_raw) if _sms_phone_raw else ''
                    if _sms_phone:
                        _tab_total = float(active_tab.total()) if active_tab else float(total_revenue)
                        _sms = (
                            f"Habari {tab_customer},\n"
                            f"{business.name}: Tab imefunguliwa — "
                            f"KES {_tab_total:,.0f}.\n"
                            f"Angalia risiti yako: {receipt_url}"
                        )
                        send_sms_notification(_sms, _sms_phone)
                except Exception:
                    logger.exception(
                        "Tab open SMS failed in bar_board (business=%s)", business.id
                    )

            # SMS: bar item freshly linked into an existing tab/receipt from another
            # counter (kitchen or Quick Sell) — update the customer.
            if payment_method == 'tab' and _is_freshly_linked and receipt_url and active_tab:
                try:
                    from .notifications import normalize_ke_phone, send_sms_notification
                    _sms_phone_link = normalize_ke_phone(
                        tab_phone or (linked_customer.phone if linked_customer else '') or ''
                    ) if (tab_phone or linked_customer) else ''
                    if _sms_phone_link:
                        _sms_link = (
                            f"Habari {tab_customer},\n"
                            f"{business.name}: Kinywaji kimeongezwa kwenye tab yako.\n"
                            f"Angalia risiti iliyosasishwa: {receipt_url}"
                        )
                        send_sms_notification(_sms_link, _sms_phone_link)
                except Exception:
                    logger.exception(
                        "Tab cross-link SMS failed in bar_board (business=%s)", business.id
                    )

            # SMS notification when bar items are merged into an existing kitchen food tab
            if merge_tab_id and active_tab:
                try:
                    from .notifications import normalize_ke_phone, send_sms_notification
                    phone = None
                    if active_tab.customer:
                        phone = normalize_ke_phone(active_tab.customer.phone or '')
                    if phone:
                        new_total = float(active_tab.total())
                        _src = active_tab.source
                        counter_label = 'Kitchen' if _src == 'kitchen' else ('Quick Sell' if _src == 'qs' else 'Bar')
                        sms_msg = (
                            f"Habari {active_tab.customer_name},\n"
                            f"{business.name} imeongeza KES {float(total_revenue):,.0f} "
                            f"kwenye tab yako ({counter_label}).\n"
                            f"Jumla sasa: KES {new_total:,.0f}"
                        )
                        send_sms_notification(sms_msg, phone)
                except Exception:
                    logger.exception(
                        "Tab merge SMS failed in bar_board (business=%s)", business.id
                    )

    try:
        non_keg_items = list(
            Item.objects
            .filter(store__business=business, is_keg=False, store__is_kitchen=False)
            .order_by('description')
            .values('id', 'description', 'unit', 'selling_price')
        )
    except Exception:
        logger.exception(
            "bar_board non_keg_items query failed (user=%s business=%s)",
            request.user.username, business.id,
        )
        non_keg_items = []

    return render(request, 'core/bar/bar_board.html', {
        'is_owner': is_owner,
        'business': business,
        'success_data': success_data,
        'current_user_id': request.user.id,
        'non_keg_items': non_keg_items,
        'has_my_shift': has_my_shift,
    })


# ── Receive barrels ───────────────────────────────────────────────────────────

@login_required
@require_POST
def receive_barrel(request):
    """Owner/manager receives N sealed barrels from the distributor."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    business = up.business

    # Server-side double-submit backstop — see core/idempotency.py. The client
    # already disables the submit button, which covers a second click, but not a
    # real duplicate request (slow-network retry). This creates real stock
    # (barrels + Receipt transactions), so a duplicate would double-count both.
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    item = Item.objects.filter(
        id=request.POST.get('item_id'),
        store__business=business,
        is_keg=True,
    ).first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Keg item not found'}, status=404)

    try:
        count = max(1, int(request.POST.get('count', 1)))
        cost = Decimal(str(request.POST.get('cost_per_barrel', '0')))
        gross_kg = Decimal(str(request.POST.get('gross_kg') or str(business.keg_default_gross_kg)))
        tare_kg = Decimal(str(request.POST.get('tare_kg') or str(business.keg_default_tare_kg)))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid numbers'}, status=400)

    if cost <= 0:
        return JsonResponse({'ok': False, 'error': 'Enter the cost per barrel'}, status=400)

    # Scale reading overrides gross if provided
    scale_raw = (request.POST.get('scale_reading') or '').strip()
    try:
        actual_gross = Decimal(scale_raw) if scale_raw else gross_kg
    except Exception:
        actual_gross = gross_kg

    # Target: explicit input or cost × business multiplier
    target_raw = (request.POST.get('target_per_barrel') or '').strip()
    try:
        target = (
            Decimal(target_raw)
            if target_raw
            else (cost * business.keg_revenue_multiplier).quantize(Decimal('1'))
        )
    except Exception:
        target = (cost * business.keg_revenue_multiplier).quantize(Decimal('1'))

    net_ml = (actual_gross - tare_kg) * 1000
    created_ids = []

    for _barrel_idx in range(count):
        barrel = KegBarrel.objects.create(
            business=business,
            store=item.store,
            item=item,
            gross_weight_kg=actual_gross,
            tare_weight_kg=tare_kg,
            cost_price=cost,
            target_revenue=target,
            received_by=request.user,
        )
        KegWeightReading.objects.create(
            barrel=barrel,
            weight_kg=actual_gross,
            reading_type='RECEIVE',
            recorded_by=request.user,
            note=f"Received — gross {actual_gross} kg, tare {tare_kg} kg",
        )
        Transaction.objects.create(
            item=item,
            business=business,
            type='Receipt',
            qty=net_ml,
            recipient=f"Distributor — barrel #{barrel.id}, KES {float(cost):.0f}",
        )
        created_ids.append(barrel.id)

    # Update item fields — cost_price for P&L reference, keg_type if supplied
    update_item_fields = ['cost_price']
    keg_type_val = (request.POST.get('keg_type') or '').strip().upper()
    if keg_type_val in ('REGULAR', 'DARK', 'GOLD'):
        item.keg_type = keg_type_val
        update_item_fields.append('keg_type')
    item.cost_price = cost
    item.save(update_fields=update_item_fields)

    return JsonResponse({
        'ok': True,
        'created': created_ids,
        'count': len(created_ids),
        'target': float(target),
        'net_l': float(net_ml / 1000),
    })


# ── Tap a sealed barrel ───────────────────────────────────────────────────────

@login_required
@require_POST
def tap_barrel(request, barrel_id):
    """Owner/manager opens (taps) a sealed barrel. Enforces one TAPPED barrel per item."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

    if barrel.status != 'SEALED':
        return JsonResponse(
            {'ok': False, 'error': f"Barrel ni {barrel.status}, si SEALED"},
            status=400,
        )

    already_tapped = KegBarrel.objects.filter(
        business=up.business, item=barrel.item, status='TAPPED'
    ).exists()
    if already_tapped:
        return JsonResponse(
            {'ok': False, 'error': 'Kuna barrel inayouza tayari. Imarishe kwanza kabla ya kufungua nyingine.'},
            status=400,
        )

    barrel.tap(request.user)

    # K5.B — tap-time weigh-in for weighing bars
    if getattr(up.business, 'weighs_kegs', False):
        w_raw = (request.POST.get('starting_weight_kg') or '').strip()
        if w_raw:
            try:
                start_w = float(w_raw)
                KegWeightReading.objects.create(
                    barrel=barrel,
                    weight_kg=start_w,
                    reading_type='SPOT',  # tap-time check reuses SPOT slot
                    recorded_by=request.user,
                )
                expected = float(barrel.gross_weight_kg)
                missing_kg = expected - start_w
                if missing_kg > 2.0:
                    missing_l = round(missing_kg, 1)
                    msg = (
                        f"⚠️ {barrel.item.description}: pipa limepimwa likiwa "
                        f"pungufu wakati wa kufungua — takriban {missing_l} L "
                        f"imeisha kabla ya mauzo kurekodiwa. Angalia haraka!"
                    )
                    _fire_owner_alert_msg(up.business, barrel.item.description, msg)
            except (ValueError, TypeError):
                pass

    return JsonResponse({'ok': True, 'barrel_id': barrel.id, 'status': barrel.status})


# ── Alert helpers ─────────────────────────────────────────────────────────────

def _fire_owner_alert_msg(business, title, msg):
    """Send in-app Notification + SMS (rate-limited) to all owners."""
    from accounts.models import UserProfile
    from .models import Notification
    from .notifications import normalize_ke_phone, send_sms_notification

    now = timezone.now()
    can_sms = (
        not business.last_txn_sms_at or
        (now - business.last_txn_sms_at).total_seconds() > 600
    )
    owners = UserProfile.objects.filter(business=business, role='owner').select_related('user')
    for op in owners:
        Notification.objects.create(
            user=op.user, title=title, message=msg, notification_type='warning'
        )
        if can_sms and op.phone:
            normalized = normalize_ke_phone(op.phone)
            if normalized:
                send_sms_notification(msg, normalized)
    if can_sms:
        business.last_txn_sms_at = now
        business.save(update_fields=['last_txn_sms_at'])


# ── Weigh / spot-check ────────────────────────────────────────────────────────

def _fire_keg_alert(business, barrel_name, staff_name, variance_kes, variance_pct):
    """Notify all owners of a dangerous keg variance (in-app + SMS, respects bundling window)."""
    from accounts.models import UserProfile
    from .models import Notification
    from .notifications import normalize_ke_phone, send_sms_notification

    msg = (
        f"⚠️ {barrel_name}: variance {variance_pct:.0f}%"
        f" ({variance_kes:+.0f} KES). Staff: {staff_name}. Kagua mara moja."
    )
    now = timezone.now()
    can_sms = (
        not business.last_txn_sms_at or
        (now - business.last_txn_sms_at).total_seconds() > 600
    )
    owners = UserProfile.objects.filter(business=business, role='owner').select_related('user')
    for op in owners:
        Notification.objects.create(user=op.user, title='Keg Variance Alert', message=msg, notification_type='warning')
        if can_sms and op.phone:
            normalized = normalize_ke_phone(op.phone)
            if normalized:
                send_sms_notification(msg, normalized)
    if can_sms:
        business.last_txn_sms_at = now
        business.save(update_fields=['last_txn_sms_at'])


@login_required
@require_POST
def weigh_barrel(request, barrel_id):
    """Record a SPOT weight reading and return a variance mini-report."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    barrel = get_object_or_404(
        KegBarrel.objects.select_related('business'),
        id=barrel_id,
        business=up.business,
    )
    if barrel.status != 'TAPPED':
        return JsonResponse({'ok': False, 'error': 'Barrel si TAPPED — haiwezi kupimwa'}, status=400)

    # Staff must have their own OPEN shift to do spot checks
    from .models import Shift as _Shift
    if not up.is_owner_or_manager:
        my_shift = _Shift.objects.filter(
            business=up.business, status='OPEN', staff=request.user
        ).first()
        if not my_shift:
            return JsonResponse(
                {'ok': False, 'error': 'Fungua shift yako kwanza ili uweze kupima barrel.'},
                status=403,
            )
        linked_shift = my_shift
    else:
        linked_shift = _Shift.objects.filter(business=up.business, status='OPEN').first()

    try:
        weight_kg = Decimal(str(request.POST.get('weight_kg', '0')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Uzito si sahihi'}, status=400)

    if weight_kg <= 0:
        return JsonResponse({'ok': False, 'error': 'Ingiza uzito kutoka kwenye scale'}, status=400)

    note = (request.POST.get('note') or '').strip()

    KegWeightReading.objects.create(
        barrel=barrel,
        shift=linked_shift,
        weight_kg=weight_kg,
        reading_type='SPOT',
        recorded_by=request.user,
        note=note,
    )

    # Variance: scale is ground truth
    tare_kg = float(barrel.tare_weight_kg)
    net_remaining_kg = max(0.0, float(weight_kg) - tare_kg)
    dispensed_l = max(0.0, float(barrel.gross_weight_kg) - float(weight_kg))
    net_vol_l = barrel.net_volume_l
    rate = float(barrel.target_revenue) / net_vol_l if net_vol_l else 0.0
    expected_rev = dispensed_l * rate
    recorded_rev = float(barrel.revenue_collected)
    variance_kes = expected_rev - recorded_rev
    variance_pct = abs(variance_kes) / expected_rev * 100 if expected_rev > 0 else 0.0

    tolerance = float(barrel.business.keg_variance_tolerance_pct)
    flag = keg_metrics.variance_flag(variance_pct, tolerance)

    # F2-AC3: SPOT alert only when dispensed volume >= threshold (avoids crying wolf on tiny reads)
    if (flag == 'danger'
            and barrel.business.keg_alerts_enabled
            and dispensed_l >= float(barrel.business.keg_alert_min_litres)):
        staff_name = request.user.get_full_name() or request.user.username
        try:
            _fire_keg_alert(barrel.business, barrel.item.description, staff_name,
                            variance_kes, variance_pct)
        except Exception:
            pass

    return JsonResponse({
        'ok': True,
        'dispensed_l':        round(dispensed_l, 1),
        'net_remaining_kg':   round(net_remaining_kg, 2),
        'tare_kg':            tare_kg,
        'expected_rev':       round(expected_rev, 0),
        'recorded_rev':       round(recorded_rev, 0),
        'variance_kes':       round(variance_kes, 0),
        'variance_pct':       round(variance_pct, 1),
        'flag':               flag,
        'weight_kg':          float(weight_kg),
        'remaining_envelope': round(barrel.remaining_envelope(), 0),
    })


# ── Edit barrel parameters ───────────────────────────────────────────────────

@login_required
@require_POST
def edit_barrel(request, barrel_id):
    """Owner/manager corrects a barrel's financial parameters (cost, target, weight)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

    if barrel.status not in ('SEALED', 'TAPPED'):
        return JsonResponse({'ok': False, 'error': 'Barrel imekwisha au imetupwa'}, status=400)

    updates = {}
    try:
        cost_raw = (request.POST.get('cost_price') or '').strip()
        if cost_raw:
            updates['cost_price'] = Decimal(cost_raw)

        target_raw = (request.POST.get('target_revenue') or '').strip()
        if target_raw:
            updates['target_revenue'] = Decimal(target_raw)

        if barrel.status == 'SEALED':
            gross_raw = (request.POST.get('gross_weight_kg') or '').strip()
            if gross_raw:
                updates['gross_weight_kg'] = Decimal(gross_raw)

            tare_raw = (request.POST.get('tare_weight_kg') or '').strip()
            if tare_raw:
                updates['tare_weight_kg'] = Decimal(tare_raw)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari si sahihi'}, status=400)

    # keg_type lives on Item, not Barrel — update it separately
    keg_type_raw = (request.POST.get('keg_type') or '').strip().upper()
    if keg_type_raw in ('REGULAR', 'DARK', 'GOLD', ''):
        barrel.item.keg_type = keg_type_raw
        barrel.item.save(update_fields=['keg_type'])

    if not updates:
        # keg_type-only edit is still valid
        if keg_type_raw in ('REGULAR', 'DARK', 'GOLD', ''):
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'Hakuna mabadiliko ya kuingiza'}, status=400)

    for field, value in updates.items():
        setattr(barrel, field, value)
    barrel.save(update_fields=list(updates.keys()))

    return JsonResponse({'ok': True})


# ── Discard / return a barrel ─────────────────────────────────────────────────

@login_required
@require_POST
def discard_barrel(request, barrel_id):
    """Owner/manager writes off a barrel (returned, spoiled, wrong delivery)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

    if barrel.status == 'DEPLETED':
        return JsonResponse({'ok': False, 'error': 'Barrel imekwisha'}, status=400)

    reason = (request.POST.get('reason') or 'Imerudishwa / discarded').strip()
    barrel.close(reason=reason)
    return JsonResponse({'ok': True, 'status': barrel.status})


@login_required
@require_POST
def deplete_barrel(request, barrel_id):
    """K5.A — Mark a TAPPED barrel as DEPLETED (no wastage) for non-weighing bars.

    Called from the 'Funga Pipa' prompt when the revenue envelope is reached.
    Only applies when the barrel is still TAPPED — if already DEPLETED/DISCARDED, no-op.
    """
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

    if barrel.status != 'TAPPED':
        return JsonResponse({'ok': True, 'status': barrel.status})

    barrel.status    = 'DEPLETED'
    barrel.closed_at = timezone.now()
    barrel.save(update_fields=['status', 'closed_at'])
    from .models import _refresh_keg_baseline
    _refresh_keg_baseline(barrel)
    return JsonResponse({'ok': True, 'status': 'DEPLETED'})


# ══════════════════════════════════════════════════════════════════════════════
# SPRINT 3 — Tabs: list, tick, settle, void, convert-to-debt
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def tabs_list(request):
    """AJAX GET — returns all OPEN tabs for this business with their entries.

    ?ctx=qs  → Quick Sell tabs only (source='qs')
    default  → Bar board tabs (source='bar' and 'kitchen' for cross-counter)

    Station scoping:
      - bar-only staff: see bar entries on any tab; food entries replaced by a cross-notice
      - kitchen-only staff: redirected away (they use kitchen_tabs_list)
      - cross-access staff / owner: see ALL entries on ALL tabs
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': []})

    from .views import _station_scope
    _show_bar, _show_kitchen = _station_scope(up)
    _see_all = _show_bar and _show_kitchen  # owner or cross-access

    _ctx = request.GET.get('ctx', 'bar')

    if _ctx == 'qs':
        _source_filter = {'source': 'qs'}
    else:
        # Bar board context: bar + kitchen tabs (cross-counter visible), never QS
        _source_filter = {'source__in': ['bar', 'kitchen']}

    tabs = (
        BarTab.objects
        .filter(business=up.business, status='OPEN', **_source_filter)
        .prefetch_related(
            Prefetch('entries',
                     queryset=BarTabEntry.objects.select_related('transaction__item__store'))
        )
        .order_by('-opened_at')
    )

    # Batch-fetch receipt tokens for all open tabs so we can return receipt URLs
    _tab_ids = list(tabs.values_list('id', flat=True))
    _receipt_token_map = {}  # tab_id → receipt token
    if _tab_ids:
        # Pass 1: receipts that directly own the tab (meta.tab_id)
        for _r in Receipt.objects.filter(
            business=up.business, meta__tab_id__in=_tab_ids
        ).values('meta', 'token'):
            _rmeta = _r.get('meta') or {}
            _tid = _rmeta.get('tab_id')
            if _tid and _tid not in _receipt_token_map:
                _receipt_token_map[_tid] = _r['token']

        # Pass 2: receipts that reference the tab via linked_tab_ids (Priority 3/4 links)
        _unmapped = [tid for tid in _tab_ids if tid not in _receipt_token_map]
        if _unmapped:
            from core.tab_receipts import _safe_linked_query
            for _r in _safe_linked_query(
                Receipt.objects.filter(business=up.business), _unmapped
            ):
                _rmeta = _r.meta or {}
                for _ltid in (_rmeta.get('linked_tab_ids') or []):
                    if _ltid in _unmapped and _ltid not in _receipt_token_map:
                        _receipt_token_map[_ltid] = _r.token

    # Pending split-bill transfers touching these tabs — outgoing (an entry on
    # this tab is awaiting a DIFFERENT customer's accept/reject) and incoming
    # (a different tab's entry is proposed to land here). See
    # BarTabEntry.split_and_transfer_locked() / TabTransferRequest.
    _pending_out_by_entry = {}
    _pending_in_by_tab = {}
    if _tab_ids:
        for _t in TabTransferRequest.objects.filter(
            status='PENDING',
        ).filter(Q(source_tab_id__in=_tab_ids) | Q(dest_tab_id__in=_tab_ids)).select_related(
            'source_tab', 'dest_tab',
        ):
            if _t.source_tab_id in _tab_ids:
                _pending_out_by_entry[_t.entry_id] = {
                    'id': _t.id, 'amount': float(_t.amount), 'paid_amount': float(_t.paid_amount),
                    'dest_customer': _t.dest_tab.customer_name,
                }
            if _t.dest_tab_id in _tab_ids:
                _pending_in_by_tab.setdefault(_t.dest_tab_id, []).append({
                    'id': _t.id, 'amount': float(_t.amount), 'paid_amount': float(_t.paid_amount),
                    'note': _t.note, 'source_customer': _t.source_tab.customer_name,
                })

    def _entry_dict(e):
        """Serialise one BarTabEntry, including whether its item is a kitchen (food) item."""
        _is_kitchen_item = bool(
            e.transaction_id
            and e.transaction.item_id
            and e.transaction.item.store_id
            and e.transaction.item.store.is_kitchen
        )
        return {
            'id': e.id,
            'description': e.description,
            'amount': float(e.amount),
            'is_paid': e.is_paid,
            'payment_method': e.payment_method,
            'is_kitchen_item': _is_kitchen_item,
            'pending_transfer_out': _pending_out_by_entry.get(e.id),
            # Only computed when there's no LIVE pending request (that badge
            # already explains itself) — a REJECTED/CANCELLED one is a small
            # extra query per entry (core.models.BarTabEntry.transfer_reason_
            # note, single source of truth for this wording), acceptable
            # here since a tabs drawer is a handful of tabs, not a hot path.
            'transfer_note': ('' if e.id in _pending_out_by_entry else e.transfer_reason_note()),
        }

    result = []
    for tab in tabs:
        all_entries = list(tab.entries.all())

        _tab_phone = (tab.customer.phone if tab.customer else '') or ''

        if _see_all:
            # Owner / cross-access: full visibility on all entries
            entries = [_entry_dict(e) for e in all_entries]
            bar_entries_count   = sum(1 for e in entries if not e['is_kitchen_item'])
            kitchen_entry_count = sum(1 for e in entries if e['is_kitchen_item'])
            cross_notice = None
            if tab.source == 'kitchen' and bar_entries_count:
                cross_notice = f'+ {bar_entries_count} bar item(s)'
            elif tab.source == 'bar' and kitchen_entry_count:
                cross_notice = f'+ {kitchen_entry_count} food item(s)'
            _rcpt_token = _receipt_token_map.get(tab.id)
            _rcpt_url = request.build_absolute_uri(f'/r/{_rcpt_token}/') if _rcpt_token else None
            _opened_local = timezone.localtime(tab.opened_at)
            result.append({
                'id': tab.id,
                'customer_name': tab.customer_name,
                'customer_phone': _tab_phone,
                'server_name': tab.server_name,
                'total': sum(float(e['amount']) for e in entries),
                'unpaid_total': sum(float(e['amount']) for e in entries if not e['is_paid']),
                'entries': entries,
                'opened_at': _opened_local.strftime('%I:%M %p').lstrip('0'),
                'opened_date': _opened_local.strftime('%Y-%m-%d'),
                'is_food_tab': tab.source == 'kitchen',
                'cross_notice': cross_notice,
                'receipt_url': _rcpt_url,
                'tab_pin': tab.tab_pin,
                'cash_requested': bool(tab.cash_requested_at),
                'incoming_transfers': _pending_in_by_tab.get(tab.id, []),
            })
        else:
            # Bar-only staff: see only bar (non-kitchen) entries
            bar_entries = [
                e for e in all_entries
                if e.transaction_id
                and e.transaction.item_id
                and e.transaction.item.store_id
                and not e.transaction.item.store.is_kitchen
            ]
            kitchen_entry_count = len(all_entries) - len(bar_entries)

            # For food tabs: only show if bar items were added via cross-counter merge
            if tab.source == 'kitchen' and not bar_entries:
                continue

            entries = [_entry_dict(e) for e in bar_entries]
            unpaid = sum(float(e['amount']) for e in entries if not e['is_paid'])
            cross_notice = f'+ {kitchen_entry_count} food item(s) on this tab' if kitchen_entry_count else None
            _rcpt_token = _receipt_token_map.get(tab.id)
            _rcpt_url = request.build_absolute_uri(f'/r/{_rcpt_token}/') if _rcpt_token else None
            _opened_local = timezone.localtime(tab.opened_at)
            result.append({
                'id': tab.id,
                'customer_name': tab.customer_name,
                'customer_phone': _tab_phone,
                'server_name': tab.server_name,
                'total': sum(float(e['amount']) for e in entries),
                'unpaid_total': unpaid,
                'entries': entries,
                'opened_at': _opened_local.strftime('%I:%M %p').lstrip('0'),
                'opened_date': _opened_local.strftime('%Y-%m-%d'),
                'is_food_tab': tab.source == 'kitchen',
                'cross_notice': cross_notice,
                'receipt_url': _rcpt_url,
                'tab_pin': tab.tab_pin,
                'cash_requested': bool(tab.cash_requested_at),
                'incoming_transfers': _pending_in_by_tab.get(tab.id, []),
            })

    return JsonResponse({'tabs': result, 'bar_only_view': not _see_all})


@login_required
@require_POST
def update_tab_name(request, tab_id):
    """Allow staff to rename the customer on an open tab (also updates linked Customer + Transaction.recipient)."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    tab = get_object_or_404(
        BarTab, id=tab_id, business=up.business, status='OPEN',
        source__in=_allowed_tab_sources(up),
    )
    new_name = (request.POST.get('name') or '').strip()
    if not new_name:
        return JsonResponse({'ok': False, 'error': 'Jina haliwezi kuwa tupu.'}, status=400)

    old_name = tab.customer_name
    tab.customer_name = new_name
    tab.save(update_fields=['customer_name'])

    if tab.customer_id:
        tab.customer.name = new_name
        tab.customer.save(update_fields=['name'])

    # Propagate to Transaction.recipient so debt tracker shows the corrected name
    Transaction.objects.filter(
        id__in=tab.entries.values('transaction_id'),
        recipient__iexact=old_name,
    ).update(recipient=new_name)

    return JsonResponse({'ok': True, 'customer_name': new_name})


@login_required
@require_POST
def update_tab_phone(request, tab_id):
    """Allow staff to add/update the customer phone number on an open tab."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    tab = get_object_or_404(
        BarTab, id=tab_id, business=up.business, status='OPEN',
        source__in=_allowed_tab_sources(up),
    )
    phone = (request.POST.get('phone') or '').strip()

    if tab.customer_id:
        tab.customer.phone = phone
        tab.customer.save(update_fields=['phone'])
    elif tab.customer_name:
        _cust = Customer.objects.filter(
            business=up.business, name__iexact=tab.customer_name
        ).first()
        if _cust:
            _cust.phone = phone
            _cust.save(update_fields=['phone'])
        else:
            _cust = Customer.objects.create(
                business=up.business,
                name=tab.customer_name,
                phone=phone,
                credit_approved=True,
            )
        # Link customer FK so phone is returned by tabs_list and pre-fills STK modal
        tab.customer = _cust
        tab.save(update_fields=['customer'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def tick_entry(request, entry_id):
    """Mark a single BarTabEntry as paid."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    entry = get_object_or_404(
        BarTabEntry.objects.select_related('tab', 'transaction'),
        id=entry_id,
        tab__business=up.business,
        tab__source__in=_allowed_tab_sources(up),
        is_paid=False,
    )

    pay = (request.POST.get('payment_method') or 'cash').strip()
    if pay not in ('cash', 'mpesa'):
        pay = 'cash'

    now = timezone.now()
    entry.is_paid = True
    entry.paid_at = now
    entry.payment_method = pay
    entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])

    entry.transaction.payment_method = pay
    entry.transaction.save(update_fields=['payment_method'])

    tab = entry.tab
    tab_settled = not tab.entries.filter(is_paid=False).exists()
    receipt_url = None
    receipt_id = None
    if tab.cash_requested_at:
        # Staff has now acted on this tab — clear the "customer wants cash" badge.
        tab.cash_requested_at = None
        tab.save(update_fields=['cash_requested_at'])
    if tab_settled:
        tab.status = 'SETTLED'
        tab.settled_at = now
        tab.save(update_fields=['status', 'settled_at'])
        try:
            from .models import Receipt as _Receipt
            from core.tab_receipts import resolve_master_receipt
            # Reuse the tab's existing master receipt (from when it was opened, or
            # cross-linked from another counter) instead of always minting a new
            # one — this used to unconditionally Receipt.issue() here regardless
            # of whether the tab already had a receipt, orphaning the customer's
            # already-known PIN with a second, disconnected receipt every time a
            # staff member ticked the last item paid (bar-audit finding, 2026-07-19).
            master_rcpt, _ = resolve_master_receipt(tab.business, tab)
            if master_rcpt:
                rcpt = master_rcpt
                if rcpt.payment_method != pay:
                    rcpt.payment_method = pay
                    rcpt.save(update_fields=['payment_method'])
            else:
                all_entries = list(tab.entries.all())
                lines = [
                    {'name': e.description, 'qty': 1, 'subtotal': float(e.amount)}
                    for e in all_entries
                ]
                rcpt = _Receipt.issue(
                    business=tab.business,
                    lines=lines,
                    payment_method=pay,
                    user=request.user,
                    customer_name=tab.customer_name,
                    meta={'tab_id': tab.id},
                )
            receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
            receipt_id = rcpt.id
        except Exception:
            pass

    return JsonResponse({
        'ok': True,
        'unpaid_total': float(tab.unpaid_total()),
        'tab_settled': tab_settled,
        'receipt_url': receipt_url,
        'receipt_id': receipt_id,
    })


@login_required
@require_POST
def remove_tab_entry(request, tab_id, entry_id):
    """Owner/manager only: void a single BarTabEntry (correction for mis-added entries).

    Marks the entry and its underlying Transaction as 'void' so revenue and
    analytics exclude it. Only works on OPEN tabs with unpaid entries.
    Returns the updated unpaid total for the tab.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    try:
        entry = BarTabEntry.objects.select_related('tab', 'transaction').get(
            id=entry_id,
            tab__id=tab_id,
            tab__business=up.business,
            tab__status='OPEN',
            is_paid=False,
        )
    except BarTabEntry.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)

    now = timezone.now()
    entry.is_paid = True
    entry.paid_at = now
    entry.payment_method = 'void'
    entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])

    entry.transaction.payment_method = 'void'
    entry.transaction.qty = Decimal('0')  # nullify stock effect — item was never given to customer
    entry.transaction.save(update_fields=['payment_method', 'qty'])

    new_total = float(entry.tab.entries.filter(is_paid=False).aggregate(t=Sum('amount'))['t'] or 0)
    return JsonResponse({'ok': True, 'new_total': new_total})


@login_required
@require_POST
def split_and_transfer_entry(request, entry_id):
    """Split one tab entry between a real payment (kept on the source
    customer's own tab) and an unpaid remainder proposed as a transfer onto a
    DIFFERENT customer's open tab — e.g. Roy pays 400 of his 600 Smirnoff
    himself, his friend Bosco's tab picks up the remaining 200 (2026-07-23
    live request). Any staff with an open shift may do this (confirmed with
    Roy — not owner/manager-only, needs to work mid-shift without the owner
    present). See BarTabEntry.split_and_transfer_locked() for the mechanism.

    dest_tab_id (existing open tab) or dest_customer_name (no tab yet — e.g.
    Bosco is in the premises but isn't drinking right now, so there's nothing
    to pick from the list) must be given. A name is first checked against any
    already-open tab under that exact name (same auto-detect-by-name pattern
    already used by the cross-counter merge feature, core/keg_views.py and
    core/kitchen_views.py) before opening a brand-new one, so this can never
    silently create a second, duplicate tab for someone who already has one.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    entry = get_object_or_404(
        BarTabEntry.objects.select_related('tab'),
        id=entry_id, tab__business=up.business,
        tab__source__in=_allowed_tab_sources(up),
    )

    dest_tab_id = (request.POST.get('dest_tab_id') or '').strip()
    dest_customer_name = (request.POST.get('dest_customer_name') or '').strip()
    if dest_tab_id:
        dest_tab = get_object_or_404(
            BarTab, id=dest_tab_id, business=up.business,
            source__in=_allowed_tab_sources(up),
        )
    elif dest_customer_name:
        dest_tab = BarTab.objects.filter(
            business=up.business, status='OPEN', customer_name__iexact=dest_customer_name,
            source__in=_allowed_tab_sources(up),
        ).first()
        if not dest_tab:
            dest_tab = BarTab.create_with_credentials(
                business=up.business, store=entry.tab.store, customer_name=dest_customer_name,
                source=entry.tab.source, served_by=request.user,
            )
    else:
        return JsonResponse({'ok': False, 'error': 'Chagua tab au weka jina la mteja.'}, status=400)

    try:
        new_entry, tfr = BarTabEntry.split_and_transfer_locked(
            entry_id=entry.id,
            business=up.business,
            paid_amount=request.POST.get('paid_amount', '0'),
            paid_method=(request.POST.get('paid_method') or 'cash').strip(),
            dest_tab_id=dest_tab.id,
            staff_user=request.user,
        )
    except (InvalidOperation, ValueError) as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Nambari batili'}, status=400)

    # Notify the destination customer — SMS if a phone is on file (optional,
    # never required per Roy), and always visible on their own live receipt
    # regardless (see _pending_transfers_in in core/receipt_views.py). No
    # in-app fan-out at request time — that fires when the customer responds
    # (_notify_tab_transfer_resolved), not before, since nothing is decided yet.
    try:
        phone = dest_tab.customer.phone if dest_tab.customer else ''
        if phone:
            from .notifications import normalize_ke_phone, send_sms_notification
            from core.tab_receipts import resolve_master_receipt
            normalized = normalize_ke_phone(phone)
            if normalized:
                rcpt, _ = resolve_master_receipt(up.business, dest_tab)
                link = request.build_absolute_uri(f'/r/{rcpt.token}/') if rcpt else ''
                paid_bit = (
                    f" {entry.tab.customer_name} alishalipa KES {tfr.paid_amount:,.0f} mwenyewe."
                    if tfr.paid_amount else ""
                )
                msg = (
                    f"Habari {dest_tab.customer_name},\n"
                    f"{up.business.name}: {entry.tab.customer_name} anataka kuongeza "
                    f"KES {tfr.amount:,.0f} kwenye tab yako ({tfr.note}).{paid_bit}\n"
                    + (f"Kubali au kataa kwenye risiti yako: {link}" if link else
                       "Muulize mhudumu kukubali au kukataa.")
                )
                send_sms_notification(msg, normalized)
    except Exception:
        logger.exception('split_and_transfer_entry SMS failed (business=%s)', up.business.id)

    return JsonResponse({
        'ok': True,
        'transfer_id': tfr.id,
        'source_unpaid_total': float(entry.tab.unpaid_total()),
        'new_entry_id': new_entry.id,
    })


@login_required
@require_POST
def respond_tab_transfer(request, transfer_id):
    """Staff-side accept/reject of a pending split-bill transfer — for when
    the destination customer confirms verbally rather than via SMS/receipt
    link (phone is optional, so this must work without one). Any staff with
    permission on the destination tab's station may respond on the
    customer's behalf."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    transfer = get_object_or_404(
        TabTransferRequest, id=transfer_id, business=up.business,
        dest_tab__source__in=_allowed_tab_sources(up),
    )
    action = (request.POST.get('action') or '').strip()
    if action not in ('accept', 'reject'):
        return JsonResponse({'ok': False, 'error': 'action lazima iwe accept au reject'}, status=400)

    try:
        if action == 'accept':
            transfer.accept()
        else:
            transfer.reject()
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    from core.receipt_views import _notify_tab_transfer_resolved
    _notify_tab_transfer_resolved(transfer)

    return JsonResponse({'ok': True, 'status': transfer.status})


@login_required
@require_POST
def settle_tab(request, tab_id):
    """Settle unpaid entries on a tab and issue a receipt.

    Partial settle: POST entry_ids[] to settle only specific entries and keep
    the tab OPEN with remaining balance. Omit entry_ids to settle everything.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza ili kulipa tab.'},
                status=403,
            )

    tab = BarTab.objects.filter(id=tab_id, business=up.business).first()
    if not tab:
        return JsonResponse({'ok': False, 'error': 'Tab not found'}, status=404)
    if tab.status != 'OPEN':
        # Idempotent — already settled; return ok so the JS doesn't show an error on retry
        return JsonResponse({'ok': True, 'already_settled': True, 'total': float(tab.total())})

    pay = (request.POST.get('payment_method') or 'cash').strip()
    if pay not in ('cash', 'mpesa'):
        pay = 'cash'

    # Partial settle: optional entry_ids[] selects which entries to settle now
    raw_ids = request.POST.getlist('entry_ids')
    selected_ids = None
    if raw_ids:
        try:
            selected_ids = {int(i) for i in raw_ids if i.strip().isdigit()}
        except (ValueError, TypeError):
            selected_ids = None

    now = timezone.now()
    all_entries = list(tab.entries.all().select_related('transaction__item__store'))

    entries_to_settle = (
        [e for e in all_entries if not e.is_paid and e.id in selected_ids]
        if selected_ids is not None else
        [e for e in all_entries if not e.is_paid]
    )

    if not entries_to_settle:
        return JsonResponse({'ok': False, 'error': 'Hakuna entries zilizochaguliwa.'}, status=400)

    # Station Scoping Principle: check each entry's OWN station (item.store.is_kitchen),
    # not the tab's overall source — a bar-only staffer may legitimately settle just the
    # bar-item entries within a mixed/kitchen-sourced tab (the cross-counter merge
    # feature), but must never settle a kitchen entry, and vice versa (bar-audit
    # finding, 2026-07-19 — this endpoint had no station check at all before).
    _allowed_sources = _allowed_tab_sources(up)
    for _e in entries_to_settle:
        try:
            _entry_source = 'kitchen' if _e.transaction.item.store.is_kitchen else 'bar'
        except Exception:
            _entry_source = tab.source or 'bar'
        if _entry_source not in _allowed_sources:
            return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kulipa bidhaa hizi.'}, status=403)

    for entry in entries_to_settle:
        entry.is_paid = True
        entry.paid_at = now
        entry.payment_method = pay
        entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])
        entry.transaction.payment_method = pay
        entry.transaction.save(update_fields=['payment_method'])

    # Close the tab only when ALL entries are now paid
    tab_fully_settled = not tab.entries.filter(is_paid=False).exists()
    if tab_fully_settled:
        tab.status = 'SETTLED'
        tab.settled_at = now
        tab.save(update_fields=['status', 'settled_at'])
    if tab.cash_requested_at:
        # Staff has now acted on this tab — clear the "customer wants cash" badge.
        tab.cash_requested_at = None
        tab.save(update_fields=['cash_requested_at'])

    settled_amount = sum(float(e.amount) for e in entries_to_settle)
    customer_phone = (request.POST.get('customer_phone') or '').strip()

    # Auto-create or update Customer record on any settlement (not just credit)
    if tab_fully_settled and tab.customer_name and not tab.customer_id:
        _cust = Customer.objects.filter(
            business=tab.business, name__iexact=tab.customer_name,
        ).first()
        if _cust is None:
            _cust = Customer.objects.create(
                business=tab.business,
                name=tab.customer_name,
                phone=customer_phone,
                credit_approved=True,
            )
        elif customer_phone and not (_cust.phone or '').strip():
            _cust.phone = customer_phone
            _cust.save(update_fields=['phone'])
        tab.customer = _cust
        tab.save(update_fields=['customer'])

    # Issue a receipt — reuse the tab's existing master receipt if one exists
    # (from when it was opened, or cross-linked from another counter) instead of
    # always minting a new one. This used to unconditionally Receipt.issue() a
    # SEPARATE receipt here on every settle — partial or full — regardless of
    # whether the tab already had a receipt; on a full settlement it didn't even
    # carry a tab_id, so it was a permanent dead-end orphan. Since every counter
    # settlement (not just customer-initiated STK) is the common path for closing
    # a tab, this orphaned the customer's known PIN on nearly every tab (bar-audit
    # finding, 2026-07-19). The live receipt page recomputes its full bill from
    # the tab regardless of which entries this specific settle action covered, so
    # reusing the master is strictly more correct, not just less duplicative.
    receipt_url = None
    receipt_id = None
    try:
        from .models import Receipt as _Receipt
        from core.tab_receipts import resolve_master_receipt
        master_rcpt, _ = resolve_master_receipt(tab.business, tab)
        if master_rcpt:
            rcpt = master_rcpt
            _update_fields = []
            if pay == 'credit' and tab.customer:
                try:
                    from core.debt_views import _build_credit_receipt_meta
                    source_scope = 'kitchen' if (tab.source == 'kitchen') else 'bar'
                    _credit_meta = _build_credit_receipt_meta(tab.business, tab.customer, source_scope)
                    rcpt.meta = {**rcpt.meta, **_credit_meta}
                    _update_fields.append('meta')
                except Exception:
                    pass
            if rcpt.payment_method != pay:
                rcpt.payment_method = pay
                _update_fields.append('payment_method')
            if _update_fields:
                rcpt.save(update_fields=_update_fields)
        else:
            lines = [
                {'name': e.description, 'qty': 1, 'subtotal': float(e.amount)}
                for e in entries_to_settle
            ]
            settle_meta = {}
            # ── Live tab receipt: include tab_id so the public_receipt view can
            #    dynamically recompute lines from the BarTab when it's still OPEN ──
            if not tab_fully_settled:
                settle_meta['tab_id'] = tab.id
            if pay == 'credit' and tab.customer:
                try:
                    from core.debt_views import _build_credit_receipt_meta
                    source_scope = 'kitchen' if (tab.source == 'kitchen') else 'bar'
                    settle_meta = _build_credit_receipt_meta(tab.business, tab.customer, source_scope)
                    if not tab_fully_settled:
                        settle_meta['tab_id'] = tab.id
                except Exception:
                    pass
            rcpt = _Receipt.issue(
                business=tab.business,
                lines=lines,
                payment_method=pay,
                user=request.user,
                customer_name=tab.customer_name,
                customer_phone=customer_phone,
                source=tab.source or '',
                meta=settle_meta,
            )
        receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
        receipt_id = rcpt.id
        if customer_phone and receipt_url:
            from .notifications import normalize_ke_phone, send_sms_notification
            normalized = normalize_ke_phone(customer_phone)
            if normalized:
                sms_msg = (
                    f"Duka: {tab.business.name}\n"
                    f"Umelipa: KES {settled_amount:,.0f}\n"
                    f"Risiti: {receipt_url}"
                )
                send_sms_notification(sms_msg, normalized)
    except Exception:
        pass

    # Notify staff if same customer has other open tabs (bar or kitchen)
    other_open_tabs = []
    if tab.customer_name and tab_fully_settled:
        other_qs = BarTab.objects.filter(
            business=tab.business,
            customer_name__iexact=tab.customer_name,
            status='OPEN',
        ).exclude(id=tab.id).values('id', 'source', 'customer_name')
        for ot in other_qs:
            if ot['source'] == 'kitchen':
                label = '🍗 Food Tab'
            elif ot['source'] == 'qs':
                label = '🛒 Quick Sell Tab'
            else:
                label = '🍺 Bar Tab'
            other_open_tabs.append({'id': ot['id'], 'label': label})

    return JsonResponse({
        'ok': True,
        'tab_settled': tab_fully_settled,
        'partial': not tab_fully_settled,
        'settled_amount': settled_amount,
        'total': float(tab.total()),
        'receipt_url': receipt_url,
        'receipt_id': receipt_id,
        'other_open_tabs': other_open_tabs,
    })


@login_required
@require_POST
def void_tab(request, tab_id):
    """Void a tab — owner/manager only. Marks all unpaid entries as written off."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    tab = get_object_or_404(BarTab, id=tab_id, business=up.business, status='OPEN')
    reason = (request.POST.get('reason') or 'Imetupwa').strip()

    # Check BEFORE the loop: did any unpaid transactions carry credit (converted debt)?
    # After the loop all entries have payment_method='void' so checking then is always True.
    had_credit = tab.entries.filter(
        is_paid=False, transaction__payment_method='credit'
    ).exists()

    now = timezone.now()
    for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
        entry.is_paid = True
        entry.paid_at = now
        entry.payment_method = 'void'
        entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])
        # Remove from debt tracker: written off, not owed
        if entry.transaction_id:
            entry.transaction.payment_method = 'void'
            entry.transaction.recipient = ''
            entry.transaction.save(update_fields=['payment_method', 'recipient'])

    tab.status = 'VOID'
    tab.settled_at = now
    tab.void_reason = reason[:120]
    tab.cash_requested_at = None
    tab.save(update_fields=['status', 'settled_at', 'void_reason', 'cash_requested_at'])
    _cancel_pending_transfers_for_tab(tab)

    # Only mark defaulter when the voided tab actually carried converted credit transactions
    if had_credit and tab.customer_name:
        cust_obj = Customer.objects.filter(
            business=up.business, name=tab.customer_name
        ).first()
        if cust_obj:
            Customer.objects.filter(pk=cust_obj.pk).update(is_defaulter=True)

    return JsonResponse({'ok': True, 'reason': reason})


@login_required
@require_POST
def convert_tab_to_debt(request, tab_id):
    """Convert a tab's unpaid balance to the debt tracker under a Customer."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    tab = get_object_or_404(
        BarTab, id=tab_id, business=up.business, status='OPEN',
        source__in=_allowed_tab_sources(up),
    )

    customer_name = (request.POST.get('customer_name') or tab.customer_name).strip()
    phone = (request.POST.get('phone') or '').strip()

    # Find or create the Customer record.
    # Customer has no unique_together on (business, name/phone) so get_or_create raises
    # MultipleObjectsReturned when duplicate rows exist — always use filter().first().
    customer = None
    if phone:
        customer = Customer.objects.filter(business=up.business, phone=phone).first()
    if customer is None:
        customer = Customer.objects.filter(
            business=up.business, name__iexact=customer_name,
        ).first()
    if customer is None:
        # credit_approved=True — matches bulk_convert_tabs_to_debt and
        # shift_views._convert_open_tabs_to_debt_for_shift (the other two
        # tab-to-debt conversion sites). Without this, evaluate_credit()'s
        # check #1 (credit_approved) would trivially "block" every brand-new
        # customer created here, which is meaningless noise — they were never
        # asked to pre-approve credit, they just have an unpaid tab.
        customer = Customer.objects.create(
            business=up.business, name=customer_name, phone=phone,
            credit_approved=True,
        )

    unpaid_total = float(tab.unpaid_total())

    # Link the transactions to this customer so the debt tracker sees them.
    # Must set payment_method='credit' — debt tracker queries by that value.
    for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
        txn = entry.transaction
        txn.recipient = customer.name
        txn.payment_method = 'credit'
        txn.save(update_fields=['recipient', 'payment_method'])

    tab.customer = customer
    tab.status = 'SETTLED'
    tab.settled_at = timezone.now()
    tab.cash_requested_at = None
    tab.save(update_fields=['customer', 'status', 'settled_at', 'cash_requested_at'])
    _cancel_pending_transfers_for_tab(tab)

    # Heads-up (not a block — goods already served) if this customer is
    # already credit-risky per the K3 policy gate.
    try:
        from core.credit_policy import notify_owners_of_conversion_risk
        notify_owners_of_conversion_risk(
            up.business, customer, 'kitchen' if tab.source == 'kitchen' else 'bar', unpaid_total,
        )
    except Exception:
        logger.exception('convert_tab_to_debt credit-risk notify failed (customer=%s)', customer.id)

    # SMS to customer confirming the debt (mirrors Quick Sell credit flow)
    if customer.phone:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification
            _norm = normalize_ke_phone(customer.phone)
            if _norm:
                _source_label = 'Kitchen' if tab.source == 'kitchen' else 'Bar'
                _sms = (
                    f"Habari {customer.name},\n"
                    f"{up.business.name}: Deni la KES {unpaid_total:,.0f} "
                    f"limeandikwa ({_source_label}).\n"
                    f"Tafadhali lipa ndani ya siku {up.business.credit_window_days}."
                )
                send_sms_notification(_sms, _norm)
        except Exception:
            logger.exception(
                'convert_tab_to_debt SMS failed (business=%s customer=%s)',
                up.business.id, customer.id,
            )

    return JsonResponse({
        'ok': True,
        'customer_name': customer.name,
        'unpaid_total': unpaid_total,
        'debt_url': f'/debt/{customer.id}/',
    })


@login_required
@require_POST
def bulk_convert_tabs_to_debt(request):
    """Convert multiple open tabs to debt in one action — typically called at shift close."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    import json as _json
    try:
        tab_ids = _json.loads(request.POST.get('tab_ids', '[]'))
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Orodha ya tab si sahihi.'}, status=400)

    # Station Scoping Principle: this endpoint had NO permission check at all
    # beyond "logged in to this business" — any staff member, bar or kitchen,
    # could bulk-convert arbitrary tab IDs regardless of role or station
    # (bar-audit finding, 2026-07-19). No owner/manager-only or shift gate is
    # added here deliberately — this is designed to be called by whoever just
    # closed their own shift, at which point their shift is already CLOSED, so
    # a shift-open check would block the very person meant to use it. The
    # station filter is the real fix: no gate is more permissive than "act only
    # on your own station's tabs."
    _allowed_sources = _allowed_tab_sources(up)

    converted = 0
    for tab_id in tab_ids:
        try:
            tab = BarTab.objects.get(
                id=int(tab_id), business=up.business, status='OPEN',
                source__in=_allowed_sources,
            )
        except (BarTab.DoesNotExist, ValueError):
            continue

        customer_name = (tab.customer_name or '').strip() or f'Tab #{tab.id}'
        phone = ''
        if tab.customer_id and tab.customer.phone:
            phone = tab.customer.phone

        customer = None
        if phone:
            customer = Customer.objects.filter(business=up.business, phone=phone).first()
        if customer is None and customer_name:
            customer = Customer.objects.filter(
                business=up.business, name__iexact=customer_name,
            ).first()
        if customer is None:
            customer = Customer.objects.create(
                business=up.business, name=customer_name, phone=phone,
                credit_approved=True,
            )

        unpaid_total = float(tab.unpaid_total())

        for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
            txn = entry.transaction
            txn.recipient = customer.name
            txn.payment_method = 'credit'
            txn.save(update_fields=['recipient', 'payment_method'])

        tab.customer = customer
        tab.status = 'SETTLED'
        tab.settled_at = timezone.now()
        tab.cash_requested_at = None
        tab.save(update_fields=['customer', 'status', 'settled_at', 'cash_requested_at'])
        _cancel_pending_transfers_for_tab(tab)
        converted += 1

        try:
            from core.credit_policy import notify_owners_of_conversion_risk
            notify_owners_of_conversion_risk(
                up.business, customer, 'kitchen' if tab.source == 'kitchen' else 'bar', unpaid_total,
            )
        except Exception:
            logger.exception('bulk_convert_tabs_to_debt credit-risk notify failed (customer=%s)', customer.id)

    return JsonResponse({'ok': True, 'converted': converted})


# ── Cup tracking ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def add_cups(request):
    """Log a disposable cup purchase into the business-wide cup pool.

    Available to: owner (shift-exempt) and bar staff with an active shift.
    barrel_id is optional POST context for cost allocation only — the pool is business-wide.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)

    business = up.business
    is_owner = getattr(up, 'is_owner_or_manager', False)

    # Bar staff need an active shift; owner/manager is always exempt
    if not is_owner:
        from .shift_views import get_active_staff_shift
        shift = get_active_staff_shift(up, business)
        if shift is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza'}, status=403)
        # Also gate to bar staff (role == staff/waitress on bar counter, not kitchen-only)
        if up.role not in ('owner', 'manager', 'staff', 'waitress'):
            return JsonResponse({'ok': False, 'error': 'Hakuna ruhusa'}, status=403)

    # Server-side double-submit backstop — see core/idempotency.py. No natural
    # "already done" guard exists here (a fresh BarCupLog row is always valid),
    # so a double-tap/retry would double-count both the purchase cost AND the
    # "bought" side of the cup pool math, masking a real future shortage behind
    # false confidence (bar-audit finding, 2026-07-19).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    try:
        cup_size  = request.POST.get('cup_size', '300')
        if cup_size not in ('300', '500'):
            cup_size = '300'
        qty       = max(1, int(request.POST.get('qty', 1)))
        unit_cost = Decimal(str(request.POST.get('unit_cost', '0')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari si sahihi'}, status=400)

    if unit_cost <= 0:
        return JsonResponse({'ok': False, 'error': 'Weka bei ya kikombe kimoja'}, status=400)

    total_cost = (unit_cost * qty).quantize(Decimal('0.01'))
    note = (request.POST.get('note') or '').strip()

    # Optional: barrel context for cost allocation (not required)
    barrel = None
    barrel_id_raw = request.POST.get('barrel_id', '').strip()
    if barrel_id_raw.isdigit():
        barrel = KegBarrel.objects.filter(id=int(barrel_id_raw), business=business).first()

    BarCupLog.objects.create(
        business=business,
        barrel=barrel,
        cup_size=cup_size,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        note=note,
        recorded_by=request.user,
    )

    pool = keg_metrics.business_cup_pool(business)

    # Fire low-stock alert once when pool drops below 30 (reset when healthy stock logged)
    if pool['low_stock'] and pool['total_cups_bought'] > 0:
        from django.utils import timezone as _tz
        business.refresh_from_db(fields=['cup_low_notified_at'])
        if business.cup_low_notified_at is None:
            from .models import Notification
            from .notifications import normalize_ke_phone as _norm_phone, send_sms_notification as _send_sms
            from accounts.models import UserProfile as _UP
            _cup_msg = (
                f"⚠️ Vikombe vimekwisha! Bado {pool['remaining']} vikombe — "
                "nunua vikombe zaidi mapema."
            )
            for op in _UP.objects.filter(business=business, role='owner').select_related('user'):
                Notification.objects.create(
                    user=op.user,
                    title='Vikombe vimekwisha',
                    message=_cup_msg,
                    notification_type='warning',
                )
                if op.phone:
                    try:
                        _send_sms(_cup_msg, _norm_phone(op.phone))
                    except Exception:
                        pass
            from accounts.models import Business as _Biz
            _Biz.objects.filter(pk=business.pk).update(cup_low_notified_at=_tz.now())
    elif not pool['low_stock'] and business.cup_low_notified_at is not None:
        # Reset the notified flag when healthy stock is logged
        from accounts.models import Business as _Biz
        _Biz.objects.filter(pk=business.pk).update(cup_low_notified_at=None)

    return JsonResponse({'ok': True, 'pool': pool, 'total_cost': float(total_cost)})


# ── Daily Bar Report ───────────────────────────────────────────────────────────

@login_required
def bar_daily_report(request):
    """Owner/manager daily summary: barrels opened, cups/pints/jugs sold, revenue."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return redirect('bar_board')

    date_str = request.GET.get('date', timezone.localdate().isoformat())
    try:
        report_date = date_type.fromisoformat(date_str)
    except (ValueError, AttributeError):
        report_date = timezone.localdate()

    business = up.business

    # Barrels that were opened (received) on this date
    barrels_opened = KegBarrel.objects.filter(
        business=business, received_on=report_date
    ).select_related('item').order_by('item__description')

    # All keg transactions on this date (void excluded)
    txns = Transaction.objects.filter(
        business=business,
        item__is_keg=True,
        date=report_date,
        keg_barrel__isnull=False,
    ).exclude(payment_method='void')

    cups  = txns.filter(keg_serving='cup').aggregate(n=Sum('keg_qty'))['n'] or 0
    jugs  = txns.filter(keg_serving='jug').aggregate(n=Sum('keg_qty'))['n'] or 0
    pints = txns.filter(keg_serving='pint').aggregate(n=Sum('keg_qty'))['n'] or 0
    total_revenue = float(txns.aggregate(r=Sum('sale_amount'))['r'] or 0)

    # Per-barrel breakdown (only barrels that had sales that day)
    per_barrel = []
    barrel_ids = txns.values_list('keg_barrel_id', flat=True).distinct()
    for barrel in KegBarrel.objects.filter(id__in=barrel_ids).select_related('item'):
        bt = txns.filter(keg_barrel=barrel)
        per_barrel.append({
            'barrel': barrel,
            'cups':    bt.filter(keg_serving='cup').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'jugs':    bt.filter(keg_serving='jug').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'pints':   bt.filter(keg_serving='pint').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'revenue': float(bt.aggregate(r=Sum('sale_amount'))['r'] or 0),
        })

    # ── Waitress performance ───────────────────────────────────────────────────
    from .models import TableOrder, TableOrderItem
    from django.db.models import F as _F, DecimalField as _DF

    waitress_data = []
    for row in (
        TableOrder.objects.filter(business=business, created_at__date=report_date, status='SERVED')
        .values('waitress_id', 'waitress__first_name', 'waitress__last_name', 'waitress__username')
        .annotate(order_count=Count('id'))
        .order_by('waitress__first_name', 'waitress__last_name')
    ):
        rev = TableOrderItem.objects.filter(
            order__business=business,
            order__status='SERVED',
            order__created_at__date=report_date,
            order__waitress_id=row['waitress_id'],
        ).aggregate(
            r=Sum(_F('unit_price') * _F('quantity'), output_field=_DF())
        )['r'] or 0
        fname = row['waitress__first_name'] or ''
        lname = row['waitress__last_name'] or ''
        name  = (fname + ' ' + lname).strip() or row['waitress__username']
        waitress_data.append({
            'name':        name,
            'order_count': row['order_count'],
            'revenue':     float(rev),
        })

    # ── Staff / shift performance ──────────────────────────────────────────────
    from .models import Shift
    staff_data = []
    for shift in Shift.objects.filter(
        business=business, started_at__date=report_date
    ).select_related('staff').order_by('started_at'):
        # Skip kitchen-staff shifts — they belong in the kitchen board report
        try:
            if shift.staff.userprofile.role == 'kitchen':
                continue
        except Exception:
            pass
        shift_end = shift.ended_at or timezone.now()
        st = Transaction.objects.filter(
            business=business,
            created_at__gte=shift.started_at,
            created_at__lte=shift_end,
            type='Issue',
            item__store__is_kitchen=False,
        )
        delta   = shift_end - shift.started_at
        h, rem  = divmod(int(delta.total_seconds()), 3600)
        m       = rem // 60
        dur_str = f"{h}h {m:02d}m{' (ongoing)' if not shift.ended_at else ''}"
        staff_data.append({
            'name':    shift.staff.get_full_name() or shift.staff.username,
            'started': shift.started_at,
            'ended':   shift.ended_at,
            'duration': dur_str,
            'cups':    st.filter(keg_serving='cup').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'pints':   st.filter(keg_serving='pint').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'jugs':    st.filter(keg_serving='jug').aggregate(n=Sum('keg_qty'))['n'] or 0,
            'revenue': float(st.exclude(payment_method='void').aggregate(r=Sum('sale_amount'))['r'] or 0),
        })

    return render(request, 'core/bar/bar_daily_report.html', {
        'report_date':    report_date,
        'barrels_opened': barrels_opened,
        'cups':           cups,
        'jugs':           jugs,
        'pints':          pints,
        'total_revenue':  total_revenue,
        'per_barrel':     per_barrel,
        'waitress_data':  waitress_data,
        'staff_data':     staff_data,
        'today':          timezone.localdate(),
    })


# ── Target Recommendation API (Sprint 6) ─────────────────────────────────────

@login_required
def keg_target_recommendation(request, item_id):
    """
    Returns per-preset revenue rates for a keg item so the receive modal can
    display a live achievable-target hint based on barrel volume and presets.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False}, status=403)

    item = get_object_or_404(Item, id=item_id, store__business=up.business)
    presets = item.portion_presets.filter(quantity_consumed__gt=0).order_by('price')

    rates = []
    for p in presets:
        qty_ml = float(p.quantity_consumed)
        price  = float(p.price)
        if qty_ml > 0:
            rates.append({
                'label':       p.label,
                'price':       price,
                'qty_ml':      qty_ml,
                'rate_per_ml': round(price / qty_ml, 6),
            })

    return JsonResponse({'ok': True, 'presets': rates})


# ── Barrel Reconciliation (Sprint 6) ─────────────────────────────────────────

@login_required
def keg_reconciliation(request):
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return redirect('bar_board')

    business = up.business
    today = timezone.localdate()

    # ── Filters ───────────────────────────────────────────────────────────────
    # Default: current month
    default_from = today.replace(day=1)
    default_to   = today

    try:
        date_from = date_type.fromisoformat(request.GET.get('from', ''))
    except ValueError:
        date_from = default_from
    try:
        date_to = date_type.fromisoformat(request.GET.get('to', ''))
    except ValueError:
        date_to = default_to

    status_filter = request.GET.get('status', '')   # '' = all
    item_filter   = request.GET.get('item', '')     # item id or ''

    qs = KegBarrel.objects.filter(
        business=business,
        received_on__gte=date_from,
        received_on__lte=date_to,
    ).select_related('item').prefetch_related('weight_readings').order_by('-received_on', '-tapped_at')

    if status_filter in ('SEALED', 'TAPPED', 'DEPLETED', 'RETURNED'):
        qs = qs.filter(status=status_filter)

    if item_filter:
        qs = qs.filter(item_id=item_filter)

    # ── Build per-barrel metrics ───────────────────────────────────────────────
    barrels = []
    total_cost = total_revenue_sum = total_profit = 0.0

    for b in qs:
        bv = keg_metrics.barrel_variance(b)
        profit           = bv.revenue - bv.cost
        margin           = (profit / bv.cost * 100) if bv.cost else 0.0
        markup           = (bv.revenue / bv.cost) if bv.cost else 0.0
        revenue_pct      = (bv.revenue / bv.target * 100) if bv.target else 0.0
        remaining_target = max(0.0, bv.target - bv.revenue)

        barrels.append({
            'barrel':           b,
            'cost':             bv.cost,
            'revenue':          bv.revenue,
            'target':           bv.target,
            'profit':           profit,
            'margin':           margin,
            'markup':           markup,
            'revenue_pct':      revenue_pct,
            'remaining_target': remaining_target,
            'net_vol_l':        bv.net_vol_l,
            'book_l':           bv.book_l,
            'scale_l':          bv.scale_l,
            'variance_l':       bv.variance_l,
            'has_weight':       bv.has_weight,
            'wastage_l':        bv.wastage_l,
            'wastage_kes':      bv.wastage_kes,
            'wastage_pct':      bv.wastage_pct,
            'cups':             bv.cups,
            'pints':            bv.pints,
            'jugs':             bv.jugs,
        })

        total_cost         += bv.cost
        total_revenue_sum  += bv.revenue
        total_profit       += profit

    total_margin = (total_profit / total_cost * 100) if total_cost else 0.0

    # ── Item dropdown for filter ───────────────────────────────────────────────
    keg_item_ids = KegBarrel.objects.filter(business=business).values_list('item_id', flat=True).distinct()
    keg_items = Item.objects.filter(id__in=keg_item_ids).order_by('description')

    baseline = keg_metrics.business_keg_loss_baseline(business)
    baseline_pct = baseline['baseline_pct']
    for row in barrels:
        wp = row['wastage_pct']
        row['vs_baseline'] = round(wp - baseline_pct, 1) if wp is not None else None

    return render(request, 'core/bar/keg_reconciliation.html', {
        'barrels':         barrels,
        'baseline':        baseline,
        'date_from':       date_from,
        'date_to':         date_to,
        'status_filter':   status_filter,
        'item_filter':     item_filter,
        'keg_items':       keg_items,
        'total_cost':      total_cost,
        'total_revenue':   total_revenue_sum,
        'total_profit':    total_profit,
        'total_margin':    total_margin,
        'barrel_count':    len(barrels),
        'today':           today,
    })


# ── Barrel Detail — shift-by-shift spillage/variance breakdown ────────────────

@login_required
def keg_barrel_detail(request, barrel_id):
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return redirect('bar_board')

    business = up.business
    barrel = get_object_or_404(
        KegBarrel.objects.select_related('item', 'received_by').prefetch_related('weight_readings'),
        id=barrel_id, business=business,
    )

    net_vol_l  = float(barrel.net_volume_l)
    cost       = float(barrel.cost_price or 0)
    revenue    = float(barrel.revenue_collected or 0)
    target     = float(barrel.target_revenue or 0)
    book_ml    = float(barrel.volume_dispensed_ml or 0)

    # ── Theoretical max revenue from presets ──────────────────────────────────
    FOAM_FACTOR = 0.90  # 10% allowance for foam / spillage
    net_vol_ml = net_vol_l * 1000.0
    preset_rates = []
    for p in barrel.item.portion_presets.filter(quantity_consumed__gt=0):
        qty_ml = float(p.quantity_consumed)
        price  = float(p.price)
        if qty_ml > 0:
            rate = price / qty_ml
            max_servings = int(net_vol_ml / qty_ml)
            preset_rates.append({
                'label':        p.label,
                'price':        price,
                'qty_ml':       qty_ml,
                'max_servings': max_servings,
                'gross_max':    round(max_servings * price),
                'realistic_max': round(max_servings * price * FOAM_FACTOR),
                'rate':         rate,
            })

    shortfall   = max(0.0, target - revenue)
    pct_achieved = (revenue / target * 100) if target else 0.0

    if preset_rates:
        max_rate = max(p['rate'] for p in preset_rates)
        min_rate = min(p['rate'] for p in preset_rates)
        theoretical_gross_max     = round(net_vol_ml * max_rate)
        theoretical_realistic_max = round(net_vol_ml * max_rate * FOAM_FACTOR)
        theoretical_realistic_min = round(net_vol_ml * min_rate * FOAM_FACTOR)
        target_is_unrealistic = target > theoretical_gross_max
        # How many additional servings of each type to close the shortfall
        if shortfall > 0:
            for p in preset_rates:
                p['additional_needed'] = int(shortfall / p['price']) + 1
        else:
            for p in preset_rates:
                p['additional_needed'] = 0
    else:
        theoretical_gross_max = theoretical_realistic_max = theoretical_realistic_min = None
        target_is_unrealistic = False

    # All transactions for this barrel, oldest first (void excluded)
    txns = Transaction.objects.filter(
        business=business, keg_barrel=barrel,
    ).exclude(payment_method='void').order_by('created_at')

    # Barrel lifespan boundaries
    barrel_start = barrel.tapped_at or timezone.make_aware(
        timezone.datetime.combine(barrel.received_on, timezone.datetime.min.time())
    )
    barrel_end = barrel.closed_at or timezone.now()

    # Shifts that overlapped with this barrel being active
    from .models import Shift
    shifts = Shift.objects.filter(
        business=business,
        started_at__lt=barrel_end,
    ).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=barrel_start)
    ).select_related('staff').order_by('started_at')

    # Weight readings for this barrel, oldest first — enrich with implied remaining
    readings_raw = list(barrel.weight_readings.order_by('recorded_at').select_related('recorded_by'))
    tare = float(barrel.tare_weight_kg)
    readings = []
    for r in readings_raw:
        remaining_l = max(0.0, float(r.weight_kg) - tare)
        readings.append({'reading': r, 'remaining_l': remaining_l})

    # Per-shift breakdown — delegate to keg_metrics (reads_raw passed to avoid N+1 re-query)
    shift_rows = []
    for shift in shifts:
        sv = keg_metrics.shift_barrel_variance(shift, barrel, readings=readings_raw, barrel_txns=txns)
        if sv is None:
            continue
        delta = sv.window_end - sv.window_start
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        shift_rows.append({
            'staff':       sv.staff_name,
            'started':     sv.window_start,
            'ended':       sv.window_end,
            'is_ongoing':  shift.ended_at is None,
            'duration':    f"{h}h {m:02d}m",
            'cups':        sv.cups,
            'pints':       sv.pints,
            'jugs':        sv.jugs,
            'revenue':     sv.revenue,
            'book_l':      sv.book_ml / 1000.0,
            'scale_l':     sv.scale_ml / 1000.0 if sv.scale_ml is not None else None,
            'wastage_l':   sv.wastage_l,
            'wastage_kes': sv.wastage_kes,
            'has_weight':  sv.has_weight,
        })

    # Overall wastage — delegate to keg_metrics
    bv_total = keg_metrics.barrel_variance(barrel)
    total_wastage_l   = bv_total.wastage_l
    total_wastage_kes = bv_total.wastage_kes
    total_wastage_pct = bv_total.wastage_pct

    profit = revenue - cost
    margin = (profit / cost * 100) if cost else 0.0

    baseline = keg_metrics.business_keg_loss_baseline(barrel.business)
    baseline_vs = (round(float(total_wastage_pct) - baseline['baseline_pct'], 1)
                   if total_wastage_pct is not None else None)

    # Per-barrel cup cost allocation (cups logged against this specific barrel only)
    barrel_cup_logs = BarCupLog.objects.filter(barrel=barrel)
    barrel_cup_cost_300 = float(barrel_cup_logs.filter(cup_size='300').aggregate(
        s=Sum('total_cost'))['s'] or 0)
    barrel_cup_cost_500 = float(barrel_cup_logs.filter(cup_size='500').aggregate(
        s=Sum('total_cost'))['s'] or 0)
    barrel_cup_total_cost = barrel_cup_cost_300 + barrel_cup_cost_500

    return render(request, 'core/bar/keg_barrel_detail.html', {
        'barrel':                     barrel,
        'baseline':                   baseline,
        'baseline_vs':                baseline_vs,
        'net_vol_l':                  net_vol_l,
        'cost':                       cost,
        'revenue':                    revenue,
        'target':                     target,
        'profit':                     profit,
        'margin':                     margin,
        'book_l':                     book_ml / 1000.0,
        'total_wastage_l':            total_wastage_l,
        'total_wastage_kes':          total_wastage_kes,
        'total_wastage_pct':          total_wastage_pct,
        'shift_rows':                 shift_rows,
        'readings':                   readings,
        'cups':                       barrel.cups_dispensed or 0,
        'pints':                      barrel.pints_dispensed or 0,
        'jugs':                       barrel.jugs_dispensed or 0,
        'preset_rates':               preset_rates,
        'theoretical_gross_max':      theoretical_gross_max,
        'theoretical_realistic_max':  theoretical_realistic_max,
        'theoretical_realistic_min':  theoretical_realistic_min,
        'target_is_unrealistic':      target_is_unrealistic,
        'shortfall':                  shortfall,
        'pct_achieved':               pct_achieved,
        'barrel_cup_total_cost':      barrel_cup_total_cost,
        'barrel_cup_cost_300':        barrel_cup_cost_300,
        'barrel_cup_cost_500':        barrel_cup_cost_500,
    })


# ── Bottle / Stock Breakage ──────────────────────────────────────────────────

@login_required
@require_POST
def record_breakage(request):
    """Record a bottle/stock damage event as a Wastage transaction."""
    up = _get_up(request)
    if not up:
        return JsonResponse({"ok": False, "error": "Auth required"}, status=403)

    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    business = up.business

    # Server-side double-submit backstop — see core/idempotency.py. This form has
    # no natural "already done" guard (unlike e.g. settle_tab's tab.status), so a
    # double-tap or network retry would silently double-record the wastage,
    # inflating wastage_loss in the P&L (bar-audit finding, 2026-07-19).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    item = Item.objects.filter(
        id=request.POST.get("item_id"),
        store__business=business,
    ).first()
    if not item:
        return JsonResponse({"ok": False, "error": "Item not found"}, status=404)

    try:
        qty = Decimal(str(request.POST.get("qty", "1")))
        if qty <= 0:
            raise ValueError
    except (ValueError, Exception):
        return JsonResponse({"ok": False, "error": "Invalid quantity"}, status=400)

    note = request.POST.get("note", "").strip()

    # qty must be stored negative — Wastage reduces stock.
    # recorded_by is NOT a Transaction field; recipient carries the note instead.
    Transaction.objects.create(
        business=business,
        item=item,
        type="Wastage",
        qty=-qty,
        recipient=note or "Breakage / damage",
        payment_method="cash",
    )

    return JsonResponse({"ok": True})


# ── F2 — Staff Shrinkage Leaderboard ────────────────────────────────────────

@login_required
def bar_shrinkage_report(request):
    """Owner/manager: per-staff keg loss leaderboard for a date range."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return redirect('bar_board')

    business = up.business
    today = timezone.localdate()
    default_from = today.replace(day=1)
    default_to   = today

    try:
        date_from = date_type.fromisoformat(request.GET.get('from', ''))
    except ValueError:
        date_from = default_from
    try:
        date_to = date_type.fromisoformat(request.GET.get('to', ''))
    except ValueError:
        date_to = default_to

    # Previous period for trend comparison (same number of days)
    period_days = max(1, (date_to - date_from).days + 1)
    prev_to     = date_from - timedelta(days=1)
    prev_from   = prev_to   - timedelta(days=period_days - 1)

    rows      = keg_metrics.staff_shrinkage(business, date_from, date_to)
    prev_rows = keg_metrics.staff_shrinkage(business, prev_from, prev_to)
    prev_by_staff = {r.staff_id: r for r in prev_rows}

    leaderboard = []
    for r in rows:
        prev = prev_by_staff.get(r.staff_id)
        leaderboard.append({
            'row':          r,
            'trend_kes':    r.loss_kes - (prev.loss_kes if prev else 0.0),
            'low_coverage': r.coverage_pct < 60.0,
        })

    return render(request, 'core/bar/bar_shrinkage.html', {
        'leaderboard': leaderboard,
        'date_from':   date_from,
        'date_to':     date_to,
        'prev_from':   prev_from,
        'prev_to':     prev_to,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Sprint F4 — End-of-night Z-report
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def bar_z_report(request):
    """End-of-night Z-report. Owner sees all bar shifts for the day; staff sees own shift only."""
    from .shift_views import _reconcile

    up = _get_up(request)
    if not up:
        return redirect('login')

    business = up.business
    is_owner = getattr(up, 'is_owner_or_manager', False)

    # Date filter — default today
    date_str = request.GET.get('date', '')
    try:
        report_date = date_type.fromisoformat(date_str)
    except ValueError:
        report_date = timezone.localdate()

    day_start = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.min.time()))
    day_end   = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.max.time()))

    # Fetch bar shifts for the day (exclude kitchen shifts)
    qs = Shift.objects.filter(
        business=business,
        started_at__gte=day_start,
        started_at__lte=day_end,
    ).select_related('staff', 'store').order_by('started_at')

    if not is_owner:
        qs = qs.filter(staff=request.user)

    # Build per-shift rows, excluding kitchen-staff shifts from the bar report
    shift_rows = []
    day_cash = day_mpesa = day_credit = day_total = 0.0
    day_opening_float = 0.0
    day_expected_cash = 0.0
    day_petty_cash = 0.0
    counted_shifts = 0

    for shift in qs:
        try:
            is_kitchen_shift = shift.staff.userprofile.role == 'kitchen'
        except Exception:
            is_kitchen_shift = False
        if is_kitchen_shift:
            continue

        rec = _reconcile(shift)

        # Petty cash approved on this date attributed to this shift window
        shift_end = shift.ended_at or timezone.now()
        petty_qs = PettyCash.objects.filter(
            business=business,
            status='approved',
            created_at__gte=shift.started_at,
            created_at__lte=shift_end,
        )
        petty_total = float(petty_qs.aggregate(t=Sum('amount'))['t'] or 0)

        # Adjusted expected cash includes petty cash out
        adj_expected = round(rec['expected_cash'] - petty_total, 2)
        adj_variance = (round(float(shift.closing_cash_counted) - adj_expected, 2)
                        if shift.closing_cash_counted is not None else None)

        shift_rows.append({
            'shift':          shift,
            'cash_sales':     rec['cash_sales'],
            'mpesa_sales':    rec['mpesa_sales'],
            'credit_sales':   rec['credit_sales'],
            'total_sales':    rec['total_sales'],
            'opening_float':  float(shift.opening_float),
            'offline_adj':    rec['offline_adj'],
            'petty_cash':     petty_total,
            'expected_cash':  adj_expected,
            'closing_counted': float(shift.closing_cash_counted) if shift.closing_cash_counted is not None else None,
            'variance':       adj_variance,
            'elapsed':        rec['elapsed'],
            'status':         shift.status,
        })

        day_cash         += rec['cash_sales']
        day_mpesa        += rec['mpesa_sales']
        day_credit       += rec['credit_sales']
        day_total        += rec['total_sales']
        day_opening_float += float(shift.opening_float)
        day_expected_cash += adj_expected
        day_petty_cash   += petty_total
        counted_shifts   += 1

    # Open tabs (cash still on the floor)
    open_tabs = BarTab.objects.filter(
        business=business, status='OPEN',
        opened_at__gte=day_start,
    ).prefetch_related('entries')
    open_tab_count = open_tabs.count()
    open_tab_kes = sum(
        float(e.amount) for tab in open_tabs for e in tab.entries.filter(is_paid=False)
    )

    # Keg variance KES for the day — currently tapped barrels + barrels closed today.
    # Intentionally excludes historical barrels closed before report_date so the
    # number shown is tonight's active wastage, not a lifetime accumulation.
    barrels = KegBarrel.objects.filter(
        business=business,
    ).filter(
        Q(status='TAPPED') |
        Q(status__in=['DEPLETED', 'RETURNED'], closed_at__date=report_date)
    ).prefetch_related('weight_readings').select_related('item')
    day_keg_variance_kes = 0.0
    for b in barrels:
        bv = keg_metrics.barrel_variance(b)
        if bv.wastage_kes is not None:
            day_keg_variance_kes += bv.wastage_kes

    # F5 — bottle/spirits variance for the day from ShiftStockCount
    from .models import ShiftStockCount
    bottle_counts_today = ShiftStockCount.objects.filter(
        shift__business=business,
        shift__started_at__gte=day_start,
        shift__started_at__lte=day_end,
        item__bottle_envelope=True,
    ).select_related('item').prefetch_related('item__portion_presets')
    day_bottle_variance_kes = 0.0
    for sc in bottle_counts_today:
        loss_units = max(0.0, float(sc.book_balance) - float(sc.actual_count))
        day_bottle_variance_kes += loss_units * sc.item.bottle_expected_revenue_per_unit()

    # F6 — M-Pesa cross-check: STK push completions vs staff-recorded M-Pesa sales
    stk_mpesa_total = float(
        Payment.objects.filter(
            business=business,
            method='mpesa',
            status='completed',
            created_at__gte=day_start,
            created_at__lte=day_end,
        ).aggregate(total=Sum('amount'))['total'] or 0
    )
    mpesa_cross_check_gap = round(day_mpesa - stk_mpesa_total, 2)
    has_stk_data = stk_mpesa_total > 0

    # DJ / MC entertainment costs for the day
    from .models import PerformerSession as _PS
    _ent_aggs = _PS.objects.filter(
        business=business, date=report_date,
    ).exclude(status=_PS.STATUS_CANCELLED).aggregate(
        paid=Sum('agreed_fee', filter=Q(payment_status=_PS.PAYMENT_PAID)),
        unpaid=Sum('agreed_fee', filter=Q(payment_status=_PS.PAYMENT_PENDING)),
    )
    day_entertainment_paid   = float(_ent_aggs['paid']   or 0)
    day_entertainment_unpaid = float(_ent_aggs['unpaid'] or 0)

    # Owner consumption for the day
    owner_consumption_txns = Transaction.objects.filter(
        business=business,
        type='OwnerConsumption',
        date=report_date,
    ).select_related('item', 'recorded_by').order_by('item__description')
    day_owner_consumption_count = owner_consumption_txns.count()

    # Yesterday for navigation
    yesterday = report_date - timedelta(days=1)
    tomorrow  = report_date + timedelta(days=1)

    return render(request, 'core/bar/bar_z_report.html', {
        'report_date':         report_date,
        'yesterday':           yesterday,
        'tomorrow':            tomorrow,
        'today':               timezone.localdate(),
        'shift_rows':          shift_rows,
        'is_owner':            is_owner,
        'day_cash':            round(day_cash, 2),
        'day_mpesa':           round(day_mpesa, 2),
        'day_credit':          round(day_credit, 2),
        'day_total':           round(day_total, 2),
        'day_opening_float':   round(day_opening_float, 2),
        'day_expected_cash':   round(day_expected_cash, 2),
        'day_petty_cash':      round(day_petty_cash, 2),
        'open_tab_count':      open_tab_count,
        'open_tab_kes':        round(open_tab_kes, 2),
        'day_keg_variance_kes':    round(day_keg_variance_kes, 2),
        'day_bottle_variance_kes': round(day_bottle_variance_kes, 2),
        'stk_mpesa_total':         round(stk_mpesa_total, 2),
        'mpesa_cross_check_gap':   mpesa_cross_check_gap,
        'has_stk_data':            has_stk_data,
        'kra_pin':                   business.kra_pin or '',
        'counted_shifts':            counted_shifts,
        'business':                  business,
        'day_entertainment_paid':          round(day_entertainment_paid, 2),
        'day_entertainment_unpaid':        round(day_entertainment_unpaid, 2),
        'owner_consumption_txns':          owner_consumption_txns,
        'day_owner_consumption_count':     day_owner_consumption_count,
    })


@login_required
@require_POST
def bar_z_report_share(request):
    """Send the day's Z-report summary SMS to the owner's phone."""
    from .notifications import normalize_ke_phone, send_sms_notification
    from .shift_views import _reconcile
    from accounts.models import UserProfile

    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    business = up.business

    date_str = request.POST.get('date', '')
    try:
        report_date = date_type.fromisoformat(date_str)
    except ValueError:
        report_date = timezone.localdate()

    day_start = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.min.time()))
    day_end   = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.max.time()))

    shifts = Shift.objects.filter(
        business=business, started_at__gte=day_start, started_at__lte=day_end,
    ).select_related('staff')

    total_sales = total_cash = total_mpesa = 0.0
    for shift in shifts:
        try:
            if shift.staff.userprofile.role == 'kitchen':
                continue
        except Exception:
            pass
        rec = _reconcile(shift)
        total_sales += rec['total_sales']
        total_cash  += rec['cash_sales']
        total_mpesa += rec['mpesa_sales']

    open_tabs = BarTab.objects.filter(
        business=business, status='OPEN', opened_at__gte=day_start,
    ).count()

    msg = (
        f"Z-Report {report_date} — {business.name}\n"
        f"Jumla: KES {total_sales:,.0f} "
        f"(Cash {total_cash:,.0f} | M-Pesa {total_mpesa:,.0f})\n"
        f"Tabs bado wazi: {open_tabs}\n"
        f"Tuma na Duka Mwecheche"
    )

    owner_up = UserProfile.objects.filter(business=business, role='owner').select_related('user').first()
    phone = normalize_ke_phone(getattr(owner_up, 'phone', '') or '') if owner_up else None
    if not phone:
        return JsonResponse({'ok': False, 'error': 'Hakuna nambari ya simu ya mmiliki.'})

    try:
        send_sms_notification(msg, phone)
        return JsonResponse({'ok': True})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)})


@login_required
def voided_tabs_list(request):
    """Owner/manager: history of all voided (written-off) tabs with reason and items."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return redirect('bar_board')

    business = up.business
    today = timezone.localdate()

    try:
        date_from = date_type.fromisoformat(request.GET.get('from', ''))
    except ValueError:
        date_from = today - timedelta(days=30)
    try:
        date_to = date_type.fromisoformat(request.GET.get('to', ''))
    except ValueError:
        date_to = today

    voided = (
        BarTab.objects
        .filter(
            business=business,
            status='VOID',
            settled_at__date__gte=date_from,
            settled_at__date__lte=date_to,
        )
        .select_related('served_by', 'customer')
        .prefetch_related('entries')
        .order_by('-settled_at')
    )

    total_kes = sum(float(t.total()) for t in voided)

    return render(request, 'core/bar/voided_tabs.html', {
        'voided_tabs': voided,
        'total_kes': total_kes,
        'date_from': date_from,
        'date_to': date_to,
        'today': today,
    })


@login_required
def wall_qr_print_page(request):
    """Standalone, single-page print view for the Wall Tab QR poster — owner-only.

    Separated from payment_settings.html (2026-07-23, live report: "print shows
    4 blank pages then a tiny QR on the 5th") because trying to print just one
    small section out of that very long settings page was unreliable: the fix
    used `visibility:hidden` on the rest of the page (needed so the box could
    reset its own visibility regardless of nesting depth — see the CLAUDE.md
    Known Issues entry on this) and `position:absolute` to pull the QR box to
    the top — but `position:absolute` places an element relative to its
    nearest POSITIONED ancestor, not the page, and any Bootstrap card/container
    with its own `position:relative` between <body> and the QR box anchors it
    there instead. Since `visibility:hidden` (unlike `display:none`) still
    reserves layout space, the page's full height (every other settings
    section) survives into the print output, so the browser paginates across
    however many pages that height spans — and the QR box, anchored to
    whichever ancestor it landed near (deep in the page, close to where the
    Wall Tab QR card itself sits), printed small on a late page instead of
    large on page one. A standalone page with nothing else on it sidesteps the
    whole class of problem — same pattern already proven working for
    session_promo_page.html's poster print.
    """
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return redirect('home')
    business = up.business
    find_tab_url = request.build_absolute_uri(f'/bar/find-tab/{business.id}/')
    auto_print = request.GET.get('print') == '1'
    return render(request, 'core/wall_qr_print.html', {
        'business': business,
        'find_tab_url': find_tab_url,
        'auto_print': auto_print,
    })


def find_tab_public(request, business_id):
    """Public landing page for the wall-mounted bar QR — no login required.

    Renders a search form. The actual lookup is handled by find_tab_search (AJAX).
    """
    from accounts.models import Business as _Business
    business = get_object_or_404(_Business, id=business_id)
    open_count = BarTab.objects.filter(business=business, status='OPEN').count()
    return render(request, 'core/find_tab.html', {
        'business': business,
        'open_count': open_count,
        'search_url': f'/bar/find-tab/{business_id}/search/',
    })


def _resolve_tab_public_url(tab):
    """Where should a customer looking up this tab land?

    Prefer the tab's own Receipt page (/r/<token>/) — it already has the full
    payment UI (STK, QR, cash request). Only a tab with zero sales yet (no
    receipt issued) falls back to the bare read-only /tab/<token>/ page.
    """
    from core.tab_receipts import _safe_linked_query
    rcpt = Receipt.objects.filter(business=tab.business, meta__tab_id=tab.id).first()
    if rcpt is None:
        _linked = _safe_linked_query(
            Receipt.objects.filter(business=tab.business), [tab.id]
        )
        rcpt = _linked[0] if _linked else None
    if rcpt:
        return f'/r/{rcpt.token}/'
    if tab.tab_receipt_token:
        return f'/tab/{tab.tab_receipt_token}/'
    return None


def _findable_tabs_qs(business):
    """OPEN tabs, plus SETTLED tabs that were converted to debt (shift-close
    auto-convert, or manual "Geuza Deni") and still carry an unpaid balance.

    Bug found while auditing BillScan end-to-end (2026-07-22): this used to
    be a plain status='OPEN' filter — the exact same status a tab keeps its
    PIN under is meaningless once conversion flips it to SETTLED, so a
    customer whose tab was auto-converted at shift close (precisely the
    "abandoned tab" scenario this whole conversion path exists to catch)
    could no longer find their own bill by scanning the wall QR and typing
    the still-valid PIN they were given — "PIN not found" despite a real,
    payable balance. Mirrors the same "effective status" reasoning
    receipt_views._get_live_tab_state already uses when a tab is reached
    via its receipt token instead of a fresh PIN/name lookup — VOID tabs
    and fully-paid SETTLED tabs correctly stay excluded either way."""
    from django.db.models import Exists, OuterRef, Q
    unpaid_exists = BarTabEntry.objects.filter(
        tab=OuterRef('pk'), is_paid=False,
    ).exclude(payment_method='void')
    return (
        BarTab.objects.filter(business=business)
        .annotate(_has_unpaid=Exists(unpaid_exists))
        .filter(Q(status='OPEN') | Q(status='SETTLED', _has_unpaid=True))
    )


def find_tab_search(request, business_id):
    """Public AJAX name-or-PIN lookup for find_tab_public — no login required.

    GET ?q=<query>
    - 4-digit numeric string → PIN lookup → returns {'redirect': '/tab/<token>/'}
    - Text string → name icontains search → returns {'tabs': [...]}
    Rate-limited to 5 calls/minute per IP to prevent enumeration.
    """
    from django.core.cache import cache
    from accounts.models import Business as _Business

    ip = request.META.get('HTTP_X_FORWARDED_FOR', '') or request.META.get('REMOTE_ADDR', '')
    ip = ip.split(',')[0].strip()
    rate_key = f'find_tab_rl:{business_id}:{ip}'
    calls = cache.get(rate_key, 0)
    if calls >= 5:
        return JsonResponse({'error': 'Subiri kidogo', 'tabs': []}, status=429)
    cache.set(rate_key, calls + 1, timeout=60)

    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'tabs': []})

    business = get_object_or_404(_Business, id=business_id)

    # PIN lookup: exactly 4 digits → direct redirect
    if q.isdigit() and len(q) == 4:
        tab = _findable_tabs_qs(business).filter(tab_pin=q).first()
        url = _resolve_tab_public_url(tab) if tab else None
        if url:
            return JsonResponse({'tabs': [], 'redirect': url})
        return JsonResponse({'tabs': [], 'pin_not_found': True})

    # Name search: case-insensitive substring match
    tabs = _findable_tabs_qs(business).filter(
        customer_name__icontains=q,
    ).order_by('-opened_at')[:10]

    results = []
    for t in tabs:
        url = _resolve_tab_public_url(t)
        if not url:
            continue  # pre-migration 0092 tabs have no token — skip silently
        results.append({
            'name': t.customer_name or '—',
            'url': url,
            'opened_at': t.opened_at.strftime('%I:%M %p'),
        })
    return JsonResponse({'tabs': results})
