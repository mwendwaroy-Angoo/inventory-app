"""
Sprint 5 — Waitress Order Queue.

Flow: waitress selects table → adds items (beer presets or regular items)
      → places order → bartender sees queue on bar board → Accept → Ready → Served.
      Served auto-creates Issue transactions via the appropriate route (barrel.record_sale
      for keg items, direct Transaction for others), billed onto the table's own
      running BarTab — see TableOrder.tab's docstring (2026-08-05 redesign).
"""
import json
import logging
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

logger = logging.getLogger(__name__)


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

    # 2026-08-06 live request (Monsoon Inn) — a waitress with cross-station
    # access (can_access_kitchen, granted via Staff Permissions same as any
    # other staff member) can now facilitate orders on EITHER counter from
    # here, not just bar. _station_scope() is the same generic, role-
    # agnostic helper every other station-scoped view in this app already
    # uses — no separate permission model invented for this screen.
    from .views import _station_scope
    show_bar, show_kitchen = _station_scope(up)

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

    # Other non-keg BAR items with a selling price (snacks, soda, spirits, etc.)
    other_items = Item.objects.filter(
        store__business=business, store__is_kitchen=False,
        is_keg=False, selling_price__gt=0,
    ).exclude(id__in=tapped_barrel_item_ids).order_by('description').distinct()

    # Kitchen items orderable via the Order Desk — deliberately scoped to
    # ordinary portion-mode items only (is_produce/is_kitchen_batch=False):
    # a KitchenBatch envelope (Chipo) or ProduceBunch bunch/greens sale
    # needs its own record_sale_locked() envelope mechanics, which
    # _create_transactions_for_order() doesn't implement — those items
    # still sell fine directly from Kitchen Board itself. Only fetched at
    # all when this viewer actually has kitchen access.
    kitchen_other_items = Item.objects.none()
    if show_kitchen:
        kitchen_other_items = Item.objects.filter(
            store__business=business, store__is_kitchen=True,
            is_keg=False, is_produce=False, is_kitchen_batch=False,
            selling_price__gt=0,
        ).order_by('description').distinct()

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

    # 2026-08-12 live request (Roy) — the waitress had no way to see how long
    # she'd been on shift; the app's own accountability "elapsed"/"Muda" figure
    # is deliberately reduced by any overlapping bar/kitchen custodian shift
    # (see _shift_active_segments()'s own docstring), so reusing it here would
    # sit near zero almost the whole time she's on shift, for a reason that has
    # nothing to do with "how long has she personally been clocked in." This is
    # a SEPARATE, plain wall-clock figure — never reuses or renames the
    # accountability value, which stays exactly as it is for cash-variance
    # purposes.
    my_open_shift_started_at = None
    if up.role == 'waitress':
        my_shift = Shift.objects.filter(
            business=business, staff=request.user, status='OPEN',
        ).order_by('-started_at').first()
        if my_shift:
            my_open_shift_started_at = my_shift.started_at.isoformat()

    return render(request, 'core/bar/waitress_screen.html', {
        'keg_items':      keg_items,
        'other_items':    other_items,
        'kitchen_other_items': kitchen_other_items,
        'show_kitchen':   show_kitchen,
        'recent_orders':  recent_orders,
        'is_owner':       is_owner,
        'business':       business,
        'has_open_shift': has_open_shift,
        'is_waitress':    getattr(up, 'role', '') == 'waitress',
        'my_open_shift_started_at': my_open_shift_started_at,
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

    notes = (request.POST.get('notes') or '').strip()

    items_raw = request.POST.get('items', '[]')
    try:
        items_list = json.loads(items_raw)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid items JSON'}, status=400)

    if not items_list:
        return JsonResponse({'ok': False, 'error': 'Ongeza bidhaa kwanza'}, status=400)

    # 2026-08-06 live request (Monsoon Inn) — which counter this order is
    # for, captured from her toggle at placement time (see TableOrder.
    # station's own docstring for why this is captured, not inferred).
    # Defaults to 'bar' for a viewer with no kitchen toggle at all (every
    # order desk user before this feature, and any staff without kitchen
    # access) — unchanged behavior for everyone else.
    from .views import _station_scope
    show_bar, show_kitchen = _station_scope(up)
    station = (request.POST.get('station') or 'bar').strip()
    if station not in ('bar', 'kitchen'):
        station = 'bar'
    if station == 'kitchen' and not show_kitchen:
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kuagiza upande wa Kitchen.'}, status=403)
    if station == 'bar' and not show_bar:
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kuagiza upande wa Bar.'}, status=403)

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

    # 2026-08-05 table-service redesign: payment is no longer chosen at
    # placement time — the order bills onto the table's own running BarTab
    # once SERVED (see _resolve_table_tab() + _create_transactions_for_order()
    # below), settled later via the same cash/mpesa/split/debt toolkit every
    # other counter uses. Deliberately NOT resolved here at placement time —
    # a PENDING order might get CANCELLED before ever being served, and
    # eagerly creating a tab for every placed order would leave empty ghost
    # tabs (zero entries) sitting in the drawer for every cancelled/still-
    # pending order. The tab only comes into existence the moment there's
    # a real sale to put on it.
    order = TableOrder.objects.create(
        business=up.business,
        table_label=table_label,
        waitress=request.user,
        shift=current_shift,
        notes=notes,
        station=station,
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
            # An order's items must all belong to the station it was
            # placed under — never silently mix a kitchen item into a bar
            # order's queue or vice versa. Skip a mismatched item rather
            # than mis-route the whole order.
            item_is_kitchen = bool(item_obj.store_id and item_obj.store.is_kitchen)
            if item_is_kitchen != (station == 'kitchen'):
                continue

            preset_obj = None
            if preset_id:
                preset_obj = ItemPortionPreset.objects.filter(id=preset_id, item=item_obj).first()
            # quantity here is a genuine "how many ordered" count (e.g. 2
            # pints) — the actual stock deduction (ml for a keg pour, or
            # count × quantity_consumed for a non-keg preset) is derived
            # server-side from the preset at SERVED time, see
            # _create_transactions_for_order() below — never here.

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

    # 2026-08-06 live request (Monsoon Inn) — Bar Board and Kitchen Board
    # each pass their own ?station= so a queue drawer only shows orders
    # meant for that counter. A blank/legacy station (orders placed before
    # TableOrder.station existed) is shown on BOTH boards rather than
    # guessed onto one — same convention as PettyCash.station's blank rows.
    station_param = request.GET.get('station', '').strip()
    if station_param in ('bar', 'kitchen'):
        from django.db.models import Q as _Q
        orders_qs = orders_qs.filter(_Q(station=station_param) | _Q(station=''))

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
            'notes':          order.notes,
            'items':          items_data,
            'total':          float(order.total_amount()),
            'elapsed_mins':   elapsed_mins,
            'waitress':       (order.waitress.get_full_name() or order.waitress.username) if order.waitress else 'Haijulikani',
            'created_at':     timezone.localtime(order.created_at).strftime('%H:%M'),
        })

    pending_count = sum(1 for o in result if o['status'] == 'PENDING')
    return JsonResponse({'orders': result, 'pending_count': pending_count})


def _notify_order_progress(order, new_status, actor, up):
    """2026-08-06 live redesign request (Monsoon Inn) — Roy: the waitress↔
    bartender flow should work like real non-verbal communication — an
    order is placed, the bartender sees and acknowledges it, prepares it,
    acknowledges it's ready, the WAITRESS is told it's ready and should go
    collect it, and both sides end up knowing the same thing happened.
    Before this, PENDING→ACCEPTED and ACCEPTED→READY were silent status
    flips the waitress only ever discovered by polling her own screen
    every 20s — no active notification either way, so "acknowledgment"
    only ever existed as an implicit possibility, not a real signal.

    Fires the missing two acknowledgments (ACCEPTED, READY) to the
    waitress, and a closing confirmation on SERVED to whichever side did
    NOT perform the pickup — either the bartender hands it over and taps
    "✓ Toa" on the queue drawer, or the waitress collects it herself and
    taps "✓ Nimepokea" on her own screen (see confirm_order_pickup below);
    either action reaches the same SERVED state, but the OTHER side is
    always told once it happens, so the handoff is confirmed to both
    parties, not just enacted unilaterally by whichever one got there
    first.
    """
    from .models import Notification
    from accounts.models import UserProfile as _UP

    who_label = actor.get_full_name() or actor.username
    is_waitress_actor = order.waitress_id and order.waitress_id == actor.id

    if new_status == 'ACCEPTED':
        if order.waitress_id and not is_waitress_actor:
            Notification.objects.create(
                user=order.waitress,
                title='👀 Agizo Limeonekana',
                message=f"{who_label} ameona agizo la {order.table_label} na anaandaa sasa.",
                notification_type='info', link_url='/bar/orders/',
            )
        return

    if new_status == 'READY':
        if order.waitress_id and not is_waitress_actor:
            Notification.objects.create(
                user=order.waitress,
                title='🔔 Agizo Liko Tayari',
                message=f"Agizo la {order.table_label} liko tayari — nenda uchukue kwenye counter.",
                notification_type='warning', link_url='/bar/orders/',
            )
        return

    if new_status == 'SERVED':
        if is_waitress_actor:
            # She confirmed pickup herself — let the bar side (on-shift bar
            # staff + owner/manager, station-scoped) know the handoff is
            # genuinely done, not just left assumed.
            from .views import scoped_on_shift_targets
            targets = scoped_on_shift_targets(order.business, sources=['bar'])
            for target_up in targets.values():
                if target_up.user_id == actor.id:
                    continue
                Notification.objects.create(
                    user=target_up.user,
                    title='✅ Agizo Limechukuliwa',
                    message=f"{who_label} amechukua agizo la {order.table_label} kwenye counter.",
                    notification_type='info', link_url='/bar/orders/',
                )
            for m_up in _UP.objects.filter(
                business=order.business, role__in=['owner', 'manager']
            ).exclude(user_id=actor.id).select_related('user'):
                Notification.objects.create(
                    user=m_up.user,
                    title='✅ Agizo Limechukuliwa',
                    message=f"{who_label} amechukua agizo la {order.table_label} kwenye counter.",
                    notification_type='info', link_url='/bar/orders/',
                )
        elif order.waitress_id:
            # Bar staff handed it over directly — confirm back to the
            # waitress that it's on its way / already delivered, matching
            # the same "both sides told" contract as the ACCEPTED/READY
            # acknowledgments above.
            Notification.objects.create(
                user=order.waitress,
                title='✅ Agizo Limetolewa',
                message=f"{who_label} ametoa agizo la {order.table_label} kwenye counter.",
                notification_type='info', link_url='/bar/orders/',
            )


def _notify_order_cancelled(order, cancelled_by, reason, was_mid_prep):
    """2026-07-24 wording/accountability audit: cancelling a table order used to be
    a bare status flip on BOTH cancel paths (the waitress-side cancel_table_order()
    and the bar-board oqUpdate(...,'CANCELLED') shortcut) — no reason captured, no
    one told. Notify whichever side of the order didn't do the cancelling: the
    waitress who placed it (if bar staff cancelled), or the on-duty bar staff
    working the queue (if the waitress cancelled) — mirroring the DJ/MC session
    cancel notification shape from the same audit pass. was_mid_prep flags an
    order that was already ACCEPTED/READY — wasted prep work, worth a clearer note."""
    from .models import Notification
    from accounts.models import UserProfile as _UP

    who_label = cancelled_by.get_full_name() or cancelled_by.username
    reason_bit = f' Sababu: {reason}.' if reason else ' Hakuna sababu iliyotolewa.'
    prep_bit = ' Ilikuwa tayari inatayarishwa.' if was_mid_prep else ''
    msg = (
        f"✕ Agizo la {order.table_label} limefutwa na {who_label}.{prep_bit}{reason_bit}"
    )

    targets = {}
    if order.waitress_id and order.waitress_id != cancelled_by.id:
        targets[order.waitress_id] = order.waitress
    for up in _UP.objects.filter(
        business=order.business, role__in=['owner', 'manager']
    ).exclude(user_id=cancelled_by.id).select_related('user'):
        targets.setdefault(up.user_id, up.user)

    for user in targets.values():
        Notification.objects.create(
            user=user, title='✕ Agizo Limefutwa', message=msg, notification_type='warning',
            link_url='/bar/orders/',
        )


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
        return JsonResponse({'ok': False, 'error': 'Agizo halikupatikana'}, status=404)

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
            'error': f'Haiwezekani kubadilisha kutoka {order.status} kwenda {new_status}',
        }, status=400)

    was_mid_prep = order.status in ('ACCEPTED', 'READY')
    order.status = new_status
    update_fields = ['status', 'updated_at']

    if new_status == 'SERVED':
        order.served_at = timezone.now()
        update_fields.append('served_at')
        order.save(update_fields=update_fields)
        _create_transactions_for_order(order, up)
        _notify_order_progress(order, new_status, request.user, up)
    elif new_status == 'CANCELLED':
        reason = (request.POST.get('reason') or '').strip()[:200]
        order.cancel_reason = reason
        order.cancelled_by  = request.user
        order.cancelled_at  = timezone.now()
        update_fields += ['cancel_reason', 'cancelled_by', 'cancelled_at']
        order.save(update_fields=update_fields)
        _notify_order_cancelled(order, request.user, reason, was_mid_prep)
    else:
        order.save(update_fields=update_fields)
        if new_status in ('ACCEPTED', 'READY'):
            _notify_order_progress(order, new_status, request.user, up)

    return JsonResponse({'ok': True, 'new_status': new_status})


# ── Waitress confirms pickup (READY → SERVED) ─────────────────────────────────

@login_required
@require_POST
def confirm_order_pickup(request, order_id):
    """The waitress-side half of the two-way acknowledgment protocol —
    same READY→SERVED transition update_table_order()'s bar-board '✓ Toa'
    button already performs, but initiated from HER OWN screen once she's
    actually collected the order, rather than only ever being something
    the bartender does unilaterally. Either action reaches SERVED; the
    OTHER side is always notified via _notify_order_progress() either way
    (see its own docstring), so the handoff is confirmed to both parties
    regardless of who taps first — matching Roy's own framing: "both
    parties confirm the same."
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    try:
        order = TableOrder.objects.prefetch_related(
            'items__item', 'items__preset'
        ).get(id=order_id, business=up.business)
    except TableOrder.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Agizo halikupatikana'}, status=404)

    if order.status != 'READY':
        return JsonResponse({
            'ok': False,
            'error': 'Agizo hili si tayari kwa kuchukuliwa bado.',
        }, status=400)

    order.status = 'SERVED'
    order.served_at = timezone.now()
    order.save(update_fields=['status', 'updated_at', 'served_at'])
    _create_transactions_for_order(order, up)
    _notify_order_progress(order, 'SERVED', request.user, up)
    return JsonResponse({'ok': True, 'new_status': 'SERVED'})


def _resolve_table_tab(business, table_label, served_by):
    """Find this table's own OPEN BarTab, or create one — same anonymous-
    tab-by-name lookup convention every other counter in this app already
    uses (never search by a blank name; a table always has a real label).
    Called lazily at SERVE time (not order-placement time) so a
    PENDING/CANCELLED order that never actually sells anything never
    creates an empty ghost tab."""
    from .models import BarTab
    tab = BarTab.objects.filter(
        business=business, customer_name__iexact=table_label,
        source='bar', status='OPEN',
    ).first()
    if not tab:
        tab = BarTab.create_with_credentials(
            business=business, store=None, customer_name=table_label,
            source='bar', served_by=served_by,
        )
    return tab


def _create_transactions_for_order(order, up):
    """Create Issue transactions when an order is marked SERVED, billed onto
    the table's own running BarTab (order.tab) — see the 2026-08-05
    table-service redesign in TableOrder.tab's docstring. Every entry goes
    on as 'credit' (an ordinary open-tab charge, same as any other tab
    entry app-wide) and gets settled later via the full cash/mpesa/split/
    debt toolkit, not a payment method chosen back at order-placement time.
    """
    business = up.business
    recorded_by = order.waitress or up.user
    if not order.tab_id:
        order.tab = _resolve_table_tab(business, order.table_label, recorded_by)
        order.save(update_fields=['tab'])
    receipt_lines = []
    for oi in order.items.all():
        try:
            qty = Decimal(str(oi.quantity))
            desc = oi.preset_label or oi.item_name or oi.item.description
            if oi.preset and oi.item.is_keg:
                # Keg item with preset → barrel.record_sale_locked() already
                # creates the matching BarTabEntry itself when tab= is given.
                barrel = KegBarrel.objects.filter(
                    business=business, item=oi.item, status='TAPPED',
                ).first()
                if barrel:
                    try:
                        txn = KegBarrel.record_sale_locked(
                            barrel.id, business, oi.preset, int(qty),
                            'credit', recorded_by, tab=order.tab,
                        )
                        receipt_lines.append({'name': desc, 'subtotal': float(txn.sale_amount)})
                    except KegBarrel.DoesNotExist:
                        pass  # barrel depleted between fetch and lock — skip
                    continue
            # Non-keg or no tapped barrel found — create the Transaction
            # directly, then attach it to the table's tab as an ordinary
            # BarTabEntry (mirrors the exact pattern bar_board/kitchen_board
            # use when adding a non-keg item to an active tab).
            # 2026-07-31 — a non-keg preset (e.g. a half-portion spirit)
            # must deduct count × quantity_consumed, not the raw ordered
            # count — same authoritative-preset fix applied to Quick Sell/
            # Kitchen checkout and their STK settlement callbacks.
            stock_qty = qty
            if oi.preset:
                stock_qty = qty * Decimal(str(oi.preset.quantity_consumed))
            amount = qty * oi.unit_price
            # 2026-08-07 (Roy: "negative balances should never be there") —
            # the item was already physically served to the customer by
            # the time an order reaches SERVED; refusing to record it here
            # would leave a real, already-consumed item invisible to
            # stock. Cap the deduction and flag any shortfall instead — see
            # Item.capped_deduction().
            deductible_qty, shortfall_qty = oi.item.capped_deduction(stock_qty)
            if shortfall_qty > 0:
                from .mpesa_views import _flag_stock_shortfall
                _flag_stock_shortfall(business, oi.item, shortfall_qty, 'Table Order Served')
            txn = Transaction.objects.create(
                business=business,
                item=oi.item,
                type='Issue',
                qty=-deductible_qty,
                sale_amount=amount,
                payment_method='credit',
                recipient=order.table_label,
                date=timezone.localdate(),
                # order.waitress is set from request.user at TableOrder
                # creation and should always be present, but this function
                # only ever receives (order, up) — 'request' was never in
                # scope here, so a bare `or request.user` fallback was
                # actually a NameError waiting to fire, silently swallowed
                # by this loop's own try/except and skipping the whole
                # Transaction (zero stock effect) for that order line. up
                # (the caller's own resolved profile) is always available.
                recorded_by=recorded_by,
                preset=oi.preset,
            )
            if order.tab:
                from .models import BarTabEntry
                BarTabEntry.objects.create(
                    tab=order.tab, transaction=txn, description=desc, amount=amount,
                )
            receipt_lines.append({'name': desc, 'subtotal': float(amount)})
        except Exception:
            continue

    # Wall-QR/PIN continuity — same resolve-or-issue pattern bar_board and
    # kitchen_board use, so a table's receipt is findable/payable exactly
    # like any other tab's, and a repeat visit or cross-counter order for
    # the same customer name still consolidates onto one receipt.
    if order.tab and receipt_lines:
        try:
            from core.tab_receipts import resolve_master_receipt
            from .models import Receipt
            master_rcpt, _freshly_linked = resolve_master_receipt(business, order.tab)
            if master_rcpt:
                updated_lines = list(master_rcpt.lines) + receipt_lines
                updated_total = sum(float(ln.get('subtotal', 0)) for ln in updated_lines)
                master_rcpt.lines = updated_lines
                master_rcpt.total = Decimal(str(round(updated_total, 2)))
                master_rcpt.save(update_fields=['lines', 'total'])
            else:
                Receipt.issue(
                    business=business,
                    lines=receipt_lines,
                    payment_method='tab',
                    user=recorded_by,
                    customer_name=order.table_label,
                    meta={'tab_id': order.tab.id},
                )
        except Exception:
            logger.exception('Table order receipt issue failed business=%s order=%s', business.id, order.id)


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
        return JsonResponse({'ok': False, 'error': 'Agizo halikupatikana'}, status=404)

    if order.status in ('SERVED', 'CANCELLED'):
        return JsonResponse({'ok': False, 'error': 'Agizo hili haliwezi kufutwa'}, status=400)

    # Only the waitress who placed it or owner/staff can cancel
    if (order.waitress != request.user and not up.is_owner
            and getattr(up, 'role', '') not in ('staff',)):
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kufuta agizo hili'}, status=403)

    was_mid_prep = order.status in ('ACCEPTED', 'READY')
    reason = (request.POST.get('reason') or '').strip()[:200]
    order.status        = 'CANCELLED'
    order.cancel_reason = reason
    order.cancelled_by  = request.user
    order.cancelled_at  = timezone.now()
    order.save(update_fields=['status', 'updated_at', 'cancel_reason', 'cancelled_by', 'cancelled_at'])
    _notify_order_cancelled(order, request.user, reason, was_mid_prep)
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

    qs = qs.prefetch_related('items__item', 'tab').order_by('-created_at')[:30]
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
            # 2026-08-05 table-service redesign: once SERVED, this order's
            # items live on the table's own tab, not a payment method
            # chosen at placement — surface the tab's own running unpaid
            # total + PIN so a waitress can quote the customer a live
            # figure and know exactly which tab to point them at, without
            # leaving her own screen.
            'tab_id':      order.tab_id,
            'tab_unpaid':  float(order.tab.unpaid_total()) if order.tab_id else None,
            'tab_pin':     order.tab.tab_pin if order.tab_id else None,
        })
    return JsonResponse({'orders': result})
