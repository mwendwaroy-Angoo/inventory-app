import logging
import threading
from django.conf import settings
from django.utils import timezone as _tz
from datetime import timedelta as _td

logger = logging.getLogger(__name__)


# ── Notification Event Types ──────────────────────────────────────────────────
class NotifEvent:
    TRANSACTION_ISSUE   = 'transaction_issue'
    TRANSACTION_RECEIPT = 'transaction_receipt'
    LOW_STOCK           = 'low_stock'
    REORDER             = 'reorder'
    STAFF_LOGIN         = 'staff_login'
    STAFF_LOGOUT        = 'staff_logout'
    CUSTOMER_ORDER      = 'customer_order'
    DAILY_SUMMARY       = 'daily_summary'


# ── Routing Rules ─────────────────────────────────────────────────────────────
# Each event: (send_sms, send_email, rate_limit_sms)
# rate_limit_sms=True means obey the 10-minute bundling window
ROUTING_RULES = {
    NotifEvent.TRANSACTION_ISSUE:   (True,  True,  True),
    NotifEvent.TRANSACTION_RECEIPT: (False, False, False),
    NotifEvent.LOW_STOCK:           (False, True,  False),
    NotifEvent.REORDER:             (False, True,  False),
    NotifEvent.STAFF_LOGIN:         (False, True,  False),
    NotifEvent.STAFF_LOGOUT:        (False, False, False),
    NotifEvent.CUSTOMER_ORDER:      (True,  True,  False),
    NotifEvent.DAILY_SUMMARY:       (True,  True,  False),
}

BUNDLE_WINDOW_MINUTES = 10


def _sms_allowed_by_rate_limit(business):
    """
    Returns True if enough time has passed since the last transaction SMS.
    Updates last_txn_sms_at on the business if allowed.
    """
    now = _tz.now()
    if business.last_txn_sms_at is None:
        business.last_txn_sms_at = now
        business.save(update_fields=['last_txn_sms_at'])
        return True
    elapsed = now - business.last_txn_sms_at
    if elapsed >= _td(minutes=BUNDLE_WINDOW_MINUTES):
        business.last_txn_sms_at = now
        business.save(update_fields=['last_txn_sms_at'])
        return True
    return False


def route_notification(event_type, business, owner_phone, owner_email,
                       sms_message, email_subject, email_html,
                       text_message=None):
    """
    Central notification router. Fires SMS and/or email based on
    event type routing rules. Handles 10-minute SMS bundling for
    transaction events. Always returns (sms_sent, email_sent).
    """
    rules = ROUTING_RULES.get(event_type)
    if not rules:
        logger.warning('Unknown notification event type: %s', event_type)
        return False, False

    should_sms, should_email, rate_limited = rules
    sms_sent = False
    email_sent = False

    # SMS
    if should_sms and owner_phone:
        try:
            allowed = (not rate_limited) or _sms_allowed_by_rate_limit(business)
            if allowed:
                phone = normalize_ke_phone(owner_phone)
                if phone:
                    send_sms_notification(sms_message, phone)
                    sms_sent = True
            else:
                logger.info(
                    'SMS bundled (within %d-min window) for %s event on business %s',
                    BUNDLE_WINDOW_MINUTES, event_type, business.id
                )
        except Exception as e:
            logger.error('Router SMS failed [%s]: %s', event_type, e)

    # Email
    if should_email and owner_email:
        try:
            send_email_notification(owner_email, email_subject, email_html, text_message)
            email_sent = True
        except Exception as e:
            logger.error('Router email failed [%s]: %s', event_type, e)

    return sms_sent, email_sent


def normalize_ke_phone(phone):
    """
    Normalize a Kenyan phone number to international format for Africa's Talking.
    Handles: 07XXXXXXXX → +2547XXXXXXXX
              254XXXXXXXXX → +254XXXXXXXXX
              +254XXXXXXXXX → unchanged
    Returns None if the number cannot be normalized.
    """
    if not phone:
        return None
    phone = phone.strip().replace(' ', '').replace('-', '')
    if phone.startswith('+254') and len(phone) == 13:
        return phone  # Already correct
    if phone.startswith('254') and len(phone) == 12:
        return '+' + phone
    if phone.startswith('0') and len(phone) == 10:
        return '+254' + phone[1:]
    return None  # Unrecognizable format


def notify_transaction_async(transaction_id, business_id, daily_count=0, user_id=None):
    """Dispatch notification in a background thread so the HTTP response
    is never blocked by email / SMS / WhatsApp API calls.

    Accepts primary keys (not model instances) so each thread opens its
    own DB connection – Django ORM per-thread connections are safe.
    """
    from .models import Transaction
    from accounts.models import Business

    def _worker():
        try:
            transaction = Transaction.objects.get(id=transaction_id)
            business = Business.objects.get(id=business_id)
            user = None
            if user_id:
                from django.contrib.auth.models import User

                user = User.objects.get(id=user_id)
            notify_transaction(transaction, business, daily_count, user=user)
        except Exception:
            logger.exception(
                "Background notification failed for transaction %s", transaction_id
            )

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    logger.debug(
        "Dispatched background notification thread for transaction %s", transaction_id
    )


def send_email_notification(to_email, subject, html_message, text_message=None):
    """Send email via Resend API — works on Render free tier (HTTPS, not SMTP)."""
    from django.conf import settings as _settings
    api_key = getattr(_settings, 'RESEND_API_KEY', '') or ''
    if not api_key:
        logger.warning(
            'Email skipped — RESEND_API_KEY not configured. Recipient: %s', to_email
        )
        return False
    try:
        import resend
        resend.api_key = api_key
        params = {
            'from': _settings.DEFAULT_FROM_EMAIL,
            'to': [to_email],
            'subject': subject,
            'html': html_message,
        }
        if text_message:
            params['text'] = text_message
        resend.Emails.send(params)
        logger.info('Email sent via Resend to %s — subject: %s', to_email, subject)
        return True
    except Exception as e:
        logger.error('Email failed to %s — %s: %s', to_email, type(e).__name__, e)
        return False


def send_sms_notification(message, phone_number):
    if not phone_number:
        return False
    original_phone = phone_number
    phone_number = normalize_ke_phone(phone_number)
    if not phone_number:
        logger.warning('SMS skipped — could not normalize phone number: %s', original_phone)
        return False
    try:
        import africastalking

        africastalking.initialize(
            username=settings.AT_USERNAME, api_key=settings.AT_API_KEY
        )
        sms = africastalking.SMS
        response = sms.send(message, [phone_number])
        logger.info(f"SMS sent to {phone_number}: {response}")
        return True
    except Exception as e:
        logger.error(f"SMS failed to {phone_number}: {e}")
        return False


def send_whatsapp_notification(phone, message, business=None):
    """
    WhatsApp via Twilio — disabled until a production Twilio WhatsApp
    sender number is configured. Logs a warning so we know it was attempted.
    """
    logger.warning(
        "WhatsApp notification skipped (no production sender configured) "
        "for phone %s",
        phone,
    )
    return False


def create_in_app_notification(user, title, message, notification_type="info"):
    from core.models import Notification

    try:
        Notification.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type=notification_type,
        )
        logger.info(f"In-app notification created for {user.username}: {title}")
        return True
    except Exception as e:
        logger.error(f"In-app notification failed: {e}")
        return False


def notify_transaction(transaction, business, daily_count=0, user=None):
    item = transaction.item
    trans_type = transaction.type
    qty = abs(transaction.qty)
    recorded_by = (user.get_full_name() or user.username) if user else "N/A"

    owner_profile = business.users.filter(role="owner").first()
    if not owner_profile:
        logger.warning(f"No owner found for business {business.name}")
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    if trans_type == "Issue":
        emoji = "📤"
        action = "issued/sold"
    else:
        emoji = "📥"
        action = "received"

    # In-app notification always (both Issue and Receipt)
    create_in_app_notification(
        owner,
        f"{emoji} {trans_type}: {item.description}",
        f"{qty} {item.unit} {action}. Balance: {item.current_balance()}. By: {recorded_by}",
        notification_type="transaction",
    )

    # Issue: route through central router (SMS with 10-min bundling + email)
    # Receipt: in-app only — cost price email is sent separately from views.py
    if trans_type == 'Issue':
        sms_msg = (
            f'{business.name}: {qty} {item.unit} of {item.description} issued'
            f'{" to " + transaction.recipient if transaction.recipient else ""}. '
            f'Balance: {item.current_balance()} {item.unit}.'
        )
        email_subject = f'Transaction Alert — {business.name}'
        email_html = f"""
<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
    <h2 style="color:#c9a84c;">🧾 Transaction Recorded</h2>
    <p>Business: <strong>{business.name}</strong></p>
    <div style="background:#f5f5f5;padding:1rem;border-radius:8px;margin:1rem 0;">
        <strong>{item.description}</strong><br>
        Type: Issue / Sale<br>
        Quantity: {qty} {item.unit}<br>
        {'Customer: ' + transaction.recipient + '<br>' if transaction.recipient else ''}
        Remaining balance: {item.current_balance()} {item.unit}<br>
        Recorded by: {recorded_by}<br>
        Date: {transaction.date}
    </div>
    <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
</div>
"""
        route_notification(
            NotifEvent.TRANSACTION_ISSUE,
            business, owner_phone, owner_email,
            sms_msg, email_subject, email_html,
        )

    if item.needs_reorder():
        notify_reorder_alert(item, business, owner, owner_email, owner_phone)


def notify_reorder_alert(item, business, owner, owner_email, owner_phone):
    subject = f"⚠️ Low Stock Alert — {item.description}"
    text_message = (
        f"Low stock alert from Duka Mwecheche\n\n"
        f"Business: {business.name}\n"
        f"Item: {item.description} ({item.material_no})\n"
        f"Current Balance: {item.current_balance()} {item.unit}\n"
        f"Reorder Level: {item.reorder_level} {item.unit}\n"
        f"Reorder Quantity: {item.reorder_quantity} {item.unit}\n\n"
        f"Please restock this item soon.\n\n"
        f"— Duka Mwecheche"
    )
    create_in_app_notification(
        owner,
        f"⚠️ Low Stock: {item.description}",
        f"Balance: {item.current_balance()} {item.unit}. Reorder level: {item.reorder_level}",
        notification_type="warning",
    )
    route_notification(
        NotifEvent.LOW_STOCK,
        business, owner_phone, owner_email,
        '',  # no SMS per routing table
        subject, None,
        text_message=text_message,
    )


def notify_staff_login(user, business, action="logged in"):
    owner_profile = business.users.filter(role="owner").first()
    if not owner_profile:
        return

    owner = owner_profile.user
    staff_name = user.get_full_name() or user.username

    from django.utils import timezone

    now = timezone.localtime(timezone.now()).strftime("%B %d, %Y at %I:%M %p")

    emoji = "🟢" if action == "logged in" else "🔴"
    create_in_app_notification(
        owner,
        f"{emoji} {user.username} {action}",
        f"Staff member {action} at {now}",
        notification_type="staff",
    )

    # Email audit trail for logins only (routing rule: email only, no SMS)
    if action == "logged in":
        try:
            owner_profiles = business.users.filter(role='owner')
            for op in owner_profiles:
                owner_phone = getattr(op, 'phone', '') or business.phone or ''
                login_time = timezone.localtime(timezone.now()).strftime('%d %b %Y at %H:%M')
                email_html = f"""
<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
    <h2 style="color:#c9a84c;">👤 Staff Login</h2>
    <p><strong>{staff_name}</strong> logged in to {business.name}.</p>
    <p style="color:#888;">Time: {login_time}</p>
    <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
</div>
"""
                route_notification(
                    NotifEvent.STAFF_LOGIN,
                    business,
                    owner_phone,
                    op.user.email,
                    '',
                    f'Staff Login — {staff_name} | {business.name}',
                    email_html,
                )
        except Exception as e:
            logger.error('Login audit email failed: %s', e)


def notify_new_order(order):
    """Notify the business owner (and staff) about a new customer order."""
    business = order.business
    owner_profile = business.users.filter(role="owner").first()
    if not owner_profile:
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    delivery_label = "🚚 Delivery" if order.delivery_mode == "delivery" else "🏪 Pickup"
    pay_labels = {
        "mpesa": "💳 M-Pesa",
        "cash": "💵 Cash",
        "pickup_pay": "🏪 Pay at Pickup",
    }
    pay_label = pay_labels.get(order.payment_method, order.payment_method)

    items_text = ", ".join(
        f"{l.item.description} x{l.quantity}"
        for l in order.lines.select_related("item")
    )

    subject = f"🛒 New Order #{order.order_number} — {business.name}"
    message = (
        f"New order on Duka Mwecheche\n\n"
        f"Order: {order.order_number}\n"
        f"Customer: {order.customer_name} ({order.customer_phone})\n"
        f"Location: {order.customer_location or 'N/A'}\n"
        f"Items: {items_text}\n"
        f"Total: KES {order.total_amount:,.0f}\n"
        f"Delivery: {delivery_label}\n"
        f"Payment: {pay_label}\n"
        f"Notes: {order.notes or 'None'}\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification for owner
    create_in_app_notification(
        owner,
        f"🛒 New Order #{order.order_number}",
        f"{order.customer_name} — KES {order.total_amount:,.0f} ({delivery_label}, {pay_label})",
        notification_type="order",
    )

    # In-app notification for all staff
    for staff_profile in business.users.filter(role="staff"):
        create_in_app_notification(
            staff_profile.user,
            f"🛒 New Order #{order.order_number}",
            f"{order.customer_name} — KES {order.total_amount:,.0f} ({delivery_label})",
            notification_type="order",
        )

    # Route email + SMS through central router (CUSTOMER_ORDER: both channels, no bundling)
    sms_msg = (
        f'NEW ORDER: {order.customer_name} — KES {order.total_amount:,.0f}. '
        f'Log in to Duka Mwecheche to confirm.'
    )
    email_html = f"""
<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
    <h2 style="color:#c9a84c;">🛒 New Customer Order</h2>
    <p>Order from <strong>{order.customer_name}</strong></p>
    <div style="background:#f5f5f5;padding:1rem;border-radius:8px;margin:1rem 0;">
        Order #: {order.order_number}<br>
        Amount: KES {order.total_amount:,.0f}<br>
        Type: {delivery_label}<br>
        Payment: {pay_label}<br>
        Time: {order.created_at.strftime('%d %b %Y %H:%M')}
    </div>
    <p><a href="https://www.dukamwecheche.co.ke/orders/">View Order</a></p>
    <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
</div>
"""
    route_notification(
        NotifEvent.CUSTOMER_ORDER,
        business, owner_phone, owner_email,
        sms_msg, subject, email_html,
    )


def send_daily_summary(business):
    from datetime import date
    from collections import defaultdict
    from core.models import Transaction

    owner_profile = business.users.select_related("user").filter(role="owner").first()
    if not owner_profile:
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    today = date.today()
    sales = Transaction.objects.filter(
        business=business,
        type="Issue",
        date=today,
    ).select_related("item")

    receipts = Transaction.objects.filter(
        business=business,
        type="Receipt",
        date=today,
    ).count()

    # Single pass — avoids evaluating the queryset three times
    item_sales = defaultdict(int)
    total_revenue = 0
    total_cost = 0
    total_transactions = 0

    for t in sales:
        total_revenue += t.revenue()
        total_cost += t.cost()
        item_sales[t.item.description] += abs(t.qty)
        total_transactions += 1

    total_profit = total_revenue - total_cost

    subject = f"📊 Daily Summary — {business.name} — {today}"
    message = (
        f"Daily Business Summary from Duka Mwecheche\n\n"
        f"Business: {business.name}\n"
        f"Date: {today}\n\n"
        f"{'='*40}\n"
        f"SALES SUMMARY\n"
        f"{'='*40}\n"
        f"Total Transactions: {total_transactions} sales, {receipts} receipts\n"
        f"Total Revenue:  KES {total_revenue:,.2f}\n"
        f"Total Cost:     KES {total_cost:,.2f}\n"
        f"Gross Profit:   KES {total_profit:,.2f}\n"
        f"Profit Margin:  "
        f"{round(total_profit/total_revenue*100, 1) if total_revenue > 0 else 0}%\n\n"
        f"{'='*40}\n"
        f"TOP ITEMS SOLD TODAY\n"
        f"{'='*40}\n"
    )

    top = sorted(item_sales.items(), key=lambda x: x[1], reverse=True)[:5]
    for item_name, qty in top:
        message += f"• {item_name}: {qty} units\n"

    message += f"\n{'='*40}\n"
    message += (
        "View full report at: https://stock-made-simpler-sms.onrender.com/sales/\n\n"
    )
    message += "— Duka Mwecheche"

    send_email_notification(owner_email, subject, None, text_message=message)

    # SMS nudge after daily summary email
    try:
        if owner_phone:
            phone = normalize_ke_phone(owner_phone)
            if phone:
                nudge = (
                    f'Duka Mwecheche: Your daily summary for {business.name} '
                    f'is ready. Check your email for the full report.'
                )
                send_sms_notification(nudge, phone)
    except Exception as e:
        logger.error('Daily summary SMS nudge failed: %s', e)

    # Also send reorder recommendations as part of daily summary
    try:
        notify_reorder_recommendations(business)
    except Exception:
        logger.exception("notify_reorder_recommendations failed during daily summary")


def notify_reorder_recommendations(business, max_items=20, create_draft=False):
    """Compute recommended order quantities and notify the business owner.

    Optionally creates a draft PurchaseOrder when `create_draft` is True.
    """
    from django.apps import apps

    Item = apps.get_model("core", "Item")
    PurchaseOrder = apps.get_model("core", "PurchaseOrder")
    PurchaseOrderLine = apps.get_model("core", "PurchaseOrderLine")

    owner_profile = business.users.filter(role="owner").first()
    if not owner_profile:
        return None

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    items = Item.objects.filter(business=business).order_by("material_no")
    recs = []
    for item in items:
        try:
            qty = item.recommended_order_qty()
        except Exception:
            qty = 0
        if qty and qty > 0:
            recs.append((item, qty))

    if not recs:
        return None

    # Limit list for concise notifications
    top = recs[:max_items]

    title = f"🔔 Reorder Recommendations — {business.name}"
    short_msg = ", ".join([f"{it.material_no}:{q}" for it, q in top])
    long_msg_lines = [f"Reorder recommendations for {business.name}", ""]
    for it, q in top:
        long_msg_lines.append(
            f"• {it.material_no} | {it.description} → Recommend: {q} {it.unit}"
        )
    long_msg = "\n".join(long_msg_lines)

    # In-app notification
    create_in_app_notification(owner, title, short_msg, notification_type="warning")

    # Email via router (REORDER: email only, no SMS per routing table)
    route_notification(
        NotifEvent.REORDER,
        business, owner_phone, owner_email,
        '',  # no SMS per routing table
        title, None,
        text_message=long_msg + "\n\n— Duka Mwecheche",
    )

    # Optionally create a draft PO
    created_po = None
    if create_draft:
        try:
            po = PurchaseOrder.objects.create(
                business=business, status="draft", created_by=None
            )
            for it, q in recs:
                PurchaseOrderLine.objects.create(
                    po=po, item=it, quantity_ordered=q, unit_price=it.cost_price or 0
                )
            created_po = po
            create_in_app_notification(
                owner,
                f"Draft PO-{po.id} created",
                f"A draft PO with {len(recs)} lines was created.",
                notification_type="order",
            )
        except Exception:
            logger.exception(
                "Failed to create draft PO in notify_reorder_recommendations"
            )

    return created_po


def notify_new_bid_opportunity(procurement_request):
    """Notify registered suppliers about a new procurement opportunity."""
    from core.models import SupplierApplication

    requesting_business = procurement_request.business

    # Find suppliers approved to bid for this business
    approved_suppliers = SupplierApplication.objects.filter(
        business=requesting_business, status="approved"
    ).select_related("supplier")

    if not approved_suppliers.exists():
        logger.info(
            f"No approved suppliers found for business {requesting_business.name}"
        )
        return

    subject_line = f"💼 New Procurement Opportunity — {requesting_business.name}"
    message_body = (
        f"A new procurement request has been posted on Duka Mwecheche\n\n"
        f"Business: {requesting_business.name}\n"
        f"Item: {procurement_request.item_description}\n"
        f"Quantity: {procurement_request.quantity} {procurement_request.unit}\n"
        f"Budget: KES {procurement_request.budget:,.0f}\n"
        f"Deadline: {procurement_request.deadline.strftime('%B %d, %Y') if procurement_request.deadline else 'N/A'}\n"
        f"Location: {procurement_request.location or 'N/A'}\n\n"
        f"Submit your bid on the platform to compete for this opportunity.\n\n"
        f"— Duka Mwecheche"
    )

    for app in approved_suppliers:
        supplier_business = app.supplier
        supplier_owner = supplier_business.users.filter(role="owner").first()

        if not supplier_owner:
            continue

        supplier_user = supplier_owner.user
        supplier_email = supplier_user.email
        supplier_phone = supplier_owner.phone or supplier_business.phone

        # In-app notification
        create_in_app_notification(
            supplier_user,
            "💼 New Bid Opportunity",
            f"{requesting_business.name} — {procurement_request.item_description} ({procurement_request.quantity} {procurement_request.unit})",
            notification_type="procurement",
        )

        # Email notification
        send_email_notification(
            supplier_email, subject_line, None, text_message=message_body
        )

        # SMS notification
        sms_msg = (
            f"[Duka Mwecheche] New Bid: {requesting_business.name} seeking "
            f"{procurement_request.quantity} {procurement_request.unit} of "
            f"{procurement_request.item_description}. Budget: KES {procurement_request.budget:,.0f}. "
            f"Submit your bid now!"
        )
        send_sms_notification(sms_msg, supplier_phone)


def notify_supplier_bid_received(bid):
    """Notify the requesting business when they receive a new bid."""
    requesting_business = bid.procurement.business
    owner_profile = requesting_business.users.filter(role="owner").first()

    if not owner_profile:
        logger.warning(f"No owner found for business {requesting_business.name}")
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or requesting_business.phone

    supplier_name = bid.supplier.name

    subject = (
        f"📋 New Bid Received — {supplier_name} for {bid.procurement.item_description}"
    )
    message = (
        f"You have received a new bid on Duka Mwecheche\n\n"
        f"Procurement Request: {bid.procurement.item_description}\n"
        f"Supplier: {supplier_name}\n"
        f"Bid Amount: KES {bid.amount:,.0f}\n"
        f"Delivery Timeline: {bid.delivery_timeline}\n"
        f"Proposal: {bid.proposal[:200]}...\n\n"
        f"Review and score this bid on the platform.\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification
    create_in_app_notification(
        owner,
        f"📋 New Bid from {supplier_name}",
        f"KES {bid.amount:,.0f} for {bid.procurement.quantity} {bid.procurement.unit}",
        notification_type="procurement",
    )

    # Email to owner
    send_email_notification(owner_email, subject, None, text_message=message)

    # SMS to owner
    sms_msg = (
        f"[Duka Mwecheche] New Bid: {supplier_name} bid KES {bid.amount:,.0f} "
        f"for {bid.procurement.item_description}. Review on the platform."
    )
    send_sms_notification(sms_msg, owner_phone)


def notify_supplier_bid_awarded(bid):
    """Notify the supplier when their bid is accepted."""
    supplier_business = bid.supplier
    owner_profile = supplier_business.users.filter(role="owner").first()

    if not owner_profile:
        logger.warning(f"No owner found for supplier {supplier_business.name}")
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or supplier_business.phone

    requesting_business = bid.procurement.business

    subject = f"🎉 Bid Awarded! — {requesting_business.name}"
    message = (
        f"Congratulations! Your bid has been accepted on Duka Mwecheche\n\n"
        f"Buyer: {requesting_business.name}\n"
        f"Item: {bid.procurement.item_description}\n"
        f"Quantity: {bid.procurement.quantity} {bid.procurement.unit}\n"
        f"Your Bid: KES {bid.amount:,.0f}\n"
        f"Delivery Timeline: {bid.delivery_timeline}\n\n"
        f"A purchase order will be created shortly. Check your platform for next steps.\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification
    create_in_app_notification(
        owner,
        "🎉 Bid Awarded!",
        f"Your bid to {requesting_business.name} for KES {bid.amount:,.0f} has been accepted!",
        notification_type="success",
    )

    # Email to supplier owner
    send_email_notification(owner_email, subject, None, text_message=message)

    # SMS to supplier owner
    sms_msg = (
        f"[Duka Mwecheche] 🎉 Bid Awarded! {requesting_business.name} accepted your bid "
        f"for KES {bid.amount:,.0f}. Check your account for purchase order details."
    )
    send_sms_notification(sms_msg, owner_phone)


def notify_rider_delivery_assigned(rider_profile, order):
    """Notify a rider when they are assigned a delivery order."""
    rider_user = rider_profile.user
    rider_email = rider_user.email
    rider_phone = rider_profile.phone

    business = order.business

    subject = f"🚚 New Delivery Assigned — Order #{order.order_number}"
    message = (
        f"You have been assigned a new delivery on Duka Mwecheche\n\n"
        f"Order Number: {order.order_number}\n"
        f"From: {business.name}\n"
        f"Customer: {order.customer_name} ({order.customer_phone})\n"
        f"Delivery Location: {order.customer_location}\n"
        f"Items: {', '.join(f'{ol.item.description} x{ol.quantity}' for ol in order.lines.all())}\n"
        f"Order Total: KES {order.total_amount:,.0f}\n"
        f"Delivery Fee: KES {order.delivery_fee:,.0f}\n"
        f"Status: {order.get_status_display()}\n\n"
        f"Accept this delivery on the app and proceed to pickup.\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification
    create_in_app_notification(
        rider_user,
        "🚚 Delivery Assigned",
        f"Order #{order.order_number} — {order.customer_name} in {order.customer_location}",
        notification_type="delivery",
    )

    # Email to rider
    send_email_notification(rider_email, subject, None, text_message=message)

    # SMS to rider
    sms_msg = (
        f"[Duka Mwecheche] New Delivery: Order #{order.order_number} from {business.name} "
        f"to {order.customer_name}. Fee: KES {order.delivery_fee:,.0f}. Accept in app."
    )
    send_sms_notification(sms_msg, rider_phone)


def notify_business_rider_assigned(order, rider_profile):
    """Notify the business owner when a rider is assigned to their order."""
    business = order.business
    owner_profile = business.users.filter(role="owner").first()

    if not owner_profile:
        logger.warning(f"No owner found for business {business.name}")
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    rider_name = rider_profile.user.get_full_name() or rider_profile.user.username
    rider_phone = rider_profile.phone
    rider_vehicle = (
        rider_profile.get_vehicle_type_display()
        if hasattr(rider_profile, "get_vehicle_type_display")
        else rider_profile.vehicle_type
    )

    subject = f"🚗 Rider Assigned — Order #{order.order_number}"
    message = (
        f"A rider has been assigned to your order on Duka Mwecheche\n\n"
        f"Order: #{order.order_number}\n"
        f"Customer: {order.customer_name}\n"
        f"Location: {order.customer_location}\n"
        f"Rider: {rider_name}\n"
        f"Rider Phone: {rider_phone}\n"
        f"Vehicle: {rider_vehicle}\n"
        f"Delivery Fee: KES {order.delivery_fee:,.0f}\n\n"
        f"The rider will contact the customer shortly for pickup/delivery details.\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification
    create_in_app_notification(
        owner,
        "🚗 Rider Assigned",
        f"Order #{order.order_number} — {rider_name} ({rider_phone})",
        notification_type="delivery",
    )

    # Email to owner
    send_email_notification(owner_email, subject, None, text_message=message)

    # SMS to owner
    sms_msg = (
        f"[Duka Mwecheche] Rider Assigned: Order #{order.order_number} "
        f"— {rider_name} ({rider_phone}) will collect from your store."
    )
    send_sms_notification(sms_msg, owner_phone)
