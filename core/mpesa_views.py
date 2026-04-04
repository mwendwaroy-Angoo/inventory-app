"""
M-Pesa Daraja callback views + STK Push trigger.

Endpoints:
    POST /mpesa/callback/       — STK Push result callback (from Safaricom)
    POST /mpesa/stk-push/       — Initiate STK Push (from our frontend)
    GET  /mpesa/status/<id>/    — Query payment status
"""

import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required

from .models import Payment, Order, Transaction, Item
from .mpesa import initiate_stk_push, format_phone_ke, query_stk_status
from .notifications import notify_transaction

logger = logging.getLogger(__name__)


# ── STK PUSH CALLBACK (from Safaricom) ──────────────────────────────────────

@csrf_exempt
@require_POST
def mpesa_callback(request):
    """Receive STK Push result from Safaricom.

    This is called by Safaricom's servers — no auth, CSRF exempt.
    We validate by matching the CheckoutRequestID to a pending Payment.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid JSON'})

    stk_callback = data.get('Body', {}).get('stkCallback', {})
    merchant_request_id = stk_callback.get('MerchantRequestID', '')
    checkout_request_id = stk_callback.get('CheckoutRequestID', '')
    result_code = stk_callback.get('ResultCode')
    result_desc = stk_callback.get('ResultDesc', '')

    logger.info(
        "M-Pesa callback: CheckoutID=%s ResultCode=%s Desc=%s",
        checkout_request_id, result_code, result_desc,
    )

    # Find the matching payment
    try:
        payment = Payment.objects.get(
            checkout_request_id=checkout_request_id,
            status='pending',
        )
    except Payment.DoesNotExist:
        logger.warning("No pending payment for CheckoutID: %s", checkout_request_id)
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    payment.merchant_request_id = merchant_request_id
    payment.result_code = result_code
    payment.result_desc = result_desc

    if result_code == 0:
        # Payment successful — extract receipt number
        metadata = stk_callback.get('CallbackMetadata', {}).get('Item', [])
        receipt = ''
        for item in metadata:
            if item.get('Name') == 'MpesaReceiptNumber':
                receipt = item.get('Value', '')

        payment.status = 'completed'
        payment.mpesa_receipt = receipt
        payment.completed_at = timezone.now()
        payment.save()

        # Update order status if linked
        if payment.order:
            order = payment.order
            total_paid = sum(
                p.amount for p in order.payments.filter(status='completed')
            )
            if total_paid >= order.total_amount:
                order.status = 'paid'
                order.save(update_fields=['status'])
                _fulfill_order(order)

        logger.info("Payment completed: %s receipt=%s", payment.id, receipt)
    else:
        payment.status = 'failed'
        payment.save()
        logger.info("Payment failed: %s code=%s", payment.id, result_code)

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _fulfill_order(order):
    """Create Issue transactions for a paid order (auto-deduct stock)."""
    today = timezone.localtime(timezone.now()).date()
    for line in order.lines.select_related('item'):
        txn = Transaction.objects.create(
            item=line.item,
            date=today,
            type='Issue',
            qty=-line.quantity,
            invoice_no=order.order_number,
            recipient=order.customer_name,
            business=order.business,
        )
        daily_count = Transaction.objects.filter(
            business=order.business, date=today
        ).count()
        try:
            notify_transaction(txn, order.business, daily_count)
        except Exception:
            pass


# ── INITIATE STK PUSH (from our app) ────────────────────────────────────────

@csrf_exempt
@require_POST
def stk_push_view(request):
    """Trigger an STK Push for a customer payment.

    Accepts JSON:
        {
            "phone": "0712345678",
            "amount": 500,
            "order_id": 123       // optional — link to order
        }

    Or can be called by authenticated business users from the dashboard.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    phone = data.get('phone', '')
    amount = data.get('amount', 0)
    order_id = data.get('order_id')

    if not phone or not amount:
        return JsonResponse({'error': 'Phone and amount required'}, status=400)

    try:
        amount = int(amount)
        if amount < 1:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Amount must be a positive integer'}, status=400)

    phone_formatted = format_phone_ke(phone)

    # Determine business from order or authenticated user
    business = None
    order = None

    if order_id:
        try:
            order = Order.objects.get(id=order_id)
            business = order.business
        except Order.DoesNotExist:
            return JsonResponse({'error': 'Order not found'}, status=404)

    if not business and request.user.is_authenticated:
        profile = getattr(request.user, 'userprofile', None)
        if profile and profile.business:
            business = profile.business

    if not business:
        return JsonResponse({'error': 'Cannot determine business'}, status=400)

    # Build callback URL
    callback_url = request.build_absolute_uri('/mpesa/callback/')

    account_ref = order.order_number if order else f"DUKA-{business.id}"
    description = "Duka Mwecheche"

    # Create pending payment record
    payment = Payment.objects.create(
        order=order,
        business=business,
        amount=amount,
        method='mpesa',
        status='pending',
        phone=phone_formatted,
    )

    # Call Safaricom STK Push
    result = initiate_stk_push(
        phone_number=phone_formatted,
        amount=amount,
        account_reference=account_ref,
        description=description,
        callback_url=callback_url,
    )

    if result and result.get('ResponseCode') == '0':
        payment.checkout_request_id = result.get('CheckoutRequestID', '')
        payment.merchant_request_id = result.get('MerchantRequestID', '')
        payment.save(update_fields=['checkout_request_id', 'merchant_request_id'])

        return JsonResponse({
            'success': True,
            'message': 'STK Push sent. Check your phone.',
            'payment_id': payment.id,
            'checkout_request_id': payment.checkout_request_id,
        })
    else:
        payment.status = 'failed'
        payment.result_desc = str(result) if result else 'No response from M-Pesa'
        payment.save()
        return JsonResponse({
            'success': False,
            'error': 'Failed to initiate M-Pesa payment. Try again.',
        }, status=502)


# ── PAYMENT STATUS CHECK ────────────────────────────────────────────────────

@require_GET
def payment_status(request, payment_id):
    """Check the status of a payment (polling from frontend)."""
    try:
        payment = Payment.objects.get(id=payment_id)
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Payment not found'}, status=404)

    # If still pending, optionally query Safaricom
    if payment.status == 'pending' and payment.checkout_request_id:
        stk_result = query_stk_status(payment.checkout_request_id)
        if stk_result and stk_result.get('ResultCode') is not None:
            result_code = int(stk_result['ResultCode'])
            if result_code == 0:
                payment.status = 'completed'
                payment.result_code = result_code
                payment.completed_at = timezone.now()
                payment.save()
            elif result_code != 1032:  # 1032 = "Request cancelled by user" — might retry
                payment.status = 'failed'
                payment.result_code = result_code
                payment.result_desc = stk_result.get('ResultDesc', '')
                payment.save()

    return JsonResponse({
        'payment_id': payment.id,
        'status': payment.status,
        'mpesa_receipt': payment.mpesa_receipt,
        'amount': float(payment.amount),
    })
