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
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    BarTab, BarTabEntry, Customer, Item, ItemPortionPreset, KitchenBatch,
    KitchenConsumableLog, ProduceBunch, Receipt, Store, Transaction,
)
from . import keg_metrics

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
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    business = up.business
    enable = request.POST.get('enable') == '1'
    business.has_kitchen = enable
    business.save(update_fields=['has_kitchen'])

    if enable:
        _ensure_kitchen_store(business)

    return JsonResponse({'ok': True, 'has_kitchen': enable})


# ── Kitchen Food Wastage ─────────────────────────────────────────────────────

@login_required
@require_POST
def kitchen_wastage(request):
    """Record food spoilage / drops as a Wastage transaction on a kitchen item."""
    up = _get_up(request)
    if not up:
        return JsonResponse({"ok": False, "error": "Auth required"}, status=403)

    business = up.business
    is_owner = bool(getattr(up, 'is_owner_or_manager', False))

    if not is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    kitchen_store = _kitchen_store(business)
    if not kitchen_store:
        return JsonResponse({"ok": False, "error": "Kitchen not configured"}, status=400)

    item = Item.objects.filter(
        id=request.POST.get("item_id"),
        store=kitchen_store,
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

    Transaction.objects.create(
        business=business,
        item=item,
        type="Wastage",
        qty=-qty,
        recipient=note or "Food wastage",
        payment_method="cash",
    )
    return JsonResponse({"ok": True})


# ── Kitchen Board (GET = render, POST = checkout) ─────────────────────────────

@login_required
def kitchen_board(request):
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    is_owner = bool(getattr(up, 'is_owner_or_manager', False))
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
    batch_items = []      # grill/nyama choma — ProduceBunch envelope
    kitchen_batches = []  # chips/stew — KitchenBatch P&L envelope

    if kitchen_store:
        items_qs = (
            Item.objects
            .filter(store=kitchen_store)
            .prefetch_related('portion_presets')
            .order_by('description')
        )
        for item in items_qs:
            presets = [
                {
                    'id': p.id, 'label': p.label, 'price': float(p.price),
                    'qty': float(p.quantity_consumed), 'khaki_type': p.khaki_type,
                }
                for p in item.portion_presets.all().order_by('display_order', 'price')
            ]
            if item.is_kitchen_batch:
                # Kitchen batch item (chips, stew, ugali) — KitchenBatch P&L
                open_batches = list(
                    KitchenBatch.objects.filter(
                        item=item, business=business, status='OPEN'
                    ).order_by('received_on')
                )
                kitchen_batches.append({
                    'id': item.id,
                    'name': item.description,
                    'unit': item.unit,
                    'presets': presets,
                    'open_batches': [_batch_to_dict(b) for b in open_batches],
                    'has_open_batch': bool(open_batches),
                })
            elif item.is_produce and item.produce_mode == 'BUNCH':
                # Grill batch item (nyama choma, mutura) — ProduceBunch envelope
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
                # Portion item (chicken wing, smokie, samosa)
                balance = float(item.current_balance())
                portion_items.append({
                    'id': item.id,
                    'name': item.description,
                    'unit': item.unit,
                    'selling_price': float(item.selling_price or 0),
                    'balance': balance,
                    'presets': presets,
                })

    # Flat list for the food wastage modal — all kitchen items, sorted by name.
    wastage_items = sorted(
        [{'id': i['id'], 'name': i['name'], 'unit': i.get('unit', '')}
         for i in portion_items + batch_items + kitchen_batches],
        key=lambda x: x['name'],
    )

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
            'opened_at': timezone.localtime(tab.opened_at).strftime('%I:%M %p').lstrip('0'),
        })

    # Open bar tabs (source='bar') — for "add to bar tab" payment option
    bar_tab_names = list(
        BarTab.objects
        .filter(business=business, source='bar', status='OPEN')
        .values_list('customer_name', flat=True)
        .distinct()
        .order_by('customer_name')
    )

    # Today's kitchen revenue — cash + mpesa + credit (food tab, bar tab, deni).
    # 'void' is excluded. Credit-method transactions are the same DB rows as
    # the later settled ones, so there is no double-counting when tabs settle.
    kitchen_revenue_today = Decimal('0')
    if kitchen_store:
        txns = Transaction.objects.filter(
            business=business,
            type='Issue',
            date=timezone.localdate(),
            item__store=kitchen_store,
            payment_method__in=['cash', 'mpesa', 'credit'],
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

    khaki_pool = keg_metrics.kitchen_consumable_pool(business)

    return render(request, 'core/kitchen/kitchen_board.html', {
        'is_owner': is_owner,
        'business': business,
        'kitchen_store': kitchen_store,
        'portion_items': json.dumps(portion_items),
        'batch_items': json.dumps(batch_items),
        'kitchen_batches': json.dumps(kitchen_batches),
        'khaki_pool': json.dumps(khaki_pool),
        'mix_siblings_json': json.dumps(mix_siblings),
        'food_tabs': json.dumps(food_tabs_data),
        'bar_tab_names': json.dumps(bar_tab_names),  # all kitchen staff can add food to bar tabs
        'kitchen_revenue_today': kitchen_revenue_today,
        'food_tab_count': len(food_tabs_data),
        'has_stk': has_stk,
        'has_my_shift': has_my_shift,
        'can_access_bar': can_access_bar,
        'can_receive_stock': can_receive_stock,
        'wastage_items_json': json.dumps(wastage_items),
    })


def _batch_to_dict(batch):
    """Serialize a KitchenBatch to a JS-friendly dict."""
    return {
        'id': batch.id,
        'item_id': batch.item_id,
        'cost_total': float(batch.cost_total),
        'revenue_collected': float(batch.revenue_collected),
        'profit': float(batch.profit),
        'profit_pct': batch.profit_pct,
        'status': batch.status,
        'received_on': str(batch.received_on),
        'days_open': batch.days_open,
        'cost_note': batch.cost_note or '',
    }


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
        stk_payment_id_raw = (request.POST.get('stk_payment_id') or '').strip()
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid request'}, status=400)

    # ── STK idempotency gate ──────────────────────────────────────────────────
    # If this checkout was initiated by a kitchen STK push, claim kitchen_settled
    # atomically. If the Daraja callback already processed the cart (set
    # kitchen_settled=True), skip and tell the frontend it's already done.
    if stk_payment_id_raw.isdigit():
        from django.db import transaction as _db_txn
        from core.models import Payment as _Payment
        try:
            with _db_txn.atomic():
                _pmt = _Payment.objects.select_for_update().get(
                    id=int(stk_payment_id_raw),
                    business=business,
                    kitchen_cart__isnull=False,
                )
                if _pmt.kitchen_settled:
                    return JsonResponse({
                        'ok': True,
                        'already_settled': True,
                        'total': float(_pmt.amount),
                    })
                _pmt.kitchen_settled = True
                _pmt.save(update_fields=['kitchen_settled'])
        except _Payment.DoesNotExist:
            pass  # No matching kitchen STK payment — proceed normally

    if not cart:
        return JsonResponse({'ok': False, 'error': 'Cart is empty'}, status=400)

    if payment_method == 'credit' and not credit_name:
        return JsonResponse({'ok': False, 'error': 'Jina la mteja linahitajika kwa deni'}, status=400)

    # ── CREDIT DISCIPLINE GATE (kitchen credit only — not food_tab; tab creation
    #    does not yet have a recipient with credit history to evaluate) ─────────
    if payment_method == 'credit':
        recipient_name = credit_name
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
                    phone=credit_phone,
                    credit_approved=True,
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
            import secrets as _secrets
            active_tab = BarTab.objects.create(
                business=business,
                store=kitchen_store,
                customer_name=tab_customer,
                source='kitchen',
                served_by=request.user,
                tab_receipt_token=_secrets.token_urlsafe(20),
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
        batch_id = entry.get('batch_id')

        if batch_id:
            # Kitchen batch item (chips, stew) — KitchenBatch P&L envelope
            try:
                batch = KitchenBatch.objects.get(id=batch_id, business=business, status='OPEN')
            except KitchenBatch.DoesNotExist:
                continue
            preset = None
            if preset_id:
                preset = ItemPortionPreset.objects.filter(id=preset_id, item=batch.item).first()
            txn = batch.record_sale(
                amount=amount,
                payment_method=txn_pm,
                recipient=txn_recipient,
                preset=preset,
                recorded_by=request.user,
            )
            if active_tab and txn:
                BarTabEntry.objects.create(
                    tab=active_tab, transaction=txn, description=desc, amount=amount,
                )
            receipt_lines.append({'name': desc, 'subtotal': float(amount)})
            total += amount
        elif bunch_id:
            # Grill batch item (nyama choma, mutura) — ProduceBunch revenue envelope
            try:
                bunch = ProduceBunch.objects.get(id=bunch_id, business=business, status='OPEN')
            except ProduceBunch.DoesNotExist:
                continue
            txn = bunch.record_sale(
                amount=amount,
                payment_method=txn_pm,
                recipient=txn_recipient,
                recorded_by=request.user,
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
                recorded_by=request.user,
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
            cust = _Customer.objects.create(business=business, name=credit_name, phone=credit_phone, credit_approved=True)
        elif credit_phone and not cust.phone:
            cust.phone = credit_phone
            cust.save(update_fields=['phone'])

    receipt_url = None
    receipt_number = None
    rcpt = None
    master_rcpt = None    # tracked outside try so SMS guard can read it
    _is_new_bar_link = False  # True when food tab is freshly linked to an existing bar tab receipt

    # ── food_tab: resolve master receipt ─────────────────────────────────────
    # Priority 1: food tab already has its own master receipt (subsequent rounds)
    # Priority 2: same customer has an open bar tab with a master receipt —
    #             link the food tab to it so the customer keeps one URL for both.
    # Priority 3: neither found — a new receipt will be created below.
    if payment_method == 'food_tab' and active_tab:
        master_rcpt = Receipt.objects.filter(
            business=business,
            meta__tab_id=active_tab.id,
        ).first()
        if master_rcpt is None:
            # Also check if this food tab's ID appears in another receipt's linked_tab_ids
            # (e.g. bar board already linked this tab into its bar receipt on a previous order)
            master_rcpt = Receipt.objects.filter(
                business=business,
                meta__linked_tab_ids__contains=[active_tab.id],
            ).first()

        if master_rcpt is None:
            try:
                _bar_qs = BarTab.objects.filter(
                    business=business, status='OPEN', source='bar'
                )
                _btab = (
                    _bar_qs.filter(customer=active_tab.customer).first()
                    if active_tab.customer
                    else _bar_qs.filter(customer_name__iexact=tab_customer).first()
                )
                if _btab:
                    _bar_rcpt = Receipt.objects.filter(
                        business=business,
                        meta__tab_id=_btab.id,
                    ).first()
                    if _bar_rcpt:
                        _linked = list(_bar_rcpt.meta.get('linked_tab_ids') or [])
                        if active_tab.id not in _linked:
                            _linked.append(active_tab.id)
                            _bar_rcpt.meta['linked_tab_ids'] = _linked
                            _bar_rcpt.save(update_fields=['meta'])
                            _is_new_bar_link = True
                        master_rcpt = _bar_rcpt
            except Exception:
                logger.exception(
                    'food_tab: bar-tab receipt lookup failed business=%s', business.id
                )

    _kitchen_rcpt_reused = False  # True when credit lines appended to existing receipt

    # For credit sales, check if a receipt already exists today for this customer
    # (e.g. they had a bar tab or QS deni earlier). Append rather than create new.
    if payment_method == 'credit' and credit_name and master_rcpt is None:
        try:
            from decimal import Decimal as _DecKB
            _existing_k = Receipt.objects.filter(
                business=business,
                customer_name__iexact=credit_name,
                created_at__date=timezone.localdate(),
            ).exclude(payment_method='statement').order_by('-created_at').first()
            if _existing_k:
                _updated_lines_k = list(_existing_k.lines) + receipt_lines
                _updated_total_k = sum(float(ll.get('subtotal', 0)) for ll in _updated_lines_k)
                _existing_k.lines = _updated_lines_k
                _existing_k.total = _DecKB(str(round(_updated_total_k, 2)))
                _existing_k.save(update_fields=['lines', 'total'])
                master_rcpt = _existing_k
                _kitchen_rcpt_reused = True
        except Exception:
            logger.exception('Kitchen credit receipt dedup failed business=%s', business.id)

    if payment_method in ('cash', 'mpesa', 'credit', 'food_tab'):
        try:
            kitchen_meta = {}
            if payment_method == 'credit' and credit_name:
                try:
                    from .models import Customer as _CustMeta
                    from core.debt_views import _build_credit_receipt_meta
                    _cust_m = _CustMeta.objects.filter(
                        business=business, name__iexact=credit_name
                    ).first()
                    if _cust_m:
                        kitchen_meta = _build_credit_receipt_meta(business, _cust_m, 'kitchen')
                except Exception:
                    pass
            if payment_method == 'food_tab' and active_tab:
                kitchen_meta['tab_id'] = active_tab.id

            if master_rcpt:
                rcpt = master_rcpt
            else:
                rcpt = Receipt.issue(
                    business=business,
                    lines=receipt_lines,
                    payment_method='tab' if payment_method == 'food_tab' else txn_pm,
                    user=request.user,
                    customer_name=credit_name if payment_method == 'credit' else tab_customer,
                    customer_phone=credit_phone if payment_method == 'credit' else tab_phone,
                    source='kitchen',
                    meta=kitchen_meta,
                )
            receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
            receipt_number = rcpt.receipt_number
        except Exception:
            logger.exception('Kitchen Receipt.issue failed business=%s', business.id)

    # SMS to customer:
    #  _is_new_bar_link → food just linked to bar tab receipt, send "chakula kimeongezwa"
    #  master_rcpt None → brand new standalone food tab receipt, send first-time SMS
    #  Otherwise        → subsequent round on existing receipt, no SMS
    if payment_method == 'food_tab' and active_tab and receipt_url:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification
            _sms_phone_raw = tab_phone or (active_tab.customer.phone if active_tab.customer else '')
            _sms_phone_k = normalize_ke_phone(_sms_phone_raw) if _sms_phone_raw else ''
            if _sms_phone_k:
                if _is_new_bar_link:
                    _sms_k = (
                        f"Habari {tab_customer},\n"
                        f"{business.name}: Chakula kimeongezwa kwenye tab yako.\n"
                        f"Angalia risiti iliyosasishwa: {receipt_url}"
                    )
                    send_sms_notification(_sms_k, _sms_phone_k)
                elif master_rcpt is None:
                    _tab_total_k = float(active_tab.total()) if active_tab else float(total)
                    _sms_k = (
                        f"Habari {tab_customer},\n"
                        f"{business.name}: Food tab imefunguliwa — "
                        f"KES {_tab_total_k:,.0f}.\n"
                        f"Angalia risiti yako: {receipt_url}"
                    )
                    send_sms_notification(_sms_k, _sms_phone_k)
        except Exception:
            logger.exception('Food tab open SMS failed business=%s', business.id)

    # SMS receipt to the customer who initiated a kitchen STK push
    if payment_method == 'mpesa' and stk_payment_id_raw.isdigit() and rcpt:
        try:
            from core.models import Payment as _PmtSms
            from .notifications import normalize_ke_phone, send_sms_notification
            _pmt_for_sms = _PmtSms.objects.filter(
                id=int(stk_payment_id_raw), business=business
            ).first()
            if _pmt_for_sms and _pmt_for_sms.phone:
                _normalized = normalize_ke_phone(_pmt_for_sms.phone)
                if _normalized:
                    _sms_url = f"https://www.dukamwecheche.co.ke/r/{rcpt.token}/"
                    _sms_msg = (
                        f"Asante! KES {int(float(total))} kwa "
                        f"{business.name}. Risiti: {_sms_url}"
                    )
                    send_sms_notification(_sms_msg, _normalized)
        except Exception:
            logger.exception('Kitchen STK receipt SMS failed business=%s', business.id)

    # SMS to customer on direct credit sale (suppress when appending to existing receipt)
    if payment_method == 'credit' and credit_phone and receipt_url and not _kitchen_rcpt_reused:
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
    can_receive = getattr(up, 'is_owner_or_manager', False) or getattr(up, 'can_receive_kitchen_stock', False)
    if not can_receive:
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya kupokea stok inahitajika'}, status=403)

    # Shift gate: staff must have an open shift even to receive stock
    if not getattr(up, 'is_owner_or_manager', False):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True,
                 'error': 'Fungua shift yako kwanza kabla ya kupokea stok.'},
                status=403,
            )

    business = up.business
    kitchen_store = _ensure_kitchen_store(business)

    mode = request.POST.get('mode', 'portion')  # 'portion', 'batch', 'batch_group', or 'kitchen_batch'

    # ── kitchen_batch: create a KitchenBatch for is_kitchen_batch items ──────
    if mode == 'kitchen_batch':
        item_id = request.POST.get('item_id')
        try:
            item = Item.objects.get(id=item_id, store=kitchen_store, is_kitchen_batch=True)
        except Item.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana'}, status=404)
        except (ValueError, TypeError):
            return JsonResponse({'ok': False, 'error': 'item_id batili'}, status=400)
        try:
            cost_total = Decimal(str(request.POST.get('cost_total', '0') or '0'))
            cost_note  = (request.POST.get('cost_note') or '').strip()[:200]
            note       = (request.POST.get('note') or '').strip()[:200]
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Nambari batili'}, status=400)
        if cost_total <= 0:
            return JsonResponse({'ok': False, 'error': 'Gharama lazima iwe zaidi ya 0'}, status=400)
        batch = KitchenBatch.objects.create(
            business=business, store=kitchen_store, item=item,
            cost_total=cost_total, cost_note=cost_note, note=note,
            recorded_by=request.user,
        )
        return JsonResponse({'ok': True, 'mode': 'kitchen_batch', 'batch': _batch_to_dict(batch),
                             'item_id': item.id, 'item_name': item.description})

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
    """Return open tabs, prior debt, and duplicate-name warnings for a customer name.

    Used for cross-counter merge prompt, prior-debt gate, and name dedup.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': [], 'prior_debt': None, 'similar_names': []})
    name = (request.GET.get('customer') or '').strip()
    if not name or len(name) < 2:
        return JsonResponse({'tabs': [], 'prior_debt': None, 'similar_names': []})

    # Open tabs for this customer (exact match, case-insensitive)
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
            'opened_at':     timezone.localtime(tab.opened_at).strftime('%I:%M %p').lstrip('0'),
        })

    # Check for outstanding debt under this customer name, scoped to the
    # requesting user's station — kitchen staff only see kitchen debt, bar
    # staff only see bar debt, so a bar debt cannot block a kitchen order.
    prior_debt = None
    from .debt_views import _get_customer_debt_data, _debt_scope
    _scope = _debt_scope(up, up.business)
    customer = Customer.objects.filter(
        business=up.business, name__iexact=name,
    ).first()
    if customer:
        debt_data = _get_customer_debt_data(customer, up.business, scope=_scope)
        if debt_data['outstanding'] > 0:
            prior_debt = {
                'outstanding': debt_data['outstanding'],
                'has_overdue': debt_data.get('has_overdue', False),
                'customer_id': customer.id,
                'is_defaulter': getattr(customer, 'is_defaulter', False),
            }

    # Detect other open tabs with similar (but not identical) names — possible duplicates
    all_open_tabs = BarTab.objects.filter(
        business=up.business, status='OPEN',
    ).exclude(customer_name__iexact=name).values_list('customer_name', flat=True).distinct()
    name_lower = name.lower()
    similar_names = []
    for other_name in all_open_tabs:
        if not other_name:
            continue
        other_lower = other_name.lower()
        # Flag if one name is a prefix of the other, or they share ≥4 chars from the start
        if (other_lower.startswith(name_lower[:4]) or name_lower.startswith(other_lower[:4])):
            if other_lower != name_lower:
                similar_names.append(other_name)

    return JsonResponse({
        'tabs': result,
        'prior_debt': prior_debt,
        'similar_names': similar_names[:5],  # cap at 5
    })


# ── Food tabs API (reuses same settle/void/debt endpoints as bar tabs) ─────────

@login_required
def kitchen_tabs_list(request):
    """AJAX GET — open food tabs for this business.

    Station scoping:
      - kitchen-only staff: see only food (kitchen) entries; bar entries replaced by cross-notice
      - cross-access staff / owner: see ALL entries
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'tabs': []})

    from .views import _station_scope
    _show_bar, _show_kitchen = _station_scope(up)
    _see_all = _show_bar and _show_kitchen

    food_tabs = (
        BarTab.objects
        .filter(business=up.business, source='kitchen', status='OPEN')
        .prefetch_related(
            Prefetch('entries',
                     queryset=BarTabEntry.objects.select_related('transaction__item__store'))
        )
        .order_by('-opened_at')
    )

    # Batch-fetch receipt tokens for all food tabs so we can return receipt URLs
    from .models import Receipt as _KbReceipt
    _food_tab_ids_all = list(food_tabs.values_list('id', flat=True))
    _kb_receipt_map = {}
    if _food_tab_ids_all:
        # Pass 1: receipts that directly own the food tab (meta.tab_id)
        for _r in _KbReceipt.objects.filter(
            business=up.business, meta__tab_id__in=_food_tab_ids_all
        ).values('meta', 'token'):
            _rmeta = _r.get('meta') or {}
            _tid = _rmeta.get('tab_id')
            if _tid and _tid not in _kb_receipt_map:
                _kb_receipt_map[_tid] = _r['token']

        # Pass 2: receipts that reference the food tab via linked_tab_ids
        _kb_unmapped = [tid for tid in _food_tab_ids_all if tid not in _kb_receipt_map]
        if _kb_unmapped:
            from django.db.models import Q as _QK
            _klq = _QK()
            for _kut in _kb_unmapped:
                _klq |= _QK(meta__linked_tab_ids__contains=[_kut])
            for _r in _KbReceipt.objects.filter(business=up.business).filter(_klq).values('meta', 'token'):
                _rmeta = _r.get('meta') or {}
                for _ltid in (_rmeta.get('linked_tab_ids') or []):
                    if _ltid in _kb_unmapped and _ltid not in _kb_receipt_map:
                        _kb_receipt_map[_ltid] = _r['token']

    result = []
    for tab in food_tabs:
        all_entries = list(tab.entries.all())
        _tab_phone = (tab.customer.phone if tab.customer else '') or ''
        _opened_local = timezone.localtime(tab.opened_at)

        # Always show only kitchen entries for settlement — bar items settle at Bar Board.
        # This applies to both owner/cross-access and kitchen-only staff.
        kitchen_entries = [
            e for e in all_entries
            if not e.transaction_id
            or not e.transaction.item_id
            or not e.transaction.item.store_id
            or e.transaction.item.store.is_kitchen
        ]
        bar_count = len(all_entries) - len(kitchen_entries)
        entries = [
            {'id': e.id, 'description': e.description, 'amount': float(e.amount), 'is_paid': e.is_paid}
            for e in kitchen_entries
        ]
        if bar_count:
            cross_notice = (
                f'+ {bar_count} bar item(s) — settle at Bar Board'
                if _see_all
                else f'+ {bar_count} bar item(s) on this tab'
            )
        else:
            cross_notice = None

        _rcpt_token = _kb_receipt_map.get(tab.id)
        _rcpt_url = request.build_absolute_uri(f'/r/{_rcpt_token}/') if _rcpt_token else None

        result.append({
            'id': tab.id,
            'customer_name': tab.customer_name,
            'customer_phone': _tab_phone,
            'total': sum(float(e['amount']) for e in entries),
            'unpaid_total': sum(float(e['amount']) for e in entries if not e['is_paid']),
            'entries': entries,
            'opened_at': _opened_local.strftime('%I:%M %p').lstrip('0'),
            'opened_date': _opened_local.strftime('%Y-%m-%d'),
            'is_bar_tab': False,
            'cross_notice': cross_notice,
            'receipt_url': _rcpt_url,
        })

    # Bar tabs that have kitchen entries — show read-only (kitchen items only).
    # Kitchen staff can track what food they've added to a customer's bar tab without
    # seeing the bar/alcohol portion. Filtered via transaction→item→store.is_kitchen.
    bar_tabs = (
        BarTab.objects
        .filter(
            business=up.business,
            source='bar',
            status='OPEN',
            entries__transaction__item__store__is_kitchen=True,
        )
        .distinct()
        .order_by('-opened_at')
    )
    for tab in bar_tabs:
        kitchen_entries = list(
            tab.entries
            .filter(transaction__item__store__is_kitchen=True)
            .values('id', 'description', 'amount', 'is_paid')
        )
        kitchen_entries = [
            {'id': e['id'], 'description': e['description'],
             'amount': float(e['amount']), 'is_paid': e['is_paid']}
            for e in kitchen_entries
        ]
        unpaid = sum(e['amount'] for e in kitchen_entries if not e['is_paid'])
        result.append({
            'id': tab.id,
            'customer_name': tab.customer_name,
            'total': sum(e['amount'] for e in kitchen_entries),
            'unpaid_total': float(unpaid),
            'entries': kitchen_entries,
            'opened_at': timezone.localtime(tab.opened_at).strftime('%I:%M %p').lstrip('0'),
            'is_bar_tab': True,  # renders as read-only — actions stay on bar board
        })

    return JsonResponse({'tabs': result})


# ── Kitchen Batch endpoints (Sprint KF1) ──────────────────────────────────────

def _kb_gate(request):
    """
    Common auth + business + shift gate for kitchen batch endpoints.
    Returns (up, business, error_response) where error_response is non-None on failure.
    """
    if not request.user.is_authenticated:
        return None, None, JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    up = _get_up(request)
    if not up:
        return None, None, JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    business = up.business
    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return up, business, JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza'},
                status=403,
            )
    return up, business, None


@login_required
@require_POST
def kitchen_batch_receive(request):
    """Create a new KitchenBatch for a is_kitchen_batch item (owner or receive-permitted staff)."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    is_owner = getattr(up, 'is_owner_or_manager', False)
    can_receive = is_owner or getattr(up, 'can_receive_kitchen_stock', False)
    if not can_receive:
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya kupokea stok inahitajika'}, status=403)

    kitchen_store = _ensure_kitchen_store(business)
    item_id = (request.POST.get('item_id') or '').strip()

    try:
        item = Item.objects.get(id=item_id, store=kitchen_store, is_kitchen_batch=True)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana'}, status=404)
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'item_id batili'}, status=400)

    try:
        cost_total = Decimal(str(request.POST.get('cost_total', '0') or '0'))
        cost_note  = (request.POST.get('cost_note') or '').strip()[:200]
        note       = (request.POST.get('note') or '').strip()[:200]
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari batili'}, status=400)

    if cost_total <= 0:
        return JsonResponse({'ok': False, 'error': 'Gharama lazima iwe zaidi ya 0'}, status=400)

    # Warn if a batch is already open for this item — allow anyway (multi-pot)
    already_open = KitchenBatch.objects.filter(
        item=item, business=business, status='OPEN'
    ).exists()

    batch = KitchenBatch.objects.create(
        business=business,
        store=kitchen_store,
        item=item,
        cost_total=cost_total,
        cost_note=cost_note,
        note=note,
        recorded_by=request.user,
    )
    return JsonResponse({
        'ok': True,
        'batch': _batch_to_dict(batch),
        'already_had_open': already_open,
        'item_id': item.id,
        'item_name': item.description,
    })


@login_required
@require_POST
def deplete_kitchen_batch(request, batch_id):
    """Mark a KitchenBatch as DEPLETED (all sold, batch done)."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    try:
        batch = KitchenBatch.objects.get(id=batch_id, business=business, status='OPEN')
    except KitchenBatch.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Batch haikupatikana au imefungwa tayari'}, status=404)

    batch.deplete()
    return JsonResponse({'ok': True, 'batch': _batch_to_dict(batch)})


@login_required
@require_POST
def discard_kitchen_batch(request, batch_id):
    """Discard a KitchenBatch — food went to waste / thrown away."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    try:
        batch = KitchenBatch.objects.get(id=batch_id, business=business)
    except KitchenBatch.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Batch haikupatikana'}, status=404)

    if batch.status == 'DISCARDED':
        return JsonResponse({'ok': False, 'error': 'Imeshatupwa tayari'}, status=400)

    reason = (request.POST.get('reason') or '').strip() or 'Chakula kimemwagwa / kimeoza'
    batch.discard(reason)
    return JsonResponse({'ok': True, 'batch': _batch_to_dict(batch)})


@login_required
@require_POST
def kitchen_consumable_add(request):
    """Log a kitchen consumable purchase (khaki bags, tomato sauce, cooking oil)."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    consumable_type = (request.POST.get('consumable_type') or '').strip().upper()
    valid_types = ('KHAKI_SMALL', 'KHAKI_LARGE', 'SAUCE_TOMATO', 'OIL_COOKING', 'OTHER')
    if consumable_type not in valid_types:
        return JsonResponse({'ok': False, 'error': 'Aina ya bidhaa batili'}, status=400)

    try:
        qty        = Decimal(str(request.POST.get('qty', '0') or '0'))
        unit_cost  = Decimal(str(request.POST.get('unit_cost', '0') or '0'))
        total_cost = qty * unit_cost
        note       = (request.POST.get('note') or '').strip()[:120]
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari batili'}, status=400)

    if qty <= 0 or unit_cost <= 0:
        return JsonResponse({'ok': False, 'error': 'Idadi na bei lazima ziwe zaidi ya 0'}, status=400)

    KitchenConsumableLog.objects.create(
        business=business,
        consumable_type=consumable_type,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        note=note,
        recorded_by=request.user,
    )
    pool = keg_metrics.kitchen_consumable_pool(business)
    return JsonResponse({'ok': True, 'pool': pool, 'total_cost': float(total_cost)})


@login_required
def kitchen_consumable_pool_api(request):
    """AJAX GET — current kitchen consumable pool balances."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False}, status=403)
    pool = keg_metrics.kitchen_consumable_pool(up.business)
    return JsonResponse({'ok': True, 'pool': pool})


@login_required
def kitchen_stats_api(request):
    """AJAX GET — today's kitchen revenue for the live badge update."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False}, status=403)
    business = up.business
    kitchen_store = _kitchen_store(business)
    revenue_today = Decimal('0')
    if kitchen_store:
        txns = Transaction.objects.filter(
            business=business,
            type='Issue',
            date=timezone.localdate(),
            item__store=kitchen_store,
            payment_method__in=['cash', 'mpesa', 'credit'],
        ).select_related('item')
        revenue_today = sum(Decimal(str(t.revenue())) for t in txns)
    return JsonResponse({'ok': True, 'revenue_today': float(revenue_today)})
