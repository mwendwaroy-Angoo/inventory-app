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

from core.models import Customer, CustomerDebtPayment, SalaryDeduction, Transaction, WriteOffRequest
from core.views import get_user_profile, owner_required, owner_or_manager_required, _station_scope


# ── Scope helper ─────────────────────────────────────────────────────────────

def _debt_scope(profile, business):
    """Return 'bar', 'kitchen', or 'all' for the current user.

    'all' = owner or cross-authorised staff (sees both sub-ledgers as two sections).
    'kitchen' = kitchen-only staff (can_access_kitchen and NOT can_access_bar).
    'bar' = everyone else (bar/general staff, no kitchen access).
    """
    if not getattr(business, 'has_kitchen', False):
        return 'all'
    if profile.is_owner_or_manager:
        return 'all'
    if profile.can_access_bar and profile.can_access_kitchen:
        return 'all'
    if profile.is_kitchen_staff or (profile.can_access_kitchen and not profile.can_access_bar):
        return 'kitchen'
    return 'bar'


# ── Core data helper ──────────────────────────────────────────────────────────

def _txn_transfer_note(txn):
    """Reason-note for a debt transaction that traces back to a rejected/
    cancelled split-bill transfer (BarTabEntry.transfer_reason_note(), core/
    models.py) — e.g. "Ilikuwa itafunikwa na Bosco, alikataa kulipa" so a
    customer scanning their own QR (or the owner reading the debt ledger)
    can see WHY this specific amount became debt, not just a bare line item
    (2026-07-24 live request). Returns '' for a transaction with no
    BarTabEntry at all (e.g. a direct Quick Sell credit sale, never on a
    tab) or with no such history — the ordinary case.
    """
    try:
        entry = txn.tab_entry
    except Exception:
        return ''
    return entry.transfer_reason_note()


def _get_customer_debt_data(customer, business, scope='all'):
    """Compute debt data for one customer, optionally filtered to a sub-ledger.

    scope='bar'     → only bar-origin credit txns + bar-tagged payments
    scope='kitchen' → only kitchen-origin txns + kitchen-tagged payments
    scope='all'     → entire ledger (owner view / businesses without kitchen)
    """
    today = timezone.localdate()
    window = business.credit_window_days or 30

    credit_qs = Transaction.objects.filter(
        business=business,
        recipient=customer.name,
        payment_method='credit',
        type='Issue',
    ).exclude(
        # Transactions linked to an OPEN tab are tab charges, not standalone debt.
        # They enter the debt ledger only after the tab is settled as credit / converted.
        tab_entry__tab__status='OPEN',
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
                'transfer_note': _txn_transfer_note(txn),
            })
        else:
            unpaid_transactions.append({
                'txn': txn,
                'amount': round(txn_amount, 2),
                'days_outstanding': (today - txn.date).days,
                'is_overdue': (today - txn.date).days > window,
                'transfer_note': _txn_transfer_note(txn),
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
        elif not payments:
            # Credit transactions exist (e.g. open tab entries) but no debt payments
            # recorded yet — treat as new rather than high_risk; the customer has no
            # established payment behaviour in our system yet.
            score = 'new'
            score_label = _('New — No History')
            score_color = '#888'
            score_pct   = 0
        else:
            # completion_rate < 50% but no overdue items yet — new/partial payer.
            # high_risk fires only when there are OVERDUE items (line 132 above).
            # A partial first payment on a fresh tab should not brand the customer
            # as high_risk before their window has even elapsed.
            score = 'moderate'
            score_label = _('Moderate')
            score_color = '#fbbf24'
            score_pct   = max(5, int(completion_rate * 0.3))

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
    today = timezone.localdate()
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
    is_owner = user_profile.is_owner_or_manager
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

    has_daraja = bool(
        getattr(business, 'daraja_consumer_key', None)
        and getattr(business, 'daraja_consumer_secret', None)
        and (getattr(business, 'mpesa_till', None) or getattr(business, 'mpesa_paybill', None))
    )

    # Annotate each unpaid entry with its write-off request (if any) for the template
    all_txn_ids = [entry['txn'].id for entry in data.get('unpaid_transactions', [])]
    if all_txn_ids:
        wo_map = {}
        for wo_obj in WriteOffRequest.objects.filter(
            transaction_id__in=all_txn_ids,
        ).select_related('requested_by', 'manager_by', 'reviewed_by'):
            wo_map[wo_obj.transaction_id] = wo_obj
        for entry in data.get('unpaid_transactions', []):
            entry['write_off'] = wo_map.get(entry['txn'].id)

    pending_wo_count = WriteOffRequest.objects.filter(
        transaction__business=business,
        status=WriteOffRequest.STATUS_PENDING,
    ).count() if is_owner else 0

    return render(request, 'core/customer_debt_profile.html', {
        **data,
        'is_owner':        is_owner,
        'scope':           scope,
        'has_kitchen':     has_kitchen,
        'has_daraja':      has_daraja,
        'today':           timezone.now().date().isoformat(),
        'today_label':     timezone.now().date().strftime('%B %d, %Y'),
        'payment_methods': CustomerDebtPayment.PAYMENT_METHOD_CHOICES,
        'credit_standing': credit_standing,
        'pending_wo_count': pending_wo_count,
    })


def _do_settle_debt_payment(customer, business, amount, payment_method, source,
                             notes='', recorded_by=None,
                             site_url='https://www.dukamwecheche.co.ke'):
    """Create CustomerDebtPayment + FIFO reconciliation + issue receipt + SMS.

    Shared by record_debt_payment (HTTP view) and _settle_debt_customer_from_payment
    (M-Pesa callback). Returns (receipt, post_data) on success, raises on fatal error.
    post_data is _get_customer_debt_data recomputed AFTER the payment is recorded.
    """
    from .models import BarTabEntry, BarTab, Receipt
    from .notifications import normalize_ke_phone, send_sms_notification

    amount = Decimal(str(amount))
    data = _get_customer_debt_data(customer, business, source)
    unpaid_before = data['unpaid_transactions']
    method_label = 'M-Pesa' if payment_method == 'mpesa' else 'Cash'
    recorder = ''
    if recorded_by:
        recorder = recorded_by.get_full_name() or recorded_by.username

    CustomerDebtPayment.objects.create(
        customer=customer,
        business=business,
        amount_paid=amount,
        payment_method=payment_method,
        source=source,
        notes=notes,
        recorded_by=recorded_by,
    )

    # FIFO BarTabEntry reconciliation — only flip is_paid when fully covered
    try:
        now = timezone.now()
        settled_tab_ids = list(BarTab.objects.filter(
            business=business, customer=customer, status='SETTLED',
        ).values_list('id', flat=True))
        if settled_tab_ids:
            paid_remaining = float(amount)
            for entry in unpaid_before:
                if paid_remaining <= 0:
                    break
                txn = entry['txn']
                entry_amount = float(entry['amount'])
                covered = round(min(entry_amount, paid_remaining), 2)
                paid_remaining = round(paid_remaining - covered, 2)
                if covered >= entry_amount:
                    BarTabEntry.objects.filter(
                        tab__id__in=settled_tab_ids,
                        transaction=txn,
                        is_paid=False,
                    ).update(is_paid=True, paid_at=now, payment_method=payment_method)
    except Exception:
        pass

    # Recompute score AFTER payment
    post_data = _get_customer_debt_data(customer, business, source)
    score_label = post_data.get('score_label', '')
    effective_window = post_data.get('effective_window', business.credit_window_days or 30)

    # Stamp last_cleared_at when debt hits zero
    if float(post_data['outstanding']) == 0:
        Customer.objects.filter(pk=customer.pk).update(last_cleared_at=timezone.now())

    # Build FIFO receipt lines
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

    remaining_balance = round(max(0.0, float(post_data['outstanding'])), 2)
    if remaining_balance > 0:
        receipt_lines.append({'name': 'Bado unalipa', 'qty': -1, 'subtotal': remaining_balance})

    if max_days == 0:
        days_label = 'umelipa leo'
    elif max_days == 1:
        days_label = 'umelipa siku 1 baadaye'
    else:
        days_label = f'umelipa siku {max_days} baadaye'
    window_label = f'kiwango siku {effective_window}'

    recorder_suffix = f' · alirekodiwa na {recorder}' if recorder else ''
    receipt_lines.append({
        'name': (
            f"Malipo: {method_label} · {source.capitalize()} · {days_label} ({window_label})"
            f" · {score_label}{recorder_suffix}"
        ),
        'qty': 0,
        'subtotal': 0,
    })

    receipt_meta = {
        'credit_score': post_data.get('score', 'new'),
        'score_label': str(post_data.get('score_label', '')),
        'score_color': post_data.get('score_color', '#888'),
        'outstanding': float(post_data.get('outstanding', 0)),
        'scope': source,
    }
    rcpt = Receipt.issue(
        business=business,
        lines=receipt_lines,
        payment_method=payment_method,
        user=recorded_by,
        customer_name=customer.name,
        customer_phone=customer.phone or '',
        meta=receipt_meta,
    )

    try:
        if customer.phone:
            normalized = normalize_ke_phone(customer.phone)
            if normalized:
                receipt_url = f"{site_url}/r/{rcpt.token}/"
                sms_msg = (
                    f"{business.name}: Deni lako limelipiwa!\n"
                    f"KES {amount:,.0f} ({method_label}) — {days_label} ({window_label})\n"
                    f"Alama ya mikopo: {score_label}\n"
                    f"Risiti: {receipt_url}"
                )
                send_sms_notification(sms_msg, normalized)
    except Exception:
        pass

    return rcpt, post_data


@login_required
@require_POST
def record_debt_payment(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    scope = _debt_scope(user_profile, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    # K5.E — shift gate: staff must have an open shift to record debt payments
    if not user_profile.is_owner_or_manager:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(user_profile, business) is False:
            messages.error(request, _('Fungua shift yako kwanza kabla ya kurekodi malipo ya deni.'))
            return redirect('customer_debt_profile', customer_id=customer_id)

    amount_raw = request.POST.get('amount_paid', '').strip()
    method     = request.POST.get('payment_method', 'cash')
    notes      = request.POST.get('notes', '').strip()

    # Server-side double-submit backstop — see core/idempotency.py. This is a
    # real <form> POST/redirect (no AJAX guard), so a double-click on "Record
    # Payment" or a back-button resubmission would otherwise create a second,
    # real CustomerDebtPayment for the same cash/mpesa payment.
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        messages.info(request, _('Malipo haya tayari yamerekodiwa.'))
        return redirect('customer_debt_profile', customer_id=customer_id)

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

    site_url = request.build_absolute_uri('/')[:-1]
    try:
        rcpt, post_data = _do_settle_debt_payment(
            customer=customer, business=business,
            amount=amount, payment_method=method,
            source=payment_scope, notes=notes,
            recorded_by=request.user, site_url=site_url,
        )
        messages.success(
            request,
            _('Payment of KES %(amount)s recorded for %(customer)s.')
            % {'amount': f'{amount:,.2f}', 'customer': customer.name}
        )
        return redirect('public_receipt', token=rcpt.token)
    except Exception:
        messages.error(request, _('An error occurred recording the payment. Please try again.'))
        return redirect('customer_debt_profile', customer_id=customer_id)


@login_required
@require_POST
def debt_stk_push(request, customer_id):
    """Staff initiates STK Push to collect debt payment from the customer's phone.

    POST params: amount, phone, source ('bar'|'kitchen')
    Returns JSON: {ok, payment_id, amount} or {error}.
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    scope = _debt_scope(user_profile, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    if not user_profile.is_owner_or_manager:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(user_profile, business) is False:
            return JsonResponse({'error': 'Fungua shift yako kwanza.'}, status=403)

    amount_raw = request.POST.get('amount', '').strip()
    phone = (request.POST.get('phone', '').strip() or customer.phone or '').strip()

    if scope == 'all':
        payment_scope = request.POST.get('source', 'bar')
        if payment_scope not in ('bar', 'kitchen'):
            payment_scope = 'bar'
    else:
        payment_scope = scope

    try:
        amount = int(float(amount_raw))
        if amount < 1:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Weka kiasi sahihi cha kulipwa.'}, status=400)

    if not phone:
        return JsonResponse({'error': 'Weka nambari ya simu ya M-Pesa ya mteja.'}, status=400)

    # Server-side double-submit backstop — see core/idempotency.py. This is the
    # one STK-initiation entry point in the app with no prior client-side
    # button-disable guard at all; without this, a rapid double-tap on "Send
    # STK" fires two separate STK Push prompts to the customer's phone for the
    # same debt, and if the customer approves both, that's a real double-charge
    # — not just a duplicate record.
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'error': 'STK Push hii tayari imetumwa.', 'duplicate': True}, status=409)

    data = _get_customer_debt_data(customer, business, payment_scope)
    if amount > float(data['outstanding']):
        return JsonResponse(
            {'error': f'Kiasi cha KES {amount:,} kinazidi deni la KES {data["outstanding"]:,.0f}.'},
            status=400,
        )

    from .mpesa import resolve_mpesa_config, initiate_stk_push, format_phone_ke
    from .models import Payment, Store

    target_store = None
    if payment_scope == 'kitchen':
        target_store = Store.objects.filter(
            business=business, is_kitchen=True, has_own_mpesa=True
        ).first()

    cfg = resolve_mpesa_config(business, target_store)
    shortcode = (cfg.get('till') or cfg.get('paybill') or '').strip()
    if not shortcode:
        return JsonResponse({'error': 'Hakuna M-Pesa iliyosakinishwa. Wasiliana na mmiliki.'}, status=400)

    phone_fmt = format_phone_ke(phone)
    callback_url = request.build_absolute_uri('/mpesa/callback/')

    result = initiate_stk_push(
        phone_number=phone_fmt,
        amount=amount,
        account_reference=f"DENI-{customer.id}",
        description="Duka Mwecheche",
        callback_url=callback_url,
        consumer_key=cfg.get('consumer_key') or None,
        consumer_secret=cfg.get('consumer_secret') or None,
        shortcode=shortcode,
        passkey=cfg.get('passkey') or None,
        use_till=bool(cfg.get('till')),
        env=cfg.get('environment', 'sandbox'),
    )

    if not result or result.get('ResponseCode') != '0':
        err = result.get('ResponseDescription', 'STK Push imeshindwa') if result else 'Hakuna jibu kutoka kwa Safaricom'
        return JsonResponse({'error': err}, status=400)

    payment = Payment.objects.create(
        business=business,
        store=cfg.get('store'),
        source=payment_scope,
        debt_customer=customer,
        amount=amount,
        method='mpesa',
        status='pending',
        phone=phone_fmt,
        checkout_request_id=result.get('CheckoutRequestID', ''),
        merchant_request_id=result.get('MerchantRequestID', ''),
    )

    return JsonResponse({'ok': True, 'payment_id': payment.id, 'amount': amount})


@login_required
@require_POST
def send_debt_reminder(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    scope = _debt_scope(user_profile, business)
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    # K5.E — shift gate: staff must have an open shift to send debt reminders
    if not user_profile.is_owner_or_manager:
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

    # Find the most recent live-receipt (tab receipt) for this customer so they
    # can pay directly from the SMS link without visiting the business in person.
    from .models import Receipt as _Rcpt
    from .receipt_views import _receipt_all_tab_ids
    pay_link_suffix = ''
    all_cust_receipts = _Rcpt.objects.filter(
        business=business, customer_name=customer.name,
    ).exclude(payment_method='statement').order_by('-created_at')
    latest_tab_rcpt = None
    for _r in all_cust_receipts[:10]:
        # A receipt is payable even without its own meta.tab_id if a tab was
        # cross-linked into it (resolve_master_receipt Priority 2/3/4) — check
        # both, not just tab_id, so a valid pay link isn't skipped.
        if _receipt_all_tab_ids(_r):
            latest_tab_rcpt = _r
            break
    if latest_tab_rcpt:
        pay_url = request.build_absolute_uri(f'/r/{latest_tab_rcpt.token}/')
        pay_link_suffix = f" Lipa hapa: {pay_url}"

    msg = (
        f"{business.name}: {customer.name}, bado una deni la {outstanding_str}. "
        f"Tafadhali lipa ndani ya siku {window}. Asante.{pay_link_suffix}"
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
        # If this amount only became debt because a split-bill transfer to a
        # different customer's tab was rejected or never resolved, say so —
        # a bare "Kikombe — KES 30" line gives Roy no way to recognise why
        # he's being asked to pay it (2026-07-24 live request).
        note_bit = f" — {entry['transfer_note']}" if entry.get('transfer_note') else ''
        lines.append({
            'name': (
                f"{txn.item.description} — {txn.date.strftime('%d %b %Y')}"
                f" · siku {entry['days_outstanding']}{overdue_tag}{note_bit}"
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
@owner_or_manager_required
@require_POST
def clear_defaulter(request, customer_id):
    """Owner/manager: reinstate a written-off customer — clears is_defaulter and re-approves credit."""
    user_profile = get_user_profile(request)
    customer = get_object_or_404(Customer, id=customer_id, business=user_profile.business)

    Customer.objects.filter(pk=customer.pk).update(
        is_defaulter=False,
        credit_approved=True,
        last_cleared_at=timezone.now(),
    )

    from .models import Notification
    Notification.objects.create(
        user=request.user,
        title=f"✅ {customer.name} — Ameruhusiwa Tena",
        message=f"{customer.name} amesamehewa deni la zamani na anaweza kukopa tena.",
        notification_type='info',
    )

    messages.success(request, f"{customer.name} amesafishwa — anaweza kukopa tena.")
    return redirect('customer_debt_profile', customer_id=customer_id)


@owner_or_manager_required
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


# ── Write-off approval workflow (Sprint WO1) ──────────────────────────────────

@login_required
@require_POST
def request_write_off(request, txn_id):
    """Any staff member (or owner) creates a write-off request for a credit transaction.

    Staff: creates WriteOffRequest and notifies owner + managers. Does NOT void yet.
    Owner/manager: same — approval is always a separate action for audit trail.
    The customer is never blocked by this — they can pay any time regardless.
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    txn = get_object_or_404(
        Transaction,
        id=txn_id,
        business=up.business,
        payment_method='credit',
        type='Issue',
    )

    # Station Scoping Principle: the write-off button is only ever rendered in
    # the UI for a customer's own-station credit lines, but the endpoint itself
    # had no matching gate — a bar-only staffer could pass any txn_id and both
    # act on AND see (item_name, amount, customer) a kitchen transaction, and
    # vice versa. Owner/manager always see both (matches every other station
    # gate in this app).
    show_bar, show_kitchen = _station_scope(up)
    txn_is_kitchen = bool(txn.item_id and getattr(txn.item.store, 'is_kitchen', False))
    if (txn_is_kitchen and not show_kitchen) or (not txn_is_kitchen and not show_bar):
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kiingilio hiki.'}, status=403)

    # Check for an existing pending request on this transaction
    existing = WriteOffRequest.objects.filter(transaction=txn).first()
    if existing:
        if existing.status == WriteOffRequest.STATUS_PENDING:
            return JsonResponse({'ok': False, 'error': 'Ombi tayari lipo — subiri idhini ya mmiliki.'}, status=400)
        if existing.status == WriteOffRequest.STATUS_APPROVED:
            return JsonResponse({'ok': False, 'error': 'Kiingilio hiki kimefutwa tayari.'}, status=400)
        if existing.status == WriteOffRequest.STATUS_REJECTED:
            return JsonResponse({'ok': False, 'error': 'Ombi lilikataliwa awali. Wasiliana na mmiliki moja kwa moja.'}, status=400)

    reason = request.POST.get('reason', '').strip()
    if not reason:
        return JsonResponse({'ok': False, 'error': 'Andika sababu ya kuomba kufuta.'}, status=400)

    customer_name = txn.recipient or ''
    item_name = txn.item.description if txn.item_id else '?'
    amount = float(txn.revenue())
    requester_name = request.user.get_full_name() or request.user.username

    wo = WriteOffRequest.objects.create(
        transaction=txn,
        requested_by=request.user,
        reason=reason,
        customer_name_cache=customer_name,
    )

    # Notify all owners and managers (not the requester themselves)
    from .models import Notification
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification

    targets = _UP.objects.filter(
        business=up.business, role__in=['owner', 'manager'],
    ).exclude(user=request.user).select_related('user')

    for om in targets:
        Notification.objects.create(
            user=om.user,
            title='📝 Ombi la Kufuta Kiingilio',
            message=(
                f"{requester_name} anaomba kufuta: {item_name} "
                f"KES {amount:,.0f} ({customer_name}). Sababu: {reason}"
            ),
            notification_type='warning',
        )
        if om.phone:
            normalized = normalize_ke_phone(om.phone)
            if normalized:
                sms = (
                    f"{up.business.name}: {requester_name} anaomba kufuta kiingilio: "
                    f"{item_name} KES {amount:,.0f} ({customer_name}). "
                    f"Sababu: {reason}. Angalia app kuidhinisha au kukataa."
                )
                send_sms_notification(sms, normalized)

    return JsonResponse({
        'ok': True,
        'request_id': wo.id,
        'message': 'Ombi limetumwa. Mmiliki/meneja ataona na kukuambia uamuzi.',
    })


@login_required
@owner_or_manager_required
@require_POST
def manager_review_write_off(request, req_id):
    """Manager records a recommendation (approve/reject advisory) on a write-off request.

    This does NOT execute the void — only the owner's final decision does.
    The owner is notified of the manager's recommendation.
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    wo = get_object_or_404(WriteOffRequest, id=req_id, transaction__business=up.business)

    if wo.status != WriteOffRequest.STATUS_PENDING:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lishafanyiwa uamuzi wa mwisho.'}, status=400)

    verdict = request.POST.get('verdict', '').strip()
    if verdict not in ('approved', 'rejected'):
        return JsonResponse({'ok': False, 'error': 'Tuma verdict=approved au rejected.'}, status=400)

    wo.manager_verdict = verdict
    wo.manager_by = request.user
    wo.manager_at = timezone.now()
    wo.save(update_fields=['manager_verdict', 'manager_by', 'manager_at'])

    txn = wo.transaction
    item_name = txn.item.description if txn.item_id else '?'
    amount = float(txn.revenue())
    manager_name = request.user.get_full_name() or request.user.username
    verdict_sw = 'ameidhinisha' if verdict == 'approved' else 'amekataa'

    # Notify all owners of the manager's recommendation
    from .models import Notification
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification

    owners = _UP.objects.filter(business=up.business, role='owner').select_related('user')
    for ow in owners:
        Notification.objects.create(
            user=ow.user,
            title=f"{'✅' if verdict == 'approved' else '❌'} Meneja {verdict_sw} write-off",
            message=(
                f"{manager_name} {verdict_sw} kufuta: {item_name} "
                f"KES {amount:,.0f} ({wo.customer_name_cache}). "
                f"Uamuzi wako (mmiliki) ndio wa mwisho."
            ),
            notification_type='info' if verdict == 'approved' else 'warning',
        )
        if ow.phone:
            normalized = normalize_ke_phone(ow.phone)
            if normalized:
                send_sms_notification(
                    f"{up.business.name}: Meneja {manager_name} {verdict_sw} write-off "
                    f"{item_name} KES {amount:,.0f}. Angalia app kufanya uamuzi wa mwisho.",
                    normalized,
                )

    label = 'Imependekezwa' if verdict == 'approved' else 'Imekataliwa na Meneja'
    return JsonResponse({'ok': True, 'verdict': verdict, 'label': label})


@login_required
@owner_required
@require_POST
def approve_write_off(request, req_id):
    """Owner approves a write-off request — executes the void immediately.

    This is the FINAL decision. The transaction's payment_method is set to 'void',
    removing it from the debt tracker and revenue. The customer's receipt meta is
    updated so the line is hidden on the public receipt page.
    If the requesting staff member was already penalised by a manager rejection,
    that Haki deduction is deleted (owner overrides manager).
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    wo = get_object_or_404(WriteOffRequest, id=req_id, transaction__business=up.business)

    if wo.status == WriteOffRequest.STATUS_APPROVED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lishaidhinishwa tayari.'}, status=400)
    if wo.status == WriteOffRequest.STATUS_REJECTED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lilikataliwa — haliwezi kuidhinishwa tena.'}, status=400)

    txn = wo.transaction
    item_name = txn.item.description if txn.item_id else '?'
    customer_name = wo.customer_name_cache or txn.recipient or '—'
    amount = float(txn.revenue())
    reviewer_name = request.user.get_full_name() or request.user.username

    # Execute the void
    txn.payment_method = 'void'
    txn.recipient = ''
    txn.save(update_fields=['payment_method', 'recipient'])

    wo.status = WriteOffRequest.STATUS_APPROVED
    wo.reviewed_by = request.user
    wo.reviewed_at = timezone.now()
    wo.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    # Same signal as void_tab: this credit is unrecoverable and the business
    # is eating the loss, so flag the customer the same way a voided debt tab
    # does — was previously only done for void_tab, leaving this equally-final
    # "written off, uncollectable" path invisible to future credit decisions.
    if customer_name and customer_name != '—':
        cust_obj = Customer.objects.filter(business=up.business, name=customer_name).first()
        if cust_obj:
            Customer.objects.filter(pk=cust_obj.pk).update(is_defaulter=True)

    # Remove any Haki deduction the manager may have already created (owner overrides)
    SalaryDeduction.objects.filter(write_off=wo).delete()

    # Update recent receipts so the line is hidden on the public receipt page
    _mark_receipt_write_off(up.business, customer_name, item_name, amount)

    from .models import Notification
    from core.notifications import normalize_ke_phone, send_sms_notification

    Notification.objects.create(
        user=request.user,
        title='✅ Write-off Imeidhinishwa',
        message=f"{reviewer_name} amefuta: {item_name} KES {amount:,.0f} ({customer_name}).",
        notification_type='info',
    )

    # Notify the requesting staff member
    if wo.requested_by:
        Notification.objects.create(
            user=wo.requested_by,
            title='✅ Ombi la Write-off Limeidhinishwa',
            message=f"Mmiliki ameidhinisha: {item_name} KES {amount:,.0f} ({customer_name}) imefutwa.",
            notification_type='info',
        )
        from accounts.models import UserProfile as _UP
        sp = _UP.objects.filter(user=wo.requested_by, business=up.business).first()
        if sp and sp.phone:
            normalized = normalize_ke_phone(sp.phone)
            if normalized:
                send_sms_notification(
                    f"{up.business.name}: Mmiliki ameidhinisha ombi lako — "
                    f"{item_name} KES {amount:,.0f} imefutwa kutoka kwa deni.",
                    normalized,
                )

    return JsonResponse({'ok': True, 'status': 'approved', 'voided_amount': amount, 'customer': customer_name})


@login_required
@owner_required
@require_POST
def reject_write_off(request, req_id):
    """Owner rejects a write-off request — creates a Haki salary deduction.

    FINAL decision. The transaction stays as a credit (not voided).
    A SalaryDeduction is created for the requesting staff member for this period.
    The staff member is notified via in-app + SMS.
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    wo = get_object_or_404(WriteOffRequest, id=req_id, transaction__business=up.business)

    if wo.status == WriteOffRequest.STATUS_REJECTED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lilikataliwa tayari.'}, status=400)
    if wo.status == WriteOffRequest.STATUS_APPROVED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lishaidhinishwa — haliwezi kukataliwa tena.'}, status=400)

    txn = wo.transaction
    item_name = txn.item.description if txn.item_id else '?'
    customer_name = wo.customer_name_cache or txn.recipient or '—'
    amount = float(txn.revenue())
    reviewer_name = request.user.get_full_name() or request.user.username

    # If a void was applied (e.g., manager had approved), restore the transaction
    if txn.payment_method == 'void':
        txn.payment_method = 'credit'
        txn.recipient = wo.customer_name_cache
        txn.save(update_fields=['payment_method', 'recipient'])

    wo.status = WriteOffRequest.STATUS_REJECTED
    wo.reviewed_by = request.user
    wo.reviewed_at = timezone.now()
    wo.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    # Create Haki deduction for the requesting staff member
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification

    if wo.requested_by and not wo.haki_deduction_created:
        staff_profile = _UP.objects.filter(user=wo.requested_by, business=up.business).first()
        if staff_profile:
            period = timezone.localdate().strftime('%Y-%m')
            SalaryDeduction.objects.create(
                business=up.business,
                staff=staff_profile,
                period=period,
                amount=amount,
                reason=(
                    f"Ombi la kufuta deni lilikataliwa na mmiliki: "
                    f"{item_name} KES {amount:,.0f} ({customer_name})"
                ),
                created_by=request.user,
                write_off=wo,
            )
            wo.haki_deduction_created = True
            wo.save(update_fields=['haki_deduction_created'])

            from .models import Notification
            Notification.objects.create(
                user=wo.requested_by,
                title='❌ Ombi la Write-off Limekataliwa',
                message=(
                    f"Mmiliki amekataa: {item_name} KES {amount:,.0f} ({customer_name}). "
                    f"KES {amount:,.0f} itaondolewa kwenye mshahara wako."
                ),
                notification_type='warning',
            )
            if staff_profile.phone:
                normalized = normalize_ke_phone(staff_profile.phone)
                if normalized:
                    send_sms_notification(
                        f"{up.business.name}: Ombi lako la kufuta {item_name} "
                        f"KES {amount:,.0f} limekataliwa. KES {amount:,.0f} "
                        f"itaondolewa kwenye mshahara wako wa {period}.",
                        normalized,
                    )

    from .models import Notification
    Notification.objects.create(
        user=request.user,
        title='❌ Write-off Imekataliwa',
        message=f"{reviewer_name} alikataa: {item_name} KES {amount:,.0f} ({customer_name}). Haki deduction imetumwa.",
        notification_type='warning',
    )

    deducted_from = ''
    if wo.requested_by:
        deducted_from = wo.requested_by.get_full_name() or wo.requested_by.username

    return JsonResponse({
        'ok': True,
        'status': 'rejected',
        'message': f'Imekataliwa — Haki deduction ya KES {amount:,.0f} imetumwa kwa {deducted_from}.',
    })


@login_required
@owner_or_manager_required
def pending_write_offs(request):
    """Owner/manager: list of all pending write-off requests for this business."""
    up = get_user_profile(request)
    if not up:
        return redirect('login')

    pending = (
        WriteOffRequest.objects
        .filter(transaction__business=up.business, status=WriteOffRequest.STATUS_PENDING)
        .select_related(
            'transaction__item', 'requested_by', 'manager_by',
            'transaction__item__store',
        )
        .order_by('-created_at')
    )
    recent = (
        WriteOffRequest.objects
        .filter(transaction__business=up.business)
        .exclude(status=WriteOffRequest.STATUS_PENDING)
        .select_related('transaction__item', 'requested_by', 'reviewed_by')
        .order_by('-reviewed_at')[:20]
    )

    return render(request, 'core/write_offs_pending.html', {
        'pending': pending,
        'recent': recent,
        'is_owner': up.is_owner,
    })


def _mark_receipt_write_off(business, customer_name, item_name, amount):
    """Add a write-off marker to the customer's recent receipts.

    The receipt public page reads receipt.meta['write_offs'] and hides matching lines.
    Matches by item name + amount. Handles the duplicate-entry case by consuming
    one match per write-off entry (a list, not a set).
    """
    from .models import Receipt
    import datetime
    since = timezone.localdate() - datetime.timedelta(days=14)
    receipts = (
        Receipt.objects
        .filter(business=business, customer_name=customer_name, created_at__date__gte=since)
        .exclude(payment_method='statement')
    )
    for rcpt in receipts:
        meta = rcpt.meta or {}
        wo_list = meta.setdefault('write_offs', [])
        wo_list.append({'name': item_name, 'amount': round(amount, 2)})
        rcpt.meta = meta
        rcpt.save(update_fields=['meta'])
