from django.db import models
from django.utils import timezone


# ────────────────────────────────────────────────
# LOCATION MODELS
# ────────────────────────────────────────────────

class BusinessType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Business Types"


class County(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class SubCounty(models.Model):
    county = models.ForeignKey(County, on_delete=models.CASCADE, related_name='subcounties')
    name = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.name} ({self.county.name})"

    class Meta:
        unique_together = ['county', 'name']
        ordering = ['name']
        verbose_name_plural = "Sub Counties"


class Ward(models.Model):
    sub_county = models.ForeignKey(SubCounty, on_delete=models.CASCADE, related_name='wards')
    name = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.name} ({self.sub_county.name})"

    class Meta:
        unique_together = ['sub_county', 'name']
        ordering = ['name']


# ────────────────────────────────────────────────
# CUSTOMER MODEL
# ────────────────────────────────────────────────

class Customer(models.Model):
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='customers')
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


# ────────────────────────────────────────────────
# NOTIFICATION MODEL
# ────────────────────────────────────────────────

class Notification(models.Model):
    TYPE_CHOICES = [
        ('transaction', 'Transaction'),
        ('warning', 'Warning'),
        ('staff', 'Staff'),
        ('report', 'Report'),
        ('info', 'Info'),
        ('order', 'Order'),
    ]

    user = models.ForeignKey(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='app_notifications'
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='info'
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} — {self.user.username}"


# ────────────────────────────────────────────────
# STORE, ITEM, TRANSACTION
# ────────────────────────────────────────────────

class Store(models.Model):
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=100)
    suitable_for_types = models.ManyToManyField(BusinessType, related_name='suitable_stores', blank=True)

    def __str__(self):
        business_name = self.business.name if self.business else "No Business"
        return f"{self.name} ({business_name})"


class Item(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='items')
    material_no = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=200)
    unit = models.CharField(max_length=20)
    opening_bin_balance = models.IntegerField(default=0)
    opening_physical = models.IntegerField(default=0)
    reorder_quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=0)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='items', null=True, blank=True)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='KES', editable=False)

    def current_balance(self):
        total_movement = self.transactions.aggregate(models.Sum('qty'))['qty__sum'] or 0
        return self.opening_bin_balance + total_movement

    def physical_balance(self):
        total_movement = self.transactions.aggregate(models.Sum('qty'))['qty__sum'] or 0
        return self.opening_physical + total_movement

    def deficit(self):
        return max(0, self.current_balance() - self.physical_balance())

    def surplus(self):
        return max(0, self.physical_balance() - self.current_balance())

    def needs_reorder(self):
        return self.current_balance() <= self.reorder_level

    def stock_value(self):
        if self.cost_price and self.current_balance() > 0:
            return float(self.cost_price) * self.current_balance()
        return 0

    def profit_per_unit(self):
        if self.selling_price and self.cost_price:
            return float(self.selling_price) - float(self.cost_price)
        return 0

    def __str__(self):
        return f"{self.material_no} - {self.description}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('Receipt', 'Receipt'),
        ('Issue', 'Issue'),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='transactions')
    date = models.DateField(default=timezone.now)
    invoice_no = models.CharField(max_length=50, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    qty = models.IntegerField()
    recipient = models.CharField(max_length=200, blank=True)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='transactions', null=True, blank=True)

    def revenue(self):
        if self.type == 'Issue' and self.item.selling_price:
            return abs(self.qty) * float(self.item.selling_price)
        return 0

    def cost(self):
        if self.type == 'Issue' and self.item.cost_price:
            return abs(self.qty) * float(self.item.cost_price)
        return 0

    def profit(self):
        return self.revenue() - self.cost()

    def __str__(self):
        return f"{self.type} {abs(self.qty)} {self.item.unit} - {self.item.description}"


# ────────────────────────────────────────────────
# ORDER MODEL (Customer Marketplace)
# ────────────────────────────────────────────────

class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('paid', 'Paid'),
        ('ready', 'Ready for Pickup'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    DELIVERY_CHOICES = [
        ('pickup', 'Pickup'),
        ('delivery', 'Delivery'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('mpesa', 'M-Pesa'),
        ('cash', 'Cash on Delivery'),
        ('pickup_pay', 'Pay at Pickup'),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='orders')
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=20)
    customer_location = models.CharField(max_length=200, blank=True)
    order_number = models.CharField(max_length=30, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    delivery_mode = models.CharField(max_length=10, choices=DELIVERY_CHOICES, default='pickup')
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=15, choices=PAYMENT_METHOD_CHOICES, default='mpesa')
    rider = models.ForeignKey('RiderProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_orders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.order_number} — {self.customer_name}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            from django.utils.crypto import get_random_string
            prefix = timezone.localtime(timezone.now()).strftime('%y%m%d')
            self.order_number = f"ORD-{prefix}-{get_random_string(4, '0123456789ABCDEF')}"
        super().save(*args, **kwargs)

    def recalculate_total(self):
        subtotal = sum(line.line_total for line in self.lines.all())
        self.total_amount = subtotal + self.delivery_fee
        self.save(update_fields=['total_amount'])


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.item.description} x{self.quantity}"


# ────────────────────────────────────────────────
# PAYMENT MODEL (M-Pesa & Others)
# ────────────────────────────────────────────────

class Payment(models.Model):
    METHOD_CHOICES = [
        ('mpesa', 'M-Pesa'),
        ('cash', 'Cash'),
        ('bank', 'Bank Transfer'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments', null=True, blank=True)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='mpesa')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    phone = models.CharField(max_length=20, blank=True)
    mpesa_receipt = models.CharField(max_length=30, blank=True, db_index=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, db_index=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.method} {self.amount} KES — {self.status}"


# ────────────────────────────────────────────────
# RIDER PROFILE
# ────────────────────────────────────────────────

class RiderProfile(models.Model):
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='rider_profile')
    phone = models.CharField(max_length=20)
    county = models.ForeignKey(County, on_delete=models.SET_NULL, null=True, blank=True)
    vehicle_type = models.CharField(max_length=30, choices=[
        ('motorcycle', 'Motorcycle'),
        ('bicycle', 'Bicycle'),
        ('car', 'Car'),
        ('foot', 'On Foot'),
    ], default='motorcycle')
    is_available = models.BooleanField(default=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__first_name']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_vehicle_type_display()})"


# ────────────────────────────────────────────────
# SUPPLIER RELATIONSHIP
# ────────────────────────────────────────────────

class SupplierRelationship(models.Model):
    """Links a business owner to their preferred suppliers (other businesses on the platform)."""
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='supplier_links')
    supplier = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='customer_links')
    notes = models.TextField(blank=True, help_text='e.g. payment terms, contact person')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['business', 'supplier']
        ordering = ['supplier__name']

    def __str__(self):
        return f"{self.business.name} → {self.supplier.name}"