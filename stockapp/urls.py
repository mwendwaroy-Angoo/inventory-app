from django.contrib import admin
from django.urls import path, include
from core.views import home, stock_list, add_transaction, item_detail, transaction_history, export_stock_excel

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),  # For login/logout
    path('', home, name='home'),  # This makes home.html the homepage
    path('stock/', stock_list, name='stock_list'),  # New URL pattern for stock list
    path('add-transaction/', add_transaction, name='add_transaction'),  # New URL pattern for adding transactions
    path('item/<int:item_id>/', item_detail, name='item_detail'),  # Detail view for each item
    path('history/', transaction_history, name='transaction_history'),
    path('export/', export_stock_excel, name='export_stock'),
]
