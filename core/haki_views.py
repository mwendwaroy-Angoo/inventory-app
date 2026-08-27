"""
core/haki_views.py — Haki module (Sprints H1-H4).

Haki = fairness / dues in Kiswahili.
Philosophy: the app already protects owners from theft (shrinkage). Haki is the
positive mirror — it makes each staffer's contribution visible, tracks what they're
owed and whether it was paid on time, and gives staff visibility into their own
standing. Honesty both directions.

Views:
    H1: staff_contribution_report  /staff/contribution/   (owner)
    H2: record_salary_payment      /staff/<id>/salary/    (owner)
    H3: my_work_and_pay            /me/                   (any staff)
    H4: haki_recognition_statement /staff/<id>/statement/ (owner — shareable)
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Abs, Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import UserProfile
from core.models import (
    CustomerDebtPayment, PettyCash, SalaryAdvanceRequest, SalaryDeduction,
    SalaryPayment, Shift, Transaction,
    RecurringExpense, Notification, BarTab, Receipt, StockVarianceQuery,
)
from core.views import get_user_profile, owner_required, owner_or_manager_required


def _salary_period_balance(business, staff_profile, period):
    """(expected, paid, remaining) for one staff member's period — expected
    comes from their configured RecurringExpense salary line (None if not
    configured, in which case remaining is meaningless and not shown), paid
    sums EVERY SalaryPayment for that period regardless of type (full/
    partial/advance — an advance reduces what's still owed exactly like a
    partial payment does). Single source of truth used at confirmation,
    Kazi Yangu, and advance-approval time so the figure is always the same.
    """
    configured = RecurringExpense.objects.filter(
        business=business, staff_profile=staff_profile, is_active=True,
    ).first()
    expected = configured.amount if configured else None
    paid = SalaryPayment.objects.filter(
        business=business, staff=staff_profile, period=period,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    remaining = (expected - paid) if expected is not None else None
    return expected, paid, remaining


def _period_date_range(period_str):
    """'2026-08' -> (date(2026,8,1), date(2026,8,31)) — the calendar span a
    salary `period` string covers, for pulling a staffer's own contribution
    figures (which are computed over a real date_from/date_to range, not a
    period label) for that SAME payroll period specifically."""
    year, month = map(int, period_str.split('-'))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _staff_tenure_window(staff_profile, business):
    """Earliest activity -> now (or departure) for one staff member — the
    same "full tenure" window staff_journey() (owner-facing) has always used.
    Factored out 2026-08-27 so my_work_and_pay() (Kazi Yangu, staff's own
    self-service page) can show the SAME "all my data since I began" window
    to the staffer themselves, per Roy's live request: "ensure that staff
    can see all their data since they began" — previously Kazi Yangu's
    contribution summary was hardcoded to the current calendar month only."""
    staff_user = staff_profile.user
    first_shift = Shift.objects.filter(business=business, staff=staff_user).order_by('started_at').first()
    first_txn = Transaction.objects.filter(business=business, recorded_by=staff_user).order_by('date').first()
    candidates = [d for d in [
        first_shift.started_at.date() if first_shift else None,
        first_txn.date if first_txn else None,
    ] if d]
    tenure_start = min(candidates) if candidates else timezone.localdate()
    tenure_end = (timezone.localtime(staff_profile.departed_at).date()
                  if staff_profile.departed_at else timezone.localdate())
    return tenure_start, tenure_end


# ── Contribution helper ───────────────────────────────────────────────────────

def _staff_contribution(staff_profile, business, date_from, date_to):
    """Build contribution data for one staff member over [date_from, date_to].

    Returns a dict with: revenue_kes, shifts, hours, debts_recovered_kes,
    clean_keg_record, milestones (list of badge strings), salary_status.
    """
    user = staff_profile.user

    # ── Shifts for this period ─────────────────────────────────────────────────
    shift_qs = Shift.objects.filter(
        business=business,
        staff=user,
        started_at__date__gte=date_from,
        started_at__date__lte=date_to,
    ).order_by('started_at')

    shift_count = shift_qs.count()
    total_hours = 0.0
    for sh in shift_qs:
        if sh.ended_at and sh.started_at:
            delta = (sh.ended_at - sh.started_at).total_seconds() / 3600.0
            total_hours += max(0.0, delta)

    # ── Revenue: individually attributed via Transaction.recorded_by ────────────
    # Every Issue transaction records the staff member who created it. One query,
    # zero overlap regardless of concurrent shifts or multiple counters.
    _rev = Case(
        When(sale_amount__isnull=False, then=F('sale_amount')),
        default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    aggs = Transaction.objects.filter(
        business=business,
        type='Issue',
        recorded_by=user,
        date__gte=date_from,
        date__lte=date_to,
    ).exclude(payment_method='void').aggregate(
        cash=Sum(_rev, filter=Q(payment_method='cash')),
        mpesa=Sum(_rev, filter=Q(payment_method='mpesa')),
        credit=Sum(_rev, filter=Q(payment_method='credit')),
    )
    cash_revenue   = Decimal(str(aggs['cash']   or 0))
    mpesa_revenue  = Decimal(str(aggs['mpesa']  or 0))
    credit_revenue = Decimal(str(aggs['credit'] or 0))
    total_revenue  = cash_revenue + mpesa_revenue + credit_revenue

    # ── Debts recovered by this staff ──
    debts_recovered = float(
        CustomerDebtPayment.objects.filter(
            business=business,
            recorded_by=user,
            paid_at__date__gte=date_from,
            paid_at__date__lte=date_to,
        ).exclude(reverted=True).aggregate(total=Sum('amount_paid'))['total'] or 0
    )

    # ── Keg clean-handling from shrinkage module ──
    # Only relevant for staff who actually have bar access. Kitchen-only staff
    # never touch kegs, so showing "clean keg record" is meaningless and misleading.
    has_bar_access = getattr(staff_profile, 'can_access_bar', False) or staff_profile.role in ('owner', 'staff', 'waitress')
    keg_loss = 0.0
    is_keg_business = getattr(business, 'has_keg', False)
    if is_keg_business and has_bar_access:
        try:
            from core.keg_metrics import staff_shrinkage
            rows = staff_shrinkage(business, date_from, date_to)
            for row in rows:
                if row.staff_id == user.id:
                    keg_loss = row.loss_kes
                    break
        except Exception:
            pass

    # Only set clean_keg_record=True when the business has kegs AND the staff
    # member has bar access — never for kitchen-only staff.
    clean_keg = is_keg_business and has_bar_access and keg_loss == 0.0

    # ── Milestone badges (positive only — H1-AC1) ──
    milestones = []
    if shift_count >= 30:
        milestones.append('🏅 30+ shifts')
    if debts_recovered >= 10000:
        milestones.append(f'💰 KES {debts_recovered:,.0f} recovered')
    if clean_keg and shift_count >= 10:
        milestones.append('✨ Clean handling')
    if float(total_revenue) >= 50000:
        milestones.append(f'⭐ KES {float(total_revenue):,.0f} mwezi huu')

    # ── Dismissed stock variances (compliance record) ──
    # 2026-08-26 (Roy — theft-verdict redesign): 'dismiss' now stamps
    # compliance_noted=True/owner_accepted=False the MOMENT the owner first
    # rejects a variance (status=DISPUTED), not only once the appeal window
    # closes — the stock correction is immediate, but the CONSEQUENCE
    # against this staffer's own record must not, per Roy's own framing
    # ("the verdict now becomes permanent" only after the window). Excluding
    # DISPUTED here means a still-open appeal never counts against her yet —
    # only a genuinely finalized (RESOLVED) verdict does.
    dismissed_variances = StockVarianceQuery.objects.filter(
        queried_staff=staff_profile,
        compliance_noted=True,
        status=StockVarianceQuery.RESOLVED,
        stock_take__taken_at__date__gte=date_from,
        stock_take__taken_at__date__lte=date_to,
    ).count()

    # ── 2026-07-26 (item 1) — petty cash storytelling: a REJECTED entry is an
    # unresolved cash-accountability question against this staffer specifically —
    # surfaced here with the owner's own stated reason (review_note) so the report
    # reads as "here's what happened and why," not a bare number. Pending entries
    # shown too so the staffer's own report explains why a shift's cash might not
    # look "closed" yet.
    petty_cash_rejected = list(PettyCash.objects.filter(
        business=business, recorded_by=user, status='rejected',
        date__gte=date_from, date__lte=date_to,
    ).select_related('reviewed_by').order_by('-reviewed_at'))
    petty_cash_rejected_kes = sum(float(e.amount) for e in petty_cash_rejected)
    petty_cash_pending_kes = float(PettyCash.objects.filter(
        business=business, recorded_by=user, status='pending',
        date__gte=date_from, date__lte=date_to,
    ).aggregate(t=Sum('amount'))['t'] or 0)

    # ── 2026-07-26 (item 8b) — wastage + stock-variance loss attribution. Both
    # were entirely absent from this report before — a staffer's Haki record
    # showed clean keg handling and debts recovered, but nothing about wastage
    # they logged or stock variances attributed to their shift, an incomplete
    # accountability picture in either direction (it can also clear them, when
    # StockVarianceQuery.attributed_shift correctly points elsewhere).
    # invoice_no='[ADJ-NOLOSS]' excluded — a Rekebisha shortage correction
    # explicitly marked "not a real loss" (e.g. reversing a duplicate-receipt
    # bug) must not be attributed to a staffer's own accountability record
    # as if it were a real handling loss (2026-07-31 live report).
    # 2026-08-21 fix (cross-sectional sweep after the sales_dashboard
    # naive-cost-formula report): this used to sum
    # Abs(qty) * item.cost_price directly in SQL — correct for a plain item,
    # but a bunch-discarded Wastage row (ProduceBunch.discard()) stores
    # qty as a FRACTION OF THE BUNCH'S TARGET REVENUE and prices against
    # bunch.cost_price, not item.cost_price (see Transaction.
    # _stock_movement_cost()'s own bunch branch and docstring — "never
    # item.cost_price, which isn't even the same unit of account for a
    # bunch-tracked produce item") — misattributing a staffer's wastage_kes
    # for any discarded batch/bunch they handled. Switched to the shared
    # loss_value() helper, iterated in Python (same pattern already used by
    # analytics_views.py's wastage_loss/void_loss for the identical reason).
    wastage_kes = sum(
        t.loss_value() for t in Transaction.objects.filter(
            business=business, type='Wastage', recorded_by=user,
            date__gte=date_from, date__lte=date_to,
        ).exclude(invoice_no='[ADJ-NOLOSS]').select_related(
            'item', 'keg_barrel', 'produce_bunch', 'kitchen_batch', 'preset',
        )
    )

    # 2026-08-22 fix (Roy — shift-change accountability): this used to sum
    # EVERY decrease-direction variance attributed to this staff's shift
    # regardless of resolution — including one the owner already ACCEPTED
    # (owner_accepted=True), which means the staff's own explanation was
    # believed and a corrective transaction created reflecting the real,
    # legitimate cause (an unrecorded sale, say) — not a real loss at all,
    # just a paperwork catch-up. Overcounting every accepted-and-explained
    # row as "loss" against a staffer since this figure was first built
    # (2026-07-26). Roy's own explicit rule: only a variance with "no
    # explanation nor affirmation from the required parties" should ever
    # count toward a staffer's own track record — i.e. exclude only the
    # AFFIRMED (owner_accepted=True) rows; still-pending, staff-responded-
    # but-not-yet-reviewed, and a FINALIZED dismissal (owner_accepted=False,
    # status=RESOLVED) all correctly still count until a genuine affirmation
    # clears them.
    #
    # 2026-08-26 addition (Roy — theft-verdict redesign): DISPUTED is a NEW
    # state that didn't exist when the rule above was written — it's the
    # owner's PRELIMINARY theft verdict, appeal window still open. Roy's own
    # explicit framing was two-stage on purpose ("if... the business owner
    # decides to be firm with his decision the verdict now becomes
    # permanent") — the consequence against a staffer's own record must not
    # be real until the window has actually run its course, or the appeal
    # would be theater with no real effect. Excluded here; a still-PENDING/
    # RESPONDED row (never reviewed at all, owner_accepted still None) is
    # untouched by this and keeps counting exactly as it always has.
    unaffirmed_variances_qs = StockVarianceQuery.objects.filter(
        attributed_shift__staff=user, attributed_shift__business=business,
        direction='decrease',
        stock_take__taken_at__date__gte=date_from,
        stock_take__taken_at__date__lte=date_to,
    ).exclude(owner_accepted=True).exclude(
        status=StockVarianceQuery.DISPUTED,
    ).select_related('item', 'stock_take').order_by('-created_at')
    variance_loss_kes = float(
        unaffirmed_variances_qs.aggregate(t=Sum('estimated_revenue'))['t'] or 0
    )

    return {
        'profile': staff_profile,
        'user': user,
        'revenue_kes': float(total_revenue),
        'cash_revenue': float(cash_revenue),
        'mpesa_revenue': float(mpesa_revenue),
        'credit_revenue': float(credit_revenue),
        'total_revenue': float(total_revenue),
        'shift_count': shift_count,
        'hours': round(total_hours, 1),
        'debts_recovered_kes': debts_recovered,
        'keg_loss_kes': keg_loss,
        'clean_keg_record': clean_keg,
        'milestones': milestones,
        'dismissed_variances': dismissed_variances,
        'petty_cash_rejected': petty_cash_rejected,
        'petty_cash_rejected_kes': petty_cash_rejected_kes,
        'petty_cash_pending_kes': petty_cash_pending_kes,
        'wastage_kes': wastage_kes,
        'variance_loss_kes': variance_loss_kes,
        # 2026-08-22 — the actual rows behind variance_loss_kes, so a
        # staffer (Kazi Yangu) or the owner (payroll suggestion) can see
        # EXACTLY which item/date each contribution came from, not just a
        # bare total — Roy: "the staff should have a view of their own
        # journey so that they do not claim that they were paid unfairly."
        'unaffirmed_variances': list(unaffirmed_variances_qs[:20]),
    }


# ── Recognition tier (2026-08-22, Roy) ──────────────────────────────────────
#
# "What are the merits set that determine an outstanding employee?" — the
# app already fires four separate positive milestone nudges (30+ shifts,
# KES 50k+ revenue, KES 10k+ debts recovered, clean keg record) plus a
# growing negative-side ledger (wastage, unaffirmed stock variance, rejected
# petty cash, dismissed variances) — but never COMBINES them into one
# answer. This does, as a plain points score with graduated deductions —
# Roy's own explicit rule: "one or two dismissed variances should not knock
# the staff out, lowering the tier is just enough."
#
# Deliberately a transparent points formula, not a black-box weighting — the
# breakdown returned alongside the score/tier is shown to BOTH the owner and
# the staffer themselves (same transparency principle as the payroll
# suggestion above), so a tier is always explainable, never just asserted.

RECOGNITION_MIN_SHIFTS = 5  # below this, there's simply not enough data to rate fairly.


def compute_staff_recognition(contrib, audience='owner'):
    """Combine an already-computed _staff_contribution() dict into one
    recognition tier — 'gold' | 'silver' | 'bronze' | 'developing' | 'unrated'.

    `audience`: 'owner' (default — the reader is looking AT a staffer, e.g.
    staff_contribution_report/staff_journey) keeps the existing 3rd-person
    Swahili wording ("Anahitaji Kuboresha" = "[they] need to improve").
    'staff' (the reader IS the staffer this is about, e.g. Kazi Yangu's own
    self-service page) switches the two verb-conjugated tier labels to
    direct 2nd-person address ("Unahitaji Kuboresha" = "YOU need to
    improve"). 2026-08-27 live correction (Roy): "in haki from the business
    owner side shows 'anahitaji kuboresha' ... on the staff's side in kazi
    yangu says 'anahitaji kuboresha' ... the system should be talking to
    the staff ... 'unahitaji kuboresha' since it is being addressed to the
    staff — enforce this anywhere there is such communication." 'Bora
    Kabisa' (gold) and 'Mzuri' (silver) are plain adjectives with no person
    marker either way, so only bronze/developing need a variant.

    Points (positive side, capped at 100 total before deductions):
      - Consistency: shift_count, full marks at 30+ shifts (the existing
        milestone's own threshold) — up to 30 points.
      - Revenue: full marks at KES 50,000+ this period (the existing
        milestone's own threshold) — up to 40 points, the single biggest
        factor since it's the most direct measure of contribution.
      - Debt recovery: full marks at KES 10,000+ recovered (the existing
        milestone's own threshold) — up to 20 points.
      - Clean keg handling: a flat 10-point bonus when clean_keg_record is
        True (only meaningful for keg businesses; simply 0 otherwise).

    Deductions (graduated — Roy's own explicit rule: 1-2 of anything minor
    must only ever lower the tier, never disqualify outright):
      - Dismissed stock variances and rejected petty cash entries: the
        first two cost 3 points each (a real but small ding); the third
        and beyond cost 8 points each — a genuine PATTERN, not an isolated
        mistake, is treated as materially more serious than the count alone
        would suggest linearly.
      - Unaffirmed stock-variance loss and wastage: scored as a PERCENTAGE
        of the staffer's own revenue, never a flat KES figure — KES 500
        unexplained against a slow KES 5,000 month is a much bigger red
        flag than the same KES 500 against a busy KES 100,000 month, and a
        flat-KES rule would be unfair to a business's highest performers
        simply for handling the most stock.

    Pattern cap: even a high point score can never reach the top (gold)
    tier while a genuine PATTERN is still live — 3+ dismissed variances,
    3+ rejected petty cash entries, or unaffirmed variance loss exceeding
    5% of revenue. This is the one place a "you can't be Outstanding right
    now" line is drawn, and it's drawn at PATTERN, never at a single
    mistake — matching Roy's own explicit instruction.

    Returns a dict with 'tier', 'tier_label', 'score', 'capped', and
    'breakdown' (list of (label, points, is_deduction) tuples) so the tier
    is always explainable to both the owner and the staffer, never just
    asserted.
    """
    shift_count = contrib.get('shift_count', 0)
    if shift_count < RECOGNITION_MIN_SHIFTS:
        return {
            'tier': 'unrated', 'tier_label': 'Bado Hakuna Data ya Kutosha',
            'score': None, 'capped': False, 'breakdown': [],
        }

    revenue = float(contrib.get('total_revenue', 0) or 0)
    debts = float(contrib.get('debts_recovered_kes', 0) or 0)
    clean_keg = bool(contrib.get('clean_keg_record'))
    dismissed = int(contrib.get('dismissed_variances', 0) or 0)
    variance_loss = float(contrib.get('variance_loss_kes', 0) or 0)
    wastage = float(contrib.get('wastage_kes', 0) or 0)
    petty_rejected = len(contrib.get('petty_cash_rejected') or [])

    breakdown = []

    consistency_pts = min(30, round(30 * shift_count / 30))
    breakdown.append(('Uthabiti (zamu)', consistency_pts, False))

    revenue_pts = min(40, round(40 * revenue / 50000)) if revenue > 0 else 0
    breakdown.append(('Mapato', revenue_pts, False))

    debt_pts = min(20, round(20 * debts / 10000)) if debts > 0 else 0
    breakdown.append(('Madeni Yaliyorudishwa', debt_pts, False))

    keg_pts = 10 if clean_keg else 0
    if contrib.get('keg_loss_kes') is not None:
        breakdown.append(('Ushughulikiaji wa Keg Safi', keg_pts, False))

    raw_score = consistency_pts + revenue_pts + debt_pts + keg_pts

    def _graduated(count, label_singular):
        if count <= 0:
            return 0
        if count <= 2:
            pts = count * 3
        else:
            pts = 6 + (count - 2) * 8
        breakdown.append((f'{count} {label_singular}', -pts, True))
        return pts

    dismissed_ded = _graduated(dismissed, 'tofauti za stock zilizokataliwa')
    petty_ded = _graduated(petty_rejected, 'petty cash iliyokataliwa')

    variance_pct = (variance_loss / revenue) if revenue > 0 else 0
    wastage_pct = (wastage / revenue) if revenue > 0 else 0
    variance_ded = min(20, round(variance_pct * 100 * 4))
    if variance_ded:
        breakdown.append((f'Tofauti ya stock isiyoelezwa ({variance_pct * 100:.1f}% ya mapato)', -variance_ded, True))
    wastage_ded = min(15, round(wastage_pct * 100 * 3))
    if wastage_ded:
        breakdown.append((f'Upotevu ({wastage_pct * 100:.1f}% ya mapato)', -wastage_ded, True))

    total_deductions = dismissed_ded + petty_ded + variance_ded + wastage_ded
    score = max(0, min(100, raw_score - total_deductions))

    # A live PATTERN — never a single mistake — bars the top tier regardless
    # of how high the point score otherwise is.
    capped = dismissed >= 3 or petty_rejected >= 3 or variance_pct > 0.05

    _bronze_label = '🥉 Unaendelea Vizuri' if audience == 'staff' else '🥉 Anaendelea Vizuri'
    _developing_label = 'Unahitaji Kuboresha' if audience == 'staff' else 'Anahitaji Kuboresha'

    if score >= 80 and not capped:
        tier, tier_label = 'gold', '🥇 Bora Kabisa'
    elif score >= 55:
        tier, tier_label = 'silver', '🥈 Mzuri'
    elif score >= 30:
        tier, tier_label = 'bronze', _bronze_label
    else:
        tier, tier_label = 'developing', _developing_label

    return {
        'tier': tier, 'tier_label': tier_label, 'score': score,
        'capped': capped, 'breakdown': breakdown,
    }


def _salary_status(staff_profile, business):
    """Return salary due / paid status for the current month."""
    today = timezone.localdate()
    period_str = today.strftime('%Y-%m')

    salary_entry = RecurringExpense.objects.filter(
        business=business,
        staff_profile=staff_profile,
        is_active=True,
        period='MONTHLY',
    ).first()

    if not salary_entry:
        return None

    payment = SalaryPayment.objects.filter(
        business=business,
        staff=staff_profile,
        period=period_str,
    ).first()

    # Due date: owner-configured pay_day, or last day of month if pay_day=0
    last_day = calendar.monthrange(today.year, today.month)[1]
    pay_day = int(salary_entry.pay_day or 0)
    if pay_day == 0:
        due_date = date(today.year, today.month, last_day)
    else:
        due_date = date(today.year, today.month, min(pay_day, last_day))

    return {
        'amount': salary_entry.amount,
        'period': period_str,
        'due_date': due_date,
        'paid': payment.paid if payment else False,
        'paid_at': payment.paid_at if payment else None,
        'days_overdue': payment.days_overdue if payment else max(0, (today - due_date).days if today > due_date else 0),
        'payment': payment,
    }


# ── H1: Owner — Staff Contribution Ledger ────────────────────────────────────

@login_required
@owner_or_manager_required
def staff_contribution_report(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    if not getattr(business, 'haki_enabled', True):
        messages.info(request, _('The Haki module is disabled for this business.'))
        return redirect('home')

    # Date range filter
    today = timezone.localdate()
    date_from_str = request.GET.get('from', (today - timedelta(days=29)).isoformat())
    date_to_str   = request.GET.get('to', today.isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
        date_to   = date.fromisoformat(date_to_str)
    except ValueError:
        date_from = today - timedelta(days=29)
        date_to   = today

    staff_profiles = UserProfile.objects.filter(
        business=business, user__is_active=True,
    ).exclude(role='owner').select_related('user').order_by('user__first_name')

    current_period = today.strftime('%Y-%m')

    period_date_from, period_date_to = _period_date_range(current_period)

    rows = []
    for sp in staff_profiles:
        contrib = _staff_contribution(sp, business, date_from, date_to)
        contrib['salary'] = _salary_status(sp, business)
        contrib['recognition'] = compute_staff_recognition(contrib)
        _check_and_fire_recognition(sp, business, contrib)

        # 2026-08-22 (Roy — shift-change accountability): a SUGGESTED payroll
        # deduction, computed over the actual PAYROLL PERIOD specifically
        # (never the report's own adjustable date_from/date_to filter above,
        # which the owner may have widened/narrowed for a completely
        # different reason) — "this would just be a suggestion from the
        # system based on the staff's performance, not a permanent
        # declaration." Never applied automatically; the owner still types
        # whatever amount they actually pay. Only meaningful when a real
        # configured salary line exists to suggest a deduction FROM.
        if date_from == period_date_from and date_to == period_date_to:
            period_contrib = contrib
        else:
            period_contrib = _staff_contribution(sp, business, period_date_from, period_date_to)
        contrib['period_variance_loss_kes'] = period_contrib['variance_loss_kes']
        contrib['period_unaffirmed_variances'] = period_contrib['unaffirmed_variances']
        if contrib['salary'] and period_contrib['variance_loss_kes'] > 0:
            contrib['suggested_salary'] = max(
                Decimal('0'),
                contrib['salary']['amount'] - Decimal(str(period_contrib['variance_loss_kes'])),
            )
        else:
            contrib['suggested_salary'] = None

        # Deductions this period (from rejected write-off requests)
        deductions = list(SalaryDeduction.objects.filter(
            business=business, staff=sp, period=current_period,
        ).order_by('-created_at'))
        contrib['deductions'] = deductions
        contrib['deduction_total'] = sum(d.amount for d in deductions)

        # Salary payments this period (support multiple partial payments)
        pay_rows = list(SalaryPayment.objects.filter(
            business=business, staff=sp, period=current_period, paid=True,
        ).order_by('paid_at'))
        contrib['pay_rows'] = pay_rows
        contrib['paid_total'] = sum(p.amount for p in pay_rows)

        rows.append(contrib)

    # Sort: most revenue first
    rows.sort(key=lambda r: -r['revenue_kes'])

    from core.models import WriteOffRequest
    pending_wo_count = WriteOffRequest.objects.filter(
        transaction__business=business,
        status=WriteOffRequest.STATUS_PENDING,
    ).count()

    # 2026-07-26 (item 8 follow-up) — pending emergency advance requests need
    # an owner decision; surfaced here so they're seen alongside everyone
    # else's contribution/pay status, not buried in a separate page.
    pending_advances = list(SalaryAdvanceRequest.objects.filter(
        business=business, status='pending',
    ).select_related('staff__user').order_by('requested_at'))

    return render(request, 'core/haki_contribution.html', {
        'rows': rows,
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'date_from_label': date_from.strftime('%d %b %Y'),
        'date_to_label': date_to.strftime('%d %b %Y'),
        'current_period': current_period,
        'pending_wo_count': pending_wo_count,
        'pending_advances': pending_advances,
    })


# ── H2: Record salary payment ─────────────────────────────────────────────────

@login_required
@owner_or_manager_required
@require_POST
def record_salary_payment(request, profile_id):
    """Record a salary payment (full or partial instalment) for a staff member.

    Multiple payments per period are allowed — they stack. Use payment_type='partial'
    for instalments; 'full' for a single complete month's payment.
    staff_note is optional and shown to the staff member on their Kazi Yangu page.
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    staff_profile = get_object_or_404(UserProfile, id=profile_id, business=business)

    period       = request.POST.get('period', timezone.localdate().strftime('%Y-%m'))
    amount       = request.POST.get('amount', '0').strip()
    method       = request.POST.get('method', 'cash')
    notes        = request.POST.get('notes', '').strip()
    payment_type = request.POST.get('payment_type', 'full')
    staff_note   = request.POST.get('staff_note', '').strip()

    if payment_type not in ('full', 'partial'):
        payment_type = 'full'

    try:
        amount_dec = Decimal(amount)
        if amount_dec <= 0:
            raise ValueError
    except (ValueError, Exception):
        messages.error(request, _('Please enter a valid salary amount.'))
        return redirect('staff_contribution_report')

    today    = timezone.localdate()
    last_day = calendar.monthrange(today.year, today.month)[1]
    due_date = date(today.year, today.month, last_day)

    # Always create a new row (multiple partial payments per period are valid)
    payment = SalaryPayment.objects.create(
        business=business,
        staff=staff_profile,
        period=period,
        amount=amount_dec,
        payment_type=payment_type,
        due_date=due_date,
        paid=True,
        paid_at=timezone.now(),
        method=method,
        notes=notes,
        staff_note=staff_note,
        recorded_by=request.user,
    )

    staff_name  = staff_profile.user.get_full_name() or staff_profile.user.username
    phone       = staff_profile.phone
    period_label = ''
    try:
        import datetime as _dt
        period_label = _dt.datetime.strptime(period, '%Y-%m').strftime('%B %Y')
    except Exception:
        period_label = period

    if phone:
        try:
            from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
            normalized = normalize_ke_phone(phone)
            if normalized:
                if payment_type == 'partial':
                    msg = (
                        f"{business.name}: Sehemu ya mshahara wako wa {period_label} "
                        f"KES {amount_dec:,.0f} imelipwa"
                        + (f" — {staff_note}" if staff_note else "")
                        + ". Angalia app kwa maelezo zaidi. 🙏"
                    )
                else:
                    msg = (
                        f"{business.name}: Mshahara wako wa {period_label} "
                        f"KES {amount_dec:,.0f} umelipwa. Asante kwa kazi nzuri. 🙏"
                        + (f"\n{staff_note}" if staff_note else "")
                    )
                send_sms_notification_async(msg, normalized)
        except Exception:
            pass

    type_label = 'Sehemu ya' if payment_type == 'partial' else ''
    messages.success(
        request,
        _(f'{type_label} Mshahara wa KES {amount_dec:,.2f} umerekodiwa kwa {staff_name}.').strip()
    )
    return redirect('staff_contribution_report')


# ── Item 8 (2026-07-26): staff confirms receipt ──────────────────────────────

@login_required
@require_POST
def confirm_salary_payment(request, payment_id):
    """Staff acknowledges they actually received a recorded salary payment —
    closes the loop record_salary_payment's SMS notice started but never
    confirmed. Own payment only; owner/manager have no need to confirm their
    own records.
    """
    user_profile = get_user_profile(request)
    payment = get_object_or_404(
        SalaryPayment, id=payment_id, business=user_profile.business, staff=user_profile,
    )
    if payment.confirmed_by_staff:
        return JsonResponse({'ok': True, 'message': 'Tayari umethibitisha.'})

    payment.confirmed_by_staff = True
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=['confirmed_by_staff', 'confirmed_at'])

    who = request.user.get_full_name() or request.user.username
    when = timezone.localtime(payment.confirmed_at).strftime('%d %b %Y, %H:%M')
    _expected, _paid, remaining = _salary_period_balance(
        user_profile.business, user_profile, payment.period,
    )
    message = f'{who} amethibitisha kupokea mshahara wa KES {payment.amount:,.0f} ({payment.period}) — {when}.'
    if remaining is not None:
        message += (
            f' Iliyobaki kwa {payment.period}: KES {remaining:,.0f}.' if remaining > 0
            else f' Mshahara wa {payment.period} umekamilika.'
        )
    if payment.recorded_by_id and payment.recorded_by_id != request.user.id:
        Notification.objects.create(
            user=payment.recorded_by,
            title='✅ Mshahara Umethibitishwa',
            message=message,
            notification_type='info',
            link_url='/staff/contribution/',
        )
    return JsonResponse({
        'ok': True, 'message': 'Asante — imethibitishwa.',
        'remaining_balance': float(remaining) if remaining is not None else None,
    })


# ── Item 8 follow-up (2026-07-26): salary advance requests ───────────────────

@login_required
@require_POST
def request_salary_advance(request):
    """Staff requests an emergency salary advance — amount + reason, always
    reviewed by the owner (manager verdict on money matters is not final
    anywhere else in this app either — see WriteOffRequest's own docstring).
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    if user_profile.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Owners/managers do not request advances.'}, status=400)

    amount_raw = (request.POST.get('amount') or '').strip()
    reason = (request.POST.get('reason') or '').strip()
    period = (request.POST.get('period') or '').strip() or timezone.localdate().strftime('%Y-%m')

    try:
        amount_dec = Decimal(amount_raw)
        if amount_dec <= 0:
            raise ValueError
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Ingiza kiasi sahihi.'}, status=400)
    if not reason:
        return JsonResponse({'ok': False, 'error': 'Eleza sababu ya dharura.'}, status=400)

    adv = SalaryAdvanceRequest.objects.create(
        business=business, staff=user_profile, amount_requested=amount_dec,
        reason=reason, period=period,
    )

    who = request.user.get_full_name() or request.user.username
    for op in UserProfile.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
        Notification.objects.create(
            user=op.user,
            title='🆘 Ombi la Advance ya Mshahara',
            message=f'{who} ameomba advance ya KES {amount_dec:,.0f} ({period}): {reason}',
            notification_type='warning',
            link_url='/staff/contribution/',
        )
    return JsonResponse({'ok': True, 'request_id': adv.id})


@login_required
@require_POST
def review_salary_advance(request, advance_id):
    """Approve (disburses immediately — creates the actual SalaryPayment,
    payment_type='advance', reducing that period's remaining balance right
    away) or reject (with a reason, no money moves).
    """
    user_profile = get_user_profile(request)
    if not user_profile or not user_profile.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Owner or manager only.'}, status=403)
    business = user_profile.business
    adv = get_object_or_404(SalaryAdvanceRequest, id=advance_id, business=business)
    if adv.status != 'pending':
        return JsonResponse({'ok': False, 'error': 'Ombi hili tayari limeamuliwa.'}, status=400)

    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        return JsonResponse({'ok': False, 'error': 'Invalid action'}, status=400)
    review_note = (request.POST.get('review_note') or '').strip()
    method = request.POST.get('method', 'cash')

    adv.reviewed_by = request.user
    adv.reviewed_at = timezone.now()
    adv.review_note = review_note

    if action == 'approve':
        today = timezone.localdate()
        last_day = calendar.monthrange(today.year, today.month)[1]
        due_date = date(today.year, today.month, last_day)
        payment = SalaryPayment.objects.create(
            business=business, staff=adv.staff, period=adv.period,
            amount=adv.amount_requested, payment_type='advance',
            due_date=due_date, paid=True, paid_at=timezone.now(),
            method=method, recorded_by=request.user,
            notes=f'Advance ya dharura: {adv.reason}',
        )
        adv.status = 'approved'
        adv.salary_payment = payment
    else:
        adv.status = 'rejected'
    adv.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'salary_payment'])

    reviewer = request.user.get_full_name() or request.user.username
    when = timezone.localtime(adv.reviewed_at).strftime('%d %b %Y, %H:%M')
    _expected, _paid, remaining = _salary_period_balance(business, adv.staff, adv.period)

    if action == 'approve':
        message = f'Advance yako ya KES {adv.amount_requested:,.0f} imeidhinishwa na {reviewer} tarehe {when}.'
        if remaining is not None:
            message += (
                f' Iliyobaki kwa {adv.period}: KES {remaining:,.0f}.' if remaining > 0
                else f' Mshahara wa {adv.period} umekamilika.'
            )
    else:
        message = f'Advance yako ya KES {adv.amount_requested:,.0f} imekataliwa na {reviewer} tarehe {when}.'
        if review_note:
            message += f' Sababu: {review_note}'

    Notification.objects.create(
        user=adv.staff.user,
        title='✅ Advance Imeidhinishwa' if action == 'approve' else '❌ Advance Imekataliwa',
        message=message,
        notification_type=('info' if action == 'approve' else 'warning'),
        link_url='/me/',
    )
    return JsonResponse({'ok': True, 'message': message, 'status': adv.status})


# ── Item 8 (2026-07-26): bulk payroll run ────────────────────────────────────

STAFF_PAY_ROLES = ['staff', 'waitress', 'kitchen', 'manager']


@login_required
@owner_or_manager_required
def run_payroll(request):
    """One pass across all active pay-eligible staff for a period — reuses the
    exact same SalaryPayment-creation + SMS logic record_salary_payment uses,
    per selected staff line, instead of visiting each person one at a time.
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    today = timezone.localdate()
    current_period = today.strftime('%Y-%m')

    if request.method == 'POST':
        selected_ids = request.POST.getlist('pay_staff_id')
        method = request.POST.get('method', 'cash')
        period = request.POST.get('period', current_period)
        last_day = calendar.monthrange(today.year, today.month)[1]
        due_date = date(today.year, today.month, last_day)

        paid_count = 0
        for profile_id in selected_ids:
            amount_raw = (request.POST.get(f'amount_{profile_id}') or '').strip()
            try:
                amount_dec = Decimal(amount_raw)
                if amount_dec <= 0:
                    continue
            except Exception:
                continue
            staff_profile = UserProfile.objects.filter(
                id=profile_id, business=business, role__in=STAFF_PAY_ROLES, user__is_active=True,
            ).first()
            if not staff_profile:
                continue

            configured = RecurringExpense.objects.filter(
                business=business, staff_profile=staff_profile, is_active=True,
            ).first()
            payment_type = 'full'
            if configured and amount_dec < configured.amount:
                payment_type = 'partial'

            SalaryPayment.objects.create(
                business=business, staff=staff_profile, period=period,
                amount=amount_dec, payment_type=payment_type,
                due_date=due_date, paid=True, paid_at=timezone.now(),
                method=method, recorded_by=request.user,
            )
            paid_count += 1

            staff_name = staff_profile.user.get_full_name() or staff_profile.user.username
            period_label = due_date.strftime('%B %Y')
            phone = staff_profile.phone
            if phone:
                try:
                    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
                    normalized = normalize_ke_phone(phone)
                    if normalized:
                        msg = (
                            f"{business.name}: Mshahara wako wa {period_label} "
                            f"KES {amount_dec:,.0f} umelipwa. Asante kwa kazi nzuri. 🙏"
                        )
                        send_sms_notification_async(msg, normalized)
                except Exception:
                    pass

        messages.success(request, _(f'Mishahara {paid_count} imerekodiwa.'))
        return redirect('staff_contribution_report')

    staff_profiles = UserProfile.objects.filter(
        business=business, role__in=STAFF_PAY_ROLES, user__is_active=True,
    ).select_related('user')

    rows = []
    for sp in staff_profiles:
        configured = RecurringExpense.objects.filter(
            business=business, staff_profile=sp, is_active=True,
        ).first()
        already_paid = SalaryPayment.objects.filter(
            business=business, staff=sp, period=current_period,
        ).aggregate(t=Sum('amount'))['t'] or 0
        rows.append({
            'profile': sp,
            'suggested_amount': configured.amount if configured else None,
            'already_paid': already_paid,
        })

    return render(request, 'core/haki_payroll_run.html', {
        'rows': rows,
        'current_period': current_period,
    })


# ── H3: Staff — "Kazi Yangu" self-service page ───────────────────────────────

@login_required
def my_work_and_pay(request):
    """Staff sees their OWN contribution data and pay status only (H2-AC2 privacy)."""
    user_profile = get_user_profile(request)
    business = user_profile.business

    if not business:
        messages.error(request, _('No business found.'))
        return redirect('home')

    if user_profile.is_owner:
        return redirect('staff_contribution_report')

    if not getattr(business, 'haki_enabled', True):
        return redirect('home')

    today = timezone.localdate()
    date_from = today.replace(day=1)  # Current month
    current_period = today.strftime('%Y-%m')
    contrib = _staff_contribution(user_profile, business, date_from, today)
    # audience='staff': this page is Kazi Yangu, ALWAYS the staffer reading
    # about themselves (owners are redirected away above) — 2nd-person
    # wording ("unahitaji kuboresha"), never 3rd person. See
    # compute_staff_recognition()'s own docstring for the full reasoning.
    contrib['recognition'] = compute_staff_recognition(contrib, audience='staff')
    salary  = _salary_status(user_profile, business)

    # 2026-08-27 live request (Roy): "ensure that staff can see all their
    # data since they began" — this whole page was hardcoded to the current
    # calendar month, and pay/advance history were capped at the last 6-12
    # rows. Full-tenure contribution summary (revenue/shifts/hours/debts
    # recovered/milestones/recognition), computed the exact same way
    # staff_journey() (the owner-facing tenure report) already does, so the
    # staffer sees the identical numbers the owner would if they looked.
    tenure_start, tenure_end = _staff_tenure_window(user_profile, business)
    contrib_all_time = _staff_contribution(user_profile, business, tenure_start, tenure_end)
    contrib_all_time['recognition'] = compute_staff_recognition(contrib_all_time, audience='staff')

    # Payment history: ALL rows, not just the last 12 (small per-staff
    # table, no date filtering needed — same precedent as staff_journey()'s
    # own salary_payments query).
    pay_history = SalaryPayment.objects.filter(
        business=business,
        staff=user_profile,
    ).order_by('-period', '-paid_at')

    # Deductions this period — shown to staff so they understand any shortfall
    deductions = list(SalaryDeduction.objects.filter(
        business=business, staff=user_profile, period=current_period,
    ).order_by('-created_at'))
    deduction_total = sum(d.amount for d in deductions)

    # ALL deductions ever, not just this period — part of "all their data
    # since they began."
    all_deductions = list(SalaryDeduction.objects.filter(
        business=business, staff=user_profile,
    ).order_by('-created_at'))
    all_deductions_total = sum(d.amount for d in all_deductions)

    # Paid this period
    paid_rows = list(SalaryPayment.objects.filter(
        business=business, staff=user_profile, period=current_period, paid=True,
    ).order_by('paid_at'))
    paid_total = sum(p.amount for p in paid_rows)

    # 2026-07-26 (item 8 follow-up) — remaining balance + advance request
    # history/status, so the staffer sees the full accountability picture:
    # what's expected, what's paid (full/partial/advance combined), what's
    # left, and where any emergency advance requests stand.
    expected, _paid_total_calc, remaining_balance = _salary_period_balance(
        business, user_profile, current_period,
    )
    # 2026-08-27: ALL advance requests ever, not just the last 10 — same
    # "since they began" fix as pay_history above.
    advance_requests = list(SalaryAdvanceRequest.objects.filter(
        business=business, staff=user_profile,
    ).select_related('reviewed_by').order_by('-requested_at'))

    # 2026-07-30 — Maombi/Maagizo redesign: surface pending owner instructions
    # right here too, not only on the /staff-requests/ page — Kazi Yangu is
    # where a staffer already checks "what do I need to do," so a pending
    # instruction belongs alongside it. Same query staff_request_list() uses
    # for a staffer's own instructions tab (assigned to them, or broadcast).
    from .models import StaffRequest
    from .staff_request_views import _assigned_or_broadcast_q
    pending_instructions = list(
        StaffRequest.objects.filter(
            business=business, direction=StaffRequest.DIRECTION_INSTRUCTION, status='pending',
        ).filter(_assigned_or_broadcast_q(user_profile)).select_related('related_item').order_by('due_date', 'created_at')[:10]
    )

    return render(request, 'core/haki_kazi_yangu.html', {
        'pending_instructions': pending_instructions,
        **contrib,
        'salary': salary,
        'pay_history': pay_history,
        'period_label': date_from.strftime('%B %Y'),
        'deductions': deductions,
        'deduction_total': deduction_total,
        'all_deductions': all_deductions,
        'all_deductions_total': all_deductions_total,
        'paid_rows': paid_rows,
        'paid_total': paid_total,
        'current_period': current_period,
        'expected_salary': expected,
        'remaining_balance': remaining_balance,
        'advance_requests': advance_requests,
        'all_time': contrib_all_time,
        'tenure_start': tenure_start,
        'tenure_end': tenure_end,
    })


# ── H4: Recognition nudge check + shareable statement ────────────────────────

def _check_and_fire_recognition(staff_profile, business, contrib):
    """Fire an in-app nudge to owner when a staffer hits a positive milestone.
    Only fires once per milestone per period (deduped by Notification message content).
    Called from staff_contribution_report.
    """
    from core.notifications import create_in_app_notification
    try:
        owner_user = UserProfile.objects.filter(
            business=business, role='owner'
        ).select_related('user').first()
        if not owner_user:
            return

        staff_name = staff_profile.user.get_full_name() or staff_profile.user.username
        for badge in contrib.get('milestones', []):
            title = f'🌟 {staff_name} — Milestone'
            # Dedup: don't re-notify the same badge this month
            period_prefix = timezone.localdate().strftime('%Y-%m')
            msg_key = f"[{period_prefix}] {staff_name}: {badge}"
            if not Notification.objects.filter(
                user=owner_user.user, message__startswith=msg_key
            ).exists():
                create_in_app_notification(
                    user=owner_user.user,
                    title=title,
                    message=f"{msg_key}. Consider recognising them.",
                    notification_type='staff',
                    link_url=f'/staff/{staff_profile.id}/statement/',
                )
    except Exception:
        pass


@login_required
def haki_recognition_statement(request, profile_id):
    """Generate a shareable pay + contribution statement for one staff member.

    Owner can view any staff member's statement.
    Staff can view ONLY their own statement (H2-AC2 privacy).
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    staff_profile = get_object_or_404(UserProfile, id=profile_id, business=business)

    # Privacy gate: staff can only see their own statement; managers can see any
    if not user_profile.is_owner_or_manager and staff_profile.id != user_profile.id:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Huwezi kuona taarifa ya mwenzio.')

    if not getattr(business, 'haki_enabled', True):
        return redirect('home')

    today = timezone.localdate()
    date_from = today.replace(day=1)

    contrib = _staff_contribution(staff_profile, business, date_from, today)
    # audience='staff' unconditionally: this is a personal statement — a
    # printable/shareable "taarifa" document meant to be handed TO (or
    # shared with) the staff member it's about, whether it's the staffer
    # themselves pulling it up (Kazi Yangu's "🌟 Taarifa Yangu") or the
    # owner reviewing/printing it to give them — it always reads as
    # addressed to the staffer, never a 3rd-person report FOR the owner
    # (that's staff_contribution_report's job instead).
    contrib['recognition'] = compute_staff_recognition(contrib, audience='staff')
    salary  = _salary_status(staff_profile, business)

    pay_history = SalaryPayment.objects.filter(
        business=business,
        staff=staff_profile,
    ).order_by('-period')[:12]

    # Send SMS statement if POST with send_sms
    sms_sent = False
    if request.method == 'POST' and request.POST.get('send_sms') == '1':
        phone = staff_profile.phone
        if phone:
            try:
                from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
                normalized = normalize_ke_phone(phone)
                if normalized:
                    period_label = date_from.strftime('%B %Y')
                    paid_str = 'Umelipwa' if (salary and salary['paid']) else 'Bado kulipwa'
                    salary_line = f"Mshahara {period_label}: {paid_str}" + (
                        f" KES {salary['amount']:,.0f}" if salary else ''
                    )
                    contrib_line = f"Mapato {period_label}: KES {contrib['revenue_kes']:,.0f}"
                    shifts_line  = f"Zamu: {contrib['shift_count']}, Saa: {contrib['hours']}"
                    badges = ', '.join(contrib['milestones']) if contrib['milestones'] else ''
                    msg = (
                        f"{business.name} — Taarifa yako ya Kazi\n"
                        f"{contrib_line}\n{shifts_line}\n{salary_line}"
                        + (f"\n{badges}" if badges else '')
                    )
                    send_sms_notification_async(msg, normalized)
                    sms_sent = True
                    messages.success(request, _('Statement sent to %(phone)s.') % {'phone': phone})
            except Exception:
                messages.error(request, _('Could not send SMS. Check staff phone number.'))
        else:
            messages.error(request, _('This staff member has no phone number saved.'))

    return render(request, 'core/haki_statement.html', {
        **contrib,
        'salary': salary,
        'pay_history': pay_history,
        'period_label': date_from.strftime('%B %Y'),
        'sms_sent': sms_sent,
        'business': business,
    })


# ── Duty Log — Staff / Manager Footprint ─────────────────────────────────────

@login_required
@owner_or_manager_required
def staff_duty_log(request, profile_id):
    """Daily supervisory footprint for any staff or manager.

    Shows: shifts worked, transactions they recorded, receipts they issued,
    and tabs they opened or settled — all scoped to one calendar date.
    Owner/manager only; accessible for past dates (next-morning review).
    """
    up = get_user_profile(request)
    if not up:
        from django.shortcuts import redirect
        return redirect('home')
    business = up.business

    staff_profile = get_object_or_404(UserProfile, id=profile_id, business=business)
    staff_user = staff_profile.user

    # Date param — defaults to today
    date_str = request.GET.get('date', '')
    try:
        report_date = date.fromisoformat(date_str)
    except ValueError:
        report_date = timezone.localdate()

    prev_date = report_date - timedelta(days=1)
    next_date = report_date + timedelta(days=1)
    today = timezone.localdate()

    # ── Shifts ────────────────────────────────────────────────────────────────
    shifts = list(Shift.objects.filter(
        business=business,
        staff=staff_user,
        started_at__date=report_date,
    ).select_related('store').order_by('started_at'))

    # 2026-07-31 live report — Roy: "small variances unaccounted for by the
    # system irregardless of the staff's compliance all along the app." Root
    # cause found here: this shift-variance calculation used to be a
    # hand-rolled, INCOMPLETE reimplementation of shift_views._reconcile()
    # ("Replicate _reconcile logic inline (no import needed)") that silently
    # dropped three things the real reconciliation always accounts for —
    # (a) no item__store__is_kitchen station scoping at all, so on a combo
    # bar+kitchen business a kitchen staffer's variance here was computed
    # against the WHOLE business's cash sales, bar included, and vice versa;
    # (b) `Sum('sale_amount')` with no fallback to abs(qty)*item.selling_price
    # — SQL SUM skips NULLs, so any Issue transaction that never set
    # sale_amount (any plain sale outside a preset/batch/bunch envelope —
    # exactly the class covering chipo/smokies/kuku sold through different
    # mechanisms) silently contributed KES 0 instead of its real amount; (c)
    # no petty-cash/debt-recovered/offline-sales adjustment, so this figure
    # could never match the real close-shift variance even when (a) and (b)
    # didn't apply. Every other surface in this app already computes this
    # through the one real `_reconcile()` function — using it here instead
    # closes the gap for free, with no separate per-item-type coverage list
    # to maintain (every kitchen sale mechanism — KitchenBatch, ProduceBunch,
    # portion items — already funnels through the SAME Transaction/type=
    # 'Issue' shape _reconcile() reads).
    from core.shift_views import _reconcile
    shift_summaries = []
    for sh in shifts:
        duration_mins = None
        if sh.ended_at:
            delta = sh.ended_at - sh.started_at
            duration_mins = int(delta.total_seconds() / 60)
        variance = None
        if sh.closing_cash_counted is not None:
            variance = _reconcile(sh)['variance']
        shift_summaries.append({
            'shift': sh,
            'duration_mins': duration_mins,
            'variance': variance,
            'store_name': sh.store.name if sh.store else '—',
        })

    # ── Transactions recorded ─────────────────────────────────────────────────
    transactions = list(Transaction.objects.filter(
        business=business,
        recorded_by=staff_user,
        date=report_date,
    ).select_related('item__store').order_by('created_at'))

    # Same NULL-sale_amount gap as above — Transaction.revenue() is this
    # app's one canonical figure (sale_amount when set, else
    # abs(qty)*item.selling_price), not a raw sale_amount read.
    txn_revenue = sum(t.revenue() for t in transactions)
    txn_by_method = {}
    for t in transactions:
        if t.type == 'Issue' and t.payment_method not in ('void', 'tab', ''):
            txn_by_method[t.payment_method] = (
                txn_by_method.get(t.payment_method, 0) + t.revenue()
            )

    # ── Receipts issued ───────────────────────────────────────────────────────
    receipts = list(Receipt.objects.filter(
        business=business,
        created_by=staff_user,
        created_at__date=report_date,
    ).order_by('created_at'))

    # ── Tabs opened or settled by this staff member ───────────────────────────
    tabs_opened = list(BarTab.objects.filter(
        business=business,
        served_by=staff_user,
        opened_at__date=report_date,
    ).order_by('opened_at'))

    tabs_settled = list(BarTab.objects.filter(
        business=business,
        served_by=staff_user,
        settled_at__date=report_date,
    ).exclude(status='OPEN').order_by('settled_at'))

    return render(request, 'core/staff_duty_log.html', {
        'staff_profile': staff_profile,
        'report_date': report_date,
        'prev_date': prev_date,
        'next_date': next_date,
        'today': today,
        'shift_summaries': shift_summaries,
        'transactions': transactions,
        'txn_revenue': txn_revenue,
        'txn_by_method': txn_by_method,
        'receipts': receipts,
        'tabs_opened': tabs_opened,
        'tabs_settled': tabs_settled,
        'is_manager': staff_profile.role == 'manager',
    })


# ── Staff Journey — full-tenure history, survives rename/departure ───────────

@login_required
@owner_or_manager_required
def staff_journey(request, profile_id):
    """The durable "story" of one staff member's time at this business —
    revenue handled, hours worked, salary paid, and name/username history —
    all still queryable after they've been renamed or removed from the active
    roster (accounts.views.deactivate_staff soft-deletes rather than hard
    deletes specifically so this stays possible). Deliberately looks up
    UserProfile with NO active-state filter, unlike every roster-list view.
    """
    up = get_user_profile(request)
    if not up:
        return redirect('home')
    business = up.business

    staff_profile = get_object_or_404(UserProfile, id=profile_id, business=business)
    staff_user = staff_profile.user

    # ── Tenure window: earliest activity → now (or departure) ──────────────────
    tenure_start, tenure_end = _staff_tenure_window(staff_profile, business)

    contrib = _staff_contribution(staff_profile, business, tenure_start, tenure_end)
    contrib['recognition'] = compute_staff_recognition(contrib)

    # ── Full keg-handling detail (contrib only keeps a collapsed loss figure) ──
    keg_detail = None
    if getattr(business, 'has_keg', False):
        try:
            from core.keg_metrics import staff_shrinkage
            for row in staff_shrinkage(business, tenure_start, tenure_end):
                if row.staff_id == staff_user.id:
                    keg_detail = row
                    break
        except Exception:
            pass

    # ── Full salary history (small per-staff table, no date filtering needed) ──
    salary_payments = list(SalaryPayment.objects.filter(
        business=business, staff=staff_profile,
    ).order_by('-period'))
    salary_deductions = list(SalaryDeduction.objects.filter(
        business=business, staff=staff_profile,
    ).order_by('-created_at'))
    total_paid = sum(float(p.amount) for p in salary_payments if p.paid)
    total_deducted = sum(float(d.amount) for d in salary_deductions)

    # ── Name/username history ───────────────────────────────────────────────────
    from accounts.models import StaffNameChangeLog
    name_history = list(StaffNameChangeLog.objects.filter(staff=staff_user).order_by('-changed_at'))

    return render(request, 'core/staff_journey.html', {
        'staff_profile': staff_profile,
        'tenure_start': tenure_start,
        'tenure_end': tenure_end,
        'contrib': contrib,
        'keg_detail': keg_detail,
        'salary_payments': salary_payments,
        'salary_deductions': salary_deductions,
        'total_paid': total_paid,
        'total_deducted': total_deducted,
        'name_history': name_history,
        'is_departed': staff_profile.is_departed,
    })
