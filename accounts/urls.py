from django.urls import path
from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('staff/add/', views.add_staff, name='add_staff'),
    path('staff/', views.staff_list, name='staff_list'),
]