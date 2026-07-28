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
from decimal import Decimal

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, KegBarrel, KegWeightReading, PettyCash, Shift, ShiftStockCount, Transaction


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
    # invoice_no='[SVQ]' excludes stock-take variance corrections (2026-07-25 live
    # report, Monsoon Inn) — if a stock take is accepted while a shift happens to be
    # open, its corrective "cash sale" transaction would otherwise inflate this
    # shift's expected_cash for money the staffer never actually collected during
    # their shift, making an accurate physical count look like a false shortage.
    txns = Transaction.objects.filter(
        business=shift.business,
        type='Issue',
        created_at__gte=shift.started_at,
        created_at__lte=end,
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
    _petty_qs = PettyCash.objects.filter(business=shift.business, created_at__gte=shift.started_at, created_at__lte=end)
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
        business=shift.business,
        paid_at__gte=shift.started_at, paid_at__lte=end,
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
    elapsed_secs = int((end - shift.started_at).total_seconds())
    hours, rem   = divmod(elapsed_secs, 3600)
    mins         = rem // 60
    return {
        'cash_sales':    round(cash_sales, 2),
        'mpesa_sales':   round(mpesa_sales, 2),
        'credit_sales':  round(credit_sales, 2),
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

    if anchor:
        base = float(anchor.closing_cash_counted or 0)
        window_start = anchor.ended_at
        anchor_label = (
            f"{anchor.staff.get_full_name() or anchor.staff.username} — "
            f"{timezone.localtime(anchor.ended_at).strftime('%d %b, %H:%M')}"
        )
    else:
        base = 0.0
        window_start = None
        anchor_label = None

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

_SHIFT_AUTO_CLOSE_GRACE_HOURS = 2   # grace period after closing time before auto-close fires


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


def _auto_close_expired_shifts(business):
    """Close any OPEN shifts that ran past the business's closing time + grace.

    Returns a list of dicts describing what was auto-closed (for toast/notification).
    Does nothing when:
      - business.closing_time is not set (treat as 24-hour / unrestricted)
      - closing_time == opening_time (ambiguous — also treated as 24-hour)
    Handles overnight businesses (opening_time > closing_time, e.g. bar opens
    22:00, closes 02:00) by projecting the close onto the following calendar day.
    """
    closing_time = getattr(business, 'closing_time', None)
    opening_time = getattr(business, 'opening_time', None)

    if not closing_time or closing_time == opening_time:
        return []   # 24-hour or no hours configured — never auto-close

    now_local = timezone.localtime(timezone.now())
    local_tz  = timezone.get_current_timezone()
    grace     = timedelta(hours=_SHIFT_AUTO_CLOSE_GRACE_HOURS)
    is_overnight = closing_time < opening_time  # e.g. opens 22:00, closes 02:00

    open_shifts = list(
        Shift.objects.filter(business=business, status='OPEN').select_related('staff')
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

        if now_local >= timezone.localtime(scheduled_close + grace):
            staff_name = shift.staff.get_full_name() or shift.staff.username
            note = (
                f'[Auto-closed at {closing_time.strftime("%H:%M")} — '
                f'business hours ended. Staff may have forgotten.]'
            )
            shift.status     = 'CLOSED'
            shift.ended_at   = scheduled_close
            shift.auto_closed = True
            shift.notes      = (shift.notes + '\n' + note).strip() if shift.notes else note
            shift.save(update_fields=['status', 'ended_at', 'auto_closed', 'notes'])

            # Same tab-to-debt sweep a manual close performs — the system is force-closing
            # because we're already past closing time + grace, so always convert (no
            # "manager_taking_over" concept applies to an unattended auto-close).
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

    if not ShiftStockCount.objects.filter(shift=shift).exists():
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
        all_shifts_data.append({
            'id':          s.id,
            'staff_name':  s.staff.get_full_name() or s.staff.username,
            'section':     _section(s),
            'covers_both': _covers_both(s),
            'status':      s.status,
            'started_at':  timezone.localtime(s.started_at).strftime('%H:%M'),
            'elapsed':     rec['elapsed'],
            'cash_sales':  rec['cash_sales'],
            'mpesa_sales': rec['mpesa_sales'],
            'total_sales': rec['total_sales'],
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
            'opening_float':  float(my_shift.opening_float),
            'cash_sales':     rec['cash_sales'],
            'mpesa_sales':    rec['mpesa_sales'],
            'credit_sales':   rec['credit_sales'],
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
    till = till_expected_cash(up.business, my_station)
    expected_opening_cash = Decimal(str(till['expected_cash'])) - banked
    opening_variance = opening_float - expected_opening_cash

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
    # the shift).
    if abs(opening_variance) > 500:
        try:
            from .notifications import normalize_ke_phone as _nkp, send_sms_notification as _ssms
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
        'expected_opening_cash': float(expected_opening_cash),
        'opening_variance': float(opening_variance),
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

    try:
        closing_cash = Decimal(str(request.POST.get('closing_cash_counted', '0')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari si sahihi'}, status=400)

    notes_add = (request.POST.get('notes') or '').strip()
    try:
        offline_amt = Decimal(str(request.POST.get('offline_sales_amount', '0') or '0'))
    except Exception:
        offline_amt = Decimal('0')
    offline_note = (request.POST.get('offline_sales_note') or '').strip()

    shift.closing_cash_counted  = closing_cash
    shift.ended_at              = timezone.now()
    shift.status                = 'CLOSED'
    shift.offline_sales_amount  = offline_amt
    shift.offline_sales_note    = offline_note
    if notes_add:
        shift.notes = (shift.notes + '\n' + notes_add).strip()
    shift.save(update_fields=[
        'closing_cash_counted', 'ended_at', 'status', 'notes',
        'offline_sales_amount', 'offline_sales_note',
    ])

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
            from .notifications import normalize_ke_phone as _nkp, send_sms_notification as _ssms
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
        'cash_sales':           rec['cash_sales'],
        'mpesa_sales':          rec['mpesa_sales'],
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
        from .notifications import normalize_ke_phone, send_sms_notification
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
            Notification.objects.create(
                user=op.user, title='💬 Maelezo ya Tofauti ya Fedha', message=msg,
                notification_type='info', link_url='/bar/shift/history/',
            )
            if op.phone:
                try:
                    send_sms_notification(msg, normalize_ke_phone(op.phone))
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
            from .notifications import normalize_ke_phone, send_sms_notification
            send_sms_notification(msg, normalize_ke_phone(staff_profile.phone))
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
        from .notifications import normalize_ke_phone, send_sms_notification
        for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
            Notification.objects.create(
                user=op.user, title='💬 Maelezo ya Tofauti — Ufunguzi', message=msg,
                notification_type='info', link_url='/bar/shift/history/',
            )
            if op.phone:
                try:
                    send_sms_notification(msg, normalize_ke_phone(op.phone))
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
            from .notifications import normalize_ke_phone, send_sms_notification
            send_sms_notification(msg, normalize_ke_phone(staff_profile.phone))
    except Exception:
        logger.exception('review_opening_variance: notify failed for shift %s', shift.id)

    return JsonResponse({'ok': True, 'message': msg, 'is_reversal': is_reversal})


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
                        from .notifications import normalize_ke_phone, send_sms_notification
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
                                    send_sms_notification(msg, normalized)
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

@login_required
@require_POST
def confirm_shift(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if shift.status != 'CLOSED':
        return JsonResponse({'ok': False, 'error': 'Shift si CLOSED — haiwezi kuthibitishwa'}, status=400)

    notes_add = (request.POST.get('notes') or '').strip()
    shift.confirmed_by = request.user
    shift.status = 'CONFIRMED'
    if notes_add:
        shift.notes = (shift.notes + '\n✓ ' + notes_add).strip()
    shift.save(update_fields=['confirmed_by', 'status', 'notes'])
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

    shifts_qs = _base_qs.select_related('staff', 'confirmed_by').order_by('-started_at')[:60]

    rows = []
    for shift in shifts_qs:
        rec = _reconcile(shift)
        var = rec['variance']
        if var is None:
            var_class = 'pending'
        elif abs(var) <= 50:
            var_class = 'ok'
        elif abs(var) <= 200:
            var_class = 'warn'
        else:
            var_class = 'danger'
        rows.append({
            'shift':     shift,
            'rec':       rec,
            'var_class': var_class,
        })

    return render(request, 'core/bar/shift_history.html', {
        'rows':     rows,
        # Owner-or-manager (matches the identity-scoping check above and this app's
        # established is_owner_or_manager convention) — a manager confirms shifts and
        # reviews cash-variance explanations the same as an owner would.
        'is_owner': _is_owner,
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
        # Station scoping: only show items from the same counter as this shift
        _shift_store = shift.store
        if _shift_store is not None:
            items = items.filter(store__is_kitchen=_shift_store.is_kitchen)
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

        _shift_store = shift.store
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
                if _shift_store is not None and item.store_id:
                    if item.store.is_kitchen != _shift_store.is_kitchen:
                        continue
                book = item.current_balance()
                ShiftStockCount.objects.update_or_create(
                    shift=shift, item=item,
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
