"""
core/debt_views.py
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.models import Customer, CustomerDebtPayment, Transaction
from core.views import get_user_profile, owner_required


def _get_customer_debt_data(customer, business):
    today = timezone.now().date()
    window = business.credit_window_days or 30

    credit_txns = list(
        Transaction.objects.filter(
            business=business,
            recipient=customer.name,
            payment_method='credit',
            type='Issue',
        ).order_by('date').select_related('item')
    )

    payments = list(
        CustomerDebtPayment.objects.filter(
            customer=customer,
            business=business,
        ).order_by('paid_at')
    )

    total_credit_amount = sum(float(t.revenue()) for t in credit_txns)
    total_paid = sum(float(p.amount_paid) for p in payments)
    outstanding = max(0.0, total_credit_amount - total_paid)

    remaining_paid = total_paid
    unpaid_transactions = []

    for txn in credit_txns:
        txn_amount = float(txn.revenue())
        if remaining_paid >= txn_amount:
            remaining_paid -= txn_amount
        elif remaining_paid > 0:
            partial_unpaid = txn_amount - remaining_paid
            remaining_paid = 0
            unpaid_transactions.append({
                'txn': txn,
                'amount': round(partial_unpaid, 2),
                'days_outstanding': (today - txn.date).days,
                'is_overdue': (today - txn.date).days > window,
            })
        else:
            unpaid_transactions.append({
                'txn': txn,
                'amount': round(txn_amount, 2),
                'days_outstanding': (today - txn.date).days,
                'is_overdue': (today - txn.date).days > window,
            })

    aged = {'current': 0.0, 'overdue_30': 0.0, 'overdue_60': 0.0, 'overdue_90': 0.0}
    for entry in unpaid_transactions:
        days = entry['days_outstanding']
        amt  = entry['amount']
        if days <= window:
            aged['current'] += amt
        elif days <= 30:
            aged['overdue_30'] += amt
        elif days <= 60:
            aged['overdue_60'] += amt
        else:
            aged['overdue_90'] += amt
    aged = {k: round(v, 2) for k, v in aged.items()}

    has_overdue = any(e['is_overdue'] for e in unpaid_transactions)

    if not credit_txns:
        score = 'new'
        score_label = _('New — No History')
        score_color = '#888'
        score_pct   = 0
    else:
        completion_rate = (total_paid / total_credit_amount * 100) if total_credit_amount > 0 else 0
        avg_days = _calc_avg_payment_days(customer, business)

        if has_overdue and outstanding > 0:
            score = 'high_risk'
            score_label = _('High Risk')
            score_color = '#f87171'
            score_pct   = max(10, int(completion_rate * 0.4))
        elif completion_rate >= 90 and (avg_days is None or avg_days <= window * 0.6):
            score = 'reliable'
            score_label = _('Reliable')
            score_color = '#6ee7b7'
            score_pct   = min(100, int(70 + completion_rate * 0.3))
        elif completion_rate >= 50:
            score = 'moderate'
            score_label = _('Moderate')
            score_color = '#fbbf24'
            score_pct   = int(40 + completion_rate * 0.3)
        else:
            score = 'high_risk'
            score_label = _('High Risk')
            score_color = '#f87171'
            score_pct   = max(5, int(completion_rate * 0.4))

    effective_window = min(
        customer.expected_payment_days or window,
        window
    )

    return {
        'customer':            customer,
        'outstanding':         round(outstanding, 2),
        'total_credit':        round(total_credit_amount, 2),
        'total_paid':          round(total_paid, 2),
        'unpaid_transactions': unpaid_transactions,
        'payments':            payments,
        'aged':                aged,
        'has_overdue':         has_overdue,
        'score':               score,
        'score_label':         score_label,
        'score_color':         score_color,
        'score_pct':           score_pct,
        'effective_window':    effective_window,
        'global_window':       window,
        'txn_count':           len(credit_txns),
        'payment_count':       len(payments),
    }


def _calc_avg_payment_days(customer, business):
    payments = CustomerDebtPayment.objects.filter(
        customer=customer,
        business=business,
    ).order_by('paid_at')

    if not payments.exists():
        return None

    first_txn = Transaction.objects.filter(
        business=business,
        recipient=customer.name,
        payment_method='credit',
        type='Issue',
    ).order_by('date').first()

    if not first_txn:
        return None

    first_payment = payments.first()
    days = (first_payment.paid_at.date() - first_txn.date).days
    return max(0, days)


@login_required
def debt_dashboard(request):
    user_profile = get_user_profile(request)
    business = user_profile.business
    today = timezone.now().date()
    window = business.credit_window_days or 30

    customers_with_credit = Customer.objects.filter(
        business=business,
    ).prefetch_related('debt_payments')

    dashboard_rows = []
    total_outstanding = 0.0
    total_overdue = 0.0

    for customer in customers_with_credit:
        data = _get_customer_debt_data(customer, business)
        if data['outstanding'] > 0 or data['txn_count'] > 0:
            dashboard_rows.append(data)
            total_outstanding += data['outstanding']
            if data['has_overdue']:
                total_overdue += data['outstanding']

    dashboard_rows.sort(key=lambda x: (-int(x['has_overdue']), -x['outstanding']))

    return render(request, 'core/debt_dashboard.html', {
        'rows':              dashboard_rows,
        'total_outstanding': round(total_outstanding, 2),
        'total_overdue':     round(total_overdue, 2),
        'customer_count':    len([r for r in dashboard_rows if r['outstanding'] > 0]),
        'credit_window':     window,
        'today':             today.strftime('%B %d, %Y'),
    })


@login_required
def customer_debt_profile(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    is_owner = user_profile.is_owner

    customer = get_object_or_404(Customer, id=customer_id, business=business)
    data = _get_customer_debt_data(customer, business)

    return render(request, 'core/customer_debt_profile.html', {
        **data,
        'is_owner':    is_owner,
        'today':       timezone.now().date().isoformat(),
        'today_label': timezone.now().date().strftime('%B %d, %Y'),
        'payment_methods': CustomerDebtPayment.PAYMENT_METHOD_CHOICES,
    })


@login_required
@require_POST
def record_debt_payment(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    amount_raw = request.POST.get('amount_paid', '').strip()
    method     = request.POST.get('payment_method', 'cash')
    notes      = request.POST.get('notes', '').strip()

    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise ValueError('Amount must be positive')
    except (InvalidOperation, ValueError):
        messages.error(request, _('Please enter a valid payment amount.'))
        return redirect('customer_debt_profile', customer_id=customer_id)

    # Snapshot debt data BEFORE recording payment — needed for receipt lines
    data = _get_customer_debt_data(customer, business)
    if amount > Decimal(str(data['outstanding'])):
        messages.error(
            request,
            _('Payment of KES %(amount)s exceeds outstanding balance of KES %(outstanding)s.')
            % {'amount': f'{amount:,.2f}', 'outstanding': f"{data['outstanding']:,.2f}"}
        )
        return redirect('customer_debt_profile', customer_id=customer_id)

    unpaid_before   = data['unpaid_transactions']
    score_label     = data.get('score_label', '')
    method_label    = 'M-Pesa' if method == 'mpesa' else 'Cash'
    recorder        = request.user.get_full_name() or request.user.username

    CustomerDebtPayment.objects.create(
        customer=customer,
        business=business,
        amount_paid=amount,
        payment_method=method,
        notes=notes,
        recorded_by=request.user,
    )

    # Build receipt lines: show each original credit transaction being cleared (FIFO)
    receipt_lines = []
    paid_remaining = float(amount)
    max_days = 0
    for entry in unpaid_before:
        if paid_remaining <= 0:
            break
        txn = entry['txn']
        covered = round(min(entry['amount'], paid_remaining), 2)
        paid_remaining = round(paid_remaining - covered, 2)
        max_days = max(max_days, entry['days_outstanding'])
        receipt_lines.append({
            'name': f"{txn.item.description} — deni la {txn.date.strftime('%d %b %Y')}",
            'qty': 1,
            'subtotal': covered,
        })
    if not receipt_lines:
        receipt_lines.append({'name': notes or 'Malipo ya deni', 'qty': 1, 'subtotal': float(amount)})

    # Metadata line (subtotal=0, hidden in template)
    days_label = f"{max_days} siku" if max_days > 0 else 'siku moja'
    receipt_lines.append({
        'name': f"Malipo: {method_label} · {days_label} za kulipa · {score_label} · alirekodiwa na {recorder}",
        'qty': 0,
        'subtotal': 0,
    })

    # Issue receipt and redirect straight to it
    receipt_token = None
    try:
        from .models import Receipt
        rcpt = Receipt.issue(
            business=business,
            lines=receipt_lines,
            payment_method=method,
            user=request.user,
            customer_name=customer.name,
            customer_phone=customer.phone or '',
        )
        receipt_token = rcpt.token
        receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
        # Auto-SMS the payment confirmation to the customer
        if customer.phone:
            from .notifications import normalize_ke_phone, send_sms_notification
            normalized = normalize_ke_phone(customer.phone)
            if normalized:
                sms_msg = (
                    f"{business.name}: Deni lako limelipiwa!\n"
                    f"KES {amount:,.0f} ({method_label}) — {days_label} za kulipa\n"
                    f"Alama ya mikopo: {score_label}\n"
                    f"Risiti: {receipt_url}"
                )
                send_sms_notification(sms_msg, normalized)
    except Exception:
        pass

    messages.success(
        request,
        _('Payment of KES %(amount)s recorded for %(customer)s.')
        % {'amount': f'{amount:,.2f}', 'customer': customer.name}
    )
    if receipt_token:
        return redirect('public_receipt', token=receipt_token)
    return redirect('customer_debt_profile', customer_id=customer_id)


@login_required
@require_POST
def send_debt_reminder(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    customer = get_object_or_404(Customer, id=customer_id, business=business)
    data = _get_customer_debt_data(customer, business)

    if data['outstanding'] <= 0:
        messages.info(request, _('%(customer)s has no outstanding balance.') % {'customer': customer.name})
        return redirect('customer_debt_profile', customer_id=customer_id)

    if not customer.phone:
        messages.error(request, _('%(customer)s does not have a phone number on file.') % {'customer': customer.name})
        return redirect('customer_debt_profile', customer_id=customer_id)

    from core.notifications import normalize_ke_phone, send_sms_notification
    normalized_phone = normalize_ke_phone(customer.phone)
    if not normalized_phone:
        messages.error(request, _('Could not send reminder — invalid phone number format: %(phone)s') % {'phone': customer.phone})
        return redirect('customer_debt_profile', customer_id=customer_id)

    outstanding_str = f"KES {data['outstanding']:,.0f}"
    window = business.credit_window_days or 30
    msg = (
        f"{business.name}: {customer.name}, bado una deni la {outstanding_str}. "
        f"Tafadhali lipa ndani ya siku {window}. Asante."
    )

    ok, _ = send_sms_notification(msg, normalized_phone)
    if ok:
        messages.success(
            request,
            _('Reminder sent to %(customer)s (%(phone)s).')
            % {'customer': customer.name, 'phone': customer.phone}
        )
    else:
        messages.warning(
            request,
            _('Reminder could not be sent to %(phone)s — check your Africa\'s Talking account balance and settings.')
            % {'phone': customer.phone}
        )

    return redirect('customer_debt_profile', customer_id=customer_id)


@login_required
@owner_required
@require_POST
def toggle_credit_approval(request, customer_id):
    user_profile = get_user_profile(request)
    customer = get_object_or_404(Customer, id=customer_id, business=user_profile.business)
    customer.credit_approved = not customer.credit_approved
    customer.save(update_fields=['credit_approved'])

    status = _('approved') if customer.credit_approved else _('revoked')
    messages.success(
        request,
        _('Credit %(status)s for %(customer)s.')
        % {'status': status, 'customer': customer.name}
    )
    return redirect('customer_debt_profile', customer_id=customer_id)


@login_required
@owner_required
@require_POST
def update_customer_credit_settings(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    window = business.credit_window_days or 30

    epd_raw = request.POST.get('expected_payment_days', '').strip()
    if epd_raw:
        try:
            epd = int(epd_raw)
            if epd > window:
                messages.error(
                    request,
                    _('Expected payment days (%(epd)s) cannot exceed the business credit window (%(window)s days).')
                    % {'epd': epd, 'window': window}
                )
                return redirect('customer_debt_profile', customer_id=customer_id)
            customer.expected_payment_days = epd
        except ValueError:
            pass
    else:
        customer.expected_payment_days = None

    cl_raw = request.POST.get('credit_limit', '').strip()
    if cl_raw:
        try:
            customer.credit_limit = Decimal(cl_raw)
        except InvalidOperation:
            pass
    else:
        customer.credit_limit = None

    customer.save(update_fields=['expected_payment_days', 'credit_limit'])
    messages.success(request, _('Credit settings updated for %(customer)s.') % {'customer': customer.name})
    return redirect('customer_debt_profile', customer_id=customer_id)
