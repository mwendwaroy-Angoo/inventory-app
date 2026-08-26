"""
Guided Stock Reconciliation views.

Flow:
  1. Owner/manager opens /stock/take/ → enters physical counts.
  2. On POST, StockTake + StockVarianceQuery rows are created for non-zero variances.
  3. The shift's staff member (if linked) is notified via SMS + in-app.
  4. Staff responds at /stock/variances/<id>/respond/.
  5. Owner reviews at /stock/variances/:
       - Accept → corrective Transaction created, resolved immediately, no
         effect on the staffer's own recognition/Haki record.
       - Dismiss (2026-08-26 redesign — a THEFT verdict, not just "I don't
         believe this") → the SAME kind of corrective Transaction is created
         immediately (tagged '[THEFT]'), but the row goes to DISPUTED, not
         RESOLVED — the accused staffer gets Business.variance_dispute_
         window_hours to respond before the verdict (which affects only
         their own record, never the stock correction above) becomes
         permanent. The owner can finalize early ('finalize_now') or
         reconsider at any point after the first decision — see
         review_variance()'s own docstring for the full mechanism.
  6. A finalized (RESOLVED) theft verdict sets compliance_noted=True →
     appears on the Haki contribution report and feeds variance_loss_kes.
"""

import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import (
    Item, ItemPortionPreset, ShiftStockCount, StockTake, StockVarianceQuery, Store, Transaction,
)
from core.notifications import (
    create_in_app_notification, normalize_ke_phone, send_sms_notification,
    send_sms_notification_async,
)
from core.views import get_user_profile, owner_or_manager_required

logger = logging.getLogger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def _notify_owner(business, title, message):
    """Send in-app + SMS + email notification to all owners of a business.

    2026-08-26 live request (Roy — "regarding email notification, write in
    the e-mail notification for stock takes to the business owners, it is
    quite important"): this was in-app + SMS only, confirmed by direct
    trace, no email anywhere in this file. SMS is easy to miss in a busy
    day; email is the one channel Roy specifically wants for something this
    consequential (a stock-take variance, and now a theft verdict).
    """
    from accounts.models import UserProfile
    from core.notifications import send_email_notification_async
    owners = UserProfile.objects.filter(business=business, role='owner').select_related('user')
    for op in owners:
        create_in_app_notification(op.user, title, message, notification_type='warning', link_url='/stock/variances/')
        if op.phone:
            send_sms_notification_async(message, normalize_ke_phone(op.phone))
        if op.user.email:
            send_email_notification_async(op.user.email, title, None, text_message=message)


def item_has_pending_variance(item_id):
    """2026-07-26 (item 6, live request): a stock-take discrepancy on a
    SPECIFIC item blocks selling that exact item — never the whole business —
    until the owner makes a decision via review_variance() (accept or
    dismiss). 'responded' (staff has explained but owner hasn't decided yet)
    still blocks — only a genuine owner decision clears it, per Roy's
    explicit "only revocable on the owner's side."

    2026-08-26 (Roy — theft-verdict redesign): DISPUTED also unblocks, not
    just RESOLVED. Rejecting now means "I believe this was theft" — the
    stock correction happens immediately, and Roy was explicit the item
    "should be sellable again... another staff's mess should not affect
    her normal operations" — the accountability appeal window that follows
    (see StockVarianceQuery.dispute_deadline) is entirely about the ACCUSED
    STAFFER's own record, never about the item's availability.
    """
    return StockVarianceQuery.objects.filter(
        item_id=item_id,
    ).exclude(status__in=[StockVarianceQuery.RESOLVED, StockVarianceQuery.DISPUTED]).exists()


def finalize_expired_variance_disputes(business):
    """Lazily auto-finalize any DISPUTED variance whose appeal window has
    passed — same "checked on read, no real cron" pattern already
    established by KitchenStockReceipt.maybe_auto_close() (this app
    deliberately avoids a real background scheduler; see CLAUDE.md's
    repeated "deferred-cron pattern" notes). Called from every page a
    human might load that would want to see the latest state: the
    owner's variances list and the staffer's own respond page.

    The verdict (owner_accepted=False, compliance_noted=True,
    corrective_txn already set at the moment 'Kataa' was first clicked)
    never changes here — only `status` flips to RESOLVED and
    `dispute_deadline` clears, i.e. this only removes the "still within
    the appeal window" state. Notifies the accused staffer that the
    verdict is now permanent; does not re-notify the owner, who already
    made the decision that's now taking effect.
    """
    expired = StockVarianceQuery.objects.filter(
        stock_take__business=business,
        status=StockVarianceQuery.DISPUTED,
        dispute_deadline__lte=timezone.now(),
    ).select_related('queried_staff__user', 'item')

    for svq in expired:
        svq.status = StockVarianceQuery.RESOLVED
        svq.dispute_deadline = None
        svq.save(update_fields=['status', 'dispute_deadline'])

        if svq.queried_staff:
            when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
            create_in_app_notification(
                svq.queried_staff.user,
                f"🔒 Uamuzi wa Kudumu: {svq.item_name_cache}",
                f"Muda wa kujibu tofauti ya {svq.item_name_cache} umeisha bila jibu — "
                f"uamuzi wa mmiliki umekuwa wa kudumu tarehe {when}. Umerekodiwa kwenye "
                f"rekodi yako ya utendaji na malipo.",
                notification_type='warning',
                link_url=f'/stock/variances/{svq.id}/respond/',
            )
            if svq.queried_staff.phone:
                send_sms_notification_async(
                    f"Muda wa kujibu tofauti ya {svq.item_name_cache} umeisha — uamuzi "
                    f"umekuwa wa kudumu.",
                    normalize_ke_phone(svq.queried_staff.phone),
                )


def _matching_preset_for_increase(item, variance):
    """2026-08-26 (Roy, live — "if the variance is +0.25 or +0.5 or +0.75
    the cost price division should be according to preset"): an INCREASE
    variance that lands on a clean fraction (0.25/0.5/0.75 of a bottle,
    say) is far more likely a PORTIONED preset amount than a whole new
    delivery — the owner cannot "receive" a quarter bottle from a
    supplier the way they receive a whole one. Finds the item's own
    preset whose `quantity_consumed` matches `variance` (small tolerance
    for Decimal noise), so the accept flow can route the cost the owner
    enters into THAT preset's own `cost_price` (per-whole-unit basis,
    same as `item.cost_price` — see `ItemPortionPreset.cost_price`'s own
    docstring) instead of blindly overwriting `item.cost_price` itself,
    which represents the whole bottle, not one portion of it. Returns
    None when nothing matches closely enough — a whole-number variance
    (a genuine new bottle) or an odd, non-preset-aligned fraction (very
    likely accumulated preset-fraction drift, see diagnose_stock_
    shortfalls' own 2026-08-21 'preset_drift' section) both correctly
    fall through to the original flat item.cost_price behaviour.
    """
    if not item or variance is None:
        return None
    target = abs(variance)
    if target == 0:
        return None
    best = None
    best_diff = None
    for preset in item.portion_presets.all():
        if not preset.quantity_consumed:
            continue
        diff = abs(preset.quantity_consumed - target)
        if diff <= Decimal('0.01') and (best_diff is None or diff < best_diff):
            best, best_diff = preset, diff
    return best


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

    # 2026-08-26 — lazy auto-finalize sweep, same "checked on read" pattern
    # as KitchenStockReceipt.maybe_auto_close(). Runs before the querysets
    # below so a just-expired dispute shows up as resolved, not disputed.
    finalize_expired_variance_disputes(business)

    pending   = list(StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.PENDING,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user')
      .prefetch_related('item__portion_presets').order_by('created_at'))

    responded = list(StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESPONDED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user')
      .prefetch_related('item__portion_presets').order_by('responded_at'))

    # 2026-08-26 (Roy — "the cost price division should be according to
    # preset"): for every still-open INCREASE row, work out up front which
    # preset (if any) the fraction matches, so the template can show a
    # "≈ {preset label}" hint and default the accept prompt's cost
    # suggestion correctly — and, when NOTHING matches a clean fraction OR
    # a whole number, a warning that this looks like accumulated preset-
    # fraction drift (see diagnose_stock_shortfalls' 'preset_drift'
    # section) rather than a real unrecorded delivery, steering toward
    # Rekebisha instead of blindly accepting it as a receipt.
    for v in pending + responded:
        if v.direction == StockVarianceQuery.INCREASE and v.item:
            v.matched_preset = _matching_preset_for_increase(v.item, v.variance)
            v.looks_like_drift = (
                v.matched_preset is None
                and abs(v.variance) != abs(v.variance).to_integral_value()
            )
        else:
            v.matched_preset = None
            v.looks_like_drift = False

    # 2026-08-26 (Roy — theft-verdict redesign): a rejected variance whose
    # appeal window is still open — the stock is already corrected, the
    # item already sellable again; this section is purely "still waiting to
    # see if the staffer responds, or for you to confirm/reconsider."
    disputed = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.DISPUTED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user', 'corrective_txn').order_by('dispute_deadline')

    resolved  = list(StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESOLVED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user', 'corrective_txn',
                     'owner_action_by')
      .prefetch_related('item__portion_presets').order_by('-owner_acted_at')[:30])
    for v in resolved:
        v.matched_preset = (
            _matching_preset_for_increase(v.item, v.variance)
            if v.direction == StockVarianceQuery.INCREASE and v.item else None
        )

    return render(request, 'core/stock_variances_pending.html', {
        'pending':   pending,
        'responded': responded,
        'disputed':  disputed,
        'resolved':  resolved,
        'is_owner':  user_profile.is_owner_or_manager,
        'dispute_window_hours': business.variance_dispute_window_hours,
    })


# ── View 5: Staff response form ───────────────────────────────────────────────

@login_required
def respond_to_variance(request, var_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')
    business = user_profile.business

    # 2026-08-26 — same lazy sweep as pending_variances(), so a staffer
    # loading this page right after their own window quietly expired sees
    # "already resolved" instead of a form that's no longer live.
    finalize_expired_variance_disputes(business)

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

    # 2026-08-26 (Roy — theft-verdict redesign): DISPUTED means the owner
    # has already made a preliminary verdict and the stock is already
    # corrected — this page is now specifically the ACCUSED STAFFER'S
    # chance to explain before that verdict becomes permanent, within
    # dispute_deadline. Responding here does NOT change status back to
    # RESPONDED (that would lose the fact a verdict + deadline already
    # exist) — it stays DISPUTED, appeal window still running, and the
    # owner is notified a response has arrived.
    is_disputed = (svq.status == StockVarianceQuery.DISPUTED)

    if request.method == 'POST':
        response_type     = request.POST.get('response_type', '').strip()
        response_customer = request.POST.get('response_customer', '').strip()
        response_note     = request.POST.get('response_note', '').strip()

        if not response_type:
            return render(request, 'core/stock_variance_respond.html', {
                'svq': svq, 'error': 'Tafadhali chagua aina ya jibu.',
                'response_choices': StockVarianceQuery.RESPONSE_CHOICES,
                'is_disputed': is_disputed,
            })

        svq.response_type     = response_type
        svq.response_customer = response_customer
        svq.response_note     = response_note
        svq.responded_at      = timezone.now()
        update_fields = ['response_type', 'response_customer', 'response_note', 'responded_at']
        if not is_disputed:
            svq.status = StockVarianceQuery.RESPONDED
            update_fields.append('status')
        svq.save(update_fields=update_fields)

        conductor_name = request.user.get_full_name() or request.user.username
        resp_label = dict(StockVarianceQuery.RESPONSE_CHOICES).get(response_type, response_type)
        if is_disputed:
            deadline_label = (
                timezone.localtime(svq.dispute_deadline).strftime('%d %b %Y, %H:%M')
                if svq.dispute_deadline else '—'
            )
            _notify_owner(
                business,
                f"⏰ Jibu Limepokewa Kabla ya Muda Kuisha: {svq.item_name_cache}",
                f"{conductor_name} amejibu tofauti ya {svq.item_name_cache} ({resp_label}) — "
                f"bado unahitaji kuthibitisha au kubadilisha uamuzi wako kabla ya {deadline_label}.",
            )
        else:
            _notify_owner(
                business,
                f"📊 Jibu la Tofauti: {svq.item_name_cache}",
                f"{conductor_name} amejibu tofauti ya {svq.item_name_cache}: {resp_label}.",
            )

        return render(request, 'core/stock_variance_respond.html', {
            'svq': svq, 'submitted': True, 'is_disputed': is_disputed,
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
        'is_disputed':      is_disputed,
    })


# ── View 6: Owner review action (accept / dismiss) ────────────────────────────

@owner_or_manager_required
def review_variance(request, var_id):
    """Owner's decision on a stock-take variance — accept, dismiss (now a
    theft verdict, see below), reconsider a past verdict, or finalize a
    dispute early.

    2026-08-26 REDESIGN (Roy, live — "the purpose of this level of scrutiny
    is to capture theft"): 'dismiss' ("Kataa") used to mean nothing more
    than "I don't believe this explanation" — it never corrected the book
    balance and never distinguished an innocent mistake from something
    deliberate. Roy's own framing: a business must replenish/correct its
    stock regardless of WHY it's short — "whether stock is stolen or not,
    the business owner cannot fail to replenish stock" — but that physical
    correction is a completely separate question from the ACCUSATION
    against a specific staffer, which carries real consequences (their
    recognition score, their pay) and deserves a chance to be explained
    before it's locked in.

    So 'dismiss' now means "I believe this was NOT an innocent, explainable
    gap" and creates the SAME kind of corrective transaction 'accept as
    wastage' already did — immediately and permanently, tagged '[THEFT]' so
    it reads distinctly from an ordinary cause-unknown correction in
    Transaction History. The row does NOT go straight to RESOLVED, though —
    it becomes DISPUTED with a `dispute_deadline`
    (Business.variance_dispute_window_hours, owner-configurable) during
    which the accused staffer can respond (see respond_to_variance()) and
    the owner can still reconsider. `item_has_pending_variance()` already
    treats DISPUTED the same as RESOLVED for the ITEM's own availability —
    "another staff's mess should not affect her normal operations" (Roy) —
    this window is purely about the STAFFER's own record.

    Once a row has a corrective_txn already attached, any further accept/
    dismiss call is a pure RECONSIDERATION — same "any number of times,
    never re-touches the underlying record" pattern already established by
    review_petty_cash()'s own undo mechanism (2026-07-25). It flips ONLY
    owner_accepted/compliance_noted/status — the corrective_txn already
    created is never touched, re-created, or reversed, matching Roy's own
    explicit rule: "the only thing that should change is the staff's
    performance record and remuneration ... but not the stock balance."
    select_for_update() below closes the one real race this creates that
    didn't matter before: two near-simultaneous first-time decisions on the
    same row could otherwise both read owner_accepted=None and both create
    a corrective transaction for the same physical shortfall.

    2026-08-26 (Roy, live, same-day follow-up — "the only way stock would
    be extra during stock take is if it was received but not received in
    the system... that is a plus not a theft"): an INCREASE-direction
    variance (physical count HIGHER than the book) never means theft — the
    theft-verdict machinery above (the '[THEFT]' tag, DISPUTED appeal
    window, staff accusation) is scoped to DECREASE only. An increase is
    always reasoned about as an unrecorded RECEIPT (the owner restocked but
    never logged Add Transaction → Receipt), never a "cash sale" — 'accept'
    creates the Receipt (as it already did) and additionally lets the owner
    set a cost price for it, defaulting to the item's CURRENT cost_price
    ("like the previous receipt") unless overridden — a new, narrow,
    documented exception to "Item.cost_price has exactly ONE designed
    writer" (Add Transaction's Receipt flow), in the same spirit as the
    KitchenBatch.open_batch() exception already carved out there. 'dismiss'
    on an increase means "I don't believe this recount" — resolves
    immediately with NO correction created (nothing to append — the owner
    is saying the extra stock isn't real) and NO accountability
    consequence (compliance_noted stays False, no appeal window, no theft
    wording). Because an increase-dismiss may create no correction at all,
    `has_correction` (whether `corrective_txn_id` is set) — not merely
    "has owner_accepted been set" — is the real signal for whether a
    later 'accept' must still DO the correction (reconsidering an
    increase's earlier "not accepted" into "accept" performs the deferred
    Receipt creation right then) or is a pure field-flip reconsideration.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    user_profile = get_user_profile(request)
    business = user_profile.business
    action = request.POST.get('action', '')  # 'accept', 'dismiss', or 'finalize_now'

    from django.db import transaction as _db_txn
    with _db_txn.atomic():
        svq = get_object_or_404(
            StockVarianceQuery.objects.select_for_update(),
            id=var_id, stock_take__business=business,
        )
        # has_correction (not merely "has owner_accepted been set") is the
        # real signal for whether the physical stock correction still needs
        # to happen — see the 2026-08-26 docstring addition above for why
        # these two can diverge for an INCREASE-direction dismiss.
        has_correction = (svq.corrective_txn_id is not None)
        is_increase = (svq.direction == StockVarianceQuery.INCREASE)
        reviewer_name = request.user.get_full_name() or request.user.username
        now = timezone.now()
        when = timezone.localtime(now).strftime('%d %b %Y, %H:%M')

        if action == 'finalize_now':
            if svq.status != StockVarianceQuery.DISPUTED:
                return JsonResponse({'ok': False, 'error': 'Tofauti hii si kwenye muda wa kusubiri jibu.'})
            svq.status = StockVarianceQuery.RESOLVED
            svq.dispute_deadline = None
            svq.save(update_fields=['status', 'dispute_deadline'])
            msg = f'Uamuzi umethibitishwa na {reviewer_name} tarehe {when} — umekuwa wa kudumu.'
            if svq.queried_staff:
                create_in_app_notification(
                    svq.queried_staff.user,
                    f"🔒 Uamuzi wa Kudumu: {svq.item_name_cache}",
                    f"Mmiliki {reviewer_name} amethibitisha uamuzi wa tofauti ya "
                    f"{svq.item_name_cache} tarehe {when} — umekuwa wa kudumu, bila kusubiri "
                    f"muda wote uliobaki. Umerekodiwa kwenye rekodi yako ya utendaji na malipo.",
                    notification_type='warning',
                    link_url=f'/stock/variances/{svq.id}/respond/',
                )
            return JsonResponse({'ok': True, 'message': msg})

        if action == 'revert_miscount':
            # 2026-08-28 live request (Roy, urgent — Chrome Vodka physically
            # 4.75, system 0.75 after a -4 theft-verdict correction; "I do
            # not want to use rekebisha stock so that this variance query
            # to the staff shows that the owner reverted and accepted that
            # it was a stock count miscalculation"). Distinct from the
            # plain accept-reconsideration above: that one deliberately
            # NEVER touches the stock ("the real deficit stands, only
            # whether it was malicious changes"). This is for the DIFFERENT
            # case where the owner has determined there was never a real
            # deficit AT ALL — the physical count taken at stock-take time
            # was simply wrong — so the correction itself must be undone,
            # not just the accusation. Requires a corrective_txn to exist;
            # reverses it via a COMPENSATING transaction (this app never
            # deletes a Transaction) rather than mutating its qty, and
            # retags the ORIGINAL to '[ADJ-NOLOSS]' — the same established,
            # already-excluded-everywhere convention used for a Rekebisha
            # correction later found not to be a real loss — so it stops
            # counting as a real KES loss in P&L/analytics/Haki wastage
            # figures, exactly as if the shortfall had never been real.
            if not has_correction:
                return JsonResponse({'ok': False, 'error': 'Hakuna marekebisho ya kubatilisha kwenye tofauti hii.'})

            orig_txn = svq.corrective_txn
            reversal_note = request.POST.get('owner_response_note', '').strip()

            try:
                if orig_txn.qty < 0:
                    reversal_txn = Transaction.objects.create(
                        business=business, item=svq.item, type='Receipt',
                        qty=abs(orig_txn.qty), payment_method='',
                        recipient=(
                            reversal_note
                            or 'Marekebisho ya awali yamebatilishwa — kosa la kuhesabu stock, si upungufu halisi.'
                        ),
                        recorded_by=request.user, invoice_no='[SVQ-REVERT]',
                    )
                else:
                    reversal_txn = Transaction.objects.create(
                        business=business, item=svq.item, type='Wastage',
                        qty=-abs(orig_txn.qty), payment_method='',
                        recipient=(
                            reversal_note
                            or 'Marekebisho ya awali yamebatilishwa — kosa la kuhesabu stock, si mapokezi halisi.'
                        ),
                        recorded_by=request.user, invoice_no='[SVQ-REVERT]',
                    )
                if orig_txn.type == 'Wastage' and orig_txn.invoice_no != '[ADJ-NOLOSS]':
                    orig_txn.invoice_no = '[ADJ-NOLOSS]'
                    orig_txn.save(update_fields=['invoice_no'])
            except Exception as exc:
                logger.exception("Error reverting corrective transaction for variance %s", var_id)
                return JsonResponse({'ok': False, 'error': str(exc)})

            new_balance = svq.item.current_balance() if svq.item else None
            svq.owner_accepted   = True
            svq.compliance_noted = False
            svq.owner_action_by  = request.user
            svq.owner_acted_at   = now
            svq.status            = StockVarianceQuery.RESOLVED
            svq.dispute_deadline = None
            svq.owner_note = (
                f"Kosa la kuhesabu stock lililobainika na {reviewer_name} tarehe {when} — "
                f"marekebisho ya awali (transaction #{orig_txn.id}) yamebatilishwa kwa "
                f"transaction #{reversal_txn.id}. Si upungufu halisi."
                + (f' Maelezo: {reversal_note}' if reversal_note else '')
            )
            svq.save(update_fields=[
                'owner_accepted', 'compliance_noted', 'owner_action_by',
                'owner_acted_at', 'status', 'dispute_deadline', 'owner_note',
            ])

            msg = (
                f'Kosa la kuhesabu limekubaliwa na {reviewer_name} tarehe {when} — stock '
                f'imerekebishwa kurudi {new_balance} {svq.item.unit if svq.item else ""}. '
                f'Hii haitahesabika kwenye rekodi ya utendaji ya mfanyakazi.'
            )
            if svq.queried_staff:
                staff_msg = (
                    f"Mmiliki {reviewer_name} amegundua kuwa tofauti ya {svq.item_name_cache} "
                    f"tarehe {when} ilikuwa KOSA LA KUHESABU wakati wa hesabu ya stock — SI "
                    f"upungufu halisi. Marekebisho ya awali yamebatilishwa na stock imerekebishwa. "
                    f"Hii HAITAHESABIKA kwenye rekodi yako ya utendaji au malipo."
                )
                create_in_app_notification(
                    svq.queried_staff.user,
                    f"✅ Ilikuwa Kosa la Kuhesabu: {svq.item_name_cache}",
                    staff_msg, notification_type='info',
                    link_url=f'/stock/variances/{svq.id}/respond/',
                )
            return JsonResponse({'ok': True, 'message': msg})

        if action == 'accept':
            if has_correction:
                # Pure reversal — never creates or touches corrective_txn.
                was_theft_verdict = (svq.owner_accepted is False)
                _owner_note = request.POST.get('owner_response_note', '').strip()
                prior_reviewer = (
                    (svq.owner_action_by.get_full_name() or svq.owner_action_by.username)
                    if svq.owner_action_by else '—'
                )
                svq.owner_accepted   = True
                svq.compliance_noted = False
                svq.owner_action_by  = request.user
                svq.owner_acted_at   = now
                svq.status            = StockVarianceQuery.RESOLVED
                svq.dispute_deadline = None
                if was_theft_verdict:
                    svq.owner_note = (
                        f"MAREKEBISHO: uamuzi wa awali (haukukubaliwa kama maelezo halali) na "
                        f"{prior_reviewer} umebadilishwa na {reviewer_name} tarehe {when} — sasa "
                        f"umekubaliwa." + (f' Sababu: {_owner_note}' if _owner_note else '')
                    )
                svq.save(update_fields=[
                    'owner_accepted', 'compliance_noted', 'owner_action_by',
                    'owner_acted_at', 'status', 'dispute_deadline', 'owner_note',
                ])
                msg = (
                    f'Uamuzi umebadilishwa na {reviewer_name} tarehe {when} — hautahesabika '
                    f'tena kwenye rekodi ya utendaji.'
                )
                if svq.queried_staff:
                    staff_msg = (
                        f"Mmiliki {reviewer_name} amebadilisha uamuzi wa awali kuhusu tofauti ya "
                        f"{svq.item_name_cache} tarehe {when} — haitahesabika tena kwenye rekodi "
                        f"yako ya utendaji au malipo."
                    )
                    if _owner_note:
                        staff_msg += f' Sababu: {_owner_note}'
                    create_in_app_notification(
                        svq.queried_staff.user,
                        f"✅ Uamuzi Umebadilishwa: {svq.item_name_cache}",
                        staff_msg, notification_type='info',
                        link_url=f'/stock/variances/{svq.id}/respond/',
                    )
                return JsonResponse({'ok': True, 'message': msg})

            # ── No correction exists yet ────────────────────────────────
            # For DECREASE this is always genuinely first-time (a decrease
            # ALWAYS creates a correction on its first decision, whichever
            # way it goes). For INCREASE it may ALSO be a reconsideration
            # from an earlier "not accepted" dismiss (see the dismiss
            # branch below) — that dismiss deliberately never created a
            # correction, so the deferred Receipt is created right here.
            was_previously_not_accepted = (svq.owner_accepted is False)
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
                    # 2026-08-26 (Roy — "the only way stock would be extra
                    # ... is if it was received but not received in the
                    # system"): always an unrecorded RECEIPT, never a
                    # "sale" — the owner sets a cost price for it,
                    # defaulting to the item's own CURRENT cost_price
                    # ("like the previous receipt") when left blank/
                    # invalid. A DELIBERATE, NARROW exception to "Item.
                    # cost_price has exactly ONE designed writer" — same
                    # documented-exception category as KitchenBatch.
                    # open_batch(); see this view's own docstring.
                    _cost_str = request.POST.get('owner_cost_price', '').strip()
                    cost_price = None
                    if _cost_str:
                        try:
                            cost_price = Decimal(_cost_str)
                            if cost_price < 0:
                                cost_price = None
                        except InvalidOperation:
                            cost_price = None

                    # 2026-08-26 same-day follow-up — "the cost price
                    # division should be according to preset": a variance
                    # matching a preset's own quantity_consumed (a portion
                    # of a bottle, not a whole one) routes the cost into
                    # THAT preset's own cost_price instead of the item's
                    # flat, whole-bottle cost_price — both fields share the
                    # same per-whole-unit basis (see ItemPortionPreset.
                    # cost_price's own docstring), so no conversion is
                    # needed, only the right field to write.
                    matched_preset = _matching_preset_for_increase(svq.item, svq.variance)
                    if cost_price is None:
                        if matched_preset is not None:
                            cost_price = (
                                matched_preset.cost_price
                                if matched_preset.cost_price is not None
                                else svq.item.cost_price
                            )
                        else:
                            cost_price = svq.item.cost_price

                    corrective_txn = Transaction.objects.create(
                        business=business,
                        item=svq.item,
                        type='Receipt',
                        qty=svq.variance,
                        payment_method='',
                        recipient=_owner_note or 'Mapokezi yasiyorekodiwa',
                        recorded_by=request.user,
                        date=svq.stock_take.taken_at.date(),
                        preset=matched_preset,
                    )
                    if matched_preset is not None:
                        if cost_price is not None and cost_price != matched_preset.cost_price:
                            ItemPortionPreset.objects.filter(id=matched_preset.id).update(cost_price=cost_price)
                    elif cost_price is not None and cost_price != svq.item.cost_price:
                        Item.objects.filter(id=svq.item_id).update(cost_price=cost_price)
            except Exception as exc:
                logger.exception("Error creating corrective transaction for variance %s", var_id)
                return JsonResponse({'ok': False, 'error': str(exc)})

            svq.owner_accepted   = True
            svq.compliance_noted = False
            svq.owner_action_by  = request.user
            svq.owner_acted_at   = now
            svq.corrective_txn   = corrective_txn
            svq.owner_note       = _owner_note
            svq.status           = StockVarianceQuery.RESOLVED
            svq.dispute_deadline = None
            svq.save(update_fields=[
                'owner_accepted', 'compliance_noted', 'owner_action_by',
                'owner_acted_at', 'corrective_txn', 'owner_note', 'status',
                'dispute_deadline',
            ])

            # 2026-07-24 wording/accountability audit: neither branch named who acted
            # or when, and 'accept' never told the staffer who reported the variance
            # what happened to their explanation — only 'dismiss' did, an inconsistency
            # since both are equally a final decision on the same reported variance.
            if is_increase:
                msg = f'Imekubaliwa kama mapokezi yasiyorekodiwa na {reviewer_name} tarehe {when}'
            else:
                msg = f'Imekubaliwa na {reviewer_name} tarehe {when}'
            if corrective_txn:
                msg += f' — transaction ya {svq.item_name_cache} imeundwa.'
            else:
                msg += '.'
            if was_previously_not_accepted:
                msg = f'MAREKEBISHO: {msg}'

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

            if is_increase:
                # 2026-08-26 (Roy — "that is a plus not a theft"): an
                # INCREASE-direction variance can never be a theft verdict
                # — "dismiss" here means "I don't believe this recount,"
                # nothing more. No '[THEFT]' tag, no DISPUTED appeal
                # window, no accountability consequence
                # (compliance_noted stays False). If a Receipt was
                # already created (has_correction — the owner previously
                # accepted this as an unrecorded receipt and is now
                # reconsidering), that Receipt is NEVER reversed — same
                # "never touch the stock a second time" rule as the
                # decrease theft-verdict reconsideration below, just
                # without any theft framing to walk back.
                svq.owner_accepted   = False
                svq.compliance_noted = False
                svq.owner_action_by  = request.user
                svq.owner_acted_at   = now
                svq.status           = StockVarianceQuery.RESOLVED
                svq.dispute_deadline = None
                svq.owner_note       = _dismiss_note
                svq.save(update_fields=[
                    'owner_accepted', 'compliance_noted', 'owner_action_by',
                    'owner_acted_at', 'status', 'dispute_deadline', 'owner_note',
                ])

                if has_correction:
                    msg = (
                        f'Umebadilisha uamuzi wa "{svq.item_name_cache}" kuwa HAIKUKUBALIWA na '
                        f'{reviewer_name} tarehe {when} — Transaction #{svq.corrective_txn_id} '
                        f'HAITAGUSWA (stock haibadiliki tena baada ya kusahihishwa).'
                    )
                else:
                    msg = (
                        f'Hesabu ya ziada ya "{svq.item_name_cache}" haikukubaliwa na '
                        f'{reviewer_name} tarehe {when} — hakuna marekebisho yaliyofanywa, '
                        f'stock haijabadilishwa.'
                    )
                if _dismiss_note:
                    msg += f' Sababu: {_dismiss_note}'

                if svq.queried_staff:
                    staff_msg = (
                        f"Mmiliki {reviewer_name} ameamua kuwa ongezeko la {svq.item_name_cache} "
                        f"halikukubaliwa kama mapokezi halisi — hesabu yako haikuchukuliwa. "
                        f"Hii SI tofauti ya wizi/utendaji — haitaathiri rekodi yako."
                    )
                    if _dismiss_note:
                        staff_msg += f' Sababu: {_dismiss_note}'
                    create_in_app_notification(
                        svq.queried_staff.user,
                        f"ℹ️ Hesabu Haikukubaliwa: {svq.item_name_cache}",
                        staff_msg, notification_type='info',
                        link_url=f'/stock/variances/{svq.id}/respond/',
                    )
                return JsonResponse({'ok': True, 'message': msg})

            if has_correction:
                # Re-affirming theft after a prior reversal, OR simply
                # re-confirming while still DISPUTED before the window
                # expired — Roy's own "if he decides to be firm with his
                # decision the verdict now becomes permanent." Either way,
                # never creates a second corrective transaction.
                was_theft_verdict = (svq.owner_accepted is False)
                prior_reviewer = (
                    (svq.owner_action_by.get_full_name() or svq.owner_action_by.username)
                    if svq.owner_action_by else '—'
                )
                svq.owner_accepted   = False
                svq.compliance_noted = True
                svq.owner_action_by  = request.user
                svq.owner_acted_at   = now
                svq.status           = StockVarianceQuery.RESOLVED
                svq.dispute_deadline = None
                if not was_theft_verdict:
                    svq.owner_note = (
                        f"MAREKEBISHO: uamuzi ulioondolewa hapo awali na {prior_reviewer} "
                        f"umerudishwa na {reviewer_name} tarehe {when} — sasa umerekodiwa "
                        f"tena kwenye rekodi ya utendaji."
                        + (f' Sababu: {_dismiss_note}' if _dismiss_note else '')
                    )
                svq.save(update_fields=[
                    'owner_accepted', 'compliance_noted', 'owner_action_by',
                    'owner_acted_at', 'status', 'dispute_deadline', 'owner_note',
                ])
                msg = f'Uamuzi umethibitishwa na {reviewer_name} tarehe {when} — umekuwa wa kudumu.'
                if svq.queried_staff:
                    staff_msg = (
                        f"Mmiliki {reviewer_name} ameendelea na uamuzi kuhusu tofauti ya "
                        f"{svq.item_name_cache} tarehe {when} — umekuwa wa kudumu. Umerekodiwa "
                        f"kwenye rekodi yako ya utendaji na malipo."
                    )
                    if _dismiss_note:
                        staff_msg += f' Sababu: {_dismiss_note}'
                    create_in_app_notification(
                        svq.queried_staff.user,
                        f"🔒 Uamuzi wa Kudumu: {svq.item_name_cache}",
                        staff_msg, notification_type='warning',
                        link_url=f'/stock/variances/{svq.id}/respond/',
                    )
                return JsonResponse({'ok': True, 'message': msg})

            # ── First-time reject — the new theft-verdict path ─────────────
            # DECREASE only — is_increase already returned above, since
            # "the only way stock would be extra ... is if it was received
            # but not received in the system... that is a plus not a
            # theft" (Roy). "the purpose of this level of scrutiny is to
            # capture theft... the business owner cannot fail to replenish
            # stock" (Roy) — unlike the old dismiss (which corrected
            # nothing), this now creates a corrective transaction just
            # like 'accept as wastage' does — the physical correction
            # happens immediately and permanently, completely independent
            # of the appeal window that follows. Tagged '[THEFT]'
            # (distinct from '[ADJ]'/'[ADJ-NOLOSS]'/'[SVQ]') so it reads
            # distinctly in Transaction History — a genuine, real loss
            # (unlike '[ADJ-NOLOSS]'), so it correctly counts everywhere a
            # real Wastage loss already does (P&L, analytics) with no
            # special exclusion needed.
            corrective_txn = None
            try:
                if svq.item:
                    corrective_txn = Transaction.objects.create(
                        business=business, item=svq.item, type='Wastage',
                        qty=-abs(svq.variance), payment_method='',
                        recipient=_dismiss_note or 'Tofauti ya stock — hakuna maelezo yanayokubalika',
                        recorded_by=request.user, date=svq.stock_take.taken_at.date(),
                        invoice_no='[THEFT]',
                    )
            except Exception as exc:
                logger.exception("Error creating corrective transaction for variance %s", var_id)
                return JsonResponse({'ok': False, 'error': str(exc)})

            window_hours = business.variance_dispute_window_hours or 48
            deadline = now + timedelta(hours=window_hours)

            svq.owner_accepted   = False
            svq.owner_action_by  = request.user
            svq.owner_acted_at   = now
            svq.compliance_noted = True
            svq.owner_note       = _dismiss_note
            svq.corrective_txn   = corrective_txn
            svq.dispute_deadline = deadline
            svq.status           = StockVarianceQuery.DISPUTED
            svq.save(update_fields=[
                'owner_accepted', 'owner_action_by', 'owner_acted_at',
                'compliance_noted', 'owner_note', 'corrective_txn',
                'dispute_deadline', 'status',
            ])

            deadline_label = timezone.localtime(deadline).strftime('%d %b %Y, %H:%M')
            unit_label = svq.item.unit if svq.item else ''

            if svq.queried_staff:
                staff_msg = (
                    f"Mmiliki {reviewer_name} ameamua kuwa tofauti ya {svq.item_name_cache} "
                    f"({abs(svq.variance):.2g} {unit_label}) haikuwa maelezo halali — imewekwa "
                    f"kama tofauti isiyoelezeka. Una hadi {deadline_label} kutoa maelezo yako "
                    f"kabla uamuzi huu kuwa wa kudumu — utaathiri rekodi yako ya utendaji na malipo."
                )
                if _dismiss_note:
                    staff_msg += f' Sababu: {_dismiss_note}'
                create_in_app_notification(
                    svq.queried_staff.user,
                    f"🚨 Uamuzi wa Awali: {svq.item_name_cache}",
                    staff_msg, notification_type='warning',
                    link_url=f'/stock/variances/{svq.id}/respond/',
                )
                if svq.queried_staff.phone:
                    send_sms_notification_async(staff_msg, normalize_ke_phone(svq.queried_staff.phone))

            dismiss_msg = (
                f'Imekataliwa na {reviewer_name} tarehe {when} — stock imesahihishwa mara moja. '
                f'Mfanyakazi ana hadi {deadline_label} kujibu kabla uamuzi kuwa wa kudumu.'
            )
            if _dismiss_note:
                dismiss_msg += f' Sababu: {_dismiss_note}'
            return JsonResponse({
                'ok': True, 'message': dismiss_msg,
                'dispute_deadline': deadline.isoformat(),
            })

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

    # 2026-08-25 (Roy — "ensure stock variance during staff stock take is
    # accurate so that we catch this theft that has been happening"): a
    # genuine, concrete gap found while auditing the wider accountability
    # pipeline for this ask. Rekebisha is the single most theft-relevant
    # lever in the app — it PERMANENTLY reconciles the book balance to
    # whatever the person doing it claims is the physical count — but,
    # unlike every sibling loss-recording action (record_breakage,
    # kitchen_wastage, petty cash), it has NEVER notified the owner at all,
    # for either direction, owner-triggered or (since 2026-08-11)
    # can_adjust_stock-delegated-staff-triggered. A dishonest delegated
    # staffer could recount their own shortfall, correct the book down to
    # match reality, optionally tick "not a real loss" (widened to
    # delegated staff on 2026-08-23 per Roy's own permission-parity
    # principle), and the owner would never be told — completely erasing
    # the evidence trail an independent stock take would otherwise have
    # caught, with zero visibility anywhere.
    #
    # Deliberately NOT wired into variance_loss_kes/compute_staff_
    # recognition — Rekebisha is the staffer's own VOLUNTARY, HONEST
    # self-correction (the opposite behaviour from theft); scoring it the
    # same way an independent stock-take-discovered variance is scored
    # would perversely teach staff to leave the books wrong and hope no
    # one ever stock-takes it, which is worse for detection, not better.
    # Roy also explicitly said (2026-08-22) not to add more raw metrics to
    # that scoring rubric. Pure owner/manager VISIBILITY instead — only
    # fires for a DELEGATED staffer's own correction (an owner/manager
    # correcting their own item needs no notification about their own
    # action); a repeated pattern (same item, same staffer, always just
    # before a stock take) becomes something Roy can actually see.
    if not is_owner:
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from core.notifications import normalize_ke_phone, send_sms_notification_async

        reporter_name = request.user.get_full_name() or request.user.username
        when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
        adj_message = (
            f"⚖️ Rekebisha: {item.description} — {direction_label} (na {reporter_name}, "
            f"{when}). Salio jipya: {new_balance:g} {item.unit}."
        )
        if no_real_loss:
            adj_message += ' Imewekwa alama "sio hasara halisi" — haitahesabika kama hasara.'
        if note:
            adj_message += f' Sababu: {note}'

        for om in _UP.objects.filter(
            business=business, role__in=['owner', 'manager']
        ).exclude(user=request.user).select_related('user'):
            Notification.objects.create(
                user=om.user, title='⚖️ Marekebisho ya Stock',
                message=adj_message, notification_type='warning',
                link_url='/stock/',
            )
            if om.phone:
                normalized = normalize_ke_phone(om.phone)
                if normalized:
                    send_sms_notification_async(f"{business.name}: {adj_message}", normalized)

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
