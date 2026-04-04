"""
Customer-facing USSD ordering via Africa's Talking.

Endpoint: POST /ussd/customer/

Flow:
  Main menu  → 1. Browse Businesses  → Select Business → Select Item → Enter Qty → Confirm
             → 2. Track Order         → Enter Order No → Show Status
             → 3. My Orders           → Show recent orders

Unlike the owner USSD (core/ussd.py) which is matched by phone to a business,
this is a generic customer-facing USSD where any phone can browse and order.
"""

import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Business
from core.models import Item, Order, OrderLine
from core.mpesa import format_phone_ke

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5
BIZ_PER_PAGE = 5


def _normalize_phone(phone):
    phone = phone.strip()
    if phone.startswith('0'):
        return '+254' + phone[1:]
    if phone.startswith('254') and not phone.startswith('+'):
        return '+' + phone
    return phone


@csrf_exempt
@require_POST
def customer_ussd_callback(request):
    """Handle customer USSD callback from Africa's Talking."""
    session_id = request.POST.get('sessionId', '')
    phone_number = request.POST.get('phoneNumber', '')
    text = request.POST.get('text', '').strip()

    phone = _normalize_phone(phone_number)
    parts = text.split('*') if text else []
    level = len(parts)

    # ── LEVEL 0: Main menu ──
    if level == 0:
        return HttpResponse(
            "CON 🏪 Duka Mwecheche — Customer\n\n"
            "1. Browse Businesses\n"
            "2. Track Order\n"
            "3. My Recent Orders"
        )

    action = parts[0]

    # ═══════════════════════════════════════════
    # 1. BROWSE BUSINESSES
    # ═══════════════════════════════════════════
    if action == '1':
        return _handle_browse(phone, parts, level)

    # ═══════════════════════════════════════════
    # 2. TRACK ORDER
    # ═══════════════════════════════════════════
    if action == '2':
        return _handle_track(phone, parts, level)

    # ═══════════════════════════════════════════
    # 3. MY RECENT ORDERS
    # ═══════════════════════════════════════════
    if action == '3':
        return _handle_my_orders(phone, parts, level)

    return HttpResponse("END Invalid option. Dial again.")


# ────────────────────────────────────────────────────
# 1. BROWSE → SELECT BIZ → SELECT ITEM → QTY → CONFIRM
# ────────────────────────────────────────────────────

def _get_businesses(page=1):
    """Get businesses that have priced items."""
    biz_ids = Item.objects.filter(
        selling_price__isnull=False,
    ).values_list('business_id', flat=True).distinct()

    businesses = Business.objects.filter(id__in=biz_ids).order_by('name')
    total = businesses.count()
    start = (page - 1) * BIZ_PER_PAGE
    end = start + BIZ_PER_PAGE
    page_items = list(businesses[start:end])
    has_more = end < total
    return page_items, has_more


def _get_biz_items(business, page=1):
    """Get available items for a business."""
    items = Item.objects.filter(
        business=business,
        selling_price__isnull=False,
    ).order_by('description')

    all_items = [i for i in items if i.current_balance() > 0]
    total = len(all_items)
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = all_items[start:end]
    has_more = end < total
    return page_items, has_more


def _handle_browse(phone, parts, level):
    # Level 1: List businesses
    if level == 1:
        businesses, has_more = _get_businesses(page=1)
        if not businesses:
            return HttpResponse("END No businesses available at the moment.")
        lines = ["CON SELECT BUSINESS:\n"]
        for i, biz in enumerate(businesses, 1):
            county = f" ({biz.county.name})" if biz.county else ""
            lines.append(f"{i}. {biz.name}{county}")
        if has_more:
            lines.append(f"{len(businesses) + 1}. More >>")
        lines.append("0. Back")
        return HttpResponse("\n".join(lines))

    # Level 2: Business selected → show items
    if level == 2:
        choice = parts[1]
        if choice == '0':
            return HttpResponse(
                "CON 🏪 Duka Mwecheche — Customer\n\n"
                "1. Browse Businesses\n"
                "2. Track Order\n"
                "3. My Recent Orders"
            )

        try:
            idx = int(choice)
        except ValueError:
            return HttpResponse("END Invalid selection.")

        businesses, has_more = _get_businesses(page=1)

        # Pagination
        if has_more and idx == len(businesses) + 1:
            businesses2, has_more2 = _get_businesses(page=2)
            lines = ["CON SELECT BUSINESS:\n"]
            for i, biz in enumerate(businesses2, 1):
                county = f" ({biz.county.name})" if biz.county else ""
                lines.append(f"{i}. {biz.name}{county}")
            if has_more2:
                lines.append(f"{len(businesses2) + 1}. More >>")
            lines.append("0. Back")
            return HttpResponse("\n".join(lines))

        if idx < 1 or idx > len(businesses):
            return HttpResponse("END Invalid selection.")

        business = businesses[idx - 1]
        items, has_more_items = _get_biz_items(business, page=1)

        if not items:
            return HttpResponse(f"END {business.name} has no items available.")

        lines = [f"CON {business.name} — Items:\n"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. {item.description} KES{item.selling_price:,.0f}"
            )
        if has_more_items:
            lines.append(f"{len(items) + 1}. More >>")
        lines.append("0. Back")
        return HttpResponse("\n".join(lines))

    # Level 3: Item selected → ask qty
    if level == 3:
        try:
            biz_idx = int(parts[1])
            item_choice = parts[2]
        except (ValueError, IndexError):
            return HttpResponse("END Invalid selection.")

        if item_choice == '0':
            # Go back to business list
            businesses, has_more = _get_businesses(page=1)
            lines = ["CON SELECT BUSINESS:\n"]
            for i, biz in enumerate(businesses, 1):
                lines.append(f"{i}. {biz.name}")
            if has_more:
                lines.append(f"{len(businesses) + 1}. More >>")
            lines.append("0. Back")
            return HttpResponse("\n".join(lines))

        businesses, _ = _get_businesses(page=1)
        if biz_idx < 1 or biz_idx > len(businesses):
            return HttpResponse("END Invalid business.")

        business = businesses[biz_idx - 1]
        items, has_more_items = _get_biz_items(business, page=1)

        try:
            item_idx = int(item_choice)
        except ValueError:
            return HttpResponse("END Invalid selection.")

        # Pagination for items
        if has_more_items and item_idx == len(items) + 1:
            items2, has_more2 = _get_biz_items(business, page=2)
            lines = [f"CON {business.name} — Items:\n"]
            for i, item in enumerate(items2, 1):
                lines.append(f"{i}. {item.description} KES{item.selling_price:,.0f}")
            if has_more2:
                lines.append(f"{len(items2) + 1}. More >>")
            lines.append("0. Back")
            return HttpResponse("\n".join(lines))

        if item_idx < 1 or item_idx > len(items):
            return HttpResponse("END Invalid item.")

        item = items[item_idx - 1]
        return HttpResponse(
            f"CON {item.description}\n"
            f"Price: KES {item.selling_price:,.0f}\n"
            f"Available: {item.current_balance()} {item.unit}\n\n"
            f"Enter quantity:"
        )

    # Level 4: Qty entered → confirm
    if level == 4:
        try:
            biz_idx = int(parts[1])
            item_idx = int(parts[2])
            qty = int(parts[3])
        except (ValueError, IndexError):
            return HttpResponse("END Invalid input.")

        if qty < 1:
            return HttpResponse("END Quantity must be at least 1.")

        businesses, _ = _get_businesses(page=1)
        if biz_idx < 1 or biz_idx > len(businesses):
            return HttpResponse("END Invalid business.")

        business = businesses[biz_idx - 1]
        items, _ = _get_biz_items(business, page=1)

        if item_idx < 1 or item_idx > len(items):
            return HttpResponse("END Invalid item.")

        item = items[item_idx - 1]

        if qty > item.current_balance():
            return HttpResponse(
                f"END Not enough stock!\n"
                f"{item.description}: {item.current_balance()} available."
            )

        total = float(item.selling_price) * qty

        return HttpResponse(
            f"CON ORDER SUMMARY:\n"
            f"Business: {business.name}\n"
            f"Item: {item.description}\n"
            f"Qty: {qty}\n"
            f"Total: KES {total:,.0f}\n\n"
            f"1. Confirm Order\n"
            f"2. Cancel"
        )

    # Level 5: Confirm order
    if level == 5:
        confirm = parts[4]
        if confirm != '1':
            return HttpResponse("END Order cancelled.")

        try:
            biz_idx = int(parts[1])
            item_idx = int(parts[2])
            qty = int(parts[3])
        except (ValueError, IndexError):
            return HttpResponse("END Error processing order.")

        businesses, _ = _get_businesses(page=1)
        if biz_idx < 1 or biz_idx > len(businesses):
            return HttpResponse("END Error: business not found.")

        business = businesses[biz_idx - 1]
        items, _ = _get_biz_items(business, page=1)

        if item_idx < 1 or item_idx > len(items):
            return HttpResponse("END Error: item not found.")

        item = items[item_idx - 1]

        if qty > item.current_balance():
            return HttpResponse("END Not enough stock. Try again.")

        # Create the order
        order = Order.objects.create(
            business=business,
            customer_name=f"USSD Customer",
            customer_phone=format_phone_ke(phone),
            notes=f"Ordered via USSD ({phone})",
        )

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
                    f"📱 USSD Order #{order.order_number}",
                    f"Customer ({phone}) ordered {qty}x {item.description} "
                    f"— KES {order.total_amount:,.0f}",
                    notification_type='transaction',
                )
        except Exception as e:
            logger.error(f"USSD order notification error: {e}")

        return HttpResponse(
            f"END Order placed!\n\n"
            f"Order #: {order.order_number}\n"
            f"Total: KES {order.total_amount:,.0f}\n\n"
            f"Pay via M-Pesa or track at:\n"
            f"dukamwecheche.com/shop/order/{order.order_number}/\n\n"
            f"Thank you!"
        )

    return HttpResponse("END Session expired. Dial again.")


# ────────────────────────────────────────────────────
# 2. TRACK ORDER
# ────────────────────────────────────────────────────

def _handle_track(phone, parts, level):
    if level == 1:
        return HttpResponse("CON Enter your order number:\n(e.g. ORD-260404-A1B2)")

    if level == 2:
        order_num = parts[1].strip().upper()
        try:
            order = Order.objects.get(order_number=order_num)
        except Order.DoesNotExist:
            return HttpResponse(f"END Order {order_num} not found.\nCheck the number and try again.")

        lines_qs = order.lines.select_related('item').all()
        status_map = {
            'pending': '⏳ Pending',
            'confirmed': '✅ Confirmed',
            'paid': '💰 Paid',
            'ready': '📦 Ready',
            'completed': '🎉 Completed',
            'cancelled': '❌ Cancelled',
        }

        item_lines = []
        for line in lines_qs:
            item_lines.append(f"  {line.item.description} x{line.quantity}")

        return HttpResponse(
            f"END Order: {order.order_number}\n"
            f"Status: {status_map.get(order.status, order.status)}\n"
            f"Total: KES {order.total_amount:,.0f}\n\n"
            f"Items:\n" + "\n".join(item_lines)
        )

    return HttpResponse("END Invalid input.")


# ────────────────────────────────────────────────────
# 3. MY RECENT ORDERS
# ────────────────────────────────────────────────────

def _handle_my_orders(phone, parts, level):
    phone_normalized = _normalize_phone(phone)
    phone_variants = [
        phone_normalized,
        phone_normalized.lstrip('+'),
        '0' + phone_normalized[4:] if len(phone_normalized) > 4 else phone_normalized,
    ]

    from django.db.models import Q
    q = Q()
    for v in phone_variants:
        q |= Q(customer_phone=v)

    orders = Order.objects.filter(q).order_by('-created_at')[:5]

    if not orders:
        return HttpResponse(
            "END No orders found for your phone.\n"
            "Place an order first via option 1."
        )

    if level == 1:
        lines = ["END Your recent orders:\n"]
        for order in orders:
            status = order.get_status_display()
            lines.append(
                f"• {order.order_number} — {status}\n"
                f"  KES {order.total_amount:,.0f}"
            )
        lines.append(f"\nTo track: Dial again > option 2")
        return HttpResponse("\n".join(lines))

    return HttpResponse("END Invalid input.")
