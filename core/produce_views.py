"""
Kibanda Produce Module — greens / bunch (revenue-envelope) selling.

Specific orders  ("terere ya 10")        -> _sell_item_amount  (FIFO across that item's bunches)
Generic mix      ("mboga za kienyeji 20") -> ProduceBunch.sell_mix (proportional across a mix group)

quick_sell() routes cart lines carrying mode='bunch' or mode='mix' here via
handle_bunch_cart_entry(); the rest of Quick Sell is unchanged.
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import Item, Transaction, ProduceBunch


# ──────────────────────────────────────────────────────────────────────────
# SALE HELPERS (called from quick_sell)
# ──────────────────────────────────────────────────────────────────────────
def _sell_item_amount(business, item, amount, payment_method='cash', recipient='', recorded_by=None):
    amount = Decimal(str(amount))
    bunches = [
        b for b in item.bunches.filter(business=business, status='OPEN')
                               .order_by('received_on', 'id')
        if b.remaining() > 0
    ]
    if not bunches or amount <= 0:
        return [], Decimal('0')

    txns, sold = [], Decimal('0')
    for b in bunches:
        if amount <= 0:
            break
        take = min(b.remaining(), amount)
        t = b.record_sale(take, payment_method, recipient, recorded_by=recorded_by)
        if t:
            txns.append(t)
            sold += take
            amount -= take

    if amount > 0 and bunches:
        t = bunches[-1].record_sale(amount, payment_method, recipient, recorded_by=recorded_by)
        if t:
            txns.append(t)
            sold += amount
    return txns, sold


def handle_bunch_cart_entry(entry, business, payment_method, recipient='', recorded_by=None):
    try:
        amount = Decimal(str(entry.get('amount', 0)))
    except Exception:
        amount = Decimal('0')
    if amount <= 0:
        return None, None

    mode = entry.get('mode')

    if mode == 'mix':
        group = (entry.get('mix_group') or '').strip()
        if not group:
            return None, None
        raw_ids = entry.get('selected_ids') or []
        item_ids = [int(x) for x in raw_ids if str(x).isdigit() or isinstance(x, int)] or None
        txns, _breakdown = ProduceBunch.sell_mix(business, group, amount, payment_method, recipient=recipient, item_ids=item_ids, recorded_by=recorded_by)
        if not txns:
            return None, None
        name = entry.get('label') or f"Mboga za kienyeji ({group})"
        return {'name': name, 'qty': 1, 'subtotal': float(amount)}, txns[-1]

    if mode == 'bunch':
        item = Item.objects.filter(id=entry.get('id'), store__business=business).first()
        if not item:
            return None, None
        txns, sold = _sell_item_amount(business, item, amount, payment_method, recipient, recorded_by=recorded_by)
        if not txns:
            return None, None
        return {'name': item.description, 'qty': 1, 'subtotal': float(sold)}, txns[-1]

    return None, None


# ──────────────────────────────────────────────────────────────────────────
# AJAX — the greens board that drives the Quick Sell tiles
# ──────────────────────────────────────────────────────────────────────────
@login_required
def produce_board(request):
    """Greens tiles + PORTION produce items for the current business.
    Returns greens (BUNCH tiles), mixes, can_receive flag, and portion_items
    (all PORTION-mode produce for the unified +From market dropdown)."""
    from .views import get_user_profile
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'greens': [], 'mixes': [], 'portion_items': []})

    business = up.business

    # ── BUNCH-mode greens ─────────────────────────────────────────────
    items = (
        Item.objects.filter(store__business=business, is_produce=True, produce_mode='BUNCH')
        .exclude(store__is_kitchen=True)  # kitchen batch items live on Kitchen Board only
        .prefetch_related('bunches', 'portion_presets')
        .order_by('description')
    )

    greens, mix_map = [], {}
    for it in items:
        all_bunches = list(it.bunches.filter(business=business))
        open_b = [b for b in all_bunches if b.status == 'OPEN' and b.remaining() > 0]
        has_history = len(all_bunches) > 0
        remaining = float(sum((b.remaining() for b in open_b), Decimal('0')))
        target_open = float(sum((b.target_revenue or Decimal('0') for b in open_b), Decimal('0')))
        presets = [{'label': p.label, 'price': float(p.price)}
                   for p in it.portion_presets.all()]
        oldest = min(open_b, key=lambda b: (b.received_on, b.id)) if open_b else None
        greens.append({
            'id': it.id,
            'name': it.description,
            'mix_group': it.mix_group,
            'presets': presets,
            'open_bunches': len(open_b),
            'remaining': remaining,
            'target_open': target_open,
            'wilting': bool(oldest and oldest.is_wilting()),
            'oldest_bunch_id': oldest.id if oldest else None,
            'has_history': has_history,
            # For the "empty tile" tap — pre-fill receive modal
            'item_balance': float(it.current_balance()),
            'cost_price': float(it.cost_price or 0),
            'unit': it.unit or 'Bunch',   # lets the receive modal detect greens vs sack items
        })
        if it.mix_group:
            g = mix_map.setdefault(it.mix_group, {
                'mix_group': it.mix_group, 'remaining': 0.0, 'presets': [], 'members': 0,
                'has_history': False,
            })
            g['remaining'] += remaining
            g['members'] += 1
            if has_history:
                g['has_history'] = True
            if not g['presets'] and presets:
                g['presets'] = presets

    # ── PORTION-mode produce (onions, tomatoes, potatoes, etc.) ──────
    portion_items = []
    for it in (Item.objects
               .filter(store__business=business, is_produce=True, produce_mode='PORTION')
               .exclude(store__is_kitchen=True)  # kitchen items live on Kitchen Board only
               .order_by('description')):
        portion_items.append({
            'id': it.id,
            'name': it.description,
            'unit': it.unit or 'Pcs',
            'produce_mode': 'PORTION',
            'cost_price': float(it.cost_price or 0),
        })

    return JsonResponse({
        'greens': greens,
        'mixes': list(mix_map.values()),
        'can_receive': bool(getattr(up, 'is_owner', False)),
        'portion_items': portion_items,
    })


# ──────────────────────────────────────────────────────────────────────────
# OWNER ACTIONS — receive stock from the market (bunches + dry goods)
# ──────────────────────────────────────────────────────────────────────────
@login_required
@require_POST
def receive_bunches(request):
    """Owner logs produce bought at the market.

    For BUNCH mode (greens): creates ProduceBunch rows + Receipt transactions.
    For PORTION mode (potatoes, onions, etc.): creates a single Receipt
    transaction for the total units received and updates item.cost_price
    to total_batch_cost / units so margins are computed correctly.
    """
    from .views import get_user_profile
    up = get_user_profile(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    business = up.business
    item = Item.objects.filter(id=request.POST.get('item_id'), store__business=business).first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Item not found'}, status=404)

    produce_mode_req = request.POST.get('produce_mode', 'BUNCH')

    # ── PORTION mode: Receipt + update cost_price ────────────────────
    if produce_mode_req == 'PORTION':
        try:
            units = Decimal(str(request.POST.get('units', '0')))
            total_cost = Decimal(str(request.POST.get('total_cost', '0')))
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Bad units or cost'}, status=400)

        if units <= 0 or total_cost <= 0:
            return JsonResponse(
                {'ok': False, 'error': 'Enter the number of units received and total cost'},
                status=400
            )

        # Per-unit cost: mama mboga paid total_cost for all units
        unit_cost = (total_cost / units).quantize(Decimal('0.01'))
        item.cost_price = unit_cost
        item.save(update_fields=['cost_price'])

        Transaction.objects.create(
            item=item,
            business=business,
            type='Receipt',
            qty=units,
            recipient=(
                f"Market — {int(units)} {item.unit or 'units'}, "
                f"batch cost KES {float(total_cost):.2f} "
                f"(KES {float(unit_cost):.2f} per {item.unit or 'unit'})"
            ),
        )
        return JsonResponse({
            'ok': True,
            'mode': 'PORTION',
            'units': float(units),
            'unit_cost': float(unit_cost),
        })

    # ── BUNCH mode: ProduceBunch + Receipt ───────────────────────────
    try:
        count = max(1, int(request.POST.get('count', 1)))
        cost = Decimal(str(request.POST.get('cost_price')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Bad cost or count'}, status=400)

    size = request.POST.get('size', 'MEDIUM')
    target_raw = (request.POST.get('target_revenue') or '').strip()
    try:
        target = Decimal(target_raw) if target_raw else item.default_bunch_target(cost)
    except Exception:
        target = item.default_bunch_target(cost)

    created = []
    for _bunch_idx in range(count):
        b = ProduceBunch.objects.create(
            item=item, business=business, size=size,
            cost_price=cost, target_revenue=target,
        )
        Transaction.objects.create(item=item, business=business, type='Receipt', qty=Decimal('1'))
        created.append(b.id)

    return JsonResponse({'ok': True, 'created': created, 'target': float(target), 'count': len(created)})


@login_required
@require_POST
def discard_bunch(request, bunch_id):
    """Write off a wilted / unsold bunch as wastage."""
    from .views import get_user_profile
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    bunch = get_object_or_404(ProduceBunch, id=bunch_id, business=up.business)
    bunch.discard(request.POST.get('reason', 'Wilted / end of day'))
    return JsonResponse({'ok': True, 'status': bunch.status})
