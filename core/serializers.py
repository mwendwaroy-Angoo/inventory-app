from rest_framework import serializers
from .models import Item, Transaction, Store, Notification, Customer
from accounts.models import Business


class StoreSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = ['id', 'name', 'item_count']

    def get_item_count(self, obj):
        return obj.items.count()


class ItemSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source='store.name', read_only=True)
    current_balance = serializers.SerializerMethodField()
    needs_reorder = serializers.SerializerMethodField()
    stock_value = serializers.SerializerMethodField()
    profit_per_unit = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id', 'material_no', 'description', 'unit',
            'store', 'store_name',
            'opening_bin_balance', 'opening_physical',
            'reorder_quantity', 'reorder_level',
            'selling_price', 'cost_price', 'currency',
            'current_balance', 'needs_reorder',
            'stock_value', 'profit_per_unit',
        ]
        read_only_fields = ['currency']

    def get_current_balance(self, obj):
        return obj.current_balance()

    def get_needs_reorder(self, obj):
        return obj.needs_reorder()

    def get_stock_value(self, obj):
        return obj.stock_value()

    def get_profit_per_unit(self, obj):
        return obj.profit_per_unit()


class ItemWriteSerializer(serializers.ModelSerializer):
    """Separate serializer for create/update — no computed fields."""

    class Meta:
        model = Item
        fields = [
            'id', 'material_no', 'description', 'unit',
            'store', 'opening_bin_balance', 'opening_physical',
            'reorder_quantity', 'reorder_level',
            'selling_price', 'cost_price',
        ]


class TransactionSerializer(serializers.ModelSerializer):
    item_description = serializers.CharField(source='item.description', read_only=True)
    item_material_no = serializers.CharField(source='item.material_no', read_only=True)
    revenue = serializers.SerializerMethodField()
    profit = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'item', 'item_description', 'item_material_no',
            'date', 'invoice_no', 'type', 'qty', 'recipient',
            'revenue', 'profit',
        ]

    def get_revenue(self, obj):
        return obj.revenue()

    def get_profit(self, obj):
        return obj.profit()


class TransactionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ['item', 'date', 'invoice_no', 'type', 'qty', 'recipient']

    def validate_qty(self, value):
        if value == 0:
            raise serializers.ValidationError("Quantity cannot be zero.")
        return value

    def validate(self, data):
        # Issues must have negative qty, receipts positive
        if data['type'] == 'Issue' and data['qty'] > 0:
            data['qty'] = -data['qty']
        elif data['type'] == 'Receipt' and data['qty'] < 0:
            data['qty'] = abs(data['qty'])
        return data


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'message', 'notification_type', 'is_read', 'created_at']
        read_only_fields = ['title', 'message', 'notification_type', 'created_at']


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'location', 'created_at']
        read_only_fields = ['created_at']


class BusinessSummarySerializer(serializers.Serializer):
    """Read-only summary of business performance."""
    total_items = serializers.IntegerField()
    total_stock_value = serializers.FloatField()
    low_stock_count = serializers.IntegerField()
    today_sales = serializers.IntegerField()
    today_revenue = serializers.FloatField()
    today_profit = serializers.FloatField()
    stores_count = serializers.IntegerField()
    staff_count = serializers.IntegerField()
