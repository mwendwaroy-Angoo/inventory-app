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
def _sell_item_amount(business, item, amount, payment_method='cash', recipient=''):
    """
    Sell `amount` of a single named green, FIFO across its OPEN bunches
    (oldest first = sell-before-it-wilts). Spills into the next bunch if the
    oldest can't absorb the whole amount. Returns (transactions, total_sold).
    """
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
        t = b.record_sale(take, payment_method, recipient)
        if t:
            txns.append(t)
            sold += take
            amount -= take

    # All bunches hit target mid-request: let the last open bunch absorb the rest
    # (a generous "ongeza" rather than refusing the customer).
    if amount > 0 and bunches:
        t = bunches[-1].record_sale(amount, payment_method, recipient)
        if t:
            txns.append(t)
            sold += amount
    return txns, sold


def handle_bunch_cart_entry(entry, business, payment_method):
    """
    Process one Quick Sell cart line with mode 'bunch' or 'mix'.
    Returns (recorded_dict_or_None, last_transaction_or_None).
    """
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
        txns, _breakdown = ProduceBunch.sell_mix(business, group, amount, payment_method)
        if not txns:
            return None, None
        name = entry.get('label') or f"Mboga za kienyeji ({group})"
        return {'name': name, 'qty': 1, 'subtotal': float(amount)}, txns[-1]

    if mode == 'bunch':
        item = Item.objects.filter(id=entry.get('id'), store__business=business).first()
        if not item:
            return None, None
        txns, sold = _sell_item_amount(business, item, amount, payment_method)
        if not txns:
            return None, None
        return {'name': item.description, 'qty': 1, 'subtotal': float(sold)}, txns[-1]

    return None, None


# ──────────────────────────────────────────────────────────────────────────
# AJAX — the greens board that drives the Quick Sell tiles
# ──────────────────────────────────────────────────────────────────────────
@login_required
def produce_board(request):
    """Greens tiles for the current business: each bunch-item + each mix group,
    with live remaining envelope, price-point presets, and wilting flags."""
    from .views import get_user_profile
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'greens': [], 'mixes': []})

    business = up.business
    items = (
        Item.objects.filter(store__business=business, is_produce=True, produce_mode='BUNCH')
        .prefetch_related('bunches', 'portion_presets')
        .order_by('description')
    )

    greens, mix_map = [], {}
    for it in items:
        open_b = [b for b in it.bunches.all() if b.status == 'OPEN' and b.remaining() > 0]
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
        })
        if it.mix_group:
            g = mix_map.setdefault(it.mix_group, {
                'mix_group': it.mix_group, 'remaining': 0.0, 'presets': [], 'members': 0,
            })
            g['remaining'] += remaining
            g['members'] += 1
            if not g['presets'] and presets:
                g['presets'] = presets

    return JsonResponse({
        'greens': greens,
        'mixes': list(mix_map.values()),
        'can_receive': bool(getattr(up, 'is_owner', False)),
    })


# ──────────────────────────────────────────────────────────────────────────
# OWNER ACTIONS — receive bunches from the market, discard wilted ones
# ──────────────────────────────────────────────────────────────────────────
@login_required
@require_POST
def receive_bunches(request):
    """Owner logs bunches bought at the market. Creates ProduceBunch rows and a
    Receipt transaction (+1 each) so Item stock reflects the bunch count."""
    from .views import get_user_profile
    up = get_user_profile(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    business = up.business
    item = Item.objects.filter(id=request.POST.get('item_id'), store__business=business).first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Item not found'}, status=404)

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
    for _ in range(count):
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
