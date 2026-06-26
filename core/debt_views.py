"""
core/debt_views.py — Sprint K1: source-scoped debt sub-ledgers.

Kitchen-only staff see/settle only kitchen-origin credit.
Bar/general staff see/settle only bar-origin credit.
Owner (and cross-authorised staff) see both ledgers as separate sections.
Discriminator: Transaction.item.store.is_kitchen == True → kitchen; False → bar.
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


# ── Scope helper ─────────────────────────────────────────────────────────────

def _debt_scope(profile, business):
    """Return 'bar', 'kitchen', or 'all' for the current user.

    'all' = owner or cross-authorised staff (sees both sub-ledgers as two sections).
    'kitchen' = kitchen-only staff (can_access_kitchen and NOT can_access_bar).
    'bar' = everyone else (bar/general staff, no kitchen access).
    """
    if not getattr(business, 'has_kitchen', False):
        return 'all'
    if profile.is_owner:
        return 'all'
    if profile.can_access_bar and profile.can_access_kitchen:
        return 'all'
    if profile.is_kitchen_staff or (profile.can_access_kitchen and not profile.can_access_bar):
        return 'kitchen'
    return 'bar'


# ── Core data helper ──────────────────────────────────────────────────────────

def _get_customer_debt_data(customer, business, scope='all'):
    """Compute debt data for one customer, optionally filtered to a sub-ledger.

    scope='bar'     → only bar-origin credit txns + bar-tagged payments
    scope='kitchen' → only kitchen-origin txns + kitchen-tagged payments
    scope='all'     → entire ledger (owner view / businesses without kitchen)
    """
    today = timezone.now().date()
    window = business.credit_window_days or 30

    credit_qs = Transaction.objects.filter(
        business=business,
        recipient=customer.name,
        payment_method='credit',
        type='Issue',
    ).order_by('date').select_related('item__store')

    payment_qs = CustomerDebtPayment.objects.filter(
        customer=customer,
        business=business,
    ).order_by('paid_at')

    if scope == 'kitchen':
        credit_qs = credit_qs.filter(item__store__is_kitchen=True)
        payment_qs = payment_qs.filter(source='kitchen')
    elif scope == 'bar':
        credit_qs = credit_qs.filter(item__store__is_kitchen=False)
        payment_qs = payment_qs.filter(source='bar')

    credit_txns = list(credit_qs)
    payments    = list(payment_qs)

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
        avg_days = _calc_avg_payment_days(customer, business, scope)

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


def _calc_avg_payment_days(customer, business, scope='all'):
    payment_qs = CustomerDebtPayment.objects.filter(
        customer=customer,
        business=business,
    ).order_by('paid_at')

    txn_qs = Transaction.objects.filter(
        business=business,
        recipient=customer.name,
        payment_method='credit',
        type='Issue',
    ).select_related('item__store')

    if scope == 'kitchen':
        payment_qs = payment_qs.filter(source='kitchen')
        txn_qs = txn_qs.filter(item__store__is_kitchen=True)
    elif scope == 'bar':
        payment_qs = payment_qs.filter(source='bar')
        txn_qs = txn_qs.filter(item__store__is_kitchen=False)

    if not payment_qs.exists():
        return None

    first_txn = txn_qs.order_by('date').first()
    if not first_txn:
        return None

    first_payment = payment_qs.first()
    days = (first_payment.paid_at.date() - first_txn.date).days
    return max(0, days)


# ── Views ─────────────────────────────────────────────────────────────────────

@login_required
def debt_dashboard(request):
    user_profile = get_user_profile(request)
    business = user_profile.business
    today = timezone.now().date()
    window = business.credit_window_days or 30
    scope = _debt_scope(user_profile, business)

    customers_with_credit = Customer.objects.filter(
        business=business,
    ).prefetch_related('debt_payments')

    dashboard_rows = []
    total_outstanding = 0.0
    total_overdue = 0.0

    for customer in customers_with_credit:
        data = _get_customer_debt_data(customer, business, scope)
        if data['outstanding'] > 0 or data['txn_count'] > 0:
            dashboard_rows.append(data)
            total_outstanding += data['outstanding']
            if data['has_overdue']:
                total_overdue += data['outstanding']

    dashboard_rows.sort(key=lambda x: (-int(x['has_overdue']), -x['outstanding']))

    scope_label = {'bar': 'Bar', 'kitchen': 'Kitchen', 'all': 'All'}.get(scope, 'All')

    return render(request, 'core/debt_dashboard.html', {
        'rows':              dashboard_rows,
        'total_outstanding': round(total_outstanding, 2),
        'total_overdue':     round(total_overdue, 2),
        'customer_count':    len([r for r in dashboard_rows if r['outstanding'] > 0]),
        'credit_window':     window,
        'today':             today.strftime('%B %d, %Y'),
        'scope':             scope,
        'scope_label':       scope_label,
    })


@login_required
def customer_debt_profile(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    is_owner = user_profile.is_owner
    scope = _debt_scope(user_profile, business)

    customer = get_object_or_404(Customer, id=customer_id, business=business)

    if scope == 'all':
        # Owner sees two separate sub-ledger sections plus a combined total
        bar_data     = _get_customer_debt_data(customer, business, scope='bar')
        kitchen_data = _get_customer_debt_data(customer, business, scope='kitchen')
        data         = _get_customer_debt_data(customer, business, scope='all')
        data['bar_data']     = bar_data
        data['kitchen_data'] = kitchen_data
        has_kitchen = getattr(business, 'has_kitchen', False)
    else:
        data = _get_customer_debt_data(customer, business, scope)
        has_kitchen = False  # non-owner scoped view is single-ledger

    from core.credit_policy import get_credit_standing
    credit_standing = get_credit_standing(business, customer, scope=scope)

    return render(request, 'core/customer_debt_profile.html', {
        **data,
        'is_owner':       is_owner,
        'scope':          scope,
        'has_kitchen':    has_kitchen,
        'today':          timezone.now().date().isoformat(),
        'today_label':    timezone.now().date().strftime('%B %d, %Y'),
        'payment_methods': CustomerDebtPayment.PAYMENT_METHOD_CHOICES,
        'credit_standing': credit_standing,
    })


@login_required
@require_POST
def record_debt_payment(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    scope = _debt_scope(user_profile, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    # K5.E — shift gate: staff must have an open shift to record debt payments
    if not user_profile.is_owner:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(user_profile, business) is False:
            messages.error(request, _('Fungua shift yako kwanza kabla ya kurekodi malipo ya deni.'))
            return redirect('customer_debt_profile', customer_id=customer_id)

    amount_raw = request.POST.get('amount_paid', '').strip()
    method     = request.POST.get('payment_method', 'cash')
    notes      = request.POST.get('notes', '').strip()

    # Owner must specify which sub-ledger they're settling
    if scope == 'all':
        debt_source = request.POST.get('debt_source', 'bar')
        if debt_source not in ('bar', 'kitchen'):
            messages.error(request, _('Please specify whether this payment is for Bar or Kitchen debt.'))
            return redirect('customer_debt_profile', customer_id=customer_id)
        payment_scope = debt_source
    else:
        payment_scope = scope

    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise ValueError('Amount must be positive')
    except (InvalidOperation, ValueError):
        messages.error(request, _('Please enter a valid payment amount.'))
        return redirect('customer_debt_profile', customer_id=customer_id)

    # Validate against the SCOPED outstanding balance
    data = _get_customer_debt_data(customer, business, payment_scope)
    if amount > Decimal(str(data['outstanding'])):
        messages.error(
            request,
            _('Payment of KES %(amount)s exceeds the %(scope)s outstanding balance of KES %(outstanding)s.')
            % {
                'amount': f'{amount:,.2f}',
                'scope': payment_scope.capitalize(),
                'outstanding': f"{data['outstanding']:,.2f}",
            }
        )
        return redirect('customer_debt_profile', customer_id=customer_id)

    unpaid_before = data['unpaid_transactions']
    method_label  = 'M-Pesa' if method == 'mpesa' else 'Cash'
    recorder      = request.user.get_full_name() or request.user.username

    CustomerDebtPayment.objects.create(
        customer=customer,
        business=business,
        amount_paid=amount,
        payment_method=method,
        source=payment_scope,
        notes=notes,
        recorded_by=request.user,
    )

    # Recompute score AFTER payment
    post_data       = _get_customer_debt_data(customer, business, payment_scope)
    score_label     = post_data.get('score_label', '')
    effective_window = post_data.get('effective_window', business.credit_window_days or 30)

    # Stamp last_cleared_at when outstanding drops to zero
    if float(post_data['outstanding']) == 0:
        from django.utils import timezone as _tz
        Customer.objects.filter(pk=customer.pk).update(last_cleared_at=_tz.now())

    # Build receipt lines: FIFO coverage of unpaid transactions
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

    # Remaining balance — use post_data (recomputed after payment was recorded)
    remaining_balance = round(max(0.0, float(post_data['outstanding'])), 2)
    if remaining_balance > 0:
        receipt_lines.append({
            'name': 'Bado unalipa',
            'qty': -1,
            'subtotal': remaining_balance,
        })

    # Days label
    if max_days == 0:
        days_label = 'umelipa leo'
    elif max_days == 1:
        days_label = 'umelipa siku 1 baadaye'
    else:
        days_label = f'umelipa siku {max_days} baadaye'
    window_label = f'kiwango siku {effective_window}'

    receipt_lines.append({
        'name': (
            f"Malipo: {method_label} · {payment_scope.capitalize()} · {days_label} ({window_label})"
            f" · {score_label} · alirekodiwa na {recorder}"
        ),
        'qty': 0,
        'subtotal': 0,
    })

    receipt_token = None
    try:
        from .models import Receipt
        receipt_meta = {
            'credit_score': post_data.get('score', 'new'),
            'score_label': str(post_data.get('score_label', '')),
            'score_color': post_data.get('score_color', '#888'),
            'outstanding': float(post_data.get('outstanding', 0)),
            'scope': payment_scope,
        }
        rcpt = Receipt.issue(
            business=business,
            lines=receipt_lines,
            payment_method=method,
            user=request.user,
            customer_name=customer.name,
            customer_phone=customer.phone or '',
            meta=receipt_meta,
        )
        receipt_token = rcpt.token
        receipt_url = request.build_absolute_uri(f'/r/{rcpt.token}/')
        if customer.phone:
            from .notifications import normalize_ke_phone, send_sms_notification
            normalized = normalize_ke_phone(customer.phone)
            if normalized:
                sms_msg = (
                    f"{business.name}: Deni lako limelipiwa!\n"
                    f"KES {amount:,.0f} ({method_label}) — {days_label} ({window_label})\n"
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
    scope = _debt_scope(user_profile, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    # K5.E — shift gate: staff must have an open shift to send debt reminders
    if not user_profile.is_owner:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(user_profile, business) is False:
            messages.error(request, _('Fungua shift yako kwanza kabla ya kutuma kikumbusha.'))
            return redirect('customer_debt_profile', customer_id=customer_id)

    data = _get_customer_debt_data(customer, business, scope)

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

    ok, _detail = send_sms_notification(msg, normalized_phone)
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


# ── K4: Receipt meta helpers + statement view ─────────────────────────────────

def _build_credit_receipt_meta(business, customer, scope, when=None):
    """Build the meta dict for a credit/tab receipt (K4.2 + K4.3).

    Call AFTER the credit transactions have been written to DB so
    _get_customer_debt_data reflects the updated outstanding.
    """
    from datetime import timedelta
    from django.utils import timezone as _tz
    from core.credit_policy import evaluate_credit

    data = _get_customer_debt_data(customer, business, scope)
    window = data['effective_window']

    if data['unpaid_transactions']:
        oldest_date = data['unpaid_transactions'][0]['txn'].date
        due_date_str = (oldest_date + timedelta(days=window)).strftime('%d %b %Y')
    else:
        due_date_str = (_tz.localdate() + timedelta(days=window)).strftime('%d %b %Y')

    try:
        decision = evaluate_credit(business, customer, scope=scope, when=when)
        warn = decision.tier == 'warn'
        warn_msg = (
            'Onyo: ukichelewa kulipa deni hili, hutaweza kupata deni tena hadi ulipe.'
            if warn else ''
        )
    except Exception:
        warn = False
        warn_msg = ''

    return {
        'credit_score': data.get('score', 'new'),
        'score_label': str(data.get('score_label', '')),
        'score_color': data.get('score_color', '#888'),
        'outstanding': float(data.get('outstanding', 0)),
        'due_date': due_date_str,
        'scope': scope,
        'warn': warn,
        'warn_msg': warn_msg,
    }


@login_required
@require_POST
def customer_debt_statement(request, customer_id):
    """Generate a scoped debt statement receipt and redirect to its public URL.

    Privacy: _debt_scope() gates kitchen-only staff to their ledger only.
    """
    up = get_user_profile(request)
    if not up:
        return redirect('login')
    business = up.business
    scope = _debt_scope(up, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    data = _get_customer_debt_data(customer, business, scope)

    if data['outstanding'] <= 0:
        messages.info(
            request,
            _('%(customer)s hana deni kwa sasa.') % {'customer': customer.name}
        )
        return redirect('customer_debt_profile', customer_id=customer_id)

    from datetime import timedelta
    from django.utils import timezone as _tz

    window = data['effective_window']
    today = _tz.localdate()

    lines = []
    for entry in data['unpaid_transactions']:
        txn = entry['txn']
        overdue_tag = ' ✗' if entry['is_overdue'] else ''
        lines.append({
            'name': (
                f"{txn.item.description} — {txn.date.strftime('%d %b %Y')}"
                f" · siku {entry['days_outstanding']}{overdue_tag}"
            ),
            'qty': 1,
            'subtotal': entry['amount'],
        })

    if data['unpaid_transactions']:
        oldest_date = data['unpaid_transactions'][0]['txn'].date
        due_date_str = (oldest_date + timedelta(days=window)).strftime('%d %b %Y')
    else:
        due_date_str = (today + timedelta(days=window)).strftime('%d %b %Y')

    lines.append({
        'name': f"Jumla: KES {data['outstanding']:,.0f} · Lipa kabla {due_date_str}",
        'qty': 0,
        'subtotal': 0,
    })

    meta = {
        'is_statement': True,
        'credit_score': data.get('score', 'new'),
        'score_label': str(data.get('score_label', '')),
        'score_color': data.get('score_color', '#888'),
        'outstanding': float(data['outstanding']),
        'due_date': due_date_str,
        'scope': scope,
        'aged': data.get('aged', {}),
        'warn': False,
        'warn_msg': '',
    }

    from .models import Receipt
    rcpt = Receipt.issue(
        business=business,
        lines=lines,
        payment_method='statement',
        user=request.user,
        customer_name=customer.name,
        customer_phone=customer.phone or '',
        source=scope if scope != 'all' else '',
        meta=meta,
    )
    return redirect('public_receipt', token=rcpt.token)


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
