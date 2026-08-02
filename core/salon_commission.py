"""
UBA §9.4 (Sprint S3) — commission ledger, computed per stylist per period
from Transaction.performed_by + Service.commission_*. Deliberately reuses
the EXISTING Haki module rather than inventing a parallel payment
mechanism — recording a commission payment is just `haki_views.
record_salary_payment()`, which already creates SalaryPayment, sends the
employee SMS confirmation (H2), and is what `_salary_period_balance()`
(also existing) subtracts to compute what's still owed. Salon is, per the
spec's own framing, the best-fit business type for Haki in the entire
platform — the Staff Contribution Ledger and SalaryPayment's overdue
tracking already exist; this only supplies the "expected" commission
figure that ledger was missing for commission-paid staff.
"""
import calendar
from datetime import date
from decimal import Decimal

from .models import Transaction


def commission_report(business, stylist, period):
    """period = 'YYYY-MM', matching SalaryPayment.period's own format."""
    from .haki_views import _salary_period_balance

    year, month = int(period[:4]), int(period[5:7])
    date_from = date(year, month, 1)
    date_to = date(year, month, calendar.monthrange(year, month)[1])

    txns = list(
        Transaction.objects.filter(
            business=business, performed_by=stylist, type='Issue', service__isnull=False,
            date__gte=date_from, date__lte=date_to,
        ).select_related('service')
    )
    revenue_total = sum(float(t.revenue()) for t in txns)
    commission_total = sum(float(t.service.commission_amount(t.revenue())) for t in txns)

    _expected, paid, _remaining = _salary_period_balance(business, stylist, period)
    owed = max(Decimal('0'), Decimal(str(commission_total)) - paid)

    return {
        'services_count': len(txns),
        'revenue_total': round(revenue_total, 2),
        'commission_total': round(commission_total, 2),
        'paid': float(paid),
        'owed': float(owed),
    }
