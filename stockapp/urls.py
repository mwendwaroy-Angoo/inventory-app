from django.contrib import admin
from django.urls import path, include
from core.views import (
    home, stock_list, add_transaction, item_detail,
    transaction_history, export_stock_excel,
    manage_items, add_item, edit_item, delete_item, manage_stores
)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('business/', include('accounts.urls')),
    path('', home, name='home'),
    path('stock/', stock_list, name='stock_list'),
    path('stock/stores/', manage_stores, name='manage_stores'),
    path('stock/manage/', manage_items, name='manage_items'),
    path('stock/add/', add_item, name='add_item'),
    path('stock/edit/<int:item_id>/', edit_item, name='edit_item'),
    path('stock/delete/<int:item_id>/', delete_item, name='delete_item'),
    path('add-transaction/', add_transaction, name='add_transaction'),
    path('item/<int:item_id>/', item_detail, name='item_detail'),
    path('history/', transaction_history, name='transaction_history'),
    path('export/', export_stock_excel, name='export_stock'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)