"""
USSD callback handler for Africa's Talking.

AT sends POST with: sessionId, serviceCode, phoneNumber, text
- text="" on first dial
- text="1" after selecting option 1
- text="1*3" after selecting option 1 then 3
- etc.

Response must start with:
- "CON " for ongoing session (expecting more input)
- "END " for terminal response (session closes)
"""

import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.models import Item, Transaction
from accounts.models import UserProfile, Business
from datetime import date

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5


def normalize_phone(phone):
    """Convert phone to +254... format for matching."""
    phone = phone.strip()
    if phone.startswith('0'):
        return '+254' + phone[1:]
    if phone.startswith('254') and not phone.startswith('+'):
        return '+' + phone
    return phone


def find_business_by_phone(phone):
    """Find the business associated with this phone number."""
    phone_normalized = normalize_phone(phone)
    # Also try without + prefix and with 0 prefix for matching
    phone_variants = [
        phone_normalized,                          # +254712345678
        phone_normalized.lstrip('+'),              # 254712345678
        '0' + phone_normalized[4:],                # 0712345678
    ]

    # Check UserProfile.phone first
    for variant in phone_variants:
        profile = UserProfile.objects.filter(phone=variant).select_related('business').first()
        if profile and profile.business:
            return profile.business, profile

    # Check Business.phone
    for variant in phone_variants:
        business = Business.objects.filter(phone=variant).first()
        if business:
            owner_profile = business.users.filter(role='owner').first()
            return business, owner_profile

    return None, None


def get_item_list(business, page=1):
    """Get paginated item list for the business."""
    items = Item.objects.filter(
        store__business=business
    ).select_related('store').order_by('description')

    total = items.count()
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = list(items[start:end])
    has_more = end < total

    return page_items, has_more, total


def format_item_menu(items, _page, has_more):
    """Format items as a numbered USSD menu."""
    lines = []
    for i, item in enumerate(items, 1):
        balance = item.current_balance()
        price = f" KES{item.selling_price}" if item.selling_price else ""
        lines.append(f"{i}. {item.description}{price} ({balance} {item.unit})")

    if has_more:
        lines.append(f"{len(items) + 1}. More items >>")

    lines.append("0. Back")
    return "\n".join(lines)


@csrf_exempt
@require_POST
def ussd_callback(request):
    """Handle USSD callback from Africa's Talking."""
    _session_id = request.POST.get('sessionId', '')
    phone_number = request.POST.get('phoneNumber', '')
    text = request.POST.get('text', '').strip()

    # Split input chain
    parts = text.split('*') if text else []
    level = len(parts)

    # Find business by phone
    business, profile = find_business_by_phone(phone_number)

    if not business:
        return HttpResponse(
            "END Welcome to Duka Mwecheche.\n\n"
            "Your phone number is not registered.\n"
            "Please register at the web app first,\n"
            "or ask your business owner to add\n"
            "your phone number to your staff profile."
        )

    user_name = ""
    if profile and profile.user:
        user_name = profile.user.get_full_name() or profile.user.username

    # ── LEVEL 0: Main menu ──
    if level == 0:
        return HttpResponse(
            f"CON Duka Mwecheche\n"
            f"Welcome, {user_name or 'User'}!\n"
            f"Business: {business.name}\n\n"
            f"1. Record Sale\n"
            f"2. Restock Item\n"
            f"3. Check Stock\n"
            f"4. Today's Summary"
        )

    action = parts[0]

    # ═══════════════════════════════════════════
    # 1. RECORD SALE / 2. RESTOCK
    # ═══════════════════════════════════════════
    if action in ('1', '2'):
        trans_type = 'Issue' if action == '1' else 'Receipt'
        action_label = 'SELL' if action == '1' else 'RESTOCK'

        # Level 1: Show item list (page 1)
        if level == 1:
            items, has_more, _total = get_item_list(business, page=1)
            if not items:
                return HttpResponse("END No items found in your inventory.")
            menu = format_item_menu(items, 1, has_more)
            return HttpResponse(f"CON {action_label} — Select item:\n{menu}")

        # Level 2: Item selected (or pagination)
        if level == 2:
            choice = parts[1]

            # Check for pagination
            items, has_more, _total = get_item_list(business, page=1)

            if choice == '0':
                return HttpResponse(
                    "CON Duka Mwecheche\n\n"
                    "1. Record Sale\n"
                    "2. Restock Item\n"
                    "3. Check Stock\n"
                    "4. Today's Summary"
                )

            try:
                idx = int(choice)
            except ValueError:
                return HttpResponse("END Invalid selection.")

            # Pagination: if they selected the "More" option
            if has_more and idx == len(items) + 1:
                items2, has_more2, _ = get_item_list(business, page=2)
                menu = format_item_menu(items2, 2, has_more2)
                return HttpResponse(f"CON {action_label} — Select item:\n{menu}")

            if idx < 1 or idx > len(items):
                return HttpResponse("END Invalid selection. Try again.")

            item = items[idx - 1]
            balance = item.current_balance()

            if trans_type == 'Issue' and balance <= 0:
                return HttpResponse(f"END {item.description} is out of stock (0 {item.unit}).")

            return HttpResponse(
                f"CON {item.description}\n"
                f"Current stock: {balance} {item.unit}\n"
                f"{'Price: KES ' + str(item.selling_price) if item.selling_price else ''}\n\n"
                f"Enter quantity to {action_label.lower()}:"
            )

        # Level 3: Quantity entered — confirm
        if level == 3:
            choice = parts[1]
            qty_str = parts[2]

            try:
                idx = int(choice)
                qty = int(qty_str)
            except ValueError:
                return HttpResponse("END Invalid input. Please enter a number.")

            if qty < 1:
                return HttpResponse("END Quantity must be at least 1.")

            items, _, _ = get_item_list(business, page=1)
            if idx < 1 or idx > len(items):
                return HttpResponse("END Invalid item. Try again.")

            item = items[idx - 1]
            balance = item.current_balance()

            if trans_type == 'Issue' and qty > balance:
                return HttpResponse(
                    f"END Not enough stock!\n"
                    f"{item.description}: {balance} {item.unit} available.\n"
                    f"You tried to sell {qty}."
                )

            total_price = float(item.selling_price or 0) * qty

            return HttpResponse(
                f"CON Confirm {action_label}:\n"
                f"Item: {item.description}\n"
                f"Qty: {qty} {item.unit}\n"
                f"{'Total: KES ' + f'{total_price:,.0f}' if trans_type == 'Issue' and item.selling_price else ''}\n\n"
                f"1. Confirm\n"
                f"2. Cancel"
            )

        # Level 4: Confirmation
        if level == 4:
            confirm = parts[3]
            if confirm != '1':
                return HttpResponse("END Transaction cancelled.")

            try:
                idx = int(parts[1])
                qty = int(parts[2])
            except ValueError:
                return HttpResponse("END Error processing transaction.")

            items, _, _ = get_item_list(business, page=1)
            if idx < 1 or idx > len(items):
                return HttpResponse("END Error: item not found.")

            item = items[idx - 1]
            balance = item.current_balance()

            if trans_type == 'Issue':
                if qty > balance:
                    return HttpResponse("END Not enough stock.")
                qty_signed = -qty
            else:
                qty_signed = qty

            # Record the transaction
            transaction = Transaction.objects.create(
                item=item,
                type=trans_type,
                qty=qty_signed,
                recipient=f'USSD ({phone_number})',
                business=business,
            )

            new_balance = item.current_balance()

            # Send notification
            try:
                from core.notifications import notify_transaction
                daily_count = Transaction.objects.filter(
                    business=business, date=date.today()
                ).count()
                user = profile.user if profile else None
                notify_transaction(transaction, business, daily_count, user=user)
            except (ImportError, RuntimeError) as e:
                logger.error("USSD notification error: %s", e)

            if trans_type == 'Issue':
                total_price = float(item.selling_price or 0) * qty
                return HttpResponse(
                    f"END Sale recorded!\n\n"
                    f"{qty} {item.unit} of {item.description}\n"
                    f"{'Total: KES ' + f'{total_price:,.0f}' if item.selling_price else ''}\n"
                    f"Remaining: {new_balance} {item.unit}\n\n"
                    f"Thank you!"
                )
            else:
                return HttpResponse(
                    f"END Restock recorded!\n\n"
                    f"{qty} {item.unit} of {item.description}\n"
                    f"New balance: {new_balance} {item.unit}\n\n"
                    f"Thank you!"
                )

    # ═══════════════════════════════════════════
    # 3. CHECK STOCK
    # ═══════════════════════════════════════════
    if action == '3':
        # Level 1: Show item list
        if level == 1:
            items, has_more, _total = get_item_list(business, page=1)
            if not items:
                return HttpResponse("END No items in your inventory.")
            menu = format_item_menu(items, 1, has_more)
            return HttpResponse(f"CON CHECK STOCK — Select item:\n{menu}")

        # Level 2: Show item detail
        if level == 2:
            choice = parts[1]
            if choice == '0':
                return HttpResponse(
                    "CON Duka Mwecheche\n\n"
                    "1. Record Sale\n"
                    "2. Restock Item\n"
                    "3. Check Stock\n"
                    "4. Today's Summary"
                )

            try:
                idx = int(choice)
            except ValueError:
                return HttpResponse("END Invalid selection.")

            items, has_more, _ = get_item_list(business, page=1)

            # Pagination
            if has_more and idx == len(items) + 1:
                items2, has_more2, _ = get_item_list(business, page=2)
                menu = format_item_menu(items2, 2, has_more2)
                return HttpResponse(f"CON CHECK STOCK — Select item:\n{menu}")

            if idx < 1 or idx > len(items):
                return HttpResponse("END Invalid selection.")

            item = items[idx - 1]
            balance = item.current_balance()
            status = "LOW STOCK!" if item.needs_reorder() else "OK"
            price_info = f"Selling price: KES {item.selling_price}\n" if item.selling_price else ""
            cost_info = ""
            if profile and profile.is_owner and item.cost_price:
                cost_info = f"Cost price: KES {item.cost_price}\n"
                stock_value = float(item.cost_price) * balance
                cost_info += f"Stock value: KES {stock_value:,.0f}\n"

            return HttpResponse(
                f"END {item.description}\n"
                f"({item.material_no})\n\n"
                f"Balance: {balance} {item.unit}\n"
                f"Status: {status}\n"
                f"Reorder level: {item.reorder_level}\n"
                f"{price_info}"
                f"{cost_info}"
                f"Store: {item.store.name}"
            )

    # ═══════════════════════════════════════════
    # 4. TODAY'S SUMMARY
    # ═══════════════════════════════════════════
    if action == '4':
        today = date.today()
        today_transactions = Transaction.objects.filter(
            business=business,
            date=today
        ).select_related('item')

        total_sales = 0
        total_receipts = 0
        sale_count = 0
        receipt_count = 0

        for t in today_transactions:
            if t.type == 'Issue':
                sale_count += 1
                total_sales += abs(t.qty) * float(t.item.selling_price or 0)
            else:
                receipt_count += 1
                total_receipts += t.qty

        # Low stock count
        all_items = Item.objects.filter(store__business=business)
        low_stock = sum(1 for i in all_items if i.needs_reorder())

        return HttpResponse(
            f"END Today's Summary — {business.name}\n"
            f"{today.strftime('%d %b %Y')}\n\n"
            f"Sales: {sale_count} transactions\n"
            f"Revenue: KES {total_sales:,.0f}\n"
            f"Restocks: {receipt_count} transactions\n"
            f"Low stock items: {low_stock}\n\n"
            f"— Duka Mwecheche"
        )

    # ── FALLBACK ──
    return HttpResponse(
        "END Invalid option. Please try again."
    )
