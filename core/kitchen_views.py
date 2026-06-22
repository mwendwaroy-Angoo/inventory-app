"""
Kitchen / Grill Board — views for the fast food / nyama choma side venture.

Accessible to:
  - Business owners (always)
  - Staff with role='kitchen'
  - Regular staff of the business (can sell from kitchen too)

Blocked for:
  - Riders, suppliers (unrelated roles)
  - Staff of other businesses
"""
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    BarTab, BarTabEntry, Item, ItemPortionPreset, ProduceBunch,
    Receipt, Store, Transaction,
)

logger = logging.getLogger(__name__)


def _get_up(request):
    """Return UserProfile or None."""
    try:
        return request.user.userprofile
    except Exception:
        return None


def _kitchen_store(business):
    """Return the kitchen Store for this business, or None."""
    return Store.objects.filter(business=business, is_kitchen=True).first()


def _ensure_kitchen_store(business):
    """Return or create the kitchen store for this business."""
    store = _kitchen_store(business)
    if not store:
        store = Store.objects.create(business=business, name='Kitchen', is_kitchen=True)
    return store


# ── Toggle kitchen module (owner only, AJAX POST) ──────────────────────────────

@login_required
@require_POST
def toggle_kitchen(request):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    business = up.business
    enable = request.POST.get('enable') == '1'
    business.has_kitchen = enable
    business.save(update_fields=['has_kitchen'])

    if enable:
        _ensure_kitchen_store(business)

    return JsonResponse({'ok': True, 'has_kitchen': enable})


# ── Kitchen Board (GET = render, POST = checkout) ─────────────────────────────

@login_required
def kitchen_board(request):
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    is_owner = bool(getattr(up, 'is_owner', False))
    role = getattr(up, 'role', 'staff')

    # Restrict access — riders and suppliers have no business here
    if role in ('rider', 'supplier'):
        return redirect('home')

    # Must have kitchen enabled (or be the owner setting it up)
    if not business.has_kitchen and not is_owner:
        return redirect('home')

    if request.method == 'POST':
        return _kitchen_checkout(request, up, business, is_owner)

    # ── GET: build board data ──────────────────────────────────────────────────
    kitchen_store = _kitchen_store(business)
    portion_items = []
    batch_items = []

    if kitchen_store:
        items_qs = (
            Item.objects
            .filter(store=kitchen_store)
            .prefetch_related('portion_presets')
            .order_by('description')
        )
        for item in items_qs:
            presets = [
                {'id': p.id, 'label': p.label, 'price': float(p.price), 'qty': float(p.quantity_consumed)}
                for p in item.portion_presets.all().order_by('display_order', 'price')
            ]
            if item.is_produce and item.produce_mode == 'BUNCH':
                # Batch item (nyama choma, mutura) — show revenue envelope
                open_bunches = list(
                    ProduceBunch.objects.filter(
                        item=item, business=business, status='OPEN'
                    ).order_by('received_on')
                )
                batch_items.append({
                    'id': item.id,
                    'name': item.description,
                    'unit': item.unit,
                    'presets': presets,
                    'open_bunches': [
                        {
                            'id': b.id,
                            'size': b.size,
                            'remaining': float(b.remaining()),
                            'target_revenue': float(b.target_revenue),
                            'revenue_collected': float(b.revenue_collected),
                            'cost_price': float(b.cost_price),
                        }
                        for b in open_bunches
                    ],
                    'total_remaining': sum(float(b.remaining()) for b in open_bunches),
                    'has_stock': any(b.remaining() > 0 for b in open_bunches),
                })
            else:
                # Portion item (chipo, chicken wing, smokie, samosa)
                balance = float(item.current_balance())
                portion_items.append({
                    'id': item.id,
                    'name': item.description,
                    'unit': item.unit,
                    'selling_price': float(item.selling_price),
                    'balance': balance,
                    'presets': presets,
                })

    # Open food tabs (source='kitchen') for this business
    food_tabs = list(
        BarTab.objects
        .filter(business=business, source='kitchen', status='OPEN')
        .prefetch_related('entries')
        .order_by('-opened_at')
    )
    food_tabs_data = []
    for tab in food_tabs:
        entries = [
            {'id': e.id, 'description': e.description, 'amount': float(e.amount), 'is_paid': e.is_paid}
            for e in tab.entries.all()
        ]
        food_tabs_data.append({
            'id': tab.id,
            'customer_name': tab.customer_name,
            'total': float(tab.total()),
            'unpaid_total': float(tab.unpaid_total()),
            'entries': entries,
            'opened_at': tab.opened_at.strftime('%H:%M'),
        })

    # Open bar tabs (source='bar') — for "add to bar tab" payment option
    bar_tab_names = list(
        BarTab.objects
        .filter(business=business, source='bar', status='OPEN')
        .values_list('customer_name', flat=True)
        .distinct()
        .order_by('customer_name')
    )

    # Today's kitchen revenue
    kitchen_revenue_today = Decimal('0')
    if kitchen_store:
        txns = Transaction.objects.filter(
            business=business,
            type='Issue',
            date=timezone.localdate(),
            item__store=kitchen_store,
            payment_method__in=['cash', 'mpesa'],
        ).select_related('item')
        kitchen_revenue_today = sum(Decimal(str(t.revenue())) for t in txns)

    return render(request, 'core/kitchen/kitchen_board.html', {
        'is_owner': is_owner,
        'business': business,
        'kitchen_store': kitchen_store,
        'portion_items': json.dumps(portion_items),
        'batch_items': json.dumps(batch_items),
        'food_tabs': json.dumps(food_tabs_data),
        'bar_tab_names': json.dumps(bar_tab_names),
        'kitchen_revenue_today': kitchen_revenue_today,
        'food_tab_count': len(food_tabs_data),
    })


def _kitchen_checkout(request, up, business, is_owner):
    """Handle kitchen sale POST."""
    try:
        cart = json.loads(request.POST.get('cart', '[]'))
        payment_method = request.POST.get('payment_method', 'cash')
        tab_customer = (request.POST.get('tab_customer') or '').strip()
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid request'}, status=400)

    if not cart:
        return JsonResponse({'ok': False, 'error': 'Cart is empty'}, status=400)

    kitchen_store = _kitchen_store(business)
    if not kitchen_store:
        return JsonResponse({'ok': False, 'error': 'Kitchen not configured'}, status=400)

    # Resolve or create a food/bar tab if needed
    active_tab = None
    if payment_method in ('food_tab', 'bar_tab') and tab_customer:
        source = 'kitchen' if payment_method == 'food_tab' else 'bar'
        active_tab = BarTab.objects.filter(
            business=business,
            customer_name__iexact=tab_customer,
            source=source,
            status='OPEN',
        ).first()
        if not active_tab and payment_method == 'food_tab':
            active_tab = BarTab.objects.create(
                business=business,
                store=kitchen_store,
                customer_name=tab_customer,
                source='kitchen',
                served_by=request.user,
            )
        elif not active_tab and payment_method == 'bar_tab':
            return JsonResponse({'ok': False, 'error': f'Hakuna tab wazi kwa "{tab_customer}"'}, status=400)

    receipt_lines = []
    total = Decimal('0')
    txn_pm = 'credit' if active_tab else payment_method

    for entry in cart:
        item_id = entry.get('item_id')
        preset_id = entry.get('preset_id')
        amount = Decimal(str(entry.get('amount', 0)))
        qty = Decimal(str(entry.get('qty', 1)))
        desc = entry.get('description', '')
        bunch_id = entry.get('bunch_id')

        if bunch_id:
            # Batch/grill item — use ProduceBunch revenue envelope
            try:
                bunch = ProduceBunch.objects.get(id=bunch_id, business=business, status='OPEN')
            except ProduceBunch.DoesNotExist:
                continue
            txn = bunch.record_sale(
                amount=amount,
                payment_method=txn_pm,
                recipient=tab_customer or '',
            )
            if active_tab:
                BarTabEntry.objects.create(
                    tab=active_tab,
                    transaction=txn,
                    description=desc,
                    amount=amount,
                )
            receipt_lines.append({'name': desc, 'subtotal': float(amount)})
            total += amount
        else:
            # Portion item — standard Issue transaction
            try:
                item = Item.objects.get(id=item_id, store__is_kitchen=True, store__business=business)
            except Item.DoesNotExist:
                continue
            txn = Transaction.objects.create(
                business=business,
                item=item,
                type='Issue',
                qty=-qty,
                sale_amount=amount,
                payment_method=txn_pm,
                recipient=tab_customer or '',
                recorded_by=request.user.username,
            )
            if active_tab:
                BarTabEntry.objects.create(
                    tab=active_tab,
                    transaction=txn,
                    description=desc,
                    amount=amount,
                )
            receipt_lines.append({'name': desc, 'subtotal': float(amount), 'qty': float(qty)})
            total += amount

    if not receipt_lines:
        return JsonResponse({'ok': False, 'error': 'No valid items'}, status=400)

    receipt_url = None
    receipt_number = None
    if payment_method in ('cash', 'mpesa'):
        try:
            rcpt = Receipt.issue(
                business=business,
                lines=receipt_lines,
                payment_method=payment_method,
                user=request.user,
                customer_name=tab_customer,
            )
            receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
            receipt_number = rcpt.receipt_number
        except Exception:
            logger.exception('Kitchen Receipt.issue failed business=%s', business.id)

    tab_id = active_tab.id if active_tab else None
    return JsonResponse({
        'ok': True,
        'total': float(total),
        'payment_method': payment_method,
        'tab_id': tab_id,
        'tab_customer': tab_customer,
        'receipt_url': receipt_url,
        'receipt_number': receipt_number,
    })


# ── Receive kitchen stock (owner only) ────────────────────────────────────────

@login_required
@require_POST
def kitchen_receive(request):
    """Receive kitchen stock — portion items (Receipt txn) or batch items (ProduceBunch)."""
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    business = up.business
    kitchen_store = _ensure_kitchen_store(business)

    mode = request.POST.get('mode', 'portion')  # 'portion' or 'batch'
    item_id = request.POST.get('item_id')

    try:
        item = Item.objects.get(id=item_id, store=kitchen_store)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Item not found'}, status=404)

    if mode == 'batch':
        cost = Decimal(str(request.POST.get('cost_price', 0)))
        target = request.POST.get('target_revenue')
        target = Decimal(str(target)) if target else item.default_bunch_target(cost)
        note = (request.POST.get('note') or '').strip()
        bunch = ProduceBunch.objects.create(
            item=item,
            business=business,
            size='LARGE',
            cost_price=cost,
            target_revenue=target,
            note=note,
        )
        return JsonResponse({'ok': True, 'bunch_id': bunch.id, 'target': float(target)})
    else:
        qty = Decimal(str(request.POST.get('qty', 0)))
        cost = Decimal(str(request.POST.get('cost_price', 0)))
        if qty <= 0:
            return JsonResponse({'ok': False, 'error': 'Qty must be > 0'}, status=400)
        Transaction.objects.create(
            business=business,
            item=item,
            type='Receipt',
            qty=qty,
            payment_method='cash',
            recorded_by=request.user.username,
        )
        if cost > 0:
            item.cost_price = cost / qty
            item.save(update_fields=['cost_price'])
        return JsonResponse({'ok': True, 'new_balance': float(item.current_balance())})


# ── Food tabs API (reuses same settle/void/debt endpoints as bar tabs) ─────────

@login_required
def kitchen_tabs_list(request):
    """AJAX GET — open food tabs for this business."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': []})

    tabs = (
        BarTab.objects
        .filter(business=up.business, source='kitchen', status='OPEN')
        .prefetch_related('entries')
        .order_by('-opened_at')
    )
    result = []
    for tab in tabs:
        entries = [
            {'id': e.id, 'description': e.description, 'amount': float(e.amount), 'is_paid': e.is_paid}
            for e in tab.entries.all()
        ]
        result.append({
            'id': tab.id,
            'customer_name': tab.customer_name,
            'total': float(tab.total()),
            'unpaid_total': float(tab.unpaid_total()),
            'entries': entries,
            'opened_at': tab.opened_at.strftime('%H:%M'),
        })
    return JsonResponse({'tabs': result})
