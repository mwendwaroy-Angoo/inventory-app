"""
WhatsApp bot via Twilio webhook.

Customers can text a business WhatsApp number to:
  - Browse items  (text: SHOP <business_name>)
  - View cart     (text: CART)
  - Place order   (text: ORDER)
  - Track order   (text: TRACK <order_number>)
  - Get help      (text: HELP or HI)

Business owners/staff receive:
  - Order notifications are sent automatically via core.notifications

Twilio webhook: POST /whatsapp/webhook/
Configure in Twilio console: When a message comes in → <your_domain>/whatsapp/webhook/
"""

import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Business, UserProfile
from core.models import Item, Order, OrderLine
from core.mpesa import format_phone_ke

logger = logging.getLogger(__name__)

# In-memory session store (per-phone).
# In production with multiple workers, use Django cache or DB.
# Key: phone number, Value: dict with state info
_sessions = {}


def _twiml_reply(message):
    """Return a TwiML response with a text message."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response><Message>' + _escape_xml(message) + '</Message></Response>'
    )
    return HttpResponse(xml, content_type='text/xml')


def _escape_xml(text):
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _normalize_phone(phone):
    phone = phone.strip().replace('whatsapp:', '')
    if phone.startswith('0'):
        return '+254' + phone[1:]
    if phone.startswith('254') and not phone.startswith('+'):
        return '+' + phone
    return phone


def _get_session(phone):
    return _sessions.get(phone, {})


def _set_session(phone, data):
    _sessions[phone] = data


def _clear_session(phone):
    _sessions.pop(phone, None)


@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """Handle incoming WhatsApp messages from Twilio."""
    from_number = request.POST.get('From', '')
    body = request.POST.get('Body', '').strip()
    phone = _normalize_phone(from_number)

    if not body:
        return _twiml_reply("Welcome to Duka Mwecheche! Send *HELP* to see options.")

    cmd = body.upper().split()
    keyword = cmd[0] if cmd else ''

    session = _get_session(phone)

    # ── State machine: if we're in an active flow ──
    if session.get('state'):
        return _handle_state(phone, body, session)

    # ── Top-level commands ──
    if keyword in ('HI', 'HELLO', 'HELP', 'MENU'):
        return _cmd_help(phone)

    if keyword == 'SHOP':
        query = ' '.join(cmd[1:]) if len(cmd) > 1 else ''
        return _cmd_shop(phone, query)

    if keyword == 'BROWSE':
        return _cmd_browse(phone, cmd)

    if keyword == 'ADD':
        return _cmd_add(phone, cmd)

    if keyword == 'CART':
        return _cmd_cart(phone)

    if keyword == 'ORDER':
        return _cmd_order_start(phone)

    if keyword == 'TRACK':
        order_num = cmd[1] if len(cmd) > 1 else ''
        return _cmd_track(phone, order_num)

    if keyword == 'CLEAR':
        return _cmd_clear(phone)

    # Unknown — show help
    return _cmd_help(phone)


# ────────────────────────────────────────────────────────────
# COMMANDS
# ────────────────────────────────────────────────────────────

def _cmd_help(phone):
    _clear_session(phone)
    msg = (
        "*🏪 Duka Mwecheche — WhatsApp Ordering*\n\n"
        "Send any of these commands:\n\n"
        "*SHOP* — Browse businesses\n"
        "*SHOP <name>* — Search businesses\n"
        "*BROWSE <number>* — View a business's items\n"
        "*ADD <item_no> <qty>* — Add to cart\n"
        "*CART* — View your cart\n"
        "*CLEAR* — Clear your cart\n"
        "*ORDER* — Place your order\n"
        "*TRACK <order_no>* — Track an order\n"
        "*HELP* — Show this menu"
    )
    return _twiml_reply(msg)


def _cmd_shop(phone, query):
    """List businesses, optionally filtered by name."""
    businesses = Business.objects.all()

    # Only businesses with priced items
    from core.models import Item as ItemModel
    biz_ids = ItemModel.objects.filter(
        selling_price__isnull=False
    ).values_list('business_id', flat=True).distinct()
    businesses = businesses.filter(id__in=biz_ids)

    if query:
        businesses = businesses.filter(name__icontains=query)

    businesses = list(businesses[:10])

    if not businesses:
        return _twiml_reply(
            "No businesses found. Try *SHOP* to see all, or *SHOP <name>* to search."
        )

    lines = ["*🏪 Businesses on Duka Mwecheche*\n"]
    for i, biz in enumerate(businesses, 1):
        county = biz.county.name if biz.county else ''
        lines.append(f"{i}. *{biz.name}*" + (f" — {county}" if county else ''))

    lines.append(f"\nSend *BROWSE <number>* to view items.\nE.g. _BROWSE 1_")

    # Store business list in session for BROWSE
    _set_session(phone, {
        'businesses': [b.id for b in businesses],
    })

    return _twiml_reply('\n'.join(lines))


def _cmd_browse(phone, cmd):
    """View a business's items."""
    session = _get_session(phone)
    businesses = session.get('businesses', [])

    try:
        idx = int(cmd[1]) - 1 if len(cmd) > 1 else -1
    except (ValueError, IndexError):
        return _twiml_reply("Send *BROWSE <number>*. E.g. _BROWSE 1_")

    if idx < 0 or idx >= len(businesses):
        return _twiml_reply("Invalid selection. Send *SHOP* to see the list again.")

    biz_id = businesses[idx]
    try:
        business = Business.objects.get(id=biz_id)
    except Business.DoesNotExist:
        return _twiml_reply("Business not found.")

    items = Item.objects.filter(
        business=business,
        selling_price__isnull=False,
    ).order_by('description')

    available = [i for i in items if i.current_balance() > 0][:15]

    if not available:
        return _twiml_reply(f"*{business.name}* has no items available right now.")

    lines = [f"*📦 {business.name} — Items*\n"]
    item_ids = []
    for i, item in enumerate(available, 1):
        lines.append(
            f"{i}. {item.description} — KES {item.selling_price:,.0f} "
            f"({item.current_balance()} {item.unit})"
        )
        item_ids.append(item.id)

    lines.append(f"\nSend *ADD <item_no> <qty>* to add to cart.\nE.g. _ADD 1 2_")

    session['browse_biz'] = biz_id
    session['browse_items'] = item_ids
    _set_session(phone, session)

    return _twiml_reply('\n'.join(lines))


def _cmd_add(phone, cmd):
    """Add item to cart."""
    session = _get_session(phone)
    item_ids = session.get('browse_items', [])
    biz_id = session.get('browse_biz')

    if not item_ids or not biz_id:
        return _twiml_reply("Please *SHOP* and *BROWSE* a business first.")

    try:
        idx = int(cmd[1]) - 1 if len(cmd) > 1 else -1
        qty = int(cmd[2]) if len(cmd) > 2 else 1
    except (ValueError, IndexError):
        return _twiml_reply("Send *ADD <item_no> <qty>*. E.g. _ADD 1 2_")

    if idx < 0 or idx >= len(item_ids):
        return _twiml_reply("Invalid item number.")

    if qty < 1:
        return _twiml_reply("Quantity must be at least 1.")

    item_id = item_ids[idx]
    try:
        item = Item.objects.get(id=item_id)
    except Item.DoesNotExist:
        return _twiml_reply("Item not found.")

    if item.current_balance() < qty:
        return _twiml_reply(
            f"Not enough stock for {item.description}. "
            f"Available: {item.current_balance()} {item.unit}"
        )

    # Add to cart in session
    cart = session.get('cart', [])
    # Check if already in cart
    for entry in cart:
        if entry['item_id'] == item_id:
            entry['qty'] += qty
            break
    else:
        cart.append({
            'item_id': item_id,
            'name': item.description,
            'qty': qty,
            'price': float(item.selling_price),
        })

    session['cart'] = cart
    session['cart_biz'] = biz_id
    _set_session(phone, session)

    total = sum(e['qty'] * e['price'] for e in cart)
    return _twiml_reply(
        f"✅ Added {qty}x {item.description} to cart.\n\n"
        f"Cart: {len(cart)} item(s) · KES {total:,.0f}\n"
        f"Send *CART* to view, *ORDER* to checkout, or *ADD* more."
    )


def _cmd_cart(phone):
    """Show cart contents."""
    session = _get_session(phone)
    cart = session.get('cart', [])

    if not cart:
        return _twiml_reply("Your cart is empty. Send *SHOP* to browse.")

    lines = ["*🛒 Your Cart*\n"]
    total = 0
    for i, entry in enumerate(cart, 1):
        subtotal = entry['qty'] * entry['price']
        total += subtotal
        lines.append(f"{i}. {entry['name']} x{entry['qty']} — KES {subtotal:,.0f}")

    lines.append(f"\n*Total: KES {total:,.0f}*")
    lines.append("\nSend *ORDER* to place order\nSend *CLEAR* to empty cart")

    return _twiml_reply('\n'.join(lines))


def _cmd_clear(phone):
    """Clear cart."""
    session = _get_session(phone)
    session.pop('cart', None)
    session.pop('cart_biz', None)
    _set_session(phone, session)
    return _twiml_reply("🗑️ Cart cleared. Send *SHOP* to browse again.")


def _cmd_order_start(phone):
    """Start order flow — ask for name."""
    session = _get_session(phone)
    cart = session.get('cart', [])

    if not cart:
        return _twiml_reply("Your cart is empty. Send *SHOP* to browse.")

    session['state'] = 'order_name'
    _set_session(phone, session)

    return _twiml_reply("📋 *Placing your order*\n\nPlease send your *full name*:")


def _cmd_track(phone, order_number):
    """Track an order by number."""
    if not order_number:
        return _twiml_reply("Send *TRACK <order_number>*. E.g. _TRACK ORD-260404-A1B2_")

    try:
        order = Order.objects.get(order_number=order_number.upper())
    except Order.DoesNotExist:
        return _twiml_reply(f"Order {order_number} not found. Check the number and try again.")

    lines = order.lines.select_related('item').all()
    status_emoji = {
        'pending': '⏳', 'confirmed': '✅', 'paid': '💰',
        'ready': '📦', 'completed': '🎉', 'cancelled': '❌',
    }
    emoji = status_emoji.get(order.status, '❓')

    msg_lines = [
        f"*📦 Order {order.order_number}*\n",
        f"Status: {emoji} *{order.get_status_display()}*",
        f"Total: KES {order.total_amount:,.0f}\n",
        "*Items:*",
    ]
    for line in lines:
        msg_lines.append(f"  • {line.item.description} x{line.quantity}")

    if order.status == 'pending':
        msg_lines.append(
            f"\n💳 Pay via M-Pesa at:\n"
            f"https://your-domain/shop/order/{order.order_number}/"
        )

    return _twiml_reply('\n'.join(msg_lines))


# ────────────────────────────────────────────────────────────
# STATE HANDLERS (multi-step flows)
# ────────────────────────────────────────────────────────────

def _handle_state(phone, body, session):
    state = session.get('state')

    if state == 'order_name':
        session['customer_name'] = body.strip()
        session['state'] = 'order_location'
        _set_session(phone, session)
        return _twiml_reply(
            f"Thanks, *{session['customer_name']}*!\n\n"
            "Send your *location/area* for delivery (or send *SKIP*):"
        )

    if state == 'order_location':
        location = '' if body.upper() == 'SKIP' else body.strip()
        session['customer_location'] = location
        session['state'] = 'order_confirm'
        _set_session(phone, session)

        cart = session.get('cart', [])
        total = sum(e['qty'] * e['price'] for e in cart)

        msg_lines = [
            "*📋 Confirm your order:*\n",
            f"Name: {session['customer_name']}",
            f"Phone: {phone}",
        ]
        if location:
            msg_lines.append(f"Location: {location}")
        msg_lines.append(f"\n*Items:*")
        for entry in cart:
            msg_lines.append(f"  • {entry['name']} x{entry['qty']}")
        msg_lines.append(f"\n*Total: KES {total:,.0f}*")
        msg_lines.append("\nSend *YES* to confirm or *NO* to cancel.")

        return _twiml_reply('\n'.join(msg_lines))

    if state == 'order_confirm':
        if body.upper() in ('YES', 'Y', 'CONFIRM'):
            return _finalize_order(phone, session)
        else:
            session.pop('state', None)
            _set_session(phone, session)
            return _twiml_reply(
                "Order cancelled. Your cart is still saved.\n"
                "Send *ORDER* to try again or *CLEAR* to empty cart."
            )

    # Unknown state — reset
    session.pop('state', None)
    _set_session(phone, session)
    return _cmd_help(phone)


def _finalize_order(phone, session):
    """Create the order in the database."""
    cart = session.get('cart', [])
    biz_id = session.get('cart_biz')

    if not cart or not biz_id:
        _clear_session(phone)
        return _twiml_reply("Something went wrong. Please start again with *SHOP*.")

    try:
        business = Business.objects.get(id=biz_id)
    except Business.DoesNotExist:
        _clear_session(phone)
        return _twiml_reply("Business not found. Please start again with *SHOP*.")

    # Validate stock
    order_items = []
    for entry in cart:
        try:
            item = Item.objects.get(id=entry['item_id'])
        except Item.DoesNotExist:
            continue
        if item.current_balance() < entry['qty']:
            _clear_session(phone)
            return _twiml_reply(
                f"Sorry, {item.description} no longer has enough stock. "
                f"Available: {item.current_balance()}. Please start again."
            )
        order_items.append((item, entry['qty']))

    if not order_items:
        _clear_session(phone)
        return _twiml_reply("No valid items in cart. Please start again with *SHOP*.")

    # Create order
    order = Order.objects.create(
        business=business,
        customer_name=session.get('customer_name', 'WhatsApp Customer'),
        customer_phone=format_phone_ke(phone),
        customer_location=session.get('customer_location', ''),
        notes='Ordered via WhatsApp',
    )

    for item, qty in order_items:
        OrderLine.objects.create(
            order=order,
            item=item,
            quantity=qty,
            unit_price=item.selling_price,
        )

    order.recalculate_total()

    # Notify business owner
    try:
        from core.notifications import create_in_app_notification
        owner_profile = business.users.filter(role='owner').first()
        if owner_profile:
            create_in_app_notification(
                owner_profile.user,
                f"📱 New WhatsApp Order #{order.order_number}",
                f"{session.get('customer_name', 'Customer')} ordered "
                f"{len(order_items)} item(s) worth KES {order.total_amount:,.0f}",
                notification_type='transaction',
            )
    except Exception as e:
        logger.error(f"WhatsApp order notification error: {e}")

    # Clear session
    _clear_session(phone)

    return _twiml_reply(
        f"🎉 *Order placed!*\n\n"
        f"Order #: *{order.order_number}*\n"
        f"Total: KES {order.total_amount:,.0f}\n\n"
        f"Track: send *TRACK {order.order_number}*\n"
        f"Pay via M-Pesa on the tracking page.\n\n"
        f"Thank you for shopping with {business.name}!"
    )
