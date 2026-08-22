"""
Guided Stock Reconciliation views.

Flow:
  1. Owner/manager opens /stock/take/ → enters physical counts.
  2. On POST, StockTake + StockVarianceQuery rows are created for non-zero variances.
  3. The shift's staff member (if linked) is notified via SMS + in-app.
  4. Staff responds at /stock/variances/<id>/respond/.
  5. Owner reviews at /stock/variances/ and accepts (creates corrective Transaction) or dismisses.
  6. Dismissed variances set compliance_noted=True → appear on Haki contribution report.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import (
    Item, ShiftStockCount, StockTake, StockVarianceQuery, Store, Transaction,
)
from core.notifications import (
    create_in_app_notification, normalize_ke_phone, send_sms_notification,
    send_sms_notification_async,
)
from core.views import get_user_profile, owner_or_manager_required

logger = logging.getLogger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def _notify_owner(business, title, message):
    """Send in-app + SMS notification to all owners of a business."""
    from accounts.models import UserProfile
    owners = UserProfile.objects.filter(business=business, role='owner').select_related('user')
    for op in owners:
        create_in_app_notification(op.user, title, message, notification_type='warning', link_url='/stock/variances/')
        if op.phone:
            send_sms_notification_async(message, normalize_ke_phone(op.phone))


def item_has_pending_variance(item_id):
    """2026-07-26 (item 6, live request): a stock-take discrepancy on a
    SPECIFIC item blocks selling that exact item — never the whole business —
    until the owner resolves it via review_variance() (accept or dismiss),
    which is the ONLY thing that flips status to RESOLVED. This is why no
    separate "unlock" endpoint is needed: resolution IS the unlock, and it is
    already owner/manager-only (see @owner_or_manager_required on that view).
    'responded' (staff has explained but owner hasn't decided yet) still
    blocks — only a genuine owner decision clears it, per Roy's explicit
    "only revocable on the owner's side."
    """
    return StockVarianceQuery.objects.filter(
        item_id=item_id,
    ).exclude(status=StockVarianceQuery.RESOLVED).exists()


# ── Shift-change accountability (2026-08-22, Roy) ──────────────────────────────
#
# Q: "staff A closes shift after 12 hours, takes stock take, goes home... staff
# B reports and opens shift then takes stock take but then the system shows an
# imbalance... to whom does the variance attribution fall on to?"
#
# attribute_variance_shift() (shift_views.py) already answers "A or B" fairly —
# it walks back to A's shift when B's own shift shows no activity yet. What it
# CANNOT tell apart is "A's count was sloppy" from "the loss happened during the
# unattended gap itself" — both land on the same coarse "ask A" outcome. Worse,
# a single legitimate owner sale (the owner never needs a shift to sell) landing
# after B's shift technically started can fool that coarse check into blaming
# B instead, for a loss that predates her entirely.
#
# The functions below add a SHARPER, trail-aware check specifically for an
# OPENING-phase count: instead of inferring who's accountable from "did this
# shift touch the item," they directly compare A's own verified physical
# CLOSING count to B's OPENING count, netted against every real Transaction
# recorded in between (sales by the owner included) — Roy's own framing:
# "so long as there is a trail of the sales, the system should make the gap
# make sense... all parties concerned should be aware of the same." A residual
# that survives netting the real trail is the genuine, still-unexplained gap.

def _immediately_preceding_shift(business, current_shift, is_kitchen):
    """The one shift that closed right before `current_shift` opened, on the
    SAME station — the literal "who had custody right before this gap"
    shift. Deliberately distinct from attribute_variance_shift()'s own walk-
    back, which can skip PAST this shift to an even earlier one if it shows
    zero activity on the specific item in question — correct for ordinary
    attribution, but gap-reconciliation specifically needs THIS shift's own
    physical closing count as its anchor, not whichever shift the coarse
    mechanism happens to land on."""
    from .models import Shift
    from .shift_views import _station_q
    return Shift.objects.filter(
        business=business, started_at__lt=current_shift.started_at,
    ).filter(_station_q(is_kitchen)).order_by('-started_at').first()


def _gap_reconciled_variance(business, item, prior_shift):
    """If `prior_shift` physically counted this item at its own close
    (ShiftStockCount phase='closing'), compute what the item's balance
    SHOULD be right now using that real count as the anchor plus every real
    Transaction recorded on this item since — regardless of whether the
    coarse "has the new shift touched this item" check would have credited
    that activity to the old shift or the new one. Returns None when no such
    closing count exists (nothing to reconcile against — caller falls back
    to ordinary book-balance attribution), otherwise a dict with the anchor
    count, the net movement since, and the resulting expected balance."""
    if not prior_shift:
        return None
    closing = ShiftStockCount.objects.filter(
        shift=prior_shift, item=item, phase='closing',
    ).first()
    if not closing:
        return None
    anchor_at = closing.recorded_at
    net_movement = Transaction.objects.filter(
        business=business, item=item, created_at__gt=anchor_at,
    ).aggregate(t=Sum('qty'))['t'] or 0
    net_movement = Decimal(str(net_movement))
    return {
        'closing':       closing,
        'anchor_at':     anchor_at,
        'net_movement':  net_movement,
        'expected_now':  closing.actual_count + net_movement,
    }


def _process_variance_row(business, item, actual_count, linked_shift, stock_take, conducted_by, phase=None):
    """Evaluate one item's physical count against its book balance, decide
    attribution, and create the StockVarianceQuery row if warranted.

    For phase='opening' with a linked shift, first tries the sharper gap-
    reconciliation check (see _gap_reconciled_variance()) against the
    immediately-preceding same-station shift's own closing count. A
    residual fully explained by the real trail is auto-resolved — kind=
    'gap', owner_accepted=True, status=RESOLVED, no one asked to respond,
    but the row still exists and is notified so "all parties concerned" can
    see the reconciliation (Roy's own words). A genuine leftover residual
    after netting the trail becomes a real, still-pending 'gap' row,
    attributed to the prior shift specifically (not the coarse walk-back's
    answer, which a legitimate owner sale after the new shift's own
    started_at could otherwise mislead). Falls back to ordinary
    attribute_variance_shift()-based 'shift' attribution whenever the prior
    shift never physically counted this item at all.

    Returns (svq_or_None, redirected_bool, auto_resolved_bool). svq is None
    only when there was nothing to flag at all (variance is exactly zero).
    """
    from accounts.models import UserProfile
    from .shift_views import attribute_variance_shift

    book_balance = Decimal(str(item.current_balance()))
    variance = actual_count - book_balance
    if variance == 0:
        return None, False, False

    kind = StockVarianceQuery.KIND_SHIFT
    gap_note = ''
    auto_resolved = False
    attributed_shift = attribute_variance_shift(business, linked_shift, item=item)

    if phase == 'opening' and linked_shift and item.store_id:
        is_kitchen = bool(item.store.is_kitchen)
        prior_shift = _immediately_preceding_shift(business, linked_shift, is_kitchen)
        gap = _gap_reconciled_variance(business, item, prior_shift)
        if gap:
            residual = actual_count - gap['expected_now']
            when_label = timezone.localtime(gap['anchor_at']).strftime('%d %b, %H:%M')
            net_sign = '+' if gap['net_movement'] >= 0 else ''
            trail_note = (
                f"Zamu ya awali ilihesabu {gap['closing'].actual_count:.2g} tarehe {when_label}. "
                f"Tangu wakati huo, mienendo halisi ya hisa (mauzo/mapokezi) = "
                f"{net_sign}{gap['net_movement']:.2g}. Ilitegemewa iwe {gap['expected_now']:.2g}, "
                f"imehesabiwa {actual_count:.2g}."
            )
            kind = StockVarianceQuery.KIND_GAP
            attributed_shift = prior_shift
            book_balance = gap['expected_now']
            variance = residual
            if residual == 0:
                auto_resolved = True
                gap_note = trail_note + " Imelinganishwa kiotomatiki na mfumo — hakuna hatua inayohitajika."
            else:
                gap_note = trail_note + " Tofauti hii bado haijaelezwa."

    direction = StockVarianceQuery.DECREASE if variance < 0 else StockVarianceQuery.INCREASE
    estimated_revenue = None
    if direction == StockVarianceQuery.DECREASE and item.selling_price:
        estimated_revenue = abs(variance) * Decimal(str(item.selling_price))

    queried_staff = None
    if attributed_shift and attributed_shift.staff:
        queried_staff = UserProfile.objects.filter(
            user=attributed_shift.staff, business=business
        ).first()

    svq = StockVarianceQuery.objects.create(
        stock_take=stock_take,
        item=item,
        item_name_cache=item.description,
        book_balance=book_balance,
        actual_count=actual_count,
        direction=direction,
        estimated_revenue=estimated_revenue,
        queried_staff=queried_staff,
        attributed_shift=attributed_shift,
        kind=kind,
        gap_note=gap_note,
    )

    if auto_resolved:
        svq.owner_accepted = True
        svq.status = StockVarianceQuery.RESOLVED
        svq.owner_note = 'Imelinganishwa kiotomatiki na mfumo — tazama maelezo hapo juu.'
        svq.save(update_fields=['owner_accepted', 'status', 'owner_note'])

    redirected = bool(
        linked_shift and attributed_shift and attributed_shift.id != linked_shift.id
    )
    return svq, redirected, auto_resolved


def _send_variance_notifications(
    business, conducted_by, variances_created, variance_items,
    by_queried_staff, redirected_for_current, current_staff_profile,
    auto_reconciled_count, auto_reconciled_items, auto_reconciled_by_staff,
):
    """Notification batching shared by run_accountability_stock_take()'s two
    callers — split out purely so the function itself doesn't run too long."""
    if variances_created:
        items_summary = ', '.join(variance_items[:5])
        if len(variance_items) > 5:
            items_summary += f' ... (+{len(variance_items) - 5} zaidi)'

        conductor_name = conducted_by.get_full_name() or conducted_by.username
        owner_msg = (
            f"Hesabu ya stok na {conductor_name}: tofauti {variances_created} "
            f"imepatikana ({items_summary}). Angalia: /stock/variances/"
        )
        if redirected_for_current:
            owner_msg += (
                f" Baadhi ya vitu ({len(redirected_for_current)}) vimehusishwa na "
                f"zamu iliyopita, si zamu ya sasa — angalia ukurasa wa Variances kwa maelezo."
            )
        if auto_reconciled_count:
            owner_msg += (
                f" (Vitu {auto_reconciled_count} zaidi vilikuwa na tofauti lakini "
                f"vimeelezwa kiotomatiki na mfumo — hakuna hatua inayohitajika.)"
            )
        _notify_owner(business, f"📊 Tofauti za Stok ({variances_created})", owner_msg)

        for bucket in by_queried_staff.values():
            qs_profile = bucket['staff']
            if not qs_profile:
                continue
            qs_items = ', '.join(bucket['items'][:5])
            if len(bucket['items']) > 5:
                qs_items += f' ... (+{len(bucket["items"]) - 5} zaidi)'
            if bucket['redirected']:
                staff_msg = (
                    f"Kuna tofauti {len(bucket['items'])} za stok zinazohusishwa na zamu yako "
                    f"iliyopita ({qs_items}) — hazikutokea leo, zilikuwepo kabla ya zamu ya sasa "
                    f"kuanza. Tafadhali eleza: jaribu ukurasa wa 'Variances' katika app."
                )
            else:
                staff_msg = (
                    f"Kuna tofauti {len(bucket['items'])} za stok wakati wa zamu yako "
                    f"({qs_items}). Tafadhali eleza: jaribu ukurasa wa 'Variances' katika app."
                )
            create_in_app_notification(
                qs_profile.user,
                f"📊 Tofauti {len(bucket['items'])} za Stok",
                staff_msg,
                notification_type='warning',
                link_url='/stock/variances/',
            )
            if qs_profile.phone:
                send_sms_notification_async(staff_msg, normalize_ke_phone(qs_profile.phone))

        if redirected_for_current and current_staff_profile:
            clear_items = ', '.join(redirected_for_current[:5])
            if len(redirected_for_current) > 5:
                clear_items += f' ... (+{len(redirected_for_current) - 5} zaidi)'
            clear_msg = (
                f"Hesabu ya stock imepata tofauti kwa vitu ({clear_items}) ambavyo "
                f"hujauza bado kwenye zamu yako ya sasa — vimehusishwa na zamu iliyopita "
                f"badala yako. Hakuna hatua inayohitajika kwako kwa sasa."
            )
            create_in_app_notification(
                current_staff_profile.user,
                "ℹ️ Tofauti za Stock — Si Zamu Yako",
                clear_msg,
                notification_type='info',
                link_url='/stock/variances/',
            )

    # 2026-08-22 — transparency for gap variances the system already
    # explained via the recorded sales trail (e.g. the owner selling with no
    # shift open) — Roy: "all parties concerned should be aware of the
    # same." Purely informational; never asks anyone to respond.
    if auto_reconciled_count:
        if not variances_created:
            items_summary = ', '.join(auto_reconciled_items[:5])
            if len(auto_reconciled_items) > 5:
                items_summary += f' ... (+{len(auto_reconciled_items) - 5} zaidi)'
            owner_msg = (
                f"Tofauti {auto_reconciled_count} zilizoonekana kwenye hesabu ya stock "
                f"({items_summary}) zimeelezwa kiotomatiki na mfumo — mauzo/mapokezi "
                f"halisi wakati wa mapumziko yanahesabu tofauti hiyo. Hakuna hatua "
                f"inayohitajika."
            )
            _notify_owner(business, f"ℹ️ Tofauti Zilizolinganishwa Kiotomatiki ({auto_reconciled_count})", owner_msg)

        for bucket in auto_reconciled_by_staff.values():
            qs_profile = bucket['staff']
            if not qs_profile:
                continue
            qs_items = ', '.join(bucket['items'][:5])
            if len(bucket['items']) > 5:
                qs_items += f' ... (+{len(bucket["items"]) - 5} zaidi)'
            staff_msg = (
                f"Tofauti ya stock ya zamu yako iliyopita kwa vitu ({qs_items}) "
                f"imeelezwa kiotomatiki na mfumo — mauzo halisi wakati wa mapumziko "
                f"yanahesabu tofauti hiyo. Hakuna hatua inayohitajika kwako."
            )
            create_in_app_notification(
                qs_profile.user,
                "ℹ️ Tofauti Imeelezwa Kiotomatiki",
                staff_msg,
                notification_type='info',
                link_url='/stock/variances/',
            )

        if current_staff_profile and auto_reconciled_items:
            clear_items = ', '.join(auto_reconciled_items[:5])
            if len(auto_reconciled_items) > 5:
                clear_items += f' ... (+{len(auto_reconciled_items) - 5} zaidi)'
            clear_msg = (
                f"Ulipata tofauti ukifungua shift kwa vitu ({clear_items}) lakini mfumo "
                f"umeelewa kiotomatiki kwa kutumia mauzo halisi wakati wa mapumziko. "
                f"Hakuna unachohitaji kufanya."
            )
            create_in_app_notification(
                current_staff_profile.user,
                "ℹ️ Tofauti Imeelewa Kiotomatiki",
                clear_msg,
                notification_type='info',
                link_url='/stock/variances/',
            )


def run_accountability_stock_take(business, conducted_by, linked_shift, counts, store=None, phase=None, write_shift_stock_count=True):
    """The real accountability engine behind BOTH surfaces that let staff log
    a physical stock count against the app's own book balance:
    (1) the dedicated guided page (start_stock_take — owner/manager only, no
        phase context, preserves its exact pre-2026-08-22 behaviour), and
    (2) the quick "Hesabu Stock" modal on Bar/Kitchen Board's shift open/
        close flow (stock_take_api — any staff, phase='opening'|'closing')
        — 2026-08-22 (Roy): the real accountability tool needs to be
        symmetric, offered at both ends of a shift, not just close. That
        modal previously only ever wrote an informational ShiftStockCount
        row with no attribution, no notification, and no request for an
        explanation at all — every count taken there now feeds this same
        engine.

    counts: [{'item_id':, 'actual_count':}, ...].
    write_shift_stock_count: set False when the caller already wrote its own
    ShiftStockCount snapshot for this exact (shift, item, phase) moments
    earlier in the same request (stock_take_api()'s own loop) — avoids a
    redundant duplicate write; still fully idempotent either way via the
    model's own unique_together.

    Returns (stock_take, variances_created, auto_reconciled_count).
    """
    stock_take = StockTake.objects.create(
        business=business, store=store, conducted_by=conducted_by, shift=linked_shift,
    )

    from accounts.models import UserProfile

    variances_created = 0
    auto_reconciled_count = 0
    variance_items = []
    auto_reconciled_items = []
    by_queried_staff = {}
    auto_reconciled_by_staff = {}
    current_staff_profile = None
    if linked_shift and linked_shift.staff:
        current_staff_profile = UserProfile.objects.filter(
            user=linked_shift.staff, business=business,
        ).first()
    redirected_for_current = []

    for row in counts:
        try:
            item_id      = int(row.get('item_id', 0))
            actual_count = Decimal(str(row.get('actual_count', 0)))
        except (TypeError, ValueError, InvalidOperation):
            continue

        item = Item.objects.filter(id=item_id, store__business=business).first()
        if item is None:
            continue

        if write_shift_stock_count and linked_shift:
            ShiftStockCount.objects.update_or_create(
                shift=linked_shift, item=item, phase=(phase or 'closing'),
                defaults={
                    'book_balance': Decimal(str(item.current_balance())),
                    'actual_count': actual_count,
                    'recorded_by':  conducted_by,
                },
            )

        svq, redirected, auto_resolved = _process_variance_row(
            business, item, actual_count, linked_shift, stock_take, conducted_by, phase=phase,
        )
        if svq is None:
            continue

        item_line = (
            f"{item.description}: {'−' if svq.variance < 0 else '+'}"
            f"{abs(svq.variance):.2g} {item.unit}"
        )

        if auto_resolved:
            auto_reconciled_count += 1
            auto_reconciled_items.append(item_line)
            if svq.queried_staff:
                bucket = auto_reconciled_by_staff.setdefault(svq.queried_staff.id, {
                    'staff': svq.queried_staff, 'items': [],
                })
                bucket['items'].append(item_line)
            continue

        variances_created += 1
        variance_items.append(item_line)
        if svq.queried_staff:
            bucket = by_queried_staff.setdefault(svq.queried_staff.id, {
                'staff': svq.queried_staff, 'items': [], 'redirected': redirected,
            })
            bucket['items'].append(item_line)
        if redirected and current_staff_profile:
            redirected_for_current.append(item_line)

    _send_variance_notifications(
        business, conducted_by, variances_created, variance_items,
        by_queried_staff, redirected_for_current, current_staff_profile,
        auto_reconciled_count, auto_reconciled_items, auto_reconciled_by_staff,
    )

    return stock_take, variances_created, auto_reconciled_count


# ── View 1: Start / submit a stock take ───────────────────────────────────────

@owner_or_manager_required
def start_stock_take(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    # Optional query params
    store_id  = request.GET.get('store') or request.POST.get('store')
    shift_id  = request.GET.get('shift') or request.POST.get('shift')

    from core.models import Shift
    linked_shift = None
    if shift_id:
        linked_shift = Shift.objects.filter(id=shift_id, business=business).first()

    scoped_store = None
    if store_id:
        scoped_store = Store.objects.filter(id=store_id, business=business).first()

    if request.method == 'POST':
        # Accept JSON body or form-encoded counts[]
        raw_counts = request.POST.get('counts')
        if raw_counts:
            try:
                counts = json.loads(raw_counts)
            except (json.JSONDecodeError, TypeError):
                counts = []
        else:
            counts = []

        if not counts:
            return JsonResponse({'ok': False, 'error': 'Hakuna hesabu zilizotumwa.'}, status=400)

        # Server-side double-submit backstop (2026-07-25 audit finding) — same gap
        # class already fixed elsewhere in this app. A duplicate request (slow-
        # network retry) would otherwise create a second StockTake with duplicate
        # StockVarianceQuery rows for the same items and double-notify both the
        # queried staff and the owner for the same physical count.
        from core.idempotency import claim_checkout_token
        idem_token = (request.POST.get('idempotency_token') or '').strip()
        if not claim_checkout_token(business.id, idem_token):
            return JsonResponse({'ok': False, 'error': 'Hesabu hii tayari imewasilishwa.', 'duplicate': True}, status=409)

        try:
            # 2026-08-22 — the dedicated guided page passes phase=None,
            # preserving its exact pre-existing behaviour (ordinary
            # attribute_variance_shift()-based attribution only, no gap-
            # reconciliation) — that check is deliberately opt-in via an
            # explicit phase='opening', which only the quick shift-open
            # modal (stock_take_api) sends.
            stock_take, variances_created, auto_reconciled_count = run_accountability_stock_take(
                business, request.user, linked_shift, counts, store=scoped_store,
            )
        except Exception as exc:
            logger.exception("Stock take POST failed: %s", exc)
            return JsonResponse({'ok': False, 'error': f'Hitilafu ya seva: {exc}'}, status=500)

        return JsonResponse({
            'ok': True, 'take_id': stock_take.id, 'variance_count': variances_created,
            'auto_reconciled_count': auto_reconciled_count,
        })

    # ── GET ──────────────────────────────────────────────────────────────────
    # Build item list scoped to the right store(s)
    items_qs = Item.objects.filter(
        store__business=business,
        is_produce=False,
    ).select_related('store').order_by('store__name', 'description')

    if scoped_store:
        items_qs = items_qs.filter(store=scoped_store)

    items_data = []
    for item in items_qs:
        items_data.append({
            'id':              item.id,
            'description':     item.description,
            'unit':            item.unit,
            'balance':         float(item.current_balance()),
            'store_name':      item.store.name if item.store else '',
            'volume_ml':       item.volume_ml,
            'selling_price':   float(item.selling_price) if item.selling_price else None,
            'material_number': item.material_no or '',
        })

    stores = Store.objects.filter(business=business).order_by('name')

    return render(request, 'core/stock_take_form.html', {
        'items_data':   json.dumps(items_data),
        'stores':       stores,
        'scoped_store': scoped_store,
        'linked_shift': linked_shift,
        'shift_id':     shift_id or '',
        'store_id':     store_id or '',
    })


# ── View 2: Stock take detail (post-submission summary) ───────────────────────

@login_required
def stock_take_detail(request, take_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')
    business = user_profile.business

    stock_take = get_object_or_404(StockTake, id=take_id, business=business)

    # Access: the person who conducted OR owner/manager
    if (not user_profile.is_owner_or_manager
            and stock_take.conducted_by != request.user):
        return redirect('home')

    variances = stock_take.variances.select_related(
        'item', 'queried_staff__user', 'owner_action_by',
    ).order_by('-created_at')

    return render(request, 'core/stock_take_detail.html', {
        'stock_take': stock_take,
        'variances':  variances,
        'is_owner':   user_profile.is_owner_or_manager,
    })


# ── View 3: History list ──────────────────────────────────────────────────────

@owner_or_manager_required
def stock_take_history(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    takes = StockTake.objects.filter(business=business).select_related(
        'conducted_by', 'store', 'shift',
    ).prefetch_related('variances')[:50]

    takes_data = []
    for st in takes:
        total   = st.variances.count()
        pending = st.variances.filter(status=StockVarianceQuery.PENDING).count()
        takes_data.append({'take': st, 'total': total, 'pending': pending})

    return render(request, 'core/stock_take_history.html', {
        'takes_data': takes_data,
    })


# ── View 4: Owner review — all pending variances ──────────────────────────────

@owner_or_manager_required
def pending_variances(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    pending   = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.PENDING,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user').order_by('created_at')

    responded = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESPONDED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user').order_by('responded_at')

    resolved  = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESOLVED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user', 'corrective_txn',
                     'owner_action_by').order_by('-owner_acted_at')[:30]

    return render(request, 'core/stock_variances_pending.html', {
        'pending':   pending,
        'responded': responded,
        'resolved':  resolved,
        'is_owner':  user_profile.is_owner_or_manager,
    })


# ── View 5: Staff response form ───────────────────────────────────────────────

@login_required
def respond_to_variance(request, var_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')
    business = user_profile.business

    svq = get_object_or_404(StockVarianceQuery, id=var_id, stock_take__business=business)

    # Privacy gate: only the queried staff member or owner/manager
    is_queried = (
        svq.queried_staff is not None
        and svq.queried_staff.user == request.user
    )
    if not user_profile.is_owner_or_manager and not is_queried:
        return redirect('home')

    if svq.status == StockVarianceQuery.RESOLVED:
        return render(request, 'core/stock_variance_respond.html', {
            'svq': svq, 'already_resolved': True,
        })

    if request.method == 'POST':
        response_type     = request.POST.get('response_type', '').strip()
        response_customer = request.POST.get('response_customer', '').strip()
        response_note     = request.POST.get('response_note', '').strip()

        if not response_type:
            return render(request, 'core/stock_variance_respond.html', {
                'svq': svq, 'error': 'Tafadhali chagua aina ya jibu.',
                'response_choices': StockVarianceQuery.RESPONSE_CHOICES,
            })

        svq.response_type     = response_type
        svq.response_customer = response_customer
        svq.response_note     = response_note
        svq.responded_at      = timezone.now()
        svq.status            = StockVarianceQuery.RESPONDED
        svq.save(update_fields=[
            'response_type', 'response_customer', 'response_note',
            'responded_at', 'status',
        ])

        # Notify owner
        conductor_name = request.user.get_full_name() or request.user.username
        resp_label = dict(StockVarianceQuery.RESPONSE_CHOICES).get(response_type, response_type)
        _notify_owner(
            business,
            f"📊 Jibu la Tofauti: {svq.item_name_cache}",
            f"{conductor_name} amejibu tofauti ya {svq.item_name_cache}: {resp_label}.",
        )

        return render(request, 'core/stock_variance_respond.html', {
            'svq': svq, 'submitted': True,
        })

    # GET: show optional preset hint
    preset_hint = None
    if svq.item and svq.direction == StockVarianceQuery.DECREASE:
        from core.models import ItemPortionPreset
        presets = ItemPortionPreset.objects.filter(item=svq.item).order_by('price')
        if presets.exists() and svq.item.volume_ml:
            cheapest = presets.first()
            approx = int(abs(float(svq.variance)) / float(cheapest.quantity_consumed or 1))
            if approx > 0:
                preset_hint = (
                    f"Tofauti ya {abs(svq.variance):.2g} ≈ {approx} × "
                    f"{cheapest.label} (KES {cheapest.price} kila moja)"
                )

    return render(request, 'core/stock_variance_respond.html', {
        'svq':              svq,
        'response_choices': StockVarianceQuery.RESPONSE_CHOICES,
        'preset_hint':      preset_hint,
    })


# ── View 6: Owner review action (accept / dismiss) ────────────────────────────

@owner_or_manager_required
def review_variance(request, var_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    user_profile = get_user_profile(request)
    business = user_profile.business

    svq = get_object_or_404(StockVarianceQuery, id=var_id, stock_take__business=business)

    if svq.status == StockVarianceQuery.RESOLVED:
        return JsonResponse({'ok': False, 'error': 'Already resolved.'})

    action = request.POST.get('action', '')  # 'accept' or 'dismiss'

    if action == 'accept':
        corrective_txn = None
        try:
            # Staff-responded variances use the staff's response type.
            # Pending variances accept an owner-provided reason via POST.
            if svq.response_type:
                _pay      = svq.response_type
                _customer = svq.response_customer or ''
            else:
                _pay      = request.POST.get('owner_response_type', 'cash').strip()
                _customer = request.POST.get('owner_response_customer', '').strip()

            _owner_note = request.POST.get('owner_response_note', '').strip()

            if svq.direction == StockVarianceQuery.DECREASE and svq.item:
                if _pay == 'wastage':
                    # Owner says cause is unknown — record as wastage, no revenue.
                    # Wastage must be negative so current_balance() decreases.
                    corrective_txn = Transaction.objects.create(
                        business=business,
                        item=svq.item,
                        type='Wastage',
                        qty=-abs(svq.variance),
                        payment_method='',
                        recipient=_owner_note or 'Stock adjustment — cause unknown',
                        recorded_by=request.user,
                        date=svq.stock_take.taken_at.date(),
                    )
                else:
                    # Unrecorded sale — cash / mpesa / credit.
                    # Issue must be negative so current_balance() decreases.
                    _pay_for_txn = _pay if _pay in ('cash', 'mpesa', 'credit') else 'cash'
                    corrective_txn = Transaction.objects.create(
                        business=business,
                        item=svq.item,
                        type='Issue',
                        qty=-abs(svq.variance),
                        sale_amount=(svq.estimated_revenue if svq.estimated_revenue else None),
                        payment_method=_pay_for_txn,
                        recipient=_customer,
                        recorded_by=request.user,
                        date=svq.stock_take.taken_at.date(),
                        # 2026-07-25 live report (Monsoon Inn): accepting a morning
                        # stock-take variance was showing up as TODAY's live revenue
                        # on the home dashboard before any real sale had happened —
                        # the discrepancy predates its discovery (it's a correction,
                        # not a fresh POS sale), so it must never inflate the
                        # real-time "today so far" tracking a business owner uses to
                        # follow the day's actual trading. Tagged and excluded from
                        # bar_today_revenue/kitchen_today_revenue (core/views.py) and
                        # _reconcile()'s shift cash/mpesa totals (core/shift_views.py)
                        # — the revenue still counts everywhere else (item history,
                        # analytics/P&L, debt tracker if credit) since it's real money,
                        # just discovered late; same [ADJ]-tag convention already used
                        # by adjust_stock_balance for the same "correction, not a
                        # normal transaction" purpose.
                        invoice_no='[SVQ]',
                    )
            elif (svq.direction == StockVarianceQuery.INCREASE and svq.item):
                corrective_txn = Transaction.objects.create(
                    business=business,
                    item=svq.item,
                    type='Receipt',
                    qty=svq.variance,
                    payment_method='',
                    recipient=_owner_note or '',
                    recorded_by=request.user,
                    date=svq.stock_take.taken_at.date(),
                )
        except Exception as exc:
            logger.exception("Error creating corrective transaction for variance %s", var_id)
            return JsonResponse({'ok': False, 'error': str(exc)})

        svq.owner_accepted  = True
        svq.owner_action_by = request.user
        svq.owner_acted_at  = timezone.now()
        svq.corrective_txn  = corrective_txn
        svq.owner_note      = _owner_note
        svq.status          = StockVarianceQuery.RESOLVED
        svq.save(update_fields=[
            'owner_accepted', 'owner_action_by', 'owner_acted_at',
            'corrective_txn', 'owner_note', 'status',
        ])

        # 2026-07-24 wording/accountability audit: neither branch named who acted
        # or when, and 'accept' never told the staffer who reported the variance
        # what happened to their explanation — only 'dismiss' did, an inconsistency
        # since both are equally a final decision on the same reported variance.
        reviewer_name = request.user.get_full_name() or request.user.username
        when = timezone.localtime(svq.owner_acted_at).strftime('%d %b %Y, %H:%M')
        msg = f'Imekubaliwa na {reviewer_name} tarehe {when}'
        if corrective_txn:
            msg += f' — transaction ya {svq.item_name_cache} imeundwa.'
        else:
            msg += '.'

        if svq.queried_staff:
            create_in_app_notification(
                svq.queried_staff.user,
                f"✅ Tofauti Imekubaliwa: {svq.item_name_cache}",
                f"Mmiliki {reviewer_name} amekubali maelezo yako ya tofauti ya "
                f"{svq.item_name_cache} tarehe {when}.",
                notification_type='info',
                link_url=f'/stock/variances/{svq.id}/respond/',
            )

        return JsonResponse({'ok': True, 'message': msg})

    elif action == 'dismiss':
        _dismiss_note = request.POST.get('owner_response_note', '').strip()

        svq.owner_accepted   = False
        svq.owner_action_by  = request.user
        svq.owner_acted_at   = timezone.now()
        svq.compliance_noted = True
        svq.owner_note       = _dismiss_note
        svq.status           = StockVarianceQuery.RESOLVED
        svq.save(update_fields=[
            'owner_accepted', 'owner_action_by', 'owner_acted_at',
            'compliance_noted', 'owner_note', 'status',
        ])

        reviewer_name = request.user.get_full_name() or request.user.username
        when = timezone.localtime(svq.owner_acted_at).strftime('%d %b %Y, %H:%M')

        # Notify staff
        if svq.queried_staff:
            staff_msg = (
                f"Mmiliki {reviewer_name} amekataa maelezo yako ya tofauti ya "
                f"{svq.item_name_cache} tarehe {when}. Imerekodiwa kwenye rekodi yako ya utendaji."
            )
            if _dismiss_note:
                staff_msg += f' Sababu: {_dismiss_note}'
            create_in_app_notification(
                svq.queried_staff.user,
                f"⚠️ Tofauti Imekataliwa: {svq.item_name_cache}",
                staff_msg,
                notification_type='warning',
                link_url=f'/stock/variances/{svq.id}/respond/',
            )

        dismiss_msg = f'Imekataliwa na {reviewer_name} tarehe {when} — imerekodiwa kwenye rekodi ya utendaji.'
        if _dismiss_note:
            dismiss_msg += f' Sababu: {_dismiss_note}'
        return JsonResponse({'ok': True, 'message': dismiss_msg})

    return JsonResponse({'ok': False, 'error': 'Invalid action.'})


def adjust_stock_balance(request, item_id):
    """
    Quick stock-balance correction for countable items (wines, spirits, dry goods).
    Owner/manager enters the physical count; a Wastage (shortage) or Receipt (surplus)
    transaction is created to reconcile the book balance.
    Returns JSON — called from stock_list.html modal.

    2026-08-11 live request (Roy — "in the event the manager or business owner
    is not around"): this view was previously gated by @owner_or_manager_
    required, a decorator built for full-page views — it HTML-redirects on
    failure, which is wrong for an AJAX endpoint that only ever returns JSON
    (the caller's fetch() just saw a redirected HTML response, not a real
    error). Removed the decorator; the permission check now lives inline
    (JSON-friendly, matching every other AJAX endpoint in this app) and
    additionally accepts an explicit UserProfile.can_adjust_stock opt-in for
    a trusted staffer, same delegation pattern as can_manage_kegs/
    can_receive_stock. Still requires an open shift for delegated staff.
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)

    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not is_owner:
        if not getattr(up, 'can_adjust_stock', False):
            return JsonResponse({'ok': False, 'error': 'Ruhusa ya kurekebisha stock inahitajika.'}, status=403)
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'},
                status=403,
            )

    business = up.business
    item = Item.objects.filter(id=item_id, store__business=business).select_related('store').first()
    if not item:
        return JsonResponse({'ok': False, 'error': 'Item haikupatikana.'}, status=404)

    if item.is_keg or item.is_produce:
        return JsonResponse({'ok': False, 'error': 'Marekebisho haya ni kwa vitu vinavyohesabika tu.'}, status=400)

    actual_str = request.POST.get('actual_count', '').strip()
    note = request.POST.get('note', '').strip()
    # 2026-08-12 (Roy, standing principle): a delegated permission toggle
    # grants the FULL function, not a restricted subset of it — same rule
    # now applied to can_manage_kegs' Tupa/Pokea gap. A can_adjust_stock
    # staffer's "not a real loss" judgment is honored exactly like the
    # owner/manager's, matching the checkbox's own visibility in
    # stock_list.html.
    can_flag_no_loss = is_owner or getattr(up, 'can_adjust_stock', False)
    no_real_loss = can_flag_no_loss and request.POST.get('no_real_loss') in ('1', 'true', 'on')

    if not actual_str:
        return JsonResponse({'ok': False, 'error': 'Taja hesabu halisi.'}, status=400)

    try:
        actual = Decimal(actual_str)
        if actual < 0:
            return JsonResponse({'ok': False, 'error': 'Hesabu haiwezi kuwa chini ya sifuri.'}, status=400)
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Hesabu si sahihi.'}, status=400)

    book = item.current_balance()
    variance = actual - book

    # UBA R1 §7.2 — Rekebisha is a REAL physical count, whichever direction
    # (or none at all) it lands on, so this is exactly the "confirm the
    # balance" moment fast-onboarding items (added with an unknown opening
    # count) are waiting for. Set unconditionally, all three branches below.
    item.balance_confirmed_at = timezone.now()
    item.save(update_fields=['balance_confirmed_at'])

    if variance == 0:
        return JsonResponse({'ok': True, 'no_change': True, 'message': 'Hesabu inafanana na rekodi — hakuna marekebisho yaliyohitajika.'})

    txn_note = note or 'Stock adjustment'

    if variance < 0:
        # Wastage must be negative so current_balance() decreases to the actual count.
        # invoice_no='[ADJ]' — same convention as the surplus branch below —
        # so transaction_history can show this as a count correction rather
        # than plain "Wastage" (which reads as spoilage/loss, not "I
        # recounted and the book was wrong"). Previously only the surplus
        # branch was tagged, an inconsistency found while fixing the
        # display (Roy's report: a surplus correction showed as a plain
        # "Receipt" with no visible distinction from a real delivery).
        #
        # 2026-07-31 live report — a Rekebisha shortage correcting a
        # PHANTOM prior receipt (Chrome Brandy/Gilbey's — a duplicate
        # stock receipt from the network-disruption double-submit bug)
        # showed up as real "Wastage — KES X cost lost" on Daily Sales,
        # net profit, and Haki wastage attribution — overstating a loss
        # that never actually happened (no real money was ever spent on
        # phantom units that were never really received). `[ADJ]` alone
        # can't distinguish this from a GENUINE shortage correction (e.g.
        # a recount discovering real breakage never logged) — that case
        # legitimately IS a real cost and must still count. `no_real_loss`
        # (an explicit owner/manager choice at correction time, never
        # inferred) tags the transaction `[ADJ-NOLOSS]` instead, which
        # analytics_dashboard's wastage_loss, daily_sales's wastage tile,
        # and haki_views._staff_contribution's wastage_kes all exclude —
        # the stock BALANCE correction itself is identical either way,
        # only whether it counts as a financial loss differs.
        Transaction.objects.create(
            business=business,
            item=item,
            type='Wastage',
            qty=-abs(variance),
            date=timezone.localdate(),
            recorded_by=request.user,
            recipient=txn_note,
            invoice_no='[ADJ-NOLOSS]' if no_real_loss else '[ADJ]',
            payment_method='',
        )
        direction_label = f'Punguzo la {abs(variance):g} {item.unit}'
    else:
        # Surplus — stock found above book.  invoice_no='[ADJ]' marks this as
        # a count correction so the home dashboard "missing cost price" alert
        # (which targets supplier Receipts) ignores it.
        Transaction.objects.create(
            business=business,
            item=item,
            type='Receipt',
            qty=variance,
            date=timezone.localdate(),
            recorded_by=request.user,
            recipient=txn_note,
            invoice_no='[ADJ]',
            payment_method='',
        )
        direction_label = f'Ongezeko la {variance:g} {item.unit}'

    new_balance = item.current_balance()
    return JsonResponse({
        'ok': True,
        'message': f'{item.description}: {direction_label}. Salio jipya: {new_balance:g} {item.unit}.',
        'new_balance': str(new_balance),
    })


@owner_or_manager_required
def toggle_adjustment_no_loss(request, txn_id):
    """Retroactively mark an already-recorded Rekebisha shortage correction
    as "not a real loss" (or revert that) — 2026-07-31 live report: Roy
    corrected Chrome Brandy/Gilbey's balances via Rekebisha (reversing the
    duplicate-receipt idempotency bug fixed the same day) and the shortfall
    showed up as real "Wastage — cost lost", overstating a loss that never
    happened. adjust_stock_balance() now captures this choice AT correction
    time via the no_real_loss checkbox — this endpoint is for entries
    already recorded before that existed, or for correcting the choice
    later either direction. Only ever touches a Transaction this SAME
    mechanism created (type='Wastage', invoice_no in ('[ADJ]',
    '[ADJ-NOLOSS]')) — never a genuine Wastage entry, and never the stock
    balance itself (qty is untouched either way — only invoice_no flips).
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    txn = Transaction.objects.filter(
        id=txn_id, business=up.business, type='Wastage',
        invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]'],
    ).select_related('item').first()
    if not txn:
        return JsonResponse({'ok': False, 'error': 'Muamala huu haupatikani au si marekebisho ya Rekebisha.'}, status=404)

    if txn.invoice_no == '[ADJ]':
        txn.invoice_no = '[ADJ-NOLOSS]'
        txn.save(update_fields=['invoice_no'])
        msg = (
            f'{txn.item.description}: marekebisho haya sasa YAMEWEKWA kama si hasara halisi — '
            f'hayatahesabiwa tena kwenye upotevu/gharama.'
        )
    else:
        txn.invoice_no = '[ADJ]'
        txn.save(update_fields=['invoice_no'])
        msg = (
            f'{txn.item.description}: marekebisho haya sasa yanahesabika kama hasara halisi tena.'
        )

    return JsonResponse({'ok': True, 'message': msg, 'invoice_no': txn.invoice_no})
