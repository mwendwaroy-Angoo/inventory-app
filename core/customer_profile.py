"""
Customer profiling — the shared read layer behind a customer's own journey.

2026-08-23 live request (Roy), items 1, 2, 5 and 6 of one batch:
  1) "Customer Profiling (History Transactions [Date Time Item Served by
     Recorded by])"
  2) "Payment history in customer profile to display items paid for, for
     recorded transactions"
  5/6) a customer search engine reachable by ALL staff — Roy's own framing:
     "customers have been asking for one simple thing, 'can you search for
     me in your system?' and I have never been able to do that."

Everything here is READ-ONLY. It computes nothing new and stores nothing —
it assembles what already exists (Transaction, BarTab, BarTabEntry,
CustomerDebtPayment) into the one view a customer or a staffer standing in
front of them actually wants.

Identity: a customer is reachable through THREE different links depending on
how the sale happened, and any single one of them alone misses real history:
  - Transaction.recipient — set for every credit sale AND (via
    KegBarrel.record_sale's `recipient=tab.customer_name if tab else ''`)
    for tab sales regardless of payment method;
  - BarTab.customer  — the FK, set once a tab settles or converts to debt;
  - BarTab.customer_name — the typed name, which is all an ordinary open tab
    has before either of the above exist.
customer_identity_q() is the single union of all three, so every consumer
(history, search, journey) resolves identity exactly the same way.
"""
import uuid
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import CustomerDebtPayment, Transaction


def customer_identity_q(customer):
    """Q matching every Transaction attributable to this customer.

    See the module docstring for why all three links are needed. Name
    matching is case-insensitive throughout, matching how every other
    customer lookup in this app resolves a name (`name__iexact`) — never a
    bare `=`, since the same person is routinely typed with different
    capitalisation across a busy evening.
    """
    return (
        Q(recipient__iexact=customer.name)
        | Q(tab_entry__tab__customer_id=customer.id)
        | Q(tab_entry__tab__customer_name__iexact=customer.name)
    )


def _station_of(txn):
    """'kitchen' | 'bar' — same discriminator the whole app uses."""
    try:
        if txn.item_id and txn.item.store_id:
            return 'kitchen' if txn.item.store.is_kitchen else 'bar'
    except Exception:
        pass
    return 'bar'


def _person(user):
    if not user:
        return ''
    return user.get_full_name() or user.username


def customer_transaction_history(business, customer, scope='all', limit=None):
    """Every sale ever attributed to this customer, newest first.

    Each row carries exactly what Roy asked for — date, time, item, served
    by, recorded by — plus the amount/payment method needed to make the row
    mean anything, and the originating tab so the UI can link onward.

    "Served by" and "Recorded by" are genuinely different people and both
    matter: the waitress/bartender whose tab it was (`BarTab.served_by`)
    versus whoever actually keyed the transaction in (`Transaction
    .recorded_by`) — on a busy counter those routinely differ, and an
    accountability trail that collapses them into one is useless.

    Voided transactions are excluded (they were corrected away and never
    really happened); so are the internal tag conventions that are not
    customer-facing sales at all.
    """
    qs = (
        Transaction.objects
        .filter(customer_identity_q(customer), business=business, type='Issue')
        .exclude(payment_method='void')
        .exclude(invoice_no__in=['[SVQ]', '[ADJ]', '[ADJ-NOLOSS]'])
        .select_related('item__store', 'recorded_by',
                        'tab_entry', 'tab_entry__tab', 'tab_entry__tab__served_by')
        .distinct()
        .order_by('-created_at', '-id')
    )
    if scope == 'kitchen':
        qs = qs.filter(item__store__is_kitchen=True)
    elif scope == 'bar':
        qs = qs.filter(item__store__is_kitchen=False)
    if limit:
        qs = qs[:limit]

    rows = []
    for txn in qs:
        try:
            entry = txn.tab_entry
        except Exception:
            entry = None
        tab = entry.tab if entry is not None else None
        when = timezone.localtime(txn.created_at)
        # The tab entry's own description carries the preset label actually
        # sold ("Paja Nusu", "Kikombe") — strictly more informative than the
        # parent item's catalogue name, which is all a non-tab sale has.
        label = (entry.description if entry is not None and entry.description else None)
        if not label:
            label = txn.item.description if txn.item_id else '—'
        rows.append({
            'txn': txn,
            'date': when.date(),
            'time': when.strftime('%I:%M %p').lstrip('0'),
            'datetime': when,
            'item': label,
            'qty': abs(txn.qty or Decimal('0')),
            'amount': float(txn.revenue()),
            'payment_method': txn.payment_method or '',
            'served_by': _person(tab.served_by) if tab is not None else '',
            'recorded_by': _person(txn.recorded_by),
            'station': _station_of(txn),
            'tab': tab,
            'is_paid': bool(entry.is_paid) if entry is not None else None,
        })
    return rows


def customer_payment_history(business, customer, scope='all'):
    """Every debt payment this customer has made, newest first, each with the
    items it actually went towards.

    IMPORTANT — this coverage is DERIVED, not stored. CustomerDebtPayment
    deliberately records only an amount: its own docstring states "Payments
    are not linked to specific transactions — they reduce the total balance
    using FIFO logic (oldest debt is cleared first) in the views." So which
    items a given payment covered is reconstructed here by replaying that
    same oldest-first walk, using the SAME credit queryset shape
    _get_customer_debt_data() itself uses, so the two can never disagree
    about what is owed. It is an explanation of where the money went, not a
    second source of truth — and the UI labels it as such.
    """
    credit_qs = Transaction.objects.filter(
        Q(payment_method='credit') | Q(was_credit=True),
        business=business,
        recipient=customer.name,
        type='Issue',
    ).exclude(
        tab_entry__tab__status='OPEN',
    ).order_by('date', 'id').select_related('item__store', 'tab_entry')

    payment_qs = CustomerDebtPayment.objects.filter(
        customer=customer, business=business,
    ).exclude(reverted=True).order_by('paid_at', 'id')

    if scope == 'kitchen':
        credit_qs = credit_qs.filter(item__store__is_kitchen=True)
        payment_qs = payment_qs.filter(source='kitchen')
    elif scope == 'bar':
        credit_qs = credit_qs.filter(item__store__is_kitchen=False)
        payment_qs = payment_qs.filter(source='bar')

    debts = [
        {'txn': t, 'total': float(t.revenue()), 'covered': 0.0}
        for t in credit_qs
    ]

    rows = []
    cursor = 0
    for pay in payment_qs:
        remaining = float(pay.amount_paid)
        covered_items = []
        while remaining > 0.005 and cursor < len(debts):
            debt = debts[cursor]
            owed = debt['total'] - debt['covered']
            if owed <= 0.005:
                cursor += 1
                continue
            applied = min(owed, remaining)
            debt['covered'] += applied
            remaining -= applied
            try:
                entry = debt['txn'].tab_entry
            except Exception:
                entry = None
            label = (entry.description if entry is not None and entry.description else None)
            if not label:
                label = debt['txn'].item.description if debt['txn'].item_id else '—'
            covered_items.append({
                'item': label,
                'date': debt['txn'].date,
                'applied': round(applied, 2),
                'full': applied >= owed - 0.005,
            })
            if debt['covered'] >= debt['total'] - 0.005:
                cursor += 1
        when = timezone.localtime(pay.paid_at)
        rows.append({
            'payment': pay,
            'date': when.date(),
            'time': when.strftime('%I:%M %p').lstrip('0'),
            'datetime': when,
            'amount': float(pay.amount_paid),
            'method': pay.payment_method,
            'source': pay.source,
            'recorded_by': _person(getattr(pay, 'recorded_by', None)),
            'covered_items': covered_items,
            # Money paid beyond every debt on record (an overpayment, or a
            # payment recorded before its own charge was) — surfaced rather
            # than silently dropped.
            'unapplied': round(remaining, 2) if remaining > 0.005 else 0.0,
        })

    rows.reverse()  # newest first for display
    return rows


def customer_summary(business, customer, history=None):
    """Headline figures for a customer's journey — total ever spent, how many
    separate purchases, and when they were first and last seen."""
    rows = history if history is not None else customer_transaction_history(business, customer)
    total = sum(r['amount'] for r in rows)
    return {
        'txn_count': len(rows),
        'total_spend': round(total, 2),
        'first_seen': rows[-1]['date'] if rows else None,
        'last_seen': rows[0]['date'] if rows else None,
    }


def ensure_ledger_token(customer):
    """Stable, unguessable public token for this customer's own ledger page.

    Generated on first use rather than at creation time so no migration has
    to backfill every existing customer. Same security model as a receipt
    token: the URL is the proof of identity, and it only ever reaches the
    customer's own phone via their own reminder SMS.
    """
    if not customer.ledger_token:
        customer.ledger_token = uuid.uuid4().hex
        customer.save(update_fields=['ledger_token'])
    return customer.ledger_token
