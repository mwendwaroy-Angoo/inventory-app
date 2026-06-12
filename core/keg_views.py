"""
Bar & Club Module — Keg lifecycle views and Bar Board.

Sprint 2: board API, receive/tap/weigh/discard, Bar Board HTML + sell (cash/mpesa).
Sprint 3: tab sell path.  Sprint 4: shift handover.
"""
import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, ItemPortionPreset, KegBarrel, KegWeightReading, Transaction


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
            'tapped_barrel_cost':   float(primary.cost_price) if primary else 0.0,
            'tapped_barrel_target': float(primary.target_revenue) if primary else 0.0,
            'next_sealed_cost':     float(next_sealed.cost_price) if next_sealed else 0.0,
            'next_sealed_target':   float(next_sealed.target_revenue) if next_sealed else 0.0,
            'next_sealed_gross':    float(next_sealed.gross_weight_kg) if next_sealed else 0.0,
            'next_sealed_tare':     float(next_sealed.tare_weight_kg) if next_sealed else 0.0,
        })

    return JsonResponse({
        'kegs': kegs,
        'can_receive': bool(getattr(up, 'is_owner', False)),
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
        cart_json = request.POST.get('keg_cart', '[]')
        payment_method = request.POST.get('payment_method', 'cash')

        try:
            cart = json.loads(cart_json)
        except Exception:
            cart = []

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

            barrel.record_sale(preset, qty, payment_method, request.user)
            amount = Decimal(str(float(preset.price) * qty))
            total_revenue += amount
            receipt_lines.append({
                'name': f"{barrel.item.description} — {preset.label} ×{qty}",
                'subtotal': float(amount),
            })

        if receipt_lines:
            success_data = {
                'lines': receipt_lines,
                'total': float(total_revenue),
                'payment_method': payment_method,
                'timestamp': timezone.localtime(timezone.now()).strftime('%H:%M'),
            }

    return render(request, 'core/bar/bar_board.html', {
        'is_owner': is_owner,
        'business': business,
        'success_data': success_data,
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

    # Update item.cost_price to latest barrel cost for P&L reference
    item.cost_price = cost
    item.save(update_fields=['cost_price'])

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
    """Record a weight reading and return a variance mini-report."""
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

    try:
        weight_kg = Decimal(str(request.POST.get('weight_kg', '0')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Uzito si sahihi'}, status=400)

    if weight_kg <= 0:
        return JsonResponse({'ok': False, 'error': 'Ingiza uzito kutoka kwenye scale'}, status=400)

    reading_type = request.POST.get('reading_type', 'SPOT')
    if reading_type not in ('SPOT', 'SHIFT_CLOSE', 'SHIFT_OPEN'):
        reading_type = 'SPOT'

    note = (request.POST.get('note') or '').strip()

    KegWeightReading.objects.create(
        barrel=barrel,
        weight_kg=weight_kg,
        reading_type=reading_type,
        recorded_by=request.user,
        note=note,
    )

    # Variance: scale is ground truth
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
        'dispensed_l': round(dispensed_l, 1),
        'expected_rev': round(expected_rev, 0),
        'recorded_rev': round(recorded_rev, 0),
        'variance_kes': round(variance_kes, 0),
        'variance_pct': round(variance_pct, 1),
        'flag': flag,
        'weight_kg': float(weight_kg),
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

    if not updates:
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
