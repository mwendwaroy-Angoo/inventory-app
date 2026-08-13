"""
Sprint 4 — Shift Handover Module.

Flow: staff opens shift (opening float) → sells → closes shift (counts cash + weighs barrels)
      → owner / incoming staff confirms → CONFIRMED.
      Incoming staff opens their shift → confirms barrel weights (SHIFT_OPEN) vs SHIFT_CLOSE.

Reconciliation:
  expected_closing_cash = opening_float + cash_sales_during_shift
  variance = closing_cash_counted − expected_closing_cash
"""
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    BusinessException, Item, KegBarrel, KegWeightReading, PettyCash, Shift,
    ShiftStockCount, Store, Transaction,
)


def _get_up(request):
    from .views import get_user_profile
    return get_user_profile(request)


def _detect_overlapping_shift_pairs(shifts):
    """Return [(shift_a, shift_b), ...] for any two DIFFERENT staff members'
    shifts (already filtered to one station) whose time windows intersect.

    2026-07-26 (live Monsoon Inn report: Z-report showed KES 4000 cash, the
    till had KES 1700, mpesa was inflated by a similar ratio). Root cause:
    open_shift() only blocks the SAME staffer from having two open shifts —
    nothing stops a second staffer opening a shift on the same counter while
    the first one is still open (a forgotten shift-handover close, or two
    people genuinely sharing one till). _reconcile() correctly sums every
    sale in ITS OWN shift's window, but a naive sum-across-shifts (as
    bar_z_report used to do) then counts every overlapping sale twice. This
    helper detects that condition so callers can show a true, deduped total
    instead, plus explain why two shift rows show similar figures.
    """
    shifts = sorted(shifts, key=lambda s: s.started_at)
    now = timezone.now()
    pairs = []
    for i in range(len(shifts)):
        a = shifts[i]
        a_end = a.ended_at or now
        for j in range(i + 1, len(shifts)):
            b = shifts[j]
            if b.started_at >= a_end:
                break  # sorted by started_at — no further shift can overlap `a`
            b_end = b.ended_at or now
            if b.started_at < a_end and a.started_at < b_end and a.staff_id != b.staff_id:
                pairs.append((a, b))
    return pairs


def _shift_active_segments(shift, uncapped_end):
    """Return a list of (start, end) datetime intervals within
    [shift.started_at, uncapped_end] during which THIS shift is the most-
    recently-STARTED shift on its station — the set of moments its own
    reconciliation should actually count.

    2026-08-06 live report (Monsoon Inn): bartender Susan opened at float=0
    and sold KES 490 cash before waitress Sarah arrived and opened her OWN
    shift with float=490 — correctly declaring what was already in the
    till. From that moment both shifts were OPEN concurrently on the bar
    station, and the original version of this fix (a single hard cap at
    the moment a newer shift opens) handled exactly that. But the SAME
    evening surfaced the case that a single cap gets wrong: Sarah's first
    bar shift (#67) opened at 13:17:39 — 88 seconds after Susan's (#66) —
    ran until 17:11:03, and then CLOSED. Susan's own shift never closed at
    all; she stayed open the whole time. A single cap at 13:17:39 froze
    Susan's window there PERMANENTLY, silently erasing the entire
    17:11:03–18:14:43 stretch where Susan was once again the ONLY open
    shift on the counter — showing her a bogus "0h 01m / KES 0" and (since
    every dashboard revenue tile ultimately reads real Issue transactions
    the same way) contributing to the "bar revenue reset to 0" report.

    Fix: instead of one cap, walk every later same-station shift in order
    and carve its own [started_at, ended_at-or-now] span OUT of this
    shift's window — reopening this shift's own attribution the moment
    that later shift's span ends, if nothing even newer has started by
    then. This correctly produces MULTIPLE disjoint segments when a
    handover happens more than once (open → overlap → the newer shift
    closes → sole attribution resumes → another shift opens → ...),
    while still behaving exactly like the original single-cap fix for the
    simple one-handover case. Owner shifts are left fully unsegmented — an
    owner's shift isn't tied to one station the same way (see
    _reconcile()'s own "owner shift stays unscoped" exception).
    """
    try:
        staff_role = shift.staff.userprofile.role
    except Exception:
        staff_role = 'staff'
    if staff_role == 'owner':
        return [(shift.started_at, uncapped_end)]

    stn = _shift_station(shift)
    later_shifts = list(
        Shift.objects.filter(
            business=shift.business,
            started_at__gt=shift.started_at, started_at__lt=uncapped_end,
        )
        .exclude(id=shift.id)
        .filter(_station_q(stn == 'kitchen'))
        # 2026-08-08 live report (Roy) — "waitress shift opening when the
        # bartender was already there is resetting the bar revenue...
        # revenue should reset during a bar-to-bar STAFF shift change, the
        # waitress should not have an effect." A waitress with cross-
        # station access joining an already-open counter is a concurrent
        # HELPER sharing the same till, never a replacement/handover the
        # way one bartender relieving another is — so her shift-open must
        # never cap anyone else's already-open attribution on that
        # station. Only a genuine counter handover (any non-waitress role)
        # still caps as before.
        .exclude(staff__userprofile__role='waitress')
        .order_by('started_at')
    )
    if not later_shifts:
        segments = [(shift.started_at, uncapped_end)]
    else:
        segments = []
        cursor = shift.started_at
        for later in later_shifts:
            if later.started_at > cursor:
                segments.append((cursor, later.started_at))
            later_end = later.ended_at or uncapped_end
            if later_end > cursor:
                cursor = later_end
        if cursor < uncapped_end:
            segments.append((cursor, uncapped_end))

    # 2026-08-08 same-day follow-up — the exclusion above stops a waitress
    # from capping anyone ELSE's attribution, but a real cash-accountability
    # bug would reopen the moment her OWN segment is computed: nothing yet
    # stops her from ALSO claiming the same sales a concurrently-open real
    # till custodian (bar/kitchen staff, manager, owner) on her station is
    # independently accruing — the same physical cash would then be
    # "expected" twice over, once from each of them, at whichever of the two
    # shifts closes and gets asked to explain a variance. A waitress is a
    # concurrent HELPER, never the till custodian (same reasoning as
    # open_shift() disregarding her opening-float variance entirely) — so
    # subtract out any stretch a non-waitress shift on her own station
    # overlaps, leaving her with real accountability only for genuine gaps
    # where she is the sole person on the counter.
    if staff_role == 'waitress':
        custodian_shifts = (
            Shift.objects.filter(business=shift.business, started_at__lt=uncapped_end)
            .exclude(id=shift.id)
            .exclude(staff__userprofile__role='waitress')
            .filter(_station_q(stn == 'kitchen'))
        )
        custodian_spans = [
            (c.started_at, c.ended_at or uncapped_end) for c in custodian_shifts
        ]
        segments = _subtract_intervals(segments, custodian_spans)

    return segments


def _subtract_intervals(base_segments, remove_segments):
    """Interval set-difference: base_segments minus every interval in
    remove_segments. Used by _shift_active_segments() to strip any period a
    waitress's own segment overlaps a concurrently-open non-waitress ("real"
    till custodian) shift on the same station."""
    if not remove_segments:
        return base_segments
    result = []
    for b_start, b_end in base_segments:
        pieces = [(b_start, b_end)]
        for r_start, r_end in remove_segments:
            next_pieces = []
            for p_start, p_end in pieces:
                if r_end <= p_start or r_start >= p_end:
                    next_pieces.append((p_start, p_end))  # no overlap
                    continue
                if r_start > p_start:
                    next_pieces.append((p_start, r_start))
                if r_end < p_end:
                    next_pieces.append((r_end, p_end))
            pieces = next_pieces
        result.extend(pieces)
    return result


def _segments_q(field, segments):
    """Build a Q object matching `field` falling inside ANY of the given
    (start, end) segments — the queryset counterpart to
    _shift_active_segments(), used by _reconcile() so a shift's sales/
    petty-cash/debt-recovered figures sum across every disjoint segment
    it's actually responsible for, not just one contiguous range."""
    q = Q(pk__in=[])  # always-false base case (no segments → no matches)
    for start, end in segments:
        q |= Q(**{f'{field}__gte': start, f'{field}__lte': end})
    return q


def get_active_staff_shift(user_profile, business, manager_must_have_shift=False):
    """Return the caller's open Shift, or None.

    Owner always returns None — meaning "no gate needed, proceed".
    Staff must have their own OPEN shift regardless of business type.
    Use the return value like:
        shift = get_active_staff_shift(up, business)
        if shift is False:      # staff with no open shift
            return error(...)   # block the action
        # proceed (shift is either the Shift object or None for owner)
    Returns:
        None  — caller is owner (or manager, when manager_must_have_shift is
                False — the default); no shift gate required
        Shift — caller has an open shift; proceed
        False — caller must have their own shift and doesn't; block

    manager_must_have_shift (2026-07-26, live clarification): Roy's real
    operational model — the owner sells freely at all times with no gate;
    a manager SUPERVISES and may perform oversight/corrective actions
    (settle, void, revoke, restock, receive stock, approve requests) without
    opening a shift, but to actually SELL (create a new Issue/checkout) a
    manager must open their OWN shift, exactly like ordinary staff. Pass
    True only at the handful of real "new sale" entry points (Quick Sell,
    bar/kitchen checkout, Add Transaction's Issue path) — every other call
    site (settlement, void, revoke, restock, breakage, variance response,
    write-off, etc.) is oversight, not a sale, and keeps the default
    manager-bypass behavior unchanged.
    """
    if getattr(user_profile, 'is_owner', False):
        return None
    if getattr(user_profile, 'is_manager', False) and not manager_must_have_shift:
        return None
    from .models import Shift
    active = Shift.objects.filter(
        business=business, status='OPEN', staff=user_profile.user
    ).first()
    return active if active else False


def _shift_station(shift):
    """'kitchen' or 'bar' — the counter this shift belongs to.

    2026-07-28 live report (Monsoon Inn till showing bar cash with zero bar
    sales that day): prefers the explicit Shift.station field, captured from
    the request path at open_shift() time — the one 100%-reliable source,
    since it's set from WHICH BOARD "Fungua Shift" was actually clicked on,
    not who the staffer nominally is. Falls back to role-based inference
    (staff.userprofile.role == 'kitchen') only for rows created before this
    field existed (blank station) — role inference is WRONG whenever a
    manager or any cross-access staffer (can_access_kitchen/can_access_bar)
    works the counter that isn't their nominal role, which is exactly the
    scenario that produced the live bug.

    An owner's own shift (if they ever open one) has no single station and
    must not be passed through here for till purposes — see
    till_expected_cash()'s docstring.
    """
    if shift.station in ('bar', 'kitchen'):
        return shift.station
    try:
        return 'kitchen' if shift.staff.userprofile.role == 'kitchen' else 'bar'
    except Exception:
        return 'bar'


def _station_q(is_kitchen):
    """Q object matching a Shift's station for DB-level filtering — the
    queryset counterpart to _shift_station() (same explicit-field-first,
    role-fallback-for-blank-rows logic), for call sites that need to filter
    many shifts at once rather than inspect one instance."""
    target = 'kitchen' if is_kitchen else 'bar'
    role_fallback = (
        Q(staff__userprofile__role='kitchen') if is_kitchen
        else ~Q(staff__userprofile__role='kitchen')
    )
    return Q(station=target) | (Q(station='') & role_fallback)


def station_revenue_window_start(business, is_kitchen, now=None):
    """Start of the "today's revenue" tile window for one station — a
    plain calendar-day boundary (local midnight), matching how a real
    till / M-Pesa portal reports daily revenue.

    2026-08-12 live request (Roy), reversing the confirm-anchored design
    documented below: "regardless of monsoon being 24hrs, the revenue
    specifically should go by day regardless of shift presence... let us
    make revenue realistic since it says today's revenue... it goes hand
    in hand with the way Safaricom's till mpesa portal usually is —
    revenue per day." The confirm-anchored version (2026-08-01/02) had
    already caused real confusion twice — a confirmed, days-old shift's
    revenue still showing on "Tonight" hours after being confirmed — and
    Roy decided a strict daily reset is the more honest, expected
    behaviour, even for a business that never closes. Deliberately does
    NOT touch till_expected_cash()/shift close-close mechanics — Roy was
    explicit those stay exactly as they are ("shifts and counter cash
    modals are very fine as they are"); only the REVENUE tiles reset to
    plain daily reporting.

    History, kept for context: this used to anchor on the station's most
    recently CONFIRMED shift (`Shift.confirmed_at`), or — before that
    (2026-07-31/08-01) — extend backward through an open/auto-closed shift
    spanning midnight so a still-open counter's sales wouldn't vanish at
    00:00. Both were real, deliberate designs Roy asked for at the time;
    superseded by this simpler daily-reset rule now that it's caused
    confusion more than once.
    """
    now = now or timezone.now()
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


def _station_reset_anchor(business, is_kitchen):
    """The most recent genuine "start fresh" signal for one station's live
    revenue tile — either a human CONFIRM (Thibitisha), or the counter
    having gone genuinely quiet long enough for the auto-close safety net
    to fire (see _SHIFT_AUTO_CLOSE_INACTIVITY_HOURS below). Returns
    (anchor_dt, 'confirmed'|'auto_closed', shift) or (None, None, None).

    2026-08-02, same-day live follow-up to the 2026-08-01 confirm-only
    rule above — Roy's own words: "the bar revenue and sales could reset
    after the manager ends shift or if the shift is autoclosed but in a
    logical sense." An auto-close under the OLD fixed clock-time grace
    could fire while genuine sales were still happening (see
    _auto_close_expired_shifts()'s docstring), so it deliberately never
    reset this tile. Now that auto-close only fires after real, sustained
    inactivity, it's just as trustworthy a "this counter's day is over"
    signal as an explicit confirm — so it's added as a second candidate
    anchor here. An ordinary manual close still AWAITING confirmation
    (auto_closed=False) is deliberately NOT a candidate — that's exactly
    the case StationRevenueWindowStartTest already locks in as must-NOT-
    reset (a human hasn't signed off on it yet, and the safety net didn't
    fire either — nothing here has actually confirmed the counter is
    quiet)."""
    from .models import Shift
    last_confirmed = (
        Shift.objects.filter(business=business, status='CONFIRMED')
        .filter(_station_q(is_kitchen))
        .exclude(confirmed_at__isnull=True)
        .order_by('-confirmed_at')
        .first()
    )
    last_auto_closed = (
        Shift.objects.filter(business=business, status='CLOSED', auto_closed=True)
        .filter(_station_q(is_kitchen))
        .exclude(ended_at__isnull=True)
        .order_by('-ended_at')
        .first()
    )
    candidates = []
    if last_confirmed:
        candidates.append(('confirmed', last_confirmed, last_confirmed.confirmed_at))
    if last_auto_closed:
        candidates.append(('auto_closed', last_auto_closed, last_auto_closed.ended_at))
    if not candidates:
        return None, None, None
    kind, shift, dt = max(candidates, key=lambda c: c[2])
    return dt, kind, shift


def _window_revenue(business, is_kitchen, start, end):
    """Cash+mpesa Issue revenue for one station, strictly within [start, end).
    Same filter shape as home()'s bar_today_revenue/kitchen_today_revenue —
    kept as one shared helper so the tile total and its own breakdown can
    never silently drift apart from each other."""
    txns = Transaction.objects.filter(
        business=business, type='Issue',
        created_at__gte=start, created_at__lt=end,
        payment_method__in=['cash', 'mpesa'],
        item__store__is_kitchen=is_kitchen,
    ).exclude(payment_method='void').exclude(invoice_no='[SVQ]').select_related('item')
    return sum(t.revenue() for t in txns)


def station_revenue_window_info(business, is_kitchen, now=None):
    """Explains *why* a station's live revenue tile on the home dashboard
    shows what it shows — the human-facing sibling to
    station_revenue_window_start(), matching the same "vipi hesabu hii
    ilipatikana?" transparency this app already gives the continuous-till
    tile (see till_expected_cash()).

    2026-08-12 — simplified alongside station_revenue_window_start()'s own
    reversion to a plain daily (local-midnight) window: the anchor is now
    always "tangu usiku wa manane wa leo" (since today's midnight), full
    stop — no more confirm/auto-close anchor-hunting. `pending_shifts` is
    kept (still genuinely useful — "which shift contributed how much
    today," checkable against Shift History) but now lists every shift
    for this station that overlaps ANY part of today's window, regardless
    of confirm status, since confirm state no longer affects the window
    boundary at all.

    History, kept for context: this used to explain a confirm/auto-close
    anchor that could span multiple days — see station_revenue_window_
    start()'s own docstring for why that was reversed.
    """
    from .models import Shift
    now = now or timezone.now()

    window_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    anchor_label = "Tangu usiku wa manane wa leo"

    shifts_qs = list(
        Shift.objects.filter(business=business)
        .filter(_station_q(is_kitchen))
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gte=window_start))
        .filter(started_at__lt=now)
        .select_related('staff')
        .order_by('started_at')
    )

    total_revenue = _window_revenue(business, is_kitchen, window_start, now)

    pending_shifts = []
    shifts_revenue_sum = 0
    for s in shifts_qs:
        s_start = max(s.started_at, window_start)
        s_end = s.ended_at or now
        s_revenue = _window_revenue(business, is_kitchen, s_start, s_end) if s_end > s_start else 0
        shifts_revenue_sum += s_revenue
        pending_shifts.append({
            'id': s.id,
            'staff_name': s.staff.get_full_name() or s.staff.username,
            'started_at': timezone.localtime(s.started_at),
            'ended_at': timezone.localtime(s.ended_at) if s.ended_at else None,
            'status': s.status,
            'revenue': s_revenue,
        })

    other_revenue = max(0, total_revenue - shifts_revenue_sum)

    return {
        'window_start': window_start,
        'anchor_label': anchor_label,
        'total_revenue': total_revenue,
        'other_revenue': other_revenue,
        'pending_shifts': pending_shifts,
    }


# ── Variance attribution ──────────────────────────────────────────────────────

def attribute_variance_shift(business, current_shift, item=None, keg_barrel=None):
    """Decide which shift is accountable for a stock/keg variance discovered right now.

    The real-world problem this solves: a stock take (or a keg SPOT weigh-in) can
    happen at any moment during ANY shift — often minutes after a new staff member
    opens up. If that discrepancy already existed before their shift started, blaming
    whoever happens to be on duty when it's DISCOVERED is unfair and gives the wrong
    person "questions to answer." The fair test: has this shift actually recorded any
    activity on this specific item/barrel since it started? If yes, the variance is
    plausibly theirs (or at least happened on their watch) — attribute to them. If
    zero activity, the variance predates their custody — walk back to the most recent
    PRIOR shift and attribute there instead.

    Args:
        business:      accounts.Business
        current_shift: the Shift open (or most recently closed) at the moment the
                        variance was discovered — None means an owner/manager acting
                        directly with no shift context, in which case there is no
                        "current shift" to compare against and this returns None.
        item:          core.Item — pass for a stock variance.
        keg_barrel:    core.KegBarrel — pass for a keg SPOT weigh-in variance.

    Returns:
        Shift instance believed responsible, or None if either there's no shift
        context to attribute to, or no prior shift can be identified (e.g. the very
        first shift of the business, or an overnight/unattended gap with nothing
        recorded before it either) — callers should treat None as "not clearly
        anyone's — say so plainly rather than guessing."
    """
    if current_shift is None:
        return None

    qs = Transaction.objects.filter(business=business, created_at__gte=current_shift.started_at)
    if item is not None:
        qs = qs.filter(item=item)
    elif keg_barrel is not None:
        qs = qs.filter(keg_barrel=keg_barrel)
    else:
        return current_shift

    if qs.exists():
        return current_shift

    return Shift.objects.filter(
        business=business, started_at__lt=current_shift.started_at,
    ).order_by('-started_at').first()


# ── Reconciliation helper ─────────────────────────────────────────────────────

def _reconcile(shift):
    """Return a dict of sales totals and cash reconciliation for a shift."""
    end = shift.ended_at or timezone.now()
    # 2026-08-06 live report (Monsoon Inn, bartender Susan + waitress Sarah) —
    # a shift's own reconciliation window is the UNION of every segment during
    # which it's the most-recently-started shift on its station, not one
    # single capped range — see _shift_active_segments()'s own docstring for
    # the full incident writeup (including the follow-up bug where a single
    # cap permanently erased a shift's attribution even after the shift that
    # capped it had itself since closed).
    segments = _shift_active_segments(shift, end)
    # invoice_no='[SVQ]' excludes stock-take variance corrections (2026-07-25 live
    # report, Monsoon Inn) — if a stock take is accepted while a shift happens to be
    # open, its corrective "cash sale" transaction would otherwise inflate this
    # shift's expected_cash for money the staffer never actually collected during
    # their shift, making an accurate physical count look like a false shortage.
    txns = Transaction.objects.filter(
        _segments_q('created_at', segments),
        business=shift.business,
        type='Issue',
    ).exclude(invoice_no='[SVQ]')
    # Scope to the correct counter so concurrent bar + kitchen shifts don't bleed.
    # 2026-07-28 — uses _shift_station(shift) (explicit field, falling back to
    # role only for pre-migration rows) rather than role alone: a manager or
    # any cross-access staffer (can_access_kitchen/can_access_bar) working the
    # OTHER counter than their nominal role broke plain role-based inference —
    # confirmed live (Monsoon Inn till showing bar cash with zero bar sales).
    # Owner → no station filter: the owner may sell on either board and we must
    #   not exclude their transactions by store type. The is_kitchen filter
    #   exists only to separate concurrent staff shifts; the owner's shift
    #   doesn't need it. Also avoids the INNER JOIN that silently drops items
    #   with store=None.
    try:
        staff_role = shift.staff.userprofile.role
    except Exception:
        staff_role = 'staff'
    _shift_stn = _shift_station(shift)

    if staff_role != 'owner':
        txns = txns.filter(item__store__is_kitchen=(_shift_stn == 'kitchen'))
    # Revenue per transaction: use sale_amount when set (keg pours, preset Quick Sell),
    # otherwise abs(qty) * selling_price (regular Quick Sell without preset).
    _rev = Case(
        When(sale_amount__isnull=False, then=F('sale_amount')),
        default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    cash_sales   = float(txns.filter(payment_method='cash'  ).aggregate(t=Sum(_rev))['t'] or 0)
    mpesa_sales  = float(txns.filter(payment_method='mpesa' ).aggregate(t=Sum(_rev))['t'] or 0)
    credit_sales = float(txns.filter(payment_method='credit').aggregate(t=Sum(_rev))['t'] or 0)
    # 2026-07-31 live report — "cash sales and mpesa ... should not include
    # confirmed unpaid bills and debts, only what was confirmed". credit_sales
    # is stock given out on a tab/deni that hasn't been collected yet — it's a
    # real stock-reduction event, not money in hand. confirmed_sales is the
    # one figure every "how much have we actually collected" surface should
    # show as its headline, with credit_sales called out separately (never
    # silently folded into a "total" a viewer would read as fully confirmed).
    # total_sales is kept for the few surfaces that intentionally show every
    # component broken out side by side (e.g. shift_history.html's stat row).
    confirmed_sales = cash_sales + mpesa_sales
    total_sales  = cash_sales + mpesa_sales + credit_sales
    offline_adj  = float(shift.offline_sales_amount or 0)

    # Approved petty cash taken out of the till during this shift's own window — this
    # money physically left the drawer for a legitimate business reason and must be
    # subtracted before comparing to the physical count, or a staffer who handed out
    # cash the owner already approved looks "short" for money they never took.
    # Folded in here (not just at the Z-report) so the FIRST number staff/owner ever
    # see — right at close_shift(), including the >KES 500 alert — is already correct;
    # previously only the Z-report (built later) subtracted it, so a false shortfall
    # alarm could already have fired by the time the report caught up.
    # 2026-07-27 — station-scoped (PettyCash.station), mirroring the txns
    # station filter immediately above (including the same "owner shift stays
    # unscoped" exception — an owner's own shift isn't tied to one counter, so
    # its reconciliation must still see petty cash from either till).
    # Previously unscoped entirely, so a kitchen petty-cash withdrawal bled
    # into a concurrently-open bar shift's reconciliation and vice versa on
    # any combo bar+kitchen business — the same double-count-across-counters
    # shape as the overlap bug this file already guards against for
    # Transaction/CustomerDebtPayment. Blank-station rows (pre-migration, or a
    # genuinely business-wide withdrawal with no clear till) are excluded from
    # both stations rather than guessed into one — see PettyCash.station's docstring.
    _petty_qs = PettyCash.objects.filter(_segments_q('created_at', segments), business=shift.business)
    if staff_role != 'owner':
        _petty_qs = _petty_qs.filter(station=_shift_stn)
    petty_total = float(_petty_qs.filter(status='approved').aggregate(t=Sum('amount'))['t'] or 0)

    # 2026-07-26 (item 1) — petty cash still awaiting the owner's decision, shown
    # separately from the already-approved total so the shift-close screen can
    # present the full chain: cash sales → petty cash (pending) → remaining, and
    # only "solidify" the remaining figure once the owner actually decides.
    petty_pending = float(_petty_qs.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0)
    petty_rejected = float(_petty_qs.filter(status='rejected').aggregate(t=Sum('amount'))['t'] or 0)

    # 2026-07-26 (item 3 fix) — debt RECOVERED (an old credit sale being paid back
    # in cash/mpesa) is a completely separate model (CustomerDebtPayment) from a
    # NEW credit sale (Transaction.payment_method='credit', already in credit_sales
    # above). Real cash/mpesa a customer hands over to clear an old debt lands in
    # the till during THIS shift and must be added to expected_cash the same way
    # any other cash/mpesa sale is — before this fix it was invisible to
    # reconciliation entirely, understating expected_cash by exactly the recovered
    # amount (a false "surplus" once counted, not a shortage).
    from .models import CustomerDebtPayment
    debt_qs = CustomerDebtPayment.objects.filter(
        _segments_q('paid_at', segments),
        business=shift.business,
    )
    if staff_role != 'owner':
        debt_qs = debt_qs.filter(source=_shift_stn)
    debt_recovered_cash  = float(debt_qs.filter(payment_method='cash' ).aggregate(t=Sum('amount_paid'))['t'] or 0)
    debt_recovered_mpesa = float(debt_qs.filter(payment_method='mpesa').aggregate(t=Sum('amount_paid'))['t'] or 0)

    # expected_cash includes any offline cash that staff declared but didn't enter in the
    # system, plus debt recovered in cash, net of approved petty cash paid out during
    # the shift. Petty cash still PENDING owner review is deliberately NOT subtracted
    # yet — that is the "remaining if approved" provisional figure below.
    expected_cash = float(shift.opening_float) + cash_sales + offline_adj + debt_recovered_cash - petty_total
    expected_cash_if_pending_approved = expected_cash - petty_pending
    variance = None
    if shift.closing_cash_counted is not None:
        variance = round(float(shift.closing_cash_counted) - expected_cash, 2)
    # Elapsed = sum of this shift's own attributed segments, not a bare
    # end-minus-start of the full range — a shift with a gap carved out by
    # a concurrently-open newer shift shouldn't show that gap as "its"
    # elapsed time either.
    elapsed_secs = int(sum((seg_end - seg_start).total_seconds() for seg_start, seg_end in segments))
    hours, rem   = divmod(elapsed_secs, 3600)
    mins         = rem // 60
    return {
        'cash_sales':    round(cash_sales, 2),
        'mpesa_sales':   round(mpesa_sales, 2),
        'credit_sales':  round(credit_sales, 2),
        'confirmed_sales': round(confirmed_sales, 2),
        'total_sales':   round(total_sales, 2),
        'petty_cash':    round(petty_total, 2),
        'petty_cash_pending':  round(petty_pending, 2),
        'petty_cash_rejected': round(petty_rejected, 2),
        'debt_recovered_cash':  round(debt_recovered_cash, 2),
        'debt_recovered_mpesa': round(debt_recovered_mpesa, 2),
        'expected_cash': round(expected_cash, 2),
        'expected_cash_if_pending_approved': round(expected_cash_if_pending_approved, 2),
        'variance':           variance,
        'elapsed':            f"{hours}h {mins:02d}m",
        'elapsed_mins':       elapsed_secs // 60,
        'offline_adj':        round(offline_adj, 2),
        'offline_sales_note': shift.offline_sales_note or '',
    }


def _ad_hoc_expense_total_for_shift(shift):
    """Sum of ad-hoc BusinessExpense entries (core/recurring_expense_views.py's
    "Matumizi ya Leo" tool) dated on any calendar day this shift's own active
    segments touch, station-scoped the same way petty cash is in _reconcile().

    2026-08-09 (Roy, explicit confirmation): a backdated ad-hoc expense must
    reconcile against Shift History AND the Z-report for the SPECIFIC DAY it
    is dated for — never the day it happens to be recorded on, and never
    till_expected_cash() (the continuous "right now" dashboard tile, which
    stays completely untouched by design — see record_ad_hoc_expense()'s own
    docstring). Deliberately NOT folded into _reconcile() itself, which is
    also used by the LIVE in-progress shift panel (active_shift_api) and the
    moment-of-close comparison (close_shift) — those keep showing exactly the
    live, un-adjusted figure staff actually compared against their physical
    count at the time; only the two named historical/summary reports
    recompute with this on top, additively, at display time.

    Uses the same "owner shift stays unscoped" and "blank-station entries
    count toward neither station" conventions _reconcile()'s own petty-cash
    query already established.
    """
    from .models import BusinessExpense
    end = shift.ended_at or timezone.now()
    segments = _shift_active_segments(shift, end)
    if not segments:
        return 0.0
    date_min = min(timezone.localtime(s).date() for s, _e in segments)
    date_max = max(timezone.localtime(e).date() for _s, e in segments)
    try:
        staff_role = shift.staff.userprofile.role
    except Exception:
        staff_role = 'staff'
    qs = BusinessExpense.objects.filter(
        business=shift.business, date__range=(date_min, date_max),
    )
    if staff_role != 'owner':
        qs = qs.filter(station=_shift_station(shift))
    return float(qs.aggregate(t=Sum('amount'))['t'] or 0)


def till_expected_cash(business, station, as_of=None):
    """Live, continuous "how much cash SHOULD be sitting in this till right
    now" for one station ('bar' or 'kitchen') — independent of shift
    boundaries.

    2026-07-27, Roy's own accountability ask: what the system says is cash
    and what staff physically counts at the counter should match REGARDLESS
    of a shift change in between, and must also correctly include any cash
    sales the OWNER makes directly without ever opening a shift (the owner
    sells freely, no gate — see get_active_staff_shift()). A per-shift
    _reconcile() alone can't answer this: it only knows about activity
    inside ONE shift's own [started_at, ended_at] window, and resets its
    frame of reference every time a new shift opens. This instead anchors on
    the last moment this station's cash was PHYSICALLY counted (a shift
    close — close_shift() always sets closing_cash_counted) and adds every
    cash movement since, purely by time + station, whether or not a shift
    happened to be open when it occurred — a gap with no shift at all is
    just as visible to this query as one covered by a shift.

    Formula:
        expected = anchor.closing_cash_counted   (0 if this station has never closed a shift)
                 + cash sales since the anchor
                 + debt recovered in cash since the anchor
                 - approved petty cash since the anchor (station-scoped — see
                   PettyCash.station; unattributed/blank-station entries are
                   deliberately excluded from BOTH stations rather than guessed)
                 - banked/removed amounts declared by any shift opened since
                   the anchor (Shift.banked_amount)

    Owner-staffed shifts are excluded as anchors and from the banked-amount
    sweep — _reconcile() already treats an owner's own shift as unscoped (it
    may span both counters at once), so it can't reliably anchor ONE
    station's till; the owner's own cash/debt/petty-cash activity is still
    counted via the plain time-window sums above, same as any other actor.

    Returns a dict — expected_cash, anchor_at (datetime or None),
    anchor_label (str or None), breakdown (dict of the components above).
    Never raises.
    """
    as_of = as_of or timezone.now()
    is_kitchen = (station == 'kitchen')

    # 2026-07-27 live report (Roy): the till appeared to "reset" to 0 at business
    # closing hours. Root cause: _auto_close_expired_shifts() force-closes an OPEN
    # shift past closing time + grace WITHOUT ever asking anyone to count the
    # drawer — it sets ended_at but deliberately leaves closing_cash_counted at
    # its default of None (nobody counted, so there is nothing real to record).
    # This anchor query originally only checked ended_at__isnull=False, so an
    # auto-closed shift qualified as an anchor and `float(None or 0)` silently
    # treated "we never counted" as "the till held exactly zero" — a real
    # physical-cash figure was never actually confirmed to be zero, it just
    # looked that way. closing_cash_counted__isnull=False restricts anchors to
    # shifts where a REAL physical count happened (every manual close_shift()
    # always sets this field, even to an explicit 0 — that IS a real data point,
    # unlike an auto-close's None). Movements during the skipped auto-closed
    # shift's own window are still correctly summed below (those queries are
    # pure time-windows, not shift-scoped) — only which shift can serve as the
    # restart point changes.
    # 2026-07-28 live report (Monsoon Inn till showing bar cash with zero bar
    # sales that day): station matching now goes through _station_q() (the
    # explicit Shift.station field, falling back to role only for
    # pre-migration blank rows) instead of role alone -- a manager's or
    # cross-access staffer's shift on the OTHER counter than their nominal
    # role was being picked up as the wrong station's anchor.
    anchor_qs = Shift.objects.filter(
        business=business, status__in=('CLOSED', 'CONFIRMED'),
        ended_at__isnull=False, ended_at__lte=as_of,
        closing_cash_counted__isnull=False,
    ).exclude(staff__userprofile__role='owner').filter(_station_q(is_kitchen)).select_related('staff')
    anchor = anchor_qs.order_by('-ended_at').first()

    # 2026-07-30 live request — "ensure the owner can confirm cash at
    # counters at any given moment": a shift CLOSE was, until now, the only
    # way to reset this running ledger — see TillCount's own docstring for
    # the full reasoning. Whichever of the two anchor sources is more
    # recent wins; a spot confirmation always supersedes an older shift
    # close, and a shift closed AFTER the last spot confirmation always
    # supersedes that confirmation in turn.
    from .models import TillCount
    count_anchor = TillCount.objects.filter(
        business=business, station=('kitchen' if is_kitchen else 'bar'),
        counted_at__lte=as_of,
    ).order_by('-counted_at').first()
    use_count_anchor = bool(count_anchor) and (not anchor or count_anchor.counted_at > anchor.ended_at)

    # 2026-07-28 live report (Monsoon Inn: Bar tile showed KES 1400 with zero
    # bar sales that day). ROOT CAUSE: when a station has NEVER had a shift
    # close with a real physical cash count, `window_start` was None, which
    # left the cash-sales/debt-recovered queries below completely unfiltered
    # by time — summing EVERY cash Issue transaction ever recorded against
    # that station since the business was created, including sales from
    # before this till-tracking feature even existed. That's not "what's
    # currently in the till" — it's an arbitrary historical total with no
    # relationship to physical cash sitting in a drawer today. Until a real
    # physical count establishes a starting point for a station, the correct
    # answer is "unknown," not a guessed number — same principle as the
    # closing-count anchor requirement itself. anchor_established=False
    # signals every caller (home dashboard tile, open-shift variance check,
    # the open-shift modal's float suggestion) to show/treat this as
    # "not yet tracked" rather than a number to trust or alert on.
    if not anchor and not count_anchor:
        return {
            'expected_cash': None,
            'anchor_established': False,
            'anchor_at': None,
            'anchor_label': None,
            'breakdown': {
                'base': 0.0, 'cash_sales': 0.0, 'debt_recovered': 0.0,
                'petty_cash': 0.0, 'banked': 0.0,
            },
        }

    if use_count_anchor:
        base = float(count_anchor.counted_amount)
        window_start = count_anchor.counted_at
        who = (
            count_anchor.counted_by.get_full_name() or count_anchor.counted_by.username
            if count_anchor.counted_by else 'Mmiliki'
        )
        anchor_label = (
            f"{who} (Thibitisho la Pesa) — "
            f"{timezone.localtime(count_anchor.counted_at).strftime('%d %b, %H:%M')}"
        )
    else:
        base = float(anchor.closing_cash_counted or 0)
        window_start = anchor.ended_at
        anchor_label = (
            f"{anchor.staff.get_full_name() or anchor.staff.username} — "
            f"{timezone.localtime(anchor.ended_at).strftime('%d %b, %H:%M')}"
        )

    _rev = Case(
        When(sale_amount__isnull=False, then=F('sale_amount')),
        default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    txns = Transaction.objects.filter(
        business=business, type='Issue', payment_method='cash',
        created_at__lte=as_of, item__store__is_kitchen=is_kitchen,
    ).exclude(invoice_no='[SVQ]')
    if window_start:
        txns = txns.filter(created_at__gt=window_start)
    cash_sales = float(txns.aggregate(t=Sum(_rev))['t'] or 0)

    from .models import CustomerDebtPayment
    debt_qs = CustomerDebtPayment.objects.filter(
        business=business, payment_method='cash',
        source=('kitchen' if is_kitchen else 'bar'), paid_at__lte=as_of,
    )
    if window_start:
        debt_qs = debt_qs.filter(paid_at__gt=window_start)
    debt_recovered = float(debt_qs.aggregate(t=Sum('amount_paid'))['t'] or 0)

    petty_qs = PettyCash.objects.filter(
        business=business, status='approved',
        station=('kitchen' if is_kitchen else 'bar'), created_at__lte=as_of,
    )
    if window_start:
        petty_qs = petty_qs.filter(created_at__gt=window_start)
    petty_approved = float(petty_qs.aggregate(t=Sum('amount'))['t'] or 0)

    banked = 0.0
    if window_start:
        banked_qs = Shift.objects.filter(
            business=business, started_at__gt=window_start, started_at__lte=as_of,
        ).exclude(staff__userprofile__role='owner').filter(_station_q(is_kitchen))
        banked = float(banked_qs.aggregate(t=Sum('banked_amount'))['t'] or 0)

    expected = base + cash_sales + debt_recovered - petty_approved - banked
    return {
        'expected_cash': round(expected, 2),
        'anchor_established': True,
        'anchor_at':     window_start,
        'anchor_label':  anchor_label,
        'breakdown': {
            'base':           round(base, 2),
            'cash_sales':     round(cash_sales, 2),
            'debt_recovered': round(debt_recovered, 2),
            'petty_cash':     round(petty_approved, 2),
            'banked':         round(banked, 2),
        },
    }


@login_required
@require_POST
def confirm_till_count(request):
    """Owner/manager confirms the cash physically at a counter RIGHT NOW —
    2026-07-30 live request. Independent of shift open/close: the owner can
    walk up to either counter at any moment, count the drawer, and reset
    what the system expects from that point forward. See TillCount's own
    docstring for why this is a SEPARATE anchor source from a shift close,
    not a replacement for it.

    Deliberately owner/manager only (same tier as every other cash-figure
    correction in this app) — unlike opening_float, which the shift's own
    staffer may self-correct while OPEN, a till count is a spot-check BY
    DESIGN meant to verify staff, not something staff verify themselves.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    if not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Ruhusa ya mmiliki/meneja pekee'}, status=403)

    station = (request.POST.get('station') or '').strip()
    if station not in ('bar', 'kitchen'):
        return JsonResponse({'ok': False, 'error': 'Counter si sahihi'}, status=400)

    raw = (request.POST.get('counted_amount') or '').strip()
    try:
        counted = Decimal(raw)
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi'}, status=400)
    if counted < 0:
        return JsonResponse({'ok': False, 'error': 'Kiasi hakiwezi kuwa hasi'}, status=400)

    note = (request.POST.get('note') or '').strip()[:300]

    # Snapshot what the system expected BEFORE this confirmation becomes the
    # new anchor — purely for this row's own audit trail.
    before = till_expected_cash(up.business, station)
    expected_amount = before['expected_cash']
    variance = (float(counted) - expected_amount) if expected_amount is not None else None

    from .models import TillCount
    count = TillCount.objects.create(
        business=up.business, station=station, counted_amount=counted,
        expected_amount=expected_amount, variance=variance,
        counted_by=request.user, note=note,
    )

    who = request.user.get_full_name() or request.user.username
    when = timezone.localtime(count.counted_at).strftime('%d %b, %H:%M')
    station_label = 'Bar' if station == 'bar' else 'Kitchen'
    if expected_amount is None:
        msg = f"💰 {who} amethibitisha pesa ya {station_label}: KES {counted:,.0f} — {when}."
    else:
        diff = float(counted) - expected_amount
        diff_txt = (
            f"sawa na ilivyotarajiwa" if abs(diff) < 1 else
            f"{'zaidi' if diff > 0 else 'pungufu'} kwa KES {abs(diff):,.0f} (mfumo ulitarajia KES {expected_amount:,.0f})"
        )
        msg = f"💰 {who} amethibitisha pesa ya {station_label}: KES {counted:,.0f} — {diff_txt} — {when}."
    if note:
        msg += f' "{note}"'

    # Notify station-scoped on-shift staff + owners/managers — everyone
    # whose accountability this fresh baseline affects.
    try:
        from .models import Notification as _Notif
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        from .views import scoped_on_shift_targets

        notify_targets = scoped_on_shift_targets(up.business, {station})
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']):
            notify_targets[op.user_id] = op
        for target in notify_targets.values():
            if target.user_id == request.user.id:
                continue
            _Notif.objects.create(
                user=target.user, title='💰 Pesa ya Counter Imethibitishwa',
                message=msg, notification_type='info', link_url='/',
            )
            phone = (target.phone or '').strip()
            if phone:
                normalized = normalize_ke_phone(phone)
                if normalized:
                    send_sms_notification_async(msg, normalized)
    except Exception:
        logger.exception('confirm_till_count: notify failed business=%s station=%s', up.business_id, station)

    return JsonResponse({
        'ok': True, 'message': msg, 'counted_amount': float(counted),
        'expected_amount': expected_amount, 'variance': variance,
    })


def _tapped_barrels_for_business(business):
    """Return list of dicts for each TAPPED barrel, with last SHIFT_CLOSE reading."""
    barrels = KegBarrel.objects.filter(
        business=business, status='TAPPED'
    ).select_related('item')
    result = []
    for barrel in barrels:
        last_close = KegWeightReading.objects.filter(
            barrel=barrel, reading_type='SHIFT_CLOSE'
        ).order_by('-recorded_at').first()
        result.append({
            'barrel_id':       barrel.id,
            'name':            barrel.item.description,
            'tare_kg':         float(barrel.tare_weight_kg),
            'last_close_kg':   float(last_close.weight_kg) if last_close else None,
            'last_close_net':  round(float(last_close.weight_kg) - float(barrel.tare_weight_kg), 2) if last_close else None,
            'last_close_by':   (last_close.recorded_by.get_full_name() or last_close.recorded_by.username) if last_close and last_close.recorded_by else None,
            'last_close_at':   timezone.localtime(last_close.recorded_at).strftime('%H:%M') if last_close else None,
        })
    return result


# ── Auto-close stale shifts ───────────────────────────────────────────────────

_SHIFT_AUTO_CLOSE_INACTIVITY_HOURS = 6   # 2026-08-06, raised back from 3:
    # Roy's live Monsoon Inn report the same evening the 3-hour value
    # shipped: a waitress's genuinely-still-active shift auto-closed with
    # "cause unknown... we did not close it," forcing a mid-shift reopen
    # with a guessed float — real friction, not the intended safety net
    # (which exists for a shift genuinely forgotten, not a quiet lull
    # during a slow stretch past closing time). 3 hours turned out too
    # aggressive for a real bar/kitchen's uneven pace. 6 hours is a
    # middle ground between the original 8 and the over-tightened 3 —
    # still meaningfully tighter than 8 now that the owner can force-close
    # a forgotten shift on behalf of staff/manager at any time (see
    # close_shift()'s _can_confirm_shift on-behalf branch), without
    # punishing an ordinary slow period. Hours of zero sales on a station,
    # past closing time, before the auto-close safety net fires —
    # replaces the old fixed post-closing-time clock grace (see
    # _auto_close_expired_shifts()'s docstring for why).


def _convert_open_tabs_to_debt_for_shift(shift, business, should_convert):
    """Auto-convert this shift's still-OPEN tabs to debt (Geuza Deni).

    Shared by close_shift() (manual close) and _auto_close_expired_shifts()
    (system safety-net close) — bar-audit finding, 2026-07-19: the auto-close
    path used to skip this entirely, meaning a shift auto-closed because staff
    forgot (precisely the scenario most likely to also have forgotten open
    tabs) left any customer tab silently OPEN forever, with no conversion, no
    notification, and no visibility anywhere — not even in the missed-tasks
    reminder, which only checks stock-take and barrel-weight readings.

    Returns (auto_converted_count, auto_converted_names, open_tabs_list) —
    open_tabs_list covers tabs left OPEN (should_convert=False) so the caller
    can surface them for manual resolution, same shape close_shift() already
    returns to the bar board.
    """
    from .models import BarTab
    from core.models import Customer

    _tab_source = _shift_station(shift)

    open_tabs = list(
        BarTab.objects.filter(business=business, status='OPEN', source=_tab_source)
        .prefetch_related('entries')
        .select_related('customer')
    )
    auto_converted = 0
    auto_converted_names = []
    for tab in open_tabs:
        if not should_convert:
            continue
        try:
            customer_name = (tab.customer_name or '').strip() or f'Tab #{tab.id}'
            phone = ''
            if tab.customer_id and tab.customer.phone:
                phone = tab.customer.phone

            cust = None
            if phone:
                cust = Customer.objects.filter(business=business, phone=phone).first()
            if cust is None:
                cust = Customer.objects.filter(
                    business=business, name__iexact=customer_name,
                ).first()
            if cust is None:
                cust = Customer.objects.create(
                    business=business, name=customer_name, phone=phone,
                    credit_approved=True,
                )

            unpaid_total = float(tab.unpaid_total())

            for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
                txn = entry.transaction
                txn.recipient = cust.name
                txn.payment_method = 'credit'
                txn.save(update_fields=['recipient', 'payment_method'])

            tab.customer = cust
            tab.status = 'SETTLED'
            tab.settled_at = timezone.now()
            tab.cash_requested_at = None
            tab.save(update_fields=['customer', 'status', 'settled_at', 'cash_requested_at'])
            from core.keg_views import _cancel_pending_transfers_for_tab, _sync_master_receipt_payment_method
            _cancel_pending_transfers_for_tab(tab)
            _sync_master_receipt_payment_method(business, tab, 'credit')
            auto_converted += 1
            auto_converted_names.append(customer_name)

            try:
                from core.credit_policy import notify_owners_of_conversion_risk
                notify_owners_of_conversion_risk(
                    business, cust, _tab_source, unpaid_total,
                )
            except Exception:
                logger.exception(
                    'shift close: credit-risk notify failed for tab %s in shift %s', tab.id, shift.id,
                )
        except Exception:
            logger.exception('shift close: auto-convert failed for tab %s in shift %s', tab.id, shift.id)

    open_tabs_list = []
    for tab in open_tabs:
        if tab.status == 'OPEN':
            opened_at_str = ''
            if tab.opened_at:
                local_dt = timezone.localtime(tab.opened_at)
                opened_at_str = local_dt.strftime('%H:%M')
            open_tabs_list.append({
                'id': tab.id,
                'customer_name': tab.customer_name or '—',
                'opened_at': opened_at_str,
            })

    return auto_converted, auto_converted_names, open_tabs_list


def _station_last_sale_at(business, is_kitchen):
    """Most recent Issue-sale timestamp anywhere on this business+station —
    the activity signal _auto_close_expired_shifts() uses (2026-08-02,
    replacing the old fixed clock-time grace below). None if the station
    has never recorded a sale."""
    return (
        Transaction.objects.filter(
            business=business, type='Issue', item__store__is_kitchen=is_kitchen,
        )
        .order_by('-created_at')
        .values_list('created_at', flat=True)
        .first()
    )


def _auto_close_expired_shifts(business):
    """Close any OPEN shift whose station has gone genuinely quiet, past
    the business's closing time.

    Returns a list of dicts describing what was auto-closed (for toast/notification).
    Does nothing when:
      - business.closing_time is not set (treat as 24-hour / unrestricted)
      - closing_time == opening_time (ambiguous — also treated as 24-hour)
    Handles overnight businesses (opening_time > closing_time, e.g. bar opens
    22:00, closes 02:00) by projecting the close onto the following calendar day.

    2026-08-02, same-day live follow-up to the 2026-08-01 "manager exempt"
    fix below (fully superseded, not just extended) — a real Monsoon Inn
    scenario: doors are sometimes shut early (before the configured closing
    time) to avoid unwanted attention, while customers stay inside and the
    manager/owner keep selling well past both that early shutdown AND the
    configured closing_time, after ordinary staff have already gone home.
    A flat "closing_time + N hours" clock grace has no way to tell that
    apart from a genuinely abandoned shift — and the previous fix's blanket
    "managers are just never auto-closed" rule swung too far the other way,
    since a manager shift that really is abandoned for days would then
    never get swept at all, and — per _convert_open_tabs_to_debt_for_shift's
    docstring — the conversion sweep is STATION-scoped, not shift-scoped, so
    exempting the manager's own shift row never actually protected a
    manager's tabs from an OTHER (non-exempt) shift's sweep on the same
    counter either.

    The real, reliable signal is ACTIVITY, not role or the clock: a shift's
    station only becomes eligible for auto-close once we're past the
    configured closing time AND no sale has been recorded on that station
    (by anyone — owner, manager, or staff; see _station_last_sale_at()) for
    _SHIFT_AUTO_CLOSE_INACTIVITY_HOURS straight. As long as someone keeps
    ringing up real sales on the counter, no shift on it ever becomes
    eligible, no matter what the clock or the business's configured hours
    say — solving the "still serving after the doors are shut" case
    directly, for every role, including manager and owner shifts (neither
    is special-cased anymore). Only once the counter has been truly silent
    for a long stretch does the safety net (auto-close + tab-to-debt sweep,
    see _convert_open_tabs_to_debt_for_shift) fire — see
    _station_reset_anchor() above for how this also now resets the live
    revenue tile once it does.
    """
    closing_time = getattr(business, 'closing_time', None)
    opening_time = getattr(business, 'opening_time', None)

    if not closing_time or closing_time == opening_time:
        return []   # 24-hour or no hours configured — never auto-close

    now_local = timezone.localtime(timezone.now())
    local_tz  = timezone.get_current_timezone()
    is_overnight = closing_time < opening_time  # e.g. opens 22:00, closes 02:00

    open_shifts = list(
        Shift.objects.filter(business=business, status='OPEN')
        .select_related('staff')
    )
    auto_closed = []

    for shift in open_shifts:
        start_local = timezone.localtime(shift.started_at)
        shift_date  = start_local.date()

        # Scheduled close: closing_time on shift_date, or next day for overnight
        close_date   = (shift_date + timedelta(days=1)) if is_overnight else shift_date
        close_naive  = datetime.combine(close_date, closing_time)
        scheduled_close = timezone.make_aware(close_naive, local_tz)

        # If the computed close is at or before shift start, the shift opened in an
        # unusual window (e.g. opened after closing time). Skip — don't auto-close.
        if scheduled_close <= shift.started_at:
            continue

        # Never even consider a shift still within business hours.
        if now_local < timezone.localtime(scheduled_close):
            continue

        is_kitchen = _shift_station(shift) == 'kitchen'
        last_sale_at = _station_last_sale_at(shift.business, is_kitchen)
        last_activity = last_sale_at or shift.started_at
        inactive_for = now_local - timezone.localtime(last_activity)

        if inactive_for < timedelta(hours=_SHIFT_AUTO_CLOSE_INACTIVITY_HOURS):
            continue  # this counter is still genuinely active — leave it alone

        staff_name = shift.staff.get_full_name() or shift.staff.username
        # The shift's own "end" is the last real thing that happened on its
        # station, not the nominal closing time — accurate for Shift History,
        # and this is exactly what _station_reset_anchor() uses to reset the
        # live revenue tile once auto-close fires.
        effective_end = last_activity if last_activity > shift.started_at else scheduled_close
        note = (
            f'[Auto-closed — no sales on this counter for '
            f'{_SHIFT_AUTO_CLOSE_INACTIVITY_HOURS}+ hours since '
            f'{timezone.localtime(effective_end).strftime("%d %b, %H:%M")}, '
            f'past business closing time of {closing_time.strftime("%H:%M")}.]'
        )
        shift.status     = 'CLOSED'
        shift.ended_at   = effective_end
        shift.auto_closed = True
        shift.notes      = (shift.notes + '\n' + note).strip() if shift.notes else note
        shift.save(update_fields=['status', 'ended_at', 'auto_closed', 'notes'])

        # Same tab-to-debt sweep a manual close performs — the system is
        # force-closing because the counter has been genuinely silent for
        # hours, so always convert (no "manager_taking_over" concept
        # applies to an unattended auto-close).
        _converted_n, _converted_names, _ = _convert_open_tabs_to_debt_for_shift(
            shift, business, should_convert=True,
        )

        auto_closed.append({
            'shift_id':        shift.id,
            'staff_name':      staff_name,
            'scheduled_close': closing_time.strftime('%H:%M'),
            'tabs_converted':  _converted_n,
        })

    if auto_closed:
        try:
            from accounts.models import UserProfile as _UP
            from core.notifications import create_in_app_notification
            owner_profile = _UP.objects.filter(
                business=business, role='owner'
            ).select_related('user').first()
            if owner_profile:
                names   = ', '.join(r['staff_name'] for r in auto_closed)
                close_t = auto_closed[0]['scheduled_close']
                total_tabs_converted = sum(r['tabs_converted'] for r in auto_closed)
                tabs_note = (
                    f" {total_tabs_converted} tab(s) zilizoachwa wazi zimegeuzwa deni."
                    if total_tabs_converted else ''
                )
                create_in_app_notification(
                    user=owner_profile.user,
                    title='⏰ Shift Auto-Closed',
                    message=(
                        f"{names}: shift auto-closed at {close_t} "
                        f"(business hours ended). Staff may have forgotten to close shift."
                        f"{tabs_note}"
                    ),
                    notification_type='shift',
                    link_url='/bar/shift/history/',
                )
        except Exception:
            pass

    return auto_closed


def _missed_tasks_for_shift(shift, business):
    """Return a list of human-readable task descriptions that were skipped on
    an auto-closed shift. Empty list means nothing was missed.

    Checks:
      1. Stock take  — ShiftStockCount rows linked to the shift
      2. Barrel readings (SHIFT_CLOSE) — keg businesses with TAPPED barrels only
    """
    from .models import ShiftStockCount, KegWeightReading, KegBarrel
    missed = []

    if not ShiftStockCount.objects.filter(shift=shift, phase='closing').exists():
        missed.append('Hesabu ya bidhaa (stock take) haikufanywa')

    has_keg = getattr(business, 'has_keg', False)
    if has_keg:
        tapped = KegBarrel.objects.filter(business=business, status='TAPPED').exists()
        if tapped:
            end = shift.ended_at or timezone.now()
            had_reading = KegWeightReading.objects.filter(
                barrel__business=business,
                reading_type='SHIFT_CLOSE',
                recorded_at__gte=shift.started_at,
                recorded_at__lte=end,
            ).exists()
            if not had_reading:
                missed.append('Uzito wa pipa (barrel scale reading) haukupimwa')

    return missed


# ── Active shift API (for bar board polling) ──────────────────────────────────

@login_required
def active_shift_api(request):
    """JSON: current user's own shift + all active shifts for the business.

    d.shift      — the calling user's shift (for bar board panel; null if not open).
    d.all_shifts — every OPEN/CLOSED shift across all staff (for owner dashboard).
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'shift': None, 'all_shifts': []})

    # Sweep before building the response so stale shifts are already CLOSED
    # when we query all_open and my_shift below.
    auto_closed = _auto_close_expired_shifts(up.business)

    def _section(s):
        return _shift_station(s)

    def _covers_both(s):
        try:
            sup = s.staff.userprofile
            if sup.role == 'kitchen':
                return getattr(sup, 'can_access_bar', False)
            return getattr(sup, 'can_access_kitchen', False)
        except Exception:
            return False

    # 2026-07-27 — continuous till figure per station, for both the opening-shift
    # suggestion (replaces the old my-own-shift-scoped last_closing — see
    # till_expected_cash()'s docstring for why that was wrong for anyone opening
    # after a DIFFERENT staff member's shift) and the owner's home-dashboard
    # "expected cash right now" tiles, which need both stations regardless of
    # which board this request came from.
    till_by_station = {'bar': till_expected_cash(up.business, 'bar')}
    if getattr(up.business, 'has_kitchen', False):
        till_by_station['kitchen'] = till_expected_cash(up.business, 'kitchen')

    # All open/closing shifts for the business (for dashboard)
    all_open = list(
        Shift.objects.filter(
            business=up.business,
            status__in=('OPEN', 'CLOSED'),
        ).order_by('started_at').select_related('staff__userprofile')
    )
    all_shifts_data = []
    for s in all_open:
        rec = _reconcile(s)
        try:
            _staff_role = s.staff.userprofile.role
        except Exception:
            _staff_role = ''
        all_shifts_data.append({
            'id':          s.id,
            'staff_id':    s.staff_id,
            'staff_name':  s.staff.get_full_name() or s.staff.username,
            'staff_role':  _staff_role,
            'section':     _section(s),
            'covers_both': _covers_both(s),
            'status':      s.status,
            'started_at':  timezone.localtime(s.started_at).strftime('%H:%M'),
            'elapsed':     rec['elapsed'],
            'cash_sales':  rec['cash_sales'],
            'mpesa_sales': rec['mpesa_sales'],
            'total_sales': rec['total_sales'],
            'confirmed_sales': rec['confirmed_sales'],
            'credit_sales': rec['credit_sales'],
            'debt_recovered_cash':  rec['debt_recovered_cash'],
            'debt_recovered_mpesa': rec['debt_recovered_mpesa'],
        })

    # MY shift — for the bar board's own shift panel
    my_shift = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
        staff=request.user,
    ).order_by('-started_at').first()

    if not my_shift:
        # Owner with no own shift: surface the most recently started OPEN staff shift
        # for the relevant counter so the bar/kitchen board panel shows live shift info
        # instead of "Hakuna shift iliyofunguliwa". is_mine=False keeps the Close button
        # hidden — the owner is not the one closing another staff's shift from here.
        if up.role == 'owner':
            _target_section = 'kitchen' if request.path.startswith('/kitchen/') else 'bar'
            # all_open is oldest-first; reversed() gives most-recently-started first.
            proxy = next(
                (s for s in reversed(all_open) if s.status == 'OPEN' and _section(s) == _target_section),
                None,
            )
            if proxy:
                proxy_rec = _reconcile(proxy)
                return JsonResponse({
                    'shift': {
                        'id':            proxy.id,
                        'status':        proxy.status,
                        'staff_name':    proxy.staff.get_full_name() or proxy.staff.username,
                        'started_at':    timezone.localtime(proxy.started_at).strftime('%H:%M'),
                        'opening_float': float(proxy.opening_float),
                        'cash_sales':    proxy_rec['cash_sales'],
                        'mpesa_sales':   proxy_rec['mpesa_sales'],
                        'credit_sales':  proxy_rec['credit_sales'],
                        'confirmed_sales': proxy_rec['confirmed_sales'],
                        'total_sales':   proxy_rec['total_sales'],
                        'expected_cash': proxy_rec['expected_cash'],
                        'expected_cash_if_pending_approved': proxy_rec['expected_cash_if_pending_approved'],
                        'petty_cash':    proxy_rec['petty_cash'],
                        'petty_cash_pending':  proxy_rec['petty_cash_pending'],
                        'petty_cash_rejected': proxy_rec['petty_cash_rejected'],
                        'debt_recovered_cash':  proxy_rec['debt_recovered_cash'],
                        'debt_recovered_mpesa': proxy_rec['debt_recovered_mpesa'],
                        'variance':      proxy_rec['variance'],
                        'elapsed':       proxy_rec['elapsed'],
                        'is_mine':       False,
                    },
                    'can_open': True,
                    'all_shifts': all_shifts_data,
                    'till_by_station': till_by_station,
                    'auto_closed': len(auto_closed),
                })

        # Float suggestion: 2026-07-27 — station-wide live till figure (was
        # previously scoped to "the previous CONFIRMED shift opened by THIS
        # SAME staff member", which meant a different staffer opening after
        # someone else closed got no suggestion at all, and never accounted
        # for owner sales made in the gap with no shift open — see
        # till_expected_cash()'s docstring). last_closing kept (same key,
        # same meaning to the frontend: "what should I count towards") for
        # the my_station this staffer is about to open on.
        #
        # 2026-07-28 — path-based (this endpoint has two distinct URLs,
        # /bar/shift/active/ and /kitchen/shift/active/, same pattern as
        # _target_section above), not role-based: a manager or cross-access
        # staffer opening a shift on the OTHER counter than their nominal
        # role would otherwise be suggested the wrong station's float.
        my_open_station = 'kitchen' if request.path.startswith('/kitchen/') else 'bar'
        last_closing = till_by_station.get(my_open_station, till_by_station['bar'])['expected_cash']

        # Missed-tasks reminder: check the most recent CLOSED auto-closed shift
        # for this staff member. Remind until the shift is CONFIRMED.
        prev_auto = Shift.objects.filter(
            business=up.business,
            staff=request.user,
            status='CLOSED',
            auto_closed=True,
        ).order_by('-ended_at').first()
        missed_tasks = _missed_tasks_for_shift(prev_auto, up.business) if prev_auto else []

        return JsonResponse({
            'shift': None,
            'can_open': True,
            'last_closing': last_closing,
            'all_shifts': all_shifts_data,
            'till_by_station': till_by_station,
            'auto_closed': len(auto_closed),
            'missed_tasks': missed_tasks,
        })

    rec = _reconcile(my_shift)
    return JsonResponse({
        'shift': {
            'id':             my_shift.id,
            'status':         my_shift.status,
            'staff_name':     my_shift.staff.get_full_name() or my_shift.staff.username,
            'started_at':     timezone.localtime(my_shift.started_at).strftime('%H:%M'),
            # 2026-08-12 — full ISO timestamp for the waitress's own live
            # wall-clock timer (see waitress_screen()'s own comment); a
            # plain HH:MM string can't seed a client-side duration ticker.
            'started_at_iso': my_shift.started_at.isoformat(),
            'opening_float':  float(my_shift.opening_float),
            'cash_sales':     rec['cash_sales'],
            'mpesa_sales':    rec['mpesa_sales'],
            'credit_sales':   rec['credit_sales'],
            'confirmed_sales': rec['confirmed_sales'],
            'total_sales':    rec['total_sales'],
            'expected_cash':  rec['expected_cash'],
            'expected_cash_if_pending_approved': rec['expected_cash_if_pending_approved'],
            'petty_cash':     rec['petty_cash'],
            'petty_cash_pending':  rec['petty_cash_pending'],
            'petty_cash_rejected': rec['petty_cash_rejected'],
            'debt_recovered_cash':  rec['debt_recovered_cash'],
            'debt_recovered_mpesa': rec['debt_recovered_mpesa'],
            'variance':       rec['variance'],
            'elapsed':        rec['elapsed'],
            'is_mine':        True,
        },
        'can_open': False,
        'all_shifts': all_shifts_data,
        'till_by_station': till_by_station,
        'auto_closed': len(auto_closed),
    })


# ── Open shift ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def open_shift(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    # Each staff member may have one active shift at a time — concurrent shifts across
    # different staff (e.g. bar counter + kitchen counter) are explicitly allowed.
    existing = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
        staff=request.user,
    ).first()
    if existing:
        return JsonResponse({
            'ok': False,
            'error': "Una shift iliyofunguliwa tayari. Imalize kwanza.",
        }, status=400)

    try:
        opening_float = Decimal(str(request.POST.get('opening_float', '0')))
    except Exception:
        opening_float = Decimal('0')

    try:
        banked = Decimal(str(request.POST.get('banked_amount', '0') or '0'))
    except Exception:
        banked = Decimal('0')

    prev_closing_raw = request.POST.get('prev_closing', '').strip()
    notes = (request.POST.get('notes') or '').strip()

    # Build automatic audit note for the cash handover chain
    auto_note_parts = []
    if prev_closing_raw:
        try:
            prev = Decimal(str(prev_closing_raw))
            auto_note_parts.append(f"Shift iliyopita iliisha na KES {int(prev)}")
            if banked > 0:
                auto_note_parts.append(f"KES {int(banked)} iliondolewa/kubanked")
            auto_note_parts.append(f"Float ya kuanza: KES {int(opening_float)}")
        except Exception:
            pass

    full_notes = ' · '.join(auto_note_parts)
    if notes:
        full_notes = (full_notes + '\n' + notes).strip() if full_notes else notes

    # 2026-07-26 — warn (never block) when another staffer already has this exact
    # station open. Nothing stops two people sharing one till on purpose, but a
    # forgotten shift-handover close is the single most common cause of the
    # overlapping-shift Z-report double-count (see _detect_overlapping_shift_pairs
    # docstring) — surfacing it AT OPEN TIME, before any sales happen under the
    # confusion, is cheaper than untangling it after the fact.
    #
    # 2026-07-28 live report (Monsoon Inn till showing bar cash with zero bar
    # sales that day): my_station is now path-based (this endpoint has two
    # distinct URLs, /bar/shift/open/ and /kitchen/shift/open/) rather than
    # role-based — a manager or cross-access staffer opening a shift on the
    # OTHER counter than their nominal role was being mis-tagged, which
    # cascaded into every later till/reconciliation calculation for that
    # shift. Also now the shift's own explicit station of record — see
    # Shift.station and _shift_station().
    my_station = 'kitchen' if request.path.startswith('/kitchen/') else 'bar'
    overlap_warning = None
    if getattr(up, 'role', '') != 'owner':
        same_station_open = Shift.objects.filter(
            business=up.business, status='OPEN',
        ).exclude(staff=request.user).select_related('staff__userprofile')
        for other in same_station_open:
            other_station = _shift_station(other)
            if other_station == my_station:
                other_name = other.staff.get_full_name() or other.staff.username
                started = timezone.localtime(other.started_at).strftime('%H:%M')
                overlap_warning = (
                    f"⚠️ {other_name} ana shift ya {('kitchen' if my_station == 'kitchen' else 'bar')} "
                    f"iliyo wazi tangu {started}. Mkishirikiana till moja, mauzo yenu mawili "
                    f"yataonekana kwa wote wawili — hii si tatizo mradi mfahamu."
                )
                break

    # 2026-07-27 — continuous till accountability: compute what the system
    # expects this station's cash to be RIGHT NOW (before this shift exists,
    # so the anchor/window still correctly points at whatever came before —
    # the previous shift's close, or the owner selling directly in the gap
    # with no shift open at all) and compare it to what staff physically
    # counted and typed in as opening_float. Mirrors the >KES 500 threshold
    # close_shift() already uses for its own closing-cash variance alert.
    #
    # banked_amount is subtracted from the raw till figure before comparing —
    # it's a real, legitimate reduction staff/owner is declaring RIGHT NOW
    # (cash physically removed before this count), which till_expected_cash()
    # itself has no way to know about yet since this shift doesn't exist
    # until the create() below. Without this, every single banked-cash open
    # would falsely read as a shortage equal to the banked amount.
    # 2026-07-28 — if this station has never had a real physical cash count
    # (till['anchor_established'] False), there is nothing trustworthy to
    # compare opening_float against yet — see till_expected_cash()'s
    # docstring. Leave both fields None (already nullable) and skip the
    # variance alert entirely rather than comparing against a guessed
    # number; THIS shift's own eventual close (with a real counted amount)
    # becomes the first anchor for every future computation.
    # 2026-08-08 live report (Roy) — a waitress joining an already-open
    # counter is counting cash that's ALREADY mid-session, including sales
    # the counter staff made but haven't posted yet; comparing that count
    # against till_expected_cash() (built for someone opening an
    # independent till) produces a confusing, meaningless "variance" that
    # isn't really hers to explain. Disregarded entirely for a waitress —
    # her opening_float is still recorded (for her own record), but never
    # compared or flagged. Same reasoning as the till['anchor_established']
    # False case just above: nothing trustworthy to compare against.
    till = till_expected_cash(up.business, my_station)
    if till['anchor_established'] and getattr(up, 'role', '') != 'waitress':
        expected_opening_cash = Decimal(str(till['expected_cash'])) - banked
        opening_variance = opening_float - expected_opening_cash
    else:
        expected_opening_cash = None
        opening_variance = None

    shift = Shift.objects.create(
        business=up.business,
        store=up.business.stores.first() if up.business.stores.exists() else None,
        staff=request.user,
        station=my_station,
        opening_float=opening_float,
        banked_amount=banked,
        expected_opening_cash=expected_opening_cash,
        opening_variance=opening_variance,
        notes=full_notes,
    )

    # Notify owner when a non-owner opens a shift
    if not up.is_owner:
        staff_name = request.user.get_full_name() or request.user.username
        float_str = f"KES {int(opening_float)}"
        try:
            from .models import Notification
            from django.contrib.auth import get_user_model
            User = get_user_model()
            owners = User.objects.filter(
                userprofile__business=up.business,
                userprofile__role='owner',
            )
            for owner in owners:
                Notification.objects.create(
                    user=owner,
                    title=f'🟢 Shift Imefunguliwa — {staff_name}',
                    message=f"{staff_name} amefungua shift na float ya {float_str}.",
                    notification_type='staff',
                    link_url='/bar/shift/history/',
                )
        except Exception:
            pass

    # Opening cash variance alert — fires to owner/manager when what staff
    # physically counted differs materially from the till's live-computed
    # expected figure (same >KES 500 threshold and delivery shape as
    # close_shift()'s closing-cash variance alert, just at the other end of
    # the shift). opening_variance is None when this station has no
    # established anchor yet — nothing to compare against, so no alert.
    if opening_variance is not None and abs(opening_variance) > 500:
        try:
            from .notifications import normalize_ke_phone as _nkp, send_sms_notification_async as _ssms
            from .models import Notification as _Notif
            from accounts.models import UserProfile as _UP
            _staff_name = request.user.get_full_name() or request.user.username
            _direction  = 'pungufu' if opening_variance < 0 else 'zaidi'
            _alert_msg  = (
                f"⚠️ Tofauti ya fedha ufunguzi wa shift — {_staff_name} ({my_station}): "
                f"alihesabu KES {int(opening_float)}, mfumo ulitarajia KES "
                f"{int(expected_opening_cash)} ({_direction} kwa KES {abs(int(opening_variance))}). "
                f"Angalia Shift History kwa maelezo zaidi."
            )
            for _op in _UP.objects.filter(
                business=up.business, role__in=['owner', 'manager']
            ).select_related('user'):
                _Notif.objects.create(
                    user=_op.user,
                    title='⚠️ Tofauti ya Fedha — Ufunguzi',
                    message=_alert_msg,
                    notification_type='warning',
                    link_url='/bar/shift/history/',
                )
                if _op.phone:
                    try:
                        _ssms(_alert_msg, _nkp(_op.phone))
                    except Exception:
                        pass
        except Exception:
            logger.exception('open_shift: opening cash variance alert failed for shift %s', shift.id)

    # Kitchen staff have no kegs — skip barrel confirm step entirely
    if getattr(up, 'role', '') == 'kitchen':
        tapped = []
    else:
        tapped = _tapped_barrels_for_business(up.business)

    return JsonResponse({
        'ok': True,
        'shift_id': shift.id,
        'staff_name': request.user.get_full_name() or request.user.username,
        'opening_float': float(opening_float),
        'expected_opening_cash': float(expected_opening_cash) if expected_opening_cash is not None else None,
        'opening_variance': float(opening_variance) if opening_variance is not None else None,
        'started_at': timezone.localtime(shift.started_at).strftime('%H:%M'),
        'tapped_barrels': tapped,
        'overlap_warning': overlap_warning,
    })


# ── Close shift ───────────────────────────────────────────────────────────────

@login_required
@require_POST
def close_shift(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if shift.status != 'OPEN':
        return JsonResponse({'ok': False, 'error': 'Shift si OPEN'}, status=400)

    # 2026-08-02 live request (Roy): "give the owner the ability to close
    # shift on behalf of the manager or staff in shift at any point in
    # time" — for exactly the scenario where business hours have passed,
    # real sales were made, but the staffer forgot to close on the app
    # before leaving, and the owner doesn't want to wait for the auto-close
    # inactivity sweep. Reuses the SAME permission tier already established
    # for confirm_shift() — owner always, a manager only with the
    # can_confirm_shifts delegation toggle and never for another manager's
    # shift — rather than inventing a second, parallel manager-delegation
    # concept for what is functionally the same kind of "act on someone
    # else's shift" authority. This also closes a real pre-existing gap:
    # before this check, close_shift() had NO authorization tying the actor
    # to shift.staff at all — any staff member of the business who knew or
    # guessed another shift's id could already close it silently.
    _on_behalf = shift.staff_id != request.user.id
    if _on_behalf and not _can_confirm_shift(up, shift):
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kufunga shift hii.'}, status=403)

    # Blank/omitted closing_cash_counted means UNCOUNTED (None), not a
    # physically-verified zero — same semantics _auto_close_expired_shifts()
    # already uses, and required so till_expected_cash()'s anchor (which
    # only trusts a real count) never silently treats "the owner didn't
    # know the count" as "the drawer genuinely held nothing." The normal
    # self-close modal already blocks submitting a blank value client-side,
    # so this changes nothing for that path — it only matters for the new
    # close-on-behalf-of flow, where the actor often has no physical count.
    _cash_raw = (request.POST.get('closing_cash_counted') or '').strip()
    if _cash_raw:
        try:
            closing_cash = Decimal(_cash_raw)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Nambari si sahihi'}, status=400)
    else:
        closing_cash = None

    notes_add = (request.POST.get('notes') or '').strip()
    try:
        offline_amt = Decimal(str(request.POST.get('offline_sales_amount', '0') or '0'))
    except Exception:
        offline_amt = Decimal('0')
    offline_note = (request.POST.get('offline_sales_note') or '').strip()
    force_reason = (request.POST.get('force_close_reason') or '').strip()[:300]

    shift.closing_cash_counted  = closing_cash
    shift.ended_at              = timezone.now()
    shift.status                = 'CLOSED'
    shift.offline_sales_amount  = offline_amt
    shift.offline_sales_note    = offline_note
    shift.closed_by             = request.user
    if _on_behalf:
        shift.force_close_reason = force_reason
    if notes_add:
        shift.notes = (shift.notes + '\n' + notes_add).strip()
    shift.save(update_fields=[
        'closing_cash_counted', 'ended_at', 'status', 'notes',
        'offline_sales_amount', 'offline_sales_note',
        'closed_by', 'force_close_reason',
    ])

    # Tell the shift's own staff their shift was closed on their behalf —
    # same wording/accountability standard used everywhere else in this
    # app (who acted, when, why) rather than a silent status change they'd
    # only discover later on Shift History.
    if _on_behalf:
        try:
            from .notifications import normalize_ke_phone as _nkp2, send_sms_notification_async as _ssms2
            from .models import Notification as _Notif2
            _closer_name = request.user.get_full_name() or request.user.username
            _when = timezone.localtime(shift.ended_at).strftime('%d %b, %H:%M')
            _msg = f"🔒 Shift yako ilifungwa na {_closer_name} saa {_when}"
            if force_reason:
                _msg += f" — sababu: {force_reason}"
            _Notif2.objects.create(
                user=shift.staff, title='🔒 Shift Yako Imefungwa',
                message=_msg, notification_type='warning',
                link_url='/bar/shift/history/',
            )
            _staff_up = getattr(shift.staff, 'userprofile', None)
            if _staff_up and _staff_up.phone:
                try:
                    _ssms2(_msg, _nkp2(_staff_up.phone))
                except Exception:
                    pass
        except Exception:
            logger.exception('close_shift: on-behalf notify failed for shift %s', shift.id)

    # Process barrel weights (SHIFT_CLOSE readings)
    barrel_weights_raw = request.POST.get('barrel_weights', '[]')
    try:
        barrel_weights_list = json.loads(barrel_weights_raw)
    except Exception:
        barrel_weights_list = []

    weight_readings = []
    for entry in barrel_weights_list:
        try:
            bid  = int(entry.get('barrel_id', 0))
            wkg  = Decimal(str(entry.get('weight_kg', '0') or '0'))
            if wkg <= 0:
                continue
            barrel = KegBarrel.objects.filter(
                id=bid, business=up.business, status='TAPPED'
            ).select_related('item').first()
            if not barrel:
                continue
            KegWeightReading.objects.create(
                barrel=barrel,
                shift=shift,
                weight_kg=wkg,
                reading_type='SHIFT_CLOSE',
                recorded_by=request.user,
                note='Mwisho wa shift',
            )
            # F2: check variance on SHIFT_CLOSE — no volume threshold, fire if danger
            if up.business.keg_alerts_enabled:
                try:
                    from . import keg_metrics
                    bv = keg_metrics.barrel_variance(barrel)
                    if bv.wastage_pct is not None:
                        tol = float(up.business.keg_variance_tolerance_pct)
                        if keg_metrics.variance_flag(bv.wastage_pct, tol) == 'danger':
                            from .keg_views import _fire_keg_alert
                            _fire_keg_alert(
                                up.business,
                                barrel.item.description,
                                request.user.get_full_name() or request.user.username,
                                bv.wastage_kes or 0.0,
                                bv.wastage_pct,
                                barrel_id=barrel.id,
                            )
                            # UBA M3 §5.3 — feed this into the Maduka Yangu
                            # exception feed too. _fire_keg_alert() above stays
                            # untouched (per-user Notification/SMS delivery);
                            # this is the additive, durable feed row.
                            try:
                                _be_store = Store.objects.filter(
                                    business=up.business, is_kitchen=(_shift_station(shift) == 'kitchen')
                                ).first()
                                BusinessException.raise_exception(
                                    business=up.business, kind='shrinkage', severity='danger',
                                    title=f"Upungufu wa {barrel.item.description}",
                                    detail=(
                                        f"{request.user.get_full_name() or request.user.username}: "
                                        f"KES {bv.wastage_kes or 0:,.0f} ({bv.wastage_pct:.1f}%)"
                                    ),
                                    store=_be_store, shift=shift,
                                    staff=getattr(request.user, 'userprofile', None),
                                    amount_kes=bv.wastage_kes, link_url='/bar/reconciliation/',
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
            net_kg = round(float(wkg) - float(barrel.tare_weight_kg), 2)
            weight_readings.append({
                'barrel_id': barrel.id,
                'name':      barrel.item.description,
                'weight_kg': float(wkg),
                'net_kg':    net_kg,
                'tare_kg':   float(barrel.tare_weight_kg),
            })
        except Exception:
            continue

    rec = _reconcile(shift)

    # Cash variance alert — fire to owner when discrepancy > KES 500
    _variance = rec.get('variance')
    if _variance is not None and abs(_variance) > 500:
        try:
            from .notifications import normalize_ke_phone as _nkp, send_sms_notification_async as _ssms
            from .models import Notification as _Notif
            from accounts.models import UserProfile as _UP
            _staff_name = shift.staff.get_full_name() or shift.staff.username
            _direction  = 'upungufu' if _variance < 0 else 'ziada'
            _alert_msg  = (
                f"⚠️ Tofauti ya fedha mwisho wa shift — {_staff_name}: "
                f"KES {abs(_variance):,.0f} ({_direction}). "
                f"Angalia Z-Report kwa maelezo zaidi."
            )
            for _op in _UP.objects.filter(
                business=up.business, role__in=['owner', 'manager']
            ).select_related('user'):
                _Notif.objects.create(
                    user=_op.user,
                    title='⚠️ Tofauti ya Fedha',
                    message=_alert_msg,
                    notification_type='warning',
                    link_url='/bar/shift/history/',
                )
                if _op.phone:
                    try:
                        _ssms(_alert_msg, _nkp(_op.phone))
                    except Exception:
                        pass
        except Exception:
            logger.exception('close_shift: cash variance alert failed for shift %s', shift.id)

        # UBA M3 §5.3 — additive feed row for Maduka Yangu, alongside (not
        # instead of) the Notification/SMS fired just above.
        try:
            _be_store = Store.objects.filter(
                business=up.business, is_kitchen=(_shift_station(shift) == 'kitchen')
            ).first()
            BusinessException.raise_exception(
                business=up.business, kind='cash_variance',
                severity='danger' if abs(_variance) > 2000 else 'warn',
                title=f"Tofauti ya Fedha — {shift.staff.get_full_name() or shift.staff.username}",
                detail=(
                    f"KES {abs(_variance):,.0f} ({'upungufu' if _variance < 0 else 'ziada'}) "
                    f"mwisho wa shift."
                ),
                store=_be_store, shift=shift,
                staff=getattr(shift.staff, 'userprofile', None),
                amount_kes=abs(_variance), link_url='/bar/shift/history/',
            )
        except Exception:
            pass

    # Auto-convert open tabs to debt at shift close — but ONLY when the business
    # is past its closing time (or operates 24/7 with no closing_time set).
    # A staff member who closes shift early (e.g. for a break) while the bar is
    # still within operating hours should NOT have their customers' tabs wiped.
    # 24/7 bars (no closing_time): always convert — shift changes ARE the end-of-service.
    #
    # manager_taking_over: staff can tick this when a manager or owner is staying
    # on to serve remaining customers. Suppresses auto-convert so open tabs stay
    # alive for the manager to settle directly from the bar board.
    _biz = up.business
    _has_closing_time = bool(getattr(_biz, 'closing_time', None))
    _manager_taking_over = request.POST.get('manager_taking_over') == '1'
    _should_convert = (not _has_closing_time or not _biz.is_open()) and not _manager_taking_over

    # Single source of truth shared with _auto_close_expired_shifts() — see that
    # function's call site for why this must never be a copy again.
    auto_converted, auto_converted_names, open_tabs_list = (
        _convert_open_tabs_to_debt_for_shift(shift, up.business, _should_convert)
    )

    return JsonResponse({
        'ok': True,
        'expected_cash':        rec['expected_cash'],
        'variance':             rec['variance'],
        'total_sales':          rec['total_sales'],
        'confirmed_sales':      rec['confirmed_sales'],
        'cash_sales':           rec['cash_sales'],
        'mpesa_sales':          rec['mpesa_sales'],
        'credit_sales':         rec['credit_sales'],
        # 2026-07-25 live report (Monsoon Inn): _reconcile() has correctly netted
        # approved petty cash out of expected_cash since the previous session's
        # fix, but the amount itself was never sent to the frontend — staff saw
        # only the final number with no way to see it already accounts for petty
        # cash, undermining trust that "cash sales make sense exactly." Now shown
        # alongside the offline-sales note in the close-shift result panel.
        'petty_cash':           rec['petty_cash'],
        'petty_cash_pending':   rec['petty_cash_pending'],
        'petty_cash_rejected':  rec['petty_cash_rejected'],
        'expected_cash_if_pending_approved': rec['expected_cash_if_pending_approved'],
        'debt_recovered_cash':  rec['debt_recovered_cash'],
        'debt_recovered_mpesa': rec['debt_recovered_mpesa'],
        'offline_sales_amount': float(offline_amt),
        'offline_sales_note':   offline_note,
        'weight_readings':      weight_readings,
        'open_tabs':            open_tabs_list,
        'open_tabs_count':      len(open_tabs_list),
        'auto_converted':       auto_converted,
        'auto_converted_names': auto_converted_names,
        'manager_taking_over':  _manager_taking_over,
        'closed_on_behalf':     _on_behalf,
        'staff_name':           shift.staff.get_full_name() or shift.staff.username,
    })


# ── Cash variance accountability (staff explains, owner reviews) ──────────────

@login_required
@require_POST
def add_shift_variance_note(request, shift_id):
    """Staff's own explanation for a cash variance, captured right after they see
    the number at close — never required (skip is always an option, matching this
    app's reason-chips contract everywhere else). Only the shift's own staff member
    (or owner/manager) may add it. Folds an optional M-Pesa reference in separately
    so 'I sent it to M-Pesa' has a checkable paper trail, not just a claim.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)
    if not up.is_owner_or_manager and shift.staff_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'Si zamu yako'}, status=403)

    note = (request.POST.get('variance_note') or '').strip()[:300]
    mpesa_ref = (request.POST.get('variance_mpesa_ref') or '').strip()[:40]
    shift.variance_note = note
    shift.variance_mpesa_ref = mpesa_ref
    shift.save(update_fields=['variance_note', 'variance_mpesa_ref'])

    if note:
        rec = _reconcile(shift)
        var = rec['variance']
        staff_name = shift.staff.get_full_name() or shift.staff.username
        var_txt = f"KES {abs(var):,.0f} ({'upungufu' if (var or 0) < 0 else 'ziada'})" if var is not None else '—'
        msg = f"{staff_name} ameeleza tofauti ya fedha ({var_txt}): \"{note}\""
        if mpesa_ref:
            msg += f" — M-Pesa ref: {mpesa_ref}"
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
            Notification.objects.create(
                user=op.user, title='💬 Maelezo ya Tofauti ya Fedha', message=msg,
                notification_type='info', link_url='/bar/shift/history/',
            )
            if op.phone:
                try:
                    send_sms_notification_async(msg, normalize_ke_phone(op.phone))
                except Exception:
                    pass

    return JsonResponse({'ok': True, 'message': 'Maelezo yamehifadhiwa.' if note else 'Sawa.'})


@login_required
@require_POST
def review_shift_variance(request, shift_id):
    """Owner/manager's side of the same conversation — acknowledge the staff's
    explanation (or the variance itself, if no note was given) or flag it for
    follow-up. Mirrors review_petty_cash()/review_variance()'s pending→reviewed
    lifecycle, and — like both of those — notifies the staff member of the
    decision, naming who decided and when, per this app's wording/accountability
    standard. Re-reviewable any number of times, same as petty cash's undo.
    """
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)
    status = request.POST.get('status', '')
    if status not in ('acknowledged', 'flagged'):
        return JsonResponse({'ok': False, 'error': 'Hali si sahihi'}, status=400)

    note = (request.POST.get('review_note') or '').strip()[:300]
    is_reversal = bool(shift.variance_reviewed_at) and shift.variance_review_status != status

    shift.variance_review_status = status
    shift.variance_review_note = note
    shift.variance_reviewed_by = request.user
    shift.variance_reviewed_at = timezone.now()
    shift.save(update_fields=[
        'variance_review_status', 'variance_review_note',
        'variance_reviewed_by', 'variance_reviewed_at',
    ])

    reviewer_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(shift.variance_reviewed_at).strftime('%d %b, %H:%M')
    verb = 'Imethibitishwa' if status == 'acknowledged' else 'Imewekwa alama kwa ufuatiliaji'
    prefix = 'MAREKEBISHO: ' if is_reversal else ''
    msg = f"{prefix}{verb} na {reviewer_name} — {when}."
    if note:
        msg += f" \"{note}\""

    try:
        from .models import Notification
        title = '✓ Tofauti ya Fedha Imethibitishwa' if status == 'acknowledged' else '🔍 Tofauti ya Fedha — Ufuatiliaji'
        Notification.objects.create(
            user=shift.staff, title=title, message=msg, notification_type='info',
            link_url='/bar/shift/history/',
        )
        from accounts.models import UserProfile as _UP
        staff_profile = _UP.objects.filter(user=shift.staff, business=up.business).first()
        if staff_profile and staff_profile.phone:
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
            send_sms_notification_async(msg, normalize_ke_phone(staff_profile.phone))
    except Exception:
        logger.exception('review_shift_variance: notify failed for shift %s', shift.id)

    return JsonResponse({'ok': True, 'message': msg, 'is_reversal': is_reversal})


# ── Opening-variance accountability (mirrors the closing-side pair above) ─────

@login_required
@require_POST
def add_opening_variance_note(request, shift_id):
    """Staff's own explanation for an opening-cash variance — mirrors
    add_shift_variance_note() exactly, just for the other end of the shift.
    Never required; skip is always an option."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)
    if not up.is_owner_or_manager and shift.staff_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'Si zamu yako'}, status=403)

    note = (request.POST.get('variance_note') or '').strip()[:300]
    mpesa_ref = (request.POST.get('variance_mpesa_ref') or '').strip()[:40]
    shift.opening_variance_note = note
    shift.opening_variance_mpesa_ref = mpesa_ref
    shift.save(update_fields=['opening_variance_note', 'opening_variance_mpesa_ref'])

    if note:
        var = shift.opening_variance
        staff_name = shift.staff.get_full_name() or shift.staff.username
        var_txt = f"KES {abs(var):,.0f} ({'pungufu' if (var or 0) < 0 else 'zaidi'})" if var is not None else '—'
        msg = f"{staff_name} ameeleza tofauti ya fedha ya ufunguzi ({var_txt}): \"{note}\""
        if mpesa_ref:
            msg += f" — M-Pesa ref: {mpesa_ref}"
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
            Notification.objects.create(
                user=op.user, title='💬 Maelezo ya Tofauti — Ufunguzi', message=msg,
                notification_type='info', link_url='/bar/shift/history/',
            )
            if op.phone:
                try:
                    send_sms_notification_async(msg, normalize_ke_phone(op.phone))
                except Exception:
                    pass

    return JsonResponse({'ok': True, 'message': 'Maelezo yamehifadhiwa.' if note else 'Sawa.'})


@login_required
@require_POST
def review_opening_variance(request, shift_id):
    """Owner/manager acknowledges (or flags) an opening-cash variance —
    Roy's own real scenario: staff opened with float=0 because the till's
    cash had already been taken (e.g. deposited to the owner's own M-Pesa)
    before this shift started, with nobody having declared it as
    banked_amount at open time. Acknowledging here is not just a label —
    it FOLDS the explained amount into this shift's own banked_amount, the
    same field till_expected_cash() already reads to carry the running
    balance forward, so every later computation reflects the correction
    immediately rather than waiting for this shift to eventually close.
    Re-reviewable any number of times (same undo pattern as petty cash /
    the closing-side review_shift_variance): re-acknowledging is a no-op on
    the ledger, flipping AWAY from acknowledged reverses the fold-in
    exactly, since the adjustment amount is always deterministically
    (expected_opening_cash - opening_float) — both frozen at shift open.
    """
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)
    if shift.expected_opening_cash is None:
        return JsonResponse({'ok': False, 'error': 'Hakuna kiasi kilichotarajiwa kilichorekodiwa kwa shift hii'}, status=400)

    status = request.POST.get('status', '')
    if status not in ('acknowledged', 'flagged'):
        return JsonResponse({'ok': False, 'error': 'Hali si sahihi'}, status=400)

    note = (request.POST.get('review_note') or '').strip()[:300]
    was_acknowledged = shift.opening_variance_review_status == 'acknowledged'
    is_reversal = bool(shift.opening_variance_reviewed_at) and shift.opening_variance_review_status != status

    # Positive = shortfall (counted less than expected — cash left before the
    # count); negative = surplus (counted more — cash added before the count).
    adjustment = shift.expected_opening_cash - shift.opening_float
    update_fields = [
        'opening_variance_review_status', 'opening_variance_review_note',
        'opening_variance_reviewed_by', 'opening_variance_reviewed_at',
    ]
    if status == 'acknowledged' and not was_acknowledged:
        shift.banked_amount = (shift.banked_amount or Decimal('0')) + adjustment
        update_fields.append('banked_amount')
    elif status != 'acknowledged' and was_acknowledged:
        shift.banked_amount = (shift.banked_amount or Decimal('0')) - adjustment
        update_fields.append('banked_amount')

    shift.opening_variance_review_status = status
    shift.opening_variance_review_note = note
    shift.opening_variance_reviewed_by = request.user
    shift.opening_variance_reviewed_at = timezone.now()
    shift.save(update_fields=update_fields)

    reviewer_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(shift.opening_variance_reviewed_at).strftime('%d %b, %H:%M')
    verb = 'Imethibitishwa' if status == 'acknowledged' else 'Imewekwa alama kwa ufuatiliaji'
    prefix = 'MAREKEBISHO: ' if is_reversal else ''
    msg = f"{prefix}{verb} na {reviewer_name} — {when}."
    if note:
        msg += f" \"{note}\""

    try:
        from .models import Notification
        title = '✓ Tofauti ya Ufunguzi Imethibitishwa' if status == 'acknowledged' else '🔍 Tofauti ya Ufunguzi — Ufuatiliaji'
        Notification.objects.create(
            user=shift.staff, title=title, message=msg, notification_type='info',
            link_url='/bar/shift/history/',
        )
        from accounts.models import UserProfile as _UP
        staff_profile = _UP.objects.filter(user=shift.staff, business=up.business).first()
        if staff_profile and staff_profile.phone:
            from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
            send_sms_notification_async(msg, normalize_ke_phone(staff_profile.phone))
    except Exception:
        logger.exception('review_opening_variance: notify failed for shift %s', shift.id)

    return JsonResponse({'ok': True, 'message': msg, 'is_reversal': is_reversal})


@login_required
@require_GET
def recheck_opening_variance(request, shift_id):
    """Read-only: recompute what this shift's opening-cash variance would
    look like if calculated NOW, using today's more-complete data, without
    touching the original frozen record.

    2026-08-06 live scenario (Monsoon Inn): a bartender sells but doesn't
    post the sales at the time; a waitress arrives, opens her own shift,
    and (honestly) counts more cash than the system expects because that
    unposted cash is physically sitting there — a real opening-variance
    flag with an innocent cause. Once the bartender later backdates her
    missed sales via Add Transaction's "Ilifanyika wakati mwingine?"
    toggle (setting created_at to when the sale actually happened, before
    the waitress's own shift.started_at), till_expected_cash() — a live
    query — already picks that transaction up automatically. But
    Shift.expected_opening_cash/opening_variance are ONE-TIME snapshots
    taken the instant the waitress opened, frozen before the backdated
    entry existed, so they stay stale forever unless something re-checks
    them.

    till_expected_cash(..., as_of=shift.started_at) already supports this
    exactly: every internal sum filters by created_at__lte=as_of, so a
    later-inserted but correctly-backdated transaction is included in the
    recompute even though it didn't exist yet when the original snapshot
    was taken. Deliberately does NOT overwrite shift.expected_opening_cash/
    opening_variance — those stay as the honest historical record of what
    was known at the time; this is a side-by-side comparison for a human
    (owner/manager) to decide with, same "explain, don't auto-absolve"
    principle as every other reconciliation feature in this app.
    """
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)
    if shift.expected_opening_cash is None:
        return JsonResponse({'ok': False, 'error': 'Hakuna kiasi kilichotarajiwa kilichorekodiwa kwa shift hii'}, status=400)

    station = _shift_station(shift)
    recomputed = till_expected_cash(up.business, station, as_of=shift.started_at)
    if not recomputed['anchor_established']:
        return JsonResponse({'ok': False, 'error': 'Haiwezekani kuhesabu upya — hakuna nukta ya kuanzia bado.'}, status=400)

    banked = shift.banked_amount or Decimal('0')
    recomputed_expected = Decimal(str(recomputed['expected_cash'])) - banked
    recomputed_variance = shift.opening_float - recomputed_expected

    original_variance = shift.opening_variance or Decimal('0')
    explained = original_variance - recomputed_variance  # how much of the original gap this accounts for

    return JsonResponse({
        'ok': True,
        'original_expected': float(shift.expected_opening_cash),
        'original_variance': float(original_variance),
        'recomputed_expected': float(recomputed_expected),
        'recomputed_variance': float(recomputed_variance),
        'explained_amount': float(explained),
        'fully_explained': abs(recomputed_variance) < Decimal('1'),
        'breakdown': recomputed['breakdown'],
    })


# ── Edit opening float (2026-07-30, live request) ──────────────────────────────
# Roy's exact scenario: staff opens a shift and forgets to physically count and
# type in the cash actually sitting at the counter — open_shift() then silently
# defaults opening_float to 0 (see request.POST.get('opening_float', '0')),
# which poisons this shift's own expected_cash/variance math in _reconcile()
# with a number nobody actually meant. This is a genuine data-entry mistake
# ("the float really was KES X, I just forgot to type it"), distinct from the
# opening-variance flow above ("0 is correct, the cash was already banked
# elsewhere") — that flow explains a number as legitimate; this one corrects a
# number that was simply never entered. One shared view + two URL names (like
# every other bar/kitchen-mirrored shift action in this file) covers both
# counters, since Shift itself isn't split by station beyond the `station`
# field already used everywhere else in this file.

def _can_edit_opening_float(up, shift, user_id):
    """While OPEN, the staffer who opened the shift may fix their own entry —
    no different from re-typing a number before anything downstream relied on
    it, and needs to work mid-shift without hunting down the owner (same
    reasoning as split-transfer). Once CLOSED, this becomes a retroactive
    correction touching figures other reports may already show (variance,
    Z-report) — owner/manager only from that point, matching every other
    financial-figure correction in this app. A CONFIRMED shift is treated as
    fully signed off and not editable here."""
    if not up or shift.status == 'CONFIRMED':
        return False
    if shift.status == 'OPEN':
        return getattr(up, 'is_owner_or_manager', False) or shift.staff_id == user_id
    if shift.status == 'CLOSED':
        return getattr(up, 'is_owner_or_manager', False)
    return False


@login_required
@require_POST
def edit_shift_opening_float(request, shift_id):
    """Correct a shift's opening float after the fact. Does NOT touch
    till_expected_cash()'s running ledger — that anchors on
    closing_cash_counted, never opening_float, so correcting a past shift's
    float doesn't retroactively change the continuous till figure, only this
    shift's own report. Recomputes the derived opening_variance so it stays
    consistent with the corrected float, and — since a review already done
    (opening-variance or close-side) was necessarily based on the OLD, wrong
    number — resets any such review back to unreviewed rather than leaving a
    stale "✓ Imethibitishwa" stamp sitting next to a figure that just changed;
    the reset itself is named in the audit line so nobody has to guess why a
    prior review disappeared."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    raw = (request.POST.get('opening_float') or '').strip()
    try:
        new_float = Decimal(raw)
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi'}, status=400)
    if new_float < 0:
        return JsonResponse({'ok': False, 'error': 'Kiasi hakiwezi kuwa hasi'}, status=400)

    reason = (request.POST.get('reason') or '').strip()[:200]

    from django.db import transaction as _db_txn
    with _db_txn.atomic():
        try:
            shift = Shift.objects.select_for_update().get(id=shift_id, business=up.business)
        except Shift.DoesNotExist:
            return JsonResponse({'ok': False, 'error': 'Shift haikupatikana'}, status=404)

        if not _can_edit_opening_float(up, shift, request.user.id):
            return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kubadilisha float hii'}, status=403)

        old_float = shift.opening_float
        if new_float == old_float:
            return JsonResponse({'ok': True, 'message': 'Hakuna mabadiliko.', 'unchanged': True})

        update_fields = ['opening_float']
        shift.opening_float = new_float

        reset_notes = []
        if shift.expected_opening_cash is not None:
            shift.opening_variance = new_float - shift.expected_opening_cash
            update_fields.append('opening_variance')
            if shift.opening_variance_review_status:
                reset_notes.append('ukaguzi wa tofauti ya ufunguzi')
                shift.opening_variance_review_status = ''
                shift.opening_variance_review_note = ''
                shift.opening_variance_reviewed_by = None
                shift.opening_variance_reviewed_at = None
                update_fields += [
                    'opening_variance_review_status', 'opening_variance_review_note',
                    'opening_variance_reviewed_by', 'opening_variance_reviewed_at',
                ]

        # A close-side variance review already done was based on the OLD
        # expected_cash (which depends on opening_float) — reset it too so it
        # doesn't keep showing a stamp of approval on a number that just moved.
        if shift.closing_cash_counted is not None and shift.variance_review_status:
            reset_notes.append('ukaguzi wa tofauti ya kufunga')
            shift.variance_review_status = ''
            shift.variance_review_note = ''
            shift.variance_reviewed_by = None
            shift.variance_reviewed_at = None
            update_fields += [
                'variance_review_status', 'variance_review_note',
                'variance_reviewed_by', 'variance_reviewed_at',
            ]

        editor_name = request.user.get_full_name() or request.user.username
        when = timezone.localtime(timezone.now()).strftime('%d %b, %H:%M')
        audit_line = (
            f"Float ya ufunguzi ilibadilishwa kutoka KES {old_float:,.0f} kwenda "
            f"KES {new_float:,.0f} na {editor_name} — {when}."
        )
        if reason:
            audit_line += f' Sababu: "{reason}"'
        if reset_notes:
            audit_line += f" ({', '.join(reset_notes)} imefutwa, inahitaji kuangaliwa upya)"
        shift.notes = (shift.notes + '\n' if shift.notes else '') + audit_line
        update_fields.append('notes')

        shift.save(update_fields=update_fields)

    # Notify whoever else needs to know — the shift's own staff (if someone
    # else made the correction) and owners/managers (if the staffer corrected
    # their own entry) — same recipient pattern as every other shift-cash
    # correction in this file.
    try:
        from django.contrib.auth.models import User as _User
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        recipient_ids = set()
        if shift.staff_id != request.user.id:
            recipient_ids.add(shift.staff_id)
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
            if op.user_id != request.user.id:
                recipient_ids.add(op.user_id)
        for u in _User.objects.filter(id__in=recipient_ids):
            Notification.objects.create(
                user=u, title='✏️ Float ya Ufunguzi Imesahihishwa', message=audit_line,
                notification_type='info', link_url='/bar/shift/history/',
            )
        if shift.staff_id != request.user.id:
            staff_profile = _UP.objects.filter(user=shift.staff, business=up.business).first()
            if staff_profile and staff_profile.phone:
                send_sms_notification_async(audit_line, normalize_ke_phone(staff_profile.phone))
    except Exception:
        logger.exception('edit_shift_opening_float: notify failed for shift %s', shift.id)

    return JsonResponse({'ok': True, 'message': audit_line})


# ── Confirm barrel weights (incoming staff after opening their shift) ─────────

@login_required
@require_POST
def confirm_barrel_weights(request):
    """
    Incoming staff saves SHIFT_OPEN readings right after opening their shift.
    Compares with the last SHIFT_CLOSE per barrel and returns a verdict.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    # Must have their own OPEN shift
    shift = Shift.objects.filter(
        business=up.business, status='OPEN', staff=request.user
    ).first()
    if not shift:
        return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa'}, status=400)

    barrel_weights_raw = request.POST.get('barrel_weights', '[]')
    try:
        barrel_weights_list = json.loads(barrel_weights_raw)
    except Exception:
        barrel_weights_list = []

    results = []
    for entry in barrel_weights_list:
        try:
            bid  = int(entry.get('barrel_id', 0))
            wkg  = Decimal(str(entry.get('weight_kg', '0') or '0'))
            if wkg <= 0:
                continue
            barrel = KegBarrel.objects.filter(
                id=bid, business=up.business, status='TAPPED'
            ).select_related('item').first()
            if not barrel:
                continue
            KegWeightReading.objects.create(
                barrel=barrel,
                shift=shift,
                weight_kg=wkg,
                reading_type='SHIFT_OPEN',
                recorded_by=request.user,
                note='Uthibitisho wa kufungua shift',
            )
            net_kg = round(float(wkg) - float(barrel.tare_weight_kg), 2)
            last_close = KegWeightReading.objects.filter(
                barrel=barrel, reading_type='SHIFT_CLOSE'
            ).order_by('-recorded_at').first()
            if last_close:
                diff = round(float(wkg) - float(last_close.weight_kg), 2)
                flag = 'ok' if abs(diff) <= 0.3 else ('warn' if abs(diff) <= 1.0 else 'danger')
                # F2: overnight barrel-loss alert when handover gap > 1.0 kg
                if abs(diff) > 1.0 and up.business.keg_alerts_enabled:
                    try:
                        from accounts.models import UserProfile
                        from .models import Notification
                        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
                        from django.utils import timezone as _tz
                        msg = (
                            f"⚠️ Barrel {barrel.item.description}: imepoteza"
                            f" {abs(diff):.2f} kg usiku"
                            f" (SHIFT_CLOSE→SHIFT_OPEN). Kagua."
                        )
                        now = _tz.now()
                        can_sms = (
                            not up.business.last_txn_sms_at or
                            (now - up.business.last_txn_sms_at).total_seconds() > 600
                        )
                        owners = UserProfile.objects.filter(
                            business=up.business, role__in=['owner', 'manager']
                        ).select_related('user')
                        for op in owners:
                            Notification.objects.create(
                                user=op.user, title='Keg Barrel Alert', message=msg,
                                notification_type='warning',
                                link_url=f'/bar/reconciliation/{barrel.id}/',
                            )
                            if can_sms and op.phone:
                                normalized = normalize_ke_phone(op.phone)
                                if normalized:
                                    send_sms_notification_async(msg, normalized)
                        if can_sms:
                            up.business.last_txn_sms_at = now
                            up.business.save(update_fields=['last_txn_sms_at'])
                    except Exception:
                        pass
            else:
                diff = None
                flag = 'ok'
            results.append({
                'barrel_id':     barrel.id,
                'name':          barrel.item.description,
                'weight_kg':     float(wkg),
                'net_kg':        net_kg,
                'tare_kg':       float(barrel.tare_weight_kg),
                'last_close_kg': float(last_close.weight_kg) if last_close else None,
                'diff_kg':       diff,
                'flag':          flag,
            })
        except Exception:
            continue

    return JsonResponse({'ok': True, 'readings': results})


# ── Confirm shift (incoming staff / owner) ────────────────────────────────────

def _can_confirm_shift(up, shift):
    """Owner always. A manager only with the explicit can_confirm_shifts
    toggle (2026-07-30, off by default) — and even then never for another
    manager's shift, including their own: Roy's explicit call is that a
    manager's own shift close, and any other manager's, always needs the
    owner. Staff/waitress/kitchen shifts are fair game for a toggled manager."""
    if not up:
        return False
    if up.is_owner:
        return True
    if up.role != 'manager' or not getattr(up, 'can_confirm_shifts', False):
        return False
    shift_staff_role = getattr(getattr(shift.staff, 'userprofile', None), 'role', None)
    return shift_staff_role != 'manager'


@login_required
@require_POST
def confirm_shift(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if not _can_confirm_shift(up, shift):
        return JsonResponse(
            {'ok': False, 'error': 'Huna ruhusa ya kuthibitisha zamu hii — inahitaji mmiliki.'},
            status=403,
        )

    if shift.status != 'CLOSED':
        return JsonResponse({'ok': False, 'error': 'Shift si CLOSED — haiwezi kuthibitishwa'}, status=400)

    notes_add = (request.POST.get('notes') or '').strip()
    shift.confirmed_by = request.user
    shift.confirmed_at = timezone.now()
    shift.status = 'CONFIRMED'
    if notes_add:
        shift.notes = (shift.notes + '\n✓ ' + notes_add).strip()
    shift.save(update_fields=['confirmed_by', 'confirmed_at', 'status', 'notes'])
    return JsonResponse({'ok': True})


# ── Shift history page ────────────────────────────────────────────────────────

@login_required
def shift_history(request):
    up = _get_up(request)
    if not up:
        from django.shortcuts import redirect
        return redirect('login')

    # Station scoping: bar-only staff see bar shifts; kitchen-only staff see kitchen shifts.
    # 2026-07-28 fix: this used to filter on Shift.store — which is ALWAYS set
    # to the business's first store at creation (see open_shift()), completely
    # unrelated to which counter the shift was actually for. That meant a
    # kitchen-only staffer's OWN shift history filter (store__is_kitchen=True)
    # could never match anything (their shifts all had store=the bar store),
    # and a bar-only staffer's filter matched everything, kitchen shifts
    # included. Now uses _station_q() — the same explicit-field-first,
    # role-fallback predicate the till/reconciliation code uses.
    _is_owner = getattr(up, 'is_owner_or_manager', False)
    _is_kitchen = getattr(up, 'is_kitchen_staff', False)
    _can_bar = getattr(up, 'can_access_bar', False)
    _can_kitchen = getattr(up, 'can_access_kitchen', False)

    if _is_owner:
        _show_bar, _show_kitchen = True, True
    elif _is_kitchen:
        _show_bar, _show_kitchen = _can_bar, True
    else:
        _show_bar, _show_kitchen = True, _can_kitchen

    _base_qs = Shift.objects.filter(business=up.business)
    if _show_kitchen and not _show_bar:
        _base_qs = _base_qs.filter(_station_q(True))
    elif _show_bar and not _show_kitchen:
        _base_qs = _base_qs.filter(_station_q(False))

    # Identity scoping: only owner/manager sees every staff member's shifts. A regular
    # staffer (including waitress/kitchen) only ever sees their OWN shift history — station
    # scoping above answers "which counter", this answers "whose shifts", and both apply.
    if not _is_owner:
        _base_qs = _base_qs.filter(staff=request.user)

    # 2026-08-12 live request (Roy) — a real search module: quick date presets
    # (today/week/month) or a custom range, a status filter, and (owner/
    # manager only — a non-owner/manager is already hard-scoped to
    # themselves above, so a foreign staff_id would be meaningless/a scoping
    # leak) a staff picker. Previously this view had zero filter params at
    # all, always the last 60 shifts business-wide.
    has_filter = False
    preset = request.GET.get('preset', '').strip()
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    date_from = date_to = None
    if preset == 'today':
        date_from = date_to = today
        has_filter = True
    elif preset == 'week':
        date_from = today - timezone.timedelta(days=today.weekday())
        date_to = today
        has_filter = True
    elif preset == 'month':
        date_from = today.replace(day=1)
        date_to = today
        has_filter = True
    else:
        if date_from_str:
            try:
                date_from = timezone.datetime.strptime(date_from_str, '%Y-%m-%d').date()
                has_filter = True
            except ValueError:
                date_from = None
        if date_to_str:
            try:
                date_to = timezone.datetime.strptime(date_to_str, '%Y-%m-%d').date()
                has_filter = True
            except ValueError:
                date_to = None

    if date_from:
        _base_qs = _base_qs.filter(started_at__date__gte=date_from)
    if date_to:
        _base_qs = _base_qs.filter(started_at__date__lte=date_to)

    status_filter = request.GET.get('status', '').strip()
    if status_filter in ('OPEN', 'CLOSED', 'CONFIRMED'):
        _base_qs = _base_qs.filter(status=status_filter)
        has_filter = True

    staff_id_filter = request.GET.get('staff_id', '').strip()
    if _is_owner and staff_id_filter:
        _base_qs = _base_qs.filter(staff_id=staff_id_filter)
        has_filter = True
    # A non-owner/manager passing staff_id is silently ignored — they're
    # already hard-scoped to staff=request.user above, never widened here.

    # Default view (no filter active) keeps the original 60-row cap; a real
    # search shouldn't be silently truncated at 60 rows.
    row_cap = 500 if has_filter else 60
    shifts_qs = _base_qs.select_related('staff__userprofile', 'confirmed_by', 'closed_by').order_by('-started_at')[:row_cap]

    rows = []
    for shift in shifts_qs:
        rec = _reconcile(shift)
        # 2026-08-09 (Roy, explicit confirmation): fold same-day ad-hoc
        # expenses into the historical Shift History display, additively —
        # see _ad_hoc_expense_total_for_shift()'s own docstring for why this
        # is done here (display-time recompute) rather than inside
        # _reconcile() itself (which the live in-progress panel also reads).
        expense_total = _ad_hoc_expense_total_for_shift(shift)
        rec['ad_hoc_expenses'] = round(expense_total, 2)
        if expense_total:
            rec['expected_cash_after_expenses'] = round(rec['expected_cash'] - expense_total, 2)
            rec['variance_after_expenses'] = (
                round(float(shift.closing_cash_counted) - rec['expected_cash_after_expenses'], 2)
                if shift.closing_cash_counted is not None else None
            )
        else:
            rec['expected_cash_after_expenses'] = rec['expected_cash']
            rec['variance_after_expenses'] = rec['variance']
        # Badge colour reflects the AFTER-expenses variance (equal to the
        # original variance whenever no same-day ad-hoc expense exists) —
        # matching this app's own established precedent that a correction
        # applied after the fact should be reflected live in the standing
        # historical record (see the petty-cash-review-undo entry in
        # Known Issues), not frozen at whatever it looked like at close time.
        var = rec['variance_after_expenses']
        if var is None:
            var_class = 'pending'
        elif abs(var) <= 50:
            var_class = 'ok'
        elif abs(var) <= 200:
            var_class = 'warn'
        else:
            var_class = 'danger'
        shift_staff_role = getattr(getattr(shift.staff, 'userprofile', None), 'role', None)
        rows.append({
            'shift':       shift,
            'rec':         rec,
            'var_class':   var_class,
            # 2026-07-30 — Thibitisha (Confirm Shift) is now per-row: a manager
            # granted can_confirm_shifts can confirm staff/waitress/kitchen
            # shifts, but never their own or another manager's (see
            # _can_confirm_shift's docstring). Owner is unaffected.
            'can_confirm': _can_confirm_shift(up, shift),
            # Explains an absent Confirm button to a non-owner viewer — the
            # owner's own button is never hidden, so this is only meaningful
            # to a manager looking at a CLOSED shift they can't touch.
            'needs_owner': shift.status == 'CLOSED' and shift_staff_role == 'manager' and not up.is_owner,
            # 2026-07-30 — "staff forgot to input cash at hand" fix: lets the
            # shift's own staffer correct opening_float themselves while OPEN,
            # or owner/manager any time up to CLOSED (see _can_edit_opening_float).
            'can_edit_float': _can_edit_opening_float(up, shift, request.user.id),
        })

    # Owner/manager view: group rows by staff so the search results can be
    # rendered per-person, per Roy's "grouped by staff, period and data" ask.
    # `rows` itself is left completely unchanged (a plain flat list, exactly
    # as before this sprint) — every pre-existing caller of this view reads
    # `rows` directly assuming every entry has a 'shift' key; a first attempt
    # at this feature inserted header marker dicts INTO `rows` and broke 4
    # pre-existing tests that iterate it that way. `grouped_rows` is a
    # SEPARATE, additive structure built only for the template's own
    # rendering, never replacing `rows`.
    grouped_rows = None
    if _is_owner:
        from collections import OrderedDict
        groups = OrderedDict()
        for row in rows:
            staff_name = row['shift'].staff.get_full_name() or row['shift'].staff.username
            groups.setdefault(staff_name, []).append(row)
        grouped_rows = OrderedDict(sorted(groups.items()))

    staff_choices = []
    if _is_owner:
        from accounts.models import UserProfile as _UP
        staff_choices = [
            {'id': p.user_id, 'name': p.user.get_full_name() or p.user.username}
            for p in _UP.objects.filter(business=up.business).select_related('user').order_by('user__first_name', 'user__username')
        ]

    return render(request, 'core/bar/shift_history.html', {
        'rows':     rows,
        'grouped_rows': grouped_rows,
        'staff_choices': staff_choices,
        # Owner-or-manager (matches the identity-scoping check above and this app's
        # established is_owner_or_manager convention) — a manager confirms shifts and
        # reviews cash-variance explanations the same as an owner would.
        'is_owner': _is_owner,
        # Echo filter state back so the search form stays populated.
        'filter_preset': preset,
        'filter_date_from': date_from_str,
        'filter_date_to': date_to_str,
        'filter_status': status_filter,
        'filter_staff_id': staff_id_filter,
        'has_filter': has_filter,
    })


# ── Shift stock take ──────────────────────────────────────────────────────────

@login_required
def stock_take_api(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if request.method == 'GET':
        items = (
            Item.objects
            .filter(business=up.business)
            .exclude(is_keg=True)
            .exclude(is_produce=True)
            .order_by('description')
            .prefetch_related('portion_presets')
        )
        # Station scoping: only show items from the same counter as this shift.
        # 2026-08-02 regression sweep (found while fixing the identical bug in
        # analytics_views.py's Staff Pouring League): this used to read
        # shift.store, a documented broken proxy that is ALWAYS
        # business.stores.first() regardless of which counter the shift
        # actually ran on. Fixed to the real, explicit-field-first station
        # discriminator this file itself established (_shift_station()).
        items = items.filter(store__is_kitchen=(_shift_station(shift) == 'kitchen'))
        data = []
        for item in items:
            data.append({
                'item_id':      item.id,
                'name':         item.description,
                'unit':         item.unit or '',
                'book_balance': float(item.current_balance()),
                'bottle_envelope':  item.bottle_envelope,
                'tots_per_unit':    float(item.tots_per_unit or 0),
                'expected_rev_per_unit': item.bottle_expected_revenue_per_unit(),
            })
        return JsonResponse({'ok': True, 'items': data})

    if request.method == 'POST':
        try:
            counts = json.loads(request.POST.get('counts', '[]'))
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Invalid data'}, status=400)

        # 2026-07-30 — opening-shift stock take (Roy's request: staff should be
        # able to do the same count when OPENING a shift, not just closing).
        # Defaults to 'closing' so every pre-existing caller (the close-shift
        # modal) keeps writing exactly what it always has.
        phase = request.POST.get('phase') or 'closing'
        if phase not in ('opening', 'closing'):
            phase = 'closing'

        # 2026-08-02 regression sweep: same broken shift.store proxy as the
        # GET branch above — fixed to the real _shift_station() discriminator.
        _shift_is_kitchen = (_shift_station(shift) == 'kitchen')
        results = []
        for entry in counts:
            try:
                item_id      = int(entry.get('item_id', 0))
                actual       = Decimal(str(entry.get('actual_count', '0') or '0'))
                item = Item.objects.select_related('store').filter(
                    id=item_id, business=up.business
                ).first()
                if not item:
                    continue
                # Skip items that belong to the other counter
                if item.store_id:
                    if bool(item.store.is_kitchen) != _shift_is_kitchen:
                        continue
                book = item.current_balance()
                ShiftStockCount.objects.update_or_create(
                    shift=shift, item=item, phase=phase,
                    defaults={
                        'book_balance': book,
                        'actual_count': actual,
                        'recorded_by':  request.user,
                    }
                )
                variance = float(actual) - float(book)
                result_row = {
                    'name':       item.description,
                    'unit':       item.unit or '',
                    'book':       float(book),
                    'actual':     float(actual),
                    'variance':   round(variance, 2),
                }
                if item.bottle_envelope:
                    loss_units = max(0.0, float(book) - float(actual))
                    result_row['bottle_envelope'] = True
                    result_row['variance_kes'] = round(
                        loss_units * item.bottle_expected_revenue_per_unit(), 2
                    )
                results.append(result_row)
            except Exception:
                continue

        return JsonResponse({'ok': True, 'results': results})
