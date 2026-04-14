from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from accounts.forms import LocalizedAuthenticationForm
from accounts.views import logout_view
from core.views import (
    home, stock_list, add_transaction, item_detail,
    create_po_from_item,
    purchase_orders_list, purchase_order_create, purchase_order_edit, purchase_order_detail,
    item_recommendation, item_search,
    transaction_history, export_stock_excel, export_transactions_excel,
    manage_items, add_item, edit_item, delete_item,
    manage_stores, customer_list, add_customer, delete_customer,
    ajax_customers, sales_dashboard, export_sales_excel, notifications_list,
    notifications_count, daily_summary_webhook, quick_sell, offline,
)
from core.ussd import ussd_callback
from core.customer_ussd import customer_ussd_callback
from core.mpesa_views import (
    mpesa_callback, stk_push_view, payment_status,
    c2b_validation, c2b_confirmation,
    pending_prompts, confirm_prompt, dismiss_prompt,
)
from core.marketplace_views import (
    shop_home, storefront, place_order, track_order, pay_order,
    order_list, update_order_status,
    fulfillment_list, assign_rider,
    supplier_list, add_supplier, edit_supplier, remove_supplier,
)
from core.procurement_views import (
    procurement_list_owner, create_procurement, procurement_detail,
    evaluate_bids, award_bid,
    procurement_browse, submit_bid, my_bids,
    apply_as_supplier, supplier_applications, review_application,
    browse_businesses,
)
from core.feedback_views import (
    leave_customer_feedback, business_reviews,
    supplier_feedback, my_feedback,
    rate_rider, rider_performance_view, supplier_performance_view,
)
from core.whatsapp_bot import whatsapp_webhook
from core.analytics_views import analytics_dashboard, analytics_api
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', auth_views.LoginView.as_view(authentication_form=LocalizedAuthenticationForm), name='login'),
    path('accounts/logout/', logout_view, name='logout'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('business/', include('accounts.urls')),
    path('', home, name='home'),
    path('offline/', offline, name='offline'),
    path('stock/', stock_list, name='stock_list'),
    path('stock/manage/', manage_items, name='manage_items'),
    path('stock/add/', add_item, name='add_item'),
    path('stock/edit/<int:item_id>/', edit_item, name='edit_item'),
    path('stock/delete/<int:item_id>/', delete_item, name='delete_item'),
    path('stock/stores/', manage_stores, name='manage_stores'),
    path('add-transaction/', add_transaction, name='add_transaction'),
    path('quick-sell/', quick_sell, name='quick_sell'),
    path('item/<int:item_id>/', item_detail, name='item_detail'),
    path('po/create-from-item/<int:item_id>/', create_po_from_item, name='create_po_from_item'),
    path('api/item/<int:item_id>/recommendation/', item_recommendation, name='item_recommendation'),
    path('api/items/search/', item_search, name='item_search'),
    path('purchase-orders/', purchase_orders_list, name='purchase_orders_list'),
    path('purchase-orders/create/', purchase_order_create, name='purchase_order_create'),
    path('purchase-orders/<int:po_id>/edit/', purchase_order_edit, name='purchase_order_edit'),
    path('purchase-orders/<int:po_id>/', purchase_order_detail, name='purchase_order_detail'),
    path('history/', transaction_history, name='transaction_history'),
    path('export/stock/', export_stock_excel, name='export_stock'),
    path('export/transactions/', export_transactions_excel, name='export_transactions'),
    path('customers/', customer_list, name='customer_list'),
    path('customers/add/', add_customer, name='add_customer'),
    path('customers/delete/<int:customer_id>/', delete_customer, name='delete_customer'),
    path('ajax/customers/', ajax_customers, name='ajax_customers'),
    path('sales/', sales_dashboard, name='sales_dashboard'),
    path('export/sales/', export_sales_excel, name='export_sales'),
    path('notifications/', notifications_list, name='notifications'),
    path('notifications/count/', notifications_count, name='notifications_count'),
    path('cron/daily-summary/', daily_summary_webhook, name='daily_summary'),
    path('ussd/callback/', ussd_callback, name='ussd_callback'),
    path('ussd/customer/', customer_ussd_callback, name='customer_ussd'),
    path('api/v1/', include('core.api_urls')),

    # ── M-Pesa ──
    path('mpesa/callback/', mpesa_callback, name='mpesa_callback'),
    path('mpesa/stk-push/', stk_push_view, name='stk_push'),
    path('mpesa/status/<int:payment_id>/', payment_status, name='payment_status'),
    path('mpesa/c2b/validation/', c2b_validation, name='c2b_validation'),
    path('mpesa/c2b/confirmation/', c2b_confirmation, name='c2b_confirmation'),
    path('mpesa/prompts/', pending_prompts, name='pending_prompts'),
    path('mpesa/prompt/<int:prompt_id>/confirm/', confirm_prompt, name='confirm_prompt'),
    path('mpesa/prompt/<int:prompt_id>/dismiss/', dismiss_prompt, name='dismiss_prompt'),

    # ── Customer Marketplace ──
    path('shop/', shop_home, name='shop_home'),
    path('shop/<int:business_id>/', storefront, name='storefront'),
    path('shop/<int:business_id>/order/', place_order, name='place_order'),
    path('shop/order/<str:order_number>/', track_order, name='track_order'),
    path('shop/order/<str:order_number>/pay/', pay_order, name='pay_order'),

    # ── Owner: Order Management ──
    path('orders/', order_list, name='order_list'),
    path('orders/<int:order_id>/update-status/', update_order_status, name='update_order_status'),

    # ── Staff: Order Fulfillment ──
    path('fulfillment/', fulfillment_list, name='fulfillment_list'),
    path('fulfillment/<int:order_id>/assign-rider/', assign_rider, name='assign_rider'),

    # ── Owner: Supplier Management ──
    path('suppliers/', supplier_list, name='supplier_list'),
    path('suppliers/add/', add_supplier, name='add_supplier'),
    path('suppliers/<int:link_id>/edit/', edit_supplier, name='edit_supplier'),
    path('suppliers/<int:link_id>/remove/', remove_supplier, name='remove_supplier'),

    # ── Procurement System ──
    path('procurement/', procurement_list_owner, name='procurement_list_owner'),
    path('procurement/create/', create_procurement, name='create_procurement'),
    path('procurement/<int:pk>/', procurement_detail, name='procurement_detail'),
    path('procurement/<int:pk>/evaluate/', evaluate_bids, name='evaluate_bids'),
    path('procurement/bid/<int:bid_id>/award/', award_bid, name='award_bid'),
    path('procurement/browse/', procurement_browse, name='procurement_browse'),
    path('procurement/<int:pk>/bid/', submit_bid, name='submit_bid'),
    path('procurement/my-bids/', my_bids, name='my_bids'),
    path('procurement/apply/<int:business_id>/', apply_as_supplier, name='apply_as_supplier'),
    path('procurement/applications/', supplier_applications, name='supplier_applications'),
    path('procurement/applications/<int:app_id>/review/', review_application, name='review_application'),
    path('procurement/businesses/', browse_businesses, name='browse_businesses'),

    # ── Feedback & Reviews ──
    path('feedback/order/<str:order_number>/', leave_customer_feedback, name='leave_customer_feedback'),
    path('feedback/business/<int:business_id>/', business_reviews, name='business_reviews'),
    path('feedback/supplier/<int:link_id>/', supplier_feedback, name='supplier_feedback'),
    path('feedback/', my_feedback, name='my_feedback'),
    path('feedback/rider/<str:order_number>/', rate_rider, name='rate_rider'),
    path('feedback/rider-performance/<int:rider_id>/', rider_performance_view, name='rider_performance'),
    path('feedback/supplier-performance/<int:business_id>/', supplier_performance_view, name='supplier_performance'),

    # ── WhatsApp Bot ──
    path('whatsapp/webhook/', whatsapp_webhook, name='whatsapp_webhook'),

    # ── Analytics ──
    path('analytics/', analytics_dashboard, name='analytics'),
    path('api/v1/analytics/trends/', analytics_api, name='analytics_api'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)