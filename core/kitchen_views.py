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

    # Bar/general staff need explicit kitchen access permission (off by default for new staff)
    if not is_owner and not up.is_kitchen_staff:
        if not getattr(up, 'can_access_kitchen', False):
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
                    'mix_group': item.mix_group or '',
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
                    'selling_price': float(item.selling_price or 0),
                    'balance': balance,
                    'presets': presets,
                })

    # Build mix_group → sibling list for the receive modal (group sack receives)
    mix_siblings = {}
    for b in batch_items:
        mg = b.get('mix_group', '')
        if mg:
            mix_siblings.setdefault(mg, []).append({'id': b['id'], 'name': b['name']})

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

    has_stk = bool(
        business.daraja_consumer_key and
        (business.mpesa_till or business.mpesa_paybill)
    )

    # ── Shift status ──────────────────────────────────────────────────────────
    if is_owner:
        has_my_shift = True
    else:
        from .models import Shift as _ShiftCheck
        has_my_shift = _ShiftCheck.objects.filter(
            business=business, status='OPEN', staff=request.user
        ).exists()

    can_access_bar = is_owner or getattr(up, 'can_access_bar', False)
    can_receive_stock = is_owner or getattr(up, 'can_receive_kitchen_stock', False)

    return render(request, 'core/kitchen/kitchen_board.html', {
        'is_owner': is_owner,
        'business': business,
        'kitchen_store': kitchen_store,
        'portion_items': json.dumps(portion_items),
        'batch_items': json.dumps(batch_items),
        'mix_siblings_json': json.dumps(mix_siblings),
        'food_tabs': json.dumps(food_tabs_data),
        'bar_tab_names': json.dumps(bar_tab_names if can_access_bar else []),
        'kitchen_revenue_today': kitchen_revenue_today,
        'food_tab_count': len(food_tabs_data),
        'has_stk': has_stk,
        'has_my_shift': has_my_shift,
        'can_access_bar': can_access_bar,
        'can_receive_stock': can_receive_stock,
    })


def _kitchen_checkout(request, up, business, is_owner):
    """Handle kitchen sale POST."""
    # Shift gate: staff must have an open shift
    if not is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True,
                 'error': 'Fungua shift yako kwanza kabla ya kuuza.'},
                status=403,
            )

    try:
        cart = json.loads(request.POST.get('cart', '[]'))
        payment_method = request.POST.get('payment_method', 'cash')
        tab_customer = (request.POST.get('tab_customer') or '').strip()
        tab_phone    = (request.POST.get('tab_phone') or '').strip()
        credit_name  = (request.POST.get('credit_name') or '').strip()
        credit_phone = (request.POST.get('credit_phone') or '').strip()
        merge_tab_id_raw = (request.POST.get('merge_tab_id') or '').strip()
        merge_tab_id = int(merge_tab_id_raw) if merge_tab_id_raw.isdigit() else None
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid request'}, status=400)

    if not cart:
        return JsonResponse({'ok': False, 'error': 'Cart is empty'}, status=400)

    if payment_method == 'credit' and not credit_name:
        return JsonResponse({'ok': False, 'error': 'Jina la mteja linahitajika kwa deni'}, status=400)

    # ── CREDIT DISCIPLINE GATE (kitchen credit + food_tab) ────────────────────
    if payment_method in ('credit', 'food_tab'):
        recipient_name = credit_name if payment_method == 'credit' else tab_customer
        if recipient_name:
            from core.models import Customer as _CustomerModel
            from core.credit_policy import evaluate_credit
            _cust_gate = _CustomerModel.objects.filter(
                business=business, name=recipient_name
            ).first()
            if _cust_gate is None:
                _cust_gate = _CustomerModel.objects.create(
                    business=business,
                    name=recipient_name,
                    phone=credit_phone if payment_method == 'credit' else tab_phone,
                )
            _decision = evaluate_credit(business, _cust_gate, scope='kitchen')
            if not _decision.allowed:
                return JsonResponse({
                    'ok': False,
                    'credit_blocked': True,
                    'error': f'Deni imezuiwa: {_decision.reason} — Pokea malipo ya cash au M-Pesa.',
                }, status=403)
    # ─────────────────────────────────────────────────────────────────────────

    can_access_bar = is_owner or getattr(up, 'can_access_bar', False)
    if payment_method == 'bar_tab' and not can_access_bar:
        return JsonResponse({'ok': False, 'error': 'Hauna ruhusa ya kufikia bar tab.'}, status=403)

    kitchen_store = _kitchen_store(business)
    if not kitchen_store:
        return JsonResponse({'ok': False, 'error': 'Kitchen not configured'}, status=400)

    # Resolve or create a food/bar tab if needed
    active_tab = None
    if merge_tab_id:
        # Cross-counter merge: staff confirmed adding kitchen items to an existing tab (e.g. bar tab)
        try:
            active_tab = BarTab.objects.get(id=merge_tab_id, business=business, status='OPEN')
            tab_customer = active_tab.customer_name
            payment_method = 'food_tab'  # treat as tab so receipt isn't issued and flow continues
        except BarTab.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Tab haikupatikana au imefungwa tayari.'}, status=400)
    elif payment_method in ('food_tab', 'bar_tab') and tab_customer:
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
    # For tabs → 'credit'; for direct credit → 'credit'; for cash/mpesa → as-is
    txn_pm = 'credit' if (active_tab or payment_method == 'credit') else payment_method
    txn_recipient = credit_name if payment_method == 'credit' else (tab_customer or '')

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
                recipient=txn_recipient,
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
                recipient=txn_recipient,
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

    # For direct credit: auto-create Customer record
    if payment_method == 'credit' and credit_name:
        from .models import Customer as _Customer
        from .notifications import normalize_ke_phone, send_sms_notification
        cust = _Customer.objects.filter(business=business, name__iexact=credit_name).first()
        if not cust:
            cust = _Customer.objects.create(business=business, name=credit_name, phone=credit_phone)
        elif credit_phone and not cust.phone:
            cust.phone = credit_phone
            cust.save(update_fields=['phone'])

    receipt_url = None
    receipt_number = None
    if payment_method in ('cash', 'mpesa', 'credit'):
        try:
            rcpt = Receipt.issue(
                business=business,
                lines=receipt_lines,
                payment_method=txn_pm,
                user=request.user,
                customer_name=credit_name if payment_method == 'credit' else tab_customer,
                customer_phone=credit_phone if payment_method == 'credit' else tab_phone,
                source='kitchen',
            )
            receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
            receipt_number = rcpt.receipt_number
        except Exception:
            logger.exception('Kitchen Receipt.issue failed business=%s', business.id)

    # SMS to customer on direct credit sale (same pattern as Quick Sell)
    if payment_method == 'credit' and credit_phone and receipt_url:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification
            import datetime as _dt
            normalized = normalize_ke_phone(credit_phone)
            if normalized:
                credit_window = business.credit_window_days or 30
                due_date = (_dt.date.today() + _dt.timedelta(days=credit_window)).strftime('%d %b %Y')
                sms_msg = (
                    f"Duka: {business.name}\n"
                    f"Umenunua kwa deni: KES {float(total):,.0f}\n"
                    f"Tarehe ya malipo: {due_date}\n"
                    f"Risiti: {receipt_url}"
                )
                send_sms_notification(sms_msg, normalized)
        except Exception:
            logger.exception('Kitchen credit SMS failed business=%s', business.id)

    tab_id = active_tab.id if active_tab else None

    # SMS to customer when kitchen items are merged into an existing cross-counter tab
    if merge_tab_id and active_tab:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification
            phone = None
            if active_tab.customer:
                phone = normalize_ke_phone(active_tab.customer.phone or '')
            elif tab_phone:
                phone = normalize_ke_phone(tab_phone)
            if phone:
                new_total = float(active_tab.total())
                counter_label = 'Bar' if active_tab.source == 'bar' else 'Kitchen'
                sms_msg = (
                    f"Habari {active_tab.customer_name},\n"
                    f"{business.name} imeongeza KES {float(total):,.0f} kwenye tab yako "
                    f"({counter_label}).\n"
                    f"Jumla sasa: KES {new_total:,.0f}"
                )
                send_sms_notification(sms_msg, phone)
        except Exception:
            logger.exception('Tab merge SMS failed business=%s', business.id)

    return JsonResponse({
        'ok': True,
        'total': float(total),
        'payment_method': payment_method,
        'tab_id': tab_id,
        'tab_customer': tab_customer,
        'credit_name': credit_name,
        'receipt_url': receipt_url,
        'receipt_number': receipt_number,
        'merged_tab': bool(merge_tab_id and active_tab),
    })


# ── Receive kitchen stock (owner or permitted kitchen staff) ──────────────────

@login_required
@require_POST
def kitchen_receive(request):
    """Receive kitchen stock — portion items (Receipt txn) or batch items (ProduceBunch)."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    can_receive = getattr(up, 'is_owner', False) or getattr(up, 'can_receive_kitchen_stock', False)
    if not can_receive:
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya kupokea stok inahitajika'}, status=403)

    # Shift gate: staff must have an open shift even to receive stock
    if not getattr(up, 'is_owner', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True,
                 'error': 'Fungua shift yako kwanza kabla ya kupokea stok.'},
                status=403,
            )

    business = up.business
    kitchen_store = _ensure_kitchen_store(business)

    mode = request.POST.get('mode', 'portion')  # 'portion', 'batch', or 'batch_group'

    # ── batch_group: one sack split proportionally across multiple items ──────
    if mode == 'batch_group':
        try:
            raw_ids = request.POST.getlist('item_ids[]')
            total_cost = Decimal(str(request.POST.get('total_cost', '0') or '0'))
            item_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        except (ValueError, TypeError):
            return JsonResponse({'ok': False, 'error': 'Gharama batili.'}, status=400)
        if not item_ids or total_cost <= 0:
            return JsonResponse({'ok': False, 'error': 'Chagua bidhaa na weka gharama ya gunia.'}, status=400)
        items_in_group = list(Item.objects.filter(id__in=item_ids, store=kitchen_store))
        n = len(items_in_group)
        if n == 0:
            return JsonResponse({'ok': False, 'error': 'Bidhaa hazikupatikana kwenye jikoni.'}, status=400)
        per_cost = (total_cost / Decimal(n)).quantize(Decimal('0.01'))
        created = []
        for it in items_in_group:
            target = it.default_bunch_target(per_cost)
            bunch = ProduceBunch.objects.create(
                item=it, business=business, size='LARGE',
                cost_price=per_cost, target_revenue=target,
            )
            created.append({
                'item': it.description,
                'bunch_id': bunch.id,
                'cost': float(per_cost),
                'target': float(target),
            })
        return JsonResponse({'ok': True, 'group': True, 'created': created})

    item_id = request.POST.get('item_id')

    try:
        item = Item.objects.get(id=item_id, store=kitchen_store)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': f'Bidhaa {item_id} haikupatikana kwenye jikoni.'}, status=404)
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'item_id batili.'}, status=400)

    try:
        if mode == 'batch':
            cost_raw = request.POST.get('cost_price', '0') or '0'
            cost = Decimal(str(cost_raw))
            target_raw = (request.POST.get('target_revenue') or '').strip()
            target = Decimal(str(target_raw)) if target_raw else item.default_bunch_target(cost)
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
            qty_raw = request.POST.get('qty', '0') or '0'
            cost_raw = request.POST.get('cost_price', '0') or '0'
            qty = Decimal(str(qty_raw))
            cost = Decimal(str(cost_raw))
            if qty <= 0:
                return JsonResponse({'ok': False, 'error': 'Idadi lazima iwe zaidi ya 0.'}, status=400)
            Transaction.objects.create(
                business=business,
                item=item,
                type='Receipt',
                qty=qty,
                payment_method='cash',
            )
            if cost > 0:
                item.cost_price = cost / qty
                item.save(update_fields=['cost_price'])
            return JsonResponse({'ok': True, 'new_balance': float(item.current_balance())})
    except Exception as exc:
        logger.exception('kitchen_receive failed business=%s item=%s mode=%s', business.id, item_id, mode)
        return JsonResponse({'ok': False, 'error': f'Hitilafu: {exc}'}, status=500)


# ── Cross-counter tab merge check (AJAX GET) ─────────────────────────────────

@login_required
def tab_check_api(request):
    """Return any open BarTab rows matching a customer name — used for cross-counter merge prompt."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': []})
    name = (request.GET.get('customer') or '').strip()
    if not name or len(name) < 2:
        return JsonResponse({'tabs': []})
    tabs = BarTab.objects.filter(
        business=up.business,
        customer_name__iexact=name,
        status='OPEN',
    ).order_by('-opened_at')
    result = []
    for tab in tabs:
        result.append({
            'id':            tab.id,
            'source':        tab.source,
            'source_label':  'Bar' if tab.source == 'bar' else 'Kitchen',
            'customer_name': tab.customer_name,
            'total':         float(tab.total()),
            'opened_at':     tab.opened_at.strftime('%H:%M'),
        })
    return JsonResponse({'tabs': result})


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
