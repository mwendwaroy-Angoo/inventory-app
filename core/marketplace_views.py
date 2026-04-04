"""
Customer-facing marketplace views.

Public (no login required):
    /shop/                              — Discover businesses by location
    /shop/<business_id>/                — View a business storefront + items
    /shop/<business_id>/order/          — Place an order (cart → checkout)
    /shop/order/<order_number>/         — Track order status
    /shop/order/<order_number>/pay/     — Pay for order via M-Pesa STK Push
"""

import json

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from accounts.models import Business
from .models import Item, Order, OrderLine, Payment, Store, County, Transaction
from .mpesa import initiate_stk_push, format_phone_ke


# ── BUSINESS DISCOVERY ───────────────────────────────────────────────────────

def shop_home(request):
    """List businesses, optionally filtered by county or search."""
    businesses = Business.objects.select_related(
        'business_type', 'county', 'sub_county', 'ward'
    ).all()

    query = request.GET.get('q', '').strip()
    county_id = request.GET.get('county', '')

    if query:
        businesses = businesses.filter(
            Q(name__icontains=query) | Q(address__icontains=query)
        )
    if county_id:
        businesses = businesses.filter(county_id=county_id)

    # Only show businesses that have at least 1 item with a selling price
    business_ids = Item.objects.filter(
        selling_price__isnull=False
    ).values_list('business_id', flat=True).distinct()
    businesses = businesses.filter(id__in=business_ids)

    counties = County.objects.all()

    return render(request, 'marketplace/shop_home.html', {
        'businesses': businesses,
        'counties': counties,
        'query': query,
        'selected_county': county_id,
    })


# ── BUSINESS STOREFRONT ─────────────────────────────────────────────────────

def storefront(request, business_id):
    """View a business's items available for ordering."""
    business = get_object_or_404(Business, id=business_id)

    items = Item.objects.filter(
        business=business,
        selling_price__isnull=False,
    ).select_related('store')

    # Only show items with stock > 0
    available_items = [i for i in items if i.current_balance() > 0]

    stores = Store.objects.filter(business=business)

    store_filter = request.GET.get('store', '')
    search = request.GET.get('search', '').strip()

    if store_filter:
        available_items = [i for i in available_items if str(i.store_id) == store_filter]
    if search:
        available_items = [i for i in available_items if search.lower() in i.description.lower()]

    return render(request, 'marketplace/storefront.html', {
        'business': business,
        'items': available_items,
        'stores': stores,
        'store_filter': store_filter,
        'search': search,
    })


# ── PLACE ORDER ──────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def place_order(request, business_id):
    """Create an order from a JSON cart.

    Expects POST with JSON body:
        {
            "customer_name": "John Doe",
            "customer_phone": "0712345678",
            "customer_location": "Westlands",
            "notes": "Deliver by 5pm",
            "items": [
                {"item_id": 1, "qty": 2},
                {"item_id": 5, "qty": 1}
            ]
        }
    """
    business = get_object_or_404(Business, id=business_id)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    customer_name = data.get('customer_name', '').strip()
    customer_phone = data.get('customer_phone', '').strip()
    cart = data.get('items', [])

    if not customer_name or not customer_phone:
        return JsonResponse({'error': 'Name and phone are required'}, status=400)

    if not cart:
        return JsonResponse({'error': 'Cart is empty'}, status=400)

    # Validate items and stock
    order_lines = []
    for entry in cart:
        item_id = entry.get('item_id')
        qty = entry.get('qty', 1)

        try:
            item = Item.objects.get(id=item_id, business=business)
        except Item.DoesNotExist:
            return JsonResponse({'error': f'Item {item_id} not found'}, status=400)

        if qty < 1:
            return JsonResponse({'error': 'Quantity must be at least 1'}, status=400)

        if item.current_balance() < qty:
            return JsonResponse({
                'error': f'Not enough stock for {item.description}. Available: {item.current_balance()}'
            }, status=400)

        if not item.selling_price:
            return JsonResponse({
                'error': f'{item.description} has no price set'
            }, status=400)

        order_lines.append((item, qty))

    # Create order
    order = Order.objects.create(
        business=business,
        customer_name=customer_name,
        customer_phone=format_phone_ke(customer_phone),
        customer_location=data.get('customer_location', ''),
        notes=data.get('notes', ''),
    )

    for item, qty in order_lines:
        OrderLine.objects.create(
            order=order,
            item=item,
            quantity=qty,
            unit_price=item.selling_price,
        )

    order.recalculate_total()

    return JsonResponse({
        'success': True,
        'order_number': order.order_number,
        'total': float(order.total_amount),
        'message': f'Order {order.order_number} placed! Total: KES {order.total_amount:,.0f}',
    }, status=201)


# ── ORDER TRACKING ───────────────────────────────────────────────────────────

def track_order(request, order_number):
    """Customer-facing order tracking page."""
    order = get_object_or_404(Order, order_number=order_number)
    lines = order.lines.select_related('item')
    payments = order.payments.all()

    # Calculate status index for the template progress bar
    status_order = ['pending', 'confirmed', 'paid', 'ready', 'completed']
    try:
        status_index = status_order.index(order.status) + 1
    except ValueError:
        status_index = 0

    return render(request, 'marketplace/track_order.html', {
        'order': order,
        'lines': lines,
        'payments': payments,
        'status_index': status_index,
    })


# ── PAY FOR ORDER (STK PUSH) ────────────────────────────────────────────────

@csrf_exempt
@require_POST
def pay_order(request, order_number):
    """Initiate M-Pesa STK Push for an order.

    Expects JSON:  {"phone": "0712345678"}
    Uses the order's total_amount minus any completed payments.
    """
    order = get_object_or_404(Order, order_number=order_number)

    if order.status in ('paid', 'completed', 'cancelled'):
        return JsonResponse({'error': f'Order is already {order.status}'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    phone = data.get('phone', order.customer_phone)
    phone_formatted = format_phone_ke(phone)

    # Calculate remaining balance
    paid_amount = sum(
        p.amount for p in order.payments.filter(status='completed')
    )
    remaining = order.total_amount - paid_amount
    if remaining <= 0:
        order.status = 'paid'
        order.save(update_fields=['status'])
        return JsonResponse({'error': 'Order is already fully paid'}, status=400)

    amount = int(remaining)

    # Build callback URL
    callback_url = request.build_absolute_uri('/mpesa/callback/')

    # Create payment record
    payment = Payment.objects.create(
        order=order,
        business=order.business,
        amount=amount,
        method='mpesa',
        status='pending',
        phone=phone_formatted,
    )

    result = initiate_stk_push(
        phone_number=phone_formatted,
        amount=amount,
        account_reference=order.order_number,
        description="Duka Mwecheche",
        callback_url=callback_url,
    )

    if result and result.get('ResponseCode') == '0':
        payment.checkout_request_id = result.get('CheckoutRequestID', '')
        payment.merchant_request_id = result.get('MerchantRequestID', '')
        payment.save(update_fields=['checkout_request_id', 'merchant_request_id'])

        return JsonResponse({
            'success': True,
            'message': 'M-Pesa prompt sent to your phone. Enter PIN to pay.',
            'payment_id': payment.id,
        })
    else:
        payment.status = 'failed'
        payment.result_desc = str(result) if result else 'No response'
        payment.save()
        return JsonResponse({
            'success': False,
            'error': 'Could not reach M-Pesa. Please try again.',
        }, status=502)


# ── OWNER: ORDER MANAGEMENT ─────────────────────────────────────────────────

from django.contrib.auth.decorators import login_required


@login_required
def order_list(request):
    """Business owner/staff view of all orders."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    orders = Order.objects.filter(business=profile.business)

    status_filter = request.GET.get('status', '')
    if status_filter:
        orders = orders.filter(status=status_filter)

    return render(request, 'core/order_list.html', {
        'orders': orders,
        'status_filter': status_filter,
    })


@login_required
@require_POST
def update_order_status(request, order_id):
    """Update an order's status (owner/staff action)."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    order = get_object_or_404(Order, id=order_id, business=profile.business)
    new_status = request.POST.get('status', '')

    valid = [c[0] for c in Order.STATUS_CHOICES]
    if new_status not in valid:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    order.status = new_status
    order.save(update_fields=['status'])

    # If marked completed and was paid, fulfill the order
    if new_status == 'completed' and not Transaction.objects.filter(invoice_no=order.order_number).exists():
        from .mpesa_views import _fulfill_order
        _fulfill_order(order)

    return JsonResponse({'success': True, 'status': order.status})
