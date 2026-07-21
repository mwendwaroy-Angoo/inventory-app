"""
core/credit_policy.py — Credit Discipline Gate (Sprint K3.C)

Single source of truth for whether a customer may receive new credit.
Every credit-issuance point (Quick Sell deni, bar tab, kitchen tab,
Add Transaction credit) calls evaluate_credit() BEFORE creating any
credit Transaction or BarTab.

System blocks are NON-OVERRIDABLE — not by staff, not by owner role,
not by can_override_restrictions. The only way to clear a block is to
fix the underlying cause (pay the debt, wait out the cooldown) or for
the owner to change the POLICY in Payment/Business Settings.
"""

import calendar
from dataclasses import dataclass
from django.utils import timezone


@dataclass
class CreditDecision:
    allowed: bool
    tier: str          # 'ok' | 'warn' | 'blocked'
    reason: str        # human-readable; '' when ok; Swahili-capable
    overridable: bool  # always False for system blocks


def evaluate_credit(business, customer, amount=None, scope='all', when=None):
    """
    Decide whether new credit may be extended to this customer.

    Args:
        business: accounts.Business instance
        customer: core.Customer instance (must already exist in DB)
        amount:   Decimal or float — the new amount being extended (optional)
        scope:    'all' | 'bar' | 'kitchen' — which sub-ledger to check
        when:     date override (default: today, Nairobi timezone)

    Returns:
        CreditDecision
    """
    # 0. Policy off → always allow
    if not getattr(business, 'credit_policy_enabled', True):
        return CreditDecision(allowed=True, tier='ok', reason='', overridable=False)

    today = when or timezone.localdate()
    window = business.credit_window_days or 30

    # 1. Owner manual revoke: credit_approved=False → blocked
    if not customer.credit_approved:
        return CreditDecision(
            allowed=False,
            tier='blocked',
            reason=(
                'Mteja huyu hajaruhusiwa kupewa deni. '
                'Mwambie mwenye biashara akuruhusu kwenye ukurasa wa mteja.'
            ),
            overridable=False,
        )

    # 2. Permanent defaulter (bad-debt write-off, only when defaulter_permanent=True)
    if getattr(customer, 'is_defaulter', False) and getattr(business, 'defaulter_permanent', False):
        return CreditDecision(
            allowed=False,
            tier='blocked',
            reason='Mteja huyu amewahi acha deni bila kulipa. Hawezi kupewa deni tena.',
            overridable=False,
        )

    # Fetch debt data (reuses existing risk model — K3C-AC5)
    from core.debt_views import _get_customer_debt_data
    data = _get_customer_debt_data(customer, business, scope)
    outstanding = data['outstanding']

    # 3. Overdue block: outstanding debt past window + grace
    if getattr(business, 'block_if_overdue', True) and data['has_overdue']:
        grace = getattr(business, 'overdue_grace_days', 0)
        for entry in data['unpaid_transactions']:
            days_late = entry['days_outstanding'] - window
            if days_late > grace:
                return CreditDecision(
                    allowed=False,
                    tier='blocked',
                    reason=(
                        f'Mteja ana deni la zamani zaidi ya siku {window + grace}. '
                        f'Lipa KES {outstanding:,.0f} kwanza kabla ya kukopa tena.'
                    ),
                    overridable=False,
                )

    # 4. Late-repayment strikes → cooldown block
    strikes = getattr(business, 'late_repayment_strikes', 3)
    late_threshold = getattr(business, 'late_threshold_days', 7)
    late_count = _count_late_repayments(customer, business, scope, window, late_threshold)
    if late_count >= strikes:
        cooldown = getattr(business, 'cooldown_days', 14)
        last_cleared = getattr(customer, 'last_cleared_at', None)
        if last_cleared:
            from django.utils import timezone as _tz
            days_clean = (_tz.now() - last_cleared).days
            if days_clean < cooldown:
                days_left = cooldown - days_clean
                return CreditDecision(
                    allowed=False,
                    tier='blocked',
                    reason=(
                        f'Mteja amechelewa kulipa mara {late_count}. '
                        f'Subiri siku {days_left} zaidi baada ya kulipa deni.'
                    ),
                    overridable=False,
                )
        else:
            return CreditDecision(
                allowed=False,
                tier='blocked',
                reason=(
                    f'Mteja amechelewa kulipa mara {late_count}. '
                    f'Lipa deni lote na subiri siku {cooldown} kabla ya kukopa tena.'
                ),
                overridable=False,
            )

    # 5. Credit limit check
    credit_limit = customer.credit_limit
    if credit_limit is not None:
        limit_val = float(credit_limit)
        new_total = outstanding + (float(amount) if amount is not None else 0)
        if new_total > limit_val:
            return CreditDecision(
                allowed=False,
                tier='blocked',
                reason=(
                    f'Kikomo cha deni ni KES {limit_val:,.0f}. '
                    f'Bado ana KES {outstanding:,.0f} ya deni — hawezi kukopa zaidi.'
                ),
                overridable=False,
            )

    # 6. Monthly cutoff
    if getattr(business, 'debt_cycle', 'rolling') == 'monthly':
        cutoff = getattr(business, 'debt_cutoff_days_before_month_end', 5)
        last_day = calendar.monthrange(today.year, today.month)[1]
        days_until_month_end = last_day - today.day
        if days_until_month_end <= cutoff:
            return CreditDecision(
                allowed=False,
                tier='blocked',
                reason=(
                    f'Deni jipya haliwezi kutolewa ndani ya siku {cutoff} '
                    f'za mwisho wa mwezi (mwisho wa mwezi = {today.strftime("%d %b")}).'
                ),
                overridable=False,
            )

    # 7. Soft warnings (sale is allowed, but show caution)
    if data['has_overdue']:
        return CreditDecision(
            allowed=True,
            tier='warn',
            reason='Tahadhari: mteja ana deni ambalo linakaribia muda wake.',
            overridable=True,
        )

    if credit_limit is not None:
        limit_val = float(credit_limit)
        if limit_val > 0:
            pct = (outstanding / limit_val * 100)
            if pct >= 80:
                return CreditDecision(
                    allowed=True,
                    tier='warn',
                    reason=f'Tahadhari: mteja amefika {pct:.0f}% ya kikomo cha deni.',
                    overridable=True,
                )

    return CreditDecision(allowed=True, tier='ok', reason='', overridable=False)


def _count_late_repayments(customer, business, scope, window, threshold):
    """Count payments where the oldest UNPAID credit txn (FIFO) was overdue past threshold.

    Uses cumulative FIFO to avoid counting already-paid transactions as strikes — without
    this, a customer who paid off old debt and then took new debt would accumulate unfair
    strikes from the paid-off balance appearing in every subsequent payment check.
    """
    from core.models import CustomerDebtPayment, Transaction

    payments = list(
        CustomerDebtPayment.objects.filter(
            customer=customer, business=business
        ).order_by('paid_at')
    )
    if not payments:
        return 0

    credit_qs = Transaction.objects.filter(
        business=business,
        recipient=customer.name,
        payment_method='credit',
        type='Issue',
    ).order_by('date').select_related('item')

    if scope == 'bar':
        credit_qs = credit_qs.filter(item__store__is_kitchen=False)
    elif scope == 'kitchen':
        credit_qs = credit_qs.filter(item__store__is_kitchen=True)

    credit_txns = list(credit_qs)
    if not credit_txns:
        return 0

    late_count = 0
    cumulative_paid = 0.0

    for payment in payments:
        payment_date = payment.paid_at.date() if hasattr(payment.paid_at, 'date') else payment.paid_at
        # FIFO: skip txns already fully covered by prior payments, find the oldest
        # still-unpaid txn at the moment this payment was made.
        remaining_prior = cumulative_paid
        oldest_unpaid_date = None
        for txn in credit_txns:
            if txn.date > payment_date:
                break  # txn not yet issued at payment time
            txn_amount = float(txn.revenue())
            if remaining_prior >= txn_amount:
                remaining_prior -= txn_amount  # fully covered by earlier payments
            else:
                oldest_unpaid_date = txn.date
                break  # this is the oldest still-unpaid txn as of this payment date

        if oldest_unpaid_date is not None:
            days = (payment_date - oldest_unpaid_date).days
            if days > (window + threshold):
                late_count += 1

        cumulative_paid += float(payment.amount_paid)

    return late_count


def notify_owners_of_conversion_risk(business, customer, scope, unpaid_total, context_label=''):
    """Warn owners/managers when a tab-to-debt conversion adds MORE debt for a
    customer who is already credit-risky per evaluate_credit() — blocked
    (revoked/permanent defaulter/overdue/strikes/limit/cutoff) or warn-tier
    (approaching a limit or overdue window).

    NON-BLOCKING by design: unlike the hard gate at new-credit-issuance points
    (Quick Sell/Add Transaction/Kitchen direct credit — see module docstring),
    a tab-to-debt conversion happens AFTER the goods were already served — the
    tab already exists with an unpaid balance by the time this runs, so there
    is nothing left to block. This is purely a heads-up so the owner can act
    (restrict further tabs for this customer, chase payment) instead of the
    risk being invisible. Called from convert_tab_to_debt, bulk_convert_tabs_to_debt
    (core/keg_views.py) and _convert_open_tabs_to_debt_for_shift (core/shift_views.py)
    — the three places an open tab becomes a debt-tracker balance.
    """
    try:
        decision = evaluate_credit(business, customer, scope=scope)
    except Exception:
        return
    if decision.tier == 'ok':
        return

    from core.models import Notification
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification

    icon = '🚫' if decision.tier == 'blocked' else '⚠️'
    msg = (
        f"{icon} {customer.name}: {context_label}deni la KES {unpaid_total:,.0f} "
        f"limeandikwa — {decision.reason}"
    )
    for up in _UP.objects.filter(business=business, role__in=['owner', 'manager']):
        try:
            Notification.objects.create(
                user=up.user,
                title=f"{icon} Hatari ya mkopo — {customer.name}",
                message=msg,
                notification_type='warning',
            )
            if up.phone:
                normalized = normalize_ke_phone(up.phone)
                if normalized:
                    send_sms_notification(msg, normalized)
        except Exception:
            pass


def get_credit_standing(business, customer, scope='all'):
    """
    Return a dict with the customer's current credit standing for display.
    Used by the customer debt profile and customer credit settings pages.

    Returns: {decision, standing_label, standing_color, restore_hint}
    """
    if not getattr(business, 'credit_policy_enabled', True):
        return {
            'decision': None,
            'standing_label': 'Kinga imezimwa',
            'standing_color': '#888',
            'restore_hint': '',
        }

    decision = evaluate_credit(business, customer, scope=scope)

    if decision.tier == 'ok':
        label = 'Sawa — Anaweza kukopa'
        color = '#6ee7b7'
    elif decision.tier == 'warn':
        label = 'Tahadhari'
        color = '#fbbf24'
    else:
        label = 'Imezuiwa'
        color = '#f87171'

    restore_hint = ''
    if not decision.allowed:
        if not customer.credit_approved:
            restore_hint = 'Ruhusa: nenda kwenye mipangilio ya mkopo wa mteja.'
        elif getattr(customer, 'is_defaulter', False):
            restore_hint = 'Deni liliandikwa vibaya — wasiliana na mwenye biashara.'
        elif decision.reason and 'Lipa' in decision.reason:
            restore_hint = 'Hali itarejea ukamilishaji wa malipo.'
        elif decision.reason and 'Subiri' in decision.reason:
            restore_hint = 'Subiri muda uliosemwa kupita.'

    return {
        'decision': decision,
        'standing_label': label,
        'standing_color': color,
        'restore_hint': restore_hint,
    }
