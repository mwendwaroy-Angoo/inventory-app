from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum
from datetime import timedelta
import uuid
from django.urls import reverse
from . import tasks as core_tasks

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


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, HasBusiness])
def forecast_api(request):
    """Return revenue history and forecast for the user's business.

    Query params:
    - source: 'transaction' | 'order' | 'both' (default 'both')
    - cadence: 'daily'|'weekly'|'monthly' (default 'daily')
    - horizon: integer periods to forecast (default 30)
    - start / end: optional ISO dates to filter history
    """
    business = get_business(request)
    source = request.query_params.get('source', 'both')
    cadence = request.query_params.get('cadence', 'daily')
    try:
        horizon = int(request.query_params.get('horizon', 30))
    except Exception:
        horizon = 30
    date_from = request.query_params.get('start')
    date_to = request.query_params.get('end')
    product_id = request.query_params.get('product_id') or request.query_params.get('product')
    async_req = request.query_params.get('async') in ('1', 'true', 'True', 'TRUE')
    task_token = request.query_params.get('task')

    import pandas as pd

    # Task status query (polling by token)
    if task_token:
        try:
            from core.models import Forecast
            fc = Forecast.objects.filter(business=business, meta__task=task_token).order_by('-generated_at').first()
            if not fc:
                return Response({'status': 'unknown'}, status=status.HTTP_404_NOT_FOUND)
            status_meta = fc.meta or {}
            state = status_meta.get('status', 'pending')
            if state != 'completed':
                return Response({'status': state, 'meta': status_meta})
            return Response({'status': 'completed', 'history': fc.history or [], 'forecast': fc.forecast or [], 'meta': status_meta})
        except Exception:
            return Response({'error': 'internal'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    parts = []
    # Transactions (POS)
    if source in ('transaction', 'both'):
        tx_qs = Transaction.objects.filter(type='Issue', business=business)
        if date_from:
            tx_qs = tx_qs.filter(date__gte=date_from)
        if date_to:
            tx_qs = tx_qs.filter(date__lte=date_to)
        if product_id:
            try:
                tx_qs = tx_qs.filter(item_id=int(product_id))
            except Exception:
                pass
        tx_rows = list(tx_qs.values('date', 'qty', 'item__selling_price'))
        if tx_rows:
            df_tx = pd.DataFrame(tx_rows)
            df_tx['date'] = pd.to_datetime(df_tx['date'])
            df_tx['item__selling_price'] = pd.to_numeric(df_tx['item__selling_price'], errors='coerce').fillna(0.0)
            df_tx['revenue'] = df_tx['qty'].abs() * df_tx['item__selling_price']
            df_tx_group = df_tx.groupby(df_tx['date'].dt.floor('D')).agg({'revenue': 'sum'}).reset_index()
            parts.append(df_tx_group)

    # Orders (marketplace)
    if source in ('order', 'both'):
        from .models import Order
        ord_qs = Order.objects.filter(business=business, status__in=['paid', 'ready', 'completed'])
        if date_from:
            ord_qs = ord_qs.filter(created_at__date__gte=date_from)
        if date_to:
            ord_qs = ord_qs.filter(created_at__date__lte=date_to)
        ord_rows = list(ord_qs.values('created_at', 'total_amount'))
        if ord_rows:
            df_ord = pd.DataFrame(ord_rows)
            df_ord['date'] = pd.to_datetime(df_ord['created_at']).dt.floor('D')
            df_ord['total_amount'] = pd.to_numeric(df_ord['total_amount'], errors='coerce').fillna(0.0)
            df_ord_group = df_ord.groupby('date').agg({'total_amount': 'sum'}).reset_index()
            df_ord_group = df_ord_group.rename(columns={'total_amount': 'revenue'})
            parts.append(df_ord_group)

    if parts:
        df_hist = pd.concat(parts).groupby('date', as_index=False).agg({'revenue': 'sum'})
    else:
        df_hist = pd.DataFrame(columns=['date', 'revenue'])

    # If no history and not requesting async, return empty arrays
    if df_hist.empty and not async_req:
        return Response({'history': [], 'forecast': [], 'meta': {'cadence': cadence, 'horizon': horizon, 'source': source}})

    # Check for a cached persisted Forecast that matches parameters (including product/date when present)
    try:
        from core.models import Forecast
        cached_qs = Forecast.objects.filter(business=business, source=source, cadence=cadence, horizon=horizon)
        if product_id:
            try:
                cached_qs = cached_qs.filter(meta__product_id=int(product_id))
            except Exception:
                cached_qs = cached_qs.filter(meta__product_id=str(product_id))
        if date_from:
            cached_qs = cached_qs.filter(meta__start=date_from)
        if date_to:
            cached_qs = cached_qs.filter(meta__end=date_to)
        cached = cached_qs.order_by('-generated_at').first()
        if cached:
            return Response({
                'history': cached.history or [],
                'forecast': cached.forecast or [],
                'meta': {**{'cadence': cadence, 'horizon': horizon, 'source': source, 'cached': True}, **(cached.meta or {})}
            })
    except Exception:
        pass

    # If client asked for async, create a placeholder Forecast, enqueue work, and return task token
    if async_req:
        try:
            from django.apps import apps
            Forecast = None
            try:
                Forecast = apps.get_model('core', 'Forecast')
            except Exception:
                Forecast = None

            token = str(uuid.uuid4())
            fobj = None
            if Forecast:
                fobj = Forecast.objects.create(
                    business=business,
                    source=source,
                    cadence=cadence,
                    horizon=horizon,
                    history=[],
                    forecast=[],
                    meta={'task': token, 'status': 'pending', 'product_id': int(product_id) if product_id else None, 'start': date_from, 'end': date_to}
                )

            # schedule background worker (Celery when available)
            if hasattr(core_tasks.forecast_async_task, 'delay'):
                core_tasks.forecast_async_task.delay(fobj.id if fobj else None, business.id, source, cadence, horizon, date_from, date_to, int(product_id) if product_id else None)
            else:
                core_tasks.forecast_async_task(fobj.id if fobj else None, business.id, source, cadence, horizon, date_from, date_to, int(product_id) if product_id else None)

            status_url = request.build_absolute_uri(reverse('api-forecast')) + f'?task={token}'
            return Response({'task': token, 'status_url': status_url}, status=status.HTTP_202_ACCEPTED)
        except Exception:
            pass

    # Synchronous path: resample and forecast now
    from forecast import forecast as fcmod

    s = fcmod.resample_series(df_hist, cadence=cadence)
    forecast_series = fcmod.fit_ets_forecast(s, steps=horizon, cadence=cadence)

    history_list = []
    for d, v in zip(s.index.to_pydatetime(), s.values.tolist()):
        history_list.append({'date': d.isoformat(), 'revenue': float(v)})

    forecast_list = []
    for d, v in zip(forecast_series.index.to_pydatetime(), forecast_series.values.tolist()):
        forecast_list.append({'date': d.isoformat(), 'forecast': float(v)})

    return Response({
        'history': history_list,
        'forecast': forecast_list,
        'meta': {'cadence': cadence, 'horizon': horizon, 'source': source}
    })
