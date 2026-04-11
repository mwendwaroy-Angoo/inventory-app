from django.urls import path
from . import views

urlpatterns = [
    path('role-redirect/', views.role_redirect, name='role_redirect'),
    path('signup/', views.signup, name='signup'),
    path('rider/signup/', views.rider_signup, name='rider_signup'),
    path('rider/dashboard/', views.rider_dashboard, name='rider_dashboard'),
    path('rider/toggle-availability/', views.rider_toggle_availability, name='rider_toggle_availability'),
    path('rider/active-deliveries/', views.rider_active_deliveries, name='rider_active_deliveries'),
    path('rider/delivery-history/', views.rider_delivery_history, name='rider_delivery_history'),
    path('rider/earnings/', views.rider_earnings, name='rider_earnings'),
    path('supplier/signup/', views.supplier_signup, name='supplier_signup'),
    path('supplier/dashboard/', views.supplier_dashboard, name='supplier_dashboard'),
    path('supplier/clients/', views.supplier_clients, name='supplier_clients'),
    path('edit/', views.edit_business, name='edit_business'),
    path('payment-settings/', views.payment_settings, name='payment_settings'),
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/add/', views.add_staff, name='add_staff'),
    path('staff/edit/<int:user_id>/', views.edit_staff, name='edit_staff'),
    path('staff/delete/<int:user_id>/', views.delete_staff, name='delete_staff'),
    path('staff/reset-password/<int:user_id>/', views.reset_staff_password, name='reset_staff_password'),
    path('ajax/subcounties/', views.load_subcounties, name='load_subcounties'),
    path('ajax/wards/', views.load_wards, name='load_wards'),
    path('tutorial/dismiss/', views.tutorial_dismiss, name='tutorial_dismiss'),
    path('tutorial/reset/', views.tutorial_reset, name='tutorial_reset'),
    path('change-language/', views.change_language, name='change_language'),
    path('delete-account/', views.delete_account, name='delete_account'),
]