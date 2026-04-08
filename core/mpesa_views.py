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

from .models import Payment, Order, Transaction, Item, PendingTransactionPrompt
from .mpesa import initiate_stk_push, format_phone_ke, query_stk_status
from .notifications import notify_transaction, create_in_app_notification

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


# ── C2B CALLBACK (Business Till/Paybill payments) ──────────────────────────

@csrf_exempt
@require_POST
def c2b_validation(request):
    """C2B Validation URL — Safaricom calls this before completing a C2B transaction.
    Returning ResultCode 0 accepts the payment."""
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


@csrf_exempt
@require_POST
def c2b_confirmation(request):
    """C2B Confirmation URL — Safaricom calls this after a customer pays to a
    business Till/Paybill. We match the shortcode to a business and create a
    PendingTransactionPrompt for the staff/owner to confirm what was sold."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid JSON'})

    trans_type = data.get('TransactionType', '')  # e.g. Buy Goods, Pay Bill
    trans_id = data.get('TransID', '')
    trans_amount = data.get('TransAmount', '0')
    bill_ref_number = data.get('BillRefNumber', '')  # Paybill account ref
    shortcode = data.get('BusinessShortCode', '')
    msisdn = data.get('MSISDN', '')  # payer phone

    logger.info(
        "C2B confirmation: TransID=%s Amount=%s ShortCode=%s Phone=%s Ref=%s",
        trans_id, trans_amount, shortcode, msisdn, bill_ref_number,
    )

    # Match shortcode to a business by till, paybill, or pochi
    from accounts.models import Business
    business = None
    channel = ''

    if shortcode:
        business = Business.objects.filter(mpesa_till=shortcode).first()
        if business:
            channel = 'till'
        if not business:
            business = Business.objects.filter(mpesa_paybill=shortcode).first()
            if business:
                channel = 'paybill'
        if not business:
            # Pochi la Biashara sometimes uses the phone as shortcode
            business = Business.objects.filter(mpesa_pochi=shortcode).first()
            if business:
                channel = 'pochi'

    if not business:
        logger.warning("C2B: No business found for shortcode %s", shortcode)
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    # Avoid duplicate prompts for the same receipt
    if trans_id and PendingTransactionPrompt.objects.filter(mpesa_receipt=trans_id).exists():
        logger.info("C2B: Duplicate receipt %s — skipping", trans_id)
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    try:
        amount = float(trans_amount)
    except (ValueError, TypeError):
        amount = 0

    # Create the pending prompt
    prompt = PendingTransactionPrompt.objects.create(
        business=business,
        amount=amount,
        phone=msisdn,
        mpesa_receipt=trans_id,
        payment_channel=channel,
    )

    # Notify all staff + owner for this business
    from accounts.models import UserProfile
    biz_users = UserProfile.objects.filter(
        business=business, role__in=['owner', 'staff']
    ).select_related('user')

    for profile in biz_users:
        create_in_app_notification(
            user=profile.user,
            title='💰 Payment Received!',
            message=(
                f"KES {amount:,.0f} received from {msisdn} via {channel.upper()}. "
                f"Receipt: {trans_id}. Please confirm what was sold."
            ),
            notification_type='transaction',
        )

    logger.info("C2B prompt created: id=%s business=%s amount=%s", prompt.id, business.name, amount)
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


# ── PENDING TRANSACTION PROMPTS (staff/owner confirms what was sold) ─────────

@login_required
def pending_prompts(request):
    """List all pending transaction prompts for the current business."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    prompts = PendingTransactionPrompt.objects.filter(
        business=profile.business,
        status='pending',
    )
    confirmed = PendingTransactionPrompt.objects.filter(
        business=profile.business,
        status='confirmed',
    )[:20]

    items = Item.objects.filter(
        business=profile.business
    ).select_related('store').order_by('description')

    return render(request, 'core/pending_prompts.html', {
        'prompts': prompts,
        'confirmed': confirmed,
        'items': items,
    })


@login_required
@require_POST
def confirm_prompt(request, prompt_id):
    """Staff/owner confirms a payment prompt by selecting the item sold and quantity."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return JsonResponse({'error': 'No business'}, status=403)

    try:
        prompt = PendingTransactionPrompt.objects.get(
            id=prompt_id,
            business=profile.business,
            status='pending',
        )
    except PendingTransactionPrompt.DoesNotExist:
        return JsonResponse({'error': 'Prompt not found or already confirmed'}, status=404)

    item_id = request.POST.get('item_id')
    qty = request.POST.get('qty', '1')

    if not item_id:
        return JsonResponse({'error': 'Please select an item'}, status=400)

    try:
        item = Item.objects.get(id=item_id, business=profile.business)
    except Item.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)

    try:
        qty = int(qty)
        if qty < 1:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Quantity must be a positive integer'}, status=400)

    today = timezone.localtime(timezone.now()).date()
    txn = Transaction.objects.create(
        item=item,
        date=today,
        type='Issue',
        qty=-qty,
        invoice_no=prompt.mpesa_receipt or f"MPESA-{prompt.id}",
        recipient=prompt.phone,
        business=profile.business,
    )

    prompt.status = 'confirmed'
    prompt.transaction = txn
    prompt.confirmed_by = request.user
    prompt.confirmed_at = timezone.now()
    prompt.save()

    # Notify with daily count
    daily_count = Transaction.objects.filter(
        business=profile.business, date=today
    ).count()
    try:
        notify_transaction(txn, profile.business, daily_count, request.user)
    except Exception:
        pass

    return JsonResponse({
        'success': True,
        'message': f"Logged: {qty}x {item.description} — KES {float(prompt.amount):,.0f}",
    })


@login_required
@require_POST
def dismiss_prompt(request, prompt_id):
    """Dismiss a pending prompt (not a sale — e.g. refund, personal transfer)."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return JsonResponse({'error': 'No business'}, status=403)

    try:
        prompt = PendingTransactionPrompt.objects.get(
            id=prompt_id,
            business=profile.business,
            status='pending',
        )
    except PendingTransactionPrompt.DoesNotExist:
        return JsonResponse({'error': 'Prompt not found'}, status=404)

    prompt.status = 'dismissed'
    prompt.confirmed_by = request.user
    prompt.confirmed_at = timezone.now()
    prompt.save()

    return JsonResponse({'success': True, 'message': 'Prompt dismissed.'})
