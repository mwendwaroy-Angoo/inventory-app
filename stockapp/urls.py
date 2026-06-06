from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from accounts.forms import LocalizedAuthenticationForm
from accounts.views import logout_view
from core.views import (
    home,
    stock_list,
    add_transaction,
    item_detail,
    create_po_from_item,
    purchase_orders_list,
    purchase_order_create,
    purchase_order_edit,
    purchase_order_detail,
    receive_goods,
    goods_receipt_detail,
    item_recommendation,
    item_search,
    item_cost_price,
    transaction_history,
    export_stock_excel,
    export_transactions_excel,
    manage_items,
    add_item,
    edit_item,
    delete_item,
    manage_stores,
    customer_list,
    add_customer,
    delete_customer,
    ajax_customers,
    sales_dashboard,
    export_sales_excel,
    notifications_list,
    notifications_count,
    daily_summary_webhook,
    quick_sell,
    offline,
    health_check,
    manifest_json,
    service_worker,
    forecast_api,
    item_portion_presets,
)
from core.ussd import ussd_callback
from core.customer_ussd import customer_ussd_callback
from core.mpesa_views import (
    mpesa_callback,
    stk_push_view,
    payment_status,
    c2b_validation,
    c2b_confirmation,
    pending_prompts,
    confirm_prompt,
    dismiss_prompt,
)
from core.marketplace_views import (
    shop_home,
    storefront,
    place_order,
    track_order,
    pay_order,
    order_list,
    update_order_status,
    fulfillment_list,
    assign_rider,
    supplier_list,
    add_supplier,
    edit_supplier,
    remove_supplier,
)
from core.procurement_views import (
    procurement_list_owner,
    create_procurement,
    procurement_detail,
    evaluate_bids,
    award_bid,
    confirm_delivery,
    confirm_payment,
    procurement_browse,
    submit_bid,
    my_bids,
    apply_as_supplier,
    supplier_applications,
    review_application,
    browse_businesses,
)
from core.feedback_views import (
    leave_customer_feedback,
    business_reviews,
    supplier_feedback,
    my_feedback,
    rate_rider,
    rider_performance_view,
    supplier_performance_view,
)
from core.whatsapp_bot import whatsapp_webhook
from core.analytics_views import (
    analytics_dashboard,
    analytics_api,
    expense_list,
    expense_add,
    expense_edit,
    expense_delete,
    capital_investment_list,
    capital_investment_edit,
    capital_investment_delete,
    compliance_checklist,
    county_heatmap,
    revenue_target_settings,
    revenue_target_progress,
)
from core.onboarding_views import mark_section_seen
from core.restricted_items_views import (
    request_sale_approval, pending_approvals,
    decide_approval, approval_status,
)
from core.debt_views import (
    debt_dashboard,
    customer_debt_profile,
    record_debt_payment,
    send_debt_reminder,
    toggle_credit_approval,
    update_customer_credit_settings,
)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(authentication_form=LocalizedAuthenticationForm),
        name="login",
    ),
    path("accounts/logout/", logout_view, name="logout"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("business/", include("accounts.urls")),
    path("", home, name="home"),
    path("health/", health_check, name="health_check"),
    path("offline/", offline, name="offline"),
    # ── PWA ──
    path("manifest.json", manifest_json, name="manifest_json"),
    path("sw.js", service_worker, name="service_worker"),
    path("stock/", stock_list, name="stock_list"),
    path("stock/manage/", manage_items, name="manage_items"),
    path("stock/add/", add_item, name="add_item"),
    path("stock/edit/<int:item_id>/", edit_item, name="edit_item"),
    path("stock/delete/<int:item_id>/", delete_item, name="delete_item"),
    path("stock/item/<int:item_id>/presets/", item_portion_presets, name="item_portion_presets"),
    path("stock/stores/", manage_stores, name="manage_stores"),
    # ── Restricted Items / Sale Approvals ────────────────────────────────────
    path("approvals/", pending_approvals, name="pending_approvals"),
    path("approvals/<int:approval_id>/decide/", decide_approval, name="decide_approval"),
    path("approvals/<int:approval_id>/status/", approval_status, name="approval_status"),
    path("stock/item/<int:item_id>/request-sale/", request_sale_approval, name="request_sale_approval"),
    path("add-transaction/", add_transaction, name="add_transaction"),
    path("quick-sell/", quick_sell, name="quick_sell"),
    path("item/<int:item_id>/", item_detail, name="item_detail"),
    path(
        "po/create-from-item/<int:item_id>/",
        create_po_from_item,
        name="create_po_from_item",
    ),
    path(
        "api/item/<int:item_id>/recommendation/",
        item_recommendation,
        name="item_recommendation",
    ),
    path("api/items/search/", item_search, name="item_search"),
    path(
        "api/items/<int:item_id>/cost-price/", item_cost_price, name="item_cost_price"
    ),
    path("purchase-orders/", purchase_orders_list, name="purchase_orders_list"),
    path(
        "purchase-orders/create/", purchase_order_create, name="purchase_order_create"
    ),
    path(
        "purchase-orders/<int:po_id>/edit/",
        purchase_order_edit,
        name="purchase_order_edit",
    ),
    path("purchase-orders/<int:po_id>/receive/", receive_goods, name="receive_goods"),
    path(
        "purchase-orders/<int:po_id>/",
        purchase_order_detail,
        name="purchase_order_detail",
    ),
    path(
        "goods-receipts/<int:receipt_id>/",
        goods_receipt_detail,
        name="goods_receipt_detail",
    ),
    path("history/", transaction_history, name="transaction_history"),
    path("export/stock/", export_stock_excel, name="export_stock"),
    path("export/transactions/", export_transactions_excel, name="export_transactions"),
    path("customers/", customer_list, name="customer_list"),
    path("customers/add/", add_customer, name="add_customer"),
    path(
        "customers/delete/<int:customer_id>/", delete_customer, name="delete_customer"
    ),
    path("ajax/customers/", ajax_customers, name="ajax_customers"),
    # ── Debt Tracker ──────────────────────────────────────────────────────────
    path("onboarding/seen/", mark_section_seen, name="mark_section_seen"),
    path("debt/", debt_dashboard, name="debt_dashboard"),
    path("debt/<int:customer_id>/", customer_debt_profile, name="customer_debt_profile"),
    path("debt/<int:customer_id>/payment/", record_debt_payment, name="record_debt_payment"),
    path("debt/<int:customer_id>/reminder/", send_debt_reminder, name="send_debt_reminder"),
    path("debt/<int:customer_id>/toggle-credit/", toggle_credit_approval, name="toggle_credit_approval"),
    path("debt/<int:customer_id>/settings/", update_customer_credit_settings, name="update_customer_credit_settings"),
    # ── Revenue Targets ───────────────────────────────────────────────────────
    path("analytics/targets/", revenue_target_settings, name="revenue_target_settings"),
    path("analytics/targets/progress/", revenue_target_progress, name="revenue_target_progress"),
    path("sales/", sales_dashboard, name="sales_dashboard"),
    path("export/sales/", export_sales_excel, name="export_sales"),
    path("notifications/", notifications_list, name="notifications"),
    path("notifications/count/", notifications_count, name="notifications_count"),
    path("cron/daily-summary/", daily_summary_webhook, name="daily_summary"),
    path("ussd/callback/", ussd_callback, name="ussd_callback"),
    path("ussd/customer/", customer_ussd_callback, name="customer_ussd"),
    path("api/v1/", include("core.api_urls")),
    # ── M-Pesa ──
    path("mpesa/callback/", mpesa_callback, name="mpesa_callback"),
    path("mpesa/stk-push/", stk_push_view, name="stk_push"),
    path("mpesa/status/<int:payment_id>/", payment_status, name="payment_status"),
    path("mpesa/c2b/validation/", c2b_validation, name="c2b_validation"),
    path("mpesa/c2b/confirmation/", c2b_confirmation, name="c2b_confirmation"),
    path("mpesa/prompts/", pending_prompts, name="pending_prompts"),
    path(
        "mpesa/prompt/<int:prompt_id>/confirm/", confirm_prompt, name="confirm_prompt"
    ),
    path(
        "mpesa/prompt/<int:prompt_id>/dismiss/", dismiss_prompt, name="dismiss_prompt"
    ),
    # ── Customer Marketplace ──
    path("shop/", shop_home, name="shop_home"),
    path("shop/<int:business_id>/", storefront, name="storefront"),
    path("shop/<int:business_id>/order/", place_order, name="place_order"),
    path("shop/order/<str:order_number>/", track_order, name="track_order"),
    path("shop/order/<str:order_number>/pay/", pay_order, name="pay_order"),
    # ── Owner: Order Management ──
    path("orders/", order_list, name="order_list"),
    path(
        "orders/<int:order_id>/update-status/",
        update_order_status,
        name="update_order_status",
    ),
    # ── Staff: Order Fulfillment ──
    path("fulfillment/", fulfillment_list, name="fulfillment_list"),
    path("fulfillment/<int:order_id>/assign-rider/", assign_rider, name="assign_rider"),
    # ── Owner: Supplier Management ──
    path("suppliers/", supplier_list, name="supplier_list"),
    path("suppliers/add/", add_supplier, name="add_supplier"),
    path("suppliers/<int:link_id>/edit/", edit_supplier, name="edit_supplier"),
    path("suppliers/<int:link_id>/remove/", remove_supplier, name="remove_supplier"),
    # ── Procurement System ──
    path("procurement/", procurement_list_owner, name="procurement_list_owner"),
    path("procurement/create/", create_procurement, name="create_procurement"),
    path("procurement/<int:pk>/", procurement_detail, name="procurement_detail"),
    path("procurement/<int:pk>/evaluate/", evaluate_bids, name="evaluate_bids"),
    path("procurement/bid/<int:bid_id>/award/", award_bid, name="award_bid"),
    path("procurement/browse/", procurement_browse, name="procurement_browse"),
    path("procurement/<int:pk>/bid/", submit_bid, name="submit_bid"),
    path("procurement/my-bids/", my_bids, name="my_bids"),
    path(
        "procurement/bid/<int:bid_id>/confirm-delivery/",
        confirm_delivery,
        name="confirm_delivery",
    ),
    path(
        "procurement/bid/<int:bid_id>/confirm-payment/",
        confirm_payment,
        name="confirm_payment",
    ),
    path(
        "procurement/apply/<int:business_id>/",
        apply_as_supplier,
        name="apply_as_supplier",
    ),
    path(
        "procurement/applications/", supplier_applications, name="supplier_applications"
    ),
    path(
        "procurement/applications/<int:app_id>/review/",
        review_application,
        name="review_application",
    ),
    path("procurement/businesses/", browse_businesses, name="browse_businesses"),
    # ── Feedback & Reviews ──
    path(
        "feedback/order/<str:order_number>/",
        leave_customer_feedback,
        name="leave_customer_feedback",
    ),
    path(
        "feedback/business/<int:business_id>/",
        business_reviews,
        name="business_reviews",
    ),
    path(
        "feedback/supplier/<int:link_id>/", supplier_feedback, name="supplier_feedback"
    ),
    path("feedback/", my_feedback, name="my_feedback"),
    path("feedback/rider/<str:order_number>/", rate_rider, name="rate_rider"),
    path(
        "feedback/rider-performance/<int:rider_id>/",
        rider_performance_view,
        name="rider_performance",
    ),
    path(
        "feedback/supplier-performance/<int:business_id>/",
        supplier_performance_view,
        name="supplier_performance",
    ),
    # ── WhatsApp Bot ──
    path("whatsapp/webhook/", whatsapp_webhook, name="whatsapp_webhook"),
    # ── Business Expenses ──
    path("analytics/expenses/", expense_list, name="expense_list"),
    path("analytics/expenses/add/", expense_add, name="expense_add"),
    path(
        "analytics/expenses/<int:expense_id>/edit/", expense_edit, name="expense_edit"
    ),
    path(
        "analytics/expenses/<int:expense_id>/delete/",
        expense_delete,
        name="expense_delete",
    ),
    # ── Capital Investments ──
    path("analytics/capital/", capital_investment_list, name="capital_investment_list"),
    path(
        "analytics/capital/<int:investment_id>/edit/",
        capital_investment_edit,
        name="capital_investment_edit",
    ),
    path(
        "analytics/capital/<int:investment_id>/delete/",
        capital_investment_delete,
        name="capital_investment_delete",
    ),
    # ── Compliance & Licensing ──
    path("analytics/compliance/", compliance_checklist, name="compliance_checklist"),
    # ── Analytics ──
    path("analytics/heatmap/", county_heatmap, name="county_heatmap"),
    path("analytics/", analytics_dashboard, name="analytics"),
    path("analytics/forecast/", forecast_api, name="forecast_api"),
    path("api/v1/analytics/trends/", analytics_api, name="analytics_api"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
