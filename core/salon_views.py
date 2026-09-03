"""
UBA §9.2-§9.4 (Salon Sprints S1-S3) — services, bookings, and commission
JSON endpoints. A dedicated salon_board.html daily-operating screen is NOT
built this pass (documented deferral, same discipline as retail_board.html)
— these endpoints are what such a screen would call, fully testable via
direct HTTP today.
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import UserProfile

from .accountability import variance_for
from .models import Appointment, Customer, Service, Store
from .salon import complete_service_locked
from .salon_commission import commission_report
from .shift_views import get_active_staff_shift
from .views import owner_or_manager_required


@login_required
@require_POST
def complete_service_view(request):
    up = request.user.userprofile
    business = up.business
    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)

    service = Service.objects.filter(id=request.POST.get('service_id'), business=business).first()
    if not service:
        return JsonResponse({'ok': False, 'error': 'Huduma haikupatikana.'}, status=404)

    stylist_id = request.POST.get('stylist_id')
    stylist = UserProfile.objects.filter(id=stylist_id, business=business).first() if stylist_id else up
    is_redo = request.POST.get('is_redo') in ('1', 'true', 'on')

    service_txn, supply_txns = complete_service_locked(
        service, stylist, business, is_redo=is_redo,
        payment_method=request.POST.get('payment_method', 'cash'),
        recipient=request.POST.get('recipient', ''), recorded_by=up,
    )
    return JsonResponse({
        'ok': True,
        'service_txn_id': service_txn.id if service_txn else None,
        'supply_txn_ids': [t.id for t in supply_txns],
        'is_redo': is_redo,
    })


@login_required
@owner_or_manager_required
def recipe_variance_report_view(request):
    business = request.user.userprofile.business
    item_id = request.GET.get('item_id')
    stylist_id = request.GET.get('stylist_id')
    if not item_id or not stylist_id:
        return JsonResponse({'ok': False, 'error': 'item_id na stylist_id vinahitajika.'}, status=400)

    from .models import Item
    item = Item.objects.filter(id=item_id, business=business).first()
    stylist = UserProfile.objects.filter(id=stylist_id, business=business).first()
    if not item or not stylist:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)

    date_from = request.GET.get('date_from') or (timezone.localdate() - timezone.timedelta(days=30))
    date_to = request.GET.get('date_to') or timezone.localdate()

    result = variance_for('recipe_variance', business=business, store=(item, stylist), shift=None,
                           date_from=date_from, date_to=date_to)
    if result is None:
        return JsonResponse({'ok': True, 'result': None})
    return JsonResponse({
        'ok': True,
        'result': {
            'expected': float(result.expected), 'actual': float(result.actual),
            'variance': float(result.variance), 'variance_kes': float(result.variance_kes),
            'flag': result.flag, 'coverage_pct': float(result.coverage_pct), 'is_partial': result.is_partial,
        },
    })


@login_required
@require_POST
def create_appointment(request):
    up = request.user.userprofile
    business = up.business

    store_id = request.POST.get('store_id')
    store = Store.objects.filter(id=store_id, business=business).first()
    if not store:
        return JsonResponse({'ok': False, 'error': 'Duka halikupatikana.'}, status=400)

    try:
        start_at = timezone.datetime.fromisoformat(request.POST.get('start_at'))
        end_at = timezone.datetime.fromisoformat(request.POST.get('end_at'))
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at)
        if timezone.is_naive(end_at):
            end_at = timezone.make_aware(end_at)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Muda si sahihi.'}, status=400)

    staff_id = request.POST.get('staff_id')
    staff = UserProfile.objects.filter(id=staff_id, business=business).first() if staff_id else None

    if staff and Appointment.objects.filter(
        business=business, staff=staff, start_at__lt=end_at, end_at__gt=start_at,
    ).exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_NO_SHOW]).exists():
        return JsonResponse({'ok': False, 'error': 'Fundi huyu tayari ana appointment wakati huu.'}, status=400)

    customer_name = (request.POST.get('customer_name') or '').strip()
    customer = None
    if customer_name:
        # 2026-09-03 — name__iexact, not a bare =. See core/views.py's
        # add_transaction comment for the full root-cause explanation.
        customer = Customer.objects.filter(business=business, name__iexact=customer_name).first()
        if not customer:
            customer = Customer.objects.create(
                business=business, name=customer_name, phone=request.POST.get('customer_phone', ''),
            )

    appt = Appointment.objects.create(
        business=business, store=store, customer=customer, customer_name=customer_name,
        customer_phone=request.POST.get('customer_phone', ''), staff=staff,
        start_at=start_at, end_at=end_at, source=request.POST.get('source', 'walkin'),
        created_by=up,
    )
    return JsonResponse({'ok': True, 'appointment_id': appt.id})


@login_required
@require_POST
def update_appointment_status(request, appointment_id):
    up = request.user.userprofile
    appt = Appointment.objects.filter(id=appointment_id, business=up.business).first()
    if not appt:
        return JsonResponse({'ok': False, 'error': 'Haikupatikana.'}, status=404)
    new_status = request.POST.get('status')
    if new_status not in dict(Appointment.STATUS_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Hali si sahihi.'}, status=400)
    appt.status = new_status
    appt.save(update_fields=['status'])
    if new_status == Appointment.STATUS_NO_SHOW and appt.customer:
        appt.customer.no_show_count = (appt.customer.no_show_count or 0) + 1
        appt.customer.save(update_fields=['no_show_count'])
    return JsonResponse({'ok': True, 'status': appt.status})


@login_required
@owner_or_manager_required
def commission_report_view(request, stylist_id):
    business = request.user.userprofile.business
    stylist = UserProfile.objects.filter(id=stylist_id, business=business).first()
    if not stylist:
        return JsonResponse({'ok': False, 'error': 'Fundi hakupatikana.'}, status=404)
    period = request.GET.get('period') or timezone.localdate().strftime('%Y-%m')
    return JsonResponse({'ok': True, 'period': period, **commission_report(business, stylist, period)})
