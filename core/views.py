from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login
from django.contrib import messages
from django.http import HttpResponse
from .models import Item, Transaction, Store
import openpyxl
from openpyxl.utils import get_column_letter


def home(request):
    context = {
        'today': timezone.now().strftime("%B %d, %Y"),
    }

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


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('home')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def stock_list(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        messages.error(request, "Your account has no business profile. Please complete your setup.")
        return redirect('home')

    stores = Store.objects.filter(business=user_profile.business)
    selected_store_id = request.GET.get('store')

    if selected_store_id:
        try:
            selected_store_id = int(selected_store_id)
            items = Item.objects.filter(store_id=selected_store_id, store__business=user_profile.business).order_by('material_no')
        except (ValueError, TypeError):
            items = Item.objects.filter(store__business=user_profile.business).order_by('material_no')
    else:
        items = Item.objects.filter(store__business=user_profile.business).order_by('material_no')

    context = {
        'items': items,
        'stores': stores,
        'selected_store': selected_store_id if selected_store_id else None,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/stock_list.html', context)


@login_required
def add_transaction(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        messages.error(request, "Your account has no business profile. Please complete your setup.")
        return redirect('home')

    if request.method == 'POST':
        item_id = request.POST['item']
        trans_type = request.POST['type']
        quantity = int(request.POST['quantity'])
        department = request.POST.get('department', '')
        doc_no = request.POST.get('doc_no', '')

        item = get_object_or_404(Item, id=item_id)

        if trans_type == 'Issue':
            quantity = -quantity

        Transaction.objects.create(
            item=item,
            type=trans_type,
            qty=quantity,
            department=department,
            doc_no=doc_no,
            business=user_profile.business,  # fix null business on transactions
        )

        messages.success(request, f"{abs(quantity)} {item.unit} of {item.description} recorded as {trans_type.lower()}.")
        return redirect('add_transaction')

    items = Item.objects.filter(store__business=user_profile.business).order_by('material_no')
    context = {
        'items': items,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/add_transaction.html', context)


@login_required
def item_detail(request, item_id):
    try:
        user_profile = request.user.userprofile
    except Exception:
        messages.error(request, "Your account has no business profile. Please complete your setup.")
        return redirect('home')

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)
    transactions = item.transactions.all().order_by('-date')
    context = {
        'item': item,
        'transactions': transactions,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/item_detail.html', context)


@login_required
def transaction_history(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        messages.error(request, "Your account has no business profile. Please complete your setup.")
        return redirect('home')

    transactions = Transaction.objects.filter(
        item__store__business=user_profile.business
    ).select_related('item').order_by('-date')
    context = {
        'transactions': transactions,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/transaction_history.html', context)

def export_stock_excel(request):
    store_id = request.GET.get('store')
    if store_id:
        items = Item.objects.filter(store_id=store_id)
    else:
        items = Item.objects.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock List"

    columns = ['Material No', 'Description', 'Unit', 'Current Balance', 'Reorder Level', 'Status', 'Store']
    ws.append(columns)

    for item in items:
        status = "OUT OF STOCK" if item.current_balance() <= 0 else "REORDER" if item.needs_reorder() else "AVAILABLE"
        ws.append([
            item.material_no,
            item.description,
            item.unit,
            item.current_balance(),
            item.reorder_level,
            status,
            item.store.name
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=stock_list.xlsx'
    wb.save(response)
    return response