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
    CustomerDebtPayment, SalaryPayment, Shift, Transaction,
    RecurringExpense, Notification,
)
from core.views import get_user_profile, owner_required


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
    shift_q = Q()
    for sh in shift_qs:
        end = sh.ended_at or timezone.now()
        if sh.ended_at and sh.started_at:
            delta = (sh.ended_at - sh.started_at).total_seconds() / 3600.0
            total_hours += max(0.0, delta)
        shift_q |= Q(created_at__gte=sh.started_at, created_at__lte=end)

    # ── Revenue: shift-window Issue transactions ────────────────────────────────
    # Attribution: any sale made in this staff's store type during their shift window.
    # Store scope mirrors _reconcile: kitchen staff → is_kitchen=True,
    # owner → no filter, other staff → is_kitchen=False.
    # Single aggregate query over OR'd time windows avoids N queries per shift.
    cash_revenue   = Decimal('0')
    mpesa_revenue  = Decimal('0')
    credit_revenue = Decimal('0')

    if shift_q:
        _rev = Case(
            When(sale_amount__isnull=False, then=F('sale_amount')),
            default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        txns = Transaction.objects.filter(shift_q, business=business, type='Issue')
        staff_role = staff_profile.role
        if staff_role == 'kitchen':
            txns = txns.filter(item__store__is_kitchen=True)
        elif staff_role != 'owner':
            txns = txns.filter(item__store__is_kitchen=False)

        aggs = txns.aggregate(
            cash=Sum(_rev, filter=Q(payment_method='cash')),
            mpesa=Sum(_rev, filter=Q(payment_method='mpesa')),
            credit=Sum(_rev, filter=Q(payment_method='credit')),
        )
        cash_revenue   = Decimal(str(aggs['cash']   or 0))
        mpesa_revenue  = Decimal(str(aggs['mpesa']  or 0))
        credit_revenue = Decimal(str(aggs['credit'] or 0))

    total_revenue = cash_revenue + mpesa_revenue + credit_revenue

    # ── Debts recovered by this staff ──
    debts_recovered = float(
        CustomerDebtPayment.objects.filter(
            business=business,
            recorded_by=user,
            paid_at__date__gte=date_from,
            paid_at__date__lte=date_to,
        ).aggregate(total=Sum('amount_paid'))['total'] or 0
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
@owner_required
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
        business=business,
    ).exclude(role='owner').select_related('user').order_by('user__first_name')

    rows = []
    for sp in staff_profiles:
        contrib = _staff_contribution(sp, business, date_from, date_to)
        contrib['salary'] = _salary_status(sp, business)
        _check_and_fire_recognition(sp, business, contrib)
        rows.append(contrib)

    # Sort: most revenue first
    rows.sort(key=lambda r: -r['revenue_kes'])

    return render(request, 'core/haki_contribution.html', {
        'rows': rows,
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'date_from_label': date_from.strftime('%d %b %Y'),
        'date_to_label': date_to.strftime('%d %b %Y'),
    })


# ── H2: Record salary payment ─────────────────────────────────────────────────

@login_required
@owner_required
@require_POST
def record_salary_payment(request, profile_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    staff_profile = get_object_or_404(UserProfile, id=profile_id, business=business)

    period   = request.POST.get('period', timezone.localdate().strftime('%Y-%m'))
    amount   = request.POST.get('amount', '0').strip()
    method   = request.POST.get('method', 'cash')
    notes    = request.POST.get('notes', '').strip()

    try:
        amount_dec = Decimal(amount)
        if amount_dec <= 0:
            raise ValueError
    except (ValueError, Exception):
        messages.error(request, _('Please enter a valid salary amount.'))
        return redirect('staff_contribution_report')

    # Idempotent: update or create
    today = timezone.localdate()
    last_day = calendar.monthrange(today.year, today.month)[1]
    due_date = date(today.year, today.month, last_day)

    payment, created_flag = SalaryPayment.objects.get_or_create(
        business=business,
        staff=staff_profile,
        period=period,
        defaults={
            'amount': amount_dec,
            'due_date': due_date,
        }
    )
    payment.amount = amount_dec
    payment.paid = True
    payment.paid_at = timezone.now()
    payment.method = method
    payment.notes = notes
    payment.recorded_by = request.user
    payment.save()

    staff_name = staff_profile.user.get_full_name() or staff_profile.user.username

    # SMS the employee: "Your salary for <month> KES X has been paid. Thank you."
    phone = staff_profile.phone
    if phone:
        try:
            from core.notifications import normalize_ke_phone, send_sms_notification
            normalized = normalize_ke_phone(phone)
            month_label = timezone.datetime.strptime(period, '%Y-%m').strftime('%B %Y')
            if normalized:
                msg = (
                    f"{business.name}: Mshahara wako wa {month_label} "
                    f"KES {amount_dec:,.0f} umelipwa. Asante kwa kazi nzuri. 🙏"
                )
                send_sms_notification(msg, normalized)
        except Exception:
            pass

    messages.success(
        request,
        _('Salary of KES %(amount)s marked paid for %(staff)s.')
        % {'amount': f'{amount_dec:,.2f}', 'staff': staff_name}
    )
    return redirect('staff_contribution_report')


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
    contrib = _staff_contribution(user_profile, business, date_from, today)
    salary  = _salary_status(user_profile, business)

    # Payment history: last 6 months
    pay_history = SalaryPayment.objects.filter(
        business=business,
        staff=user_profile,
    ).order_by('-period')[:6]

    return render(request, 'core/haki_kazi_yangu.html', {
        **contrib,
        'salary': salary,
        'pay_history': pay_history,
        'period_label': date_from.strftime('%B %Y'),
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

    # Privacy gate: staff can only see their own statement
    if not user_profile.is_owner and staff_profile.id != user_profile.id:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Huwezi kuona taarifa ya mwenzio.')

    if not getattr(business, 'haki_enabled', True):
        return redirect('home')

    today = timezone.localdate()
    date_from = today.replace(day=1)

    contrib = _staff_contribution(staff_profile, business, date_from, today)
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
                from core.notifications import normalize_ke_phone, send_sms_notification
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
                    send_sms_notification(msg, normalized)
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
