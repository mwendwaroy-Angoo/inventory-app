from django.urls import path
from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/add/', views.add_staff, name='add_staff'),
    path('staff/edit/<int:user_id>/', views.edit_staff, name='edit_staff'),
    path('staff/delete/<int:user_id>/', views.delete_staff, name='delete_staff'),
    path('ajax/subcounties/', views.load_subcounties, name='load_subcounties'),
    path('ajax/wards/', views.load_wards, name='load_wards'),
]