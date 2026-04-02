from django.contrib import admin
from django.urls import path, include
from core.views import (
    home, stock_list, add_transaction, item_detail,
    transaction_history, export_stock_excel, export_transactions_excel,
    manage_items, add_item, edit_item, delete_item,
    manage_stores, customer_list, add_customer, delete_customer,
    ajax_customers, sales_dashboard, export_sales_excel, notifications_list,
    notifications_count, daily_summary_webhook,
)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('business/', include('accounts.urls')),
    path('', home, name='home'),
    path('stock/', stock_list, name='stock_list'),
    path('stock/manage/', manage_items, name='manage_items'),
    path('stock/add/', add_item, name='add_item'),
    path('stock/edit/<int:item_id>/', edit_item, name='edit_item'),
    path('stock/delete/<int:item_id>/', delete_item, name='delete_item'),
    path('stock/stores/', manage_stores, name='manage_stores'),
    path('add-transaction/', add_transaction, name='add_transaction'),
    path('item/<int:item_id>/', item_detail, name='item_detail'),
    path('history/', transaction_history, name='transaction_history'),
    path('export/stock/', export_stock_excel, name='export_stock'),
    path('export/transactions/', export_transactions_excel, name='export_transactions'),
    path('customers/', customer_list, name='customer_list'),
    path('customers/add/', add_customer, name='add_customer'),
    path('customers/delete/<int:customer_id>/', delete_customer, name='delete_customer'),
    path('ajax/customers/', ajax_customers, name='ajax_customers'),
    path('sales/', sales_dashboard, name='sales_dashboard'),
    path('export/sales/', export_sales_excel, name='export_sales'),
    path('notifications/', notifications_list, name='notifications_list'),
    path('notifications/count/', notifications_count, name='notifications_count'),
    path('cron/daily-summary/', daily_summary_webhook, name='daily_summary'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)