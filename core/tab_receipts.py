from django.db.utils import NotSupportedError
from django.utils import timezone


def _receipt_linked_to(business, tab_id):
    """Receipt.objects.filter(meta__linked_tab_ids__contains=[tab_id]).first(),
    tolerant of SQLite (used in local dev/tests), which doesn't support the
    `contains` lookup on JSONField — only PostgreSQL (production) does. On
    SQLite this degrades to "no match" rather than crashing; on PostgreSQL
    behavior is unchanged.
    """
    from .models import Receipt
    try:
        return Receipt.objects.filter(
            business=business, meta__linked_tab_ids__contains=[tab_id],
        ).first()
    except NotSupportedError:
        return None


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
        other_tabs_qs = other_tabs_qs.filter(customer_name__iexact=tab.customer_name)
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
        candidate = Receipt.objects.filter(
            business=business,
            customer_name__iexact=tab.customer_name,
            created_at__date=timezone.localdate(),
        ).exclude(payment_method='statement').order_by('-created_at').first()
        if candidate:
            _link_tab_into_receipt(candidate, tab.id)
            return candidate, True

    return None, False


def _link_tab_into_receipt(receipt, tab_id):
    linked = list(receipt.meta.get('linked_tab_ids') or [])
    if tab_id not in linked:
        linked.append(tab_id)
        receipt.meta['linked_tab_ids'] = linked
        receipt.save(update_fields=['meta'])
