import datetime
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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


class BusinessTypeRequirement(models.Model):
    """
    Defines a prerequisite requirement for a specific business type.
    business_type=None means it appears for ALL business types
    that have a formal tier (not micro/informal).
    """
    TIER_CHOICES = [
        ('micro',   'Micro / Informal'),
        ('semi',    'Semi-Formal'),
        ('formal',  'Formal / Regulated'),
    ]

    business_type    = models.ForeignKey(
        BusinessType,
        on_delete=models.CASCADE,
        related_name='requirements',
        null=True, blank=True,
        help_text='Leave blank for universal requirements'
    )
    tier             = models.CharField(max_length=10, choices=TIER_CHOICES,
                                        default='formal')
    name             = models.CharField(max_length=200)
    description      = models.TextField(blank=True,
        help_text='Brief explanation of what this is and why it is needed')
    issuing_authority = models.CharField(max_length=200, blank=True,
        help_text='e.g. County Government, NTSA, PPB')
    approximate_cost  = models.CharField(max_length=100, blank=True,
        help_text='e.g. KES 10,000 annually')
    is_mandatory     = models.BooleanField(default=True)
    display_order    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name        = 'Business Type Requirement'
        verbose_name_plural = 'Business Type Requirements'

    def __str__(self):
        bt = self.business_type.name if self.business_type else 'Universal'
        return f"{bt} — {self.name}"


class BusinessCompliance(models.Model):
    """
    Self-declared compliance record for a business against a requirement.
    Phase 1: declaration only.
    Phase 2: add document_upload + verified_by + verified_at fields.
    """
    business    = models.ForeignKey(
        'accounts.Business',
        on_delete=models.CASCADE,
        related_name='compliance_records',
    )
    requirement = models.ForeignKey(
        BusinessTypeRequirement,
        on_delete=models.CASCADE,
        related_name='compliance_records',
    )
    is_declared  = models.BooleanField(default=False)
    declared_at  = models.DateTimeField(null=True, blank=True)
    notes        = models.TextField(blank=True,
        help_text='Optional — e.g. permit number, expiry date')

    class Meta:
        unique_together = ['business', 'requirement']
        ordering        = ['requirement__display_order']
        verbose_name        = 'Business Compliance Record'
        verbose_name_plural = 'Business Compliance Records'

    def __str__(self):
        status = '✅' if self.is_declared else '⬜'
        return f"{status} {self.business.name} — {self.requirement.name}"


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
    county = models.ForeignKey(
        'core.County',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='customers',
    )
    credit_approved = models.BooleanField(
        default=False,
        help_text='Is this customer approved to buy on credit?',
    )
    credit_limit = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text='Maximum outstanding credit balance allowed (KES).',
    )
    expected_payment_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Expected days this customer takes to pay. Cannot exceed the business credit window.',
    )
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
        ('transaction', _('Transaction')),
        ('warning', _('Warning')),
        ('staff', _('Staff')),
        ('report', _('Report')),
        ('info', _('Info')),
        ('order', _('Order')),
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


class Category(models.Model):
    """Hierarchical category for inventory items.

    Use `code` as the stable external identifier (SuggestedCode in CSV).
    """
    code = models.CharField(max_length=50, unique=True)
    level1 = models.CharField(max_length=120)
    level2 = models.CharField(max_length=120, blank=True, null=True)
    level3 = models.CharField(max_length=120, blank=True, null=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level1', 'level2', 'level3']
        indexes = [models.Index(fields=['code']), models.Index(fields=['level1'])]

    def __str__(self):
        if self.level3:
            return f"{self.level1} > {self.level2} > {self.level3}"
        if self.level2:
            return f"{self.level1} > {self.level2}"
        return self.level1


class Item(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='items')
    material_no = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=200)
    unit = models.CharField(max_length=20)
    category = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    tags = models.JSONField(default=list, blank=True)
    opening_bin_balance = models.IntegerField(default=0)
    opening_physical = models.IntegerField(default=0)
    reorder_quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=0)
    # Supply-chain tuning fields
    lead_time_days = models.IntegerField(default=7, help_text='Expected supplier lead time (days)')
    safety_days = models.IntegerField(default=2, help_text='Safety stock expressed as days of cover')
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='items', null=True, blank=True)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='KES', editable=False)
    is_yield_item = models.BooleanField(
        default=False,
        help_text=_('Enable if this item loses weight/volume during processing (e.g. butchery cuts, keg pints).'),
    )
    yield_factor = models.DecimalField(
        max_digits=5, decimal_places=4,
        null=True, blank=True,
        help_text=_('Fraction of received quantity that becomes usable stock (e.g. 0.65 = 65% yield).'),
    )
    is_restricted = models.BooleanField(
        default=False,
        help_text='Staff require owner approval to sell this item.'
    )
    restriction_notes = models.CharField(
        max_length=200, blank=True,
        help_text='Reason for restriction — visible to owner only. e.g. Reserved for special customer, Do not sell until market day.'
    )
    restricted_quantity = models.PositiveIntegerField(
        default=0,
        help_text='Reserve this many units. Staff can freely sell above this threshold. '
                  'Set to 0 to require approval for ALL sales of this item.'
    )
    is_produce = models.BooleanField(
        default=False,
        help_text='Enable portion-based selling. Owner defines price presets (e.g. KES 40 = quarter head). Used for vegetables, produce, and gorogoro items.'
    )

    # ── Greens / bunch-based produce (Kibanda Produce Module) ──────────────
    PRODUCE_MODE_CHOICES = [
        ('PORTION', _('Portion / fraction (cabbage, gorogoro)')),
        ('BUNCH', _('Bunch — revenue envelope (greens / mboga)')),
    ]
    produce_mode = models.CharField(
        max_length=10, choices=PRODUCE_MODE_CHOICES, default='PORTION',
        help_text=_('PORTION = a fixed quantity per price (cabbage = 0.25 head, gorogoro = 1 tin). '
                    'BUNCH = each bunch is a money target depleted by price-point sales '
                    '(sukuma, spinach, kienyeji).'),
    )
    mix_group = models.CharField(
        max_length=40, blank=True, default='',
        help_text=_('Tag greens that can be sold together as one generic order — e.g. "kienyeji". '
                    'Items sharing a tag appear under a single mix tile and a generic '
                    '"mboga za kienyeji ya 20" is split across them. Leave blank for greens '
                    'only ever sold by name (e.g. sukuma, spinach).'),
    )
    revenue_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('1.70'),
        help_text=_('Default markup used to pre-fill a bunch target from its market cost '
                    '(1.70 → a 40/= bunch targets 68/=). Overridable per bunch by eye.'),
    )

    def default_bunch_target(self, cost):
        """Suggested envelope for a freshly received bunch: cost × multiplier."""
        try:
            mult = self.revenue_multiplier or Decimal('1.70')
            return (Decimal(str(cost)) * mult).quantize(Decimal('1'))
        except Exception:
            return Decimal('0')

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

    # --- Demand & reorder helpers (basic demand-driven heuristics) ---
    def avg_daily_issues(self, window_days=30):
        """Average daily issues (sales) over the past `window_days` days."""
        since = timezone.now().date() - datetime.timedelta(days=window_days)
        total = self.transactions.filter(type='Issue', date__gte=since).aggregate(models.Sum('qty'))['qty__sum'] or 0
        total = abs(total)
        try:
            return float(total) / float(window_days) if window_days else 0.0
        except Exception:
            return 0.0

    def lead_time_demand(self):
        """Demand expected during lead time (units)."""
        return int(round(self.avg_daily_issues() * (self.lead_time_days or 0)))

    def safety_stock(self):
        """Simple safety stock expressed as `safety_days * avg_daily_demand`."""
        return int(round(self.avg_daily_issues() * (self.safety_days or 0)))

    def reorder_point(self):
        """Reorder point (ROP) = lead-time demand + safety stock."""
        return int(round(self.lead_time_demand() + self.safety_stock()))

    def target_stock(self):
        """Target stock level after replenishment (ROP + reorder_quantity buffer)."""
        return int(round(self.reorder_point() + (self.reorder_quantity or 0)))

    def on_order(self):
        """Quantity currently on open purchase orders for this item."""
        # Resolve the PO line model dynamically to avoid circular import issues
        try:
            from django.apps import apps
            PurchaseOrderLine = apps.get_model('core', 'PurchaseOrderLine')
        except Exception:
            PurchaseOrderLine = None
        if not PurchaseOrderLine:
            return 0
        qs = PurchaseOrderLine.objects.filter(item=self, po__status__in=['draft', 'ordered', 'part_received'])
        ordered = qs.aggregate(total=models.Sum('quantity_ordered'))['total'] or 0
        received = qs.aggregate(total=models.Sum('quantity_received'))['total'] or 0
        try:
            return max(0, int(ordered - received))
        except Exception:
            return 0

    def shortage(self):
        """Units short of ROP considering on-order quantities."""
        return max(0, self.reorder_point() - (self.current_balance() + self.on_order()))

    def overstock(self):
        """Units in excess of target stock (suggest promotions/transfers)."""
        return max(0, self.current_balance() - self.target_stock())

    def recommended_order_qty(self):
        """Recommended quantity to order now to reach target stock (respecting reorder_quantity minimum).
        Returns 0 when no order is recommended.
        """
        req = self.target_stock() - (self.current_balance() + self.on_order())
        if req <= 0:
            return 0
        min_qty = self.reorder_quantity or 0
        return max(min_qty, int(req))

    def needs_reorder(self):
        # Prefer computed ROP if available; fall back to legacy reorder_level
        try:
            return (self.current_balance() + self.on_order()) <= max(self.reorder_level or 0, self.reorder_point())
        except Exception:
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


class ImportJob(models.Model):
    JOB_TYPE_CHOICES = [
        ('taxonomy', 'Taxonomy CSV'),
        ('products', 'Products CSV'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    job_type = models.CharField(max_length=20, choices=JOB_TYPE_CHOICES)
    original_filename = models.CharField(max_length=255, blank=True)
    file_path = models.CharField(max_length=1024)
    commit = models.BooleanField(default=False)
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    result_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"ImportJob {self.id} {self.job_type} {self.status}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('Receipt', _('Receipt')),
        ('Issue', _('Issue')),
        ('Wastage', _('Wastage')),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='transactions')
    date = models.DateField(default=timezone.now)
    invoice_no = models.CharField(max_length=50, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    qty = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text='Signed quantity. Negative for Issue/Wastage, positive for Receipt. Supports fractional values for produce items.'
    )
    recipient = models.CharField(max_length=200, blank=True)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='transactions', null=True, blank=True)
    PAYMENT_METHOD_CHOICES = [
        ('cash',   'Cash'),
        ('mpesa',  'M-Pesa'),
        ('credit', 'Credit / Tab'),
    ]
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash',
        blank=True,
    )
    sale_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text=_('Actual cash taken for this sale line. Set for produce / bunch portion '
                    'sales where the price is NOT selling_price × qty. Preferred by revenue().'),
    )
    produce_bunch = models.ForeignKey(
        'ProduceBunch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales',
        help_text=_('The greens bunch this portion sale was drawn from, if any.'),
    )

    def revenue(self):
        if self.type != 'Issue':
            return 0
        if self.sale_amount is not None:
            return float(self.sale_amount)
        if self.item.selling_price:
            return abs(float(self.qty)) * float(self.item.selling_price)
        return 0

    def cost(self):
        if self.type != 'Issue':
            return 0
        # Bunch sales carry their cost on the bunch, not the item.
        if self.produce_bunch_id and self.produce_bunch and self.produce_bunch.cost_price:
            return abs(float(self.qty)) * float(self.produce_bunch.cost_price)
        if self.item.cost_price:
            return abs(float(self.qty)) * float(self.item.cost_price)
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
        ('pending', _('Pending')),
        ('confirmed', _('Confirmed')),
        ('paid', _('Paid')),
        ('ready', _('Ready for Pickup')),
        ('completed', _('Completed')),
        ('cancelled', _('Cancelled')),
    ]

    DELIVERY_CHOICES = [
        ('pickup', _('Pickup')),
        ('delivery', _('Delivery')),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('mpesa', _('M-Pesa')),
        ('cash', _('Cash on Delivery')),
        ('pickup_pay', _('Pay at Pickup')),
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


class Forecast(models.Model):
    """Persisted revenue forecasts for a business.

    Stores the input history and produced forecast as JSON so the UI can
    display precomputed forecasts quickly.
    """
    SOURCE_CHOICES = [
        ('transaction', 'Transaction'),
        ('order', 'Order'),
        ('both', 'Both'),
    ]
    CADENCE_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='forecasts', null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='both')
    cadence = models.CharField(max_length=10, choices=CADENCE_CHOICES, default='daily')
    horizon = models.IntegerField(default=30)
    generated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    history = models.JSONField(default=list, blank=True)
    forecast = models.JSONField(default=list, blank=True)
    plot_path = models.CharField(max_length=512, blank=True, null=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f"Forecast {self.business} {self.cadence} h{self.horizon} @ {self.generated_at.isoformat()}"


# ────────────────────────────────────────────────
# BUSINESS EXPENSES (for net profit calculation)
# ────────────────────────────────────────────────

class BusinessExpense(models.Model):
    CATEGORY_CHOICES = [
        ('labor', _('Labor / Salaries')),
        ('electricity', _('Electricity Bills')),
        ('rent', _('Rent')),
        ('utilities', _('Utilities (Water, Internet)')),
        ('transport', _('Transport / Logistics')),
        ('marketing', _('Marketing & Advertising')),
        ('maintenance', _('Maintenance & Repairs')),
        ('supplies', _('Office Supplies')),
        ('tax', _('Taxes & Licenses')),
        ('other', _('Other')),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = _('Business Expense')
        verbose_name_plural = _('Business Expenses')
        indexes = [
            models.Index(fields=['business', 'date']),
        ]

    def __str__(self):
        return f"{self.description} — KES {self.amount:,.0f} ({self.date})"


# ────────────────────────────────────────────────
# CAPITAL INVESTMENT (one-time startup / asset costs)
# ────────────────────────────────────────────────

class CapitalInvestment(models.Model):
    CATEGORY_CHOICES = [
        ('equipment',    _('Equipment & Machinery')),
        ('vehicle',      _('Vehicle')),
        ('property',     _('Property / Land')),
        ('renovation',   _('Renovation & Fixtures')),
        ('license',      _('Licenses & Permits')),
        ('stock',        _('Initial Stock / Inventory')),
        ('technology',   _('Technology & Software')),
        ('other',        _('Other')),
    ]

    business     = models.ForeignKey(
        'accounts.Business',
        on_delete=models.CASCADE,
        related_name='capital_investments',
    )
    description  = models.CharField(max_length=255,
        help_text='e.g. 3 Pool Tables, Borehole Drilling Rig, Matatu KBX 123Z')
    amount       = models.DecimalField(max_digits=14, decimal_places=2)
    category     = models.CharField(max_length=20, choices=CATEGORY_CHOICES,
                                    default='equipment')
    date_acquired = models.DateField(
        help_text='Date this asset was purchased or cost was incurred')
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_acquired']
        verbose_name        = _('Capital Investment')
        verbose_name_plural = _('Capital Investments')
        indexes = [
            models.Index(fields=['business', 'date_acquired']),
        ]

    def __str__(self):
        return f"{self.description} — KES {self.amount:,.0f}"


# ────────────────────────────────────────────────
# PAYMENT MODEL (M-Pesa & Others)
# ────────────────────────────────────────────────

class Payment(models.Model):
    METHOD_CHOICES = [
        ('mpesa', _('M-Pesa')),
        ('cash', _('Cash')),
        ('bank', _('Bank Transfer')),
    ]
    STATUS_CHOICES = [
        ('pending', _('Pending')),
        ('completed', _('Completed')),
        ('failed', _('Failed')),
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
    VEHICLE_CHOICES = [
        ('motorcycle', _('Motorcycle 🏍️')),
        ('bicycle', _('Bicycle 🚲')),
        ('car', _('Car 🚗')),
        ('footsubishi', _('Footsubishi (Miguu Niponye) 🚶')),
    ]

    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='rider_profile')
    phone = models.CharField(max_length=20)
    mpesa_phone = models.CharField(max_length=20, blank=True, help_text='M-Pesa phone number for receiving delivery payments')
    county = models.ForeignKey(County, on_delete=models.SET_NULL, null=True, blank=True)
    vehicle_type = models.CharField(max_length=30, choices=VEHICLE_CHOICES, default='motorcycle')
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


# ────────────────────────────────────────────────
# PROCUREMENT SYSTEM
# ────────────────────────────────────────────────

class ProcurementRequest(models.Model):
    """A business owner posts what they need to procure."""
    STATUS_CHOICES = [
        ('open', _('Open for Bids')),
        ('evaluating', _('Evaluating')),
        ('awarded', _('Awarded')),
        ('closed', _('Closed')),
        ('cancelled', _('Cancelled')),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='procurement_requests')
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(BusinessType, on_delete=models.SET_NULL, null=True, blank=True,
                                 help_text='Type of supplier needed')
    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deadline = models.DateField(help_text='Last day to submit bids')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} — {self.business.name}"

    @property
    def is_accepting_bids(self):
        return self.status == 'open' and self.deadline >= timezone.now().date()


class SupplierBid(models.Model):
    """A supplier's bid on a procurement request."""
    STATUS_CHOICES = [
        ('submitted', _('Submitted')),
        ('shortlisted', _('Shortlisted')),
        ('accepted', _('Accepted')),
        ('rejected', _('Rejected')),
    ]

    procurement = models.ForeignKey(ProcurementRequest, on_delete=models.CASCADE, related_name='bids')
    supplier = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='submitted_bids')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_timeline = models.CharField(max_length=100, help_text='e.g. 3 days, 1 week')
    proposal = models.TextField(help_text='Why you are the best fit')
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                help_text='Auto-calculated composite score (0-100)')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='submitted')
    created_at = models.DateTimeField(auto_now_add=True)
    delivery_confirmed_at = models.DateTimeField(null=True, blank=True,
        help_text='Owner confirmed delivery success')
    payment_confirmed_at = models.DateTimeField(null=True, blank=True,
        help_text='Supplier confirmed payment received')

    class Meta:
        unique_together = ['procurement', 'supplier']
        ordering = ['-score', 'amount']

    def __str__(self):
        return f"Bid by {self.supplier.name} — KES {self.amount:,.0f}"

    def is_delivery_confirmed(self):
        return self.delivery_confirmed_at is not None

    def is_payment_confirmed(self):
        return self.payment_confirmed_at is not None

    def is_fully_completed(self):
        return self.is_delivery_confirmed() and self.is_payment_confirmed()



class SupplierBidLine(models.Model):
    """Optional: item-level lines for a supplier bid.

    If suppliers submit itemised bids, these lines can be used to auto-create
    PurchaseOrderLine entries when a bid is awarded.
    """
    bid = models.ForeignKey(SupplierBid, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def line_total(self):
        try:
            return float(self.unit_price or 0) * (self.quantity or 0)
        except Exception:
            return 0

    def __str__(self):
        return f"{self.item.description} x{self.quantity} — Bid {self.bid.id}"


class SupplierApplication(models.Model):
    """A business applies to become a supplier to another business."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    applicant = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='supplier_applications_sent')
    target_business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='supplier_applications_received')
    services_offered = models.TextField(help_text='What products/services can you supply?')
    cover_letter = models.TextField(help_text='Why should this business choose you?')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['applicant', 'target_business']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.applicant.name} → {self.target_business.name} ({self.status})"


# ────────────────────────────────────────────────
# FEEDBACK & REVIEWS
# ────────────────────────────────────────────────

class Feedback(models.Model):
    """Feedback from customer→business or business→supplier."""
    TYPE_CHOICES = [
        ('customer_to_business', 'Customer → Business'),
        ('business_to_supplier', 'Business → Supplier'),
    ]

    feedback_type = models.CharField(max_length=25, choices=TYPE_CHOICES)
    # Customer → Business
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='feedbacks')
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    # Business → Supplier
    from_business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE,
                                      null=True, blank=True, related_name='feedback_given')
    to_business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE,
                                    null=True, blank=True, related_name='feedback_received')
    # Common fields
    rating = models.PositiveSmallIntegerField(help_text='1-5 stars')
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        if self.feedback_type == 'customer_to_business':
            return f"{self.customer_name} → {self.to_business} ({self.rating}★)"
        return f"{self.from_business} → {self.to_business} ({self.rating}★)"


# ────────────────────────────────────────────────
# DELIVERY RATING (per-delivery rider feedback)
# ────────────────────────────────────────────────

class DeliveryRating(models.Model):
    """Rating for a rider on a specific delivery."""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='delivery_rating')
    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name='ratings')
    rated_by = models.CharField(max_length=200, help_text='Customer name or business owner')
    rating = models.PositiveSmallIntegerField(help_text='1-5 stars')
    on_time = models.BooleanField(default=True, help_text='Was delivery on time?')
    item_condition = models.PositiveSmallIntegerField(
        default=5, help_text='1-5 condition of items on arrival')
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rider} — {self.rating}★ (Order {self.order.order_number})"


# ────────────────────────────────────────────────
# PENDING TRANSACTION PROMPT (auto-created on incoming payment)
# ────────────────────────────────────────────────

class PendingTransactionPrompt(models.Model):
    """When a customer pays via Till/Paybill/Pochi, this prompt
    asks the staff/owner to log what was sold."""
    STATUS_CHOICES = [
        ('pending', 'Pending — Awaiting Confirmation'),
        ('confirmed', 'Confirmed — Transaction Logged'),
        ('dismissed', 'Dismissed'),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='transaction_prompts')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    phone = models.CharField(max_length=20, blank=True, help_text='Payer phone number')
    mpesa_receipt = models.CharField(max_length=30, blank=True, db_index=True)
    payment_channel = models.CharField(max_length=15, blank=True, help_text='till, paybill, pochi, phone')
    transaction = models.ForeignKey(Transaction, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='prompt', help_text='Linked transaction once confirmed')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    confirmed_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='confirmed_prompts')
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"KES {self.amount:,.0f} from {self.phone} — {self.status}"


# ────────────────────────────────────────────────
# PURCHASE ORDERS
# ────────────────────────────────────────────────

class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('draft', _('Draft')),
        ('ordered', _('Ordered')),
        ('part_received', _('Partially Received')),
        ('received', _('Received')),
        ('cancelled', _('Cancelled')),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='purchase_orders')
    supplier = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='supplier_purchase_orders', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    order_date = models.DateField(default=timezone.now)
    expected_delivery_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        supplier_name = self.supplier.name if self.supplier else 'Supplier'
        return f"PO-{self.id} — {supplier_name} — {self.get_status_display()}"

    def total_ordered_value(self):
        return sum([(l.quantity_ordered or 0) * (float(l.unit_price) if l.unit_price else 0.0) for l in self.lines.all()])


class PurchaseOrderLine(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity_ordered = models.IntegerField(default=0)
    quantity_received = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def quantity_remaining(self):
        return max(0, (self.quantity_ordered or 0) - (self.quantity_received or 0))

    def __str__(self):
        return f"{self.item.description} x{self.quantity_ordered} — PO-{self.po.id}"


# ────────────────────────────────────────────────
# GOODS RECEIPTS — Variable Pricing
# ────────────────────────────────────────────────

class GoodsReceipt(models.Model):
    """
    Records one physical delivery event against a PurchaseOrder.
    A PO can have multiple receipts (partial deliveries).
    """
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='receipts')
    received_date = models.DateField(default=timezone.now)
    delivery_note_no = models.CharField(max_length=50, blank=True)
    received_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_date', '-created_at']

    def __str__(self):
        return f"GR-{self.id} for PO-{self.po.id} ({self.received_date})"

    def total_received_value(self):
        return sum(
            (l.quantity_received or 0) * float(l.actual_unit_price or 0)
            for l in self.lines.all()
        )


class GoodsReceiptLine(models.Model):
    """
    One line in a GoodsReceipt — ties back to a PurchaseOrderLine.
    Captures the actual delivery price which may differ from the PO price.
    """
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name='lines')
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.CASCADE, related_name='receipt_lines')
    quantity_received = models.IntegerField(default=0)
    actual_unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    update_cost_price = models.BooleanField(
        default=False,
        help_text=_("Tick to update this item's cost price to the actual delivery price.")
    )
    notes = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.po_line.item.description} x{self.quantity_received} @ {self.actual_unit_price}"

    @property
    def price_variance(self):
        """Actual price minus PO price. Positive = more expensive than expected."""
        po_price = self.po_line.unit_price
        if po_price is not None:
            return float(self.actual_unit_price) - float(po_price)
        return 0.0

    @property
    def price_variance_pct(self):
        po_price = self.po_line.unit_price
        if po_price and float(po_price) > 0:
            return (self.price_variance / float(po_price)) * 100
        return 0.0

    @property
    def line_total(self):
        return (self.quantity_received or 0) * float(self.actual_unit_price or 0)


# ────────────────────────────────────────────────
# CUSTOMER CREDIT / DEBT
# ────────────────────────────────────────────────

class CustomerDebtPayment(models.Model):
    """
    Records a payment made by a customer towards their outstanding credit balance.

    Outstanding balance = sum of all credit Issue transactions for the customer
                        - sum of all CustomerDebtPayments for the customer.

    Payments are not linked to specific transactions — they reduce the total
    balance using FIFO logic (oldest debt is cleared first) in the views.
    """
    PAYMENT_METHOD_CHOICES = [
        ('cash',  _('Cash')),
        ('mpesa', _('M-Pesa')),
    ]

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='debt_payments',
    )
    business = models.ForeignKey(
        'accounts.Business',
        on_delete=models.CASCADE,
        related_name='customer_debt_payments',
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        max_length=10,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash',
    )
    paid_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='debt_payments_recorded',
    )

    class Meta:
        ordering = ['-paid_at']
        verbose_name = 'Customer Debt Payment'
        verbose_name_plural = 'Customer Debt Payments'

    def __str__(self):
        return f"{self.customer.name} paid KES {self.amount_paid:,.2f} on {self.paid_at.strftime('%d %b %Y')}"


# ────────────────────────────────────────────────
# REVENUE TARGETS
# ────────────────────────────────────────────────

class RevenueTarget(models.Model):
    """
    Owner-set revenue targets per period (daily / weekly / monthly).
    Optionally scoped to a specific store for multi-store businesses.
    Only one active target per (business, target_type, store) combination.
    """
    TARGET_TYPE_CHOICES = [
        ('daily',   _('Daily')),
        ('weekly',  _('Weekly')),
        ('monthly', _('Monthly')),
    ]

    business = models.ForeignKey(
        'accounts.Business',
        on_delete=models.CASCADE,
        related_name='revenue_targets',
    )
    store = models.ForeignKey(
        'core.Store',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='revenue_targets',
        help_text='Leave blank for a business-wide target.',
    )
    target_type = models.CharField(max_length=10, choices=TARGET_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['business', 'target_type', 'store']
        ordering = ['target_type']
        verbose_name = 'Revenue Target'
        verbose_name_plural = 'Revenue Targets'

    def __str__(self):
        store_label = f' ({self.store.name})' if self.store else ' (All Stores)'
        return f"{self.business.name} — {self.get_target_type_display()} KES {self.amount:,.0f}{store_label}"


# ────────────────────────────────────────────────
# RESTRICTED ITEM APPROVAL
# ────────────────────────────────────────────────

class ItemSaleApproval(models.Model):
    """
    Created when staff attempts to sell a restricted item.
    Owner approves or denies. On approval the transaction is auto-created.
    """
    STATUS_CHOICES = [
        ('pending',  _('Pending Owner Approval')),
        ('approved', _('Approved')),
        ('denied',   _('Denied')),
    ]

    business        = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='sale_approvals')
    item            = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='sale_approvals')
    requested_by    = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='sale_approval_requests')
    quantity        = models.PositiveIntegerField()
    recipient       = models.CharField(max_length=200, blank=True)
    invoice_no      = models.CharField(max_length=50, blank=True)
    payment_method  = models.CharField(max_length=20, blank=True)
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    denial_reason   = models.TextField(blank=True)
    requested_at    = models.DateTimeField(auto_now_add=True)
    decided_at      = models.DateTimeField(null=True, blank=True)
    decided_by      = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_approval_decisions')
    transaction     = models.ForeignKey(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='approval')

    class Meta:
        ordering = ['-requested_at']
        verbose_name = 'Item Sale Approval'
        verbose_name_plural = 'Item Sale Approvals'

    def __str__(self):
        return f"{self.requested_by.username} → {self.item.description} x{self.quantity} ({self.status})"


# ────────────────────────────────────────────────
# PRODUCE / PORTION PRESETS
# ────────────────────────────────────────────────

class ItemPortionPreset(models.Model):
    """
    Defines a price point for a produce item.
    Owner configures these per item — e.g. "Quarter cabbage = KES 40 = 0.25 units consumed".
    Staff selects a preset in Quick Sell or Add Transaction instead of entering quantity.

    Examples:
      Cabbage:  KES 10 → 0.0833 heads | KES 20 → 0.1667 | KES 40 → 0.25 | KES 70 → 0.5
      Kale:     KES 10 → 4 stems (quantity_consumed=4) | KES 20 → 8 stems
      Gorogoro: KES 70 → 1 small gorogoro (qty=1) | KES 130 → 1 medium
    """
    item = models.ForeignKey(
        'Item',
        on_delete=models.CASCADE,
        related_name='portion_presets',
    )
    label = models.CharField(
        max_length=100,
        help_text='Display name shown to staff. e.g. "Quarter head", "4 stems", "Small gorogoro"'
    )
    price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text='Amount the customer pays (KES).'
    )
    quantity_consumed = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        help_text='Stock units consumed. For fractional items: 0.25 = quarter head. For count items: 4 = four stems.'
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text='Lower numbers appear first. Use to sort presets by ascending price.'
    )

    class Meta:
        ordering = ['display_order', 'price']
        verbose_name = 'Item Portion Preset'
        verbose_name_plural = 'Item Portion Presets'

    def __str__(self):
        return f"{self.item.description}: {self.label} — KES {self.price}"


# ────────────────────────────────────────────────
# GREENS — BUNCH / REVENUE-ENVELOPE MODEL (Kibanda Produce Module)
# ────────────────────────────────────────────────

class ProduceBunch(models.Model):
    """
    A single physical bunch (shada / fungu) of greens bought at the market.

    The kibanda vendor does NOT count stems. She thinks: "I paid 40/= for this
    bunch, it must give me ~70/= before it is finished." So a bunch is modelled
    as a *revenue envelope*: it carries a cost and a target, and it is depleted
    by price-point sales (10/=, 20/=, 30/=) until the target is reached.

    The stems handed over per sale (2 for a large bunch, 4 for a small one) are
    the vendor's physical judgement and never enter the system — only money does.
    """
    SIZE_CHOICES = [
        ('SMALL', _('Small')),
        ('MEDIUM', _('Medium')),
        ('LARGE', _('Large')),
    ]
    STATUS_CHOICES = [
        ('OPEN', _('Open')),
        ('DEPLETED', _('Depleted')),
        ('DISCARDED', _('Discarded / wilted')),
    ]

    item = models.ForeignKey('Item', on_delete=models.CASCADE, related_name='bunches')
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE,
        related_name='produce_bunches', null=True, blank=True,
    )
    size = models.CharField(max_length=10, choices=SIZE_CHOICES, default='MEDIUM')
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text=_('What this bunch cost at the market this morning.'),
    )
    target_revenue = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text=_('Total money this bunch must give before it is finished. '
                    'Pre-filled from cost × the item multiplier; override per bunch by eye.'),
    )
    revenue_collected = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    received_on = models.DateField(
        default=timezone.localdate,
        help_text=_('Market day this bunch was bought — drives sell-oldest-first and wilting alerts.'),
    )
    opened_on = models.DateTimeField(null=True, blank=True)
    closed_on = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['received_on', 'id']  # oldest first → sell-oldest / FIFO
        verbose_name = 'Produce Bunch'
        verbose_name_plural = 'Produce Bunches'

    def __str__(self):
        return (f"{self.item.description} — {self.get_size_display()} bunch "
                f"({self.revenue_collected}/{self.target_revenue})")

    # ── envelope maths ────────────────────────────────────────────────────
    def remaining(self):
        target = self.target_revenue or Decimal('0')
        collected = self.revenue_collected or Decimal('0')
        return max(Decimal('0'), target - collected)

    def is_sold_out(self):
        return self.remaining() <= 0

    def realized_markup(self):
        if self.cost_price and self.cost_price > 0:
            return float(self.revenue_collected or 0) / float(self.cost_price)
        return 0.0

    def age_days(self):
        return (timezone.localdate() - self.received_on).days

    def is_wilting(self, threshold_days=1):
        """Still open and older than threshold — should be cleared first."""
        return self.status == 'OPEN' and self.age_days() > threshold_days

    def _fraction(self, amount):
        """Money amount → fraction of this bunch's envelope (for stock depletion)."""
        target = self.target_revenue or Decimal('0')
        if target <= 0:
            return Decimal('0')
        return (Decimal(str(amount)) / target).quantize(Decimal('0.0001'))

    # ── selling ───────────────────────────────────────────────────────────
    def record_sale(self, amount, payment_method='cash', recipient=''):
        """
        Deplete this bunch by `amount` shillings. Creates the stock Transaction
        (Issue, real cash on sale_amount) and updates the envelope. Returns the
        Transaction. Selling past target is allowed — the surplus is tracked.
        """
        amount = Decimal(str(amount))
        if amount <= 0:
            return None
        txn = Transaction.objects.create(
            item=self.item,
            business=self.business or self.item.business,
            type='Issue',
            qty=-self._fraction(amount),
            sale_amount=amount,
            payment_method=payment_method or 'cash',
            recipient=recipient or '',
            produce_bunch=self,
        )
        self.revenue_collected = (self.revenue_collected or Decimal('0')) + amount
        if self.opened_on is None:
            self.opened_on = timezone.now()
        if self.remaining() <= 0 and self.status == 'OPEN':
            self.status = 'DEPLETED'
            self.closed_on = timezone.now()
        self.save(update_fields=['revenue_collected', 'opened_on', 'status', 'closed_on'])
        return txn

    def discard(self, reason='Wilted / end of day'):
        """Write off the unsold remainder of this bunch as wastage."""
        if self.status == 'DISCARDED':
            return None
        leftover = self.remaining()
        txn = None
        if leftover > 0:
            txn = Transaction.objects.create(
                item=self.item,
                business=self.business or self.item.business,
                type='Wastage',
                qty=-self._fraction(leftover),
                sale_amount=Decimal('0'),
                recipient=(reason or '')[:200],
                produce_bunch=self,
            )
        self.status = 'DISCARDED'
        self.closed_on = timezone.now()
        self.note = (self.note + ' | ' if self.note else '') + (reason or '')
        self.save(update_fields=['status', 'closed_on', 'note'])
        return txn

    # ── generic mix sale: "mboga za kienyeji ya 20" ────────────────────────
    @classmethod
    def sell_mix(cls, business, mix_group, amount, payment_method='cash', recipient=''):
        """
        Customer doesn't care which kienyeji — just "kienyeji ya 20". Spreads
        `amount` proportionally across the OPEN bunches in this mix group
        (weighted by remaining envelope so they run down together) and records a
        sale against each. Returns (transactions, breakdown); ([], []) if none open.
        """
        amount = Decimal(str(amount))
        bunches = list(
            cls.objects.filter(
                business=business, status='OPEN', item__mix_group=mix_group,
            ).select_related('item').order_by('received_on', 'id')
        )
        bunches = [b for b in bunches if b.remaining() > 0]
        if not bunches or amount <= 0:
            return [], []

        total_remaining = sum((b.remaining() for b in bunches), Decimal('0'))
        # Proportional split, rounded to whole shillings; remainder to fullest bunch.
        allocations = []
        allocated = Decimal('0')
        for b in bunches:
            share = ((amount * b.remaining() / total_remaining).quantize(Decimal('1'))
                     if total_remaining > 0 else Decimal('0'))
            allocations.append([b, share])
            allocated += share
        remainder = amount - allocated
        if remainder != 0 and allocations:
            allocations.sort(key=lambda pair: pair[0].remaining(), reverse=True)
            allocations[0][1] += remainder

        txns, breakdown = [], []
        for b, share in allocations:
            if share <= 0:
                continue
            t = b.record_sale(share, payment_method=payment_method, recipient=recipient)
            if t:
                txns.append(t)
                breakdown.append({'item': b.item.description, 'amount': float(share)})
        return txns, breakdown
