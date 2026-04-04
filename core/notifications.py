import os
import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def send_email_notification(subject, message, recipient_email):
    if not recipient_email:
        return False
    try:
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


def notify_transaction(transaction, business, daily_count=0):
    item = transaction.item
    trans_type = transaction.type
    qty = abs(transaction.qty)

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
        f"Date: {transaction.date}\n\n"
        f"— Duka Mwecheche"
    )

    # In-app notification always
    create_in_app_notification(
        owner,
        f"{emoji} {trans_type}: {item.description}",
        f"{qty} {item.unit} {action}. Balance: {item.current_balance()}",
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
            f"By: {transaction.recipient or 'N/A'}"
        )
        send_sms_notification(sms_msg, owner_phone)
    else:
        wa_msg = (
            f"*Duka Mwecheche — Transaction Alert*\n\n"
            f"*{trans_type}:* {qty} {item.unit} of {item.description}\n"
            f"*Balance:* {item.current_balance()} {item.unit}\n"
            f"*Recipient:* {transaction.recipient or 'N/A'}\n"
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
    owner_email = owner.email
    owner_phone = owner_profile.phone or business.phone

    from django.utils import timezone
    now = timezone.localtime(timezone.now()).strftime("%B %d, %Y at %I:%M %p")

    emoji = '🟢' if action == 'logged in' else '🔴'
    subject = f"{emoji} Staff {action.title()} — {business.name}"
    message = (
        f"Staff activity on Duka Mwecheche\n\n"
        f"Staff member: {user.get_full_name() or user.username}\n"
        f"Action: {action.title()}\n"
        f"Time: {now}\n"
        f"Business: {business.name}\n\n"
        f"— Duka Mwecheche"
    )
    # Only in-app notification during login/logout — email/SMS would block
    # the request and cause worker timeouts on Render's free tier
    create_in_app_notification(
        owner,
        f"{emoji} {user.username} {action}",
        f"Staff member {action} at {now}",
        notification_type='staff'
    )


def send_daily_summary(business):
    from datetime import date
    from core.models import Transaction, Item

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
    message += f"View full report at: https://stock-made-simpler-sms.onrender.com/sales/\n\n"
    message += f"— Duka Mwecheche"

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