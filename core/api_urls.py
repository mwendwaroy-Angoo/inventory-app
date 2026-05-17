from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from .api_views import (
    StoreViewSet, ItemViewSet, TransactionViewSet,
    NotificationViewSet, CustomerViewSet,
    business_summary, quick_sell_api,
)

router = DefaultRouter()
router.register(r'stores', StoreViewSet, basename='store')
router.register(r'items', ItemViewSet, basename='item')
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'customers', CustomerViewSet, basename='customer')

urlpatterns = [
    path('', include(router.urls)),
    path('summary/', business_summary, name='api-summary'),
    path('quick-sell/', quick_sell_api, name='api-quick-sell'),
    path('auth/token/', obtain_auth_token, name='api-token'),
]
