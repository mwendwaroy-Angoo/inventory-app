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
    is_defaulter = models.BooleanField(
        default=False,
        help_text='Had a debt written off as bad debt; permanently high-risk flag.',
    )
    last_cleared_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp when this customer last had their outstanding balance reach zero.',
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
    is_kitchen = models.BooleanField(default=False, help_text='Kitchen / grill side venture — separate POS board')

    # ── Per-counter M-Pesa overrides (Sprint K2a) ────────────────────────────
    has_own_mpesa = models.BooleanField(
        default=False,
        help_text='This counter receives M-Pesa on its own Till/Paybill, separate from the business default.',
    )
    mpesa_till = models.CharField(max_length=20, blank=True)
    mpesa_paybill = models.CharField(max_length=20, blank=True)
    mpesa_paybill_account = models.CharField(max_length=50, blank=True)
    mpesa_pochi = models.CharField(max_length=20, blank=True)
    daraja_consumer_key = models.CharField(max_length=255, blank=True)
    daraja_consumer_secret = models.CharField(max_length=255, blank=True)
    daraja_passkey = models.CharField(max_length=255, blank=True)
    daraja_environment = models.CharField(
        max_length=10, blank=True,
        help_text="Leave blank to inherit from business. Set 'sandbox' or 'production' to override.",
    )

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

    # ── Kitchen Batch Module fields (migration 0075) ──────────────────────
    is_kitchen_batch = models.BooleanField(
        default=False,
        help_text='Kitchen batch item — sold by price point from an open KitchenBatch. '
                  'Used for chips, stew, ugali and other cooked-to-batch food. '
                  'Stock is NOT counted by unit; the batch tracks cost vs revenue.'
    )

    # ── Bar / Keg Module fields (migration 0043) ───────────────────────────
    is_keg = models.BooleanField(
        default=False,
        help_text='Keg item sold from a barrel by weight/volume. Stock tracked via KegBarrel envelopes, not normal balance.'
    )
    volume_ml = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Bottle volume for single-piece liquor (750=mzinga, 350/375=half, 250=quarter).'
    )
    keg_type = models.CharField(
        max_length=8,
        choices=[
            ('REGULAR', 'Regular (Lager)'),
            ('DARK',    'Dark / Stout'),
            ('GOLD',    'Gold (Premium)'),
        ],
        blank=True,
        help_text='Keg items only — beer type for analytics grouping (Regular, Dark, Gold).',
    )
    bottle_envelope = models.BooleanField(
        default=False,
        help_text='Track this item as a bottle/spirits envelope — shift stock counts compute per-bottle '
                  'revenue variance so shrinkage is in KES, not just units.'
    )
    tot_ml = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text='Serving size in ml (e.g. 25 ml for a single tot of spirits). '
                  'Combined with volume_ml to derive tots_per_unit automatically if not set.'
    )
    tots_per_unit = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text='Number of servings per bottle/unit (e.g. 30 tots from 750 ml @ 25 ml each). '
                  'Used to convert unit variance to expected KES loss.'
    )

    def bottle_expected_revenue_per_unit(self):
        """KES expected per bottle = tots_per_unit × avg preset price. Falls back to selling_price."""
        tpu = float(self.tots_per_unit or 0)
        if tpu <= 0:
            return float(self.selling_price or 0)
        preset_prices = list(self.portion_presets.values_list('price', flat=True))
        avg_price = float(sum(preset_prices)) / len(preset_prices) if preset_prices else float(self.selling_price or 0)
        return round(tpu * avg_price, 2)

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
        if self.is_keg:
            # Keg stock is tracked via barrel envelopes, not item balance.
            # Count only sealed (unopened) barrels at cost.
            sealed = self.keg_barrels.filter(status='SEALED').aggregate(
                total=models.Sum('cost_price')
            )['total'] or 0
            return float(sealed)
        if self.cost_price and self.current_balance() > 0:
            return float(self.cost_price) * float(self.current_balance())
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
    keg_barrel = models.ForeignKey(
        'KegBarrel', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions',
        help_text='The keg barrel this pour was drawn from. Discriminator for keg analytics — parallel to produce_bunch_id.',
    )
    kitchen_batch = models.ForeignKey(
        'KitchenBatch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales',
        help_text='Kitchen batch this sale was drawn from. Discriminator for kitchen batch analytics.',
    )
    created_at = models.DateTimeField(
        default=timezone.now, null=True, blank=True,
        help_text='Exact timestamp — used for shift-level reconciliation. Can be backdated for offline sales.',
    )
    keg_serving = models.CharField(
        max_length=10, blank=True, default='',
        help_text="For keg pours: 'cup', 'pint', or 'jug'. Empty for non-keg transactions.",
    )
    keg_qty = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Number of servings in this keg pour (qty is in ml; keg_qty is the human count).',
    )
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text='Expiry date for this stock-in batch. Set on Receipt transactions only.',
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
        # Keg barrel pours: qty is stored in ml — must NOT be multiplied by KES cost_price.
        # Use proportional cost: sale_amount * (barrel_cost / barrel_target).
        if self.keg_barrel_id:
            barrel = self.keg_barrel
            if barrel and float(barrel.target_revenue or 0) > 0 and self.sale_amount is not None:
                return float(self.sale_amount) * float(barrel.cost_price) / float(barrel.target_revenue)
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
# PETTY CASH / COUNTER DRAWDOWN (Sprint 21)
# ────────────────────────────────────────────────

class PettyCash(models.Model):
    """Money taken from the counter during service for small operational expenses."""
    STATUS_CHOICES = [
        ('pending',  _('Pending Review')),
        ('approved', _('Approved')),
        ('rejected', _('Rejected')),
    ]
    REASON_CHOICES = [
        ('electricity', _('Electricity / Tokens')),
        ('supplies',    _('Supplies (tissues, serviettes, etc.)')),
        ('transport',   _('Transport / Delivery')),
        ('fuel',        _('Fuel / Gas')),
        ('food',        _('Staff Meal')),
        ('other',       _('Other')),
    ]

    business     = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='petty_cash_entries')
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    reason       = models.CharField(max_length=20, choices=REASON_CHOICES, default='other')
    description  = models.CharField(max_length=200, blank=True)
    recorded_by  = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='petty_cash_recorded')
    date         = models.DateField(default=timezone.now)
    created_at   = models.DateTimeField(auto_now_add=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by  = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='petty_cash_reviewed')
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    review_note  = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Petty Cash Entry')
        verbose_name_plural = _('Petty Cash Entries')

    def __str__(self):
        return f"{self.get_reason_display()} KES {self.amount} by {self.recorded_by} ({self.date})"


# ────────────────────────────────────────────────
# RECURRING EXPENSES (Sprint 7)
# ────────────────────────────────────────────────

class RecurringExpense(models.Model):
    PERIOD_CHOICES = [
        ('MONTHLY',   _('Monthly')),
        ('QUARTERLY', _('Quarterly (every 3 months)')),
        ('ANNUAL',    _('Annual (yearly)')),
    ]

    business          = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='recurring_expenses')
    description       = models.CharField(max_length=255)
    category          = models.CharField(max_length=20, choices=BusinessExpense.CATEGORY_CHOICES, default='other')
    amount            = models.DecimalField(max_digits=12, decimal_places=2)
    period            = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='MONTHLY')
    # For salary lines: link to a specific staff UserProfile
    staff_profile     = models.ForeignKey('accounts.UserProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='salary_entries')
    pay_day           = models.PositiveSmallIntegerField(
        default=0,
        help_text='Day of month salary is due (1–28). 0 = last day of the month.',
    )
    is_active         = models.BooleanField(default=True)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    last_notified_at  = models.DateTimeField(null=True, blank=True)
    notes             = models.CharField(max_length=255, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'description']
        verbose_name = _('Recurring Expense')
        verbose_name_plural = _('Recurring Expenses')

    def __str__(self):
        label = self.description
        if self.staff_profile:
            label += f' ({self.staff_profile.user.get_full_name() or self.staff_profile.user.username})'
        return f'{label} — KES {self.amount:,.0f} / {self.get_period_display()}'

    def period_start(self, reference_date=None):
        """Start of the current period relative to reference_date (default: today)."""
        from datetime import date as _date
        d = reference_date or timezone.localdate()
        if self.period == 'MONTHLY':
            return d.replace(day=1)
        elif self.period == 'QUARTERLY':
            quarter_month = ((d.month - 1) // 3) * 3 + 1
            return d.replace(month=quarter_month, day=1)
        else:  # ANNUAL
            return d.replace(month=1, day=1)

    def is_due_for_review(self, reference_date=None):
        """True if this expense has not been confirmed in the current period."""
        ps = self.period_start(reference_date)
        if not self.last_confirmed_at:
            return True
        confirmed_date = self.last_confirmed_at.date() if hasattr(self.last_confirmed_at, 'date') else self.last_confirmed_at
        return confirmed_date < ps

    def already_posted_this_period(self, reference_date=None):
        """True if a BusinessExpense was already auto-created for the current period."""
        ps = self.period_start(reference_date)
        return BusinessExpense.objects.filter(
            business=self.business,
            description=self.description,
            date__gte=ps,
            notes__startswith='[recurring]',
        ).exists()


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

    SOURCE_CHOICES = [
        ('bar',     _('Bar')),
        ('kitchen', _('Kitchen')),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments', null=True, blank=True)
    bar_tab = models.ForeignKey('BarTab', on_delete=models.SET_NULL, null=True, blank=True, related_name='stk_payments')
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='payments')
    store = models.ForeignKey(
        'Store', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments',
        help_text='Which store/counter received this payment (for per-counter M-Pesa reconciliation).',
    )
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='bar',
        help_text="Counter source: 'bar' or 'kitchen'. Drives per-counter cross-check in Z-report.",
    )
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
    receipt = models.ForeignKey('Receipt', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='prompts', help_text='Receipt issued at confirmation')
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
    SOURCE_CHOICES = [
        ('bar',     _('Bar')),
        ('kitchen', _('Kitchen')),
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
    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
        default='bar',
        help_text="Which sub-ledger this payment settles. Kitchen staff post 'kitchen'; bar/general staff post 'bar'.",
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
# SALARY PAYMENT  (Sprint H2 — Haki module)
# ────────────────────────────────────────────────

class SalaryPayment(models.Model):
    """Records whether a staff member's salary was paid for a given period."""
    METHOD_CHOICES = [
        ('cash',  _('Cash')),
        ('mpesa', _('M-Pesa')),
        ('bank',  _('Bank Transfer')),
    ]

    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='salary_payments',
    )
    staff = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.CASCADE, related_name='salary_payments',
    )
    period = models.CharField(
        max_length=7,
        help_text="Period string in YYYY-MM format (e.g. '2026-06').",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='cash', blank=True)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='salary_payments_recorded',
    )

    class Meta:
        ordering = ['-period', 'staff']
        unique_together = [('business', 'staff', 'period')]
        verbose_name = 'Salary Payment'
        verbose_name_plural = 'Salary Payments'

    def __str__(self):
        status = 'Paid' if self.paid else 'Due'
        return f"{self.staff.user.get_full_name() or self.staff.user.username} — {self.period} — KES {self.amount:,.0f} [{status}]"

    @property
    def days_overdue(self):
        from django.utils import timezone
        today = timezone.localdate()
        if not self.paid and self.due_date < today:
            return (today - self.due_date).days
        return 0

    @property
    def is_overdue(self):
        return self.days_overdue > 0


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
    is_jug = models.BooleanField(
        default=False,
        help_text='Legacy flag — superseded by serving_type. Kept for backward compat.',
    )
    SERVING_TYPE_CHOICES = [
        ('cup',  '☕ Cup / Kikombe'),
        ('pint', '🍺 Pint'),
        ('jug',  '🫙 Jug'),
    ]
    serving_type = models.CharField(
        max_length=10, choices=SERVING_TYPE_CHOICES, default='cup',
        help_text="For keg presets: how this serving is counted in daily reports. 'cup' for kikombe/shots, 'pint' for pints, 'jug' for jugs.",
    )

    KHAKI_CHOICES = [
        ('NONE',  'No khaki bag used'),
        ('SMALL', '1/4 Khaki (small)'),
        ('LARGE', '1/2 Khaki (large)'),
    ]
    khaki_type = models.CharField(
        max_length=8, choices=KHAKI_CHOICES, default='NONE',
        help_text='For kitchen batch presets: how many khaki bags this serving uses. '
                  'Drives the business-wide khaki pool deduction counter.',
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
    def sell_mix(cls, business, mix_group, amount, payment_method='cash', recipient='', item_ids=None):
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
        # Restrict to specific items the kibanda lady chose for this order
        if item_ids:
            ids = set(int(i) for i in item_ids if str(i).isdigit() or isinstance(i, int))
            bunches = [b for b in bunches if b.item_id in ids]
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


# ────────────────────────────────────────────────
# BAR MODULE — Shift, KegBarrel, KegWeightReading, BarTab, BarTabEntry
# (migration 0043_bar_module)
# ────────────────────────────────────────────────

class Shift(models.Model):
    STATUS_CHOICES = [
        ('OPEN',      _('Open')),
        ('CLOSED',    _('Closed — awaiting confirmation')),
        ('CONFIRMED', _('Confirmed by incoming staff')),
    ]

    business      = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='shifts')
    store         = models.ForeignKey('Store', on_delete=models.CASCADE, null=True, blank=True)
    staff         = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='shifts')
    status        = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    started_at    = models.DateTimeField(default=timezone.now)
    ended_at      = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    closing_cash_counted = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    offline_sales_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Cash collected offline (no app/no internet) during this shift, not yet in transactions.',
    )
    offline_sales_note = models.CharField(max_length=200, blank=True)
    confirmed_by  = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='shifts_confirmed'
    )
    notes         = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Shift'
        verbose_name_plural = 'Shifts'

    def __str__(self):
        return f"{self.staff.get_full_name() or self.staff.username} — {self.started_at.strftime('%d %b %Y %H:%M')} ({self.status})"


def _refresh_keg_baseline(barrel):
    """Recompute and cache the business loss baseline after a barrel becomes DEPLETED."""
    try:
        from . import keg_metrics
        from accounts.models import Business as _Business
        data = keg_metrics.business_keg_loss_baseline(barrel.business)
        _Business.objects.filter(pk=barrel.business_id).update(
            keg_loss_baseline_pct=data['baseline_pct'],
            keg_loss_baseline_sample=data['sample'],
        )
    except Exception:
        pass


class KegBarrel(models.Model):
    STATUS_CHOICES = [
        ('SEALED',   _('Sealed — received, not tapped')),
        ('TAPPED',   _('Tapped — selling')),
        ('DEPLETED', _('Depleted — target reached / empty')),
        ('RETURNED', _('Returned / discarded')),
    ]

    business        = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='keg_barrels')
    store           = models.ForeignKey('Store', on_delete=models.CASCADE, null=True, blank=True)
    item            = models.ForeignKey('Item', on_delete=models.CASCADE, related_name='keg_barrels')
    gross_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('60.00'))
    tare_weight_kg  = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))
    cost_price      = models.DecimalField(max_digits=10, decimal_places=2)
    target_revenue  = models.DecimalField(max_digits=10, decimal_places=2)
    revenue_collected   = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    volume_dispensed_ml = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Sum of preset volumes sold — the BOOK figure. Compare with weight.'
    )
    cups_dispensed = models.PositiveIntegerField(
        default=0,
        help_text='Running count of cup servings poured. Incremented by record_sale when preset.is_jug is False.',
    )
    jugs_dispensed = models.PositiveIntegerField(
        default=0,
        help_text='Running count of jug servings poured.',
    )
    pints_dispensed = models.PositiveIntegerField(
        default=0,
        help_text='Running count of pint servings poured.',
    )
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='SEALED')
    received_on = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='kegs_received'
    )
    tapped_at  = models.DateTimeField(null=True, blank=True)
    closed_at  = models.DateTimeField(null=True, blank=True)
    note       = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-received_on', '-id']
        verbose_name = 'Keg Barrel'
        verbose_name_plural = 'Keg Barrels'

    def __str__(self):
        return f"{self.item.description} — {self.get_status_display()} (barrel #{self.id})"

    # ── volume helpers ────────────────────────────────────────────────────

    @property
    def net_volume_l(self):
        return float(self.gross_weight_kg) - float(self.tare_weight_kg)

    @property
    def net_volume_ml(self):
        return self.net_volume_l * 1000.0

    def latest_weight(self):
        r = self.weight_readings.order_by('-recorded_at').first()
        return float(r.weight_kg) if r else float(self.gross_weight_kg)

    def weight_implied_dispensed_ml(self):
        """GROUND TRUTH: ml dispensed per the scale."""
        return max(0.0, (float(self.gross_weight_kg) - self.latest_weight()) * 1000.0)

    def revenue_rate_per_ml(self):
        return float(self.target_revenue) / self.net_volume_ml if self.net_volume_ml else 0.0

    def expected_revenue_from_weight(self):
        return self.weight_implied_dispensed_ml() * self.revenue_rate_per_ml()

    def remaining_envelope(self):
        return max(0.0, float(self.target_revenue) - float(self.revenue_collected))

    def realized_markup(self):
        if self.cost_price:
            return float(self.revenue_collected) / float(self.cost_price)
        return 0.0

    def age_days(self):
        if self.tapped_at:
            return (timezone.localdate() - self.tapped_at.date()).days
        return 0

    def is_stale(self, threshold_days=2):
        return self.status == 'TAPPED' and self.age_days() > threshold_days

    # ── lifecycle ─────────────────────────────────────────────────────────

    def tap(self, user):
        if self.status == 'SEALED':
            self.status = 'TAPPED'
            self.tapped_at = timezone.now()
            self.save(update_fields=['status', 'tapped_at'])

    def close(self, reason=''):
        if self.status in ('SEALED', 'TAPPED'):
            self.status = 'RETURNED' if reason else 'DEPLETED'
            self.closed_at = timezone.now()
            update_fields = ['status', 'closed_at']
            if reason:
                self.note = (self.note + ' | ' if self.note else '') + reason
                update_fields.append('note')
            self.save(update_fields=update_fields)
            if self.status == 'DEPLETED':
                _refresh_keg_baseline(self)

    def record_sale(self, preset, qty, payment_method, recorded_by, tab=None, server_name=''):
        """
        One pour. Creates Transaction(type=Issue) and updates the envelope.
        If tab is provided, payment_method is set to 'credit' and a BarTabEntry is created.
        Auto-DEPLETED when envelope reached AND latest weight ≤ tare + 0.5 kg.
        """
        ml = Decimal(str(float(preset.quantity_consumed) * qty))
        amount = Decimal(str(float(preset.price) * qty))
        pay = 'credit' if tab else (payment_method or 'cash')

        # serving_type takes precedence; fall back to legacy is_jug flag; then infer from label
        serving = getattr(preset, 'serving_type', '') or ('jug' if getattr(preset, 'is_jug', False) else 'cup')
        if serving == 'cup':
            _lbl = (getattr(preset, 'label', '') or '').lower()
            if 'jug' in _lbl:
                serving = 'jug'
            elif 'pint' in _lbl:
                serving = 'pint'

        txn = Transaction.objects.create(
            item=self.item,
            business=self.business,
            type='Issue',
            qty=-ml,
            sale_amount=amount,
            payment_method=pay,
            recipient=tab.customer_name if tab else '',
            keg_barrel=self,
            keg_serving=serving,
            keg_qty=int(qty),
        )

        self.revenue_collected = (self.revenue_collected or Decimal('0')) + amount
        self.volume_dispensed_ml = (self.volume_dispensed_ml or Decimal('0')) + ml
        if serving == 'jug':
            self.jugs_dispensed = (self.jugs_dispensed or 0) + int(qty)
            update_fields = ['revenue_collected', 'volume_dispensed_ml', 'jugs_dispensed']
        elif serving == 'pint':
            self.pints_dispensed = (self.pints_dispensed or 0) + int(qty)
            update_fields = ['revenue_collected', 'volume_dispensed_ml', 'pints_dispensed']
        else:
            self.cups_dispensed = (self.cups_dispensed or 0) + int(qty)
            update_fields = ['revenue_collected', 'volume_dispensed_ml', 'cups_dispensed']

        auto_depleted = False
        weighs = getattr(self.business, 'weighs_kegs', False)
        if self.status == 'TAPPED':
            if weighs:
                # Weight-based depletion: scale is ground truth.
                # Envelope reaching zero is informational on weighing bars.
                if self.latest_weight() <= float(self.tare_weight_kg) + 0.5:
                    self.status = 'DEPLETED'
                    self.closed_at = timezone.now()
                    update_fields += ['status', 'closed_at']
                    auto_depleted = True
            # Non-weighing bar: no auto-depletion — frontend prompts at the envelope boundary.

        self.save(update_fields=update_fields)
        if auto_depleted:
            _refresh_keg_baseline(self)

        if tab is not None:
            BarTabEntry.objects.create(
                tab=tab,
                transaction=txn,
                description=f"{preset.label} ×{qty}",
                amount=amount,
            )

        return txn

    @classmethod
    def record_sale_locked(cls, barrel_id, business, preset, qty, payment_method,
                           recorded_by, tab=None, server_name=''):
        """Thread-safe wrapper around record_sale using SELECT FOR UPDATE."""
        from django.db import transaction as _txn
        with _txn.atomic():
            barrel = (
                cls.objects
                .select_for_update()
                .select_related('item')
                .get(id=barrel_id, business=business, status='TAPPED')
            )
            return barrel.record_sale(preset, qty, payment_method, recorded_by,
                                      tab=tab, server_name=server_name)


class KegWeightReading(models.Model):
    READING_TYPES = [
        ('RECEIVE',     _('Received — verify 60 kg')),
        ('SHIFT_OPEN',  _('Shift opening check')),
        ('SHIFT_CLOSE', _('Shift closing check')),
        ('SPOT',        _('Spot check')),
        ('FINAL',       _('Final / barrel empty')),
    ]

    barrel       = models.ForeignKey(KegBarrel, on_delete=models.CASCADE, related_name='weight_readings')
    shift        = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='keg_readings')
    weight_kg    = models.DecimalField(max_digits=6, decimal_places=2)
    reading_type = models.CharField(max_length=12, choices=READING_TYPES)
    recorded_by  = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True,
        related_name='keg_readings_recorded'
    )
    confirmed_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='keg_readings_confirmed',
        help_text='Incoming staff who verified this reading at handover.'
    )
    recorded_at  = models.DateTimeField(auto_now_add=True)
    note         = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Keg Weight Reading'
        verbose_name_plural = 'Keg Weight Readings'

    def __str__(self):
        return f"{self.barrel} — {self.weight_kg} kg ({self.get_reading_type_display()})"


class BarTab(models.Model):
    STATUS_CHOICES = [
        ('OPEN',     _('Open')),
        ('SETTLED',  _('Settled')),
        ('VOID',     _('Void')),
    ]

    business      = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='bar_tabs')
    store         = models.ForeignKey('Store', on_delete=models.CASCADE, null=True, blank=True)
    shift         = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='tabs')
    customer_name = models.CharField(max_length=80)
    customer      = models.ForeignKey(
        'Customer', null=True, blank=True, on_delete=models.SET_NULL,
        help_text='Optional link to a registered customer — enables debt tracker integration.'
    )
    served_by     = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tabs_served'
    )
    server_name   = models.CharField(
        max_length=80, blank=True,
        help_text='Waitress name when she has no login.'
    )
    SOURCE_CHOICES = [('bar', 'Bar'), ('kitchen', 'Kitchen')]
    source        = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='bar')
    status        = models.CharField(max_length=8, choices=STATUS_CHOICES, default='OPEN')
    opened_at     = models.DateTimeField(auto_now_add=True)
    settled_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-opened_at']
        verbose_name = 'Bar Tab'
        verbose_name_plural = 'Bar Tabs'

    def __str__(self):
        return f"Tab — {self.customer_name} ({self.status})"

    def total(self):
        result = self.entries.aggregate(t=models.Sum('amount'))['t']
        return result or Decimal('0')

    def unpaid_total(self):
        result = self.entries.filter(is_paid=False).aggregate(t=models.Sum('amount'))['t']
        return result or Decimal('0')


class BarTabEntry(models.Model):
    tab         = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='entries')
    transaction = models.OneToOneField(
        Transaction, on_delete=models.CASCADE, related_name='tab_entry'
    )
    description    = models.CharField(max_length=80)
    amount         = models.DecimalField(max_digits=10, decimal_places=2)
    is_paid        = models.BooleanField(default=False)
    paid_at        = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Bar Tab Entry'
        verbose_name_plural = 'Bar Tab Entries'

    def __str__(self):
        status = 'paid' if self.is_paid else 'open'
        return f"{self.tab.customer_name} — {self.description} KES {self.amount} ({status})"


class BarCupLog(models.Model):
    """Records one batch of disposable cups purchased for the business's shared cup pool.

    barrel and item are optional cost-allocation context only — the pool math
    is done business-wide via keg_metrics.business_cup_pool(), not per-barrel.
    """
    CUP_SIZES = [
        ('300', '300 ml'),
        ('500', '500 ml'),
    ]
    business    = models.ForeignKey('accounts.Business', on_delete=models.CASCADE,
                                    related_name='cup_logs')
    barrel      = models.ForeignKey(KegBarrel, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='cup_logs')
    item        = models.ForeignKey('Item', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='cup_logs')
    cup_size    = models.CharField(max_length=3, choices=CUP_SIZES, default='300')
    qty         = models.PositiveIntegerField()
    unit_cost   = models.DecimalField(max_digits=8, decimal_places=2)
    total_cost  = models.DecimalField(max_digits=10, decimal_places=2)
    date        = models.DateField(default=timezone.localdate)
    note        = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='cup_logs_recorded')

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Bar Cup Log'
        verbose_name_plural = 'Bar Cup Logs'

    def __str__(self):
        barrel_ctx = f" — Barrel #{self.barrel_id}" if self.barrel_id else ''
        return f"{self.business_id}{barrel_ctx}: {self.qty}× {self.cup_size}ml cups @ KES {self.unit_cost}"


class ShiftStockCount(models.Model):
    """End-of-shift stock take: staff records physical item counts for peace-of-mind reconciliation."""
    shift       = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='stock_counts')
    item        = models.ForeignKey('Item', on_delete=models.SET_NULL, null=True, related_name='stock_counts')
    book_balance = models.DecimalField(max_digits=10, decimal_places=2)
    actual_count = models.DecimalField(max_digits=10, decimal_places=2)
    recorded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, related_name='stock_counts_recorded'
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['item__description']
        verbose_name = 'Shift Stock Count'
        verbose_name_plural = 'Shift Stock Counts'
        unique_together = [('shift', 'item')]

    def __str__(self):
        return f"Shift #{self.shift_id} — {self.item} ({self.actual_count} / book {self.book_balance})"

    @property
    def variance(self):
        return self.actual_count - self.book_balance


class ProduceOverhead(models.Model):
    """Operational overhead for the kibanda produce section — bags, water, transport."""
    OVERHEAD_TYPES = [
        ('BAGS',      'Polythene Bags'),
        ('WATER',     'Water (washing greens)'),
        ('TRANSPORT', 'Transport'),
        ('OTHER',     'Other'),
    ]
    business      = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='produce_overheads'
    )
    bunch         = models.ForeignKey(
        'ProduceBunch', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='overheads',
        help_text='Optional link to a specific batch/bunch this cost relates to.',
    )
    overhead_type = models.CharField(max_length=12, choices=OVERHEAD_TYPES, default='OTHER')
    qty           = models.PositiveIntegerField(default=1)
    cost          = models.DecimalField(max_digits=8, decimal_places=2)
    date          = models.DateField(default=timezone.localdate)
    note          = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Produce Overhead'
        verbose_name_plural = 'Produce Overheads'

    def __str__(self):
        return f"{self.get_overhead_type_display()} — KES {self.cost} ({self.date})"


# ── Waitress Order Queue (Sprint 5) ───────────────────────────────────────────

class TableOrder(models.Model):
    STATUS_CHOICES = [
        ('PENDING',   _('Pending — waiting at bar')),
        ('ACCEPTED',  _('Accepted — being prepared')),
        ('READY',     _('Ready for pickup')),
        ('SERVED',    _('Served — delivered to table')),
        ('CANCELLED', _('Cancelled')),
    ]
    PAYMENT_CHOICES = [
        ('cash',   'Cash'),
        ('mpesa',  'M-Pesa'),
        ('credit', 'Credit / Tab'),
    ]

    business       = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='table_orders')
    table_label    = models.CharField(max_length=30)
    waitress       = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='table_orders_placed',
    )
    shift          = models.ForeignKey(
        'Shift', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='table_orders',
    )
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default='cash')
    notes          = models.CharField(max_length=200, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    served_at      = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Table Order'
        verbose_name_plural = 'Table Orders'

    def __str__(self):
        return f"{self.table_label} — {self.get_status_display()} ({self.created_at.strftime('%H:%M')})"

    def total_amount(self):
        return sum(i.line_total() for i in self.items.all())

    def item_summary(self):
        return ', '.join(
            f"{i.preset_label or i.item.description} ×{int(i.quantity) if i.quantity == int(i.quantity) else i.quantity}"
            for i in self.items.select_related('item')
        )


class TableOrderItem(models.Model):
    order        = models.ForeignKey(TableOrder, on_delete=models.CASCADE, related_name='items')
    item         = models.ForeignKey('Item', on_delete=models.PROTECT, related_name='table_order_items')
    preset       = models.ForeignKey(
        'ItemPortionPreset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='table_order_items',
        help_text='For keg/portion items — the cup size / portion preset ordered.',
    )
    quantity     = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('1'))
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    preset_label = models.CharField(max_length=60, blank=True)
    item_name    = models.CharField(max_length=120, blank=True)
    notes        = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Table Order Item'
        verbose_name_plural = 'Table Order Items'

    def __str__(self):
        label = self.preset_label or self.item_name or self.item.description
        return f"{label} ×{self.quantity} @ KES {self.unit_price}"

    def line_total(self):
        return self.quantity * self.unit_price


# ────────────────────────────────────────────────
# KITCHEN BATCH MODULE (Sprint KF1)
# ────────────────────────────────────────────────

class KitchenBatch(models.Model):
    """
    Revenue envelope for one cooking session / pot / batch.
    Used for chips (viazi), stew (mchuzi), ugali, etc.
    No mandatory target — she cooks, sells until done, sees P&L.

    Each batch tracks:
        cost_total  → what she spent on raw material (e.g. KES 1,500 for 2 debe ya viazi)
        revenue_collected → running total as she sells by price point
        profit property → revenue - cost

    Not to be confused with ProduceBunch (greens/sack produce) — KitchenBatch
    has no target, no size, and is for cooked food only.
    Discriminator on Transaction: kitchen_batch_id (not produce_bunch_id).
    """
    STATUS_CHOICES = [
        ('OPEN',      'Open — selling'),
        ('DEPLETED',  'Depleted — all sold'),
        ('DISCARDED', 'Discarded — went to waste'),
    ]
    business          = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='kitchen_batches',
    )
    store             = models.ForeignKey(
        'Store', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_batches',
    )
    item              = models.ForeignKey(
        'Item', on_delete=models.PROTECT, related_name='kitchen_batches',
    )
    cost_total        = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Total raw-material cost for this batch (e.g. cost of potatoes, nyama etc.).',
    )
    cost_note         = models.CharField(
        max_length=200, blank=True,
        help_text='Optional note: "2 debe ya viazi @ 750 = 1500".',
    )
    revenue_collected = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
    )
    khaki_small_used  = models.PositiveIntegerField(
        default=0,
        help_text='1/4 khaki bags consumed from this batch (deducted from business khaki pool).',
    )
    khaki_large_used  = models.PositiveIntegerField(
        default=0,
        help_text='1/2 khaki bags consumed from this batch.',
    )
    status            = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default='OPEN',
    )
    received_on       = models.DateField(default=timezone.localdate)
    closed_on         = models.DateTimeField(null=True, blank=True)
    note              = models.CharField(max_length=200, blank=True)
    recorded_by       = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_batches_recorded',
    )

    class Meta:
        ordering = ['-received_on', '-id']
        verbose_name = 'Kitchen Batch'
        verbose_name_plural = 'Kitchen Batches'

    def __str__(self):
        return f"{self.item.description} batch #{self.id} — {self.status}"

    @property
    def profit(self):
        return self.revenue_collected - self.cost_total

    @property
    def profit_pct(self):
        if not self.cost_total or self.cost_total <= 0:
            return None
        return round(float(self.profit) / float(self.cost_total) * 100, 1)

    @property
    def days_open(self):
        from django.utils import timezone as _tz
        end = self.closed_on.date() if self.closed_on else _tz.localdate()
        return (end - self.received_on).days + 1

    def record_sale(self, amount, payment_method='cash', recipient='', preset=None):
        """Sell from this batch. Creates Transaction, updates revenue_collected + khaki count."""
        amount = Decimal(str(amount))
        if amount <= 0:
            return None
        txn = Transaction.objects.create(
            item=self.item,
            business=self.business,
            type='Issue',
            qty=Decimal('-1'),
            sale_amount=amount,
            payment_method=payment_method or 'cash',
            recipient=recipient or '',
            kitchen_batch=self,
        )
        self.revenue_collected = (self.revenue_collected or Decimal('0')) + amount
        if preset:
            if preset.khaki_type == 'SMALL':
                self.khaki_small_used = (self.khaki_small_used or 0) + 1
            elif preset.khaki_type == 'LARGE':
                self.khaki_large_used = (self.khaki_large_used or 0) + 1
        self.save(update_fields=['revenue_collected', 'khaki_small_used', 'khaki_large_used'])
        return txn

    def deplete(self):
        """Mark batch as sold out."""
        if self.status != 'OPEN':
            return
        from django.utils import timezone as _tz
        self.status = 'DEPLETED'
        self.closed_on = _tz.now()
        self.save(update_fields=['status', 'closed_on'])

    def discard(self, reason=''):
        """Write off unsold remainder."""
        if self.status == 'DISCARDED':
            return
        from django.utils import timezone as _tz
        self.status = 'DISCARDED'
        self.closed_on = _tz.now()
        self.note = (self.note + ' | ' if self.note else '') + (reason or 'Discarded')
        self.save(update_fields=['status', 'closed_on', 'note'])


class KitchenConsumableLog(models.Model):
    """
    Tracks purchases of kitchen consumables that are pooled business-wide:
    khaki bags (1/4 and 1/2 sizes) and other consumables like tomato sauce.
    Oil and gas/electricity are excluded — shared overhead, not per-sale cost.
    """
    CONSUMABLE_CHOICES = [
        ('KHAKI_SMALL', '1/4 Khaki bags'),
        ('KHAKI_LARGE', '1/2 Khaki bags'),
        ('SAUCE_TOMATO', 'Tomato sauce (jerrican)'),
        ('OTHER', 'Other'),
    ]
    business         = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='kitchen_consumable_logs',
    )
    consumable_type  = models.CharField(max_length=16, choices=CONSUMABLE_CHOICES)
    qty              = models.DecimalField(
        max_digits=8, decimal_places=1,
        help_text='Units bought: pieces for khaki, jerricans for sauce.',
    )
    unit_cost        = models.DecimalField(max_digits=8, decimal_places=2)
    total_cost       = models.DecimalField(max_digits=10, decimal_places=2)
    date             = models.DateField(default=timezone.localdate)
    note             = models.CharField(max_length=120, blank=True)
    recorded_by      = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_consumable_logs_recorded',
    )

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Kitchen Consumable Log'
        verbose_name_plural = 'Kitchen Consumable Logs'

    def __str__(self):
        return f"{self.get_consumable_type_display()} ×{self.qty} @ KES {self.unit_cost} — {self.date}"


# ────────────────────────────────────────────────

class Receipt(models.Model):
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='receipts'
    )
    receipt_number = models.PositiveIntegerField()
    token = models.CharField(max_length=32, unique=True, db_index=True)
    customer_name = models.CharField(max_length=100, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    payment_method = models.CharField(max_length=20, default='cash')
    total = models.DecimalField(max_digits=12, decimal_places=2)
    lines = models.JSONField(default=list)
    source = models.CharField(
        max_length=20, blank=True, default='',
        help_text="'kitchen' for kitchen board sales; '' for bar/quick-sell/debt payments."
    )
    # F6 — eTIMS fields (nullable until KRA integration is live)
    etims_receipt_no  = models.CharField(max_length=50, blank=True, default='')
    etims_url         = models.URLField(max_length=300, blank=True, default='')
    etims_submitted_at = models.DateTimeField(null=True, blank=True)
    # K4 — structured customer standing data (score, outstanding, due_date, warn)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='receipts_issued'
    )

    class Meta:
        unique_together = [('business', 'receipt_number')]
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.receipt_number} – {self.business}"

    @classmethod
    def issue(cls, business, lines, payment_method, user=None, customer_name='', customer_phone='', source='', meta=None):
        import secrets as _secrets
        from django.db import transaction as _tx
        total = sum(float(line.get('subtotal', 0)) for line in lines)
        with _tx.atomic():
            # select_for_update() + aggregate() is rejected by PostgreSQL ("FOR UPDATE is not
            # allowed with aggregate functions"). Use order_by + first() to lock the latest
            # row and read its number — correct and safe in both SQLite and PostgreSQL.
            latest = cls.objects.select_for_update().filter(
                business=business
            ).order_by('-receipt_number').first()
            last = latest.receipt_number if latest else 0
            return cls.objects.create(
                business=business,
                receipt_number=last + 1,
                token=_secrets.token_urlsafe(20),
                customer_name=customer_name or '',
                customer_phone=customer_phone or '',
                payment_method=payment_method,
                total=Decimal(str(round(total, 2))),
                lines=lines,
                source=source or '',
                meta=meta or {},
                created_by=user,
            )
