"""
Sprint 5 — Waitress Order Queue.

Flow: waitress selects table → adds items (beer presets or regular items)
      → places order → bartender sees queue on bar board → Accept → Ready → Served.
      Served auto-creates Issue transactions via the appropriate route (barrel.record_sale
      for keg items, direct Transaction for others).
"""
import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Item, ItemPortionPreset, KegBarrel, Shift,
    TableOrder, TableOrderItem, Transaction,
)


def _get_up(request):
    from .views import get_user_profile
    return get_user_profile(request)


# ── Waitress order screen ─────────────────────────────────────────────────────

@login_required
def waitress_screen(request):
    up = _get_up(request)
    if not up:
        from django.shortcuts import redirect
        return redirect('login')

    business = up.business

    # Keg items that have at least one TAPPED barrel — show with their presets
    tapped_barrel_item_ids = set(
        KegBarrel.objects.filter(business=business, status='TAPPED')
        .values_list('item_id', flat=True)
    )
    keg_items_raw = Item.objects.filter(
        id__in=tapped_barrel_item_ids
    ).prefetch_related('portion_presets').order_by('description')

    keg_items = []
    for item in keg_items_raw:
        presets = list(item.portion_presets.order_by('display_order', 'price'))
        if presets:
            keg_items.append({'item': item, 'presets': presets})
        else:
            # keg item but no presets — include as plain item
            keg_items.append({'item': item, 'presets': []})

    # Other non-keg items with a selling price (snacks, soda, food, etc.)
    other_items = Item.objects.filter(
        store__business=business,
        is_keg=False,
        selling_price__gt=0,
    ).exclude(id__in=tapped_barrel_item_ids).order_by('description').distinct()

    # Today's orders placed by this waitress (or all today for owner/staff)
    today = timezone.localdate()
    if up.role == 'waitress':
        recent_orders = TableOrder.objects.filter(
            business=business,
            waitress=request.user,
            created_at__date=today,
        ).prefetch_related('items__item').order_by('-created_at')[:20]
    else:
        recent_orders = TableOrder.objects.filter(
            business=business,
            created_at__date=today,
        ).prefetch_related('items__item').order_by('-created_at')[:20]

    is_owner = getattr(up, 'is_owner', False)
    has_open_shift = Shift.objects.filter(business=business, status='OPEN').exists()

    return render(request, 'core/bar/waitress_screen.html', {
        'keg_items':      keg_items,
        'other_items':    other_items,
        'recent_orders':  recent_orders,
        'is_owner':       is_owner,
        'business':       business,
        'has_open_shift': has_open_shift,
        'is_waitress':    getattr(up, 'role', '') == 'waitress',
    })


# ── Place order ───────────────────────────────────────────────────────────────

@login_required
@require_POST
def place_table_order(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    table_label = (request.POST.get('table_label') or '').strip()
    if not table_label:
        return JsonResponse({'ok': False, 'error': 'Chagua meza kwanza'}, status=400)

    payment_method = request.POST.get('payment_method', 'cash')
    notes = (request.POST.get('notes') or '').strip()

    items_raw = request.POST.get('items', '[]')
    try:
        items_list = json.loads(items_raw)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid items JSON'}, status=400)

    if not items_list:
        return JsonResponse({'ok': False, 'error': 'Ongeza bidhaa kwanza'}, status=400)

    # Find the current open shift (for linking the order)
    current_shift = Shift.objects.filter(
        business=up.business, status='OPEN'
    ).first()

    # Waitresses cannot place orders unless a shift is open
    if getattr(up, 'role', '') == 'waitress' and not current_shift:
        return JsonResponse({
            'ok': False,
            'error': 'Hakuna shift wazi — shift lazima ifunguliwe kwanza kabla ya kupokea maagizo'
        }, status=403)

    order = TableOrder.objects.create(
        business=up.business,
        table_label=table_label,
        waitress=request.user,
        shift=current_shift,
        payment_method=payment_method,
        notes=notes,
    )

    for entry in items_list:
        try:
            item_id   = int(entry['item_id'])
            qty       = Decimal(str(entry.get('quantity', 1)))
            unit_price = Decimal(str(entry['unit_price']))
            preset_id  = entry.get('preset_id')
            preset_label = (entry.get('preset_label') or '').strip()
            item_name  = (entry.get('item_name') or '').strip()
            item_notes = (entry.get('notes') or '').strip()

            item_obj = Item.objects.filter(id=item_id, store__business=up.business).first()
            if not item_obj:
                continue

            preset_obj = None
            if preset_id:
                preset_obj = ItemPortionPreset.objects.filter(id=preset_id, item=item_obj).first()

            TableOrderItem.objects.create(
                order=order,
                item=item_obj,
                preset=preset_obj,
                quantity=qty,
                unit_price=unit_price,
                preset_label=preset_label,
                item_name=item_name or item_obj.description,
                notes=item_notes,
            )
        except Exception:
            continue

    # If all items were invalid, clean up
    if not order.items.exists():
        order.delete()
        return JsonResponse({'ok': False, 'error': 'Hakuna bidhaa halali'}, status=400)

    return JsonResponse({
        'ok': True,
        'order_id': order.id,
        'table_label': order.table_label,
        'total': float(order.total_amount()),
        'item_count': order.items.count(),
    })


# ── Queue API (bar board polls this) ─────────────────────────────────────────

@login_required
def table_order_queue_api(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'orders': []})

    # Active orders: PENDING and ACCEPTED
    orders_qs = TableOrder.objects.filter(
        business=up.business,
        status__in=('PENDING', 'ACCEPTED', 'READY'),
    ).prefetch_related('items__item', 'items__preset').order_by('created_at')

    now = timezone.now()
    result = []
    for order in orders_qs:
        items_data = []
        for oi in order.items.all():
            items_data.append({
                'label': oi.preset_label or oi.item_name or oi.item.description,
                'quantity': float(oi.quantity),
                'unit_price': float(oi.unit_price),
                'notes': oi.notes,
            })
        elapsed_mins = int((now - order.created_at).total_seconds() // 60)
        result.append({
            'id':             order.id,
            'table_label':    order.table_label,
            'status':         order.status,
            'payment_method': order.payment_method,
            'notes':          order.notes,
            'items':          items_data,
            'total':          float(order.total_amount()),
            'elapsed_mins':   elapsed_mins,
            'waitress':       (order.waitress.get_full_name() or order.waitress.username) if order.waitress else 'Unknown',
            'created_at':     timezone.localtime(order.created_at).strftime('%H:%M'),
        })

    pending_count = sum(1 for o in result if o['status'] == 'PENDING')
    return JsonResponse({'orders': result, 'pending_count': pending_count})


# ── Update order status (bartender actions) ───────────────────────────────────

@login_required
@require_POST
def update_table_order(request, order_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    try:
        order = TableOrder.objects.prefetch_related(
            'items__item', 'items__preset'
        ).get(id=order_id, business=up.business)
    except TableOrder.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Order not found'}, status=404)

    new_status = request.POST.get('status', '').upper()
    valid_transitions = {
        'PENDING':  ['ACCEPTED', 'CANCELLED'],
        'ACCEPTED': ['READY', 'CANCELLED'],
        'READY':    ['SERVED', 'CANCELLED'],
    }
    allowed = valid_transitions.get(order.status, [])
    if new_status not in allowed:
        return JsonResponse({
            'ok': False,
            'error': f'Cannot move from {order.status} to {new_status}',
        }, status=400)

    order.status = new_status
    update_fields = ['status', 'updated_at']

    if new_status == 'SERVED':
        order.served_at = timezone.now()
        update_fields.append('served_at')
        order.save(update_fields=update_fields)
        _create_transactions_for_order(order, up)
    else:
        order.save(update_fields=update_fields)

    return JsonResponse({'ok': True, 'new_status': new_status})


def _create_transactions_for_order(order, up):
    """Create Issue transactions when an order is marked SERVED."""
    for oi in order.items.all():
        try:
            qty = Decimal(str(oi.quantity))
            if oi.preset and oi.item.is_keg:
                # Keg item with preset → use barrel.record_sale()
                barrel = KegBarrel.objects.filter(
                    business=up.business,
                    item=oi.item,
                    status='TAPPED',
                ).first()
                if barrel:
                    try:
                        KegBarrel.record_sale_locked(
                            barrel.id, up.business, oi.preset, int(qty),
                            order.payment_method, order.waitress,
                        )
                    except KegBarrel.DoesNotExist:
                        pass  # barrel depleted between fetch and lock — skip
                    continue
            # Non-keg or no barrel found — create Transaction directly
            amount = qty * oi.unit_price
            Transaction.objects.create(
                business=up.business,
                item=oi.item,
                type='Issue',
                qty=-qty,
                sale_amount=amount,
                payment_method=order.payment_method,
                recipient=order.table_label,
                date=timezone.localdate(),
            )
        except Exception:
            continue


# ── Cancel order ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def cancel_table_order(request, order_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    try:
        order = TableOrder.objects.get(id=order_id, business=up.business)
    except TableOrder.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Order not found'}, status=404)

    if order.status in ('SERVED', 'CANCELLED'):
        return JsonResponse({'ok': False, 'error': 'Order cannot be cancelled'}, status=400)

    # Only the waitress who placed it or owner/staff can cancel
    if (order.waitress != request.user and not up.is_owner
            and getattr(up, 'role', '') not in ('staff',)):
        return JsonResponse({'ok': False, 'error': 'Permission denied'}, status=403)

    order.status = 'CANCELLED'
    order.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True})


# ── Today's orders list (JSON — for waitress screen live refresh) ─────────────

@login_required
def my_orders_api(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'orders': []})

    today = timezone.localdate()
    if up.role == 'waitress':
        qs = TableOrder.objects.filter(
            business=up.business,
            waitress=request.user,
            created_at__date=today,
        )
    else:
        qs = TableOrder.objects.filter(
            business=up.business,
            created_at__date=today,
        )

    qs = qs.prefetch_related('items__item').order_by('-created_at')[:30]
    now = timezone.now()
    result = []
    for order in qs:
        result.append({
            'id':          order.id,
            'table_label': order.table_label,
            'status':      order.status,
            'total':       float(order.total_amount()),
            'item_count':  order.items.count(),
            'summary':     order.item_summary(),
            'created_at':  timezone.localtime(order.created_at).strftime('%H:%M'),
            'elapsed_mins': int((now - order.created_at).total_seconds() // 60),
        })
    return JsonResponse({'orders': result})
