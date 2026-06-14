"""
Bar & Club Module — Keg lifecycle views and Bar Board.

Sprint 2: board API, receive/tap/weigh/discard, Bar Board HTML + sell (cash/mpesa).
Sprint 3: tab sell path — BarTab CRUD, tabs drawer, tick-to-pay, convert-to-debt.
Sprint 4: shift handover.
"""
import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BarCupLog, BarTab, BarTabEntry, Customer, Item, ItemPortionPreset, KegBarrel, KegWeightReading, Transaction


def _get_up(request):
    from .views import get_user_profile
    return get_user_profile(request)


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
        .prefetch_related('portion_presets', 'keg_barrels', 'keg_barrels__cup_logs')
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

        # ── Cup stats for the tapped barrel ──────────────────────────────
        cup_300_bought = 0
        cup_500_bought = 0
        cup_300_cost   = 0.0
        cup_500_cost   = 0.0
        if primary:
            for log in primary.cup_logs.all():
                if log.cup_size == '300':
                    cup_300_bought += log.qty
                    cup_300_cost   += float(log.total_cost)
                else:
                    cup_500_bought += log.qty
                    cup_500_cost   += float(log.total_cost)
            cups_used = primary.cups_dispensed or 0
            jugs_used = primary.jugs_dispensed or 0
        else:
            cups_used = 0
            jugs_used = 0

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
            # Cup / jug tracking
            'cups_300_bought': cup_300_bought,
            'cups_500_bought': cup_500_bought,
            'cups_300_cost':   round(cup_300_cost, 2),
            'cups_500_cost':   round(cup_500_cost, 2),
            'cups_used':       cups_used,
            'jugs_used':       jugs_used,
        })

    open_tabs_qs = BarTab.objects.filter(business=business, status='OPEN')
    return JsonResponse({
        'kegs': kegs,
        'can_receive': bool(getattr(up, 'is_owner', False)),
        'open_tabs': open_tabs_qs.count(),
        'open_tab_names': list(open_tabs_qs.values_list('customer_name', flat=True).distinct()),
    })


# ── Bar Board HTML + sell ─────────────────────────────────────────────────────

@login_required
def bar_board(request):
    """Main bar board — GET renders the page, POST processes a keg cart sale."""
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    is_owner = bool(getattr(up, 'is_owner', False))
    success_data = None

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

        cart_json = request.POST.get('keg_cart', '[]')
        payment_method = request.POST.get('payment_method', 'cash')
        tab_customer = (request.POST.get('tab_customer') or '').strip()
        tab_server = (request.POST.get('tab_server') or '').strip()

        try:
            cart = json.loads(cart_json)
        except Exception:
            cart = []

        # Resolve tab for tab-payment sales
        active_tab = None
        if payment_method == 'tab' and tab_customer:
            active_tab = BarTab.objects.filter(
                business=business,
                customer_name__iexact=tab_customer,
                status='OPEN',
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
                active_tab = BarTab.objects.create(
                    business=business,
                    store=first_barrel.store if first_barrel else None,
                    customer_name=tab_customer,
                    server_name=tab_server,
                    served_by=request.user if not tab_server else None,
                )

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

            barrel.record_sale(preset, qty, payment_method, request.user, tab=active_tab)
            amount = Decimal(str(float(preset.price) * qty))
            total_revenue += amount
            receipt_lines.append({
                'name': f"{barrel.item.description} — {preset.label} ×{qty}",
                'subtotal': float(amount),
                'barrel_id': barrel.id,
            })

        if receipt_lines:
            success_data = {
                'lines': receipt_lines,
                'total': float(total_revenue),
                'payment_method': payment_method,
                'tab_customer': tab_customer if active_tab else '',
                'timestamp': timezone.localtime(timezone.now()).strftime('%H:%M'),
            }

    return render(request, 'core/bar/bar_board.html', {
        'is_owner': is_owner,
        'business': business,
        'success_data': success_data,
        'current_user_id': request.user.id,
    })


# ── Receive barrels ───────────────────────────────────────────────────────────

@login_required
@require_POST
def receive_barrel(request):
    """Owner receives N sealed barrels from the distributor."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    business = up.business
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

    for _ in range(count):
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
    """Owner opens (taps) a sealed barrel. Enforces one TAPPED barrel per item."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

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
    return JsonResponse({'ok': True, 'barrel_id': barrel.id, 'status': barrel.status})


# ── Weigh / spot-check ────────────────────────────────────────────────────────

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
    if not up.is_owner:
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
    if variance_pct <= tolerance:
        flag = 'ok'
    elif variance_pct <= tolerance * 2:
        flag = 'warning'
    else:
        flag = 'danger'

    return JsonResponse({
        'ok': True,
        'dispensed_l':       round(dispensed_l, 1),
        'net_remaining_kg':  round(net_remaining_kg, 2),
        'tare_kg':           tare_kg,
        'expected_rev':      round(expected_rev, 0),
        'recorded_rev':      round(recorded_rev, 0),
        'variance_kes':      round(variance_kes, 0),
        'variance_pct':      round(variance_pct, 1),
        'flag':              flag,
        'weight_kg':         float(weight_kg),
        'remaining_envelope': round(barrel.remaining_envelope(), 0),
    })


# ── Edit barrel parameters ───────────────────────────────────────────────────

@login_required
@require_POST
def edit_barrel(request, barrel_id):
    """Owner corrects a barrel's financial parameters (cost, target, weight)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

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
    """Owner writes off a barrel (returned, spoiled, wrong delivery)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

    if barrel.status == 'DEPLETED':
        return JsonResponse({'ok': False, 'error': 'Barrel imekwisha'}, status=400)

    reason = (request.POST.get('reason') or 'Imerudishwa / discarded').strip()
    barrel.close(reason=reason)
    return JsonResponse({'ok': True, 'status': barrel.status})


# ══════════════════════════════════════════════════════════════════════════════
# SPRINT 3 — Tabs: list, tick, settle, void, convert-to-debt
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def tabs_list(request):
    """AJAX GET — returns all OPEN tabs for this business with their entries."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': []})

    tabs = (
        BarTab.objects
        .filter(business=up.business, status='OPEN')
        .prefetch_related('entries')
        .order_by('-opened_at')
    )

    result = []
    for tab in tabs:
        entries = []
        for e in tab.entries.all():
            entries.append({
                'id': e.id,
                'description': e.description,
                'amount': float(e.amount),
                'is_paid': e.is_paid,
                'payment_method': e.payment_method,
            })
        result.append({
            'id': tab.id,
            'customer_name': tab.customer_name,
            'server_name': tab.server_name,
            'total': float(tab.total()),
            'unpaid_total': float(tab.unpaid_total()),
            'entries': entries,
            'opened_at': tab.opened_at.strftime('%H:%M'),
        })

    return JsonResponse({'tabs': result})


@login_required
@require_POST
def tick_entry(request, entry_id):
    """Mark a single BarTabEntry as paid."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    entry = get_object_or_404(
        BarTabEntry.objects.select_related('tab', 'transaction'),
        id=entry_id,
        tab__business=up.business,
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
    if tab_settled:
        tab.status = 'SETTLED'
        tab.settled_at = now
        tab.save(update_fields=['status', 'settled_at'])

    return JsonResponse({
        'ok': True,
        'unpaid_total': float(tab.unpaid_total()),
        'tab_settled': tab_settled,
    })


@login_required
@require_POST
def settle_tab(request, tab_id):
    """Settle all unpaid entries on a tab at once."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    tab = get_object_or_404(BarTab, id=tab_id, business=up.business, status='OPEN')

    pay = (request.POST.get('payment_method') or 'cash').strip()
    if pay not in ('cash', 'mpesa'):
        pay = 'cash'

    now = timezone.now()
    for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
        entry.is_paid = True
        entry.paid_at = now
        entry.payment_method = pay
        entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])
        entry.transaction.payment_method = pay
        entry.transaction.save(update_fields=['payment_method'])

    tab.status = 'SETTLED'
    tab.settled_at = now
    tab.save(update_fields=['status', 'settled_at'])

    return JsonResponse({'ok': True, 'total': float(tab.total())})


@login_required
@require_POST
def void_tab(request, tab_id):
    """Void a tab — owner only. Marks all unpaid entries as written off."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    tab = get_object_or_404(BarTab, id=tab_id, business=up.business, status='OPEN')
    reason = (request.POST.get('reason') or 'Imetupwa').strip()

    now = timezone.now()
    for entry in tab.entries.filter(is_paid=False):
        entry.is_paid = True
        entry.paid_at = now
        entry.payment_method = 'void'
        entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])

    tab.status = 'VOID'
    tab.settled_at = now
    tab.save(update_fields=['status', 'settled_at'])

    return JsonResponse({'ok': True, 'reason': reason})


@login_required
@require_POST
def convert_tab_to_debt(request, tab_id):
    """Convert a tab's unpaid balance to the debt tracker under a Customer."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    tab = get_object_or_404(BarTab, id=tab_id, business=up.business, status='OPEN')

    customer_name = (request.POST.get('customer_name') or tab.customer_name).strip()
    phone = (request.POST.get('phone') or '').strip()

    # Find or create the Customer record
    if phone:
        customer, _ = Customer.objects.get_or_create(
            business=up.business,
            phone=phone,
            defaults={'name': customer_name},
        )
    else:
        customer = Customer.objects.filter(
            business=up.business,
            name__iexact=customer_name,
        ).first()
        if not customer:
            customer = Customer.objects.create(
                business=up.business,
                name=customer_name,
                phone=phone,
            )

    unpaid_total = float(tab.unpaid_total())

    # Link the transactions to this customer so the debt tracker sees them
    for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
        txn = entry.transaction
        txn.recipient = customer.name
        txn.save(update_fields=['recipient'])

    tab.customer = customer
    tab.status = 'SETTLED'
    tab.settled_at = timezone.now()
    tab.save(update_fields=['customer', 'status', 'settled_at'])

    return JsonResponse({
        'ok': True,
        'customer_name': customer.name,
        'unpaid_total': unpaid_total,
        'debt_url': f'/debt/{customer.id}/',
    })


# ── Cup tracking ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def add_cups(request, barrel_id):
    """Owner logs a cup purchase for a specific barrel (300ml or 500ml)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    barrel = get_object_or_404(KegBarrel, id=barrel_id, business=up.business)

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

    BarCupLog.objects.create(
        barrel=barrel,
        business=up.business,
        cup_size=cup_size,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        note=note,
    )

    # Return updated cup stats for this barrel
    logs = barrel.cup_logs.all()
    cups_300 = sum(l.qty for l in logs if l.cup_size == '300')
    cups_500 = sum(l.qty for l in logs if l.cup_size == '500')
    cups_used = barrel.cups_dispensed or 0
    jugs_used = barrel.jugs_dispensed or 0

    return JsonResponse({
        'ok': True,
        'cups_300_bought': cups_300,
        'cups_500_bought': cups_500,
        'cups_used': cups_used,
        'jugs_used': jugs_used,
        'total_cost': float(total_cost),
    })
