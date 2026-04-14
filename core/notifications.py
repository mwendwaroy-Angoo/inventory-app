import logging
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings

logger = logging.getLogger(__name__)


def send_email_notification(subject, message, recipient_email, html_message=None):
    """Send an email; supports plain-text and optional HTML alternative.

    Args:
        subject: Email subject
        message: Plain-text body
        recipient_email: recipient address
        html_message: optional HTML body (string)
    """
    if not recipient_email:
        return False
    try:
        if html_message:
            msg = EmailMultiAlternatives(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient_email])
            msg.attach_alternative(html_message, "text/html")
            msg.send(fail_silently=True)
        else:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                fail_silently=True,
            )
        logger.info(f"Email sent to {recipient_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email failed to {recipient_email}: {e}")
        return False


def send_sms_notification(message, phone_number):
    if not phone_number:
        return False
    try:
        import africastalking
        africastalking.initialize(
            username=settings.AT_USERNAME,
            api_key=settings.AT_API_KEY
        )
        sms = africastalking.SMS
        if phone_number.startswith('0'):
            phone_number = '+254' + phone_number[1:]
        elif not phone_number.startswith('+'):
            phone_number = '+254' + phone_number
        response = sms.send(message, [phone_number])
        logger.info(f"SMS sent to {phone_number}: {response}")
        return True
    except Exception as e:
        logger.error(f"SMS failed to {phone_number}: {e}")
        return False


def send_whatsapp_notification(message, phone_number):
    if not phone_number:
        return False
    try:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        if phone_number.startswith('0'):
            phone_number = '+254' + phone_number[1:]
        elif not phone_number.startswith('+'):
            phone_number = '+254' + phone_number
        client.messages.create(
            from_='whatsapp:+14155238886',
            to=f'whatsapp:{phone_number}',
            body=message
        )
        logger.info(f"WhatsApp sent to {phone_number}")
        return True
    except Exception as e:
        logger.error(f"WhatsApp failed to {phone_number}: {e}")
        return False


def create_in_app_notification(user, title, message, notification_type='info'):
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
    recorded_by = (user.get_full_name() or user.username) if user else 'N/A'

    owner_profile = business.users.filter(role='owner').first()
    if not owner_profile:
        logger.warning(f"No owner found for business {business.name}")
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    if trans_type == 'Issue':
        emoji = '📤'
        action = 'issued/sold'
    else:
        emoji = '📥'
        action = 'received'

    subject = f"{emoji} Transaction Alert — {business.name}"
    message = (
        f"Transaction recorded on Duka Mwecheche\n\n"
        f"Business: {business.name}\n"
        f"Item: {item.description} ({item.material_no})\n"
        f"Type: {trans_type}\n"
        f"Quantity: {qty} {item.unit} {action}\n"
        f"Remaining Balance: {item.current_balance()} {item.unit}\n"
        f"Recipient: {transaction.recipient or 'N/A'}\n"
        f"Invoice No: {transaction.invoice_no or 'N/A'}\n"
        f"Recorded by: {recorded_by}\n"
        f"Date: {transaction.date}\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification always
    create_in_app_notification(
        owner,
        f"{emoji} {trans_type}: {item.description}",
        f"{qty} {item.unit} {action}. Balance: {item.current_balance()}. By: {recorded_by}",
        notification_type='transaction'
    )

    # Email always
    send_email_notification(subject, message, owner_email)

    # SMS if ≤15 transactions today, WhatsApp if >15
    if daily_count <= 15:
        sms_msg = (
            f"[Duka Mwecheche] {trans_type}: {qty} {item.unit} "
            f"of {item.description}. "
            f"Balance: {item.current_balance()}. "
            f"By: {recorded_by}"
        )
        send_sms_notification(sms_msg, owner_phone)
    else:
        wa_msg = (
            f"*Duka Mwecheche — Transaction Alert*\n\n"
            f"*{trans_type}:* {qty} {item.unit} of {item.description}\n"
            f"*Balance:* {item.current_balance()} {item.unit}\n"
            f"*Recipient:* {transaction.recipient or 'N/A'}\n"
            f"*Recorded by:* {recorded_by}\n"
            f"*Invoice:* {transaction.invoice_no or 'N/A'}"
        )
        send_whatsapp_notification(wa_msg, owner_phone)

    if item.needs_reorder():
        notify_reorder_alert(item, business, owner, owner_email, owner_phone)


def notify_reorder_alert(item, business, owner, owner_email, owner_phone):
    subject = f"⚠️ Low Stock Alert — {item.description}"
    message = (
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
        notification_type='warning'
    )
    send_email_notification(subject, message, owner_email)
    send_sms_notification(
        f"[Duka Mwecheche] LOW STOCK: {item.description}. "
        f"Balance: {item.current_balance()} {item.unit}. Please reorder.",
        owner_phone
    )


def notify_staff_login(user, business, action='logged in'):
    owner_profile = business.users.filter(role='owner').first()
    if not owner_profile:
        return

    owner = owner_profile.user

    from django.utils import timezone
    now = timezone.localtime(timezone.now()).strftime("%B %d, %Y at %I:%M %p")

    emoji = '🟢' if action == 'logged in' else '🔴'
    # Only in-app notification during login/logout — email/SMS would block
    # the request and cause worker timeouts on Render's free tier
    create_in_app_notification(
        owner,
        f"{emoji} {user.username} {action}",
        f"Staff member {action} at {now}",
        notification_type='staff'
    )


def notify_new_order(order):
    """Notify the business owner (and staff) about a new customer order."""
    business = order.business
    owner_profile = business.users.filter(role='owner').first()
    if not owner_profile:
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    delivery_label = '🚚 Delivery' if order.delivery_mode == 'delivery' else '🏪 Pickup'
    pay_labels = {'mpesa': '💳 M-Pesa', 'cash': '💵 Cash', 'pickup_pay': '🏪 Pay at Pickup'}
    pay_label = pay_labels.get(order.payment_method, order.payment_method)

    items_text = ', '.join(
        f"{l.item.description} x{l.quantity}" for l in order.lines.select_related('item')
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
        notification_type='order'
    )

    # In-app notification for all staff
    for staff_profile in business.users.filter(role='staff'):
        create_in_app_notification(
            staff_profile.user,
            f"🛒 New Order #{order.order_number}",
            f"{order.customer_name} — KES {order.total_amount:,.0f} ({delivery_label})",
            notification_type='order'
        )

    # Email to owner
    send_email_notification(subject, message, owner_email)

    # SMS to owner
    sms_msg = (
        f"[Duka Mwecheche] New Order #{order.order_number}: "
        f"{order.customer_name}, KES {order.total_amount:,.0f}. "
        f"{delivery_label}."
    )
    send_sms_notification(sms_msg, owner_phone)


def send_daily_summary(business):
    from datetime import date
    from core.models import Transaction

    owner_profile = business.users.filter(role='owner').first()
    if not owner_profile:
        return

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    today = date.today()
    sales = Transaction.objects.filter(
        business=business,
        type='Issue',
        date=today,
    ).select_related('item')

    total_revenue = sum(t.revenue() for t in sales)
    total_cost = sum(t.cost() for t in sales)
    total_profit = total_revenue - total_cost
    total_transactions = sales.count()

    receipts = Transaction.objects.filter(
        business=business,
        type='Receipt',
        date=today,
    ).count()

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

    from collections import defaultdict
    item_sales = defaultdict(int)
    for t in sales:
        item_sales[t.item.description] += abs(t.qty)

    top = sorted(item_sales.items(), key=lambda x: x[1], reverse=True)[:5]
    for item_name, qty in top:
        message += f"• {item_name}: {qty} units\n"

    message += f"\n{'='*40}\n"
    message += "View full report at: https://stock-made-simpler-sms.onrender.com/sales/\n\n"
    message += "— Duka Mwecheche"

    send_email_notification(subject, message, owner_email)

    wa_msg = (
        f"*📊 Daily Summary — {business.name}*\n"
        f"*Date:* {today}\n\n"
        f"*Revenue:* KES {total_revenue:,.0f}\n"
        f"*Cost:* KES {total_cost:,.0f}\n"
        f"*Profit:* KES {total_profit:,.0f}\n"
        f"*Transactions:* {total_transactions} sales\n\n"
        f"View full report: https://stock-made-simpler-sms.onrender.com/sales/"
    )
    send_whatsapp_notification(wa_msg, owner_phone)

    # Also send reorder recommendations as part of daily summary
    try:
        notify_reorder_recommendations(business)
    except Exception:
        logger.exception('notify_reorder_recommendations failed during daily summary')


def notify_reorder_recommendations(business, max_items=20, create_draft=False):
    """Compute recommended order quantities and notify the business owner.

    Optionally creates a draft PurchaseOrder when `create_draft` is True.
    """
    from django.apps import apps
    Item = apps.get_model('core', 'Item')
    PurchaseOrder = apps.get_model('core', 'PurchaseOrder')
    PurchaseOrderLine = apps.get_model('core', 'PurchaseOrderLine')

    owner_profile = business.users.filter(role='owner').first()
    if not owner_profile:
        return None

    owner = owner_profile.user
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    items = Item.objects.filter(business=business).order_by('material_no')
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
    short_msg = ', '.join([f"{it.material_no}:{q}" for it, q in top])
    long_msg_lines = [f"Reorder recommendations for {business.name}", '']
    for it, q in top:
        long_msg_lines.append(f"• {it.material_no} | {it.description} → Recommend: {q} {it.unit}")
    long_msg = '\n'.join(long_msg_lines)

    # In-app notification
    create_in_app_notification(owner, title, short_msg, notification_type='warning')

    # Email notification
    send_email_notification(title, long_msg + "\n\n— Duka Mwecheche", owner_email)

    # Optionally create a draft PO
    created_po = None
    if create_draft:
        try:
            po = PurchaseOrder.objects.create(business=business, status='draft', created_by=None)
            for it, q in recs:
                PurchaseOrderLine.objects.create(po=po, item=it, quantity_ordered=q, unit_price=it.cost_price or 0)
            created_po = po
            create_in_app_notification(owner, f"Draft PO-{po.id} created", f"A draft PO with {len(recs)} lines was created.", notification_type='order')
        except Exception:
            logger.exception('Failed to create draft PO in notify_reorder_recommendations')

    return created_po