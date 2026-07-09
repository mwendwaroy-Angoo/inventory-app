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

_SITE_URL = 'https://www.dukamwecheche.co.ke'


def _sms_receipt_to_payer(payment, receipt):
    """Send an SMS receipt link to the customer who initiated the STK push.

    Uses payment.phone (the number M-Pesa prompted). Fires silently — any
    failure is swallowed so it never blocks the settlement path."""
    phone = (payment.phone or '').strip()
    if not phone or not receipt:
        return
    try:
        from .notifications import normalize_ke_phone, send_sms_notification
        normalized = normalize_ke_phone(phone)
        if normalized:
            receipt_url = f"{_SITE_URL}/r/{receipt.token}/"
            sms = (
                f"Asante! KES {int(float(payment.amount))} kwa "
                f"{payment.business.name}. Risiti: {receipt_url}"
            )
            send_sms_notification(sms, normalized)
    except Exception:
        pass


def _bridge_stk_to_prompt(payment):
    """Create a PendingTransactionPrompt for a completed STK Push that has no
    linked order or bar tab — i.e. a manual 'Request Payment' from the dashboard.
    Idempotent: skips if a prompt for this payment already exists."""
    if payment.order_id or payment.bar_tab_id or payment.kitchen_cart or payment.qs_cart:
        return  # Tab/order/kitchen/qs each have their own completion logic
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
    """Settle BarTabEntry rows for a completed STK Push.

    If payment.tab_entry_ids is set, settle exactly those entries (partial
    settlement path). Otherwise FIFO-settle unpaid entries up to the paid
    amount (full-tab path). Issues a receipt only when the whole tab is
    settled."""
    try:
        tab = payment.bar_tab
        if not tab or tab.status != 'OPEN':
            return

        now = timezone.now()
        entry_ids = payment.tab_entry_ids  # list of int IDs, or None

        if entry_ids:
            # Partial settlement: settle only the specified entries
            unpaid_entries = list(
                tab.entries.filter(id__in=entry_ids, is_paid=False)
                .select_related('transaction')
            )
        else:
            # Full-tab FIFO settlement
            paid_amount = float(payment.amount)
            unpaid_entries = list(tab.entries.filter(is_paid=False).order_by('id').select_related('transaction'))

        for entry in unpaid_entries:
            if not entry_ids:
                if paid_amount <= 0:
                    break
                entry_amt = float(entry.amount)
                if entry_amt > paid_amount:
                    continue
                paid_amount -= entry_amt
            entry.is_paid = True
            entry.payment_method = 'mpesa'
            entry.paid_at = now
            entry.save(update_fields=['is_paid', 'payment_method', 'paid_at'])
            if entry.transaction_id:
                entry.transaction.payment_method = 'mpesa'
                entry.transaction.save(update_fields=['payment_method'])

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
            tab_rcpt = _Receipt.issue(
                business=tab.business,
                lines=lines,
                payment_method='mpesa',
                customer_name=tab.customer_name,
                meta={'tab_id': tab.id},
            )
            _sms_receipt_to_payer(payment, tab_rcpt)
            logger.info("Tab #%s settled via STK receipt=%s", tab.id, payment.mpesa_receipt)
    except Exception as exc:
        logger.warning("Tab STK settlement failed for tab_id=%s: %s", getattr(payment, 'bar_tab_id', '?'), exc)


def _create_debt_payment_from_receipt(payment):
    """Handle a customer-initiated STK debt block payment from the public receipt.

    Called when payment.receipt_token is set AND payment.tab_entry_ids is None.
    The customer paid a summary amount (not specific entries) from the debt block
    UI. Creates a CustomerDebtPayment and runs FIFO entry reconciliation.
    """
    try:
        from .models import Receipt as _Receipt, BarTab as _BarTab, Customer as _Customer
        from .models import CustomerDebtPayment as _CDP, BarTabEntry as _BTE
        from .debt_views import _get_customer_debt_data

        receipt = _Receipt.objects.filter(token=payment.receipt_token).first()
        if not receipt:
            return

        tab_id = receipt.meta.get('tab_id') if receipt.meta else None
        if not tab_id:
            return

        tab = _BarTab.objects.filter(id=tab_id).first()
        if not tab or not tab.customer_id:
            return

        cust = _Customer.objects.filter(id=tab.customer_id, business=payment.business).first()
        if not cust:
            return

        source     = payment.source or 'bar'
        mpesa_ref  = payment.mpesa_receipt or ''
        token_frag = payment.receipt_token[:12]

        # Idempotency: don't create a duplicate for the same M-Pesa receipt
        if mpesa_ref and _CDP.objects.filter(
            business=payment.business,
            customer=cust,
            notes__contains=mpesa_ref,
        ).exists():
            return

        # Compute unpaid state BEFORE creating the payment (FIFO reconciliation needs this)
        debt_data    = _get_customer_debt_data(cust, payment.business, source)
        unpaid_before = debt_data.get('unpaid_transactions', [])

        _CDP.objects.create(
            customer=cust,
            business=payment.business,
            amount_paid=payment.amount,
            payment_method='mpesa',
            source=source,
            notes=f'M-Pesa {mpesa_ref} · risiti {token_frag}',
        )

        # FIFO entry reconciliation — only mark entries is_paid=True when fully covered
        settled_tabs = list(_BarTab.objects.filter(
            business=payment.business,
            customer=cust,
            status='SETTLED',
        ).values_list('id', flat=True))

        if settled_tabs and unpaid_before:
            now_ts = timezone.now()
            paid_remaining = float(payment.amount)
            for entry in unpaid_before:
                if paid_remaining <= 0:
                    break
                txn = entry['txn']
                entry_amount = float(entry['amount'])
                covered = round(min(entry_amount, paid_remaining), 2)
                paid_remaining = round(paid_remaining - covered, 2)
                if covered >= entry_amount:
                    _BTE.objects.filter(
                        tab__id__in=settled_tabs,
                        transaction=txn,
                        is_paid=False,
                    ).update(is_paid=True, paid_at=now_ts, payment_method='mpesa')

        # Notify via the existing receipt SMS helper
        try:
            _sms_receipt_to_payer(payment, receipt)
        except Exception:
            pass

        logger.info(
            "Debt payment created from receipt: customer=%s amount=%s source=%s mpesa=%s",
            cust.id, payment.amount, source, mpesa_ref,
        )
    except Exception:
        logger.exception("_create_debt_payment_from_receipt failed payment_id=%s", payment.id)


def _settle_debt_customer_from_payment(payment):
    """Handle STK Push debt settlement initiated by staff from the debt tracker page.

    Called when payment.debt_customer_id is set. Delegates to _do_settle_debt_payment
    which creates CustomerDebtPayment, runs FIFO reconciliation, issues receipt, SMS.
    Idempotent: guarded by mpesa_receipt in CustomerDebtPayment.notes.
    """
    try:
        from .models import Customer as _Customer, CustomerDebtPayment as _CDP
        from .debt_views import _do_settle_debt_payment

        customer = _Customer.objects.filter(
            id=payment.debt_customer_id, business=payment.business
        ).first()
        if not customer:
            return

        source = payment.source or 'bar'
        mpesa_ref = payment.mpesa_receipt or ''

        # Idempotency: skip if we already recorded a CDP with this M-Pesa receipt
        if mpesa_ref and _CDP.objects.filter(
            business=payment.business,
            customer=customer,
            notes__contains=mpesa_ref,
        ).exists():
            return

        notes = f'M-Pesa {mpesa_ref} · STK deni' if mpesa_ref else 'STK deni'

        _do_settle_debt_payment(
            customer=customer,
            business=payment.business,
            amount=float(payment.amount),
            payment_method='mpesa',
            source=source,
            notes=notes,
            recorded_by=None,
        )

        logger.info(
            "Debt STK settled: customer=%s amount=%s source=%s mpesa=%s",
            customer.id, payment.amount, source, mpesa_ref,
        )
    except Exception:
        logger.exception("_settle_debt_customer_from_payment failed payment_id=%s", payment.id)


def _settle_receipt_entries_from_payment(payment):
    """Settle BarTabEntry rows across all tabs linked to a receipt.

    Used for customer-initiated STK push from the public receipt page
    (/r/<token>/pay/). Handles entries from multiple tabs (bar + kitchen)
    in one payment. Closes any tab whose remaining unpaid balance reaches 0.
    """
    try:
        from .models import BarTabEntry, BarTab, Receipt as _Receipt
        entry_ids = payment.tab_entry_ids
        if not entry_ids:
            return

        now = timezone.now()
        entries = list(
            BarTabEntry.objects.filter(
                id__in=entry_ids,
                tab__business=payment.business,
                is_paid=False,
            ).select_related('tab', 'transaction')
        )

        tabs_affected = set()
        for entry in entries:
            entry.is_paid = True
            entry.payment_method = 'mpesa'
            entry.paid_at = now
            entry.save(update_fields=['is_paid', 'payment_method', 'paid_at'])
            if entry.transaction_id:
                entry.transaction.payment_method = 'mpesa'
                entry.transaction.save(update_fields=['payment_method'])
            tabs_affected.add(entry.tab_id)

        # Close fully-paid tabs
        for tab_id in tabs_affected:
            tab = BarTab.objects.filter(id=tab_id).first()
            if tab and not tab.entries.filter(is_paid=False).exists():
                tab.status = 'SETTLED'
                tab.settled_at = now
                tab.save(update_fields=['status', 'settled_at'])

        # SMS the customer and update the receipt payment_method
        rcpt_for_notif = None
        if payment.receipt_token:
            rcpt_for_notif = _Receipt.objects.filter(token=payment.receipt_token).first()
            if rcpt_for_notif:
                all_tab_ids = [rcpt_for_notif.meta.get('tab_id')] + list(rcpt_for_notif.meta.get('linked_tab_ids') or [])
                still_unpaid = BarTabEntry.objects.filter(
                    tab__id__in=all_tab_ids, is_paid=False
                ).exists()
                if not still_unpaid:
                    rcpt_for_notif.payment_method = 'mpesa'
                    rcpt_for_notif.save(update_fields=['payment_method'])
                _sms_receipt_to_payer(payment, rcpt_for_notif)

        # Notify original serving staff, current on-shift staff, owners, managers
        try:
            from .models import Shift as _Shift, Notification as _Notif
            from .notifications import normalize_ke_phone, send_sms_notification
            from accounts.models import UserProfile as _UP

            customer_name = (rcpt_for_notif.customer_name if rcpt_for_notif else '') or 'Mteja'
            receipt_num = rcpt_for_notif.receipt_number if rcpt_for_notif else ''
            paid_amt = float(payment.amount)

            # Remaining outstanding across all linked tabs
            notif_tab_ids = []
            if rcpt_for_notif and rcpt_for_notif.meta:
                notif_tab_ids = ([rcpt_for_notif.meta.get('tab_id')]
                                 + list(rcpt_for_notif.meta.get('linked_tab_ids') or []))
            remaining_amt = float(sum(
                e.amount for e in BarTabEntry.objects.filter(
                    tab__id__in=notif_tab_ids, is_paid=False
                )
            )) if notif_tab_ids else 0.0

            if remaining_amt > 0:
                notif_msg = (
                    f"💰 {customer_name} amelipa KES {paid_amt:,.0f} kwa deni. "
                    f"Baki: KES {remaining_amt:,.0f}. Risiti #{receipt_num}"
                )
            else:
                notif_msg = (
                    f"✅ {customer_name} amelipa deni lote (KES {paid_amt:,.0f}). "
                    f"Risiti #{receipt_num}"
                )

            notify_targets = {}  # user_pk → UserProfile
            business = payment.business

            # Original servers (tab served_by)
            for tab_id in tabs_affected:
                tab_obj = BarTab.objects.filter(id=tab_id).select_related('served_by').first()
                if tab_obj and tab_obj.served_by_id:
                    up = _UP.objects.filter(user_id=tab_obj.served_by_id, business=business).first()
                    if up:
                        notify_targets[tab_obj.served_by_id] = up

            # Currently on-shift staff
            for sh in _Shift.objects.filter(business=business, status='OPEN').select_related('staff'):
                up = _UP.objects.filter(user_id=sh.staff_id, business=business).first()
                if up:
                    notify_targets[sh.staff_id] = up

            # Owners and managers
            for up in _UP.objects.filter(business=business, role__in=['owner', 'manager']):
                notify_targets[up.user_id] = up

            for up in notify_targets.values():
                _Notif.objects.create(
                    business=business,
                    user=up.user,
                    message=notif_msg,
                )
                phone = (up.phone or '').strip()
                if phone:
                    phone_n = normalize_ke_phone(phone)
                    if phone_n:
                        send_sms_notification(notif_msg, phone_n)
        except Exception as _notif_err:
            logger.warning("Receipt payment notifications failed payment=%s: %s", payment.id, _notif_err)

        logger.info(
            "Receipt STK settled entries=%s business=%s mpesa_receipt=%s",
            entry_ids, payment.business_id, payment.mpesa_receipt,
        )
    except Exception as exc:
        logger.warning("Receipt STK settlement failed payment=%s: %s", payment.id, exc)


def _settle_kitchen_order_from_payment(payment):
    """Process a kitchen cart after STK Push success — server-side fallback.

    Called from mpesa_callback and payment_status poll when payment.kitchen_cart
    is set. Idempotent: uses payment.kitchen_settled + select_for_update so only
    one of (Daraja callback, JS poll path) processes the cart, whichever arrives
    first. The other path detects kitchen_settled=True and skips."""
    from django.db import transaction as db_txn
    from core.models import KitchenBatch, ProduceBunch
    from core.kitchen_views import _kitchen_store

    try:
        with db_txn.atomic():
            pmt = Payment.objects.select_for_update().get(id=payment.id)
            if pmt.kitchen_settled:
                logger.info("Kitchen STK already settled: payment_id=%s", payment.id)
                return
            pmt.kitchen_settled = True
            pmt.save(update_fields=['kitchen_settled'])

        cart = payment.kitchen_cart or []
        business = payment.business
        kitchen_store = _kitchen_store(business)
        if not kitchen_store:
            logger.warning("Kitchen STK: no kitchen store for business=%s", business.id)
            return

        receipt_lines = []
        total = Decimal('0')

        for entry in cart:
            amount = Decimal(str(entry.get('amount', 0)))
            qty = Decimal(str(entry.get('qty', 1)))
            desc = entry.get('description', '')
            batch_id  = entry.get('batch_id')
            bunch_id  = entry.get('bunch_id')
            item_id   = entry.get('item_id')
            preset_id = entry.get('preset_id')

            if not amount:
                continue

            if batch_id:
                try:
                    batch = KitchenBatch.objects.get(id=batch_id, business=business, status='OPEN')
                    preset = ItemPortionPreset.objects.filter(id=preset_id, item=batch.item).first() if preset_id else None
                    batch.record_sale(amount=amount, payment_method='mpesa', recipient='', preset=preset)
                    receipt_lines.append({'name': desc, 'subtotal': float(amount)})
                    total += amount
                except KitchenBatch.DoesNotExist:
                    logger.warning("Kitchen STK: batch %s not found or not OPEN", batch_id)

            elif bunch_id:
                try:
                    bunch = ProduceBunch.objects.get(id=bunch_id, business=business, status='OPEN')
                    bunch.record_sale(amount=amount, payment_method='mpesa', recipient='')
                    receipt_lines.append({'name': desc, 'subtotal': float(amount)})
                    total += amount
                except ProduceBunch.DoesNotExist:
                    logger.warning("Kitchen STK: bunch %s not found or not OPEN", bunch_id)

            elif item_id:
                try:
                    item = Item.objects.get(id=item_id, store=kitchen_store)
                    Transaction.objects.create(
                        business=business, item=item, type='Issue',
                        qty=-qty, sale_amount=amount,
                        payment_method='mpesa', recipient='',
                        date=timezone.localdate(),
                    )
                    receipt_lines.append({'name': desc, 'subtotal': float(amount), 'qty': float(qty)})
                    total += amount
                except Item.DoesNotExist:
                    logger.warning("Kitchen STK: item %s not found in kitchen store", item_id)

        if receipt_lines:
            kb_rcpt = Receipt.issue(
                business=business,
                lines=receipt_lines,
                payment_method='mpesa',
                user=None,
                customer_name='',
                customer_phone='',
                source='kitchen',
            )
            _sms_receipt_to_payer(payment, kb_rcpt)
            logger.info("Kitchen STK settled from callback: payment_id=%s lines=%d total=%s",
                        payment.id, len(receipt_lines), total)

    except Exception as exc:
        logger.warning("Kitchen STK settlement failed: payment_id=%s %s", payment.id, exc)


def _settle_qs_from_payment(payment):
    """Process a Quick Sell cart after STK Push success — server-side settlement.

    Called from mpesa_callback and payment_status when payment.qs_cart is set.
    Idempotent: select_for_update + qs_settled flag so only one path processes it."""
    from django.db import transaction as db_txn

    try:
        with db_txn.atomic():
            pmt = Payment.objects.select_for_update().get(id=payment.id)
            if pmt.qs_settled:
                logger.info("QS STK already settled: payment_id=%s", payment.id)
                return
            pmt.qs_settled = True
            pmt.save(update_fields=['qs_settled'])

        cart = payment.qs_cart or []
        business = payment.business
        today = timezone.localdate()

        receipt_lines = []
        total = Decimal('0')

        for entry in cart:
            item_id   = entry.get('item_id')
            qty       = Decimal(str(entry.get('qty', 1)))
            amount    = Decimal(str(entry.get('amount', 0)))
            desc      = entry.get('description', '')
            preset_id = entry.get('preset_id')
            bunch_id  = entry.get('bunch_id')

            if not amount:
                continue

            if bunch_id:
                try:
                    from .models import ProduceBunch
                    bunch = ProduceBunch.objects.get(id=bunch_id, business=business, status='OPEN')
                    bunch.record_sale(amount=amount, payment_method='mpesa', recipient='')
                    receipt_lines.append({'name': desc, 'subtotal': float(amount)})
                    total += amount
                except Exception:
                    logger.warning("QS STK: bunch %s not found or not OPEN", bunch_id)
                continue

            if not item_id:
                continue

            try:
                item = Item.objects.get(id=item_id, business=business)
            except Item.DoesNotExist:
                logger.warning("QS STK: item %s not found for business %s", item_id, business.id)
                continue

            preset = ItemPortionPreset.objects.filter(id=preset_id, item=item).first() if preset_id else None
            sale_amount = amount if (preset or amount != qty * item.selling_price) else None

            Transaction.objects.create(
                business=business,
                item=item,
                type='Issue',
                qty=-qty,
                sale_amount=sale_amount,
                payment_method='mpesa',
                recipient='',
                date=today,
            )
            receipt_lines.append({'name': desc, 'qty': float(qty), 'subtotal': float(amount)})
            total += amount

        if receipt_lines:
            qs_rcpt = Receipt.issue(
                business=business,
                lines=receipt_lines,
                payment_method='mpesa',
                user=None,
                customer_name='',
                customer_phone=payment.phone or '',
            )
            _sms_receipt_to_payer(payment, qs_rcpt)
            logger.info("QS STK settled: payment_id=%s lines=%d total=%s", payment.id, len(receipt_lines), total)

    except Exception as exc:
        logger.warning("QS STK settlement failed: payment_id=%s %s", payment.id, exc)


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

        # Customer-initiated from public receipt page
        if payment.receipt_token:
            if payment.tab_entry_ids is not None:
                # Entry-selection mode: mark specific BarTabEntry rows paid
                _settle_receipt_entries_from_payment(payment)
            else:
                # Debt block mode: create CustomerDebtPayment + FIFO reconciliation
                _create_debt_payment_from_receipt(payment)
        # Settle bar tab (full or partial) if linked (staff-side STK push)
        elif payment.bar_tab_id:
            _settle_tab_from_payment(payment)
        # Staff-initiated debt STK Push from the debt tracker page
        elif payment.debt_customer_id:
            _settle_debt_customer_from_payment(payment)

        # Settle kitchen cart if this was a kitchen STK push
        if payment.kitchen_cart:
            _settle_kitchen_order_from_payment(payment)

        # Settle Quick Sell cart if this was a QS checkout STK push
        if payment.qs_cart:
            _settle_qs_from_payment(payment)

        # Create reconciliation prompt for manual STK pushes (no order, no tab, no kitchen, no qs)
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
    kitchen_cart = data.get('kitchen_cart')   # list or None — kitchen board STK push
    entry_ids = data.get('entry_ids')         # list of int IDs — partial tab STK push
    qs_cart = data.get('qs_cart')             # list or None — QS checkout STK push

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
        kitchen_cart=kitchen_cart if isinstance(kitchen_cart, list) else None,
        tab_entry_ids=entry_ids if isinstance(entry_ids, list) else None,
        qs_cart=qs_cart if isinstance(qs_cart, list) else None,
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
                if payment.receipt_token:
                    if payment.tab_entry_ids is not None:
                        _settle_receipt_entries_from_payment(payment)
                    else:
                        _create_debt_payment_from_receipt(payment)
                elif payment.bar_tab_id:
                    _settle_tab_from_payment(payment)
                elif payment.debt_customer_id:
                    _settle_debt_customer_from_payment(payment)
                if payment.kitchen_cart:
                    _settle_kitchen_order_from_payment(payment)
                if payment.qs_cart:
                    _settle_qs_from_payment(payment)
                _bridge_stk_to_prompt(payment)

    return JsonResponse({
        'payment_id': payment.id,
        'status': payment.status,
        'mpesa_receipt': payment.mpesa_receipt,
        'amount': float(payment.amount),
        'kitchen_settled': payment.kitchen_settled,
        'qs_settled': payment.qs_settled,
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
