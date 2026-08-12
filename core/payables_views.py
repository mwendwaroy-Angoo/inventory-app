"""
UBA §12.1 (Sprint X1) — payables views. A dedicated payables_dashboard.html
mirroring debt_dashboard.html's UI is NOT built this pass (documented
deferral, same discipline as retail_board.html) — these endpoints return
JSON, fully testable via direct HTTP today, and the aging/cash-position
data they expose is what such a template would render.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import UserProfile

from .models import SupplierInvoice
from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async, create_in_app_notification
from .payables import invoices_due_for_reminder, payables_aging_summary
from .views import owner_or_manager_required


@login_required
@owner_or_manager_required
def payables_dashboard(request):
    business = request.user.userprofile.business
    invoices = list(
        SupplierInvoice.objects.filter(business=business).select_related('store', 'supplier')
    )
    summary = payables_aging_summary(business)
    return JsonResponse({
        'ok': True,
        'aged': summary['aged'], 'total_outstanding': summary['total_outstanding'],
        'invoices': [
            {
                'id': inv.id, 'supplier': inv.supplier_name or (inv.supplier.name if inv.supplier else ''),
                'invoice_no': inv.invoice_no, 'amount': float(inv.amount),
                'paid_amount': float(inv.paid_amount), 'outstanding': float(inv.outstanding),
                'status': inv.status, 'due_date': inv.due_date.isoformat() if inv.due_date else None,
            }
            for inv in invoices
        ],
    })


@login_required
@owner_or_manager_required
@require_POST
def record_supplier_invoice(request):
    up = request.user.userprofile
    business = up.business
    supplier_name = (request.POST.get('supplier_name') or '').strip()
    amount_raw = request.POST.get('amount', '').strip()
    invoice_no = (request.POST.get('invoice_no') or '').strip()
    due_date = (request.POST.get('due_date') or '').strip() or None
    note = (request.POST.get('note') or '').strip()

    if not supplier_name:
        return JsonResponse({'ok': False, 'error': 'Jina la muuzaji linahitajika.'}, status=400)
    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    inv = SupplierInvoice.objects.create(
        business=business, supplier_name=supplier_name, invoice_no=invoice_no,
        amount=amount, due_date=due_date or None, note=note, recorded_by=up,
    )
    return JsonResponse({'ok': True, 'invoice_id': inv.id})


@login_required
@owner_or_manager_required
@require_POST
def record_supplier_payment(request, invoice_id):
    up = request.user.userprofile
    inv = SupplierInvoice.objects.filter(id=invoice_id, business=up.business).first()
    if not inv:
        return JsonResponse({'ok': False, 'error': 'Ankara haikupatikana.'}, status=404)

    try:
        amount = Decimal(request.POST.get('amount', '').strip())
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    method = request.POST.get('method', 'cash')
    reference = (request.POST.get('reference') or '').strip()
    inv.record_payment_locked(amount, method, up, reference=reference)
    return JsonResponse({'ok': True, 'status': inv.status, 'outstanding': float(inv.outstanding)})


def send_payables_due_reminders(business, days_ahead=3):
    """Owner-facing reminder (never the supplier) for invoices due soon or
    overdue. Callable from a management command (deferred-cron pattern,
    same as R1/R3/M3) — not auto-scheduled this pass."""
    invoices = invoices_due_for_reminder(business, days_ahead=days_ahead)
    if not invoices.exists():
        return 0
    lines = [f"{inv.supplier_name}: KES {inv.outstanding:,.0f}" for inv in invoices]
    msg = "⚠️ Malipo ya wauzaji yanayokaribia/kuchelewa:\n" + "\n".join(lines)
    for op in UserProfile.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
        create_in_app_notification(op.user, '💰 Malipo ya Wauzaji', msg, notification_type='warning')
        if op.phone:
            try:
                send_sms_notification_async(msg, normalize_ke_phone(op.phone))
            except Exception:
                pass
    return invoices.count()
