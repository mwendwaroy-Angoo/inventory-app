"""
Sprint 7 — Recurring Expenses.

Flow:
  Owner sets up recurring expenses once (manage page).
  At first login each period, home view flags `expense_review_due`.
  Owner goes to review page, confirms or adjusts amounts.
  On confirm: BusinessExpense records are auto-created for the period;
              SMS + email confirmation sent.
  Monthly investment nudge sent separately.
"""
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.db import transaction as db_transaction
from django.db.models import Sum

from .models import BusinessExpense, RecurringExpense
from .views import get_user_profile, owner_required, owner_or_manager_required


# ── Helpers ───────────────────────────────────────────────────────────────────

def _expenses_due_for_review(business):
    """Return RecurringExpense records that need confirmation this period."""
    today = timezone.localdate()
    return [
        e for e in RecurringExpense.objects.filter(business=business, is_active=True)
        if e.is_due_for_review(today)
    ]


def _send_expense_notifications(business, owner, total_kes, period_label):
    """SMS + email when owner confirms recurring expenses."""
    from .notifications import send_sms_notification, send_sms_notification_async, send_email_notification, send_email_notification_async
    from accounts.models import normalize_ke_phone

    owner_phone = getattr(business, 'phone', '') or ''
    owner_email = getattr(business, 'email', '') or ''

    sms = (
        f"[Duka Mwecheche] Matumizi ya {period_label} yamethibitishwa: "
        f"KES {total_kes:,.0f}. Angalia uchambuzi wako kwa maelezo zaidi."
    )
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
            background:#1a1a1a;color:#f0ece4;padding:24px;border-radius:8px;">
  <h2 style="color:#c9a84c;font-family:Georgia,serif;">Matumizi ya {period_label}</h2>
  <p>Matumizi ya mara kwa mara yamethibitishwa kwa {period_label}:</p>
  <p style="font-size:1.4rem;font-weight:bold;color:#c9a84c;">KES {total_kes:,.0f}</p>
  <p style="color:#b0b0b0;font-size:0.9rem;">
    Angalia ukurasa wa Uchambuzi kwenye Duka Mwecheche kwa maelezo zaidi ya faida na hasara.
  </p>
  <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
</div>
"""
    subject = f"Duka Mwecheche — {business.name}: Matumizi ya {period_label} Yamethibitishwa"

    if owner_email:
        send_email_notification_async(owner_email, subject, html, text_message=sms)
    if owner_phone:
        phone = normalize_ke_phone(owner_phone)
        if phone:
            send_sms_notification_async(sms, phone)


def _send_investment_nudge(business):
    """Monthly SMS + email nudge: did you acquire any new assets?"""
    from .notifications import send_sms_notification, send_sms_notification_async, send_email_notification, send_email_notification_async
    from accounts.models import normalize_ke_phone

    owner_email = getattr(business, 'email', '') or ''
    owner_phone = getattr(business, 'phone', '') or ''
    month = timezone.localdate().strftime('%B %Y')

    sms = (
        f"[Duka Mwecheche] Je, ulinunua mali yoyote mpya mwezi huu ({month})? "
        f"Rekodi uwekezaji wako kwenye sehemu ya 'Uwekezaji wa Mtaji'."
    )
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
            background:#1a1a1a;color:#f0ece4;padding:24px;border-radius:8px;">
  <h2 style="color:#c9a84c;font-family:Georgia,serif;">Ukumbusho wa Uwekezaji — {month}</h2>
  <p>Je, ulinunua vifaa, gari, au mali nyingine mwezi huu?</p>
  <p style="color:#b0b0b0;">
    Kumbuka kurekodi uwekezaji wako wa mtaji kwenye Duka Mwecheche ili uchambuzi
    wako wa faida uwe sahihi.
  </p>
  <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
</div>
"""
    subject = f"Duka Mwecheche — Ukumbusho wa Uwekezaji: {month}"
    if owner_email:
        send_email_notification_async(owner_email, subject, html, text_message=sms)
    if owner_phone:
        phone = normalize_ke_phone(owner_phone)
        if phone:
            send_sms_notification_async(sms, phone)


# ── Manage recurring expenses (CRUD) ─────────────────────────────────────────

@login_required
@owner_required
def recurring_expense_list(request):
    up = get_user_profile(request)
    business = up.business
    expenses = RecurringExpense.objects.filter(business=business).order_by('category', 'description')

    from accounts.models import UserProfile
    # 2026-07-26 fix (live request, item 7): managers were excluded from this list
    # entirely — a manager's salary line could never be added here even though
    # Sprint M1 gave managers full operational access. STAFF_PAY_ROLES is this
    # app's one place that decides who is salary-eligible; grep this constant
    # before adding a new role anywhere else that needs the same list.
    STAFF_PAY_ROLES = ['staff', 'waitress', 'kitchen', 'manager']
    # Departed staff should not be selectable for a NEW recurring salary rule —
    # existing rules against them are left untouched (RecurringExpense.staff_profile
    # is SET_NULL, not filtered here) so their pay history stays intact.
    staff_profiles = UserProfile.objects.filter(
        business=business, role__in=STAFF_PAY_ROLES, user__is_active=True,
    ).select_related('user')

    return render(request, 'core/recurring_expense_list.html', {
        'expenses':       expenses,
        'staff_profiles': staff_profiles,
        'category_choices': BusinessExpense.CATEGORY_CHOICES,
        'period_choices':   RecurringExpense.PERIOD_CHOICES,
    })


@login_required
@owner_required
@require_POST
def recurring_expense_add(request):
    up = get_user_profile(request)
    business = up.business

    description  = (request.POST.get('description') or '').strip()
    category     = request.POST.get('category', 'other')
    amount_raw   = request.POST.get('amount', '0')
    period       = request.POST.get('period', 'MONTHLY')
    staff_id     = request.POST.get('staff_profile', '') or None
    notes        = (request.POST.get('notes') or '').strip()
    try:
        pay_day = max(0, min(28, int(request.POST.get('pay_day', '0') or 0)))
    except (ValueError, TypeError):
        pay_day = 0

    try:
        amount = Decimal(str(amount_raw))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid amount'}, status=400)

    if not description:
        return JsonResponse({'ok': False, 'error': 'Description required'}, status=400)

    from accounts.models import UserProfile
    staff_profile = None
    if staff_id:
        staff_profile = UserProfile.objects.filter(id=staff_id, business=business).first()

    RecurringExpense.objects.create(
        business=business,
        description=description,
        category=category,
        amount=amount,
        period=period,
        staff_profile=staff_profile,
        pay_day=pay_day,
        notes=notes,
    )
    return JsonResponse({'ok': True})


@login_required
@owner_required
@require_POST
def recurring_expense_edit(request, expense_id):
    up = get_user_profile(request)
    expense = get_object_or_404(RecurringExpense, id=expense_id, business=up.business)

    expense.description = (request.POST.get('description') or expense.description).strip()
    expense.category    = request.POST.get('category', expense.category)
    expense.period      = request.POST.get('period', expense.period)
    expense.notes       = (request.POST.get('notes') or '').strip()
    expense.is_active   = request.POST.get('is_active', '1') == '1'
    try:
        expense.pay_day = max(0, min(28, int(request.POST.get('pay_day', '0') or 0)))
    except (ValueError, TypeError):
        expense.pay_day = 0

    amount_raw = request.POST.get('amount')
    if amount_raw:
        try:
            expense.amount = Decimal(str(amount_raw))
        except Exception:
            pass

    from accounts.models import UserProfile
    staff_id = request.POST.get('staff_profile', '') or None
    if staff_id:
        expense.staff_profile = UserProfile.objects.filter(id=staff_id, business=up.business).first()
    else:
        expense.staff_profile = None

    expense.save()
    return JsonResponse({'ok': True})


@login_required
@owner_required
@require_POST
def recurring_expense_delete(request, expense_id):
    up = get_user_profile(request)
    expense = get_object_or_404(RecurringExpense, id=expense_id, business=up.business)
    expense.delete()
    return JsonResponse({'ok': True})


# ── Period review (first-login prompt) ───────────────────────────────────────

@login_required
@owner_required
def recurring_expense_review(request):
    """
    Show all recurring expenses due for confirmation this period.
    Owner can update amounts or confirm unchanged.
    """
    up = get_user_profile(request)
    business = up.business
    today = timezone.localdate()

    due = _expenses_due_for_review(business)
    # Group by period for display
    monthly   = [e for e in due if e.period == 'MONTHLY']
    quarterly = [e for e in due if e.period == 'QUARTERLY']
    annual    = [e for e in due if e.period == 'ANNUAL']

    return render(request, 'core/recurring_expense_review.html', {
        'due':       due,
        'monthly':   monthly,
        'quarterly': quarterly,
        'annual':    annual,
        'today':     today,
    })


@login_required
@owner_required
@require_POST
def recurring_expense_confirm(request):
    """
    Owner submits the review form. For each expense:
      - Apply any updated amounts
      - Auto-create BusinessExpense for current period (idempotent)
    Then update business.last_expense_review_date and send notifications.

    Idempotent against a double-submit (double-click, back-button resubmit of
    this real <form>, slow-network retry) two ways: (1) claim_checkout_token —
    this app's standard server-side backstop for exactly this shape of form,
    and (2) select_for_update() on each RecurringExpense row before the
    already_posted_this_period() check — the check-then-create was previously
    unlocked, so two near-simultaneous confirms could both pass the check
    before either BusinessExpense.objects.create() committed, double-posting
    a recurring line (often a salary or rent — this module's biggest cost
    lines) straight into net_profit on the analytics dashboard.
    """
    up = get_user_profile(request)
    business = up.business
    today = timezone.localdate()

    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return redirect('recurring_expense_review')

    due = _expenses_due_for_review(business)
    total_kes = Decimal('0')
    created_count = 0

    for expense in due:
        with db_transaction.atomic():
            expense = RecurringExpense.objects.select_for_update().get(pk=expense.pk)

            # Apply updated amount if owner changed it
            new_amount_raw = request.POST.get(f'amount_{expense.id}')
            if new_amount_raw:
                try:
                    new_amount = Decimal(str(new_amount_raw))
                    if new_amount != expense.amount:
                        expense.amount = new_amount
                except Exception:
                    pass

            expense.last_confirmed_at = timezone.now()
            expense.save(update_fields=['amount', 'last_confirmed_at'])

            # Auto-create BusinessExpense if not already posted this period —
            # re-checked under the lock so a concurrent confirm can't slip
            # through between the check and the create.
            if not expense.already_posted_this_period(today):
                period_start = expense.period_start(today)
                desc = expense.description
                if expense.staff_profile:
                    staff_name = expense.staff_profile.user.get_full_name() or expense.staff_profile.user.username
                    desc = f'Salary — {staff_name}'

                BusinessExpense.objects.create(
                    business=business,
                    description=desc,
                    amount=expense.amount,
                    category=expense.category,
                    date=period_start,
                    notes=f'[recurring] Auto-posted for {period_start.strftime("%B %Y")}',
                )
                created_count += 1

        total_kes += expense.amount

    # Update review date on business
    business.last_expense_review_date = today
    business.save(update_fields=['last_expense_review_date'])

    # Notify
    period_label = today.strftime('%B %Y')
    try:
        _send_expense_notifications(business, request.user, float(total_kes), period_label)
    except Exception:
        pass

    # Monthly investment nudge (send once per month)
    month_start = today.replace(day=1)
    try:
        last_notified = None
        for e in RecurringExpense.objects.filter(business=business, is_active=True):
            if e.last_notified_at:
                ln = e.last_notified_at.date() if hasattr(e.last_notified_at, 'date') else e.last_notified_at
                if ln >= month_start:
                    last_notified = ln
                    break
        if not last_notified:
            _send_investment_nudge(business)
            RecurringExpense.objects.filter(business=business, is_active=True).update(
                last_notified_at=timezone.now()
            )
    except Exception:
        pass

    return render(request, 'core/recurring_expense_confirmed.html', {
        'total_kes':     float(total_kes),
        'created_count': created_count,
        'period_label':  period_label,
        'expense_count': len(due),
    })


# ── Ad-hoc, station-scoped expense (2026-08-09 live request) ─────────────────
#
# Roy, from Kitchen Board's own reconciliation area: "can I record expenses
# for a certain day" — followed by an explicit design decision when asked
# whether it should reduce today's expected drawer cash the way Petty Cash
# does: "it should not touch today's expected drawer just for that
# specified day." So this is deliberately NOT PettyCash (a till drawdown,
# always "now", feeds till_expected_cash()/_reconcile()) — it's a plain
# bookkeeping BusinessExpense, optionally backdated to a specific day,
# optionally tagged to a station for that counter's own picture, that only
# ever feeds Expense Intelligence/P&L. till_expected_cash()/_reconcile()
# must never read this field — if a future change makes them do so, that
# breaks this feature's whole reason for existing.
#
# BusinessExpense has no review/approval workflow of its own (unlike
# PettyCash) — a write here is final — so this is owner/manager only,
# matching the sensitivity tier of every other direct financial-record-
# creation action in this app (Rekebisha, stock variance review, Kitchen
# Batch cost correction).

@login_required
@require_POST
def record_ad_hoc_expense(request):
    """
    2026-08-21 live request (Roy, on-site at Monsoon Inn — "the staff have no
    way of back dating expenses... I have been left with lots of recordings
    of both yesterday and today"): previously gated by @owner_or_manager_
    required — a decorator built for full-page views, HTML-redirects on
    failure, wrong for this AJAX/JSON-only endpoint (same latent bug class
    already fixed once for adjust_stock_balance, 2026-08-11). Removed the
    decorator; the permission check now lives inline (JSON-friendly) and
    additionally accepts UserProfile.can_record_expenses for a delegated
    staffer, same pattern as can_adjust_stock/can_manage_kegs. Backdating
    itself was never the gap — this view has supported an explicit `date`
    field since 2026-08-09 — the gap was pure access.
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)
    business = up.business

    is_owner = getattr(up, 'is_owner_or_manager', False)
    if not is_owner:
        if not getattr(up, 'can_record_expenses', False):
            return JsonResponse({'ok': False, 'error': 'Ruhusa ya kurekodi matumizi inahitajika.'}, status=403)
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse(
                {'ok': False, 'shift_required': True, 'error': 'Fungua shift kwanza.'}, status=403,
            )

    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'ok': False, 'error': 'Ombi hili tayari limetumwa.', 'duplicate': True}, status=409)

    amount_raw = (request.POST.get('amount') or '').strip()
    description = (request.POST.get('description') or '').strip()
    category = (request.POST.get('category') or 'other').strip()
    station = (request.POST.get('station') or '').strip()
    date_raw = (request.POST.get('date') or '').strip()
    notes = (request.POST.get('notes') or '').strip()

    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Weka kiasi sahihi.'}, status=400)

    if not description:
        return JsonResponse({'ok': False, 'error': 'Weka maelezo mafupi ya matumizi haya.'}, status=400)

    valid_categories = [c[0] for c in BusinessExpense.CATEGORY_CHOICES]
    if category not in valid_categories:
        category = 'other'

    if station not in ('bar', 'kitchen'):
        station = ''

    # Backdate to a specific day (the whole point of this feature) — never
    # into the future, silently falls back to today rather than blocking.
    expense_date = timezone.localdate()
    if date_raw:
        try:
            parsed = date_type.fromisoformat(date_raw)
            if parsed <= timezone.localdate():
                expense_date = parsed
        except ValueError:
            pass

    expense = BusinessExpense.objects.create(
        business=business, description=description, amount=amount,
        category=category, date=expense_date, notes=notes,
        station=station, recorded_by=request.user,
    )

    day_total = BusinessExpense.objects.filter(
        business=business, date=expense_date, station=station,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    return JsonResponse({
        'ok': True,
        'message': (
            f"✓ Matumizi yamerekodiwa: {description} — KES {amount:,.0f} "
            f"({expense_date.strftime('%d %b %Y')})"
        ),
        'expense': {
            'id': expense.id, 'description': expense.description,
            'amount': float(expense.amount), 'category': expense.category,
            'date': expense.date.isoformat(), 'station': expense.station,
        },
        'day_total': float(day_total),
    })


@login_required
def ad_hoc_expenses_list(request):
    """Read-only — the ad-hoc expenses recorded for one station+date, so a
    wrong entry (most often a wrong DATE, per Roy's 2026-08-09 live report)
    can actually be found in order to correct it via edit_ad_hoc_expense()
    below. 2026-08-21: widened from owner/manager-only to also allow a
    delegated staffer (can_record_expenses) — matching the recording
    permission tier, so someone entering a backlog of their own expenses
    can see what they've already logged for the day without needing the
    owner. Editing an existing entry stays owner/manager-only (see
    edit_ad_hoc_expense's own docstring)."""
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'expenses': []})
    if not (getattr(up, 'is_owner_or_manager', False) or getattr(up, 'can_record_expenses', False)):
        return JsonResponse({'expenses': []})
    business = up.business
    station = (request.GET.get('station') or '').strip()
    if station not in ('bar', 'kitchen'):
        return JsonResponse({'expenses': []})
    date_raw = (request.GET.get('date') or '').strip()
    the_date = timezone.localdate()
    if date_raw:
        try:
            the_date = date_type.fromisoformat(date_raw)
        except ValueError:
            pass
    rows = BusinessExpense.objects.filter(
        business=business, date=the_date, station=station,
    ).order_by('-id')
    return JsonResponse({
        'date': the_date.isoformat(),
        'expenses': [
            {
                'id': e.id, 'description': e.description,
                'amount': float(e.amount), 'category': e.category,
                'date': e.date.isoformat(), 'notes': e.notes,
                'recorded_by': e.recorded_by.get_full_name() or e.recorded_by.username if e.recorded_by else '',
            }
            for e in rows
        ],
    })


@login_required
@owner_or_manager_required
@require_POST
def edit_ad_hoc_expense(request, expense_id):
    """Correct an already-recorded ad-hoc expense — most often a wrong
    DATE (2026-08-09 live report: Roy placed one on the wrong date by
    mistake). No separate "recompute" step is needed after this: Shift
    History and the Z-report both call shift_views._ad_hoc_expense_total_
    for_shift() / the day-level BusinessExpense query fresh on EVERY page
    render, never from a frozen snapshot — so moving an expense's date here
    makes it disappear from the wrong day's reconciliation and appear on
    the correct day's the very next time either report is opened, with
    zero extra action required."""
    up = get_user_profile(request)
    business = up.business
    expense = BusinessExpense.objects.filter(id=expense_id, business=business).first()
    if expense is None:
        return JsonResponse({'ok': False, 'error': 'Haipatikani.'}, status=404)

    amount_raw = (request.POST.get('amount') or '').strip()
    description = (request.POST.get('description') or '').strip()
    category = (request.POST.get('category') or 'other').strip()
    station = (request.POST.get('station') or '').strip()
    date_raw = (request.POST.get('date') or '').strip()
    notes = (request.POST.get('notes') or '').strip()

    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Weka kiasi sahihi.'}, status=400)

    if not description:
        return JsonResponse({'ok': False, 'error': 'Weka maelezo mafupi ya matumizi haya.'}, status=400)

    valid_categories = [c[0] for c in BusinessExpense.CATEGORY_CHOICES]
    if category not in valid_categories:
        category = 'other'

    if station not in ('bar', 'kitchen'):
        station = ''

    # Same "never in the future, never blocks" rule as record — but here a
    # blank/invalid date falls back to the entry's OWN existing date, not
    # today, since an edit that touches other fields but leaves the date box
    # untouched must never silently move the date to today.
    new_date = expense.date
    if date_raw:
        try:
            parsed = date_type.fromisoformat(date_raw)
            if parsed <= timezone.localdate():
                new_date = parsed
        except ValueError:
            pass

    old_date = expense.date
    expense.amount = amount
    expense.description = description
    expense.category = category
    expense.station = station
    expense.date = new_date
    expense.notes = notes
    expense.save(update_fields=['amount', 'description', 'category', 'station', 'date', 'notes'])

    return JsonResponse({
        'ok': True,
        'message': (
            f"✓ Imesahihishwa: {description} — KES {amount:,.0f} "
            f"({new_date.strftime('%d %b %Y')})"
            + (f" — ilikuwa {old_date.strftime('%d %b %Y')}" if new_date != old_date else "")
        ),
        'expense': {
            'id': expense.id, 'description': expense.description,
            'amount': float(expense.amount), 'category': expense.category,
            'date': expense.date.isoformat(), 'station': expense.station,
        },
    })


@login_required
def expense_day_total_api(request):
    """Read-only — today's (or a given date's) station-scoped expense total,
    for the small informational readout on Bar/Kitchen Board. Deliberately
    separate from till_expected_cash() — this number never subtracts from
    expected cash, it's purely for visibility."""
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'total': 0})
    business = up.business
    station = (request.GET.get('station') or '').strip()
    if station not in ('bar', 'kitchen'):
        return JsonResponse({'total': 0})
    date_raw = (request.GET.get('date') or '').strip()
    the_date = timezone.localdate()
    if date_raw:
        try:
            the_date = date_type.fromisoformat(date_raw)
        except ValueError:
            pass
    total = BusinessExpense.objects.filter(
        business=business, date=the_date, station=station,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    return JsonResponse({'total': float(total), 'date': the_date.isoformat()})
