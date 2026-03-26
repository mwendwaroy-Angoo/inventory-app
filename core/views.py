from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from .models import Item, Transaction, Store, BusinessType, Customer
from .forms import ItemForm
import openpyxl


# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_user_profile(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


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
        except AttributeError:
            context['error'] = "Profile not found. Please contact support."
    else:
        context['guest'] = True

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

        # Handle new customer creation
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
            quantity = -quantity

        Transaction.objects.create(
            item=item,
            type=trans_type,
            qty=quantity,
            recipient=recipient,
            invoice_no=invoice_no,
            business=user_profile.business,
        )

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
        form = ItemForm(request.POST, business=user_profile.business)
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
        form = ItemForm(business=user_profile.business)

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
        form = ItemForm(request.POST, instance=item, business=user_profile.business)
        if form.is_valid():
            form.save()
            messages.success(request, f"'{item.description}' updated successfully.")
            return redirect('manage_items')
    else:
        form = ItemForm(instance=item, business=user_profile.business)

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