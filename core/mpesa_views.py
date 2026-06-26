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
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required

from decimal import Decimal

from .models import Payment, Order, Transaction, Item, PendingTransactionPrompt, BarTab, BarTabEntry, Receipt, ItemPortionPreset, Store
from .mpesa import (
    initiate_stk_push, format_phone_ke, query_stk_status,
    register_c2b_url, generate_mpesa_qr, generate_emv_qr_string,
    resolve_mpesa_config, resolve_account_by_shortcode,
)
from .notifications import notify_transaction, create_in_app_notification

logger = logging.getLogger(__name__)


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _bridge_stk_to_prompt(payment):
    """Create a PendingTransactionPrompt for a completed STK Push that has no
    linked order or bar tab — i.e. a manual 'Request Payment' from the dashboard.
    Idempotent: skips if a prompt for this payment already exists."""
    if payment.order_id or payment.bar_tab_id:
        return  # Tab/order have their own completion logic
    receipt = payment.mpesa_receipt
    # When the active-poll path confirms success before the callback arrives,
    # mpesa_receipt is empty. Use a synthetic key so the prompt still appears.
    # Max: "STK" + 10-digit payment ID = 13 chars, well within the 30-char field.
    dedup_key = receipt if receipt else f"STK{payment.id}"
    if PendingTransactionPrompt.objects.filter(mpesa_receipt=dedup_key).exists():
        return  # Already created (callback fired + poll fired, or duplicate call)

    prompt = PendingTransactionPrompt.objects.create(
        business=payment.business,
        amount=payment.amount,
        phone=payment.phone or '',
        mpesa_receipt=dedup_key,
        payment_channel='stk',
    )

    from accounts.models import UserProfile as _UP
    for up in _UP.objects.filter(
        business=payment.business, role__in=['owner', 'staff']
    ).select_related('user'):
        create_in_app_notification(
            user=up.user,
            title='💰 STK Payment Received!',
            message=(
                f"KES {float(payment.amount):,.0f} received"
                + (f" from {payment.phone}" if payment.phone else "")
                + f". Receipt: {receipt}. Please confirm what was sold."
            ),
            notification_type='transaction',
        )
    logger.info("STK prompt created: id=%s business=%s amount=%s", prompt.id, payment.business_id, payment.amount)


def _settle_tab_from_payment(payment):
    """FIFO-settle unpaid BarTabEntry rows up to the paid amount, then close
    the tab and issue a receipt if all entries are now paid."""
    try:
        tab = payment.bar_tab
        if not tab or tab.status != 'OPEN':
            return

        paid_amount = float(payment.amount)
        unpaid_entries = list(tab.entries.filter(is_paid=False).order_by('id').select_related('transaction'))
        now = timezone.now()
        for entry in unpaid_entries:
            if paid_amount <= 0:
                break
            entry_amt = float(entry.amount)
            if entry_amt <= paid_amount:
                entry.is_paid = True
                entry.payment_method = 'mpesa'
                entry.paid_at = now
                entry.save(update_fields=['is_paid', 'payment_method', 'paid_at'])
                # Flip the underlying transaction so it drops out of the debt tracker
                if entry.transaction_id:
                    entry.transaction.payment_method = 'mpesa'
                    entry.transaction.save(update_fields=['payment_method'])
                paid_amount -= entry_amt

        if not tab.entries.filter(is_paid=False).exists():
            tab.status = 'SETTLED'
            tab.settled_at = now
            tab.save(update_fields=['status', 'settled_at'])

            from .models import Receipt as _Receipt
            all_entries = list(tab.entries.all())
            lines = [
                {'name': e.description, 'qty': 1, 'subtotal': float(e.amount)}
                for e in all_entries
            ]
            _Receipt.issue(
                business=tab.business,
                lines=lines,
                payment_method='mpesa',
                customer_name=tab.customer_name,
            )
            logger.info("Tab #%s settled via STK receipt=%s", tab.id, payment.mpesa_receipt)
    except Exception as exc:
        logger.warning("Tab STK settlement failed for tab_id=%s: %s", getattr(payment, 'bar_tab_id', '?'), exc)


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

    # Find the matching payment — accept 'pending' OR 'failed' (in case the active-poll
    # prematurely marked it failed before the real callback arrived).
    try:
        payment = Payment.objects.get(
            checkout_request_id=checkout_request_id,
            status__in=['pending', 'failed'],
        )
    except Payment.DoesNotExist:
        logger.warning("No open payment for CheckoutID: %s (already completed or unknown)", checkout_request_id)
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

        # Settle bar tab via FIFO if linked
        if payment.bar_tab_id:
            _settle_tab_from_payment(payment)

        # Create reconciliation prompt for manual STK pushes (no order, no tab)
        _bridge_stk_to_prompt(payment)

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
    tab_id = data.get('tab_id')

    if not phone or not amount:
        return JsonResponse({'error': 'Phone and amount required'}, status=400)

    try:
        amount = int(amount)
        if amount < 1:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Amount must be a positive integer'}, status=400)

    phone_formatted = format_phone_ke(phone)

    # Determine business from tab, order, authenticated user, or public business_id
    business = None
    order = None
    bar_tab = None

    if tab_id:
        try:
            bar_tab = BarTab.objects.get(id=int(tab_id), status='OPEN')
            business = bar_tab.business
        except (BarTab.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'Tab not found or already closed'}, status=404)

    if not business and order_id:
        try:
            order = Order.objects.get(id=order_id)
            business = order.business
        except Order.DoesNotExist:
            return JsonResponse({'error': 'Order not found'}, status=404)

    if not business and request.user.is_authenticated:
        profile = getattr(request.user, 'userprofile', None)
        if profile and profile.business:
            business = profile.business

    if not business and data.get('business_id'):
        from accounts.models import Business as _Business
        try:
            business = _Business.objects.get(id=int(data['business_id']))
        except (_Business.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'Business not found'}, status=404)

    if not business:
        return JsonResponse({'error': 'Cannot determine business'}, status=400)

    # Build callback URL
    callback_url = request.build_absolute_uri('/mpesa/callback/')

    if bar_tab:
        account_ref = f"TAB-{bar_tab.id}"
    elif order:
        account_ref = order.order_number
    else:
        account_ref = f"DUKA-{business.id}"
    description = "Duka Mwecheche"

    # Determine store/source for per-counter routing
    stk_store = None
    if bar_tab:
        stk_store = getattr(bar_tab, 'store', None)
        if stk_store is None:
            # Derive from tab source: kitchen tabs are on the kitchen store
            if getattr(bar_tab, 'source', '') == 'kitchen':
                stk_store = Store.objects.filter(business=business, is_kitchen=True).first()

    cfg = resolve_mpesa_config(business, stk_store)
    stk_shortcode = (cfg['till'] or cfg['paybill'] or '').strip() or None

    # Create pending payment record (tagged with source)
    payment = Payment.objects.create(
        order=order,
        bar_tab=bar_tab,
        business=business,
        store=cfg['store'],
        source=cfg['source'],
        amount=amount,
        method='mpesa',
        status='pending',
        phone=phone_formatted,
    )

    # Call Safaricom STK Push — use resolved credentials
    result = initiate_stk_push(
        phone_number=phone_formatted,
        amount=amount,
        account_reference=account_ref,
        description=description,
        callback_url=callback_url,
        consumer_key=cfg['consumer_key'] or None,
        consumer_secret=cfg['consumer_secret'] or None,
        shortcode=stk_shortcode,
        passkey=cfg['passkey'] or None,
        use_till=bool(cfg['till']),
        env=cfg['environment'],
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

    # If still pending, query Safaricom to speed up success detection.
    # IMPORTANT: only mark 'completed' from the query — NEVER mark 'failed'.
    # The Safaricom query API returns misleading codes (e.g. 1037 "DS timeout",
    # 1019 "expired") even while the customer is still on the approve screen, and
    # marking 'failed' here causes mpesa_callback (the real authoritative result)
    # to be silently discarded because it looks for status='pending'.
    # All failure marking must come exclusively from mpesa_callback.
    if payment.status == 'pending' and payment.checkout_request_id:
        biz = payment.business
        cfg = resolve_mpesa_config(biz, payment.store)
        stk_result = query_stk_status(
            payment.checkout_request_id,
            consumer_key=cfg['consumer_key'] or None,
            consumer_secret=cfg['consumer_secret'] or None,
            shortcode=(cfg['till'] or cfg['paybill'] or '').strip() or None,
            passkey=cfg['passkey'] or None,
            env=cfg['environment'],
        )
        if stk_result and stk_result.get('ResultCode') is not None:
            result_code = int(stk_result['ResultCode'])
            if result_code == 0:
                payment.status = 'completed'
                payment.result_code = result_code
                payment.completed_at = timezone.now()
                payment.save()
                # Re-read: if mpesa_callback already landed and set mpesa_receipt,
                # use the real receipt as dedup key instead of the synthetic one.
                payment.refresh_from_db()
                if payment.bar_tab_id:
                    _settle_tab_from_payment(payment)
                _bridge_stk_to_prompt(payment)

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

    # Match shortcode to a business (and optional store) using the resolver
    business, matched_store, channel = resolve_account_by_shortcode(shortcode)

    if not business:
        logger.warning("C2B: No business found for shortcode %s", shortcode)
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    # Determine source from the matched store
    c2b_source = 'kitchen' if (matched_store and getattr(matched_store, 'is_kitchen', False)) else 'bar'

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

    # Also create a tagged Payment record for cross-check reconciliation
    Payment.objects.create(
        business=business,
        store=matched_store,
        source=c2b_source,
        amount=amount,
        method='mpesa',
        status='completed',
        phone=msisdn,
        mpesa_receipt=trans_id,
        completed_at=timezone.now(),
    )

    # Notify staff + owner for this business
    from accounts.models import UserProfile
    biz_users = UserProfile.objects.filter(
        business=business, role__in=['owner', 'staff']
    ).select_related('user')

    for profile in biz_users:
        create_in_app_notification(
            user=profile.user,
            title='💰 Payment Received!',
            message=(
                f"KES {amount:,.0f} received from {msisdn} via {channel.upper()}"
                + (f" [{c2b_source.capitalize()}]" if c2b_source == 'kitchen' else "")
                + f". Receipt: {trans_id}. Please confirm what was sold."
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
    ).select_related('transaction__item', 'confirmed_by', 'receipt')[:20]

    items = Item.objects.filter(
        business=profile.business
    ).select_related('store').order_by('description')

    # Build preset lookup keyed by item_id for the template JS
    preset_qs = ItemPortionPreset.objects.filter(
        item__business=profile.business,
    ).order_by('item_id', 'display_order', 'price')
    presets_by_item = {}
    for p in preset_qs:
        presets_by_item.setdefault(str(p.item_id), []).append({
            'id': p.id,
            'label': p.label,
            'price': float(p.price),
            'qty': float(p.quantity_consumed),
        })

    return render(request, 'core/pending_prompts.html', {
        'prompts': prompts,
        'confirmed': confirmed,
        'items': items,
        'presets_by_item': presets_by_item,
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
    preset_id = request.POST.get('preset_id') or None

    if not item_id:
        return JsonResponse({'error': 'Please select an item'}, status=400)

    try:
        item = Item.objects.get(id=item_id, business=profile.business)
    except Item.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)

    # Resolve qty and line label — preset takes precedence over manual qty
    preset = None
    line_label = item.description
    if preset_id:
        try:
            preset = ItemPortionPreset.objects.get(id=preset_id, item=item)
        except ItemPortionPreset.DoesNotExist:
            return JsonResponse({'error': 'Preset not found'}, status=404)
        qty_decimal = Decimal(str(float(preset.quantity_consumed)))
        line_label = f"{item.description} ({preset.label})"
    else:
        raw_qty = request.POST.get('qty', '1')
        try:
            qty_int = int(raw_qty)
            if qty_int < 1:
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Quantity must be a positive integer'}, status=400)
        qty_decimal = Decimal(qty_int)

    today = timezone.localtime(timezone.now()).date()
    # sale_amount = the actual M-Pesa receipt amount — ensures revenue() is accurate
    txn = Transaction.objects.create(
        item=item,
        date=today,
        type='Issue',
        qty=-qty_decimal,
        sale_amount=prompt.amount,
        invoice_no=prompt.mpesa_receipt or f"MPESA-{prompt.id}",
        recipient=prompt.phone,
        business=profile.business,
    )

    # Issue a receipt so the transaction has a shareable record
    qty_display = float(qty_decimal)
    unit_price = round(float(prompt.amount) / qty_display, 2) if qty_display else float(prompt.amount)
    receipt_obj = Receipt.issue(
        business=profile.business,
        lines=[{
            'name': line_label,
            'qty': qty_display,
            'unit_price': unit_price,
            'subtotal': float(prompt.amount),
        }],
        payment_method='mpesa',
        user=request.user,
        customer_name='',
        customer_phone=prompt.phone or '',
    )

    prompt.status = 'confirmed'
    prompt.transaction = txn
    prompt.receipt = receipt_obj
    prompt.confirmed_by = request.user
    prompt.confirmed_at = timezone.now()
    prompt.save()

    # SMS the payer with the receipt link (non-blocking)
    if prompt.phone:
        try:
            from .notifications import send_sms_notification
            receipt_url = f"https://www.dukamwecheche.co.ke/r/{receipt_obj.token}/"
            sms_text = (
                f"Asante! KES {int(float(prompt.amount))} kwa {profile.business.name}. "
                f"Risiti: {receipt_url}"
            )
            send_sms_notification(sms_text, prompt.phone)
        except Exception:
            pass

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
        'message': f"Logged: {float(qty_decimal):g}x {line_label} — KES {float(prompt.amount):,.0f}",
        'receipt_url': f"/r/{receipt_obj.token}/",
    })


@require_GET
def mpesa_qr_view(request):
    """Generate an M-Pesa payment QR code for a business (or a specific counter).

    GET /mpesa/qr/?business_id=X&amount=Y&store_id=Z (amount + store_id optional)

    When store_id is provided and the store has has_own_mpesa=True, the QR encodes
    that counter's till/paybill. Otherwise falls back to business config.

    Path 1 — calls Safaricom Daraja Dynamic QR API, returns base64 PNG.
    Path 2 — if Daraja fails, builds EMVCo string for client-side QR rendering.
    Path 3 — fallback URL QR.

    Response JSON:
        {"mode": "img", "data": "<base64>"}   — Path 1 success
        {"mode": "emv", "data": "<string>"}   — Path 2 EMVCo
        {"mode": "url", "data": "<url>"}      — Path 3 fallback
    """
    business_id = request.GET.get('business_id')
    amount_str = request.GET.get('amount', '')
    store_id = request.GET.get('store_id')

    if not business_id:
        return JsonResponse({'error': 'business_id required'}, status=400)

    from accounts.models import Business as _Business
    try:
        business = _Business.objects.get(id=int(business_id))
    except (_Business.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'Business not found'}, status=404)

    # Resolve store for per-counter QR
    qr_store = None
    if store_id:
        try:
            qr_store = Store.objects.get(id=int(store_id), business=business)
        except (Store.DoesNotExist, ValueError, TypeError):
            pass

    cfg = resolve_mpesa_config(business, qr_store)
    shortcode = cfg['till'] or cfg['paybill']
    trx_code = 'BG' if cfg['till'] else ('PB' if cfg['paybill'] else '')

    amount = None
    if amount_str:
        try:
            amount = int(float(amount_str))
            if amount <= 0:
                amount = None
        except (ValueError, TypeError):
            amount = None

    fallback_url = request.build_absolute_uri(f'/pay/{business.id}/')

    if shortcode and trx_code:
        qr_b64 = generate_mpesa_qr(
            merchant_name=business.name,
            shortcode=shortcode,
            trx_code=trx_code,
            amount=amount,
            ref_no='PAYMENT',
        )
        if qr_b64:
            return JsonResponse({'mode': 'img', 'data': qr_b64})

        emv_str = generate_emv_qr_string(
            merchant_name=business.name,
            shortcode=shortcode,
            trx_code=trx_code,
            amount=amount,
        )
        if emv_str:
            return JsonResponse({'mode': 'emv', 'data': emv_str})

    return JsonResponse({'mode': 'url', 'data': fallback_url})


def business_payment_page(request, business_id):
    """Public page showing a business's M-Pesa payment channels.

    No login required — this is shared with customers.
    Doubles as a printable payment poster.
    """
    from accounts.models import Business as _Business
    from django.shortcuts import get_object_or_404
    business = get_object_or_404(_Business, id=business_id)
    return render(request, 'core/business_payment_page.html', {
        'business': business,
        'payment_page_url': request.build_absolute_uri(),
    })


@login_required
@require_POST
def register_business_c2b(request):
    """Owner triggers C2B URL registration with Safaricom for their Till/Paybill.

    Uses the business's own Daraja credentials stored in payment settings.
    Returns JSON so the payment settings page can show instant feedback.
    """
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business or not profile.is_owner:
        return JsonResponse({'success': False, 'error': 'Only business owners can register C2B URLs.'}, status=403)

    business = profile.business

    consumer_key = business.daraja_consumer_key.strip()
    consumer_secret = business.daraja_consumer_secret.strip()

    if not consumer_key or not consumer_secret:
        return JsonResponse({
            'success': False,
            'error': 'Please save your Daraja Consumer Key and Consumer Secret first.',
        }, status=400)

    # Determine shortcode: prefer Till, fall back to Paybill
    shortcode = (business.mpesa_till or business.mpesa_paybill or '').strip()
    if not shortcode:
        return JsonResponse({
            'success': False,
            'error': 'Please set your Till Number or Paybill Number in payment settings first.',
        }, status=400)

    base = 'https://www.dukamwecheche.co.ke'
    confirmation_url = f'{base}/mpesa/c2b/confirmation/'
    validation_url = f'{base}/mpesa/c2b/validation/'

    result = register_c2b_url(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        shortcode=shortcode,
        confirmation_url=confirmation_url,
        validation_url=validation_url,
        env=business.daraja_environment,
    )

    if result['success']:
        business.daraja_c2b_registered = True
        business.save(update_fields=['daraja_c2b_registered'])
        logger.info("C2B registered for business %s shortcode %s", business.id, shortcode)

    return JsonResponse(result)


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
