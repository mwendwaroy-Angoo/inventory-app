from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum
from datetime import timedelta

from .models import Item, Transaction, Store, Notification, Customer
from .serializers import (
    ItemSerializer, ItemWriteSerializer,
    TransactionSerializer, TransactionWriteSerializer,
    StoreSerializer, NotificationSerializer,
    CustomerSerializer, BusinessSummarySerializer,
)
from .notifications import notify_transaction


# ── PERMISSIONS ──────────────────────────────────────────────────────────────

class IsOwner(permissions.BasePermission):
    """Only business owners can write; staff can read."""
    def has_permission(self, request, view):
        profile = getattr(request.user, 'userprofile', None)
        if not profile:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return profile.is_owner


class HasBusiness(permissions.BasePermission):
    """User must be linked to a business."""
    def has_permission(self, request, view):
        profile = getattr(request.user, 'userprofile', None)
        return profile and profile.business is not None


# ── HELPER ───────────────────────────────────────────────────────────────────

def get_business(request):
    return request.user.userprofile.business


# ── VIEWSETS ─────────────────────────────────────────────────────────────────

class StoreViewSet(viewsets.ModelViewSet):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsOwner]

    def get_queryset(self):
        return Store.objects.filter(business=get_business(self.request))

    def perform_create(self, serializer):
        serializer.save(business=get_business(self.request))


class ItemViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsOwner]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ItemWriteSerializer
        return ItemSerializer

    def get_queryset(self):
        qs = Item.objects.filter(business=get_business(self.request)).select_related('store')
        # Optional filters
        store_id = self.request.query_params.get('store')
        if store_id:
            qs = qs.filter(store_id=store_id)
        low_stock = self.request.query_params.get('low_stock')
        if low_stock == '1':
            ids = [i.id for i in qs if i.needs_reorder()]
            qs = qs.filter(id__in=ids)
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(description__icontains=search)
        return qs

    def perform_create(self, serializer):
        serializer.save(business=get_business(self.request))


class TransactionViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, HasBusiness]
    http_method_names = ['get', 'post', 'head', 'options']  # no edit/delete

    def get_serializer_class(self):
        if self.action == 'create':
            return TransactionWriteSerializer
        return TransactionSerializer

    def get_queryset(self):
        qs = Transaction.objects.filter(
            business=get_business(self.request)
        ).select_related('item')
        # Optional filters
        item_id = self.request.query_params.get('item')
        if item_id:
            qs = qs.filter(item_id=item_id)
        txn_type = self.request.query_params.get('type')
        if txn_type in ('Issue', 'Receipt'):
            qs = qs.filter(type=txn_type)
        date_from = self.request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = self.request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs.order_by('-date', '-id')

    def perform_create(self, serializer):
        business = get_business(self.request)
        transaction = serializer.save(business=business)
        # Count today's transactions for notification
        today = timezone.localtime(timezone.now()).date()
        daily_count = Transaction.objects.filter(
            business=business, date=today
        ).count()
        try:
            notify_transaction(transaction, business, daily_count, user=self.request.user)
        except Exception:
            pass  # Don't fail the API call if notification fails


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=False, methods=['patch'])
    def mark_all_read(self, request):
        count = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'marked': count})


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsOwner]

    def get_queryset(self):
        return Customer.objects.filter(business=get_business(self.request))

    def perform_create(self, serializer):
        serializer.save(business=get_business(self.request))


# ── STANDALONE ENDPOINTS ─────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, HasBusiness])
def business_summary(request):
    """Dashboard summary for the user's business."""
    business = get_business(request)
    today = timezone.localtime(timezone.now()).date()

    items = Item.objects.filter(business=business)
    today_txns = Transaction.objects.filter(business=business, date=today, type='Issue')

    total_stock_value = sum(i.stock_value() for i in items)
    low_stock_count = sum(1 for i in items if i.needs_reorder())
    today_revenue = sum(t.revenue() for t in today_txns)
    today_profit = sum(t.profit() for t in today_txns)

    data = {
        'total_items': items.count(),
        'total_stock_value': round(total_stock_value, 2),
        'low_stock_count': low_stock_count,
        'today_sales': today_txns.count(),
        'today_revenue': round(today_revenue, 2),
        'today_profit': round(today_profit, 2),
        'stores_count': Store.objects.filter(business=business).count(),
        'staff_count': business.users.filter(role='staff').count(),
    }
    serializer = BusinessSummarySerializer(data)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, HasBusiness])
def quick_sell_api(request):
    """Batch sale endpoint — accepts a cart of items.

    Expects JSON: {"items": [{"item_id": 1, "qty": 2}, ...]}
    """
    business = get_business(request)
    cart = request.data.get('items', [])

    if not cart:
        return Response({'error': 'Cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

    results = []
    today = timezone.localtime(timezone.now()).date()

    for entry in cart:
        item_id = entry.get('item_id')
        qty = entry.get('qty', 1)

        try:
            item = Item.objects.get(id=item_id, business=business)
        except Item.DoesNotExist:
            results.append({'item_id': item_id, 'error': 'Item not found'})
            continue

        if qty <= 0:
            results.append({'item_id': item_id, 'error': 'Quantity must be positive'})
            continue

        if item.current_balance() < qty:
            results.append({
                'item_id': item_id,
                'error': f'Not enough stock. Available: {item.current_balance()}'
            })
            continue

        txn = Transaction.objects.create(
            item=item,
            date=today,
            type='Issue',
            qty=-qty,
            business=business,
        )

        daily_count = Transaction.objects.filter(business=business, date=today).count()
        try:
            notify_transaction(txn, business, daily_count, user=request.user)
        except Exception:
            pass

        results.append({
            'item_id': item.id,
            'description': item.description,
            'qty_sold': qty,
            'unit_price': float(item.selling_price) if item.selling_price else 0,
            'total': float(item.selling_price or 0) * qty,
            'new_balance': item.current_balance(),
        })

    total_revenue = sum(r.get('total', 0) for r in results if 'error' not in r)
    return Response({
        'sales': results,
        'total_revenue': round(total_revenue, 2),
    }, status=status.HTTP_201_CREATED)
