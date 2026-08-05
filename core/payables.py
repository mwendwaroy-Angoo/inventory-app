"""
UBA §12.1 (Sprint X1) — payables: the missing half of the cash picture.
Deliberately mirrors core.debt_views's aging-bucket helper (same boundaries:
current within the window, then 30/60/90) rather than inventing a new
scheme — same UI patterns, opposite direction.
"""
from .models import SupplierInvoice


def payables_aging_summary(business, window_days=30):
    """current/overdue_30/overdue_60/overdue_90 buckets, bucketed by days
    past due_date (or invoice_date if no due_date was set) — same shape as
    debt_views._get_customer_debt_data()'s `aged` dict."""
    from django.utils import timezone
    today = timezone.localdate()

    invoices = SupplierInvoice.objects.filter(business=business).exclude(status=SupplierInvoice.STATUS_PAID)
    aged = {'current': 0.0, 'overdue_30': 0.0, 'overdue_60': 0.0, 'overdue_90': 0.0}
    total_outstanding = 0.0
    for inv in invoices:
        outstanding = float(inv.outstanding)
        if outstanding <= 0:
            continue
        total_outstanding += outstanding
        anchor = inv.due_date or inv.invoice_date
        days_overdue = (today - anchor).days
        if days_overdue <= window_days:
            aged['current'] += outstanding
        elif days_overdue <= 30:
            aged['overdue_30'] += outstanding
        elif days_overdue <= 60:
            aged['overdue_60'] += outstanding
        else:
            aged['overdue_90'] += outstanding
    aged = {k: round(v, 2) for k, v in aged.items()}
    return {'aged': aged, 'total_outstanding': round(total_outstanding, 2)}


def total_receivables(business):
    """Business-wide outstanding customer credit — reuses the same
    per-customer aggregation debt_dashboard() already does, just summed
    without a per-row breakdown (this is a single dashboard number, not a
    listing)."""
    from .debt_views import _get_customer_debt_data
    from .models import Customer

    total = 0.0
    for customer in Customer.objects.filter(business=business):
        data = _get_customer_debt_data(customer, business)
        total += data['outstanding']
    return round(total, 2)


def cash_position(business):
    """"Hali Halisi ya Pesa" — Receivables minus Payables. The most honest
    number in the app: what customers owe minus what the business itself
    owes suppliers."""
    receivables = total_receivables(business)
    payables = payables_aging_summary(business)['total_outstanding']
    return {
        'receivables': receivables, 'payables': payables,
        'net': round(receivables - payables, 2),
    }


def invoices_due_for_reminder(business, days_ahead=3):
    """Invoices due within `days_ahead` days, or already overdue — for the
    owner payment-due reminder (never the supplier; missing a distributor
    payment costs the shop its credit line, an owner-facing risk)."""
    from datetime import timedelta

    from django.utils import timezone
    today = timezone.localdate()
    cutoff = today + timedelta(days=days_ahead)
    return SupplierInvoice.objects.filter(
        business=business, due_date__lte=cutoff,
    ).exclude(status=SupplierInvoice.STATUS_PAID)
