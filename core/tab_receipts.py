from django.db.utils import NotSupportedError
from django.utils import timezone


def _safe_linked_query(qs, tab_ids):
    """Return a materialized list of qs rows whose meta.linked_tab_ids contains
    any of tab_ids.

    `meta__linked_tab_ids__contains` is a JSONField `contains` lookup that only
    PostgreSQL (production) supports — SQLite (local dev/tests) raises
    NotSupportedError. Returns [] in that case so callers degrade to "no match"
    instead of a 500. Single source of truth for this guard — core/keg_views.py
    and core/kitchen_views.py used to build the same Q() chain unguarded
    (Sprint K9 audit).

    Deliberately returns a list, not a queryset: querysets are lazy, so a
    try/except around `.filter()` alone never actually catches anything — the
    NotSupportedError only fires when the caller evaluates it later (`.first()`,
    iteration), by which point it has escaped this function's guard entirely.
    Evaluating eagerly here (via list()) is what makes the guard real.
    """
    from django.db.models import Q
    if not tab_ids:
        return []
    q = Q()
    for tid in tab_ids:
        q |= Q(meta__linked_tab_ids__contains=[tid])
    try:
        return list(qs.filter(q))
    except NotSupportedError:
        return []


def _receipt_linked_to(business, tab_id):
    """Receipt.objects.filter(meta__linked_tab_ids__contains=[tab_id]).first(),
    tolerant of SQLite (used in local dev/tests), which doesn't support the
    `contains` lookup on JSONField — only PostgreSQL (production) does. On
    SQLite this degrades to "no match" rather than crashing; on PostgreSQL
    behavior is unchanged.
    """
    from .models import Receipt
    results = _safe_linked_query(
        Receipt.objects.filter(business=business), [tab_id]
    )
    return results[0] if results else None


def resolve_master_receipt(business, tab):
    """Find (and link, if needed) the one Receipt a customer's tab should share.

    Every counter (Bar Board, Kitchen, Quick Sell) that opens a tab for a
    customer should route them to the SAME receipt/PIN wherever possible, so
    scanning the wall QR always shows the customer their whole running bill
    regardless of which counter rang it up. Single source of truth for all
    three — previously each counter had its own partial copy of this logic
    and they drifted (Bar Board checked everything, Kitchen only checked Bar,
    Quick Sell checked nothing beyond its own tab).

    Priority order:
      1. This tab already has its own receipt (subsequent rounds on the same tab).
      2. This tab's id already appears in another receipt's linked_tab_ids
         (linked by an earlier call from another counter).
      3. Another OPEN tab for the same customer — any source, any counter —
         already has a receipt. Link this tab into it.
      4. Any receipt issued today for this customer name, any source — covers
         a Deni/credit receipt with no live tab attached.

    Returns (master_receipt_or_None, freshly_linked). `freshly_linked` is True
    only when priority 3 or 4 matched — i.e. this tab's id was JUST added to
    another receipt's linked_tab_ids in this call, which callers use to decide
    whether to send a "your order was added to your existing tab" SMS. It's
    False for priority 1/2 (nothing new — the customer already knows about
    this receipt) and for a brand-new receipt (caller has its own "tab opened"
    SMS for that case).
    """
    from .models import BarTab, Receipt

    master = Receipt.objects.filter(business=business, meta__tab_id=tab.id).first()
    if master:
        return master, False

    master = _receipt_linked_to(business, tab.id)
    if master:
        return master, False

    other_tabs_qs = BarTab.objects.filter(
        business=business, status='OPEN',
    ).exclude(id=tab.id).order_by('id')
    if tab.customer_id:
        other_tabs_qs = other_tabs_qs.filter(customer_id=tab.customer_id)
    elif tab.customer_name:
        # 2026-08-16 live report (Roy, Monsoon Inn): confirmed via
        # diagnose_receipt that this exact fallback (or Priority 4 below)
        # had merged two DIFFERENT real customers who both happen to be
        # named "Peter" onto one shared receipt/PIN — Receipt #705 ended up
        # linked to two BarTabs resolving to two different Customer records
        # (203 and 228). Our own tab has no resolved identity to check here
        # (that's why we're in this branch at all), so the only safe
        # tightening available is to never match a candidate that DOES have
        # a resolved identity of its own — an already-identified customer
        # is a real, distinct person on record, and a bare name match is
        # too weak a signal to fold an unidentified tab into their bill.
        # Two genuinely anonymous walk-ins sharing a name (both
        # customer_id=None) is the one case this still safely merges —
        # lowest stakes, since neither side is tied to any debt/Customer
        # record yet.
        other_tabs_qs = other_tabs_qs.filter(
            customer_name__iexact=tab.customer_name, customer_id__isnull=True,
        )
    else:
        other_tabs_qs = other_tabs_qs.none()

    for other in other_tabs_qs:
        candidate = Receipt.objects.filter(business=business, meta__tab_id=other.id).first()
        if candidate is None:
            candidate = _receipt_linked_to(business, other.id)
        if candidate:
            _link_tab_into_receipt(candidate, tab.id)
            return candidate, True

    if tab.customer_name:
        candidates = Receipt.objects.filter(
            business=business,
            customer_name__iexact=tab.customer_name,
            created_at__date=timezone.localdate(),
        ).exclude(payment_method='statement').order_by('-created_at')
        for candidate in candidates:
            if _candidate_conflicts_with_customer(business, candidate, tab):
                continue
            _link_tab_into_receipt(candidate, tab.id)
            return candidate, True

    return None, False


def _candidate_conflicts_with_customer(business, candidate_receipt, tab):
    """True if candidate_receipt is already tied (via its own linked tabs)
    to a DIFFERENT resolved Customer than `tab` — i.e. linking them would
    silently merge two different real people who just share a name (the
    2026-08-16 live bug — see resolve_master_receipt's own comment above).

    Only a real signal when `tab` itself has a resolved customer_id; with
    nothing to compare against (tab.customer_id is None), this can't tell
    conflict from coincidence and returns False — a Deni/credit-only
    receipt with no live tab at all (Priority 4's own stated legitimate
    use case) has nothing to check against either and is unaffected.
    """
    if not tab.customer_id:
        return False
    from .models import BarTab
    linked_ids = ([candidate_receipt.meta.get('tab_id')] if candidate_receipt.meta.get('tab_id') else []) \
        + list(candidate_receipt.meta.get('linked_tab_ids') or [])
    if not linked_ids:
        return False
    return BarTab.objects.filter(
        id__in=linked_ids, business=business,
    ).exclude(customer_id=tab.customer_id).exclude(customer_id__isnull=True).exists()


def _link_tab_into_receipt(receipt, tab_id):
    linked = list(receipt.meta.get('linked_tab_ids') or [])
    if tab_id not in linked:
        linked.append(tab_id)
        receipt.meta['linked_tab_ids'] = linked
        receipt.save(update_fields=['meta'])
