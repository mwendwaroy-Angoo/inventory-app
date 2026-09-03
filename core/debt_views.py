"""
core/debt_views.py — Sprint K1: source-scoped debt sub-ledgers.

Kitchen-only staff see/settle only kitchen-origin credit.
Bar/general staff see/settle only bar-origin credit.
Owner (and cross-authorised staff) see both ledgers as separate sections.
Discriminator: Transaction.item.store.is_kitchen == True → kitchen; False → bar.
"""

import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.models import Customer, CustomerDebtPayment, SalaryDeduction, Transaction, WriteOffRequest
from core.views import get_user_profile, owner_or_manager_required, _station_scope

logger = logging.getLogger(__name__)


# ── Scope helper ─────────────────────────────────────────────────────────────

def _debt_scope(profile, business):
    """Return 'bar', 'kitchen', or 'all' for the current user.

    'all' = owner/manager or anyone with genuine cross-station access.
    'kitchen' = this viewer only sees the kitchen counter.
    'bar' = this viewer only sees the bar counter (also every non-kitchen
    business, and the default for any role — waitress included — whose
    own station access comes from role alone, not an explicit flag).

    2026-08-10 (Roy — adding waitress debt-tracker access): rebuilt on top
    of `_station_scope()` (core/views.py), the app's single source of truth
    for show_bar/show_kitchen everywhere else, instead of a separate
    can_access_bar/can_access_kitchen re-derivation. The old formula
    assumed `can_access_bar` meaningfully represents "this staffer's OWN
    bar access" — true only for a kitchen-role staffer (see that field's
    own docstring: "Kitchen staff may access the Bar Board") — it is never
    set for an ordinary bar/general/waitress staffer, whose own default bar
    access comes from role alone, same as `_station_scope()` already
    treats it. Any such staffer granted `can_access_kitchen=True` (a
    waitress serving both stations, or ordinary bar staff given kitchen
    cross-access) therefore fell into the 'kitchen'-only branch here,
    incorrectly hiding their own station's debts — not a waitress-specific
    bug, found while wiring up her access but affecting any cross-access
    non-kitchen-role staffer. Symmetrically, a kitchen-role staffer
    correctly granted `can_access_bar=True` used to still get 'kitchen'-only
    scope, since the old formula also required `can_access_kitchen=True`
    (never set for kitchen-role staff, whose kitchen access is implicit via
    role) — now correctly resolves to 'all', matching `_station_scope()`.
    """
    if not getattr(business, 'has_kitchen', False):
        return 'all'
    show_bar, show_kitchen = _station_scope(profile)
    if show_bar and show_kitchen:
        return 'all'
    if show_kitchen:
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


def _txn_tab_entry(txn):
    """The live BarTabEntry backing a debt transaction, or None — same
    try/except-on-a-reverse-OneToOne pattern as _txn_transfer_note()
    (2026-08-11 live request: transfer a single debt item / whole debt to
    another customer). A transaction with no BarTabEntry at all (a direct
    Quick Sell credit sale, never on a tab) has nothing transferable this
    way — a documented, narrower scope than "every possible debt origin,"
    matching the split/whole-tab transfer feature's own existing BarTabEntry
    foundation rather than inventing a second, parallel mechanism."""
    try:
        return txn.tab_entry
    except Exception:
        return None


def _score_from_metrics(has_credit_txns, total_credit_amount, total_paid,
                          has_overdue, outstanding, avg_days, window, has_payments):
    """Shared credit-score formula, factored out of _get_customer_debt_data
    so the scope='all' merge path (see that function's own docstring) can
    recompute a score from combined bar+kitchen metrics using the EXACT
    same thresholds as the single-ledger path, rather than re-deriving a
    parallel formula that could quietly drift out of sync with it.
    """
    if not has_credit_txns:
        return 'new', _('New — No History'), '#888', 0

    completion_rate = (total_paid / total_credit_amount * 100) if total_credit_amount > 0 else 0

    if has_overdue and outstanding > 0:
        return 'high_risk', _('High Risk'), '#f87171', max(10, int(completion_rate * 0.4))
    elif completion_rate >= 90 and (avg_days is None or avg_days <= window * 0.6):
        return 'reliable', _('Reliable'), '#6ee7b7', min(100, int(70 + completion_rate * 0.3))
    elif completion_rate >= 50:
        return 'moderate', _('Moderate'), '#fbbf24', int(40 + completion_rate * 0.3)
    elif not has_payments:
        # Credit transactions exist (e.g. open tab entries) but no debt payments
        # recorded yet — treat as new rather than high_risk; the customer has no
        # established payment behaviour in our system yet.
        return 'new', _('New — No History'), '#888', 0
    else:
        # completion_rate < 50% but no overdue items yet — new/partial payer.
        # high_risk fires only when there are OVERDUE items above.
        # A partial first payment on a fresh tab should not brand the customer
        # as high_risk before their window has even elapsed.
        return 'moderate', _('Moderate'), '#fbbf24', max(5, int(completion_rate * 0.3))


def _get_customer_debt_data(customer, business, scope='all'):
    """Compute debt data for one customer, optionally filtered to a sub-ledger.

    scope='bar'     → only bar-origin credit txns + bar-tagged payments
    scope='kitchen' → only kitchen-origin txns + kitchen-tagged payments
    scope='all'     → both ledgers combined (owner view / businesses without kitchen)

    2026-08-09 live report (Roy) — on a combo bar+kitchen business, the
    "Unpaid Credit Transactions" table on a customer's debt profile could
    list a kitchen item as still owed even though the Kitchen Ledger tile
    right above it correctly showed that ledger as fully paid (and vice
    versa a genuinely-overdue bar item could hide behind it). Root cause:
    CustomerDebtPayment.source is a real, deliberate tag saying which
    sub-ledger a payment settles — but scope='all' used to run ONE FIFO
    pass over every bar+kitchen credit transaction (sorted purely by date)
    against the SUM of every bar+kitchen payment, with no regard for which
    ledger either side actually belonged to. The aggregate totals
    (outstanding/total_credit/total_paid) always balanced regardless — a
    sum doesn't care how it's distributed — but WHICH specific transaction
    got marked "still owed" could land on the wrong ledger's item whenever
    a payment tagged for one ledger was chronologically applied against the
    other ledger's older transaction. This wasn't just a display bug:
    evaluate_credit() (core/credit_policy.py) and Quick Sell's own credit
    gate (core/views.py) both call this function with the scope='all'
    default on every business, so a combo business's overdue/blocked
    decision — and the debt dashboard's own "Overdue" total — could be
    computed from this same wrong per-transaction picture.

    Fixed by making scope='all' a true union of two independently-correct
    FIFO runs (recurse into 'bar' and 'kitchen', each of which already
    respects its own payments-only-settle-its-own-ledger reality) rather
    than one ledger-blind pool — every aggregate figure this returns was
    already correct under the old code (sums don't care about
    attribution), only the per-transaction breakdown (and anything derived
    from it: aged buckets, has_overdue, score) changes.
    """
    today = timezone.localdate()
    window = business.credit_window_days or 30

    if scope == 'all':
        bar_d     = _get_customer_debt_data(customer, business, scope='bar')
        kitchen_d = _get_customer_debt_data(customer, business, scope='kitchen')

        unpaid_transactions = sorted(
            bar_d['unpaid_transactions'] + kitchen_d['unpaid_transactions'],
            key=lambda e: e['txn'].date,
        )
        payments = sorted(
            bar_d['payments'] + kitchen_d['payments'],
            key=lambda p: p.paid_at, reverse=True,
        )
        total_credit_amount = bar_d['total_credit'] + kitchen_d['total_credit']
        total_paid          = bar_d['total_paid'] + kitchen_d['total_paid']
        # 2026-08-15: sum the two ALREADY-CORRECT per-scope outstanding
        # figures directly — do NOT re-derive via total_credit - total_paid
        # here. Since the tab-linked-is_paid fix, a scope's own outstanding
        # is no longer simply total_credit minus total_paid (a tab-linked
        # unpaid entry counts fully regardless of the scope's total_paid) —
        # re-subtracting at the 'all' merge level would silently undo that
        # fix the moment bar+kitchen are combined, which is the default
        # scope every real caller uses.
        outstanding = round(bar_d['outstanding'] + kitchen_d['outstanding'], 2)
        aged = {k: round(bar_d['aged'][k] + kitchen_d['aged'][k], 2) for k in bar_d['aged']}
        has_overdue = bar_d['has_overdue'] or kitchen_d['has_overdue']
        credit_txns_exist = bool(bar_d['txn_count'] or kitchen_d['txn_count'])
        avg_days = _calc_avg_payment_days(customer, business, scope='all')

        score, score_label, score_color, score_pct = _score_from_metrics(
            credit_txns_exist, total_credit_amount, total_paid,
            has_overdue, outstanding, avg_days, window, bool(payments),
        )

        effective_window = min(customer.expected_payment_days or window, window)

        return {
            'customer':            customer,
            'outstanding':         outstanding,
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
            'txn_count':           bar_d['txn_count'] + kitchen_d['txn_count'],
            'payment_count':       bar_d['payment_count'] + kitchen_d['payment_count'],
        }

    credit_qs = Transaction.objects.filter(
        Q(payment_method='credit') | Q(was_credit=True),
        business=business,
        recipient=customer.name,
        type='Issue',
    ).exclude(
        # Transactions linked to an OPEN tab are tab charges, not standalone debt.
        # They enter the debt ledger only after the tab is settled as credit / converted.
        tab_entry__tab__status='OPEN',
    ).order_by('date').select_related('item__store', 'tab_entry', 'tab_entry__tab', 'recorded_by')

    payment_qs = CustomerDebtPayment.objects.filter(
        customer=customer,
        business=business,
    ).exclude(reverted=True).order_by('paid_at')

    if scope == 'kitchen':
        credit_qs = credit_qs.filter(item__store__is_kitchen=True)
        payment_qs = payment_qs.filter(source='kitchen')
    elif scope == 'bar':
        credit_qs = credit_qs.filter(item__store__is_kitchen=False)
        payment_qs = payment_qs.filter(source='bar')

    all_txns = list(credit_qs)
    payments = list(payment_qs)
    total_paid = sum(float(p.amount_paid) for p in payments)

    # 2026-08-15, second fix the same day (Roy's own live data, "i have 600
    # to pay for bar section specifically... there must be something we are
    # missing"): confirmed a real, separate bug from was_credit's own —
    # a tab-linked transaction (entry.is_paid=False, entry ground truth) was
    # STILL showing as "All paid" because the FIFO-against-cumulative-total
    # walk below assumed every shilling ever paid lined up in date order
    # against the oldest debts, which doesn't hold once payments and tab
    # items don't arrive in a clean 1:1 sequence — the cumulative math
    # concluded "covered by now" for an item nobody had actually paid.
    #
    # Fix: BarTabEntry.is_paid is 100% authoritative for a tab-linked
    # transaction and needs NO FIFO guessing at all — a tab can only reach
    # SETTLED with an is_paid=False entry via a genuine Geuza Deni debt
    # conversion (_convert_tab_to_debt_core only force-sets payment_method=
    # 'credit' on still-UNPAID entries; an ORDINARY paid-in-full tab clears
    # every entry before it can even become SETTLED — tab_settled = not
    # tab.entries.filter(is_paid=False).exists()) — so this combination can
    # never happen for an ordinary tab, unlike tab.customer_id (which IS set
    # for an ordinary settle too, the exact wrong signal the retired
    # backfill_was_credit command used).
    #
    # Tab-linked unpaid entries are therefore counted as FULLY outstanding
    # (minus any partial amount_paid) unconditionally, with no FIFO/payment-
    # total guessing needed. But the money that DID go toward a tab-linked
    # entry (whether it fully or partially covered it) is real money out of
    # the shared total_paid pool — it must be reserved away before that same
    # pool is applied to the non-tab walk below, or the exact same shilling
    # would silently reduce BOTH a tab-linked entry's own is_paid/amount_paid
    # state AND a non-tab transaction's outstanding amount at once (found
    # while writing this fix's own tests: a single 80 KES debt payment fully
    # covering one 80 KES tab-linked entry was ALSO subtracted a second time
    # from an unrelated 100 KES non-tab debt, understating it to 20).
    tab_linked_unpaid = []
    tab_linked_consumed = 0.0
    non_tab_txns = []
    for txn in all_txns:
        try:
            entry = txn.tab_entry
        except Exception:
            entry = None
        if entry is not None:
            # is_paid=True is authoritative that the FULL amount was
            # consumed, regardless of what amount_paid happens to say (a
            # tab-linked entry marked paid before amount_paid existed, or by
            # any other settle path, still has its full amount reserved
            # here — never re-available to the non-tab walk).
            tab_linked_consumed += float(entry.amount) if entry.is_paid else float(entry.amount_paid)
            if not entry.is_paid:
                tab_linked_unpaid.append(txn)
            # else: is_paid=True — fully resolved, whatever the mechanism,
            # excluded entirely; see the docstring above for why this is safe.
        else:
            non_tab_txns.append(txn)

    unpaid_transactions = []
    tab_linked_outstanding = 0.0
    for txn in tab_linked_unpaid:
        # 2026-08-15, third fix same day: is_paid=False doesn't mean "zero
        # paid" — settle_tab's own amount= param (and _do_settle_debt_
        # payment's FIFO walk) can partially cover a tab-linked entry
        # without fully resolving it (is_paid only flips True once the
        # WHOLE amount is covered). amount_paid tracks that remainder so a
        # partial payment is reflected here instead of re-showing the full
        # original amount as still owed.
        entry = txn.tab_entry
        remaining = round(float(txn.revenue()) - float(entry.amount_paid), 2)
        if remaining <= 0:
            continue
        tab_linked_outstanding += remaining
        unpaid_transactions.append({
            'txn': txn,
            'amount': remaining,
            'days_outstanding': (today - txn.date).days,
            'is_overdue': (today - txn.date).days > window,
            'transfer_note': _txn_transfer_note(txn),
        })

    # Non-tab (direct credit sale, e.g. a plain Quick Sell "Deni") has no
    # per-item is_paid flag to rely on — keeps the original, already-tested
    # FIFO-against-total_paid walk, which correctly supports a genuine
    # partial payment of a single transaction. tab_linked_consumed is
    # reserved away first so money that already paid off a tab-linked entry
    # can never also reduce a non-tab transaction's own outstanding amount.
    non_tab_available_paid = max(0.0, total_paid - tab_linked_consumed)
    remaining_paid = non_tab_available_paid
    for txn in non_tab_txns:
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

    unpaid_transactions.sort(key=lambda e: e['txn'].date)
    credit_txns = all_txns
    # 2026-08-15, FOURTH fix same day (found by the full test suite —
    # ReturnPrimitiveTest.test_return_reverses_credit_debt regressed to
    # 400.0 instead of 200.0): Total Credit must be the full historical
    # total ever extended — EVERY txn in all_txns, tab-linked or not,
    # PAID or not — never shrinking just because a tab-linked entry later
    # got paid off. Excluding paid-off tab-linked entries here (as an
    # earlier draft of this fix did) would silently reproduce THIS SAME
    # SESSION'S OWN "Total Paid exceeds Total Credit" bug, just scoped to
    # the tab-linked population instead of the non-tab one: pay off entry
    # 1 of 3 via a real debt payment → total_paid grows AND total_credit
    # shrinks by the same amount, and the next payment then exceeds a
    # now-smaller total_credit again.
    total_credit_amount = sum(float(t.revenue()) for t in all_txns)
    # Outstanding is NOT simply "sum of the itemized unpaid_transactions
    # list" — a Return's reversal transaction (qty=0, NEGATIVE sale_amount,
    # no tab) breaks the itemized per-transaction walk below when it lands
    # in an unlucky tie-order relative to the sale it's reversing (the walk
    # is a single forward pass with no way to revisit an already-appended
    # earlier line once a later negative amount effectively "pre-pays" it).
    # Non-tab outstanding is computed the ORIGINAL, order-independent way
    # (total credit minus total paid, floored at 0) — exactly matching this
    # function's pre-2026-08-15 behavior, which handled Returns correctly.
    # Tab-linked outstanding is fully separate and unconditional (is_paid=
    # False is 100% authoritative — no total_paid involved at all), so the
    # two combine cleanly with no double-counting either way. (Already
    # accumulated above, in the same loop that builds unpaid_transactions.)
    tab_linked_outstanding = round(tab_linked_outstanding, 2)
    non_tab_total_credit = sum(float(t.revenue()) for t in non_tab_txns)
    non_tab_outstanding = round(max(0.0, non_tab_total_credit - non_tab_available_paid), 2)
    outstanding = round(tab_linked_outstanding + non_tab_outstanding, 2)

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

    avg_days = _calc_avg_payment_days(customer, business, scope) if credit_txns else None
    score, score_label, score_color, score_pct = _score_from_metrics(
        bool(credit_txns), total_credit_amount, total_paid,
        has_overdue, outstanding, avg_days, window, bool(payments),
    )

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
    ).exclude(reverted=True).order_by('paid_at')

    txn_qs = Transaction.objects.filter(
        Q(payment_method='credit') | Q(was_credit=True),
        business=business,
        recipient=customer.name,
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


def _find_duplicate_customer_groups(business):
    """Customer rows sharing the same name (case/whitespace-insensitive).

    2026-08-09 live report (Roy) — "two Eugenes with the same amount and
    same items" in the debt dashboard, and a receipt showing a debt as
    cleared while the debt tracker still showed it open. Root cause traced:
    _get_customer_debt_data()'s credit_qs matches Transactions by
    `Transaction.recipient=customer.name` — a plain STRING match, never the
    Customer FK (see that function's own code). So if two Customer rows
    share a name, BOTH independently query and display the SAME underlying
    unpaid transactions as their own outstanding balance — and a payment
    recorded against one (via CustomerDebtPayment.customer, which IS FK-
    scoped) clears only that one row, leaving the other showing the exact
    same debt as still open. `Customer` has no unique_together on
    (business, name) (see this file's own Known Issues) — every known
    creation site already uses name__iexact before creating, but a
    duplicate can still occur (a genuine race between two near-simultaneous
    requests, or a duplicate that predates that convention). Merging two
    duplicates (Customer.merge_locked) is the correct fix: it only
    reassigns FK references and name strings, never touches
    Transaction.qty — so it corrects the display with zero stock impact,
    exactly matching Roy's own explicit requirement. This is the proactive
    detector so an owner can find and merge duplicates before they cause
    exactly this kind of confusion.
    """
    groups = defaultdict(list)
    for c in Customer.objects.filter(business=business).order_by('id'):
        key = (c.name or '').strip().lower()
        if key:
            groups[key].append(c)
    return [g for g in groups.values() if len(g) > 1]


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
    ).prefetch_related('debt_payments').order_by('id')

    dashboard_rows = []
    total_outstanding = 0.0
    total_overdue = 0.0
    # 2026-08-18 — one _get_customer_debt_data() result per customer, kept
    # for the duplicate-groups block below to reuse. This function
    # recurses into two full sub-computations for scope='all' and is
    # genuinely not cheap per customer — a business with a real customer
    # base makes this loop the single heaviest thing on this page (see
    # the live 502 investigation this same day: no per-request timing was
    # ever visible in production, so this was a credible but unconfirmed
    # suspect — fixing the one clearly redundant DOUBLE-computation below
    # is a safe, mechanical win regardless of whether it was the actual
    # cause; a deeper batch rewrite of _get_customer_debt_data itself is
    # deliberately NOT attempted here — same caution this function's own
    # extensive edit history already demonstrates is warranted.
    data_by_customer_id = {}

    # 2026-08-27 live report (Roy): "excess entries or exaggerations of
    # debt items and amounts" — _get_customer_debt_data() matches
    # Transaction.recipient=customer.name (a plain EXACT string, not this
    # customer's own id), so two Customer rows sharing the exact same name
    # string (see _find_duplicate_customer_groups' own docstring — "two
    # Eugenes with the same amount and same items") each independently
    # compute and would each add the SAME full outstanding into
    # total_outstanding below. duplicate_groups (further down) already
    # WARNS about this, but the dashboard's own headline total was still
    # silently inflated by it until this fix.
    #
    # Deliberately EXACT-string matching here (never the broader case/
    # whitespace-insensitive key _find_duplicate_customer_groups uses for
    # its warning) — that broader key can also match two customers whose
    # names differ only by case/spacing, and _get_customer_debt_data's own
    # exact-string filter means such a pair's two `data` dicts are NOT
    # actually identical, they reflect a real, DIFFERENT (non-overlapping)
    # set of transactions each. Skipping the second one there would silently
    # UNDER-count real outstanding debt instead of fixing an over-count —
    # exactly the kind of "amount out of the user's control" this report is
    # about, just in the other direction. Only a byte-identical name string
    # is safe to dedupe this way, since that's the one case where the two
    # computations are provably summing the exact same transactions.
    #
    # Every customer still gets its own data_by_customer_id entry (needed
    # by duplicate_groups below), so nothing here changes which duplicates
    # get flagged — merging them away (via 🔀 Sahihisha Jina la Mteja) is
    # still the real fix, for exact AND near-duplicate names alike.
    seen_exact_names = set()

    for customer in customers_with_credit:
        data = _get_customer_debt_data(customer, business, scope)
        data_by_customer_id[customer.id] = data
        if data['outstanding'] > 0 or data['txn_count'] > 0:
            exact_name = (customer.name or '').strip()
            if exact_name and exact_name in seen_exact_names:
                continue
            if exact_name:
                seen_exact_names.add(exact_name)
            dashboard_rows.append(data)
            total_outstanding += data['outstanding']
            if data['has_overdue']:
                total_overdue += data['outstanding']

    dashboard_rows.sort(key=lambda x: (-int(x['has_overdue']), -x['outstanding']))

    scope_label = {'bar': 'Bar', 'kitchen': 'Kitchen', 'all': 'All'}.get(scope, 'All')

    # 2026-08-09 live report — duplicate Customer rows sharing a name each
    # independently show the SAME underlying debt (see
    # _find_duplicate_customer_groups' own docstring); surfaced here,
    # owner/manager only, since resolving it is a merge action, not a
    # routine debt-collection one.
    duplicate_groups = []
    if getattr(user_profile, 'is_owner_or_manager', False):
        for group in _find_duplicate_customer_groups(business):
            entries = []
            for c in group:
                # Reuse the result already computed above instead of
                # recomputing it a second time for the same customer.
                d = data_by_customer_id.get(c.id) or _get_customer_debt_data(c, business, scope)
                entries.append({'customer': c, 'outstanding': d['outstanding']})
            duplicate_groups.append(entries)

    return render(request, 'core/debt_dashboard.html', {
        'rows':              dashboard_rows,
        'total_outstanding': round(total_outstanding, 2),
        'total_overdue':     round(total_overdue, 2),
        'customer_count':    len([r for r in dashboard_rows if r['outstanding'] > 0]),
        'credit_window':     window,
        'today':             today.strftime('%B %d, %Y'),
        'scope':             scope,
        'scope_label':       scope_label,
        'duplicate_groups':  duplicate_groups,
    })


@login_required
def customer_debt_profile(request, customer_id):
    user_profile = get_user_profile(request)
    business = user_profile.business
    is_owner = user_profile.is_owner_or_manager
    scope = _debt_scope(user_profile, business)

    customer = get_object_or_404(Customer, id=customer_id, business=business)

    # 2026-07-31 — "No, cancel it" on the duplicate-payment confirmation
    # banner just discards the stashed pending payment; nothing was ever
    # recorded for it in the first place, so there's nothing else to undo.
    if request.GET.get('clear_dup_confirm') == '1':
        request.session.pop(f'debt_dup_pending_{customer.id}', None)
        return redirect('customer_debt_profile', customer_id=customer_id)

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
            # Ad-hoc attribute for the template — see the app-wide rule that
            # Django templates reject any name starting with '_'; this is
            # deliberately a plain name, not a model field.
            wo_obj.can_i_approve = _can_approve_debt_action(user_profile, wo_obj)
            wo_map[wo_obj.transaction_id] = wo_obj
        for entry in data.get('unpaid_transactions', []):
            entry['write_off'] = wo_map.get(entry['txn'].id)

    # 2026-08-11 live request (Roy): "a way for staff to transfer both
    # single items and whole tabs for one customer to the other, even if
    # that customer is in the debt tracker side." Each unpaid transaction
    # that still has a live BarTabEntry behind it (the common case — most
    # debt here originated from a tab conversion, not a tab-less direct
    # credit sale) gets a tab_entry_id so the template can offer a per-row
    # "🔀 Hamisha" button calling the SAME split_and_transfer_entry endpoint
    # the tabs drawers already use (now relaxed to accept a debt-converted
    # source, see BarTabEntry.split_and_transfer_locked()). Grouped by
    # source tab too, for one "Hamisha Tab Yote" button per distinct tab
    # this customer's debt actually came from — a debt customer can have
    # more than one, if they've had several tabs converted over time.
    transferable_tabs = {}
    for entry in data.get('unpaid_transactions', []):
        te = _txn_tab_entry(entry['txn'])
        entry['tab_entry_id'] = te.id if te else None
        entry['source_tab_id'] = te.tab_id if te else None
        if te:
            row = transferable_tabs.setdefault(te.tab_id, {'id': te.tab_id, 'count': 0, 'total': 0.0})
            row['count'] += 1
            row['total'] += entry['amount']
    for row in transferable_tabs.values():
        row['total'] = round(row['total'], 2)
    transferable_tabs = sorted(transferable_tabs.values(), key=lambda r: -r['total'])

    # 2026-07-31 — debt-section "Ilikuwa Kosa" (erase a mistaken entry)
    # feature: whether it executes immediately (self-service, default) or
    # needs owner/manager approval, and whether THIS viewer could approve
    # one if it did need approval — both drive the write-off modal's copy.
    can_approve_debt_erase = user_profile.is_owner or (
        user_profile.role == 'manager' and getattr(user_profile, 'can_approve_debt_erase', False)
    )

    pending_wo_count = WriteOffRequest.objects.filter(
        transaction__business=business,
        status=WriteOffRequest.STATUS_PENDING,
    ).count() if is_owner else 0

    # 2026-07-31 — duplicate-payment confirmation banner (see
    # record_debt_payment's session-stashed pending payment).
    pending_dup_confirm = request.session.get(f'debt_dup_pending_{customer.id}')

    # 2026-08-18 — Payment History must show a reverted payment too (with a
    # visible badge), not just silently drop it — this app never hides a
    # correction, it explains it (see the wording/accountability standard).
    # Deliberately a SEPARATE query from data['payments'] (which now
    # correctly excludes reverted rows everywhere it feeds a financial
    # figure) — this one is display-only.
    payment_history = CustomerDebtPayment.objects.filter(
        customer=customer, business=business,
    ).select_related('recorded_by', 'reverted_by').order_by('-paid_at')
    if scope != 'all':
        payment_history = payment_history.filter(source=scope)
    payment_history = list(payment_history[:50])

    can_revert_debt_payment = _can_revert_debt_payment(user_profile)

    return render(request, 'core/customer_debt_profile.html', {
        **data,
        'is_owner':        is_owner,
        'scope':           scope,
        'has_kitchen':     has_kitchen,
        'transferable_tabs': transferable_tabs,
        'has_daraja':      has_daraja,
        'today':           timezone.now().date().isoformat(),
        'today_label':     timezone.now().date().strftime('%B %d, %Y'),
        'payment_methods': CustomerDebtPayment.PAYMENT_METHOD_CHOICES,
        'credit_standing': credit_standing,
        'pending_wo_count': pending_wo_count,
        'pending_dup_confirm': pending_dup_confirm,
        'debt_erase_requires_approval': business.debt_erase_requires_approval,
        'can_approve_debt_erase': can_approve_debt_erase,
        'payment_history': payment_history,
        'can_revert_debt_payment': can_revert_debt_payment,
    })


def _reconcile_tab_entries_for_debt_payment(customer, business, amount, payment_method, unpaid_before):
    """FIFO-apply one debt payment's worth of money against this customer's
    tab-linked unpaid BarTabEntries — only flips is_paid once an entry is
    FULLY covered, and keeps the underlying Transaction.payment_method in
    sync with the entry (the 2026-08-14 fix — see the long comment this
    function used to carry inline, still true, just moved here).

    Extracted out of _do_settle_debt_payment (2026-08-18) so
    revert_debt_payment() can REPLAY this exact same, already-proven logic
    when rebuilding a customer's tab-entry state after one payment out of a
    possibly-longer sequence is reverted — see
    _rebuild_tab_entry_state_for_customer()'s own docstring for why replay
    (not a surgical un-apply) is the safe way to undo one payment without
    needing to know in advance which entries any one payment touched.

    `unpaid_before` must be freshly computed (via _get_customer_debt_data)
    immediately before calling this — it reflects state BEFORE this
    specific payment is applied.
    """
    from .models import BarTabEntry, BarTab
    try:
        now = timezone.now()
        settled_tab_ids = list(BarTab.objects.filter(
            business=business, customer=customer, status='SETTLED',
        ).values_list('id', flat=True))
        if not settled_tab_ids:
            return
        paid_remaining = float(amount)
        for entry in unpaid_before:
            if paid_remaining <= 0:
                break
            txn = entry['txn']
            entry_amount = float(entry['amount'])  # already remaining-after-amount_paid
            covered = round(min(entry_amount, paid_remaining), 2)
            paid_remaining = round(paid_remaining - covered, 2)
            if covered >= entry_amount:
                # debt_collected_amount (2026-08-24) — additive by `covered`,
                # NOT jumped to F('amount') the way amount_paid is: it must
                # isolate only the DEBT-TRACKER-sourced total across this
                # entry's whole history (this payment's own share, plus
                # whatever partial debt payments came before it), never the
                # full price, since some of that price may have been (or
                # will be) collected via an ordinary counter settle instead
                # — see the field's own docstring on BarTabEntry.
                BarTabEntry.objects.filter(
                    tab__id__in=settled_tab_ids,
                    transaction=txn,
                    is_paid=False,
                ).update(
                    is_paid=True, paid_at=now, payment_method=payment_method,
                    amount_paid=F('amount'),
                    debt_collected_amount=F('debt_collected_amount') + Decimal(str(covered)),
                )
                if txn.payment_method != payment_method:
                    txn.payment_method = payment_method
                    txn.save(update_fields=['payment_method'])
            elif covered > 0:
                # A PARTIAL cover of a tab-linked entry must not be silently
                # discarded — is_paid stays False (genuinely not fully
                # resolved) and its Transaction must STAY 'credit' (still
                # genuinely, partially owed) — but the covered amount needs
                # to persist so _get_customer_debt_data reports the TRUE
                # remainder next time. F() keeps this safe under concurrent
                # partial payments (never overwrites with a stale read).
                BarTabEntry.objects.filter(
                    tab__id__in=settled_tab_ids,
                    transaction=txn,
                    is_paid=False,
                ).update(
                    amount_paid=F('amount_paid') + Decimal(str(covered)),
                    debt_collected_amount=F('debt_collected_amount') + Decimal(str(covered)),
                )
    except Exception:
        logger.exception('_reconcile_tab_entries_for_debt_payment failed (customer=%s)', customer.id)


def _rebuild_tab_entry_state_for_customer(customer, business):
    """After a CustomerDebtPayment is reverted, put this customer's
    tab-linked entries back to exactly the state they'd be in if that
    payment had never happened — regardless of how many OTHER payments
    came before or after it.

    Why replay instead of surgically undoing just the one payment: no
    record exists anywhere of exactly which BarTabEntry rows any ONE
    payment settled (_reconcile_tab_entries_for_debt_payment applies FIFO
    fresh each time and never tags the entries it touches with which
    payment did it) — reconstructing that after the fact for an arbitrary
    payment in an arbitrary position in the sequence would mean guessing.
    Resetting every touched entry back to "never paid" and then replaying
    every remaining (non-reverted) payment in chronological order through
    the exact same, already-proven reconciliation function is
    deterministic and correct no matter which payment was reverted or how
    many others exist — it doesn't need to know.

    Non-tab-linked ("direct credit sale") debt needs NO code here at all —
    _get_customer_debt_data computes it live from summing valid
    CustomerDebtPayment rows, so excluding the reverted one there already
    fixes it for free.
    """
    from .models import BarTabEntry, BarTab
    from django.db.models import Q as _Q

    settled_tab_ids = list(BarTab.objects.filter(
        business=business, customer=customer, status='SETTLED',
    ).values_list('id', flat=True))
    if settled_tab_ids:
        touched_entries = BarTabEntry.objects.filter(
            tab__id__in=settled_tab_ids,
        ).filter(
            _Q(is_paid=True) | _Q(amount_paid__gt=0),
        ).filter(
            _Q(transaction__payment_method='credit') | _Q(transaction__was_credit=True),
            transaction__type='Issue',
        ).select_related('transaction').distinct()
        for entry in touched_entries:
            entry.is_paid = False
            entry.amount_paid = Decimal('0')
            # 2026-08-24 — debt_collected_amount reset alongside amount_paid.
            # Every entry reaching this filter (payment_method still
            # 'credit', or was_credit=True — which only ever stamps for a
            # transition on a NON-open tab, something the ordinary counter-
            # settle paths never touch, since they only ever act on OPEN
            # tabs) got its entire amount_paid history from the debt
            # tracker alone, so the two fields are always equal here —
            # safe to reset together with nothing else to preserve.
            entry.debt_collected_amount = Decimal('0')
            entry.paid_at = None
            entry.payment_method = ''
            entry.save(update_fields=[
                'is_paid', 'amount_paid', 'debt_collected_amount', 'paid_at', 'payment_method',
            ])
            if entry.transaction.was_credit and entry.transaction.payment_method != 'credit':
                entry.transaction.payment_method = 'credit'
                entry.transaction.save(update_fields=['payment_method'])

    remaining_payments = CustomerDebtPayment.objects.filter(
        customer=customer, business=business, reverted=False,
    ).order_by('paid_at', 'id')
    for payment in remaining_payments:
        fresh_data = _get_customer_debt_data(customer, business, payment.source)
        _reconcile_tab_entries_for_debt_payment(
            customer, business, payment.amount_paid, payment.payment_method,
            fresh_data['unpaid_transactions'],
        )


@login_required
@require_POST
def revert_debt_payment(request, payment_id):
    """Undo a recorded CustomerDebtPayment — the 2026-08-18 live request
    (Roy): "staff recorded a debt mistakenly when it was not paid for."
    Soft-reverts (marks reverted, never deletes — matches this app's
    correction convention everywhere else), restores the customer's real
    outstanding balance, and rebuilds any tab-linked entry state that
    payment had settled — which then automatically flows through to every
    live-computed accounting figure that reads CustomerDebtPayment
    (_get_customer_debt_data's outstanding/total_paid, shift_views.
    _reconcile()'s debt_recovered_cash/mpesa, till_expected_cash()'s same
    figure, credit_policy's late-repayment scoring, haki's staff
    contribution) since none of them cache — they all query fresh, every
    call, and now all exclude reverted=True.

    Gated the same way every other delegated financial-correction action
    in this app is: owner always; a manager or staff member only with the
    explicit can_revert_debt_payment toggle (default off for both roles).
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    if not _can_revert_debt_payment(up):
        return JsonResponse(
            {'ok': False, 'error': 'Huna ruhusa ya kutengua malipo ya deni. Muulize mmiliki wa biashara.'},
            status=403,
        )

    payment = get_object_or_404(CustomerDebtPayment, id=payment_id, business=up.business)
    if payment.reverted:
        return JsonResponse({'ok': False, 'error': 'Malipo haya tayari yamekwisha tenguliwa.'}, status=409)

    # Same shift-gate as recording a payment in the first place — a
    # non-owner/manager correcting their own mistake still needs an open
    # shift, matching every other staff-initiated financial correction.
    if not up.is_owner_or_manager:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse(
                {'ok': False, 'error': 'Fungua shift yako kwanza kabla ya kutengua malipo.'},
                status=403,
            )

    reason = (request.POST.get('reason') or '').strip()
    customer = payment.customer
    amount = payment.amount_paid
    method_label = 'M-Pesa' if payment.payment_method == 'mpesa' else 'Cash'

    payment.reverted = True
    payment.reverted_at = timezone.now()
    payment.reverted_by = request.user
    payment.revert_reason = reason[:200]
    payment.save(update_fields=['reverted', 'reverted_at', 'reverted_by', 'revert_reason'])

    _rebuild_tab_entry_state_for_customer(customer, up.business)

    # Debt no longer genuinely cleared once this payment is undone — an
    # earlier last_cleared_at stamp from THIS payment clearing the balance
    # to zero would now be stale/misleading.
    post_data = _get_customer_debt_data(customer, up.business, 'all')
    if float(post_data['outstanding']) > 0 and customer.last_cleared_at:
        Customer.objects.filter(pk=customer.pk).update(last_cleared_at=None)

    who = request.user.get_full_name() or request.user.username
    when = timezone.localtime(timezone.now()).strftime('%d %b, %H:%M')
    msg = (
        f"↩️ {who} ametengua malipo ya deni ya KES {amount:,.0f} ({method_label}) kwa "
        f"{customer.name} — {when}."
        + (f" Sababu: {reason}." if reason else " Hakuna sababu iliyotolewa.")
        + f" Deni lililobaki sasa: KES {post_data['outstanding']:,.0f}."
    )
    try:
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification_async
        recipients = {
            op.user_id: op for op in
            _UP.objects.filter(business=up.business, role__in=['owner', 'manager'])
            .exclude(user=request.user)
        }
        if payment.recorded_by_id and payment.recorded_by_id != request.user.id:
            recorder_profile = getattr(payment.recorded_by, 'userprofile', None)
            if recorder_profile:
                recipients[recorder_profile.user_id] = recorder_profile
        for op in recipients.values():
            Notification.objects.create(
                user=op.user, title='↩️ Malipo ya Deni Yametenguliwa',
                message=msg, notification_type='warning',
                link_url=f'/debt/{customer.id}/',
            )
            if op.phone:
                normalized = normalize_ke_phone(op.phone)
                if normalized:
                    send_sms_notification_async(msg, normalized)
    except Exception:
        logger.exception('revert_debt_payment: notify failed payment=%s', payment.id)

    return JsonResponse({
        'ok': True,
        'message': f'Malipo yametenguliwa. Deni lililobaki: KES {post_data["outstanding"]:,.0f}.',
        'outstanding': float(post_data['outstanding']),
    })


def _can_revert_debt_payment(up):
    """Owner always. A manager OR a plain staff member may revert a
    recorded debt payment only when explicitly granted
    can_revert_debt_payment (off by default for both roles) — reverting
    un-reconciles real, already-accounted-for cash/mpesa, so this is not
    automatic for either role the way viewing the debt tracker is."""
    if up.is_owner:
        return True
    return getattr(up, 'can_revert_debt_payment', False)


def _do_settle_debt_payment(customer, business, amount, payment_method, source,
                             notes='', recorded_by=None,
                             site_url='https://www.dukamwecheche.co.ke',
                             paid_at=None):
    """Create CustomerDebtPayment + FIFO reconciliation + issue receipt + SMS.

    Shared by record_debt_payment (HTTP view) and _settle_debt_customer_from_payment
    (M-Pesa callback). Returns (receipt, post_data) on success, raises on fatal error.
    post_data is _get_customer_debt_data recomputed AFTER the payment is recorded.

    paid_at (2026-08-09 live request): optional backdate for a debt that
    was genuinely settled earlier and never recorded at the time — None
    (every existing caller) keeps the model's own default of "now".
    """
    from .models import BarTabEntry, BarTab, Receipt
    from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async

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
        **({'paid_at': paid_at} if paid_at else {}),
    )

    _reconcile_tab_entries_for_debt_payment(customer, business, amount, payment_method, unpaid_before)

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
                send_sms_notification_async(sms_msg, normalized)
    except Exception:
        pass

    return rcpt, post_data


def _flag_possible_duplicate_debt_payment(business, customer, amount, earlier_payment):
    """Non-blocking heads-up to owner/manager the MOMENT a possible
    duplicate is detected (before the staffer has even decided whether to
    confirm it) — so owner/manager have visibility into a flagged payment
    regardless of what the recording staffer does next. Never blocks (the
    second payment may well be genuine), matches the 'warn, don't silently
    block' pattern used throughout this app (e.g. evaluate_credit()'s WARN
    tier)."""
    try:
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        when = timezone.localtime(earlier_payment.paid_at).strftime('%d %b, %H:%M')
        msg = (
            f"⚠️ {customer.name}: malipo mapya ya KES {amount:,.0f} yanafanana na malipo "
            f"mengine ya kiasi hicho hicho yaliyorekodiwa {when}. Mfanyakazi anaulizwa "
            f"kuthibitisha kabla ya kurekodiwa — kagua kama hii ni malipo halisi mawili "
            f"tofauti au ilirudiwa kimakosa."
        )
        for op in _UP.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
            Notification.objects.create(
                user=op.user, title='⚠️ Malipo Yanayofanana — Yanasubiri Uthibitisho',
                message=msg, notification_type='warning',
                link_url=f'/debt/{customer.id}/',
            )
            if op.phone:
                normalized = normalize_ke_phone(op.phone)
                if normalized:
                    send_sms_notification_async(msg, normalized)
    except Exception:
        logger.exception('_flag_possible_duplicate_debt_payment failed customer=%s', customer.id)


def _notify_confirmed_duplicate_debt_payment(business, customer, amount, confirmed_by):
    """Owner/manager heads-up once a flagged possible-duplicate payment was
    explicitly confirmed as real and recorded anyway — closes the loop on
    the flag above with who made the call, matching this app's wording/
    accountability standard of explaining decisions, not just flagging
    them."""
    try:
        from .models import Notification
        from accounts.models import UserProfile as _UP
        from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
        who = confirmed_by.get_full_name() or confirmed_by.username
        when = timezone.localtime(timezone.now()).strftime('%d %b, %H:%M')
        msg = (
            f"✓ {who} amethibitisha malipo ya KES {amount:,.0f} kwa {customer.name} kuwa "
            f"halisi (si marudio) — yamerekodiwa — {when}."
        )
        for op in _UP.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
            if op.user_id == confirmed_by.id:
                continue
            Notification.objects.create(
                user=op.user, title='✓ Malipo Yanayofanana — Yamethibitishwa',
                message=msg, notification_type='info',
                link_url=f'/debt/{customer.id}/',
            )
            if op.phone:
                normalized = normalize_ke_phone(op.phone)
                if normalized:
                    send_sms_notification_async(msg, normalized)
    except Exception:
        logger.exception('_notify_confirmed_duplicate_debt_payment failed customer=%s', customer.id)


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
    # 2026-08-09 live request (Roy): "debts that were already paid but a
    # long time ago but were not recorded at the time/day they were paid" —
    # optional backdate, blank means "now" exactly as before (fully
    # backward compatible — no existing caller/test sends this field).
    paid_date_raw = request.POST.get('paid_date', '').strip()
    confirm_duplicate = request.POST.get('confirm_duplicate') == '1'
    session_key = f'debt_dup_pending_{customer.id}'

    # Confirming a flagged duplicate re-uses the ORIGINAL amount/method/
    # notes/scope stashed in the session at flag time (2026-07-31 follow-up
    # to the duplicate-detection fix — Roy asked for "a small confirmation
    # just to be sure" instead of only a background flag), not whatever was
    # resubmitted in the confirm form's own fields — the confirm button only
    # ever sends confirm_duplicate=1 plus an idempotency token, nothing a
    # user could tamper with to change what actually gets recorded. This
    # MUST run before amount_raw is parsed below and before the scope='all'
    # debt_source lookup — the confirm form posts neither field, so parsing
    # amount_raw='' first would always fail before ever reaching the
    # override (found and fixed 2026-07-31, caught by the test suite).
    debt_source_override = None
    if confirm_duplicate:
        pending = request.session.pop(session_key, None)
        if not pending:
            messages.info(
                request,
                _('Hakuna malipo yanayosubiri uthibitisho — huenda tayari yamefutwa au muda umeisha.'),
            )
            return redirect('customer_debt_profile', customer_id=customer_id)
        amount_raw = pending['amount_paid']
        method = pending['payment_method']
        notes = pending['notes']
        paid_date_raw = pending.get('paid_date', '')
        debt_source_override = pending['debt_source']

    if scope == 'all':
        # 2026-08-06 live report (Monsoon Inn) — Roy: "reconciliation should
        # be automated per counter station." The ledger-selector radio in
        # customer_debt_profile.html used to hard-code "Bar" as pre-checked
        # regardless of which ledger the customer actually owed — a manager
        # who forgot to switch it silently recorded a genuine KITCHEN
        # payment tagged source='bar', leaving the kitchen sub-ledger's own
        # unpaid items (and its own outstanding tile) untouched while the
        # money vanished into the wrong bucket. debt_source used to default
        # to 'bar' via request.POST.get(..., 'bar') too, which made the
        # "please specify" validation below unreachable dead code — any
        # missing/blank submission silently became a valid 'bar' choice
        # instead of ever raising the error it was written to raise.
        # Fixed: only auto-resolve when there is genuinely nothing to
        # choose between (no kitchen module at all, or only one of the two
        # ledgers actually has anything owed) — otherwise require an
        # explicit choice, exactly like the template's own JS now enforces
        # client-side.
        debt_source = debt_source_override or request.POST.get('debt_source')
        if not debt_source:
            if not getattr(business, 'has_kitchen', False):
                debt_source = 'bar'
            else:
                bar_out = _get_customer_debt_data(customer, business, 'bar')['outstanding']
                kitchen_out = _get_customer_debt_data(customer, business, 'kitchen')['outstanding']
                if bar_out > 0 and kitchen_out <= 0:
                    debt_source = 'bar'
                elif kitchen_out > 0 and bar_out <= 0:
                    debt_source = 'kitchen'
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

    # Duplicate-payment detection (2026-07-30 urgent live request — Roy:
    # "I am not sure if it became a double payment... fix it in a way that
    # the system could identify if there was a double entry"; 2026-07-31
    # follow-up: "I would like for the system to ask the user if that
    # double payment detection... is true or not, just a small confirmation
    # just to be sure"). CustomerDebtPayment has no natural link back to a
    # specific tab entry to check against, so the best available signal is:
    # another payment of the SAME amount, for the SAME customer, recorded
    # within the last 24 hours. Deliberately never auto-blocks the payment
    # outright (see RecordDebtPaymentRequiresConfirmationTest — two
    # genuinely separate payments of the same round amount must always be
    # ABLE to both go through) — instead requires one explicit human
    # confirmation before recording a second matching payment, rather than
    # silently recording it in the background as the first version of this
    # fix did.
    from datetime import timedelta
    if not confirm_duplicate:
        recent_match = CustomerDebtPayment.objects.filter(
            customer=customer, business=business, amount_paid=amount,
            paid_at__gte=timezone.now() - timedelta(hours=24),
        ).order_by('-paid_at').first()
        if recent_match:
            when = timezone.localtime(recent_match.paid_at).strftime('%d %b, %H:%M')
            request.session[session_key] = {
                'amount_paid': str(amount), 'payment_method': method,
                'notes': notes, 'debt_source': payment_scope, 'matched_when': when,
                'paid_date': paid_date_raw,
            }
            _flag_possible_duplicate_debt_payment(business, customer, amount, recent_match)
            messages.warning(
                request,
                _('⚠️ Malipo ya KES %(amount)s kwa %(customer)s yanafanana na mengine '
                  'yaliyorekodiwa %(when)s. Thibitisha hapa chini kama hii ni malipo '
                  'MAPYA kabisa (si yaleyale yaliyorudiwa kimakosa).')
                % {'amount': f'{amount:,.2f}', 'customer': customer.name, 'when': when}
            )
            return redirect('customer_debt_profile', customer_id=customer_id)

    # Server-side double-submit backstop — see core/idempotency.py. This is a
    # real <form> POST/redirect (no AJAX guard), so a double-click on "Record
    # Payment"/"Ndiyo, Rekodi" or a back-button resubmission would otherwise
    # create a second, real CustomerDebtPayment for the same cash/mpesa
    # payment. Claimed only right before the actual write — not earlier —
    # so a flagged-but-not-yet-confirmed attempt never burns a token for
    # nothing.
    from core.idempotency import claim_checkout_token
    idem_token = (request.POST.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        messages.info(request, _('Malipo haya tayari yamerekodiwa.'))
        return redirect('customer_debt_profile', customer_id=customer_id)

    # Optional backdate (2026-08-09 live request — Roy: a debt genuinely
    # paid off weeks ago, just never recorded at the time, should show its
    # real payment date, not today's). Blank/unparseable/future dates all
    # silently fall back to "now" — a correction feature must never itself
    # block a payment from being recorded.
    paid_at_override = None
    if paid_date_raw:
        try:
            from datetime import datetime as _dt, time as _time
            parsed_date = _dt.strptime(paid_date_raw, '%Y-%m-%d').date()
            if parsed_date <= timezone.localdate():
                paid_at_override = timezone.make_aware(_dt.combine(parsed_date, _time(12, 0)))
        except (ValueError, TypeError):
            pass

    site_url = request.build_absolute_uri('/')[:-1]
    try:
        rcpt, post_data = _do_settle_debt_payment(
            customer=customer, business=business,
            amount=amount, payment_method=method,
            source=payment_scope, notes=notes,
            recorded_by=request.user, site_url=site_url,
            paid_at=paid_at_override,
        )
        if confirm_duplicate:
            # Confirmed despite the flag — still worth a background note to
            # owner/manager (not a block, just a "this was double-checked
            # and confirmed real" trail), matching this app's wording/
            # accountability standard of explaining decisions to everyone
            # the outcome affects.
            _notify_confirmed_duplicate_debt_payment(business, customer, amount, request.user)
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

    ok, detail, _log = fire_debt_reminder(
        business, customer, scope=scope, sent_by=request.user,
        trigger='manual', base_url=request.build_absolute_uri('/').rstrip('/'),
    )
    if ok:
        messages.success(request, detail)
    else:
        messages.warning(request, detail)
    return redirect('customer_debt_profile', customer_id=customer_id)


def fire_debt_reminder(business, customer, scope='all', sent_by=None,
                       trigger='manual', base_url=''):
    """Send one debt-reminder SMS and log it. Returns (ok, message, log_or_None).

    2026-08-23 (Roy): extracted out of send_debt_reminder()'s view body so the
    SAME code path serves both the manual "Send Reminder" button and the
    automatic reminder the defaulter-flagging paths fire before flagging
    anybody (see require_reminder_before_flagging). A view-free signature —
    no `request` — is what makes that possible; `base_url` replaces
    request.build_absolute_uri() for the embedded links.

    ALWAYS writes a DebtReminderLog row, including when nothing could be
    sent (no phone, bad number, gateway refused). "We tried and could not
    reach them" is itself part of the accountability trail, and a missing
    phone must never silently look the same as never having tried.
    """
    from .models import DebtReminderLog
    from core.customer_profile import ensure_ledger_token
    from core.notifications import normalize_ke_phone, send_sms_notification

    data = _get_customer_debt_data(customer, business, scope)
    outstanding = Decimal(str(data['outstanding']))

    def _log(delivered, note):
        return DebtReminderLog.objects.create(
            business=business, customer=customer, sent_by=sent_by,
            trigger=trigger, outstanding_at_send=outstanding,
            phone=(customer.phone or ''), delivered=delivered, note=note[:200],
        )

    if data['outstanding'] <= 0:
        return False, f'{customer.name} hana deni lolote kwa sasa.', None

    if not customer.phone:
        return False, (
            f'{customer.name} hana nambari ya simu — ongeza nambari yake hapa chini '
            f'ili uweze kutuma kikumbusha.'
        ), _log(False, 'Hakuna nambari ya simu')

    normalized_phone = normalize_ke_phone(customer.phone)
    if not normalized_phone:
        return False, (
            f'Nambari ya simu si sahihi: {customer.phone}'
        ), _log(False, f'Nambari si sahihi: {customer.phone}')

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
    if latest_tab_rcpt and base_url:
        pay_link_suffix = f" Lipa hapa: {base_url}/r/{latest_tab_rcpt.token}/"

    # 2026-08-23 (Roy): "it can ride along inside the reminder SMS but as an
    # embedded link i.e. (Access your payment History) which routes the
    # customer to another ledger showing his/her historical, transactional
    # ledger" — so the customer can always check the figure being quoted at
    # them against their own itemised record, rather than being asked to
    # take it on trust.
    ledger_suffix = ''
    if base_url:
        ledger_suffix = f" Historia yako: {base_url}/ledger/{ensure_ledger_token(customer)}/"

    msg = (
        f"{business.name}: {customer.name}, bado una deni la {outstanding_str}. "
        f"Tafadhali lipa ndani ya siku {window}. Asante."
        f"{pay_link_suffix}{ledger_suffix}"
    )

    ok, _detail = send_sms_notification(msg, normalized_phone)
    if ok:
        return True, f'Kikumbusha kimetumwa kwa {customer.name} ({customer.phone}).', _log(True, '')
    return False, (
        f'Kikumbusha hakikutumwa kwa {customer.phone} — angalia salio la Africa\'s Talking.'
    ), _log(False, 'SMS gateway ilikataa')


def require_reminder_before_flagging(business, customer, sent_by=None, base_url=''):
    """Make sure this customer has been asked to pay at least once before they
    get flagged as a defaulter — firing a reminder right now if they never
    have been (2026-08-23, Roy's own enforcement rule).

    Deliberately NON-BLOCKING. It sends and logs; it never refuses to let the
    flag happen. A customer with no phone on file genuinely cannot be
    reminded, and refusing to let the business record a real bad debt over a
    missing phone number would be a worse outcome than the flag landing
    without a reminder — the DebtReminderLog row records exactly which of the
    two happened either way. Never raises: a reminder failing must not take
    down the write-off/void it is attached to.
    """
    from .models import DebtReminderLog
    try:
        if DebtReminderLog.objects.filter(business=business, customer=customer).exists():
            return None
        _ok, _msg, log = fire_debt_reminder(
            business, customer, sent_by=sent_by,
            trigger='auto_flag', base_url=base_url,
        )
        return log
    except Exception:
        logger.exception('Auto-reminder before defaulter flag failed for customer %s', customer.id)
        return None


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

    # 2026-07-24 wording/accountability audit: the old message ("amesamehewa deni la
    # zamani" — "has been forgiven the old debt") overstated what this action does.
    # This only lifts the defaulter block and re-approves credit; any actual
    # outstanding balance is untouched and still owed — that's a separate, explicit
    # write-off decision (approve_write_off), not a side effect of this one. Saying
    # otherwise here would tell a customer or staff member their debt disappeared
    # when it didn't.
    reviewer_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
    clear_message = (
        f"{customer.name} amerejeshewa ruhusa ya kukopa na {reviewer_name} tarehe {when}. "
        f"Deni la zamani (kama lipo) bado linahitaji kulipwa — hii haikufuta deni lolote."
    )

    from .models import Notification
    Notification.objects.create(
        user=request.user,
        title=f"✅ {customer.name} — Ameruhusiwa Tena",
        message=clear_message,
        notification_type='info',
        link_url=f'/debt/{customer.id}/',
    )

    messages.success(request, clear_message)
    return redirect('customer_debt_profile', customer_id=customer_id)


@owner_or_manager_required
@require_POST
def toggle_credit_approval(request, customer_id):
    user_profile = get_user_profile(request)
    customer = get_object_or_404(Customer, id=customer_id, business=user_profile.business)
    customer.credit_approved = not customer.credit_approved
    customer.save(update_fields=['credit_approved'])

    # 2026-07-24 wording/accountability audit: was an English-only django-i18n
    # string (_('Credit %(status)s for %(customer)s.')) in an otherwise all-Swahili
    # flow, with no reviewer or timestamp — inconsistent with every other
    # approve/reject message in this file.
    reviewer_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
    status_sw = 'imeruhusiwa' if customer.credit_approved else 'imezuiwa'
    messages.success(
        request,
        f"Mkopo kwa {customer.name} {status_sw} na {reviewer_name} tarehe {when}.",
    )
    return redirect('customer_debt_profile', customer_id=customer_id)


@owner_or_manager_required
def customer_identity_correct(request):
    """Standalone "search, rename, match, consolidate" entry point — 2026-08-01
    live request: Roy wanted to fix a name mistaken-identity case ("Genro" in
    the debt tracker vs "Jenerali" on an old receipt, the same real customer)
    WITHOUT first having to already be on one specific customer's profile
    page. The per-profile "🔀 Unganisha na Mteja Mwingine" modal
    (customer_debt_profile.html) already did exactly this search+merge+rename
    action, just anchored to whichever profile you happened to be viewing —
    this page is the same flow with search as the FIRST step instead.
    Renders a plain page; POSTs go straight to the existing merge_customer
    endpoint (dynamically targeted at whichever record the owner picks as
    primary), so no separate POST handler is needed here — same backend
    logic (Customer.merge_locked / rename_locked), same audit trail.
    """
    return render(request, 'core/customer_identity_correct.html', {
        'is_owner': getattr(get_user_profile(request), 'is_owner_or_manager', False),
    })


@owner_or_manager_required
def owner_alias_debt_search(request):
    """Dedicated cross-customer search/bulk-transfer page (2026-08-13 live
    request, Roy): "transfer some of the other customers tabs and debts
    that those customers claimed Bosco was to pay for them." The existing
    "🏠 Mmiliki" per-item button on customer_debt_profile.html only ever
    covers ONE customer's own page at a time — this searches unpaid debt
    items across EVERY customer at once (by customer name or item
    description) so items scattered across several different customers'
    records can be multi-selected and proposed to the owner in one submit.

    Reuses _get_customer_debt_data per candidate customer (same technique
    debtors_list_api already established) rather than a raw aggregate
    query — the FIFO/partial-payment math must stay correct per this app's
    own hard rule on how debt is computed; a bare Sum() would double-count
    or miss a partially-paid transaction. Candidates are narrowed first to
    customers who actually have a credit transaction at all (cheap), then
    each candidate's real unpaid list is computed and flattened, then
    filtered by q across both customer name and item description — so a
    search for an ITEM ("Tusker") finds it under whichever customer(s)
    actually owe it, not just customers whose NAME matches.
    """
    business = get_user_profile(request).business
    q = (request.GET.get('q') or '').strip().lower()

    credit_qs = Transaction.objects.filter(
        business=business, payment_method='credit', type='Issue',
    ).exclude(tab_entry__tab__status='OPEN')
    recipient_names = set(n for n in credit_qs.values_list('recipient', flat=True) if n)
    customers = Customer.objects.filter(
        business=business, name__in=recipient_names,
    ).order_by('name')

    rows = []
    for cust in customers:
        data = _get_customer_debt_data(cust, business, scope='all')
        for entry in data.get('unpaid_transactions', []):
            txn = entry['txn']
            item_name = txn.item.description if txn.item_id else ''
            if q and q not in cust.name.lower() and q not in item_name.lower():
                continue
            rows.append({
                'txn_id': txn.id,
                'customer_id': cust.id,
                'customer_name': cust.name,
                'is_owner_alias': cust.is_owner_alias,
                'item_name': item_name,
                'amount': entry['amount'],
                'date': txn.date,
                'days_outstanding': entry['days_outstanding'],
                'is_overdue': entry['is_overdue'],
            })
    rows.sort(key=lambda r: -r['amount'])
    total_amount = round(sum(r['amount'] for r in rows), 2)

    return render(request, 'core/owner_alias_debt_search.html', {
        'rows': rows[:300],
        'q': request.GET.get('q', ''),
        'total_rows': len(rows),
        'total_amount': total_amount,
        'truncated': len(rows) > 300,
    })


@login_required
def debtors_list_api(request):
    """AJAX GET — every customer with outstanding debt right now, station-
    scoped, for the "💳 Wateja wenye Deni" panel on Bar Board / Kitchen
    Board / Quick Sell (2026-08-02 live request, Monsoon Inn). Roy's own
    framing: a customer paying upfront (cash/mpesa/split) might still owe
    money from an earlier tab — a busy staffer has no reason to think to
    check the debt ledger before ringing up a "clean" upfront sale, so put
    the answer where they're already looking instead. Open with no query
    for the full station-scoped list; ?q= narrows it to a name search —
    both share this one endpoint so the panel and the optional search box
    stay in sync automatically.

    Deliberately open to ALL staff (not owner/manager-only like
    customer_search_api) — the whole point is any staff member checking a
    customer before completing a sale, not an owner-side admin tool.

    Reuses the exact same outstanding-balance math as
    _get_customer_debt_data (Transaction.revenue() per credit txn minus
    CustomerDebtPayment.amount_paid, grouped this time across every
    customer in one pass instead of one customer at a time) rather than
    a raw Sum('sale_amount') — see this app's own hard rule on why revenue
    must never be aggregated that way (keg/produce/preset sales don't
    always set sale_amount).

    2026-08-27 live report (Roy): "certain debts show different debts from
    the staff's side compared to the owner's side ... excess entries or
    exaggerations of debt items and amounts out of the control of the
    user." Root-caused to the exact same duplicate-Customer-row mechanism
    _find_duplicate_customer_groups() already documents and flags on the
    owner's debt_dashboard() ("two Eugenes with the same amount and same
    items", 2026-08-09) — Transaction.recipient/CustomerDebtPayment.
    customer_id match differently (a plain name string vs a real FK), so
    two Customer rows sharing one name each independently compute the SAME
    full total_credit while a payment recorded against only ONE of them
    reduced only that row's own paid bucket. debt_dashboard() at least
    WARNS about this (duplicate_groups); this staff-facing panel had no
    such awareness at all — it would show a duplicate name as two separate
    list entries, each quoting the FULL (undivided) outstanding amount,
    which reads as "excess"/"exaggerated" debt that's structurally not the
    customer's fault. Fixed by aggregating `paid` PER NAME (summing every
    Customer id that shares it, not just one) and de-duplicating the
    listing to one row per distinct name — mirrors _find_duplicate_
    customer_groups' own case/whitespace-insensitive key exactly, so this
    can never disagree with what that detector considers "the same
    customer." The underlying duplicate ROWS are still not merged here —
    that stays the owner's explicit "🔀 Sahihisha Jina la Mteja" action —
    this just stops the staff-facing figure from double-counting them.
    """
    up = get_user_profile(request)
    business = up.business
    scope = _debt_scope(up, business)
    q = (request.GET.get('q') or '').strip()

    # 2026-08-15: widened to was_credit=True alongside the live payment_method
    # check — same fix, same reasoning as _get_customer_debt_data's own
    # credit_qs (see Transaction.was_credit's docstring). Without this, a
    # customer whose credit got resolved via a settle path (rather than a
    # recorded CustomerDebtPayment) would be silently under-counted here too,
    # potentially hiding real outstanding debt from this staff-facing panel.
    credit_qs = Transaction.objects.filter(
        Q(payment_method='credit') | Q(was_credit=True),
        business=business, type='Issue',
    ).exclude(tab_entry__tab__status='OPEN').select_related('item__store')
    if scope == 'kitchen':
        credit_qs = credit_qs.filter(item__store__is_kitchen=True)
    elif scope == 'bar':
        credit_qs = credit_qs.filter(item__store__is_kitchen=False)

    # Grouped by the SAME case/whitespace-insensitive key
    # _find_duplicate_customer_groups() uses — a real person's credit can
    # be split across two Transaction.recipient spellings just as easily as
    # across two Customer rows (both are plain strings, not FKs), so this
    # panel's own "how much does this person owe" figure must combine them
    # the same way that detector already considers "the same customer",
    # never the raw exact-string dict keying this used before.
    credit_by_name_key = defaultdict(float)
    display_name_by_key = {}
    for t in credit_qs:
        if not t.recipient:
            continue
        key = t.recipient.strip().lower()
        credit_by_name_key[key] += float(t.revenue())
        display_name_by_key.setdefault(key, t.recipient)

    payment_qs = CustomerDebtPayment.objects.filter(business=business).exclude(reverted=True)
    if scope == 'kitchen':
        payment_qs = payment_qs.filter(source='kitchen')
    elif scope == 'bar':
        payment_qs = payment_qs.filter(source='bar')
    paid_by_customer_id = defaultdict(float)
    for p in payment_qs.values_list('customer_id', 'amount_paid'):
        paid_by_customer_id[p[0]] += float(p[1])

    all_matching_customers = list(
        Customer.objects.filter(business=business).order_by('id')
    )

    # Aggregate paid PER NAME KEY across every Customer row sharing it, so a
    # payment recorded against ANY duplicate (or any case/whitespace variant
    # of the same name) correctly reduces the whole group's outstanding
    # total, not just that one id's.
    paid_by_name_key = defaultdict(float)
    representative_by_key = {}
    for c in all_matching_customers:
        key = (c.name or '').strip().lower()
        if key not in credit_by_name_key:
            continue
        paid_by_name_key[key] += paid_by_customer_id.get(c.id, 0.0)
        representative_by_key.setdefault(key, c)  # first (lowest id) wins

    debtors = []
    for key, cust in representative_by_key.items():
        display_name = display_name_by_key.get(key, cust.name)
        if q and q.lower() not in display_name.lower() and q.lower() not in (cust.name or '').lower():
            continue
        total_credit = credit_by_name_key.get(key, 0.0)
        paid = paid_by_name_key.get(key, 0.0)
        outstanding = max(0.0, total_credit - paid)
        if outstanding > 0:
            debtors.append({
                'customer_id': cust.id,
                'name': cust.name,
                'outstanding': round(outstanding, 2),
                'is_defaulter': bool(cust.is_defaulter),
            })
    debtors.sort(key=lambda d: -d['outstanding'])

    return JsonResponse({'debtors': debtors[:30]})


@owner_or_manager_required
def customer_search_api(request):
    """AJAX GET — search this business's customers by name (owner/manager
    only; used by the "🔀 Unganisha na Mteja Mwingine" merge picker, 2026-07-31
    live request: "similar name flow ... user should be able to change and
    edit the customer's [identity] anywhere it appears in the system"). Plain
    substring match — the OWNER is the one confirming two spellings are the
    same real person, so this only needs to help them FIND the other record,
    not auto-detect the match itself (see Customer.merge_locked's docstring).
    """
    user_profile = get_user_profile(request)
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    customers = Customer.objects.filter(
        business=user_profile.business, name__icontains=q,
    ).order_by('name')[:10]
    return JsonResponse({
        'results': [{'id': c.id, 'name': c.name, 'phone': c.phone} for c in customers],
    })


@owner_or_manager_required
@require_POST
def merge_customer(request, customer_id):
    """Merge another customer record into this one, optionally ALSO
    correcting the resulting name in the same submit (2026-08-01 live
    request — Roy: "search for Genro and edit his name to General ... and
    match it to Jenerali ... and consolidate the two, just that simple").
    See Customer.merge_locked's / rename_locked's docstrings for the full
    reasoning and reassignment list. `customer_id` in the URL is always the
    KEPT identity (the profile page the owner is standing on when they
    trigger this); `absorb_id` (POST, optional) is a duplicate being folded
    in and deleted; `new_name` (POST, optional) renames the resulting
    identity. At least one of the two must be given.
    """
    user_profile = get_user_profile(request)
    business = user_profile.business
    keep = get_object_or_404(Customer, id=customer_id, business=business)

    absorb_id = request.POST.get('absorb_id', '').strip()
    new_name = (request.POST.get('new_name') or '').strip()

    if not absorb_id.isdigit() and not new_name:
        messages.error(request, 'Chagua mteja wa kuunganisha naye, au andika jina sahihi.')
        return redirect('customer_debt_profile', customer_id=customer_id)

    old_name = None
    try:
        if absorb_id.isdigit():
            keep, absorbed_id, old_name = Customer.merge_locked(keep.id, int(absorb_id), business)
        if new_name and new_name != keep.name:
            keep = Customer.rename_locked(keep.id, business, new_name)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('customer_debt_profile', customer_id=customer_id)

    reviewer_name = request.user.get_full_name() or request.user.username
    when = timezone.localtime(timezone.now()).strftime('%d %b %Y, %H:%M')
    if old_name:
        msg = (
            f"{old_name} ameunganishwa na {keep.name} na {reviewer_name} tarehe {when} — "
            f"deni, risiti na tabs zote za {old_name} sasa ziko chini ya {keep.name}."
        )
    else:
        msg = f"Jina limesahihishwa kuwa {keep.name} na {reviewer_name} tarehe {when}."
    messages.success(request, msg)
    return redirect('customer_debt_profile', customer_id=keep.id)


@login_required
@require_POST
def link_customer_as_owner(request, customer_id):
    """Explicit, owner/manager-confirmed action: mark this Customer record
    as actually being the business owner (2026-08-13 live request, Roy —
    a debt customer named "Bosco" IS the owner Bosco, and every item under
    that name should move to his own Mmiliki Alichukua ledger). Deliberately
    NOT automatic name-matching (see the original Mmiliki Alichukua
    design's own "the system knowing a name is the owner is NOT a
    Customer-name-matching heuristic" rule) — this flag is only ever set
    here, by an explicit tap on a specific Customer record's own profile.

    Doubles as the "resync" action once already linked — pressing it again
    proposes whatever's newly unpaid since the last time, safely
    idempotent for anything already proposed/transferred (see
    OwnerConsumptionTransferRequest.propose_to_owner_locked's own
    skip-rather-than-error behaviour for an id already pending). Per Roy's
    explicit confirmation, this NEVER auto-accepts — every proposal still
    lands in the normal pending/accept queue on Mmiliki Alichukua."""
    up = get_user_profile(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)
    business = up.business
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    was_linked = customer.is_owner_alias
    if not was_linked:
        customer.is_owner_alias = True
        customer.save(update_fields=['is_owner_alias'])

    # Always pulls the FULL cross-ledger picture (bar + kitchen), regardless
    # of the acting owner/manager's own station scope — deliberately, since
    # this action already requires is_owner_or_manager, who see both anyway.
    data = _get_customer_debt_data(customer, business, scope='all')
    txn_ids = [e['txn'].id for e in data.get('unpaid_transactions', [])]

    proposed_count = 0
    proposed_total = 0.0
    if txn_ids:
        from .models import OwnerConsumptionTransferRequest as _OCTR
        try:
            reqs = _OCTR.propose_to_owner_locked(
                txn_ids, business, request.user,
                note='Kuunganishwa kama Mmiliki' if not was_linked else 'Kusawazisha kwa Mmiliki',
            )
            proposed_count = len(reqs)
            proposed_total = sum(float(r.source_txn.sale_amount or 0) for r in reqs)
        except ValueError:
            pass  # nothing NEW to propose right now — not an error for a resync

    actor_name = request.user.get_full_name() or request.user.username
    if not was_linked:
        base_msg = f"{actor_name} ameunganisha {customer.name} kama Mmiliki."
    else:
        base_msg = f"{actor_name} amesawazisha deni la {customer.name} kwa Mmiliki."
    if proposed_count:
        msg = (
            f"{base_msg} Vitu {proposed_count} (KES {proposed_total:,.0f}) vinasubiri "
            f"uamuzi wako kwenye Mmiliki Alichukua."
        )
    else:
        msg = f"{base_msg} Hakuna deni jipya la kuhamisha kwa sasa."

    from .models import Notification
    from accounts.models import UserProfile as _UP
    for op in _UP.objects.filter(business=business, role__in=('owner', 'manager')).exclude(user=request.user):
        Notification.objects.create(
            user=op.user, title='🏠 Mteja Ameunganishwa na Mmiliki', message=msg,
            notification_type='info', link_url='/stock/owner-consumption/list/',
        )
    return JsonResponse({
        'ok': True, 'message': msg, 'is_owner_alias': True,
        'proposed_count': proposed_count, 'proposed_total': proposed_total,
    })


@login_required
@require_POST
def unlink_customer_as_owner(request, customer_id):
    """Reverses link_customer_as_owner's flag — reversible, since a mis-tap
    or a genuinely different person sharing the owner's name is always
    possible. Never touches any already-created transfer request or
    already-reclassified transaction — accept()/reject() already resolved
    those independently on their own merits; this only turns off future
    resync-button eligibility and the tab_check_api similar-name hint."""
    up = get_user_profile(request)
    if not up or not getattr(up, 'is_owner_or_manager', False):
        return JsonResponse({'ok': False, 'error': 'Owner or manager only'}, status=403)
    customer = get_object_or_404(Customer, id=customer_id, business=up.business)
    customer.is_owner_alias = False
    customer.save(update_fields=['is_owner_alias'])
    actor_name = request.user.get_full_name() or request.user.username
    msg = f"{actor_name} ameondoa uunganisho wa {customer.name} na Mmiliki."
    return JsonResponse({'ok': True, 'message': msg, 'is_owner_alias': False})


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

    # Self-service "Ilikuwa Kosa" executes with real, immediate effect (stock
    # restored right away) — matching remove_tab_entry's own shift gate for
    # non-owner/manager staff, since it's the same class of correction. The
    # plain write-off REQUEST step (this endpoint's original behavior) has
    # never required a shift — unaffected, only the self-service erase branch
    # gets this check.
    if (
        request.POST.get('is_mistake') == '1'
        and not up.business.debt_erase_requires_approval
        and not up.is_owner_or_manager
    ):
        from core.shift_views import get_active_staff_shift
        if get_active_staff_shift(up, up.business) is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift yako kwanza.'}, status=403)

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

    # 2026-08-28 live request (Roy, with a debt-tracker screenshot): a
    # single Transaction consolidating several identical units into one
    # line ("Kc smooth 250ml — KES 800" = 2 units at 400 each) had no way
    # to Futa just ONE of them — the whole line always went together.
    # qty_to_erase is optional and blank/omitted/"all of it" behaves
    # EXACTLY as before (the whole, un-split transaction). A genuine
    # partial pick splits it first (Transaction.split_quantity_locked) and
    # every remaining line below operates on the SPLIT-OFF portion only —
    # the original transaction's own remaining qty/amount stays completely
    # untouched, still owed, still visible as its own separate line.
    qty_to_erase_raw = (request.POST.get('qty_to_erase') or '').strip()
    if qty_to_erase_raw:
        try:
            from decimal import Decimal as _Decimal
            qty_to_erase = _Decimal(qty_to_erase_raw)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Idadi si sahihi.'}, status=400)
        full_qty = abs(txn.qty)
        if 0 < qty_to_erase < full_qty:
            try:
                _orig_txn, txn = Transaction.split_quantity_locked(
                    txn_id=txn.id, business=up.business,
                    qty_to_split=qty_to_erase, staff_user=request.user,
                )
            except ValueError as e:
                return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        elif qty_to_erase <= 0 or qty_to_erase > full_qty:
            return JsonResponse({'ok': False, 'error': 'Idadi lazima iwe kati ya 1 na jumla ya vitengo.'}, status=400)
        # qty_to_erase == full_qty: no split needed, proceeds on the whole txn below.

    # 2026-07-31 live request: "when an item is out on a running tab or debt
    # section... when the item is erased the system should append the
    # balances accordingly." A genuinely mistaken debt entry (wrong item/
    # customer, item never actually given out) is different from a real,
    # uncollectable debt — it must restore stock, unlike an ordinary
    # write-off. Roy's explicit call: self-service by default (any staff
    # with an open shift executes it immediately, matching the tab-side
    # "✕ Futa" behavior), with an owner-activatable opt-in
    # (Business.debt_erase_requires_approval) that routes it through the
    # same request/approve/reject lifecycle as a real write-off instead —
    # approvable by a manager granted UserProfile.can_approve_debt_erase
    # (never owner-only like a real write-off's final decision).
    is_mistake = request.POST.get('is_mistake') == '1'
    request_type = WriteOffRequest.TYPE_ERASE_MISTAKE if is_mistake else WriteOffRequest.TYPE_WRITEOFF

    customer_name = txn.recipient or ''
    item_name = txn.item.description if txn.item_id else '?'
    amount = float(txn.revenue())
    requester_name = request.user.get_full_name() or request.user.username

    wo = WriteOffRequest.objects.create(
        transaction=txn,
        requested_by=request.user,
        reason=reason,
        customer_name_cache=customer_name,
        request_type=request_type,
    )

    # 2026-09-02 live report (Roy): "owner should not see... who is he
    # requesting to approve deletion when he is the owner surely." A REAL
    # write-off (is_mistake=False) used to ALWAYS create a PENDING
    # WriteOffRequest and wait for a separate approve step, even when the
    # OWNER himself submitted it — since the final decision on a real
    # write-off is owner-only anyway (see _can_approve_debt_action), the
    # owner ended up having to approve his own request, a confusing,
    # meaningless extra click. A MANAGER submitting a real write-off still
    # goes through the pending state below — that IS genuine two-person
    # control, since a manager can only recommend (manager_review_write_
    # off), never give the final decision. is_mistake's own existing
    # self-service gate (debt_erase_requires_approval) is untouched —
    # `or up.is_owner` only ever ADDS a bypass, never removes one.
    if (is_mistake and not up.business.debt_erase_requires_approval) or up.is_owner:
        # Self-service — no approval needed, execute right now. Still goes
        # through _execute_write_off_approval() (the same code approve_write_off
        # uses) so a self-executed erase/write-off is indistinguishable in
        # its effect from an approved one — only who/when differs.
        result = _execute_write_off_approval(
            wo, request.user, self_service=True,
            base_url=request.build_absolute_uri('/').rstrip('/'),
        )
        return JsonResponse({
            'ok': True,
            'request_id': wo.id,
            'executed': True,
            'message': result['message'],
        })

    # Notify all owners and managers (not the requester themselves)
    from .models import Notification
    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async

    targets = _UP.objects.filter(
        business=up.business, role__in=['owner', 'manager'],
    ).exclude(user=request.user).select_related('user')

    type_label = 'kosa (rejesha stock)' if is_mistake else 'deni'
    for om in targets:
        Notification.objects.create(
            user=om.user,
            title='📝 Ombi la Kufuta Kiingilio',
            message=(
                f"{requester_name} anaomba kufuta kama {type_label}: {item_name} "
                f"KES {amount:,.0f} ({customer_name}). Sababu: {reason}"
            ),
            notification_type='warning',
            link_url='/debt/write-offs/pending/',
        )
        if om.phone:
            normalized = normalize_ke_phone(om.phone)
            if normalized:
                sms = (
                    f"{up.business.name}: {requester_name} anaomba kufuta kiingilio kama "
                    f"{type_label}: {item_name} KES {amount:,.0f} ({customer_name}). "
                    f"Sababu: {reason}. Angalia app kuidhinisha au kukataa."
                )
                send_sms_notification_async(sms, normalized)

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
    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async

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
            link_url='/debt/write-offs/pending/',
        )
        if ow.phone:
            normalized = normalize_ke_phone(ow.phone)
            if normalized:
                send_sms_notification_async(
                    f"{up.business.name}: Meneja {manager_name} {verdict_sw} write-off "
                    f"{item_name} KES {amount:,.0f}. Angalia app kufanya uamuzi wa mwisho.",
                    normalized,
                )

    label = 'Imependekezwa' if verdict == 'approved' else 'Imekataliwa na Meneja'
    return JsonResponse({'ok': True, 'verdict': verdict, 'label': label})


def _can_approve_debt_action(up, wo):
    """Who may approve/reject a WriteOffRequest — owner always; a manager
    only for an 'erase_mistake' request AND only when explicitly granted
    UserProfile.can_approve_debt_erase (2026-07-31). A real 'writeoff' (real,
    uncollectable debt) request's final decision always stays owner-only,
    exactly as before this feature — unchanged for every pre-existing test.
    """
    if up.is_owner:
        return True
    if (
        wo.request_type == WriteOffRequest.TYPE_ERASE_MISTAKE
        and up.role == 'manager'
        and getattr(up, 'can_approve_debt_erase', False)
    ):
        return True
    return False


def _execute_write_off_approval(wo, approver, self_service=False, base_url=''):
    """Shared execution core for approve_write_off — also called directly by
    request_write_off() for the self-service 'erase_mistake' path (Business.
    debt_erase_requires_approval=False), so a self-executed erase produces
    byte-for-byte the same effect as an approved one, just with no separate
    approval step. Returns {item_name, customer_name, amount, message}.
    """
    txn = wo.transaction
    item_name = txn.item.description if txn.item_id else '?'
    customer_name = wo.customer_name_cache or txn.recipient or '—'
    amount = float(txn.revenue())
    reviewer_name = approver.get_full_name() or approver.username
    is_mistake = wo.request_type == WriteOffRequest.TYPE_ERASE_MISTAKE

    # 2026-08-23 (Roy): "a rule that forces an automatic reminder to get sent
    # before a customer be flagged as a defaulter." Fired HERE, before the
    # void below — not next to the flag itself further down, where it looks
    # like it belongs. By that point the write-off has already neutralised
    # the transaction, so _get_customer_debt_data() correctly reports nothing
    # outstanding and fire_debt_reminder() rightly declines to send: the
    # enforcement would silently never fire at all. Caught by its own
    # end-to-end test. Skipped for erase_mistake, which never flags anybody
    # (it was the business's own data-entry error, not the customer's debt).
    _flag_customer = None
    if not is_mistake and customer_name and customer_name != '—':
        # 2026-09-03 — name__iexact, not a bare =. See core/views.py's
        # add_transaction comment for the full root-cause explanation.
        _flag_customer = Customer.objects.filter(
            business=wo.transaction.business, name__iexact=customer_name,
        ).first()
        if _flag_customer is not None:
            require_reminder_before_flagging(
                wo.transaction.business, _flag_customer,
                sent_by=approver, base_url=base_url,
            )

    # Execute the void
    txn.payment_method = 'void'
    txn.recipient = ''
    update_fields = ['payment_method', 'recipient']
    if is_mistake:
        # 2026-07-31 live request: "when the item is erased the system
        # should append the balances accordingly" — unlike a real write-off
        # (goods really left the shelf, only the receivable is forgiven),
        # a mistaken entry never should have deducted stock in the first
        # place — zeroing qty restores the balance exactly the way removing
        # a live tab entry already does (remove_tab_entry, same mechanism).
        #
        # 2026-08-19 fix (bar-ops transactional audit): this used to zero
        # qty ALONE — correct for a plain Item (current_balance() sums qty
        # directly, so it self-corrected), but a debt/credit transaction can
        # just as easily be a converted keg pour, produce-bunch, or kitchen-
        # batch sale, and those track "revenue collected" via their own
        # separate running counters (KegBarrel.revenue_collected/
        # volume_dispensed_ml, etc.) that never recompute from a Transaction
        # sum — erasing the sale without reversing that counter left the
        # barrel's/bunch's/batch's own envelope permanently overstated,
        # corrupting keg reconciliation, Bar Performance analytics, and the
        # sell-modal's remaining-envelope gate. Now uses the same shared
        # helper remove_tab_entry()/void_direct_transaction() already rely
        # on for this exact "the item was never really served" correction.
        from core.keg_views import _reverse_stock_movement_envelope
        _reverse_stock_movement_envelope(txn)
        txn.qty = Decimal('0')
        update_fields.append('qty')
    txn.save(update_fields=update_fields)

    wo.status = WriteOffRequest.STATUS_APPROVED
    wo.reviewed_by = approver
    wo.reviewed_at = timezone.now()
    wo.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    # Same signal as void_tab: this credit is unrecoverable and the business
    # is eating the loss, so flag the customer the same way a voided debt tab
    # does — was previously only done for void_tab, leaving this equally-final
    # "written off, uncollectable" path invisible to future credit decisions.
    # Never fair to flag the customer for a data-entry mistake that wasn't
    # their fault — skipped entirely for the erase_mistake type.
    if _flag_customer is not None:
        Customer.objects.filter(pk=_flag_customer.pk).update(is_defaulter=True)

    # Remove any Haki deduction the manager may have already created (owner overrides)
    SalaryDeduction.objects.filter(write_off=wo).delete()

    # Update recent receipts so the line explains itself on the public receipt page
    _mark_receipt_write_off(wo.transaction.business, customer_name, item_name, amount, when=wo.reviewed_at)

    from .models import Notification
    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async

    verb = 'amefuta (kosa — stock imerejeshwa)' if is_mistake else 'amefuta'
    if self_service:
        # 2026-09-02 — self_service now also fires for an owner's own REAL
        # write-off (is_mistake=False), which never restores stock (the
        # goods genuinely left — only the receivable is forgiven). This
        # message used to unconditionally claim "Stock imerejeshwa" (stock
        # restored), which was only ever true for the is_mistake branch —
        # would have been a factually wrong confirmation for a real
        # write-off otherwise.
        if is_mistake:
            message = f'Imefutwa — KES {amount:,.0f} ({item_name}) kutoka kwa deni la {customer_name}. Stock imerejeshwa.'
        else:
            message = f'Imefutwa — KES {amount:,.0f} ({item_name}) kutoka kwa deni la {customer_name}.'
    else:
        title_verb = '✅ Kosa Limefutwa' if is_mistake else '✅ Write-off Imeidhinishwa'
        Notification.objects.create(
            user=approver,
            title=title_verb,
            message=f"{reviewer_name} {verb}: {item_name} KES {amount:,.0f} ({customer_name}).",
            notification_type='info',
            link_url='/debt/write-offs/pending/',
        )
        # Notify the requesting staff member
        if wo.requested_by and wo.requested_by_id != approver.id:
            Notification.objects.create(
                user=wo.requested_by,
                title=title_verb,
                message=f"{reviewer_name} ame{('idhinisha' if not is_mistake else 'idhinisha ombi lako la kosa')}: "
                        f"{item_name} KES {amount:,.0f} ({customer_name}) imefutwa"
                        + (' — stock imerejeshwa.' if is_mistake else '.'),
                notification_type='info',
                link_url='/debt/write-offs/pending/',
            )
            from accounts.models import UserProfile as _UP
            sp = _UP.objects.filter(user=wo.requested_by, business=wo.transaction.business).first()
            if sp and sp.phone:
                normalized = normalize_ke_phone(sp.phone)
                if normalized:
                    send_sms_notification_async(
                        f"{wo.transaction.business.name}: {reviewer_name} ameidhinisha ombi lako — "
                        f"{item_name} KES {amount:,.0f} imefutwa kutoka kwa deni."
                        + (' Stock imerejeshwa.' if is_mistake else ''),
                        normalized,
                    )
        message = f'Imeidhinishwa — KES {amount:,.0f} ({item_name}) imefutwa kutoka kwa deni la {customer_name}.'

    return {
        'item_name': item_name,
        'customer_name': customer_name,
        'amount': amount,
        'message': message,
    }


@login_required
@require_POST
def approve_write_off(request, req_id):
    """Owner (always) or a permitted manager (erase_mistake requests only —
    see _can_approve_debt_action) approves a write-off/erase request —
    executes the void immediately via _execute_write_off_approval().

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

    if not _can_approve_debt_action(up, wo):
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kuidhinisha ombi hili.'}, status=403)

    if wo.status == WriteOffRequest.STATUS_APPROVED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lishaidhinishwa tayari.'}, status=400)
    if wo.status == WriteOffRequest.STATUS_REJECTED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lilikataliwa — haliwezi kuidhinishwa tena.'}, status=400)

    result = _execute_write_off_approval(
        wo, request.user,
        base_url=request.build_absolute_uri('/').rstrip('/'),
    )

    return JsonResponse({
        'ok': True,
        'status': 'approved',
        'voided_amount': result['amount'],
        'customer': result['customer_name'],
        'message': result['message'],
    })


@login_required
@require_POST
def reject_write_off(request, req_id):
    """Owner (always) or a permitted manager (erase_mistake requests only —
    see _can_approve_debt_action) rejects a request.

    For a real 'writeoff' request (unchanged): FINAL decision, creates a Haki
    salary deduction against the requesting staff member — asking to forgive
    real money that should have been collected is a real cost to flag.
    For an 'erase_mistake' request (2026-07-31): no Haki deduction — flagging
    something as a possible data-entry mistake and being told it's actually a
    real debt is not the same failure as trying to write off real money, so
    it is not penalised the same way. The transaction stays as a credit
    either way (never voided by a rejection).
    """
    up = get_user_profile(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    wo = get_object_or_404(WriteOffRequest, id=req_id, transaction__business=up.business)

    if not _can_approve_debt_action(up, wo):
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa ya kukataa ombi hili.'}, status=403)

    if wo.status == WriteOffRequest.STATUS_REJECTED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lilikataliwa tayari.'}, status=400)
    if wo.status == WriteOffRequest.STATUS_APPROVED:
        return JsonResponse({'ok': False, 'error': 'Ombi hili lishaidhinishwa — haliwezi kukataliwa tena.'}, status=400)

    is_mistake = wo.request_type == WriteOffRequest.TYPE_ERASE_MISTAKE
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

    from accounts.models import UserProfile as _UP
    from core.notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
    from .models import Notification

    deducted_from = ''
    if is_mistake:
        # No Haki penalty — see docstring. Still explains the outcome to the
        # requester so the debt line's status isn't a silent surprise.
        if wo.requested_by:
            Notification.objects.create(
                user=wo.requested_by,
                title='❌ Ombi la Kosa Limekataliwa',
                message=(
                    f"{reviewer_name} amekataa ombi lako — {item_name} KES {amount:,.0f} "
                    f"({customer_name}) inabaki kwenye deni kama iliyokuwa."
                ),
                notification_type='warning',
                link_url='/debt/write-offs/pending/',
            )
            staff_profile = _UP.objects.filter(user=wo.requested_by, business=up.business).first()
            if staff_profile and staff_profile.phone:
                normalized = normalize_ke_phone(staff_profile.phone)
                if normalized:
                    send_sms_notification_async(
                        f"{up.business.name}: Ombi lako la 'ilikuwa kosa' kwa {item_name} "
                        f"KES {amount:,.0f} limekataliwa — inabaki kwenye deni.",
                        normalized,
                    )
    elif wo.requested_by and not wo.haki_deduction_created:
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
            deducted_from = staff_profile.user.get_full_name() or staff_profile.user.username

            Notification.objects.create(
                user=wo.requested_by,
                title='❌ Ombi la Write-off Limekataliwa',
                message=(
                    f"Mmiliki amekataa: {item_name} KES {amount:,.0f} ({customer_name}). "
                    f"KES {amount:,.0f} itaondolewa kwenye mshahara wako."
                ),
                notification_type='warning',
                link_url='/debt/write-offs/pending/',
            )
            if staff_profile.phone:
                normalized = normalize_ke_phone(staff_profile.phone)
                if normalized:
                    send_sms_notification_async(
                        f"{up.business.name}: Ombi lako la kufuta {item_name} "
                        f"KES {amount:,.0f} limekataliwa. KES {amount:,.0f} "
                        f"itaondolewa kwenye mshahara wako wa {period}.",
                        normalized,
                    )

    Notification.objects.create(
        user=request.user,
        title='❌ Ombi Limekataliwa',
        message=(
            f"{reviewer_name} alikataa: {item_name} KES {amount:,.0f} ({customer_name})."
            + (' Haki deduction imetumwa.' if deducted_from else '')
        ),
        notification_type='warning',
        link_url='/debt/write-offs/pending/',
    )

    return JsonResponse({
        'ok': True,
        'status': 'rejected',
        'message': (
            f'Imekataliwa — Haki deduction ya KES {amount:,.0f} imetumwa kwa {deducted_from}.'
            if deducted_from else
            f'Imekataliwa — {item_name} inabaki kwenye deni la {customer_name}.'
        ),
    })


@login_required
@owner_or_manager_required
def pending_write_offs(request):
    """Owner/manager: list of all pending write-off requests for this business."""
    up = get_user_profile(request)
    if not up:
        return redirect('login')

    pending = list(
        WriteOffRequest.objects
        .filter(transaction__business=up.business, status=WriteOffRequest.STATUS_PENDING)
        .select_related(
            'transaction__item', 'requested_by', 'manager_by',
            'transaction__item__store',
        )
        .order_by('-created_at')
    )
    # 2026-07-31 — a manager granted can_approve_debt_erase gets the FINAL
    # Idhinisha/Kataa buttons for an 'erase_mistake' request specifically
    # (see _can_approve_debt_action), never for a real 'writeoff' — those
    # keep the existing owner-only-final / manager-advisory-only split
    # completely unchanged. Ad-hoc plain-name attribute, per this app's own
    # rule against leading-underscore template attributes.
    for wo in pending:
        wo.can_i_approve = _can_approve_debt_action(up, wo)

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


def _mark_receipt_write_off(business, customer_name, item_name, amount, when=None):
    """Add a write-off marker to the customer's recent receipts.

    The receipt public page reads receipt.meta['write_offs'] and, per the
    2026-07-24 wording/accountability audit, now marks the matching line as
    written off WITH an explanation and a timestamp rather than silently
    hiding it — a line that just vanishes from a customer's own bill with no
    trace reads as a mistake or something to question, not as the business
    telling them their debt for that item was cleared. Matches by item name +
    amount. Handles the duplicate-entry case by consuming one match per
    write-off entry (a list, not a set).
    """
    from .models import Receipt
    import datetime
    when = when or timezone.now()
    since = timezone.localdate() - datetime.timedelta(days=14)
    receipts = (
        Receipt.objects
        .filter(business=business, customer_name=customer_name, created_at__date__gte=since)
        .exclude(payment_method='statement')
    )
    when_local = timezone.localtime(when)
    for rcpt in receipts:
        meta = rcpt.meta or {}
        wo_list = meta.setdefault('write_offs', [])
        wo_list.append({
            'name': item_name,
            'amount': round(amount, 2),
            'written_off_at': when_local.strftime('%d %b %Y, %H:%M'),
        })
        rcpt.meta = meta
        rcpt.save(update_fields=['meta'])
