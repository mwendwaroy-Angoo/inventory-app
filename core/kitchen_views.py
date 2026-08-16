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
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    BarTab, BarTabEntry, Customer, Item, ItemPortionPreset, KitchenBatch,
    KitchenConsumableLog, KitchenStockReceipt, KitchenStockReceiptLine,
    ProduceBunch, Receipt, Store, Transaction,
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
    """Return or create the kitchen store for this business.

    Root-cause fix (2026-07-22): manage_stores lets an owner create a plain
    Store just by typing a name — with no is_kitchen checkbox — so a
    business can easily already have a store literally named "Kitchen"
    with is_kitchen=False (created before ever enabling this module, or
    before this field existed). The old version here only ever looked for
    is_kitchen=True and unconditionally created a brand-new store when it
    found none, producing two "Kitchen" stores — the real one (with the
    business's actual items/history) left unflagged, and an empty duplicate
    now flagged. If exactly one unflagged store's name matches "kitchen"
    (case-insensitive), adopt it instead of creating a new one; only create
    fresh when that match is absent or ambiguous — never guess between two
    candidates.
    """
    store = _kitchen_store(business)
    if store:
        return store

    candidates = list(
        Store.objects.filter(business=business, is_kitchen=False, name__iexact='Kitchen')
    )
    if len(candidates) == 1:
        store = candidates[0]
        store.is_kitchen = True
        store.save(update_fields=['is_kitchen'])
        return store

    return Store.objects.create(business=business, name='Kitchen', is_kitchen=True)


# ── Toggle kitchen module (owner only, AJAX POST) ──────────────────────────────

@login_required
@require_POST
def toggle_kitchen(request):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    business = up.business
    enable = request.POST.get('enable') == '1'

    # Server-side double-submit backstop — a double-click/double-tap on the
    # Business Settings toggle is exactly the "two near-simultaneous
    # requests both pass the same check-then-create" shape that has caused
    # duplicate records everywhere else in this app; _ensure_kitchen_store's
    # own check-then-create was unlocked.
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': True, 'has_kitchen': business.has_kitchen, 'duplicate': True})

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
        return JsonResponse({"ok": False, "error": "Unaruhusiwa kuingia kwanza"}, status=403)

    business = up.business
    is_owner = bool(getattr(up, 'is_owner_or_manager', False))

    if not is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    # Station Scoping Principle: get_active_staff_shift only checks for ANY open
    # shift, not specifically a kitchen one — a bar-only staffer (no
    # can_access_kitchen) with an open BAR shift could still log kitchen wastage,
    # even though the kitchen board is never shown to them (bar-module audit
    # follow-up finding, 2026-07-19 — same gap class as the tab-write endpoints
    # fixed in keg_views.py, just in the kitchen module).
    from .views import _station_scope
    _, show_kitchen = _station_scope(up)
    if not show_kitchen:
        return JsonResponse({'ok': False, 'error': 'Hakuna ruhusa ya kitchen.'}, status=403)

    kitchen_store = _kitchen_store(business)
    if not kitchen_store:
        return JsonResponse({"ok": False, "error": "Kitchen haijawekwa"}, status=400)

    item = Item.objects.filter(
        id=request.POST.get("item_id"),
        store=kitchen_store,
    ).first()
    if not item:
        return JsonResponse({"ok": False, "error": "Bidhaa haikupatikana"}, status=404)

    try:
        qty = Decimal(str(request.POST.get("qty", "1")))
        if qty <= 0:
            raise ValueError
    except (ValueError, Exception):
        return JsonResponse({"ok": False, "error": "Kiasi si sahihi"}, status=400)

    note = request.POST.get("note", "").strip()

    Transaction.objects.create(
        business=business,
        item=item,
        type="Wastage",
        qty=-qty,
        recipient=note or "Chakula kimepotea",
        payment_method="cash",
    )

    # 2026-07-25: mirrors record_breakage() (core/keg_views.py) — a real
    # stock/money loss event should explain itself, not just return {"ok": True}.
    reporter_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
    loss_kes = float(qty) * float(item.cost_price or 0)
    message = (
        f"{item.description} × {qty:g} imerekodiwa na {reporter_name} tarehe {when}"
        f"{(' — KES ' + format(loss_kes, ',.0f') + ' hasara') if loss_kes else ''}."
    )
    if note:
        message += f' Sababu: {note}'

    from .models import Notification
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
    for om in _UP.objects.filter(
        business=business, role__in=['owner', 'manager']
    ).exclude(user=request.user).select_related('user'):
        Notification.objects.create(
            user=om.user,
            title='🧯 Upotezaji wa Chakula Umerekodiwa',
            message=message,
            notification_type='warning',
            link_url='/kitchen/',
        )
        if om.phone:
            normalized = normalize_ke_phone(om.phone)
            if normalized:
                send_sms_notification_async(f"{business.name}: {message}", normalized)

    return JsonResponse({"ok": True, "message": message})


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

        # 2026-08-05 live request (Roy, Kuku/chicken-leg example): "the tile
        # module when selling should not show wings, drumsticks etc. when
        # only chicken legs were received in order to track sales
        # accurately." Several differently-priced cuts of one shared item
        # (Kuku → Bawa/Paja/Kifua/Legi Nzima) all deduct from ONE shared
        # balance, so today's plain item-level balance can never answer
        # "do we actually have wings right now" — only "do we have ANY
        # Kuku." But KitchenStockReceiptLine.preset (2026-07-25) already
        # records exactly which cut was received in what quantity, and
        # Transaction.preset (2026-07-28) already records exactly which cut
        # was SOLD — so "remaining for this specific cut" is reconstructible
        # with no new balance field: received minus sold, per preset.
        # Deliberately scoped to items that have EVER used preset-attributed
        # receiving at all (items_with_preset_receipts below) — an item
        # that's only ever been received the plain way (no Kitchen Stock
        # Receipt, no preset attribution) keeps showing every preset exactly
        # as before, so this never breaks an item that doesn't use the
        # per-cut receiving flow.
        # 2026-08-09 live correction (Roy) — the anchor-remaining check above
        # was keyed strictly by the SOLD preset's own id, so a preset that's
        # never itself been received under its own name (e.g. "Half Chicken
        # Leg", only "Full Chicken Leg" is ever actually received) either
        # never appeared as sellable at all, OR — worse — sold invisibly
        # without ever decrementing what it physically came from, leaving
        # "Full Chicken Leg" showing more remaining stock than truly exists.
        # ItemPortionPreset.tracks_stock_of (new) lets the owner say "this
        # preset is the same physical lot as that one" — both received and
        # sold totals are now grouped by stock_tracking_anchor_id (itself
        # unless tracks_stock_of is set), not the raw preset id.
        from django.db.models import Sum, F
        from django.db.models.functions import Abs as _KAbs, Coalesce as _KCoalesce
        _received_by_preset = dict(
            KitchenStockReceiptLine.objects.filter(
                item__store=kitchen_store, preset_id__isnull=False,
            )
            .annotate(anchor_id=_KCoalesce(F('preset__tracks_stock_of_id'), F('preset_id')))
            .values('anchor_id').annotate(total=Sum('qty_received')).values_list('anchor_id', 'total')
        )
        _sold_by_preset = dict(
            Transaction.objects.filter(
                business=business, type='Issue', item__store=kitchen_store,
                preset_id__isnull=False,
            ).exclude(payment_method='void')
            .annotate(anchor_id=_KCoalesce(F('preset__tracks_stock_of_id'), F('preset_id')))
            .values('anchor_id').annotate(total=Sum(_KAbs('qty')))
            .values_list('anchor_id', 'total')
        )
        # 2026-08-12 live report (Roy, Monsoon Inn): the two sums above are a
        # true LIFETIME total with no cutoff — a tether (tracks_stock_of)
        # added AFTER old sales already happened retroactively pulls those
        # old sales into the anchor's running total the moment the tether
        # exists, permanently suppressing a fresh restock's "remaining" even
        # when Roy deliberately preserved that old history (rather than
        # wiping it via Kitchen Item Reset, the wrong tool when the goal is
        # "keep the real revenue, just stop it dragging down a fresh
        # restock"). ItemPortionPreset.restock_anchor_at (stamped by Kitchen
        # Item Reset's confirm step, see kitchen_reset_views.py) is a pure
        # visibility cursor — where set on an anchor preset, override its
        # entry above with a re-derived total counting only activity dated
        # on/after that point. Every anchor with no restock point keeps the
        # exact lifetime sum computed above, byte-for-byte unchanged.
        _anchor_ids = set(_received_by_preset) | set(_sold_by_preset)
        _restock_anchors = dict(
            ItemPortionPreset.objects.filter(
                id__in=_anchor_ids, restock_anchor_at__isnull=False,
            ).values_list('id', 'restock_anchor_at')
        )
        for _anchor_id, _anchor_dt in _restock_anchors.items():
            _received_by_preset[_anchor_id] = float(
                KitchenStockReceiptLine.objects.filter(
                    item__store=kitchen_store, preset_id__isnull=False,
                    receipt__received_on__gte=timezone.localtime(_anchor_dt).date(),
                )
                .annotate(anchor_id=_KCoalesce(F('preset__tracks_stock_of_id'), F('preset_id')))
                .filter(anchor_id=_anchor_id)
                .aggregate(total=Sum('qty_received'))['total'] or 0
            )
            _sold_by_preset[_anchor_id] = float(
                Transaction.objects.filter(
                    business=business, type='Issue', item__store=kitchen_store,
                    preset_id__isnull=False, created_at__gte=_anchor_dt,
                ).exclude(payment_method='void')
                .annotate(anchor_id=_KCoalesce(F('preset__tracks_stock_of_id'), F('preset_id')))
                .filter(anchor_id=_anchor_id)
                .aggregate(total=Sum(_KAbs('qty')))['total'] or 0
            )
        # 2026-08-09 live report (Roy): investigated a report of "unable to
        # sell in the Kuku tile" right after receiving "Full Chicken Leg"
        # via a preset-attributed Kitchen Stock Receipt line — traced it to
        # tileClick()'s single-preset branch in kitchen_board.html, not
        # this gate (see the fix there). This per-ITEM gate (as opposed to
        # a per-preset one) is deliberate, tested design from the
        # 2026-08-05/09 "cut visibility" work (PresetStockTrackingTetherTest):
        # once an item has ANY preset-attributed receipt, every OTHER
        # preset on it is meant to hide until it too is received under the
        # new regime (or linked via tracks_stock_of) — "today only legs
        # came in" should not keep showing wings as sellable. Left
        # unchanged; do not "fix" this to show every preset again, it
        # would silently undo that feature.
        items_with_preset_receipts = set(
            KitchenStockReceiptLine.objects.filter(
                item__store=kitchen_store, preset_id__isnull=False,
            ).values_list('item_id', flat=True).distinct()
        )

        for item in items_qs:
            _all_item_presets = list(item.portion_presets.all().order_by('display_order', 'price'))

            def _preset_dict(p):
                d = {
                    'id': p.id, 'label': p.label, 'price': float(p.price),
                    'qty': float(p.quantity_consumed), 'khaki_type': p.khaki_type,
                    'cost_price': float(p.cost_price) if p.cost_price is not None else None,
                }
                # 2026-08-09 live report (Roy): "it is bringing unnecessary
                # portion presets unlike before where I saw full chicken
                # legs and the tether." A same-day "show everything" safety
                # net (meant to fix a DIFFERENT symptom — every preset
                # vanishing at once) made this worse, not better, by
                # surfacing presets that were never actually received.
                # Removed. Owner/manager only: expose the raw received/sold/
                # remaining numbers behind the gate so a real ledger
                # mismatch can be SEEN and diagnosed from the numbers
                # themselves, instead of guessed at again.
                if is_owner and item.id in items_with_preset_receipts:
                    anchor = p.stock_tracking_anchor_id()
                    d['_received'] = float(_received_by_preset.get(anchor) or 0)
                    d['_sold'] = float(_sold_by_preset.get(anchor) or 0)
                return d

            # 2026-08-11 live report (Roy): "why are the presets not showing
            # up when i press the Kuku tile" — traced to every configured
            # preset's own received-minus-sold anchor tally going to zero at
            # once (Roy's own added context: he'd recorded BACKDATED sales
            # against this same receipt for a previous date — those count
            # toward `_sold_by_preset` exactly like any other sale, so a
            # large backdated volume can legitimately exhaust the tracked
            # anchor even while the item's real physical balance still shows
            # stock). The 2026-08-09 diagnostic (`_received`/`_sold` on each
            # preset) only ever rendered for presets that were STILL VISIBLE
            # — the one moment it's needed most, when every preset hides at
            # once, there was nothing on screen to look at. `hidden_presets`
            # is the owner-only fix: every preset the gate filtered OUT,
            # with its own received/sold/remaining numbers, so a full wipe-
            # out can be read from real figures instead of guessed at.
            # Deliberately does not change what staff can sell — `presets`
            # (below) keeps the exact same gate as before.
            def _is_visible(p):
                return item.id not in items_with_preset_receipts or (
                    float(_received_by_preset.get(p.stock_tracking_anchor_id()) or 0)
                    - float(_sold_by_preset.get(p.stock_tracking_anchor_id()) or 0) > 0
                )

            presets = [_preset_dict(p) for p in _all_item_presets if _is_visible(p)]
            hidden_presets = []
            if is_owner and item.id in items_with_preset_receipts:
                for p in _all_item_presets:
                    if _is_visible(p):
                        continue
                    anchor = p.stock_tracking_anchor_id()
                    received = float(_received_by_preset.get(anchor) or 0)
                    sold = float(_sold_by_preset.get(anchor) or 0)
                    hidden_presets.append({
                        'id': p.id, 'label': p.label,
                        'tethered_to': p.tracks_stock_of.label if p.tracks_stock_of_id else None,
                        '_received': received, '_sold': sold,
                        '_remaining': round(received - sold, 3),
                    })
            if item.is_kitchen_batch:
                # Kitchen batch item (chips, stew, ugali) — KitchenBatch P&L
                open_batches = list(
                    KitchenBatch.objects.filter(
                        item=item, business=business, status='OPEN'
                    ).order_by('received_on')
                )
                raw_src = item.raw_material_source
                kitchen_batches.append({
                    'id': item.id,
                    'name': item.description,
                    'unit': item.unit,
                    'presets': presets,
                    'open_batches': [_batch_to_dict(b) for b in open_batches],
                    'has_open_batch': bool(open_batches),
                    # Raw-material sack tracking (2026-07-22) — when set, the
                    # receive modal switches from a typed cost to "kg drawn",
                    # and the tile shows the sack's own remaining balance,
                    # separate from whether today's batch is done.
                    'raw_source_id': raw_src.id if raw_src else None,
                    'raw_source_name': raw_src.description if raw_src else '',
                    'raw_source_unit': raw_src.unit if raw_src else '',
                    'raw_source_balance': float(raw_src.current_balance()) if raw_src else None,
                    'raw_source_cost_price': float(raw_src.cost_price or 0) if raw_src else None,
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
                    # 2026-08-11 — owner-only diagnostic: every preset the
                    # cut-visibility gate filtered OUT of `presets` above,
                    # with its own received/sold/remaining numbers. Empty
                    # unless this item has ever had a preset-attributed
                    # receipt AND at least one of its presets is currently
                    # hidden. See the comment above `_is_visible()`.
                    'hidden_presets': hidden_presets,
                    # 2026-08-09 — the RECEIVE modal's "which cut did you get
                    # today?" picker must offer EVERY configured preset (e.g.
                    # Wing, currently out of stock and so filtered out of the
                    # sell-tile `presets` above) — not just the ones
                    # currently sellable, since receiving is exactly how an
                    # out-of-stock cut comes back.
                    #
                    # 2026-08-09, same-day correction (Roy): a TETHERED
                    # preset (tracks_stock_of set — e.g. "Half Chicken Leg"
                    # tracks_stock_of "Full Chicken Leg") must NOT be
                    # offered here at all. It has no independent physical
                    # existence to receive — it's only ever a way of
                    # SELLING part of what was received under the anchor's
                    # own name. Letting staff pick it here wouldn't actually
                    # break the quantity tally (received_by_preset already
                    # groups by stock_tracking_anchor_id), but it WOULD
                    # silently write the received unit cost onto the wrong
                    # preset's own cost_price field (the tethered one, not
                    # the anchor) — a real cost-attribution bug hiding
                    # behind an otherwise-correct stock count. Only the
                    # tile-tap sell picker (`presets` above) should ever
                    # show a tethered preset.
                    'all_presets': [
                        {'id': p.id, 'label': p.label}
                        for p in item.portion_presets.all().order_by('display_order', 'price')
                        if p.tracks_stock_of_id is None
                    ],
                    # 2026-08-09 live report (Roy — Raw Potatoes pencil bug):
                    # a raw-material-source item (e.g. "Raw Potatoes" feeding
                    # Chipo's batch draw) is never sold directly, so its tile
                    # must never show "✏️ Bei Maalum" (custom SELL price) —
                    # only a cost-correction affordance makes sense for it.
                    'is_raw_material_source': item.derived_batch_items.exists(),
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

    # Today's kitchen revenue. 'void' is excluded.
    #
    # 2026-08-09 live report (Roy, Monsoon Inn): the "🍽 Leo" header tile
    # showed KES 2550 with an OPEN shift whose own cash/mpesa were both
    # KES 0 — "i have not [rung/confirmed] today's entries so that amount
    # is inaccurate". Root cause: this figure blended cash+mpesa+credit into
    # one number with no distinction — the exact "confirmed vs unpaid
    # revenue conflation" bug already found and fixed on 2026-07-31 for
    # daily_sales()/home()/stock_list.html/the close-shift result panel, but
    # never extended to Kitchen Board's OWN live header tile (a genuinely
    # separate code path — this view never called _reconcile()). A credit
    # (deni/tab) sale is stock given out, not yet collected — counting it
    # in "today's revenue" with no label makes an unconfirmed figure read
    # as money already in hand. kitchen_revenue_today is now CONFIRMED
    # (cash+mpesa) only — the one figure the header tile shows as "Leo";
    # kitchen_revenue_credit is kept separate for an explicit "Deni" note,
    # never silently folded in, matching daily_sales()'s own confirmed_rev/
    # credit_rev split and the "Mikopo Mapya" wording already used
    # elsewhere on this same page's shift panel.
    #
    # 2026-08-09, same-day follow-up: after the confirmed/credit split above
    # shipped, Roy reported "Leo" STILL showed KES 2550 while his currently
    # open shift's own cash/mpesa were both KES 0 — "not the actual sales
    # recorded for today". Traced further and found a SECOND, independent
    # gap in this same hand-rolled query: it never excluded `[SVQ]`-tagged
    # transactions — the corrective cash/mpesa Issue a stock-take VARIANCE
    # ACCEPT creates when a physical recount finds a discrepancy (see the
    # 2026-07-25 "Stock-take-accept revenue was inflating 'today's' live
    # dashboard" entry). That fix swept home()'s bar/kitchen_today_revenue,
    # the dashboard_revenue_api poll, the revenue-target progress bar, and
    # _reconcile()'s own cash/mpesa totals — but this specific Kitchen
    # Board tile is a genuinely separate, hand-rolled query that was never
    # part of that sweep, so a same-day stock-count correction (a real,
    # recent activity here — Kuku/chicken stock was actively being
    # corrected this session) could silently count as if it were a real
    # sale. Matches home()'s own `.exclude(invoice_no='[SVQ]')` exactly.
    kitchen_revenue_today = Decimal('0')
    kitchen_revenue_credit = Decimal('0')
    # 2026-08-09, second same-day follow-up (Roy — "for Leo to adjust from
    # 2550 to today's figures as much as there are none yet which means
    # zero, is that too hard surely"): the credit split and [SVQ] exclusion
    # above didn't move this figure at all, meaning whatever it's summing
    # is genuinely type='Issue', cash/mpesa, non-[SVQ], dated today — every
    # check already made comes back clean. Rather than guess a THIRD blind
    # fix, owner/manager now get a line-by-line breakdown of exactly what's
    # in "Leo" (item, preset, amount, method, exact time) so the real
    # transactions can be seen and explained directly, the same "show the
    # real breakdown instead of guessing again" approach already used for
    # home()'s till-expected-cash disclosure.
    kitchen_revenue_lines = []
    if kitchen_store:
        # 2026-08-12 live report (Roy) — "I backdated everything from 7th to
        # 11th... kitchen staff has not yet made sales but the system is
        # showing as if it had." Root cause: Transaction.date defaults to
        # timezone.now() AT CREATION TIME (a plain model field default,
        # completely independent of any created_at= override) — every
        # backdated sale entered TODAY via the kb_backdated_at mechanism
        # (2026-08-09) therefore has date=today even though created_at
        # correctly reflects the historical date, so this query (filtering
        # on `date`, not `created_at`) silently counted a whole week of
        # backdated catch-up sales as if they happened right now. Fixed to
        # use the same station_revenue_window_start()-anchored created_at
        # window home()'s own kitchen_today_revenue tile already uses (see
        # core/views.py) — the two tiles now can never drift apart either.
        from core.shift_views import station_revenue_window_start
        _window_start = station_revenue_window_start(business, is_kitchen=True)
        txns = Transaction.objects.filter(
            business=business,
            type='Issue',
            created_at__gte=_window_start,
            item__store=kitchen_store,
            payment_method__in=['cash', 'mpesa', 'credit'],
        ).exclude(payment_method='void').exclude(invoice_no='[SVQ]').select_related('item', 'preset').order_by('-created_at')
        for t in txns:
            rev = Decimal(str(t.revenue()))
            if t.payment_method == 'credit':
                kitchen_revenue_credit += rev
            else:
                kitchen_revenue_today += rev
            if is_owner:
                label = t.item.description + (f' — {t.preset.label}' if t.preset_id else '')
                kitchen_revenue_lines.append({
                    'label': label,
                    'amount': float(rev),
                    'payment_method': t.payment_method,
                    'time': timezone.localtime(t.created_at).strftime('%d %b, %H:%M'),
                    'invoice_no': t.invoice_no,
                })

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
        'is_waitress': up.role == 'waitress',
        'can_convert_tabs_to_debt': getattr(up, 'can_convert_tabs_to_debt', False),
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
        'kitchen_revenue_credit': kitchen_revenue_credit,
        'kitchen_revenue_lines': json.dumps(kitchen_revenue_lines),
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
        'from_draw': batch.source_item_id is not None,
        'source_qty_drawn': float(batch.source_qty_drawn) if batch.source_qty_drawn is not None else None,
    }


def _kitchen_checkout(request, up, business, is_owner):
    """Handle kitchen sale POST."""
    # Shift gate: staff must have an open shift to SELL. Owner is always
    # exempt; a manager supervises freely but must open their OWN shift to
    # sell, exactly like ordinary staff (2026-07-26 live clarification) — the
    # `is_owner` PARAM here is actually is_owner_or_manager (see kitchen_board()'s
    # definition), so check the real owner flag directly and require the
    # manager's own shift via manager_must_have_shift.
    if not up.is_owner:
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business, manager_must_have_shift=True) is False:
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
        idem_token = (request.POST.get('idempotency_token') or '').strip()
        # 2026-07-28 live request — checkout-time split payment, direct
        # cash/mpesa sales only. See Transaction.apply_split_payment_locked().
        try:
            split_amount = Decimal(str(request.POST.get('split_amount', '') or '0'))
        except Exception:
            split_amount = Decimal('0')
        split_method = (request.POST.get('split_method') or '').strip()
        # 2026-07-31 live request — "customer paid cash 120... there is a
        # remainder" / "mpesa 100 then 20 cash and there is a remainder" —
        # part of a food_tab sale paid now, the rest becomes debt in the
        # SAME checkout. See BarTab.settle_and_partial_convert_to_debt().
        try:
            partial_cash = Decimal(str(request.POST.get('partial_cash', '') or '0'))
        except Exception:
            partial_cash = Decimal('0')
        try:
            partial_mpesa = Decimal(str(request.POST.get('partial_mpesa', '') or '0'))
        except Exception:
            partial_mpesa = Decimal('0')
        # 2026-08-09 live request (Roy) — catch-up posting for portion-item
        # sales (chicken), mirroring Quick Sell's own whole-cart backdate
        # toggle (2026-08-07): staff left without recording two days' worth
        # of chicken sales; the owner needs to post them under the correct
        # historical date, not today's, without polluting today's live
        # dashboard/till figures. Applies to the plain portion-item, batch,
        # and bunch branches below (all three now accept created_at).
        #
        # 2026-08-12 live request (Roy) — "ensure that for backdating i can
        # put customer in debt for that day... right there on the selling
        # part": widened to also cover a direct 'credit' (Deni) checkout —
        # previously required selling as cash first, then correcting to
        # credit afterward via the Recent Payments "🤝 Deni" split. Still
        # excludes 'food_tab'/'bar_tab' — a running bill is an open,
        # ongoing thing, not something that already "happened" on a fixed
        # past date; those never set active_tab=None below either way.
        kb_backdated_at = None
        if payment_method in ('cash', 'mpesa', 'credit'):
            _bd_raw = (request.POST.get('backdated_at') or '').strip()
            if _bd_raw:
                try:
                    from datetime import datetime as _dt
                    _naive = _dt.strptime(_bd_raw, '%Y-%m-%dT%H:%M')
                    from django.utils import timezone as _tz
                    kb_backdated_at = _tz.make_aware(_naive, _tz.get_current_timezone())
                except Exception:
                    kb_backdated_at = None
    except (json.JSONDecodeError, Exception):
        return JsonResponse({'ok': False, 'error': 'Invalid request'}, status=400)

    # Server-side double-submit backstop — see core/idempotency.py. Client-side
    # guards only cover a second click on the same live page; this catches real
    # duplicate requests (slow-network retry, a stray double tap that both landed
    # before the button could disable).
    from core.idempotency import claim_checkout_token
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Mauzo haya tayari yamehifadhiwa.', 'duplicate': True}, status=409)

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

    # 2026-08-06 live request (Monsoon Inn) — a waitress may take orders and
    # settle bills on either counter, but must never be the one to PLACE a
    # debt (only the counter staff, a manager, or the owner may). An
    # ordinary food_tab is fine — this blocks a direct Deni checkout AND
    # the "part paid now, rest becomes debt" shortcut, both of which write
    # straight to the debt ledger at checkout time.
    _wants_partial_debt = (
        payment_method == 'food_tab' and bool(tab_customer)
        and (partial_cash > 0 or partial_mpesa > 0)
    )
    if up.role == 'waitress' and (payment_method == 'credit' or _wants_partial_debt):
        return JsonResponse({
            'ok': False,
            'error': 'Huwezi kuandika deni moja kwa moja — mwombe muhusika wa '
                     'counter, meneja, au mmiliki afanye hivyo.',
        }, status=403)

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
    elif payment_method in ('food_tab', 'bar_tab'):
        source = 'kitchen' if payment_method == 'food_tab' else 'bar'
        # Anonymous tab — busy-counter case: staff has no time to capture a name
        # during peak demand. Never search for an existing tab by blank name
        # (that would silently merge two unrelated anonymous customers' bills
        # together) — only look up an existing tab when a name was actually
        # given. (kitchen-audit follow-up finding, 2026-07-19 — the previous
        # `and tab_customer` gate meant a blank-name food_tab sale never
        # created a tab at all; txn_pm below fell back to the literal string
        # 'food_tab'/'bar_tab', not a recognized payment_method value, and the
        # sale had no tab, no PIN, no way to ever collect or look it up again.)
        active_tab = (
            BarTab.objects.filter(
                business=business, customer_name__iexact=tab_customer,
                source=source, status='OPEN',
            ).first()
            if tab_customer else None
        )
        if not active_tab and payment_method == 'food_tab':
            active_tab = BarTab.create_with_credentials(
                business=business,
                store=kitchen_store,
                customer_name=tab_customer,
                source='kitchen',
                served_by=request.user,
            )
            if not tab_customer:
                active_tab.customer_name = f'Tab #{active_tab.id}'
                active_tab.save(update_fields=['customer_name'])
                tab_customer = active_tab.customer_name
        elif not active_tab and payment_method == 'bar_tab':
            return JsonResponse({'ok': False, 'error': f'Hakuna tab wazi kwa "{tab_customer}"'}, status=400)

    receipt_lines = []
    total = Decimal('0')
    # 2026-08-08 live report (Roy — "balances are funny... when staff is
    # selling") — the insufficient-stock guard below used to `continue`
    # completely silently: staff's tap simply did nothing, with zero
    # explanation anywhere, easily read as "the system is confused" even
    # though the deduction math itself was already correct. Every skip now
    # gets a plain, visible reason returned to the client.
    skipped = []
    # For tabs → 'credit'; for direct credit → 'credit'; for cash/mpesa → as-is
    txn_pm = 'credit' if (active_tab or payment_method == 'credit') else payment_method
    txn_recipient = credit_name if payment_method == 'credit' else (tab_customer or '')
    # 2026-07-28 live request — checkout-time split payment (e.g. Chipo at
    # KES 100 paid as 40 cash + 60 mpesa), direct sales only (no active_tab).
    # See Transaction.apply_split_payment_locked().
    created_txn_ids = []

    for entry in cart:
        item_id = entry.get('item_id')
        preset_id = entry.get('preset_id')
        amount = Decimal(str(entry.get('amount', 0)))
        qty = Decimal(str(entry.get('qty', 1)))
        desc = entry.get('description', '')
        bunch_id = entry.get('bunch_id')
        batch_id = entry.get('batch_id')

        if batch_id:
            # Kitchen batch item (chips, stew) — KitchenBatch P&L envelope.
            # select_for_update() inside atomic() prevents a lost-update race: two
            # near-simultaneous sales from the same pot/batch (two staff ringing up
            # at once, or a network-retry racing a fresh request) could otherwise
            # both read the same stale revenue_collected and the last save wins,
            # silently discarding one sale's contribution — same race class
            # KegBarrel.record_sale_locked was built to close for kegs, but
            # KitchenBatch/ProduceBunch never got the equivalent (kitchen-module
            # audit finding, 2026-07-19).
            from django.db import transaction as _db_txn
            try:
                with _db_txn.atomic():
                    batch = KitchenBatch.objects.select_for_update().get(
                        id=batch_id, business=business, status='OPEN',
                    )
                    preset = None
                    if preset_id:
                        preset = ItemPortionPreset.objects.filter(id=preset_id, item=batch.item).first()
                    txn = batch.record_sale(
                        amount=amount,
                        payment_method=txn_pm,
                        recipient=txn_recipient,
                        preset=preset,
                        recorded_by=request.user,
                        created_at=(kb_backdated_at if kb_backdated_at and not active_tab else None),
                    )
            except KitchenBatch.DoesNotExist:
                continue
            if active_tab and txn:
                BarTabEntry.objects.create(
                    tab=active_tab, transaction=txn, description=desc, amount=amount,
                )
            elif txn:
                created_txn_ids.append(txn.id)
            receipt_lines.append({'name': desc, 'subtotal': float(amount)})
            total += amount
        elif bunch_id:
            # Grill batch item (nyama choma, mutura) — ProduceBunch revenue envelope.
            # Same lost-update race as KitchenBatch above — locked via the shared
            # ProduceBunch.record_sale_locked classmethod (single lock-safe entry
            # point also used by Quick Sell's greens/mix path and both STK
            # settlement callbacks, so the race can't reopen at any one of them).
            txn = ProduceBunch.record_sale_locked(
                bunch_id, business, amount, txn_pm, txn_recipient, recorded_by=request.user,
                created_at=(kb_backdated_at if kb_backdated_at and not active_tab else None),
            )
            if not txn:
                continue
            if active_tab:
                BarTabEntry.objects.create(
                    tab=active_tab,
                    transaction=txn,
                    description=desc,
                    amount=amount,
                )
            else:
                created_txn_ids.append(txn.id)
            receipt_lines.append({'name': desc, 'subtotal': float(amount)})
            total += amount
        else:
            # Portion item — standard Issue transaction
            try:
                item = Item.objects.get(id=item_id, store__is_kitchen=True, store__business=business)
            except Item.DoesNotExist:
                continue
            # Stock-take variance lock (item 6, 2026-07-26) — this specific item
            # only; owner-only unlock via review_variance() resolving it.
            from core.stock_take_views import item_has_pending_variance
            if item_has_pending_variance(item.id):
                continue
            # 2026-07-28 — attribute the sale to its specific preset (e.g. "Paja
            # Nusu" vs "Paja Nzima" on one shared "Kuku" item) so Transaction.cost()
            # can use that preset's own cost_price instead of the item's blended
            # one. Defensive item= filter so a preset_id can't be borrowed from a
            # different item's row.
            sale_preset = None
            if preset_id:
                sale_preset = ItemPortionPreset.objects.filter(id=preset_id, item=item).first()
            # 2026-07-31 live report (Roy — half-bottle tab entry, physical
            # stock count off by exactly that half): a preset tap's stock
            # deduction must come from the database's own current
            # quantity_consumed, never the client-supplied qty — same
            # authoritative-server fix applied to Quick Sell's checkout for
            # the identical client-trust gap.
            if sale_preset is not None:
                qty = Decimal(str(sale_preset.quantity_consumed))
            # 2026-08-07 live request (Roy: "negative balances should never
            # be there") — this plain-item branch was the one live-checkout
            # gap without a balance guard (Quick Sell's own direct checkout
            # already has this same check). Refused outright, matching
            # Quick Sell's pattern exactly — nothing has been paid or
            # served yet at this point in the request, so blocking is safe.
            if item.available_balance() < qty:
                from core.models import BusinessException
                available_now = item.available_balance()
                try:
                    BusinessException.raise_exception(
                        business, kind='shrinkage', severity='info',
                        title=f'{item.description} — imeshindikana kuuzwa, stock haitoshi',
                        detail=(
                            f'Jaribio la kuuza {qty} {item.unit} lakini {available_now} '
                            f'{item.unit} tu ipo kwenye mfumo.'
                        ),
                    )
                except Exception:
                    pass
                skipped.append({
                    'description': desc or item.description,
                    'reason': f'Stock haitoshi — {available_now} {item.unit} tu ipo (ulijaribu {qty}).',
                })
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
                preset=sale_preset,
                # See ProduceBunch.record_sale()'s 2026-08-12 comment —
                # Transaction.date defaults independently of created_at.
                **({'created_at': kb_backdated_at, 'date': timezone.localtime(kb_backdated_at).date()}
                   if kb_backdated_at and not active_tab else {}),
            )
            if active_tab:
                BarTabEntry.objects.create(
                    tab=active_tab,
                    transaction=txn,
                    description=desc,
                    amount=amount,
                )
            else:
                created_txn_ids.append(txn.id)
            receipt_lines.append({'name': desc, 'subtotal': float(amount), 'qty': float(qty)})
            total += amount

    if not receipt_lines:
        if skipped:
            return JsonResponse({
                'ok': False,
                'error': skipped[0]['description'] + ': ' + skipped[0]['reason'],
                'skipped': skipped,
            }, status=400)
        return JsonResponse({'ok': False, 'error': 'No valid items'}, status=400)

    # 2026-07-31 — partial payment now / remainder as debt (food_tab only).
    # Chained right after the tab has all its entries but BEFORE the
    # receipt is issued below, so the receipt's own debt/outstanding
    # metadata reflects the true post-settlement state from the start.
    # Never blocks the checkout on failure — the sale already happened
    # above; a bad partial amount just leaves it as an ordinary open tab
    # (still fully correctable via the tabs drawer).
    is_partial_debt_checkout = (
        payment_method == 'food_tab' and bool(tab_customer)
        and (partial_cash > 0 or partial_mpesa > 0)
    )
    partial_debt_result = None
    if is_partial_debt_checkout and active_tab:
        try:
            active_tab.settle_and_partial_convert_to_debt(
                partial_cash, partial_mpesa, tab_customer, tab_phone, request.user,
            )
            partial_debt_result = {
                'cash': float(partial_cash), 'mpesa': float(partial_mpesa),
                'remainder': float(total) - float(partial_cash) - float(partial_mpesa),
                'customer_name': tab_customer,
            }
        except ValueError as _partial_err:
            is_partial_debt_checkout = False
            logger.warning(
                'kitchen checkout: partial-debt rejected for tab %s: %s',
                active_tab.id, _partial_err,
            )

    # Checkout-time split payment — direct (no active_tab) cash/mpesa sale
    # only. Never blocks the checkout: the sale already happened above.
    # 2026-07-30 live report: "the receipt does not show the same
    # information [as the split]" — carries the true final split forward
    # into kitchen_meta below (see Transaction.payment_split_breakdown's
    # docstring for the full reasoning).
    _kitchen_split_breakdown = {}
    if (
        active_tab is None
        and payment_method in ('cash', 'mpesa')
        and split_method in ('cash', 'mpesa')
        and split_method != payment_method
        and split_amount > 0
        and created_txn_ids
    ):
        try:
            _kitchen_all_split_ids = Transaction.apply_split_payment_locked(
                created_txn_ids, business, split_amount, split_method,
                staff_user=request.user,
            )
            _kitchen_split_breakdown = Transaction.payment_split_breakdown(
                _kitchen_all_split_ids or created_txn_ids, business,
            )
        except ValueError:
            # Sale already recorded above; a bad split amount (client already
            # validates bounds, so this is a rare race/edge case) must never
            # roll back or fail the checkout itself — just skip the split.
            logger.warning(
                'kitchen checkout: split_amount %s rejected for txns %s',
                split_amount, created_txn_ids,
            )

    # For direct credit: auto-create Customer record
    if payment_method == 'credit' and credit_name:
        from .models import Customer as _Customer
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
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
    _is_new_bar_link = False  # True when food tab is freshly linked to an existing tab/receipt

    # ── food_tab: resolve master receipt so the customer keeps one URL, regardless
    # of which counter (bar/kitchen/Quick Sell) rings up their next item.
    # Single source of truth — see core/tab_receipts.py.
    if payment_method == 'food_tab' and active_tab:
        try:
            from core.tab_receipts import resolve_master_receipt
            master_rcpt, _is_new_bar_link = resolve_master_receipt(business, active_tab)
        except Exception:
            logger.exception(
                'food_tab: master receipt resolution failed business=%s', business.id
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
            if _kitchen_split_breakdown:
                kitchen_meta['split_payment'] = _kitchen_split_breakdown
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
    if payment_method == 'food_tab' and not is_partial_debt_checkout and active_tab and receipt_url:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
            _sms_phone_raw = tab_phone or (active_tab.customer.phone if active_tab.customer else '')
            _sms_phone_k = normalize_ke_phone(_sms_phone_raw) if _sms_phone_raw else ''
            if _sms_phone_k:
                if _is_new_bar_link:
                    _sms_k = (
                        f"Habari {tab_customer},\n"
                        f"{business.name}: Chakula kimeongezwa kwenye tab yako.\n"
                        f"Angalia risiti iliyosasishwa: {receipt_url}"
                    )
                    send_sms_notification_async(_sms_k, _sms_phone_k)
                elif master_rcpt is None:
                    _tab_total_k = float(active_tab.total()) if active_tab else float(total)
                    _sms_k = (
                        f"Habari {tab_customer},\n"
                        f"{business.name}: Food tab imefunguliwa — "
                        f"KES {_tab_total_k:,.0f}.\n"
                        f"Angalia risiti yako: {receipt_url}"
                    )
                    send_sms_notification_async(_sms_k, _sms_phone_k)
        except Exception:
            logger.exception('Food tab open SMS failed business=%s', business.id)

    # SMS receipt to the customer who initiated a kitchen STK push
    if payment_method == 'mpesa' and stk_payment_id_raw.isdigit() and rcpt:
        try:
            from core.models import Payment as _PmtSms
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
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
                    send_sms_notification_async(_sms_msg, _normalized)
        except Exception:
            logger.exception('Kitchen STK receipt SMS failed business=%s', business.id)

    # SMS to customer on direct credit sale (suppress when appending to existing receipt)
    if payment_method == 'credit' and credit_phone and receipt_url and not _kitchen_rcpt_reused:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
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
                send_sms_notification_async(sms_msg, normalized)
        except Exception:
            logger.exception('Kitchen credit SMS failed business=%s', business.id)

    tab_id = active_tab.id if active_tab else None

    # SMS to customer when kitchen items are merged into an existing cross-counter tab
    if merge_tab_id and active_tab:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
            phone = None
            if active_tab.customer:
                phone = normalize_ke_phone(active_tab.customer.phone or '')
            elif tab_phone:
                phone = normalize_ke_phone(tab_phone)
            if phone:
                new_total = float(active_tab.total())
                _src = active_tab.source
                counter_label = 'Kitchen' if _src == 'kitchen' else ('Quick Sell' if _src == 'qs' else 'Bar')
                sms_msg = (
                    f"Habari {active_tab.customer_name},\n"
                    f"{business.name} imeongeza KES {float(total):,.0f} kwenye tab yako "
                    f"({counter_label}).\n"
                    f"Jumla sasa: KES {new_total:,.0f}"
                )
                send_sms_notification_async(sms_msg, phone)
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
        'partial_debt': partial_debt_result,
        'skipped': skipped,
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

    # Server-side double-submit backstop — see core/idempotency.py. Every mode
    # here creates a real stock/financial record (Transaction, ProduceBunch, or
    # KitchenBatch) with no other guard against a duplicate/retried request
    # (kitchen-module audit finding, 2026-07-19 — same gap as receive_barrel/
    # add_cups/record_breakage already fixed in the bar module).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

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
        cost_note = (request.POST.get('cost_note') or '').strip()[:200]
        note      = (request.POST.get('note') or '').strip()[:200]
        # 2026-08-12 live request (Roy): a batch opened to catch up a past
        # day's fries (or a raw-material draw for one) had no way to be
        # dated anything but "today" — the other half of the same complaint
        # as the backdated-sale fix above. Optional; blank/invalid falls
        # back to today exactly as before.
        received_on = None
        _received_on_raw = (request.POST.get('received_on') or '').strip()
        if _received_on_raw:
            try:
                from datetime import datetime as _dt
                received_on = _dt.strptime(_received_on_raw, '%Y-%m-%d').date()
            except ValueError:
                received_on = None
        try:
            # Raw-material sack tracking (2026-07-22): if this item has a
            # raw_material_source configured, cost is derived from "kg drawn
            # today" instead of a typed guess — see KitchenBatch.open_batch().
            if item.raw_material_source_id:
                draw_qty = Decimal(str(request.POST.get('draw_qty', '0') or '0'))
                batch = KitchenBatch.open_batch(
                    business=business, store=kitchen_store, item=item,
                    recorded_by=request.user, cost_note=cost_note, note=note,
                    draw_qty=draw_qty, received_on=received_on,
                )
            else:
                cost_total = Decimal(str(request.POST.get('cost_total', '0') or '0'))
                batch = KitchenBatch.open_batch(
                    business=business, store=kitchen_store, item=item,
                    recorded_by=request.user, cost_note=cost_note, note=note,
                    received_on=received_on,
                    cost_total=cost_total,
                )
        except (InvalidOperation, ValueError) as e:
            return JsonResponse({'ok': False, 'error': str(e) or 'Nambari batili'}, status=400)
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
            # Supplier / order reference — reuses Transaction.invoice_no, the
            # same field Add Transaction's Receipt flow already uses for this
            # (labelled "Invoice No / Receipt No" there). Kitchen Board's
            # quick-receive modal never captured this at all before, so a
            # real supplier delivery (e.g. Meatco order #A25533) had no
            # record of who it came from once entered.
            invoice_no = (request.POST.get('note') or '').strip()[:50]
            qty = Decimal(str(qty_raw))
            cost = Decimal(str(cost_raw))
            if qty <= 0:
                return JsonResponse({'ok': False, 'error': 'Idadi lazima iwe zaidi ya 0.'}, status=400)
            # 2026-08-09 live request (Roy) — "+Pata Stok... which chicken
            # have I received today?" When a preset is chosen, route through
            # the SAME KitchenStockReceipt/Line ledger the fuller "🧾 Stock
            # Receipt" flow already uses, so per-cut visibility (see
            # kitchen_board()'s _received_by_preset) and per-preset cost
            # correctly pick this up — no new ledger invented. This is also
            # exactly the "add more to the same batch" mechanism: the ledger
            # is a running SUM per cut, not a discrete batch object, so a
            # second small correction line for the same preset just adds to
            # the existing total — never a separate, disconnected "batch".
            preset_id_raw = (request.POST.get('preset_id') or '').strip()
            preset = None
            if preset_id_raw.isdigit():
                preset = ItemPortionPreset.objects.filter(id=int(preset_id_raw), item=item).first()
            if preset is not None:
                txn = Transaction.objects.create(
                    business=business, item=item, type='Receipt',
                    qty=qty, payment_method='cash', invoice_no=invoice_no,
                )
                if cost > 0:
                    preset.cost_price = (cost / qty).quantize(Decimal('0.01'))
                    preset.save(update_fields=['cost_price'])
                receipt = KitchenStockReceipt.objects.create(
                    business=business, store=kitchen_store,
                    invoice_no=invoice_no, recorded_by=request.user,
                )
                KitchenStockReceiptLine.objects.create(
                    receipt=receipt, item=item, preset=preset, qty_received=qty,
                    line_cost=cost if cost > 0 else (preset.cost_price or Decimal('0')) * qty,
                    transaction=txn,
                )
                return JsonResponse({
                    'ok': True, 'new_balance': float(item.current_balance()),
                    'preset_id': preset.id, 'preset_label': preset.label,
                })
            Transaction.objects.create(
                business=business,
                item=item,
                type='Receipt',
                qty=qty,
                payment_method='cash',
                invoice_no=invoice_no,
            )
            if cost > 0:
                item.cost_price = cost / qty
                item.save(update_fields=['cost_price'])
            return JsonResponse({'ok': True, 'new_balance': float(item.current_balance())})
    except Exception as exc:
        logger.exception('kitchen_receive failed business=%s item=%s mode=%s', business.id, item_id, mode)
        return JsonResponse({'ok': False, 'error': f'Hitilafu: {exc}'}, status=500)


# ── Kitchen Stock Receipt (multi-item delivery, pooled cost) ───────────────────
# 2026-07-25 live request: chicken (and similar) arrives as ONE delivery
# covering several cut-items (wings, legs, drumsticks) at once — cost should
# be entered ONCE per delivery, not re-typed every time a portion is
# prepared/sold. Each line item still keeps its own completely ordinary
# stock balance and sells via the existing preset mechanism, unaffected —
# this header only exists to answer "was this whole delivery profitable".

def _kitchen_stock_receipt_to_dict(receipt):
    lines = list(receipt.lines.select_related('item', 'preset'))
    # 2026-08-12 live report (Roy): a receipt for a RAW MATERIAL (e.g. Raw
    # Potatoes, feeding Chipo's batch draws) always shows "Mapato: KES 0" —
    # correctly, since the raw item itself is never sold directly, only
    # drawn into a batch (type='Draw', never counted as revenue). That's
    # not a bug, but it left Roy with no visibility on this card into
    # whether the money actually came back — "I have sold on it and it has
    # reflected in the tile but not in the receipt." Rather than force a
    # false match between two genuinely different revenue streams (which
    # this app's own history already found to be a precision trap — see
    # total_revenue()'s own docstring), surface the linked FINISHED
    # PRODUCT's own already-correct, already-live tile figures alongside
    # the raw material's line — clearly labelled as belonging to Chipo (or
    # whichever batch item), not folded into this receipt's own total.
    raw_material_for = []
    seen_batch_items = set()
    for l in lines:
        for batch_item in l.item.derived_batch_items.all():
            if batch_item.id in seen_batch_items:
                continue
            seen_batch_items.add(batch_item.id)
            open_batches = KitchenBatch.objects.filter(
                item=batch_item, source_item_id=l.item_id, status='OPEN',
            )
            if not open_batches.exists():
                continue
            cost = sum((b.cost_total for b in open_batches), Decimal('0'))
            revenue = sum((b.revenue_collected or Decimal('0') for b in open_batches), Decimal('0'))
            raw_material_for.append({
                'item_name': batch_item.description,
                'open_batch_count': open_batches.count(),
                'cost': float(cost),
                'revenue': float(revenue),
                'profit': float(revenue - cost),
            })
    return {
        'id':            receipt.id,
        'supplier':      receipt.supplier,
        'invoice_no':    receipt.invoice_no,
        'received_on':   receipt.received_on.isoformat(),
        'status':        receipt.status,
        'note':          receipt.note,
        'total_cost':    float(receipt.total_cost),
        'total_revenue': float(receipt.total_revenue()),
        'profit':        float(receipt.profit),
        'profit_pct':    receipt.profit_pct,
        'raw_material_for': raw_material_for,
        'lines': [
            {
                'id':            l.id,
                'item_id':       l.item_id,
                'item_name':     l.item.description,
                'preset_id':     l.preset_id,
                'preset_label':  l.preset.label if l.preset_id else None,
                'qty_received':  float(l.qty_received),
                'line_cost':     float(l.line_cost),
                'unit_cost':     float(l.unit_cost),
                'current_balance': float(l.item.current_balance()),
            }
            for l in lines
        ],
    }


@login_required
@require_POST
def kitchen_stock_receipt_create(request):
    """Record one supplier delivery covering multiple portion items at once —
    e.g. 20 wings @ KES 98, 16 legs @ KES 125, 8 drumsticks @ KES 168 from one
    Meatco order. Each line creates a completely ordinary Receipt Transaction
    (same mechanism kitchen_receive()'s portion mode already uses for a single
    item) and sets that item's cost_price — the ONE cost entry Roy asked for.
    """
    up, business, err = _kb_gate(request)
    if err:
        return err
    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not (is_owner or getattr(up, 'can_receive_kitchen_stock', False)):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya kupokea stok inahitajika'}, status=403)

    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    kitchen_store = _ensure_kitchen_store(business)

    try:
        raw_lines = json.loads(request.POST.get('lines', '[]'))
    except (ValueError, TypeError):
        raw_lines = []
    if not raw_lines:
        return JsonResponse({'ok': False, 'error': 'Ongeza angalau bidhaa moja.'}, status=400)

    supplier   = (request.POST.get('supplier') or '').strip()[:100]
    invoice_no = (request.POST.get('invoice_no') or '').strip()[:50]
    note       = (request.POST.get('note') or '').strip()[:200]

    # 2026-08-11 live request (Roy): "in the receipt i tell the app that
    # this receipt is for a certain day then i just backdate from there
    # till today" — received_on already existed as an editable field on
    # the model (and is exactly what total_revenue()'s window now anchors
    # on, see that method's 2026-08-11 fix), but this view never accepted
    # it from the request, always defaulting to today. Optional — a blank
    # or invalid value keeps the original today-default behaviour.
    received_on = timezone.localdate()
    _received_on_raw = (request.POST.get('received_on') or '').strip()
    if _received_on_raw:
        try:
            from datetime import datetime as _dt
            received_on = _dt.strptime(_received_on_raw, '%Y-%m-%d').date()
        except ValueError:
            received_on = timezone.localdate()

    from django.db import transaction as _txn
    try:
        with _txn.atomic():
            receipt = KitchenStockReceipt.objects.create(
                business=business, store=kitchen_store,
                supplier=supplier, invoice_no=invoice_no, note=note,
                recorded_by=request.user, received_on=received_on,
            )
            created_lines = 0
            for row in raw_lines:
                try:
                    item_id = int(row.get('item_id', 0))
                    qty = Decimal(str(row.get('qty', '0') or '0'))
                    cost = Decimal(str(row.get('cost', '0') or '0'))
                    preset_id_raw = row.get('preset_id')
                    preset_id = int(preset_id_raw) if preset_id_raw else None
                except (TypeError, ValueError, InvalidOperation):
                    continue
                if qty <= 0 or cost <= 0:
                    continue
                item = Item.objects.filter(id=item_id, store=kitchen_store).first()
                if item is None:
                    continue
                # Per-cut costing (2026-07-25): several presets sharing ONE
                # item (e.g. Kuku → Bawa/Paja/Kifua, bought pre-cut, not whole
                # birds) can be bought at genuinely different unit costs. When
                # a line names a preset, its unit cost is written to
                # preset.cost_price, NEVER item.cost_price — item.cost_price
                # is left exactly as it was. A plain item with no preset split
                # keeps the original behaviour unchanged.
                preset = None
                if preset_id:
                    preset = ItemPortionPreset.objects.filter(id=preset_id, item=item).first()
                    if preset is None:
                        continue
                    # 2026-08-09 defense-in-depth (Roy): the frontend picker no
                    # longer offers a tethered preset (tracks_stock_of set, e.g.
                    # "Half Chicken Leg") as its own receivable line — but a
                    # stale cached page could still submit one. Resolve to its
                    # anchor preset ("Full Chicken Leg") so the cost is always
                    # written to the real, physically-received preset, never
                    # the tethered one.
                    if preset.tracks_stock_of_id:
                        preset = preset.tracks_stock_of
                txn = Transaction.objects.create(
                    business=business, item=item, type='Receipt',
                    qty=qty, payment_method='cash',
                    invoice_no=invoice_no or (supplier or '')[:50],
                    recorded_by=request.user,
                )
                unit_cost = (cost / qty).quantize(Decimal('0.01'))
                if preset is not None:
                    preset.cost_price = unit_cost
                    preset.save(update_fields=['cost_price'])
                else:
                    item.cost_price = unit_cost
                    item.save(update_fields=['cost_price'])
                KitchenStockReceiptLine.objects.create(
                    receipt=receipt, item=item, preset=preset, qty_received=qty,
                    line_cost=cost, transaction=txn,
                )
                created_lines += 1
            if created_lines == 0:
                raise ValueError('Hakuna mstari sahihi wa bidhaa.')
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    return JsonResponse({'ok': True, 'receipt': _kitchen_stock_receipt_to_dict(receipt)})


@login_required
def kitchen_stock_receipts_list(request):
    """JSON: open (and a handful of recently-closed) kitchen stock receipts
    for the board's own panel — each with a live profit-so-far preview."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    kitchen_store = _ensure_kitchen_store(business)
    open_receipts = list(
        KitchenStockReceipt.objects.filter(business=business, store=kitchen_store, status='OPEN')
        .prefetch_related('lines__item')
        .order_by('-received_on', '-id')
    )
    recent_closed = list(
        KitchenStockReceipt.objects.filter(business=business, store=kitchen_store, status='DONE')
        .prefetch_related('lines__item')
        .order_by('-closed_at')[:10]
    )
    return JsonResponse({
        'ok': True,
        'open':   [_kitchen_stock_receipt_to_dict(r) for r in open_receipts],
        'closed': [_kitchen_stock_receipt_to_dict(r) for r in recent_closed],
    })


@login_required
@require_POST
def kitchen_stock_receipt_close(request, receipt_id):
    """Staff confirms a delivery is fully sold through (or she's otherwise
    done with it) — "the calculation should go on until she says done"
    (2026-07-25 live request). Optional per-line wastage write-off for any
    genuine leftover balance; never forced — a split/over-sold line (e.g. a
    leg cut into two drumsticks) legitimately has nothing left to write off.
    """
    up, business, err = _kb_gate(request)
    if err:
        return err
    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not (is_owner or getattr(up, 'can_receive_kitchen_stock', False)):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya kupokea stok inahitajika'}, status=403)

    receipt = KitchenStockReceipt.objects.filter(id=receipt_id, business=business).first()
    if receipt is None:
        return JsonResponse({'ok': False, 'error': 'Receipt haikupatikana.'}, status=404)
    if receipt.status == 'DONE':
        return JsonResponse({'ok': True, 'already_closed': True, 'receipt': _kitchen_stock_receipt_to_dict(receipt)})

    try:
        write_offs = json.loads(request.POST.get('write_offs', '[]'))
    except (ValueError, TypeError):
        write_offs = []

    for row in write_offs:
        try:
            line_id = int(row.get('line_id', 0))
            qty = Decimal(str(row.get('qty', '0') or '0'))
        except (TypeError, ValueError, InvalidOperation):
            continue
        if qty <= 0:
            continue
        line = next((l for l in receipt.lines.select_related('item') if l.id == line_id), None)
        if line is None:
            continue
        available = line.item.current_balance()
        if qty > available:
            qty = Decimal(str(available))
        if qty <= 0:
            continue
        Transaction.objects.create(
            business=business, item=line.item, type='Wastage',
            qty=-qty, recipient=f'Receipt #{receipt.id} imefungwa',
            recorded_by=request.user,
        )

    receipt.close(request.user)
    return JsonResponse({'ok': True, 'receipt': _kitchen_stock_receipt_to_dict(receipt)})


@login_required
@require_POST
def kitchen_stock_receipt_reopen(request, receipt_id):
    """Undo a mistaken/premature close (2026-08-09 live report: Roy closed
    "Kamau" — 23 Full Chicken Leg — while it still showed KES 0 revenue,
    before he'd resumed selling; total_revenue()'s window is frozen at
    closed_at, so it could never earn any real sales again once closed).
    Owner/manager only — reopening changes a receipt's own historical
    profit figure, same sensitivity tier as every other financial-figure
    correction in this app."""
    up, business, err = _kb_gate(request)
    if err:
        return err
    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not is_owner:
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja inahitajika'}, status=403)

    receipt = KitchenStockReceipt.objects.filter(id=receipt_id, business=business).first()
    if receipt is None:
        return JsonResponse({'ok': False, 'error': 'Receipt haikupatikana.'}, status=404)

    receipt.reopen()
    return JsonResponse({'ok': True, 'receipt': _kitchen_stock_receipt_to_dict(receipt)})


@login_required
@require_POST
def kitchen_stock_receipt_delete(request, receipt_id):
    """Remove a mistaken KitchenStockReceipt bookkeeping record entirely
    (2026-08-09 live report: Roy — "you have made the previous receipt
    which was a mistake show up, I do not need it").

    2026-08-11 same-day correction: this view never required CLOSED status,
    but the frontend button only rendered on a closed receipt card — a real
    duplicate delivery (Roy entered the same 23 legs via BOTH "+Pata Stok"
    and "🧾 Stock Receipt", not realizing the first one had already worked)
    is discovered and needs deleting immediately, while it's still OPEN, not
    after an unrelated "close" step. `kitchen_board.html` now shows 🗑 Futa
    on an OPEN receipt card too, owner/manager only, same as the closed one.

    Deliberately safe: this deletes only the KitchenStockReceipt header and
    its KitchenStockReceiptLine rows — the bookkeeping wrapper around a
    delivery. The underlying Transaction each line created (the real
    Receipt that added stock) is NEVER touched — KitchenStockReceiptLine.
    transaction is a FK FROM the line TO the transaction (on_delete=
    SET_NULL only matters in the other direction, if the transaction were
    deleted); deleting the line leaves the transaction, and therefore the
    item's stock balance, completely unaffected. Owner/manager only,
    same tier as every other financial-record correction."""
    up, business, err = _kb_gate(request)
    if err:
        return err
    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not is_owner:
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja inahitajika'}, status=403)

    receipt = KitchenStockReceipt.objects.filter(id=receipt_id, business=business).first()
    if receipt is None:
        return JsonResponse({'ok': False, 'error': 'Receipt haikupatikana.'}, status=404)

    receipt.delete()
    return JsonResponse({'ok': True})


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

    # Detect similar (but not identical) existing names — possible duplicates.
    # 2026-07-31 live report (Roy: McKenzie's bar debt and kitchen debt split
    # across two differently-spelled records; his wall-QR receipt only ever
    # showed one of them) — this used to only compare against OPEN tabs, so
    # once a tab converts to debt (status leaves OPEN) it silently stopped
    # being checked against at all, even though the whole point is to catch
    # the split BEFORE it happens again. Now also checks every Customer this
    # business has ever recorded (debt history survives regardless of tab
    # status). Same cheap prefix heuristic as before — genuinely catches
    # typos/case variants ("Mckenzie" vs "McKenzie"), but a real alias
    # ("Jenerali" vs "Genro" — a different string for the same person, not a
    # typo) cannot be auto-detected by any string-similarity check; that
    # case is what Customer.merge_locked (the "🔀 Unganisha na Mteja
    # Mwingine" button on the debt profile) is for.
    all_open_tabs = BarTab.objects.filter(
        business=up.business, status='OPEN',
    ).exclude(customer_name__iexact=name).values_list('customer_name', flat=True)
    all_customers = Customer.objects.filter(
        business=up.business,
    ).exclude(name__iexact=name).values_list('name', flat=True)
    name_lower = name.lower()
    similar_names = []
    seen_lower = set()
    for other_name in list(all_open_tabs) + list(all_customers):
        if not other_name:
            continue
        other_lower = other_name.lower()
        if other_lower in seen_lower or other_lower == name_lower:
            continue
        # Flag if one name is a prefix of the other, or they share ≥4 chars from the start
        if (other_lower.startswith(name_lower[:4]) or name_lower.startswith(other_lower[:4])):
            similar_names.append(other_name)
            seen_lower.add(other_lower)

    # 2026-08-13 live request (Roy) — "Bosco" the debt customer IS the
    # owner Bosco, and staff typing a customer name at checkout should be
    # warned when it's the SAME (exact) name as a linked owner alias, or
    # merely SIMILAR (a genuinely different person could share the owner's
    # first name — never silently assume). Deliberately never auto-
    # redirects the sale itself (see Customer.is_owner_alias's own
    # docstring on why this stays a staff/owner-confirmed action, never a
    # live checkout-time interception) — this is purely an informational
    # hint, same non-blocking spirit as the similar_names check above.
    owner_alias_match = None
    alias_customers = Customer.objects.filter(business=up.business, is_owner_alias=True)
    for ac in alias_customers:
        ac_lower = ac.name.lower()
        if ac_lower == name_lower:
            owner_alias_match = {'exact': True, 'name': ac.name}
            break
        if ac_lower.startswith(name_lower[:4]) or name_lower.startswith(ac_lower[:4]):
            owner_alias_match = {'exact': False, 'name': ac.name}
            # keep scanning in case a later alias is an EXACT match instead

    return JsonResponse({
        'tabs': result,
        'prior_debt': prior_debt,
        'similar_names': similar_names[:5],  # cap at 5
        'owner_alias_match': owner_alias_match,
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
        .select_related('served_by')
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
            from core.tab_receipts import _safe_linked_query
            for _r in _safe_linked_query(
                _KbReceipt.objects.filter(business=up.business), _kb_unmapped
            ):
                _rmeta = _r.meta or {}
                for _ltid in (_rmeta.get('linked_tab_ids') or []):
                    if _ltid in _kb_unmapped and _ltid not in _kb_receipt_map:
                        _kb_receipt_map[_ltid] = _r.token

    # Pending split-bill transfers touching these food tabs — same mechanism
    # as tabs_list() in core/keg_views.py; see BarTabEntry.split_and_transfer_
    # locked() / TabTransferRequest in core/models.py.
    from .models import TabTransferRequest as _KbTransfer
    _pending_out_by_entry = {}
    _pending_in_by_tab = {}
    if _food_tab_ids_all:
        for _t in _KbTransfer.objects.filter(status='PENDING').filter(
            Q(source_tab_id__in=_food_tab_ids_all) | Q(dest_tab_id__in=_food_tab_ids_all)
        ).select_related('source_tab', 'dest_tab'):
            if _t.source_tab_id in _food_tab_ids_all:
                _pending_out_by_entry[_t.entry_id] = {
                    'id': _t.id, 'amount': float(_t.amount), 'paid_amount': float(_t.paid_amount),
                    'dest_customer': _t.dest_tab.customer_name,
                }
            if _t.dest_tab_id in _food_tab_ids_all:
                _pending_in_by_tab.setdefault(_t.dest_tab_id, []).append({
                    'id': _t.id, 'amount': float(_t.amount), 'paid_amount': float(_t.paid_amount),
                    'note': _t.note, 'source_customer': _t.source_tab.customer_name,
                })

    from .keg_views import _tab_served_by_label
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

        def _kb_entry_date(e):
            # 2026-08-02 — parity with core.keg_views.tabs_list()'s
            # _entry_dict: a tab can legitimately span several calendar
            # days, so each entry needs its own date once it's not today's.
            if e.transaction_id and e.transaction.created_at:
                _dt_local = timezone.localtime(e.transaction.created_at)
                if _dt_local.date() != timezone.localdate():
                    return _dt_local.strftime('%d %b')
            return ''

        entries = [
            {
                'id': e.id, 'description': e.description, 'amount': float(e.amount), 'is_paid': e.is_paid,
                'entry_date': _kb_entry_date(e),
                'pending_transfer_out': _pending_out_by_entry.get(e.id),
                'transfer_note': ('' if e.id in _pending_out_by_entry else e.transfer_reason_note()),
            }
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
            'server_name': _tab_served_by_label(tab),
            'total': sum(float(e['amount']) for e in entries),
            'unpaid_total': sum(float(e['amount']) for e in entries if not e['is_paid']),
            'entries': entries,
            'opened_at': _opened_local.strftime('%I:%M %p').lstrip('0'),
            'opened_date': _opened_local.strftime('%Y-%m-%d'),
            'is_bar_tab': False,
            'cross_notice': cross_notice,
            'receipt_url': _rcpt_url,
            'cash_requested': bool(tab.cash_requested_at),
            'incoming_transfers': _pending_in_by_tab.get(tab.id, []),
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
        .select_related('served_by')
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
            'server_name': _tab_served_by_label(tab),
            'total': sum(e['amount'] for e in kitchen_entries),
            'unpaid_total': float(unpaid),
            'entries': kitchen_entries,
            'opened_at': timezone.localtime(tab.opened_at).strftime('%I:%M %p').lstrip('0'),
            'is_bar_tab': True,  # renders as read-only — actions stay on bar board
            'cash_requested': bool(tab.cash_requested_at),
        })

    return JsonResponse({'tabs': result})


# ── Kitchen Batch endpoints (Sprint KF1) ──────────────────────────────────────

def _kb_gate(request):
    """
    Common auth + business + shift + station gate for kitchen batch endpoints
    (kitchen_batch_receive, deplete_kitchen_batch, discard_kitchen_batch,
    kitchen_consumable_add).
    Returns (up, business, error_response) where error_response is non-None on failure.

    Station Scoping Principle: this used to only check for ANY open shift, not
    specifically a kitchen one — a bar-only staffer (no can_access_kitchen) with
    an open BAR shift could deplete/discard a kitchen batch or log a kitchen
    consumable purchase directly, even though the kitchen board is never shown
    to them. Same gap class as kitchen_wastage() (kitchen-module audit finding,
    2026-07-19); kitchen_batch_receive happened to be separately protected by
    its own can_receive_kitchen_stock check, but the other three callers had no
    protection at all. Fixed once here at the shared gate.
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
        from .views import _station_scope
        _, show_kitchen = _station_scope(up)
        if not show_kitchen:
            return up, business, JsonResponse({'ok': False, 'error': 'Hakuna ruhusa ya kitchen.'}, status=403)
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

    # Server-side double-submit backstop — see core/idempotency.py. Creates a
    # real KitchenBatch (kitchen-module audit finding, 2026-07-19).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

    kitchen_store = _ensure_kitchen_store(business)
    item_id = (request.POST.get('item_id') or '').strip()

    try:
        item = Item.objects.get(id=item_id, store=kitchen_store, is_kitchen_batch=True)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana'}, status=404)
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'item_id batili'}, status=400)

    cost_note = (request.POST.get('cost_note') or '').strip()[:200]
    note      = (request.POST.get('note') or '').strip()[:200]

    # Warn if a batch is already open for this item — allow anyway (multi-pot)
    already_open = KitchenBatch.objects.filter(
        item=item, business=business, status='OPEN'
    ).exists()

    try:
        # See the matching comment in kitchen_receive()'s kitchen_batch branch —
        # open_batch() handles both the raw-material-draw and manual-cost paths,
        # and always sets item.cost_price = cost_total (discard() relies on it).
        if item.raw_material_source_id:
            draw_qty = Decimal(str(request.POST.get('draw_qty', '0') or '0'))
            batch = KitchenBatch.open_batch(
                business=business, store=kitchen_store, item=item,
                recorded_by=request.user, cost_note=cost_note, note=note,
                draw_qty=draw_qty,
            )
        else:
            cost_total = Decimal(str(request.POST.get('cost_total', '0') or '0'))
            batch = KitchenBatch.open_batch(
                business=business, store=kitchen_store, item=item,
                recorded_by=request.user, cost_note=cost_note, note=note,
                cost_total=cost_total,
            )
    except (InvalidOperation, ValueError) as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Nambari batili'}, status=400)

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
def deplete_kitchen_portion_item(request, item_id):
    """2026-08-08 live request (Roy — chicken specifically) — a PORTION-mode
    kitchen item (e.g. Kuku sold by cut) has no revenue-envelope the way
    KitchenBatch/ProduceBunch items do, so it never had an "Imekwisha"
    equivalent when the system's tracked balance shows leftover stock that
    physically isn't there anymore. Same shift-gated tier as
    kbDepleteBatch/kbDiscardBatch (any staff, not owner-only — this is a
    sale-completion action, not a financial correction).

    Zeroes the balance via a NO-LOSS adjustment ([ADJ-NOLOSS] — the exact
    same convention adjust_stock_balance()'s own toggle uses), never a
    genuine Wastage entry — the point of this button is "the book balance
    was never physically real" (most likely cause: the per-preset cut
    tracking being fixed in this same round), not a real financial loss to
    record. Correctly excluded from wastage_loss/net_profit/staff
    wastage_kes by the exact same exclusion every other [ADJ-NOLOSS] row
    already gets."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    try:
        item = Item.objects.get(id=item_id, business=business, store__is_kitchen=True)
    except Item.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Item haikupatikana'}, status=404)

    balance = item.current_balance()
    if balance <= 0:
        return JsonResponse({'ok': True, 'already_zero': True, 'new_balance': str(balance)})

    Transaction.objects.create(
        business=business, item=item, type='Wastage', qty=-balance,
        invoice_no='[ADJ-NOLOSS]', recipient='Imekwisha — si hasara halisi',
        recorded_by=request.user,
    )
    actor_name = request.user.get_full_name() or request.user.username
    return JsonResponse({
        'ok': True, 'new_balance': '0',
        'message': f'{item.description}: {actor_name} amesema imekwisha ({balance} {item.unit} imeondolewa, si hasara).',
    })


@login_required
@require_POST
def edit_kitchen_batch_target(request, batch_id):
    """Correct a KitchenBatch's cost_total ("target" gharama) after it was
    opened — e.g. a mistyped raw-material cost at receive time.

    2026-07-25 live request: no way existed to fix a typo'd batch cost once
    a batch was open — deplete/discard were the only two actions available.
    Owner/manager only (stricter than _kb_gate's any-open-shift-staff gate,
    which covers receive/deplete/discard): cost_total drives profit()/
    profit_pct, discard()'s wastage math, AND mirrors into item.cost_price
    (see open_batch()'s docstring) — the same sensitivity level as any other
    financial-figure correction in this app (adjust_stock_balance, petty
    cash review, stock variance review, all owner/manager-only).
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    business = up.business
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja pekee'}, status=403)

    new_cost_raw = (request.POST.get('cost_total') or '').strip()
    try:
        new_cost = Decimal(new_cost_raw)
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Nambari batili'}, status=400)
    if new_cost <= 0:
        return JsonResponse({'ok': False, 'error': 'Gharama lazima iwe zaidi ya 0'}, status=400)

    from django.db import transaction as _db_txn
    with _db_txn.atomic():
        try:
            batch = KitchenBatch.objects.select_for_update().get(
                id=batch_id, business=business, status='OPEN',
            )
        except KitchenBatch.DoesNotExist:
            return JsonResponse(
                {'ok': False, 'error': 'Batch haikupatikana au tayari imefungwa'}, status=404,
            )

        old_cost = batch.cost_total
        when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
        who = request.user.get_full_name() or request.user.username

        batch.cost_total = new_cost
        batch.note = (
            (batch.note + ' | ' if batch.note else '')
            + f'Gharama ilibadilishwa kutoka KES {old_cost:,.0f} kwenda KES {new_cost:,.0f} na {who} — {when}'
        )
        batch.save(update_fields=['cost_total', 'note'])

        # item.cost_price mirrors cost_total for kitchen batch items — see
        # open_batch()'s docstring; keep them in sync on correction too,
        # otherwise discard()'s wastage math and Transaction.cost()'s
        # proportional-share formula would price against the stale figure.
        batch.item.cost_price = new_cost
        batch.item.save(update_fields=['cost_price'])

    return JsonResponse({
        'ok': True,
        'batch': _batch_to_dict(batch),
        'message': (
            f'Gharama ya batch ya {batch.item.description} imebadilishwa kutoka '
            f'KES {old_cost:,.0f} kwenda KES {new_cost:,.0f}.'
        ),
    })


@login_required
@require_POST
def split_kitchen_batch(request, batch_id):
    """2026-08-16 live request (Roy): a kitchen staffer forgot to tap
    "Imekwisha" between buckets of fries — she kept selling through
    buckets 2 and 3 on the SAME still-open batch, so all three buckets'
    sales, cost, and profit were tangled into one. He already has the real
    "a new bucket began" moments written down in the paper sales book.
    Owner/manager only — same tier as edit_kitchen_batch_target (this
    touches cost_total/revenue_collected AND draws additional raw
    material). Splits the batch into as many pieces as cutoff timestamps
    given — see KitchenBatch.split_by_date_locked's own docstring for the
    full mechanism."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    business = up.business
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja pekee'}, status=403)

    cutoffs_raw = [c.strip() for c in (request.POST.get('cutoffs') or '').split(',') if c.strip()]
    if not cutoffs_raw:
        return JsonResponse({'ok': False, 'error': 'Weka angalau tarehe moja ya mgawanyo.'}, status=400)

    from datetime import datetime as _dt
    cutoffs = []
    for raw in cutoffs_raw:
        try:
            naive = _dt.strptime(raw, '%Y-%m-%dT%H:%M')
        except ValueError:
            return JsonResponse({'ok': False, 'error': f'Tarehe si sahihi: {raw}'}, status=400)
        cutoffs.append(timezone.make_aware(naive, timezone.get_current_timezone()))

    try:
        batches = KitchenBatch.split_by_date_locked(batch_id, business, cutoffs, request.user)
    except KitchenBatch.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Batch haikupatikana.'}, status=404)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    label = batches[0].item.description
    parts = ', '.join(
        f"#{b.id} (Gharama KES {b.cost_total:,.0f} · Mapato KES {b.revenue_collected:,.0f})"
        for b in batches
    )
    return JsonResponse({
        'ok': True,
        'message': f'{label} imegawanywa kuwa batch {len(batches)}: {parts}',
        'batches': [_batch_to_dict(b) for b in batches],
    })


@login_required
@require_POST
def edit_raw_material_cost(request, item_id):
    """Correct a raw-material-source item's own cost_price (e.g. "Raw
    Potatoes" feeding Chipo's batch draw) by typing the SACK's whole cost
    and how many of the item's own units make up one sack — the system
    divides for you.

    2026-08-09 live report (Roy): the raw-material tile's ✏️ pencil used
    to hand off to Add Transaction's Receipt flow (per this app's own
    "Item.cost_price has exactly ONE designed writer" rule) — Roy
    explicitly rejected that as "bogus" and asked for a direct sack-
    division entry instead: "it represents the 6 buckets equivalent to a
    whole sack division, it is easier that way." This is a NEW, deliberate,
    narrowly-scoped exception to that rule — same category as
    KitchenBatch.open_batch()'s pre-existing exception (see the Known
    Issues note in CLAUDE.md) — restricted to items that ARE actually a
    raw_material_source for some batch item (item.derived_batch_items
    exists), never any arbitrary item. Also moves the correction affordance
    OFF the batch tile's "✏️ Hariri Gharama" (which now hides itself for a
    raw-material-tracked batch — KitchenBatch.open_batch() already derives
    cost_total from kg_drawn × raw_item.cost_price automatically, so
    fixing THIS item's cost_price is the correct lever for every FUTURE
    draw; "Hariri Gharama" stays for batches with no raw_material_source,
    which have no such item to correct instead).

    Owner/manager only, matching every other financial-figure correction
    in this app.

    Same-day follow-up (Roy): "could it be realistic really when i had not
    put in the cost price for the gunia before, i put it just a few
    moments ago... i expected it to adjust in a certain way, not to stay
    the way it was before" — correctly spotted a real gap: KitchenBatch.
    cost_total is a SNAPSHOT taken once, at open_batch() time
    (kg_drawn × raw_item.cost_price AS IT WAS THEN) — it does not
    dynamically re-read raw_item.cost_price on every view. Correcting this
    item's cost_price alone therefore only affects FUTURE draws, exactly
    as this function's own original docstring said — but that left every
    CURRENTLY OPEN batch already drawn from this item permanently frozen
    at its old, wrong (often unset/zero) cost, with no correction path at
    all once "Hariri Gharama" was removed from a raw-material-tracked
    batch tile in the same sprint. Fixed by retroactively recomputing
    cost_total for every OPEN KitchenBatch sourced from this item
    (source_qty_drawn × the newly corrected cost_price) — the same
    "batch.item.cost_price mirrors cost_total" convention edit_kitchen_
    batch_target already follows, applied here automatically instead of
    needing a second manual step."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    business = up.business
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja pekee'}, status=403)

    item = Item.objects.filter(id=item_id, store__business=business).first()
    if item is None:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana'}, status=404)
    if not item.derived_batch_items.exists():
        return JsonResponse(
            {'ok': False, 'error': 'Hii si malighafi ya batch yoyote'}, status=400,
        )

    try:
        sack_cost = Decimal((request.POST.get('sack_cost') or '').strip())
        units_per_sack = Decimal((request.POST.get('units_per_sack') or '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Nambari batili'}, status=400)
    if sack_cost <= 0 or units_per_sack <= 0:
        return JsonResponse(
            {'ok': False, 'error': 'Gharama ya gunia na idadi ya vyombo lazima ziwe zaidi ya 0'}, status=400,
        )

    from django.db import transaction as _db_txn
    with _db_txn.atomic():
        item = Item.objects.select_for_update().get(id=item.id)
        old_cost = item.cost_price
        new_cost = (sack_cost / units_per_sack).quantize(Decimal('0.01'))
        item.cost_price = new_cost
        item.save(update_fields=['cost_price'])

        when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
        who = request.user.get_full_name() or request.user.username
        updated_batches = []
        open_batches = KitchenBatch.objects.select_for_update().filter(
            business=business, source_item_id=item.id, status='OPEN',
        ).select_related('item')
        for batch in open_batches:
            if not batch.source_qty_drawn:
                continue
            old_batch_cost = batch.cost_total
            new_batch_cost = (batch.source_qty_drawn * new_cost).quantize(Decimal('0.01'))
            batch.cost_total = new_batch_cost
            batch.note = (
                (batch.note + ' | ' if batch.note else '')
                + f'Gharama ya malighafi ilirekebishwa: batch gharama kutoka '
                f'KES {old_batch_cost:,.0f} kwenda KES {new_batch_cost:,.0f} na {who} — {when}'
            )
            batch.save(update_fields=['cost_total', 'note'])
            batch.item.cost_price = new_batch_cost
            batch.item.save(update_fields=['cost_price'])
            updated_batches.append(batch.item.description)

    extra = ''
    if updated_batches:
        extra = f' Batch zilizo wazi za {", ".join(sorted(set(updated_batches)))} zimerekebishwa pia.'

    return JsonResponse({
        'ok': True,
        'item': {'id': item.id, 'cost_price': float(item.cost_price)},
        'updated_batches': updated_batches,
        'message': (
            f'Gharama ya {item.description} imebadilishwa kutoka '
            f'KES {(old_cost or 0):,.2f} kwenda KES {new_cost:,.2f} kwa kila {item.unit} '
            f'(gunia ya KES {sack_cost:,.0f} ÷ {units_per_sack:g} {item.unit}).{extra}'
        ),
    })


@login_required
@require_POST
def reset_preset_restock_anchor(request, item_id):
    """Owner/manager-only, non-destructive sibling of Kitchen Item Reset
    (kitchen_reset_views.py) — stamps ItemPortionPreset.restock_anchor_at
    = now() on the item's own anchor presets (never a tethered preset's own
    field — stock_tracking_anchor_id() never resolves to one), so the
    per-preset "Iliyobaki" tile-visibility tally (see kitchen_board()) only
    counts receiving/sales from this moment forward.

    2026-08-12 live report (Roy, Monsoon Inn): after a genuine physical
    restock (23 fresh Full Chicken Legs, "Mapato: KES 0" — nothing sold
    against it yet), the tile stayed hidden because OLD sold history Roy
    deliberately wants preserved (real revenue, kept on purpose — NOT wiped
    by Kitchen Item Reset) still counted toward the lifetime received-vs-
    sold tally, permanently suppressing "remaining" for that anchor. Kitchen
    Item Reset's own confirm step now stamps this same field as a side
    effect of its destructive wipe — but that always requires a backup +
    typed confirmation + actually deleting rows, wrong when (like here)
    nothing needs deleting at all, the stock is already correctly received
    and just needs the STALE VISIBILITY confusion cleared. This is that
    lighter-weight lever: no backup, no typed confirmation, because it never
    deletes a single Transaction, Receipt, or KES of revenue — purely a
    forward-looking marker for what counts toward "is there stock to sell."
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Ingia kwanza'}, status=403)
    business = up.business
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja pekee'}, status=403)

    item = Item.objects.filter(id=item_id, store__business=business).first()
    if item is None:
        return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana'}, status=404)
    if item.is_kitchen_batch:
        return JsonResponse(
            {'ok': False, 'error': 'Hii inafanya kazi kwa bidhaa za kawaida (portion) pekee, si batch.'}, status=400,
        )

    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Ombi hili tayari limetumwa.'}, status=409)

    now = timezone.now()
    updated = item.portion_presets.filter(tracks_stock_of__isnull=True).update(restock_anchor_at=now)
    return JsonResponse({
        'ok': True,
        'updated': updated,
        'message': f'Alama mpya imewekwa kwa {item.description} — mauzo/mapokezi ya zamani hayataathiri tena.',
    })


@login_required
@require_POST
def kitchen_consumable_add(request):
    """Log a kitchen consumable purchase (khaki bags, tomato sauce, cooking oil)."""
    up, business, err = _kb_gate(request)
    if err:
        return err

    # Server-side double-submit backstop — see core/idempotency.py. No natural
    # "already done" guard exists here (a fresh KitchenConsumableLog row is
    # always valid), so a duplicate/retried request would double-count both
    # the purchase cost and the pool balance, masking a real future shortage
    # behind false confidence (kitchen-module audit finding, 2026-07-19 — same
    # gap class as add_cups already fixed in the bar module).
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Hii tayari imehifadhiwa.', 'duplicate': True}, status=409)

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
    from .views import _station_scope
    _, show_kitchen = _station_scope(up)
    if not show_kitchen:
        return JsonResponse({'ok': False}, status=403)
    pool = keg_metrics.kitchen_consumable_pool(up.business)
    return JsonResponse({'ok': True, 'pool': pool})


@login_required
def kitchen_stats_api(request):
    """AJAX GET — today's kitchen revenue for the live badge update.

    Station Scoping Principle explicitly calls out revenue visibility
    ("bar-only staff must NEVER see kitchen revenue") — this returned kitchen
    revenue_today to any authenticated staffer regardless of station
    (kitchen-module audit finding, 2026-07-19). Lower stakes than the
    write-endpoint gaps fixed alongside it (an aggregate KES figure, not an
    action), but still a real violation of the documented principle.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False}, status=403)
    from .views import _station_scope
    _, show_kitchen = _station_scope(up)
    if not show_kitchen:
        return JsonResponse({'ok': False}, status=403)
    business = up.business
    kitchen_store = _kitchen_store(business)
    # 2026-08-09 — same confirmed-vs-credit split AND same [SVQ] exclusion
    # as kitchen_board()'s own initial render (see that view's detailed
    # comment); this is the LIVE poll refreshing the same "🍽 Leo" header
    # tile, so it must stay consistent with what the page shows on load.
    revenue_today = Decimal('0')
    revenue_credit = Decimal('0')
    if kitchen_store:
        # 2026-08-12 — same date-vs-created_at fix as kitchen_board()'s own
        # initial render (see that view's detailed comment); this is the
        # LIVE poll refreshing the same "🍽 Leo" tile, must stay consistent.
        from core.shift_views import station_revenue_window_start
        _window_start = station_revenue_window_start(business, is_kitchen=True)
        txns = Transaction.objects.filter(
            business=business,
            type='Issue',
            created_at__gte=_window_start,
            item__store=kitchen_store,
            payment_method__in=['cash', 'mpesa', 'credit'],
        ).exclude(payment_method='void').exclude(invoice_no='[SVQ]').select_related('item')
        for t in txns:
            rev = Decimal(str(t.revenue()))
            if t.payment_method == 'credit':
                revenue_credit += rev
            else:
                revenue_today += rev
    return JsonResponse({
        'ok': True,
        'revenue_today': float(revenue_today),
        'revenue_credit': float(revenue_credit),
    })
