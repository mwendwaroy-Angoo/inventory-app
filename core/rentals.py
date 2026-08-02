"""
UBA §10.3 (Sprint L1) — rent roll generation + "arrears ARE debt" wiring.
Deliberately does NOT build a second aging engine (the spec's own explicit
instruction) — a RentalInvoice's rent charge is an ordinary
`payment_method='credit'` Transaction, exactly the shape
core.debt_views._get_customer_debt_data() already consumes, so FIFO, aged
buckets, credit score, reminders and the K3 credit policy gate are all
inherited for free. `core.salon`'s shadow-Item pattern (S1) is reused
here too — one shared shadow Item per business stands in for "rent" the
same way one shadow Item per Service stood in for a haircut.
"""
from decimal import Decimal

from .models import CustomerDebtPayment, Item, RentalAgreement, RentalInvoice, Transaction


def get_or_create_rent_shadow_item(business):
    """ONE shared shadow Item per business — unlike a Service (which needs
    its own shadow item for its own price/commission), every rent invoice
    across every unit in the portfolio can safely share one, since nothing
    in the debt tracker cares which unit a credit transaction came from,
    only the customer/amount/date."""
    existing = Item.objects.filter(business=business, material_no='RENT-SHADOW').first()
    if existing:
        return existing
    store = business.stores.first()
    return Item.objects.create(
        business=business, store=store, description='[Kodi] Malipo ya Kodi',
        material_no='RENT-SHADOW', unit='mwezi', stock_model='SERVICE',
    )


def generate_rent_roll(business, period_start, period_end, due_date):
    """L1-AC1: idempotent — running this twice for the same period creates
    no duplicate invoices, the same guarantee RecurringExpense's period
    review already provides (mirrors that exact pattern: a plain existence
    check per agreement, backed by the model's own
    unique_together=('agreement','period_start') as a second, DB-level
    guarantee against a race)."""
    shadow = get_or_create_rent_shadow_item(business)
    created = []
    agreements = RentalAgreement.objects.filter(
        business=business, status=RentalAgreement.STATUS_ACTIVE,
    ).select_related('customer', 'unit')

    for agreement in agreements:
        if RentalInvoice.objects.filter(agreement=agreement, period_start=period_start).exists():
            continue
        total = agreement.rate
        rent_txn = Transaction.objects.create(
            business=business, item=shadow, type='Issue', qty=Decimal('-1'),
            sale_amount=total, payment_method='credit', recipient=agreement.customer.name,
            date=period_start,
        )
        invoice = RentalInvoice.objects.create(
            agreement=agreement, period_start=period_start, period_end=period_end,
            rent_amount=agreement.rate, total=total, due_date=due_date, rent_txn=rent_txn,
        )
        created.append(invoice)
    return created


def sync_invoice_payment_status(invoice):
    """Reads the debt tracker's own FIFO computation for this specific
    rent_txn — no separate aging math. `paid_amount`/`status` are cached
    on the invoice purely for fast, simple reads elsewhere (the rental
    board, receipts); this function is the ONE place that recomputes them,
    always from the debt tracker's live truth."""
    from .debt_views import _get_customer_debt_data

    if not invoice.rent_txn_id:
        return invoice
    data = _get_customer_debt_data(invoice.agreement.customer, invoice.agreement.business)
    unpaid_entry = next((u for u in data['unpaid_transactions'] if u['txn'].id == invoice.rent_txn_id), None)
    if unpaid_entry is None:
        invoice.paid_amount = invoice.total
        invoice.status = RentalInvoice.STATUS_PAID
    else:
        unpaid_amount = Decimal(str(unpaid_entry['amount']))
        invoice.paid_amount = invoice.total - unpaid_amount
        invoice.status = RentalInvoice.STATUS_PARTIAL if invoice.paid_amount > 0 else RentalInvoice.STATUS_DUE
    invoice.save(update_fields=['paid_amount', 'status'])
    return invoice


def apply_rent_payment_by_unit_code(business, unit_code, amount, phone='', mpesa_ref=''):
    """L1-AC2 — the marquee M-Pesa auto-reconciliation feature. Finds the
    RentalUnit by its `code` (the Paybill account number), applies the
    payment to its tenant via the ordinary CustomerDebtPayment mechanism
    (the exact same FIFO the whole debt tracker already relies on — "apply
    to the oldest open invoice" falls out of that FIFO for free, no special
    per-invoice application logic needed) and syncs every open invoice for
    that agreement. Returns the RentalAgreement matched, or None if the
    unit code doesn't exist in this business's portfolio at all — the
    caller (mpesa_views.c2b_confirmation) decides whether that's worth a
    BusinessException, since not every business runs Rentals at all."""
    from .models import RentalUnit

    unit = RentalUnit.objects.filter(business=business, code=unit_code).first()
    if not unit:
        return None
    agreement = unit.agreements.filter(status=RentalAgreement.STATUS_ACTIVE).order_by('-start_date').first()
    if not agreement:
        return None

    CustomerDebtPayment.objects.create(
        customer=agreement.customer, business=business, amount_paid=Decimal(str(amount)),
        payment_method='mpesa', notes=f'M-Pesa {mpesa_ref} — {unit_code}',
    )
    for invoice in agreement.invoices.exclude(status=RentalInvoice.STATUS_PAID):
        sync_invoice_payment_status(invoice)
    return agreement
