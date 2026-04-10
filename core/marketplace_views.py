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
from .models import Item, Order, OrderLine, Payment, Store, County, Transaction, SupplierRelationship
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
    subtotal = 0
    adjustments = []
    for entry in cart:
        item_id = entry.get('item_id')
        qty = entry.get('qty', 1)

        try:
            item = Item.objects.get(id=item_id, business=business)
        except Item.DoesNotExist:
            return JsonResponse({'error': f'Item {item_id} not found'}, status=400)

        if qty < 1:
            return JsonResponse({'error': 'Quantity must be at least 1'}, status=400)

        available = item.current_balance()
        if available <= 0:
            adjustments.append(f'{item.description} is out of stock — removed')
            continue
        if qty > available:
            adjustments.append(f'{item.description}: adjusted to {available} (only {available} available)')
            qty = available

        if not item.selling_price:
            return JsonResponse({
                'error': f'{item.description} has no price set'
            }, status=400)

        subtotal += item.selling_price * qty
        order_lines.append((item, qty))

    if not order_lines:
        return JsonResponse({'error': 'No items available for your order'}, status=400)

    # Delivery & payment fields
    delivery_mode = data.get('delivery_mode', 'pickup')
    if delivery_mode not in ('pickup', 'delivery'):
        delivery_mode = 'pickup'

    payment_method = data.get('payment_method', 'mpesa')
    if payment_method not in ('mpesa', 'cash', 'pickup_pay'):
        payment_method = 'mpesa'

    delivery_fee = 0
    if delivery_mode == 'delivery' and business.offers_delivery:
        delivery_fee = float(business.delivery_fee or 0)

        # Proximity validation for delivery orders
        customer_lat = data.get('customer_lat')
        customer_lng = data.get('customer_lng')
        if customer_lat and customer_lng and business.latitude and business.longitude:
            distance = business.distance_to(customer_lat, customer_lng)
            if distance is not None and business.delivery_radius_km:
                if distance > float(business.delivery_radius_km):
                    return JsonResponse({
                        'error': f'Delivery is not available to your location ({distance:.1f}km away). '
                                 f'Maximum delivery distance is {business.delivery_radius_km}km. '
                                 f'Please choose Pickup instead.'
                    }, status=400)

    # Minimum order check
    if business.min_order_amount and subtotal < float(business.min_order_amount):
        return JsonResponse({
            'error': f'Minimum order amount is KES {business.min_order_amount:,.0f}'
        }, status=400)

    # Create order
    order = Order.objects.create(
        business=business,
        customer_name=customer_name,
        customer_phone=format_phone_ke(customer_phone),
        customer_location=data.get('customer_location', ''),
        notes=data.get('notes', ''),
        delivery_mode=delivery_mode,
        payment_method=payment_method,
        delivery_fee=delivery_fee,
    )

    for item, qty in order_lines:
        OrderLine.objects.create(
            order=order,
            item=item,
            quantity=qty,
            unit_price=item.selling_price,
        )

    order.recalculate_total()

    # Notify business about the new order
    from .notifications import notify_new_order
    notify_new_order(order)

    response_data = {
        'success': True,
        'order_number': order.order_number,
        'total': float(order.total_amount),
        'message': f'Order {order.order_number} placed! Total: KES {order.total_amount:,.0f}',
    }
    if adjustments:
        response_data['adjustments'] = adjustments

    return JsonResponse(response_data, status=201)


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


# ── STAFF: ORDER FULFILLMENT ─────────────────────────────────────────────────

@login_required
def fulfillment_list(request):
    """Staff view: orders that need fulfillment (confirmed/paid/ready)."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    orders = Order.objects.filter(
        business=profile.business,
        status__in=['pending', 'confirmed', 'paid', 'ready'],
    ).prefetch_related('lines__item')

    status_filter = request.GET.get('status', '')
    if status_filter:
        orders = orders.filter(status=status_filter)

    delivery_filter = request.GET.get('delivery', '')
    if delivery_filter:
        orders = orders.filter(delivery_mode=delivery_filter)

    from .models import RiderProfile
    from .performance import get_ranked_riders
    ranked_riders = get_ranked_riders(business=profile.business)

    return render(request, 'core/fulfillment_list.html', {
        'orders': orders,
        'status_filter': status_filter,
        'delivery_filter': delivery_filter,
        'ranked_riders': ranked_riders,
    })


@login_required
@require_POST
def assign_rider(request, order_id):
    """Assign a rider to a delivery order."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    order = get_object_or_404(Order, id=order_id, business=profile.business)

    if order.delivery_mode != 'delivery':
        return JsonResponse({'error': 'Not a delivery order'}, status=400)

    rider_id = request.POST.get('rider_id')
    if not rider_id:
        return JsonResponse({'error': 'Rider ID required'}, status=400)

    from .models import RiderProfile
    rider = get_object_or_404(RiderProfile, id=rider_id)
    order.rider = rider
    order.save(update_fields=['rider'])

    return JsonResponse({
        'success': True,
        'rider_name': str(rider),
    })


# ── OWNER: SUPPLIER MANAGEMENT ──────────────────────────────────────────────

from django.contrib import messages as django_messages


@login_required
def supplier_list(request):
    """Owner view: manage list of preferred suppliers."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    links = SupplierRelationship.objects.filter(
        business=profile.business
    ).select_related('supplier__business_type', 'supplier__county')

    return render(request, 'marketplace/supplier_list.html', {
        'links': links,
    })


@login_required
def add_supplier(request):
    """Owner: add a business from the platform as a supplier."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    my_business = profile.business

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier_id')
        notes = request.POST.get('notes', '')
        if supplier_id:
            supplier = get_object_or_404(Business, id=supplier_id)
            if supplier == my_business:
                django_messages.error(request, "You can't add your own business as a supplier.")
            elif SupplierRelationship.objects.filter(business=my_business, supplier=supplier).exists():
                django_messages.warning(request, f"{supplier.name} is already in your supplier list.")
            else:
                SupplierRelationship.objects.create(
                    business=my_business,
                    supplier=supplier,
                    notes=notes,
                )
                django_messages.success(request, f"{supplier.name} added as a supplier.")
        return redirect('supplier_list')

    # Show businesses on the platform (excluding own)
    query = request.GET.get('q', '').strip()
    businesses = Business.objects.exclude(id=my_business.id).select_related('business_type', 'county')
    if query:
        businesses = businesses.filter(
            Q(name__icontains=query) | Q(address__icontains=query)
        )

    # Exclude already-added suppliers
    existing_ids = SupplierRelationship.objects.filter(
        business=my_business
    ).values_list('supplier_id', flat=True)
    businesses = businesses.exclude(id__in=existing_ids)

    return render(request, 'marketplace/add_supplier.html', {
        'businesses': businesses,
        'query': query,
    })


@login_required
def edit_supplier(request, link_id):
    """Owner: edit notes on a supplier relationship."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner:
        return redirect('home')

    link = get_object_or_404(SupplierRelationship, id=link_id, business=profile.business)

    if request.method == 'POST':
        link.notes = request.POST.get('notes', '')
        link.save(update_fields=['notes'])
        django_messages.success(request, f"Notes updated for {link.supplier.name}.")
        return redirect('supplier_list')

    return render(request, 'marketplace/edit_supplier.html', {'link': link})


@login_required
def remove_supplier(request, link_id):
    """Owner: remove a supplier from the list."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner:
        return redirect('home')

    link = get_object_or_404(SupplierRelationship, id=link_id, business=profile.business)

    if request.method == 'POST':
        name = link.supplier.name
        link.delete()
        django_messages.success(request, f"{name} removed from your suppliers.")
        return redirect('supplier_list')

    return render(request, 'marketplace/remove_supplier.html', {'link': link})
