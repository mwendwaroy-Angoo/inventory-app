from django.shortcuts import render
from django.utils import timezone
from .models import Item, Transaction, Store
from django.contrib.auth.decorators import login_required, permission_required


def home(request):
    all_items = Item.objects.all()
    reorder_items = [item for item in all_items if item.needs_reorder()]
    low_stock_count = len([item for item in all_items if item.current_balance() <= item.reorder_level])
    reorder_count = len(reorder_items)

    context = {
        'today': timezone.now().strftime("%B %d, %Y"),
        'total_items': all_items.count(),
        'low_stock_count': low_stock_count,
        'reorder_count': reorder_count,
        'reorder_items': sorted(reorder_items, key=lambda x: x.current_balance())[:20],  # Top 20 urgent
    }
    return render(request, 'core/home.html', context)

@login_required
def stock_list(request):
    stores = Store.objects.all()
    selected_store_id = request.GET.get('store')  # This is a string or None

    if selected_store_id:
        try:
            selected_store_id = int(selected_store_id)  # Convert to integer
            items = Item.objects.filter(store_id=selected_store_id).order_by('material_no')
        except (ValueError, TypeError):
            items = Item.objects.all().order_by('material_no')  # Fallback if invalid
    else:
        items = Item.objects.all().order_by('material_no')

    context = {
        'items': items,
        'stores': stores,
        'selected_store': selected_store_id if selected_store_id else None,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/stock_list.html', context)
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Item, Transaction

@login_required
def add_transaction(request):
    if request.method == 'POST':
        item_id = request.POST['item']
        trans_type = request.POST['type']
        quantity = int(request.POST['quantity'])
        department = request.POST.get('department', '')
        doc_no = request.POST.get('doc_no', '')

        item = get_object_or_404(Item, id=item_id)

        # Make quantity negative for Issue
        if trans_type == 'Issue':
            quantity = -quantity

        Transaction.objects.create(
            item=item,
            type=trans_type,
            qty=quantity,
            department=department,
            doc_no=doc_no
        )

        messages.success(request, f"{abs(quantity)} {item.unit} of {item.description} recorded as {trans_type.lower()}.")
        return redirect('add_transaction')  # ← This ends the POST block

    # GET request - show form (this is reachable now!)
    items = Item.objects.all().order_by('material_no')
    context = {
        'items': items,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/add_transaction.html', context)
  
@login_required  
def item_detail(request, item_id):
        item = get_object_or_404(Item, id=item_id)
        transactions = item.transactions.all().order_by('-date')  # Latest first
        context = {
            'item': item,
            'transactions': transactions,
            'today': timezone.now().strftime("%B %d, %Y"),
        }
        return render(request, 'core/item_detail.html', context)

@login_required
def transaction_history(request):
    transactions = Transaction.objects.all().select_related('item').order_by('-date')
    context = {
        'transactions': transactions,
        'today': timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, 'core/transaction_history.html', context)

from django.http import HttpResponse
import openpyxl
from openpyxl.utils import get_column_letter

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