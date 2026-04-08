from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from .models import Item, Transaction, Store, BusinessType, Customer
from .forms import ItemForm
import openpyxl
from django.db.models import Sum, Count
from decimal import Decimal
from datetime import date, timedelta
import json
import os


# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_user_profile(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def offline(request):
    return render(request, 'offline.html')


def owner_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            if not request.user.userprofile.is_owner:
                messages.error(request, "Only business owners can access this page.")
                return redirect('stock_list')
        except Exception:
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


# ── HOME ─────────────────────────────────────────────────────────────────────

def home(request):
    context = {'today': timezone.now().strftime("%B %d, %Y")}

    if request.user.is_authenticated:
        try:
            user_profile = request.user.userprofile
            business = user_profile.business
            all_items = Item.objects.filter(business=business)
            reorder_items = [item for item in all_items if item.needs_reorder()]
            low_stock_count = len([item for item in all_items if item.current_balance() <= item.reorder_level])
            reorder_count = len(reorder_items)

            context.update({
                'total_items': all_items.count(),
                'low_stock_count': low_stock_count,
                'reorder_count': reorder_count,
                'reorder_items': sorted(reorder_items, key=lambda x: x.current_balance())[:20],
            })
        except Exception:
            context['error'] = "Profile not found. Please contact support."
    else:
        context['guest'] = True
        context['services'] = [
            ('📦', 'Inventory Management', 'Track stock levels, costs, and reorder points in real time. Never run out of stock again.'),
            ('🛒', 'Online Marketplace', 'Your own storefront where customers browse and order directly. No middleman.'),
            ('💳', 'M-Pesa Payments', 'Accept payments via Lipa Na M-Pesa. Instant STK Push to your customers\' phones.'),
            ('📱', 'USSD Access', 'Record sales and check stock via USSD — works on any phone, no internet needed.'),
            ('📊', 'Analytics Dashboard', 'See your top products, revenue trends, and profit margins at a glance.'),
            ('👥', 'Staff Management', 'Add staff, assign roles, and get notified when they log in or record transactions.'),
        ]
        context['faqs'] = [
            ('Is Duka Mwecheche free?', 'Yes! The platform is completely free for all businesses. You only pay standard M-Pesa transaction fees when accepting payments.'),
            ('Do I need a smartphone?', 'No. You can manage your stock via USSD on any basic phone. The web app works on smartphones and computers too.'),
            ('How do customers find my shop?', 'Once you register and add items with prices, your business appears on the Marketplace. Customers can search by location and product.'),
            ('Is my data safe?', 'Absolutely. Your data is stored securely on cloud servers with regular backups. Only you and your staff can access your business data.'),
            ('Can I accept M-Pesa payments?', 'Yes. We integrate with Safaricom\'s Daraja API. You\'ll need a Till or Paybill number from Safaricom to receive funds directly.'),
            ('How do I add staff?', 'Go to Manage → Staff → Add Staff. Staff members can record transactions but cannot access business settings or financial reports.'),
        ]

    return render(request, 'core/home.html', context)


# ── STOCK LIST ────────────────────────────────────────────────────────────────

@login_required
def stock_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        messages.error(request, "No business profile found.")
        return redirect('home')

    stores = Store.objects.filter(business=user_profile.business)
    selected_store_id = request.GET.get('store')
    status_filter = request.GET.get('status')  # 'low_stock' or 'reorder'

    items = Item.objects.filter(store__business=user_profile.business).order_by('material_no')

    if selected_store_id:
        try:
            selected_store_id = int(selected_store_id)
            items = items.filter(store_id=selected_store_id)
        except (ValueError, TypeError):
            pass

    # Convert to list for Python-level filtering
    all_items = list(items)

    if status_filter == 'low_stock':
        all_items = [i for i in all_items if i.current_balance() <= i.reorder_level]
    elif status_filter == 'reorder':
        all_items = [i for i in all_items if i.needs_reorder()]

    context = {
        'items': all_items,
        'stores': stores,
        'selected_store': selected_store_id if selected_store_id else None,
        'status_filter': status_filter,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/stock_list.html', context)


# ── TRANSACTIONS ──────────────────────────────────────────────────────────────

@login_required
def add_transaction(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    stores = Store.objects.filter(business=user_profile.business)
    customers = Customer.objects.filter(business=user_profile.business)

    if request.method == 'POST':
        item_id = request.POST['item']
        trans_type = request.POST['type']
        quantity = int(request.POST['quantity'])
        invoice_no = request.POST.get('invoice_no', '')
        recipient = request.POST.get('recipient', '')

        new_customer_name = request.POST.get('new_customer_name', '').strip()
        if new_customer_name and trans_type == 'Issue':
            customer, created = Customer.objects.get_or_create(
                business=user_profile.business,
                name=new_customer_name,
                defaults={'phone': request.POST.get('new_customer_phone', '')}
            )
            recipient = customer.name

        item = get_object_or_404(Item, id=item_id)

        if trans_type == 'Issue':
            if item.current_balance() < quantity:
                messages.error(
                    request,
                    f"Not enough stock for {item.description}. "
                    f"Available: {item.current_balance()} {item.unit}, "
                    f"requested: {quantity}."
                )
                return redirect('add_transaction')
            quantity = -quantity

        transaction = Transaction.objects.create(
            item=item,
            type=trans_type,
            qty=quantity,
            recipient=recipient,
            invoice_no=invoice_no,
            business=user_profile.business,
        )

        # Count today's transactions for SMS/WhatsApp decision
        from datetime import date as date_obj
        daily_count = Transaction.objects.filter(
            business=user_profile.business,
            date=date_obj.today()
        ).count()

        # Send notifications asynchronously
        try:
            from .notifications import notify_transaction
            notify_transaction(transaction, user_profile.business, daily_count, user=request.user)
        except Exception as e:
            pass  # Never block transaction recording due to notification failure

        messages.success(
            request,
            f"{abs(quantity)} {item.unit} of {item.description} recorded as {trans_type.lower()}."
        )
        return redirect('add_transaction')
    items = Item.objects.filter(store__business=user_profile.business).order_by('material_no')
    context = {
        'items': items,
        'stores': stores,
        'customers': customers,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/add_transaction.html', context)


@login_required
def transaction_history(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    transactions = Transaction.objects.filter(
        item__store__business=user_profile.business
    ).select_related('item', 'item__store').order_by('-date')

    context = {
        'transactions': transactions,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/transaction_history.html', context)


@login_required
def export_transactions_excel(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    transactions = Transaction.objects.filter(
        item__store__business=user_profile.business
    ).select_related('item', 'item__store').order_by('-date')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transaction History"
    ws.append(['Date', 'Item', 'Material No', 'Store', 'Type', 'Qty', 'Recipient', 'Invoice No'])

    for t in transactions:
        ws.append([
            str(t.date),
            t.item.description,
            t.item.material_no,
            t.item.store.name,
            t.type,
            t.qty,
            t.recipient or '—',
            t.invoice_no or '—',
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=transaction_history.xlsx'
    wb.save(response)
    return response


# ── ITEM DETAIL ───────────────────────────────────────────────────────────────

@login_required
def item_detail(request, item_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)
    transactions = item.transactions.all().order_by('-date')
    context = {
        'item': item,
        'transactions': transactions,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/item_detail.html', context)


# ── EXPORT STOCK ──────────────────────────────────────────────────────────────

@login_required
def export_stock_excel(request):
    user_profile = get_user_profile(request)
    store_id = request.GET.get('store')

    if user_profile:
        items = Item.objects.filter(store__business=user_profile.business)
        if store_id:
            items = items.filter(store_id=store_id)
    else:
        items = Item.objects.none()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock List"
    ws.append(['Material No', 'Description', 'Unit', 'Current Balance',
               'Reorder Level', 'Selling Price', 'Status', 'Store'])

    for item in items:
        status = ("OUT OF STOCK" if item.current_balance() <= 0
                  else "REORDER" if item.needs_reorder() else "AVAILABLE")
        ws.append([
            item.material_no,
            item.description,
            item.unit,
            item.current_balance(),
            item.reorder_level,
            float(item.selling_price) if item.selling_price else '',
            status,
            item.store.name,
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=stock_list.xlsx'
    wb.save(response)
    return response


# ── MANAGE ITEMS ──────────────────────────────────────────────────────────────

@login_required
@owner_required
def manage_items(request):
    user_profile = request.user.userprofile
    items = Item.objects.filter(
        business=user_profile.business
    ).select_related('store').order_by('store__name', 'description')

    context = {
        'items': items,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/manage_items.html', context)


@login_required
@owner_required
def add_item(request):
    user_profile = request.user.userprofile

    if request.method == 'POST':
        form = ItemForm(request.POST, business=user_profile.business, show_cost_price=True)
        if form.is_valid():
            item = form.save(commit=False)
            item.business = user_profile.business
            if not item.material_no:
                last_item = Item.objects.filter(
                    business=user_profile.business
                ).order_by('id').last()
                next_id = (last_item.id + 1) if last_item else 1
                item.material_no = f"MAT-{next_id:04d}"
            item.save()
            messages.success(request, f"'{item.description}' added successfully.")
            return redirect('manage_items')
    else:
        form = ItemForm(business=user_profile.business, show_cost_price=True)

    context = {
        'form': form,
        'today': timezone.now().strftime("%B %d, %Y"),
        'action': 'Add',
    }
    return render(request, 'core/item_form.html', context)


@login_required
@owner_required
def edit_item(request, item_id):
    user_profile = request.user.userprofile
    item = get_object_or_404(Item, id=item_id, business=user_profile.business)

    if request.method == 'POST':
        form = ItemForm(request.POST, instance=item,
                       business=user_profile.business, show_cost_price=True)
        if form.is_valid():
            form.save()
            messages.success(request, f"'{item.description}' updated successfully.")
            return redirect('manage_items')
    else:
        form = ItemForm(instance=item, business=user_profile.business, show_cost_price=True)

    context = {
        'form': form,
        'item': item,
        'today': timezone.now().strftime("%B %d, %Y"),
        'action': 'Edit',
    }
    return render(request, 'core/item_form.html', context)

@login_required
@owner_required
def delete_item(request, item_id):
    user_profile = request.user.userprofile
    item = get_object_or_404(Item, id=item_id, business=user_profile.business)

    if request.method == 'POST':
        item_name = item.description
        item.delete()
        messages.success(request, f"'{item_name}' deleted successfully.")
        return redirect('manage_items')

    context = {
        'item': item,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/delete_item.html', context)


# ── MANAGE STORES ─────────────────────────────────────────────────────────────

@login_required
@owner_required
def manage_stores(request):
    user_profile = request.user.userprofile
    stores = Store.objects.filter(business=user_profile.business)

    if request.method == 'POST':
        store_name = request.POST.get('name', '').strip()
        if store_name:
            Store.objects.create(name=store_name, business=user_profile.business)
            messages.success(request, f"Store '{store_name}' created successfully.")
            return redirect('manage_stores')
        else:
            messages.error(request, "Store name cannot be empty.")

    context = {
        'stores': stores,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/manage_stores.html', context)


# ── CUSTOMERS ─────────────────────────────────────────────────────────────────

@login_required
def customer_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    customers = Customer.objects.filter(business=user_profile.business)
    context = {'customers': customers}
    return render(request, 'core/customer_list.html', context)


@login_required
def add_customer(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        location = request.POST.get('location', '').strip()

        if name:
            Customer.objects.create(
                business=user_profile.business,
                name=name,
                phone=phone,
                location=location,
            )
            messages.success(request, f"Customer '{name}' added.")
            return redirect('customer_list')
        else:
            messages.error(request, "Customer name is required.")

    return render(request, 'core/customer_list.html',
                  {'customers': Customer.objects.filter(business=user_profile.business)})


@login_required
@owner_required
def delete_customer(request, customer_id):
    user_profile = request.user.userprofile
    customer = get_object_or_404(Customer, id=customer_id, business=user_profile.business)

    if request.method == 'POST':
        customer.delete()
        messages.success(request, "Customer deleted.")
    return redirect('customer_list')


# ── AJAX ──────────────────────────────────────────────────────────────────────

@login_required
def ajax_customers(request):
    """Returns customers for the transaction form dropdown."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse([], safe=False)

    customers = Customer.objects.filter(
        business=user_profile.business
    ).values('id', 'name', 'phone')
    return JsonResponse(list(customers), safe=False)


# ── SALES & P&L ───────────────────────────────────────────────────────────────

def get_date_range(period, date_from=None, date_to=None):
    """Returns (start_date, end_date) based on period filter."""
    today = date.today()
    if period == 'today':
        return today, today
    elif period == 'week':
        return today - timedelta(days=today.weekday()), today
    elif period == 'month':
        return today.replace(day=1), today
    elif period == 'year':
        return today.replace(month=1, day=1), today
    elif period == 'custom' and date_from and date_to:
        try:
            from datetime import datetime
            return (datetime.strptime(date_from, '%Y-%m-%d').date(),
                    datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            return today.replace(day=1), today
    else:
        return today.replace(day=1), today  # default to this month


@login_required
@owner_required
def sales_dashboard(request):
    user_profile = request.user.userprofile
    business = user_profile.business

    period = request.GET.get('period', 'month')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    start_date, end_date = get_date_range(period, date_from, date_to)

    # Get all issue transactions in period (sales)
    sales = Transaction.objects.filter(
        business=business,
        type='Issue',
        date__gte=start_date,
        date__lte=end_date,
    ).select_related('item')

    # Summary calculations
    total_revenue = sum(t.revenue() for t in sales)
    total_cost = sum(t.cost() for t in sales)
    total_profit = total_revenue - total_cost
    total_units_sold = sum(abs(t.qty) for t in sales)

    # Stock value
    all_items = Item.objects.filter(business=business)
    stock_value = sum(item.stock_value() for item in all_items)

    # Daily sales data for bar chart
    from collections import defaultdict
    daily_revenue = defaultdict(float)
    daily_profit = defaultdict(float)
    for t in sales:
        day_str = str(t.date)
        daily_revenue[day_str] += t.revenue()
        daily_profit[day_str] += t.profit()

    # Sort by date
    sorted_dates = sorted(daily_revenue.keys())
    chart_labels = sorted_dates
    chart_revenue = [round(daily_revenue[d], 2) for d in sorted_dates]
    chart_profit = [round(daily_profit[d], 2) for d in sorted_dates]

    # Per item breakdown
    item_sales = defaultdict(lambda: {
        'description': '',
        'units_sold': 0,
        'revenue': 0.0,
        'cost': 0.0,
        'profit': 0.0
    })
    for t in sales:
        key = t.item.id
        item_sales[key]['description'] = t.item.description
        item_sales[key]['material_no'] = t.item.material_no
        item_sales[key]['units_sold'] += abs(t.qty)
        item_sales[key]['revenue'] += t.revenue()
        item_sales[key]['cost'] += t.cost()
        item_sales[key]['profit'] += t.profit()

    item_sales_list = sorted(
        item_sales.values(),
        key=lambda x: x['revenue'],
        reverse=True
    )

    # Top selling items (by units)
    top_items = sorted(item_sales_list, key=lambda x: x['units_sold'], reverse=True)[:5]

    # Slow moving items (items with no sales in period)
    sold_item_ids = set(t.item.id for t in sales)
    slow_items = all_items.exclude(id__in=sold_item_ids)[:10]

    # Profit margin safe calculation
    profit_margin = round((total_profit / total_revenue * 100), 1) if total_revenue > 0 else 0
    context = {
        'period': period,
        'date_from': start_date,
        'date_to': end_date,
        'total_revenue': round(total_revenue, 2),
        'total_cost': round(total_cost, 2),
        'total_profit': round(total_profit, 2),
        'profit_margin': profit_margin,
        'total_units_sold': total_units_sold,
        'stock_value': round(stock_value, 2),
        'item_sales_list': item_sales_list,
        'top_items': top_items,
        'slow_items': slow_items,
        'chart_labels': json.dumps(chart_labels),
        'chart_revenue': json.dumps(chart_revenue),
        'chart_profit': json.dumps(chart_profit),
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/sales_dashboard.html', context)


@login_required
@owner_required
def export_sales_excel(request):
    user_profile = request.user.userprofile
    business = user_profile.business

    period = request.GET.get('period', 'month')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    start_date, end_date = get_date_range(period, date_from, date_to)

    sales = Transaction.objects.filter(
        business=business,
        type='Issue',
        date__gte=start_date,
        date__lte=end_date,
    ).select_related('item').order_by('date')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales Report"
    ws.append([
        'Date', 'Item', 'Material No', 'Units Sold',
        'Selling Price', 'Cost Price', 'Revenue', 'Cost', 'Profit',
        'Recipient', 'Invoice No'
    ])

    for t in sales:
        ws.append([
            str(t.date),
            t.item.description,
            t.item.material_no,
            abs(t.qty),
            float(t.item.selling_price) if t.item.selling_price else '',
            float(t.item.cost_price) if t.item.cost_price else '',
            round(t.revenue(), 2),
            round(t.cost(), 2),
            round(t.profit(), 2),
            t.recipient or '—',
            t.invoice_no or '—',
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=sales_report.xlsx'
    wb.save(response)
    return response

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────────────

@login_required
def notifications_list(request):
    notifications = request.user.app_notifications.all()[:50]
    request.user.app_notifications.filter(is_read=False).update(is_read=True)
    return render(request, 'core/notifications.html', {
        'notifications': notifications
    })


@login_required
def notifications_count(request):
    count = request.user.app_notifications.filter(is_read=False).count()
    return JsonResponse({'count': count})

def daily_summary_webhook(request):
    """
    Endpoint called by cron-job.org to trigger daily summaries.
    Protected by a secret token.
    """
    token = request.GET.get('token')
    expected = os.getenv('CRON_SECRET', 'duka-mwecheche-cron-2026')
    if token != expected:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Invalid token')

    from accounts.models import Business
    from .notifications import send_daily_summary
    businesses = Business.objects.all()
    for business in businesses:
        try:
            send_daily_summary(business)
        except Exception as e:
            pass

    return JsonResponse({'status': 'ok', 'businesses': businesses.count()})


# ── QUICK SELL ────────────────────────────────────────────────────────────────

@login_required
def quick_sell(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    success_data = None

    if request.method == 'POST':
        cart_json = request.POST.get('cart', '[]')
        try:
            cart = json.loads(cart_json)
        except (json.JSONDecodeError, TypeError):
            cart = []

        recorded = []
        last_transaction = None

        for entry in cart:
            item = Item.objects.filter(
                id=entry.get('id'),
                store__business=user_profile.business
            ).first()
            if not item:
                continue

            qty = int(entry.get('qty', 0))
            if qty < 1:
                continue

            if item.current_balance() < qty:
                messages.warning(
                    request,
                    f"Skipped {item.description}: only {item.current_balance()} {item.unit} in stock."
                )
                continue

            last_transaction = Transaction.objects.create(
                item=item,
                type='Issue',
                qty=-qty,
                business=user_profile.business,
            )
            recorded.append({
                'name': item.description,
                'qty': qty,
                'subtotal': float(item.selling_price or 0) * qty,
            })

        if recorded and last_transaction:
            total = sum(r['subtotal'] for r in recorded)

            # Send notification for the sale batch
            try:
                from .notifications import notify_transaction
                daily_count = Transaction.objects.filter(
                    business=user_profile.business,
                    date=date.today()
                ).count()
                notify_transaction(last_transaction, user_profile.business, daily_count, user=request.user)
            except Exception:
                pass

            success_data = json.dumps({'items': recorded, 'total': total})
            messages.success(
                request,
                f"Sale recorded: {len(recorded)} item{'s' if len(recorded) != 1 else ''}, KES {total:,.0f}"
            )

    # Build items with pre-calculated balance
    items_qs = Item.objects.filter(
        store__business=user_profile.business
    ).select_related('store').order_by('description')

    items = []
    for item in items_qs:
        items.append({
            'id': item.id,
            'description': item.description,
            'selling_price': item.selling_price,
            'balance': item.current_balance(),
            'unit': item.unit,
            'store_id': item.store_id,
            'reorder_level': item.reorder_level,
        })

    stores = Store.objects.filter(business=user_profile.business)

    return render(request, 'core/quick_sell.html', {
        'items': items,
        'stores': stores,
        'success_data': success_data,
    })