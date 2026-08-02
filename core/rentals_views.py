"""
UBA §10 (Sprints L1-L2) — rentals JSON endpoints. A dedicated
rental_board.html (L3) is NOT built this pass — documented deferral, same
discipline as retail_board.html/salon_board.html.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Customer, MaintenanceTicket, MeterReading, RentalAgreement, RentalUnit, Store
from .rentals import generate_rent_roll
from .views import owner_or_manager_required


def _is_caretaker(up):
    return up.role == 'caretaker'


@login_required
@owner_or_manager_required
@require_POST
def create_rental_unit(request):
    up = request.user.userprofile
    business = up.business
    store = Store.objects.filter(id=request.POST.get('store_id'), business=business).first()
    code = (request.POST.get('code') or '').strip()
    if not code:
        return JsonResponse({'ok': False, 'error': 'Msimbo wa kitengo unahitajika.'}, status=400)
    try:
        default_rate = Decimal(request.POST.get('default_rate', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiwango si sahihi.'}, status=400)

    unit = RentalUnit.objects.create(
        business=business, store=store, kind=request.POST.get('kind', 'property'),
        code=code, name=request.POST.get('name', ''), default_rate=default_rate,
        rate_period=request.POST.get('rate_period', 'month'),
        deposit_amount=Decimal(request.POST.get('deposit_amount', '0') or '0'),
        quantity=int(request.POST.get('quantity', '1') or '1'),
    )
    return JsonResponse({'ok': True, 'unit_id': unit.id})


@login_required
@owner_or_manager_required
@require_POST
def create_rental_agreement(request):
    up = request.user.userprofile
    business = up.business
    unit = RentalUnit.objects.filter(id=request.POST.get('unit_id'), business=business).first()
    if not unit:
        return JsonResponse({'ok': False, 'error': 'Kitengo hakikupatikana.'}, status=404)

    quantity = int(request.POST.get('quantity', '1') or '1')
    if unit.available_qty() < quantity:
        return JsonResponse({'ok': False, 'error': 'Kiasi hiki hakipatikani — tayari kimewekwa.'}, status=400)

    customer_name = (request.POST.get('customer_name') or '').strip()
    if not customer_name:
        return JsonResponse({'ok': False, 'error': 'Jina la mpangaji linahitajika.'}, status=400)
    customer = Customer.objects.filter(business=business, name=customer_name).first()
    if not customer:
        customer = Customer.objects.create(business=business, name=customer_name, phone=request.POST.get('customer_phone', ''))

    agreement = RentalAgreement.objects.create(
        business=business, unit=unit, customer=customer,
        start_date=request.POST.get('start_date') or timezone.localdate(),
        rate=request.POST.get('rate') or unit.default_rate, rate_period=unit.rate_period,
        quantity=quantity, deposit_held=Decimal(request.POST.get('deposit_held', '0') or '0'),
        billing_day=int(request.POST.get('billing_day', '5') or '5'),
        status=RentalAgreement.STATUS_ACTIVE, created_by=up,
    )
    return JsonResponse({'ok': True, 'agreement_id': agreement.id})


@login_required
@owner_or_manager_required
@require_POST
def terminate_rental_agreement(request, agreement_id):
    up = request.user.userprofile
    agreement = RentalAgreement.objects.filter(id=agreement_id, business=up.business).first()
    if not agreement:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    agreement.terminate(ended_by=up, reason=request.POST.get('reason', ''))
    return JsonResponse({'ok': True, 'status': agreement.status})


@login_required
@owner_or_manager_required
@require_POST
def run_rent_roll(request):
    """L1-AC1 idempotency — running this endpoint twice for the same period
    creates no duplicate invoices (generate_rent_roll's own guarantee)."""
    business = request.user.userprofile.business
    period_start = request.POST.get('period_start')
    period_end = request.POST.get('period_end')
    due_date = request.POST.get('due_date')
    if not (period_start and period_end and due_date):
        return JsonResponse({'ok': False, 'error': 'Tarehe zinahitajika.'}, status=400)
    created = generate_rent_roll(business, period_start, period_end, due_date)
    return JsonResponse({'ok': True, 'created_count': len(created), 'invoice_ids': [i.id for i in created]})


@login_required
@require_POST
def record_meter_reading(request, unit_id):
    """Caretaker-allowed action (§10.4's explicit permission list)."""
    up = request.user.userprofile
    unit = RentalUnit.objects.filter(id=unit_id, business=up.business).first()
    if not unit:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    try:
        reading = Decimal(request.POST.get('reading', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kipimo si sahihi.'}, status=400)
    mr = MeterReading.objects.create(
        unit=unit, kind=request.POST.get('kind', 'water'), reading=reading, read_by=up,
    )
    return JsonResponse({'ok': True, 'reading_id': mr.id})


@login_required
@require_POST
def report_maintenance(request, unit_id):
    """Caretaker-allowed action."""
    up = request.user.userprofile
    unit = RentalUnit.objects.filter(id=unit_id, business=up.business).first()
    if not unit:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    description = (request.POST.get('description') or '').strip()
    if not description:
        return JsonResponse({'ok': False, 'error': 'Maelezo yanahitajika.'}, status=400)
    ticket = MaintenanceTicket.objects.create(unit=unit, reported_by=up, description=description)
    return JsonResponse({'ok': True, 'ticket_id': ticket.id})


@login_required
@owner_or_manager_required
@require_POST
def close_maintenance_ticket(request, ticket_id):
    """L2's own access rule: a caretaker CANNOT alter agreements or see the
    full P&L, but closing a ticket with a cost is itself a P&L-affecting
    financial action — owner/manager only, same tier as every other
    financial-figure-correction action in this app."""
    up = request.user.userprofile
    ticket = MaintenanceTicket.objects.filter(id=ticket_id, unit__business=up.business).first()
    if not ticket:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    cost = request.POST.get('cost')
    ticket.close(up, cost=Decimal(cost) if cost else None)
    return JsonResponse({'ok': True, 'status': ticket.status})


@login_required
@owner_or_manager_required
@require_POST
def deduct_from_deposit(request, agreement_id):
    """L2-AC1: deductions are itemised — never silently absorbed into
    revenue. Reduces deposit_held and records the reason; the deposit
    itself was NEVER a Transaction (P0-B's own liability pattern — deposits
    are tracked on the agreement/PaymentPlan, not as a sale), so a
    deduction correspondingly never creates one either — it is a
    liability-side adjustment, not a new sale."""
    up = request.user.userprofile
    agreement = RentalAgreement.objects.filter(id=agreement_id, business=up.business).first()
    if not agreement:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    try:
        amount = Decimal(request.POST.get('amount', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)
    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        return JsonResponse({'ok': False, 'error': 'Sababu ya kutoa inahitajika.'}, status=400)
    if amount > agreement.deposit_held:
        return JsonResponse({'ok': False, 'error': 'Kiasi ni zaidi ya amana iliyoshikiliwa.'}, status=400)

    agreement.deposit_held = agreement.deposit_held - amount
    note = f"Ditoa KES {amount} — {reason} ({timezone.localdate()})"
    agreement.terms_note = (agreement.terms_note + '\n' + note).strip()
    agreement.save(update_fields=['deposit_held', 'terms_note'])
    return JsonResponse({'ok': True, 'deposit_held': float(agreement.deposit_held), 'note': note})
