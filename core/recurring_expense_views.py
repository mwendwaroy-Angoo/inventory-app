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
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BusinessExpense, RecurringExpense
from .views import get_user_profile, owner_required


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
    from .notifications import send_sms_notification, send_email_notification
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
        send_email_notification(owner_email, subject, html, text_message=sms)
    if owner_phone:
        phone = normalize_ke_phone(owner_phone)
        if phone:
            send_sms_notification(sms, phone)


def _send_investment_nudge(business):
    """Monthly SMS + email nudge: did you acquire any new assets?"""
    from .notifications import send_sms_notification, send_email_notification
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
        send_email_notification(owner_email, subject, html, text_message=sms)
    if owner_phone:
        phone = normalize_ke_phone(owner_phone)
        if phone:
            send_sms_notification(sms, phone)


# ── Manage recurring expenses (CRUD) ─────────────────────────────────────────

@login_required
@owner_required
def recurring_expense_list(request):
    up = get_user_profile(request)
    business = up.business
    expenses = RecurringExpense.objects.filter(business=business).order_by('category', 'description')

    from accounts.models import UserProfile
    STAFF_PAY_ROLES = ['staff', 'waitress', 'kitchen']
    staff_profiles = UserProfile.objects.filter(business=business, role__in=STAFF_PAY_ROLES).select_related('user')

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
    """
    up = get_user_profile(request)
    business = up.business
    today = timezone.localdate()

    due = _expenses_due_for_review(business)
    total_kes = Decimal('0')
    created_count = 0

    for expense in due:
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

        # Auto-create BusinessExpense if not already posted this period
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
