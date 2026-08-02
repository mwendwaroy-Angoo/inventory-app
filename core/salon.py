"""
UBA §9.2 (Sprint S1) — salon services, recipes, and the side-client
detector. Reuses Transaction as the one ledger (spec's own explicit
instruction: "do not invent a parallel ledger") via a shadow Item per
Service, and reuses the existing `type='Draw'` precedent (KitchenBatch's
"internal stock movement, not a sale") for recipe-line supply consumption
— exactly the same reason it already exists, avoiding a real bug that
would otherwise inflate revenue: a recipe-consumption Transaction with no
`sale_amount` set would fall through `revenue()`'s `abs(qty) * item.
selling_price` branch if the supply item itself has a selling_price set
(common — many salon supplies are also sold retail), silently counting
stock consumption as a second sale. `type='Draw'` already returns 0 from
`revenue()`'s very first type check, closing that off by construction.
"""
from decimal import Decimal

from .models import Item, Service, Transaction


def get_or_create_shadow_item(service):
    """One shadow Item per Service — created lazily, cached on
    Service.shadow_item. Never sold via the normal item-balance/reorder
    path; its own balance is meaningless and never checked."""
    if service.shadow_item_id:
        return service.shadow_item
    shadow = Item.objects.create(
        business=service.business, store=service.store or service.business.stores.first(),
        description=f'[Huduma] {service.name}', material_no=f'SVC-{service.id}',
        unit='huduma', selling_price=service.price, stock_model='SERVICE',
    )
    service.shadow_item = shadow
    service.save(update_fields=['shadow_item'])
    return shadow


def complete_service_locked(service, stylist, business, store=None, is_redo=False,
                             payment_method='cash', recipient='', sale_amount=None,
                             recorded_by=None, custom_supply_qtys=None):
    """S1-AC1: completing a service deducts recipe quantities from supply
    stock and (unless is_redo) issues the revenue-bearing shadow-item Issue
    transaction with a receipt-line-shaped record.

    S1-AC3: a free redo (`is_redo=True`) records supply consumption (the
    product really was used again) at ZERO revenue and is excluded from the
    stylist's variance denominator — achieved simply by never creating the
    shadow-item Issue transaction for a redo; `expected_consumption()`
    counts services performed via exactly that Issue transaction, so a redo
    is invisible to it by construction, no extra exclusion logic needed.

    `custom_supply_qtys` (optional {item_id: qty}) lets the stylist adjust
    supplies used if unusual (§9.3's "Complete service sheet"), with the
    adjustment itself visible in the resulting Draw transaction's qty —
    never silently substituted.
    """
    from django.db import transaction as _tx

    with _tx.atomic():
        service_txn = None
        if not is_redo:
            shadow = get_or_create_shadow_item(service)
            service_txn = Transaction.objects.create(
                business=business, item=shadow, type='Issue',
                qty=Decimal('-1'), sale_amount=sale_amount if sale_amount is not None else service.price,
                payment_method=payment_method, recipient=recipient,
                service=service, performed_by=stylist, recorded_by=getattr(recorded_by, 'user', None),
            )

        supply_txns = []
        for line in service.supplies.select_related('item').all():
            qty = Decimal(str((custom_supply_qtys or {}).get(line.item_id, line.qty_expected)))
            if qty <= 0:
                continue
            supply_txns.append(Transaction.objects.create(
                business=business, item=line.item, type='Draw', qty=-qty,
                service=service, performed_by=stylist,
                invoice_no='[REDO]' if is_redo else '',
                recorded_by=getattr(recorded_by, 'user', None),
            ))
        return service_txn, supply_txns


def expected_consumption(business, item, stylist, date_from, date_to):
    """Σ (services performed by this stylist referencing a recipe line for
    `item`, in the window) × qty_expected. Counts real completed services
    only — a redo never creates the shadow-item Issue transaction this
    counts, per complete_service_locked()'s own docstring."""
    from .models import ServiceSupplyLine

    lines = ServiceSupplyLine.objects.filter(item=item).select_related('service')
    total = Decimal('0')
    for line in lines:
        count = Transaction.objects.filter(
            business=business, service=line.service, performed_by=stylist,
            type='Issue', date__gte=date_from, date__lte=date_to,
        ).count()
        total += line.qty_expected * count
    return total


def actual_shrinkage(business, item, date_from, date_to):
    """Physical shrinkage for `item` from StockCountLine records closed in
    the window — reuses R3's cycle-count mechanism exactly as-is rather
    than inventing a parallel opening/closing-count tracker; a supply item
    is counted via the same Rekebisha/cycle-count tools any other item
    uses. Returns (shortage_qty, coverage_pct) — shortage_qty is the SUM of
    negative variance_qty (shortage only; a surplus doesn't indicate a side
    client and is not counted here)."""
    from .models import StockCountLine

    lines = StockCountLine.objects.filter(
        item=item, session__business=business, counted_at__isnull=False,
        counted_at__date__gte=date_from, counted_at__date__lte=date_to,
    )
    if not lines.exists():
        return Decimal('0'), Decimal('0')
    shortage = sum((abs(l.variance_qty) for l in lines if l.variance_qty and l.variance_qty < 0), Decimal('0'))
    return shortage, Decimal('100')
