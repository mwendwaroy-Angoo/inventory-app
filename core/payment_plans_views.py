"""
UBA §6.2 (Sprint P0-B) — payment plans (layaway / deposit / instalments /
booking). A dedicated layaway UI page is NOT built this pass (documented
deferral, same discipline as retail_board.html) — these JSON endpoints
are what such a page would call, fully testable via direct HTTP today.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Customer, Item, PaymentPlan, Store
from .shift_views import get_active_staff_shift


def _get_up(request):
    return getattr(request.user, 'userprofile', None)


@login_required
@require_POST
def create_payment_plan(request):
    up = _get_up(request)
    business = up.business
    if not up.is_owner_or_manager:
        gate = get_active_staff_shift(up, business)
        if gate is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)

    customer_name = (request.POST.get('customer_name') or '').strip()
    if not customer_name:
        return JsonResponse({'ok': False, 'error': 'Jina la mteja linahitajika.'}, status=400)
    customer = Customer.objects.filter(business=business, name=customer_name).first()
    if not customer:
        customer = Customer.objects.create(business=business, name=customer_name)

    kind = request.POST.get('kind', PaymentPlan.KIND_LAYAWAY)
    try:
        total_amount = Decimal(request.POST.get('total_amount', '').strip())
        if total_amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    store_id = request.POST.get('store')
    store = Store.objects.filter(id=store_id, business=business).first() if store_id else None

    reserved_item = None
    reserved_qty = None
    item_id = request.POST.get('reserved_item_id')
    if item_id:
        reserved_item = Item.objects.filter(id=item_id, business=business).first()
        if not reserved_item:
            return JsonResponse({'ok': False, 'error': 'Bidhaa haikupatikana.'}, status=400)
        reserved_qty = Decimal(request.POST.get('reserved_qty', '1'))
        if reserved_item.available_balance() < reserved_qty:
            return JsonResponse({'ok': False, 'error': 'Bidhaa hii haipatikani kwa kiasi hicho.'}, status=400)

    initial_payment = None
    if request.POST.get('initial_payment'):
        try:
            initial_payment = Decimal(request.POST.get('initial_payment'))
        except InvalidOperation:
            initial_payment = None

    plan = PaymentPlan.create_locked(
        business=business, customer=customer, kind=kind, total_amount=total_amount,
        created_by=up, store=store,
        due_date=request.POST.get('due_date') or None,
        hold_expires_on=request.POST.get('hold_expires_on') or None,
        reserved_item=reserved_item, reserved_qty=reserved_qty,
        initial_payment=initial_payment, initial_method=request.POST.get('initial_method', 'cash'),
    )
    return JsonResponse({
        'ok': True, 'plan_id': plan.id, 'balance': float(plan.balance),
        'forfeit_policy': plan.forfeit_policy,
    })


@login_required
@require_POST
def pay_payment_plan(request, plan_id):
    up = _get_up(request)
    plan = PaymentPlan.objects.filter(id=plan_id, business=up.business).first()
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Mpango haukupatikana.'}, status=404)
    try:
        amount = Decimal(request.POST.get('amount', '').strip())
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Kiasi si sahihi.'}, status=400)

    try:
        plan.pay_locked(amount, request.POST.get('method', 'cash'), up, reference=request.POST.get('reference', ''))
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    msg = f"Umelipa KES {amount:,.0f}. Salio linalobaki: KES {plan.balance:,.0f}."
    if plan.customer.phone:
        try:
            from .notifications import normalize_ke_phone, send_sms_notification
            send_sms_notification(msg, normalize_ke_phone(plan.customer.phone))
        except Exception:
            pass
    return JsonResponse({'ok': True, 'balance': float(plan.balance), 'message': msg})


@login_required
@require_POST
def convert_payment_plan_to_sale(request, plan_id):
    up = _get_up(request)
    plan = PaymentPlan.objects.filter(id=plan_id, business=up.business).first()
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Mpango haukupatikana.'}, status=404)
    try:
        plan.convert_to_sale_locked(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'status': plan.status})


@login_required
@require_POST
def refund_payment_plan(request, plan_id):
    up = _get_up(request)
    plan = PaymentPlan.objects.filter(id=plan_id, business=up.business).first()
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Mpango haukupatikana.'}, status=404)
    try:
        plan.refund_locked(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'status': plan.status})


@login_required
@require_POST
def release_payment_plan(request, plan_id):
    up = _get_up(request)
    plan = PaymentPlan.objects.filter(id=plan_id, business=up.business).first()
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Mpango haukupatikana.'}, status=404)
    try:
        plan.release(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'status': plan.status})


@login_required
@require_POST
def forfeit_payment_plan(request, plan_id):
    up = _get_up(request)
    if not up.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Huna ruhusa.'}, status=403)
    plan = PaymentPlan.objects.filter(id=plan_id, business=up.business).first()
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Mpango haukupatikana.'}, status=404)
    try:
        plan.forfeit_locked(up)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({'ok': True, 'status': plan.status})


def deposits_held_total(business):
    """"Amana Zilizoshikiliwa" — money the business is HOLDING against open
    plans, not its own revenue. A liability tile for the owner dashboard."""
    from django.db.models import Sum
    total = PaymentPlan.objects.filter(
        business=business, status=PaymentPlan.STATUS_OPEN,
    ).aggregate(total=Sum('paid_amount'))['total']
    return float(total or 0)


def send_hold_expiry_reminders(business, days_ahead=3):
    """Layaway hold expiring within `days_ahead` days — owner reminder."""
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import UserProfile

    from .notifications import create_in_app_notification, normalize_ke_phone, send_sms_notification

    today = timezone.localdate()
    cutoff = today + timedelta(days=days_ahead)
    plans = PaymentPlan.objects.filter(
        business=business, status=PaymentPlan.STATUS_OPEN,
        hold_expires_on__isnull=False, hold_expires_on__lte=cutoff,
    )
    if not plans.exists():
        return 0
    lines = [f"{p.customer.name}: {p.reserved_item.description if p.reserved_item else p.kind} — muda unaisha {p.hold_expires_on}" for p in plans]
    msg = "⏰ Amana zinazokaribia mwisho wa muda:\n" + "\n".join(lines)
    for op in UserProfile.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
        create_in_app_notification(op.user, '⏰ Amana Zinazokaribia Mwisho', msg, notification_type='warning')
        if op.phone:
            try:
                send_sms_notification(msg, normalize_ke_phone(op.phone))
            except Exception:
                pass
    return plans.count()
