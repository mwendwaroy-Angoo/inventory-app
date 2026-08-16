import datetime
import uuid
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
# UBA §7.2 (Sprint R1) — cross-tenant product dictionary + market benchmark
# ────────────────────────────────────────────────
# Privacy rules, non-negotiable (pre-answered decision #3 in
# docs/UBA_EXECUTION_ORDER.md, splitting the spec's single illustrative flag
# into two — names/barcodes/pack sizes are shared BY DEFAULT (opt-out
# available via Business.contribute_market_data), while buying/selling
# PRICES are opt-IN only (Business.contribute_price_data, default False) and
# never exposed below a county sample_size of 5. No business can ever query
# another named business's data — GlobalProduct/MarketPriceIndex carry no
# per-business price at all, only aggregates.

class GlobalProduct(models.Model):
    """Cross-tenant product dictionary — NAMES AND PACK SIZES ONLY, never a
    price. The 500th duka to scan a barcode already known to the platform
    gets its name/brand/pack pre-filled for free; the value comes purely
    from multi-tenancy, not from any one business's data being exposed."""
    barcode = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=160)
    brand = models.CharField(max_length=80, blank=True)
    pack_size = models.CharField(max_length=40, blank=True, help_text="e.g. '250g', '500ml', '2kg'")
    unit = models.CharField(max_length=20, blank=True)
    category = models.CharField(max_length=60, blank=True)
    image_url = models.URLField(blank=True)
    contributed_by = models.ForeignKey(
        'accounts.Business', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='global_products_contributed',
    )
    confirm_count = models.PositiveIntegerField(
        default=1, help_text='How many different businesses have agreed on this name.'
    )
    is_verified = models.BooleanField(default=False, help_text='True once confirm_count >= 3.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.barcode})"


class MarketPriceIndex(models.Model):
    """Anonymised, aggregated cost/price benchmark. NEVER exposes a single
    business's figures — only a county-level median, and only once
    sample_size >= 5 (see market_price.recompute_index()). A business whose
    own Business.contribute_price_data is False both contributes nothing to
    this and never sees any benchmark reading — opting out loses the
    benchmark, it does not just hide the opt-out business's own number."""
    global_product = models.ForeignKey(GlobalProduct, on_delete=models.CASCADE, related_name='price_indexes')
    county = models.ForeignKey(County, on_delete=models.SET_NULL, null=True, blank=True)
    median_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    median_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    sample_size = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('global_product', 'county')]

    def __str__(self):
        return f"{self.global_product.name} — {self.county or 'National'} (n={self.sample_size})"


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
    is_owner_alias = models.BooleanField(
        default=False,
        help_text='2026-08-13 live request (Roy): this Customer record is actually the '
                  'business owner, recorded under a name (e.g. "Bosco") that also happens '
                  'to be his own — NOT automatic name-matching (see Mmiliki Alichukua\'s own '
                  'design note on why that\'s deliberately avoided elsewhere), only ever set '
                  'via the explicit owner/manager-confirmed "🔗 Weka kama Mmiliki" action on '
                  'this customer\'s own debt profile. Once set, that action becomes a '
                  '"🔄 Sawazisha kwa Mmiliki" resync button — every currently-unpaid debt '
                  'transaction under this exact customer record is proposed (still via the '
                  'normal pending/accept step, never auto-moved) to the owner\'s own '
                  'OwnerConsumption ledger each time it\'s pressed, so debt that accumulates '
                  'under this name later doesn\'t silently pile up unnoticed. Also read by '
                  'tab_check_api to warn staff typing a SIMILAR (not exact) name at checkout '
                  '— "is this the same person as the owner?" — rather than guessing.',
    )
    last_cleared_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp when this customer last had their outstanding balance reach zero.',
    )
    dob = models.DateField(
        null=True, blank=True,
        help_text='Date of birth — used for birthday promotions.',
    )
    notes = models.TextField(
        blank=True,
        help_text='Internal notes about this customer (e.g. preferences, contact details).',
    )
    no_show_count = models.PositiveIntegerField(
        default=0,
        help_text='UBA §9.3 (Salon) — missed appointments. A repeat no-show can be required '
                  'to leave a booking deposit (PaymentPlan kind=BOOKING).'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

    @staticmethod
    def _propagate_name_change(business, old_name, new_name):
        """Rewrite every place a customer is referenced BY NAME STRING (not
        FK) from old_name to new_name — the shared engine behind both
        merge_locked (folding a second record's name into the kept one) and
        rename_locked (correcting one record's own name in place). Extracted
        2026-08-01 so a plain rename gets exactly the same "regardless of
        where the name appears in the system" guarantee merge already had:
        Transaction.recipient (the field the whole debt tracker is built
        on), BarTab.customer_name, and Receipt.customer_name — the last
        PLUS a symmetric linked_tab_ids union across every receipt either
        name ever received (see merge_locked's docstring for why a bare
        rename of customer_name alone would not be enough on its own).
        """
        if old_name == new_name:
            return
        Transaction.objects.filter(
            business=business, recipient__iexact=old_name,
        ).update(recipient=new_name)
        BarTab.objects.filter(
            business=business, customer_name__iexact=old_name,
        ).update(customer_name=new_name)
        receipts = list(
            Receipt.objects.filter(business=business, customer_name__iexact=new_name)
            | Receipt.objects.filter(business=business, customer_name__iexact=old_name)
        )
        if receipts:
            combined = set()
            for r in receipts:
                meta = r.meta or {}
                if meta.get('tab_id'):
                    combined.add(meta['tab_id'])
                combined.update(meta.get('linked_tab_ids') or [])
            for r in receipts:
                own_tab_id = (r.meta or {}).get('tab_id')
                r.meta = r.meta or {}
                r.meta['linked_tab_ids'] = sorted(combined - {own_tab_id})
                r.customer_name = new_name
                r.save(update_fields=['meta', 'customer_name'])

    @classmethod
    def rename_locked(cls, customer_id, business, new_name):
        """Correct one customer's own name in place — e.g. a plain typo, or
        settling on a canonical spelling ("General" instead of "Genro") —
        propagated everywhere that name appears via _propagate_name_change.
        Distinct from merge_locked: no second record is absorbed/deleted,
        just this one customer's identity is corrected. Returns the
        customer. Raises ValueError for a blank name or a customer that
        doesn't belong to this business.
        """
        new_name = (new_name or '').strip()
        if not new_name:
            raise ValueError('Jina jipya haliwezi kuwa tupu.')
        from django.db import transaction as _txn
        with _txn.atomic():
            customer = cls.objects.select_for_update().filter(id=customer_id, business=business).first()
            if not customer:
                raise ValueError('Mteja hakupatikana.')
            old_name = customer.name
            if old_name == new_name:
                return customer
            cls._propagate_name_change(business, old_name, new_name)
            customer.name = new_name
            customer.save(update_fields=['name'])
        return customer

    @classmethod
    def merge_locked(cls, keep_id, absorb_id, business):
        """Merge `absorb` into `keep` — the SAME real person recorded under two
        different name spellings (2026-07-31 live report: "Jenerali" vs "Genro"
        for one customer, and separately "McKenzie"/"Mckenzie" split a bar debt
        and a kitchen debt across two Customer rows so the customer's own
        wall-QR receipt only ever showed one of them). `Customer` has no
        unique_together on name and this app's own convention is exact/iexact
        matching only — a genuine spelling difference (not just case) has never
        been auto-reconciled anywhere, so this is a deliberate, owner-confirmed
        action: the owner picks the two records by id, not by fuzzy name match,
        so it works regardless of how different the two spellings are.

        Reassigns every place a customer is referenced, by FK or by name
        string, onto the kept identity:
          - Transaction.recipient (credit sales / debt — the field the whole
            debt tracker is built on)
          - BarTab.customer (FK) and .customer_name (display string)
          - CustomerDebtPayment.customer (FK, CASCADE — must move or the
            absorbed customer's whole payment history is destroyed by the
            delete below)
          - Payment.debt_customer (FK, SET_NULL — a pending/completed debt
            STK Push would otherwise silently lose its customer link)
          - Receipt.customer_name, PLUS a symmetric linked_tab_ids union
            across every receipt either name ever received — a receipt only
            shows the tabs listed in ITS OWN meta (core.receipt_views.
            _receipt_all_tab_ids), so simply renaming customer_name would NOT
            make an already-issued "Jenerali" receipt start showing the
            "Genro" tab too; every receipt in the merged group ends up
            pointing at the same combined tab set, so it no longer matters
            which specific QR/PIN the customer happens to use.

        Caller must be the one enforcing owner/manager-only — this is a
        financial-identity correction, same tier as every other correction
        tool in this app (Rekebisha, petty cash review, split-payment
        correction). Returns `keep`. Raises ValueError if keep == absorb or
        either id doesn't belong to this business.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            keep = cls.objects.select_for_update().filter(id=keep_id, business=business).first()
            absorb = cls.objects.select_for_update().filter(id=absorb_id, business=business).first()
            if not keep or not absorb:
                raise ValueError('Mteja hakupatikana.')
            if keep.id == absorb.id:
                raise ValueError('Huwezi kuunganisha mteja na yeye mwenyewe.')

            old_name = absorb.name
            new_name = keep.name

            BarTab.objects.filter(business=business, customer_id=absorb.id).update(customer=keep)
            CustomerDebtPayment.objects.filter(business=business, customer=absorb).update(customer=keep)
            Payment.objects.filter(debt_customer=absorb).update(debt_customer=keep)

            cls._propagate_name_change(business, old_name, new_name)

            absorbed_id = absorb.id
            absorb.delete()
        return keep, absorbed_id, old_name


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
    # 2026-07-25: deep-link to the record this notification is actually about, so
    # tapping it takes the reader straight into the story instead of just the
    # notifications list. Optional and additive — every existing call site keeps
    # working unchanged (blank = not clickable, notifications.html renders those as
    # plain cards exactly as before).
    link_url = models.CharField(max_length=300, blank=True, default='')

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

    # ── UBA §5.1 — Store as first-class outlet ──────────────────────────────
    STORE_TYPE_CHOICES = [
        ('bar', 'Bar'),
        ('kitchen', 'Jiko / Kitchen'),
        ('retail', 'Duka / Retail floor'),
        ('produce', 'Kibanda'),
        ('salon', 'Salon'),
        ('rental', 'Rentals'),
        ('warehouse', 'Godown / Store'),
        ('other', 'Nyingine'),
    ]
    store_type = models.CharField(
        max_length=20, default='retail', choices=STORE_TYPE_CHOICES,
        help_text='UBA capability model: what kind of outlet this store is.'
    )
    code = models.CharField(
        max_length=12, blank=True,
        help_text='Short code for this outlet — used on receipts, transfers, Paybill account (e.g. "KHY01").'
    )
    is_outlet = models.BooleanField(
        default=True,
        help_text='True = sells to customers. False = a godown/warehouse: holds stock, cannot sell.'
    )
    manager = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='managed_stores',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Deactivating blocks new sales at this store but preserves its history.'
    )
    opening_time = models.TimeField(null=True, blank=True)
    closing_time = models.TimeField(null=True, blank=True)
    target_daily_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True, help_text='Store line for SMS + storefront.')
    address_note = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

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

    def save(self, *args, **kwargs):
        # UBA §5.1 — keep store_type consistent with the existing,
        # load-bearing is_kitchen flag. Deliberately ONE-DIRECTIONAL
        # (is_kitchen -> store_type), NOT the spec's illustrative
        # bidirectional pseudocode (which also flips is_kitchen to False
        # whenever store_type != 'kitchen'). Found and ruled out during
        # this sprint: core.kitchen_views.get_or_create_kitchen_store()
        # (and any future call site) creates the kitchen Store via
        # `Store.objects.create(..., is_kitchen=True)` alone, never setting
        # store_type — the bidirectional version would silently flip that
        # store's is_kitchen back to False the moment anything saved it
        # again (store_type defaults to 'retail'), breaking the entire
        # kitchen module for every business the instant this shipped. This
        # mirrors Item.save()'s own precedent: legacy discriminators are
        # ground truth, the new UBA field is derived from them, never the
        # reverse. Setting store_type='kitchen' directly still implies
        # is_kitchen=True (the safe, additive direction) for any future
        # code that only knows about the new field.
        if self.store_type == 'kitchen':
            self.is_kitchen = True
        elif self.is_kitchen:
            self.store_type = 'kitchen'
        super().save(*args, **kwargs)

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
    created_at = models.DateTimeField(
        null=True, blank=True, auto_now_add=True,
        help_text='Null for items that existed before this field was added (2026-07-22) — '
                  'treated as "old enough" wherever this is used for that reason. Added '
                  'specifically so fresh_stock_count_checklist can tell a pre-reset item '
                  '(needs recounting) apart from one created after the reset (never had '
                  'anything to reconcile in the first place).'
    )
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

    # ── Universal Business Architecture — capability model (UBA §2.1) ──────
    # Declarative "how is this sellable thing counted" — the eight stock
    # models every future business type composes from. This is metadata
    # only for now: nothing outside save()'s own sync block reads it yet.
    # The existing discriminators (is_produce, produce_mode, is_keg,
    # is_kitchen_batch, produce_bunch_id, etc.) remain the load-bearing
    # ones every view/template/report already reads — do not remove them,
    # and do not change any of them to read stock_model instead without a
    # dedicated regression sweep (see CLAUDE.md's "discriminator
    # consistency" rule).
    STOCK_MODEL_CHOICES = [
        ('UNIT', 'Unit — countable (default)'),
        ('MEASURE', 'Measure — weight/volume, decanted from a bulk parent'),
        ('ENVELOPE', 'Envelope — revenue batch, no unit count'),
        ('VARIANT', 'Variant — one product, many sellable children (size/colour)'),
        ('SERIAL', 'Serial — each physical unit individually identified'),
        ('LOT', 'Lot — batch with its own expiry, FIFO depletion'),
        ('SERVICE', 'Service — time + skill, consumes supplies per a recipe'),
        ('ASSET', 'Asset — goes out, must come back'),
    ]
    stock_model = models.CharField(
        max_length=10, choices=STOCK_MODEL_CHOICES, default='UNIT',
        help_text='UBA capability model: how this item\'s stock is counted. '
                  'Kept in sync with is_produce/produce_mode in save() for '
                  'existing items — see Item.save().'
    )

    # ── UBA §7.2 (Sprint R1) — barcode + fast onboarding without a stock take ──
    barcode = models.CharField(
        max_length=32, blank=True, db_index=True,
        help_text='Scanned barcode, if any. Looked up against the cross-tenant '
                  'GlobalProduct dictionary — never unique per business, since '
                  'the same barcode legitimately exists at many different dukas.'
    )
    balance_confirmed_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When this item\'s on-hand balance was last established by a real '
                  'physical count — set by adjust_stock_balance() (Rekebisha) every '
                  'time it runs, and by the first-ever Receipt on a brand-new item '
                  '(fast onboarding: "Anza bila kuhesabu" never demands an opening '
                  'balance, so an item can go a while with this blank). NULL means '
                  '"never confirmed" — excluded from shrinkage attribution (would '
                  'generate false accusations against staff) and surfaced honestly '
                  'in stock_list as "Haijahesabiwa" rather than silently assumed.'
    )

    # ── UBA §7.4 (Sprint R3) — cycle counting / ABC classification ─────────
    ABC_CLASS_CHOICES = [
        ('A', 'A — juu ya thamani (hesabu kila wiki)'),
        ('B', 'B — wastani (hesabu kila mwezi)'),
        ('C', 'C — chini ya thamani (hesabu kila robo mwaka)'),
    ]
    abc_class = models.CharField(
        max_length=1, choices=ABC_CLASS_CHOICES, blank=True,
        help_text='Set by core.cycle_count.classify_abc_all() from 90-day revenue contribution '
                  '— top 80% of value=A, next 15%=B, rest=C. Blank until first classified.'
    )
    is_high_risk = models.BooleanField(
        default=False,
        help_text='High value × small size (razors, batteries, phone accessories, spirits '
                  'miniatures...) — force-included in every cycle count regardless of ABC class.'
    )

    # ── UBA §8.2 (Sprint A1) — variants (the boutique half) ─────────────────
    # Parent/child Items, deliberately NOT a separate ItemVariant table (see
    # core/variants.py's module docstring for why) — each variant IS an
    # ordinary Item with its own balance/cost/price/barcode/history, reusing
    # 100% of existing machinery. The parent is a display grouping only and
    # is never sold directly.
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='variants'
    )
    variant_label = models.CharField(max_length=60, blank=True, help_text="e.g. 'M / Navy'.")
    variant_attrs = models.JSONField(default=dict, blank=True, help_text="e.g. {'size':'M','colour':'Navy'}.")
    is_variant_parent = models.BooleanField(
        default=False, help_text='A display-grouping-only row — never sold directly.'
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
    raw_material_source = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='derived_batch_items',
        help_text='Kitchen batch items only: the raw-material Item this batch is drawn '
                  'from (e.g. Chipo → Potatoes (Raw)). When set, opening a new KitchenBatch '
                  'draws kg from this item\'s own tracked balance instead of a typed cost '
                  'guess — cost_total is derived automatically and the sack\'s remaining '
                  'balance stays visible on Kitchen Board, separate from whether today\'s '
                  'batch is done. Leave unset to keep the original manual cost-entry flow.'
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

    # ── Catalogue price-variance tracking ──────────────────────────────────
    source_catalog_entry = models.ForeignKey(
        'SupplierCatalogEntry', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='items',
        help_text='Set when this item was created via the "Add from Catalogue" bulk-add '
                  'screen from an uploaded supplier entry. Gives the price-variance report '
                  'an exact match instead of relying on fuzzy name matching. Items created '
                  'any other way (manually, or from the static catalogue) are matched by '
                  'name at report time instead.'
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

    def reserved_qty(self):
        """UBA §6.2 (Sprint P0-B) — physical stock held against an OPEN
        PaymentPlan (layaway). Reserved stock is not available stock: a
        layaway holds real goods, so `current_balance()` alone overstates
        what a NEW customer can actually be sold. Only OPEN plans reserve
        anything — completing/refunding/releasing/forfeiting a plan always
        flips status away from OPEN, so the reservation clears automatically
        the instant any of those inverse actions runs, with no separate
        "release the hold" bookkeeping step needed here."""
        total = self.payment_plan_reservations.filter(
            status='OPEN'
        ).aggregate(models.Sum('reserved_qty'))['reserved_qty__sum']
        return total or 0

    def available_balance(self):
        """current_balance() minus reserved_qty() — the number a NEW sale
        must actually check against, so two customers are never sold the
        same reserved dress. NOT swept into every existing display surface
        this pass (stock_list.html, item_detail.html, etc. still show plain
        current_balance()) — documented deferral, same discipline as every
        other UBA sprint's template work; wired into Quick Sell's checkout
        stock check instead, the one surface where overselling a reserved
        item actually causes the harm this exists to prevent."""
        return self.current_balance() - self.reserved_qty()

    def capped_deduction(self, requested_qty):
        """2026-08-07 live request (Roy: "negative balances should never be
        there, stock is either or is not") — the shared floor-at-zero
        helper for every code path that deducts stock AFTER the point where
        blocking the sale outright is no longer an option (an M-Pesa STK
        payment already confirmed, a table order already physically
        served). Quick Sell's own live checkout already refuses a sale
        outright when available_balance() is insufficient — that pattern
        stays as-is for anything still interactive (see quick_sell(),
        _kitchen_checkout()'s plain-item branch). This is for the *other*
        half: a real audit (2026-08-07) found several settlement/
        confirmation paths (STK callbacks, confirm_prompt, table-order
        SERVED conversion) that created a stock-deducting Transaction with
        NO balance check anywhere in their lifetime — the actual root
        cause of a plain item's balance drifting to e.g. -99 while a hard
        block existed elsewhere in the app.

        Returns (deductible, shortfall) — deductible is what can actually
        come off the shelf without the balance going negative (never more
        than requested_qty, never enough to push available_balance() below
        zero); shortfall is requested_qty - deductible (0 when stock was
        sufficient). Callers must still create their Transaction for the
        FULL requested_qty's revenue/receipt — the money already moved or
        the item was already served — but should use `deductible` as the
        Transaction's qty and raise a BusinessException(kind='shrinkage')
        for any non-zero shortfall so the owner can investigate rather than
        the balance silently going negative with no trace of why.
        """
        available = self.available_balance()
        if available < 0:
            available = Decimal('0')
        deductible = min(Decimal(str(requested_qty)), available)
        shortfall = Decimal(str(requested_qty)) - deductible
        return deductible, shortfall

    def variant_summary(self):
        """UBA §8.2 (Sprint A1) — for a parent Item's stock-list collapse
        row: total balance across all its variant children, and their price
        range. Only meaningful when `is_variant_parent` is True; for an
        ordinary item this just returns zeroed/empty values."""
        children = list(self.variants.all())
        if not children:
            return {'total_balance': 0, 'min_price': None, 'max_price': None, 'variant_count': 0}
        total_balance = sum(c.current_balance() for c in children)
        prices = [c.selling_price for c in children if c.selling_price is not None]
        return {
            'total_balance': total_balance,
            'min_price': min(prices) if prices else None,
            'max_price': max(prices) if prices else None,
            'variant_count': len(children),
        }

    def physical_balance(self):
        total_movement = self.transactions.aggregate(models.Sum('qty'))['qty__sum'] or 0
        return self.opening_physical + total_movement

    def deficit(self):
        return max(0, self.current_balance() - self.physical_balance())

    def surplus(self):
        return max(0, self.physical_balance() - self.current_balance())

    # --- Demand & reorder helpers (basic demand-driven heuristics) ---
    def avg_daily_issues(self, window_days=30):
        """Average daily issues (sales) over the past `window_days` days.

        Includes 'Draw' transactions too — for a raw-material item feeding a
        KitchenBatch (Item.raw_material_source), a kitchen draw IS the real
        depletion demand, even though it isn't a customer sale (type='Issue').
        Without this, reorder recommendations for the sack would never reflect
        how fast it's actually being used.
        """
        since = timezone.now().date() - datetime.timedelta(days=window_days)
        total = self.transactions.filter(type__in=['Issue', 'Draw'], date__gte=since).aggregate(models.Sum('qty'))['qty__sum'] or 0
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

    def save(self, *args, **kwargs):
        # UBA §2.1 — keep stock_model in sync with the existing, load-bearing
        # discriminators for every item type this app already builds. Only
        # fires when one of these legacy flags is set; a plain item (or a
        # future VARIANT/SERIAL/LOT/SERVICE/ASSET item nothing here creates
        # yet) keeps whatever stock_model it already has.
        if self.is_produce and self.produce_mode == 'BUNCH':
            self.stock_model = 'ENVELOPE'
        elif self.is_produce:
            self.stock_model = 'UNIT'
        elif self.is_keg:
            self.stock_model = 'MEASURE'
        elif self.is_kitchen_batch:
            self.stock_model = 'ENVELOPE'
        super().save(*args, **kwargs)

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
        ('OwnerConsumption', _('Owner Consumption')),
        ('Draw', _('Kitchen Batch Draw')),
        ('Transfer', _('Stock Transfer Between Stores')),
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
    preset = models.ForeignKey(
        'ItemPortionPreset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales',
        help_text=(
            "Which portion preset was actually sold, e.g. 'Paja Nusu' vs 'Paja Nzima' on a "
            "shared 'Kuku' item. 2026-07-28: closes the gap flagged when per-preset cost_price "
            "was built — without this, cost() had no way to know WHICH cut was sold and fell "
            "back to the item's single blended cost_price for every preset, which breaks the "
            "moment two presets share a price (e.g. a half chicken leg and a drumstick both "
            "sold at KES 150) but have different real costs. See Transaction.cost()."
        ),
    )
    transfer = models.ForeignKey(
        'StockTransfer', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions',
        help_text=(
            'UBA §5.2 — set on both legs of a stock transfer between stores '
            '(the dispatch Issue and the receive Receipt). Discriminator that '
            'excludes a transfer from revenue()/cost() everywhere — a transfer '
            'is a stock movement, never a sale.'
        ),
    )
    service = models.ForeignKey(
        'Service', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales',
        help_text='UBA §9.2 (Salon) — set on the shadow-item Issue transaction when this '
                  'is a completed service sale, and on each recipe-line supply deduction.'
    )
    performed_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='services_performed',
        help_text='UBA §9.2 (Salon) — the STYLIST who performed the service, distinct from '
                  '`recorded_by` (the cashier/whoever rang it up). Never set for non-salon sales.'
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
    recorded_by = models.ForeignKey(
        'auth.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='recorded_transactions',
        help_text='The staff member or owner who recorded this transaction. Null for async/system-generated transactions.',
    )
    settles_transaction = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='settlements',
        help_text=(
            "2026-08-07 live request — 'no way for the owner to pay for what he was "
            "given through Mmiliki Alichukua'. Set ONLY on the real, revenue-bearing "
            "Issue transaction (qty=0 — the stock already left at consumption time, "
            "this only records money coming in) created when an OwnerConsumption draw "
            "is settled — points back at the original draw. Presence of any row in "
            "`<draw>.settlements` IS the paid flag; no separate boolean needed. Generic "
            "field name/self-FK (not owner-consumption-specific) in case a future "
            "settlement flow needs the same shape."
        ),
    )
    was_credit = models.BooleanField(
        default=False,
        help_text=(
            "2026-08-15 live report (Roy, Monsoon Inn) — 'Total Paid' exceeding "
            "'Total Credit' on the debt tracker, with genuinely-still-owed items "
            "silently vanishing from 'Unpaid Credit Transactions'. Root cause: "
            "core.debt_views._get_customer_debt_data()'s 'Total Credit' figure "
            "summed transactions LIVE-filtered on payment_method=='credit' right "
            "now — but at least ~15 separate settle paths (tick_entry, settle_tab, "
            "settle_entries_amount_locked, the STK-push tab-settlement callbacks, "
            "and _do_settle_debt_payment's own FIFO reconciliation among them) all "
            "flip payment_method AWAY from 'credit' the instant a transaction is "
            "resolved — correct for shift_views._reconcile()'s cash/mpesa/credit "
            "split, but it meant the debt tracker's own 'Total Credit' silently "
            "SHRANK by that same amount the moment ANY of those paths resolved a "
            "transaction, while CustomerDebtPayment ('Total Paid', append-only) "
            "never shrank to match — eventually producing Paid > Credit and "
            "'all paid' showing on customers who genuinely still owed money. "
            "This field is the fix: a permanent, one-way marker (never cleared "
            "once set) stamped automatically the moment payment_method transitions "
            "AWAY from 'credit' on an EXISTING row (see __init__/save() below) — "
            "_get_customer_debt_data()'s 'Total Credit' now sums was_credit=True "
            "OR still-currently-credit transactions, making it a stable historical "
            "total that can never shrink out from under total_paid again. Achieved "
            "at the model layer specifically so it automatically covers every one "
            "of those ~15 existing (and any future) settle call sites with zero "
            "changes needed to any of them — a naive per-view sweep risked missing "
            "one, exactly the failure mode that caused this bug in the first place."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Snapshot of payment_method AS LOADED FROM THE DATABASE (Model.from_db
        # constructs via cls(*values), so this correctly captures the real
        # pre-existing value for a fetched row — never meaningful for a brand
        # new, not-yet-saved instance, which save() below guards against via
        # `self.pk is not None`).
        self._loaded_payment_method = self.payment_method

    def save(self, *args, **kwargs):
        # Only a transition INTO a real payment (cash/mpesa, never 'void' — a
        # voided debt is written off, not paid, and must never look
        # permanently unpaid-forever by staying counted here with no
        # matching CustomerDebtPayment) from 'credit' can possibly mean this
        # transaction was ever genuinely debt-tracked.
        if (
            self.pk is not None
            and self._loaded_payment_method == 'credit'
            and self.payment_method in ('cash', 'mpesa')
        ):
            # An ordinary tab item is ALSO briefly payment_method='credit'
            # while the tab is OPEN (KegBarrel.record_sale etc: `pay =
            # 'credit' if tab else ...`) — that's an internal "not yet
            # collected" marker, not real debt, and _get_customer_debt_data's
            # own credit_qs already correctly excludes it via
            # tab_entry__tab__status='OPEN'. Only stamp was_credit when this
            # transaction's tab (if any) is NOT still OPEN at the moment of
            # this transition — i.e. it was ALREADY being counted as real
            # debt (SETTLED via conversion) when it got resolved, or there's
            # no tab at all (a direct credit sale, debt from creation).
            try:
                tab_status = self.tab_entry.tab.status
            except Exception:
                tab_status = None
            if tab_status != 'OPEN':
                self.was_credit = True
                update_fields = kwargs.get('update_fields')
                if update_fields is not None and 'was_credit' not in update_fields:
                    kwargs['update_fields'] = list(update_fields) + ['was_credit']
        super().save(*args, **kwargs)
        self._loaded_payment_method = self.payment_method

    def revenue(self):
        # UBA §5.2 — a stock-transfer leg (type='Transfer', see the Draw-type
        # precedent this mirrors) is a movement, never a sale — already
        # excluded by the type check below, since it's never 'Issue'. The
        # explicit transfer_id check is defense-in-depth: it means a transfer
        # leg returns 0 revenue even if something ever mistakenly creates one
        # with type='Issue' instead, so this can never be silently bypassed.
        if self.transfer_id:
            return 0
        if self.type != 'Issue':
            return 0
        if self.sale_amount is not None:
            return float(self.sale_amount)
        if self.item.selling_price:
            return abs(float(self.qty)) * float(self.item.selling_price)
        return 0

    def cost(self):
        if self.transfer_id:
            return 0
        if self.type != 'Issue':
            return 0
        return self._stock_movement_cost()

    def _stock_movement_cost(self):
        """The KES cost basis behind this transaction's own qty/sale_amount —
        the keg/bunch/batch/preset-aware proportional logic shared by cost()
        (for a real sale) and loss_value() (for a Wastage/OwnerConsumption
        stock movement that never had revenue at all). Split out 2026-08-11
        (live report — Roy: the Analytics "Hasara/Losses" tile showed a
        Voids figure in the TENS OF MILLIONS on a business doing ~KES 150k of
        revenue for the period). Root cause: analytics_views.py's wastage_loss/
        void_loss/owner_drawings_cost each reimplemented a naive
        `abs(qty) * item.cost_price` formula instead of calling this — correct
        ONLY for a plain, non-keg/non-bunch/non-batch/non-preset item, and
        catastrophically wrong for a keg pour specifically: qty there is
        stored in ML while item.cost_price is priced per WHOLE KEG (tens of
        thousands of KES), so a single voided 500ml pour naively priced as
        `500 * cost_per_keg` inflates by a factor of ~1000x — more than
        enough to produce a multi-million-shilling phantom loss from a
        handful of voided pours, exactly the reported scale. void_loss's
        txns are always type='Issue' (payment_method='void'), so cost()
        itself already computes them correctly — the bug was specifically
        that void_loss never called it. wastage_loss/owner_drawings_cost
        needed this new helper since cost() deliberately zeroes non-Issue
        types (by design, so a Wastage/Draw movement doesn't get double-
        counted as if it were also a sale).
        """
        # Keg barrel pours: qty is stored in ml — must NOT be multiplied by KES cost_price.
        # Use proportional cost: sale_amount * (barrel_cost / barrel_target).
        if self.keg_barrel_id:
            barrel = self.keg_barrel
            if barrel and float(barrel.target_revenue or 0) > 0 and self.sale_amount is not None:
                return float(self.sale_amount) * float(barrel.cost_price) / float(barrel.target_revenue)
            return 0
        # Bunch sales/discards carry their cost on the bunch, not the item —
        # qty here is a fraction of the bunch's TARGET REVENUE (see
        # ProduceBunch._fraction()/.discard()), so it must be multiplied by
        # the bunch's own cost_price, never item.cost_price (which isn't even
        # the same unit of account for a bunch-tracked produce item).
        if self.produce_bunch_id and self.produce_bunch and self.produce_bunch.cost_price:
            return abs(float(self.qty)) * float(self.produce_bunch.cost_price)
        # Kitchen batch sales: qty is a constant -1 per sale (not a real unit count),
        # so falling through to abs(qty) * item.cost_price would return the WHOLE
        # batch's cost_total on every single sale (item.cost_price is deliberately
        # set to cost_total, not a per-unit price — discard()'s wastage math relies
        # on that). Use the same proportional-share approach as keg_barrel above,
        # but against revenue_collected (actual) since KitchenBatch has no fixed
        # target: sum of cost() across every sale from one batch then equals
        # cost_total exactly, instead of N × cost_total. Found 2026-07-22 while
        # designing raw-material sack tracking — a real, pre-existing overcounting
        # bug in Kitchen Performance / overall COGS for any batch sold more than once.
        # discard()'s own Wastage row deliberately sets sale_amount=0 (nothing was
        # sold) and qty = -(unrecovered fraction of cost_total) — abs(qty) *
        # item.cost_price (item.cost_price == cost_total for a batch item) is what
        # correctly reduces to "the unrecovered value" for that specific row.
        # Checking `self.sale_amount` (truthy) rather than `is not None` matters
        # here — a discard row's sale_amount is exactly 0, not None, and a real
        # sale can never legitimately be for KES 0 through this mechanism, so
        # truthiness cleanly tells a genuine sale apart from a discard row even
        # when the batch had prior revenue_collected > 0 (found while writing
        # this: `is not None` would have taken the proportional branch for a
        # discard row too, computing 0 * cost_total / revenue_collected = 0
        # instead of correctly falling through to the item.cost_price branch).
        if self.kitchen_batch_id and self.kitchen_batch:
            batch = self.kitchen_batch
            if self.sale_amount and float(batch.revenue_collected or 0) > 0:
                return float(self.sale_amount) * float(batch.cost_total) / float(batch.revenue_collected)
            if self.type == 'Issue':
                return 0
            # fall through to the item.cost_price branch for a discard() row
        # Preset-attributed cost (2026-07-28) — e.g. one shared "Kuku" item sold via
        # several presets (Bawa/Paja Nzima/Paja Nusu) that don't all cost the same
        # per piece. preset.cost_price is per whole base-item-unit (set at receiving
        # time via Kitchen Stock Receipt); qty already IS that preset's
        # quantity_consumed (e.g. -0.5 for a half leg), so the same
        # abs(qty) * cost formula used below for item.cost_price applies unchanged —
        # no proportional-envelope math needed, this is an exact quantity × unit cost.
        # Falls back to item.cost_price when the preset has no cost of its own yet
        # (the ordinary, unchanged case for every preset that isn't opted into this).
        if self.preset_id and self.preset and self.preset.cost_price is not None:
            return abs(float(self.qty)) * float(self.preset.cost_price)
        if self.item.cost_price:
            return abs(float(self.qty)) * float(self.item.cost_price)
        return 0

    def loss_value(self):
        """KES value of a Wastage/OwnerConsumption stock movement — same
        proportional keg/bunch/batch/preset-aware logic as cost(), but usable
        for transaction types cost() deliberately zeroes (see cost()'s own
        type gate). Never meant for type='Issue' — use cost()/revenue() for
        an actual sale."""
        if self.transfer_id:
            return 0
        if self.type not in ('Wastage', 'OwnerConsumption'):
            return 0
        return self._stock_movement_cost()

    def profit(self):
        return self.revenue() - self.cost()

    @classmethod
    def split_payment_method_locked(cls, txn_id, business, split_amount, new_method, staff_user=None, recipient=None):
        """Split a DIRECT-sale (no tab_entry — Quick Sell/bar/kitchen walk-up
        checkout) Issue transaction's amount across two payment methods
        (2026-07-26 live request) — e.g. a KES 500 sale entered entirely as
        M-Pesa when the customer actually paid 200 cash + 300 mpesa. Reduces
        the original transaction's amount to (original − split_amount),
        keeping its existing payment_method, and creates a NEW sibling
        transaction for split_amount tagged new_method.

        Mirrors BarTabEntry.split_paid_unpaid_locked's qty=0 remainder
        pattern — this re-bills an already-sold item, no additional stock
        leaves the shelf — and copies keg_barrel/produce_bunch/kitchen_batch
        so Transaction.cost()'s proportional-share formula still attributes
        correctly across both rows (same reasoning as that method's
        docstring). Total revenue across both rows is exactly the original
        total — this can never inflate or deflate cash/mpesa reconciliation,
        only correct which channel collected which part.

        Returns (original_txn, new_txn). Caller must hold no prior lock —
        this acquires its own via select_for_update().
        recipient (2026-08-12 live request, Roy — a Chipo sale from a past
        day, KES 50 mpesa + KES 50 owed by the customer, had no way to
        record the debt half at all): new_method may also be 'credit', in
        which case `recipient` (the customer's name) is REQUIRED and is
        written onto the split-off sibling transaction — same created_at-
        copy behavior as cash/mpesa above, so the debt correctly lands on
        the SAME historical date as the original (backdated) sale, not
        "today". The caller (split_transaction_payment_method view) is
        responsible for the Customer record / SMS side effects, matching
        this method's existing scope (Transaction-level mechanics only).
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            txn = cls.objects.select_for_update().get(pk=txn_id, business=business)
            # tab_entry is a reverse OneToOne accessor (from BarTabEntry.transaction),
            # not a physical column — no _id shortcut exists; must try/except.
            try:
                has_tab_entry = txn.tab_entry is not None
            except Exception:
                has_tab_entry = False
            if txn.type != 'Issue' or has_tab_entry:
                raise ValueError('Muamala huu hauwezi kugawanywa — si mauzo ya moja kwa moja.')
            if txn.payment_method not in ('cash', 'mpesa'):
                raise ValueError('Njia ya malipo ya sasa haiwezi kugawanywa.')
            if new_method not in ('cash', 'mpesa', 'credit') or new_method == txn.payment_method:
                raise ValueError('Chagua njia tofauti ya malipo kwa sehemu ya pili.')
            recipient = (recipient or '').strip()
            if new_method == 'credit' and not recipient:
                raise ValueError('Jina la mteja anayedaiwa linahitajika.')

            original_amount = float(txn.revenue())
            split_amount = float(split_amount)
            if split_amount <= 0 or split_amount >= original_amount:
                raise ValueError('Kiasi cha mgawanyo lazima kiwe kati ya 0 na jumla ya mauzo.')

            remaining = round(original_amount - split_amount, 2)
            txn.sale_amount = Decimal(str(remaining))
            txn.save(update_fields=['sale_amount'])

            new_txn = cls.objects.create(
                item=txn.item, business=txn.business, type='Issue',
                qty=Decimal('0'), sale_amount=Decimal(str(round(split_amount, 2))),
                payment_method=new_method,
                recipient=(recipient if new_method == 'credit' else txn.recipient),
                invoice_no=txn.invoice_no,
                recorded_by=staff_user or txn.recorded_by,
                date=txn.date,
                # created_at copied from the original (2026-08-07 fix, found
                # while wiring Quick Sell's checkout-time backdate feature):
                # without this the split-off remainder always defaulted to
                # "now" regardless of when/how the original sale itself was
                # dated, silently landing in TODAY's shift/revenue window even
                # when the original transaction was correctly backdated —
                # exactly defeating a backdate+split combination at checkout.
                # Also more correct for the pre-existing Recent-Payments
                # correction flow: a split-off portion should read as having
                # happened at the same time as the sale it was split from,
                # not at correction-click time.
                created_at=txn.created_at,
                keg_barrel_id=txn.keg_barrel_id,
                produce_bunch_id=txn.produce_bunch_id,
                kitchen_batch_id=txn.kitchen_batch_id,
            )
            return txn, new_txn

    @classmethod
    def apply_split_payment_locked(cls, txn_ids, business, split_amount, split_method, staff_user=None):
        """Checkout-time sibling of split_payment_method_locked() (2026-07-28
        live request — Roy: tabs have always supported a customer paying part
        cash + part M-Pesa, but a DIRECT sale — e.g. Chipo at KES 100, paid as
        40 cash + 60 mpesa — could only be split as a LATER correction, not at
        the point of sale itself). Given the just-created direct-sale Issue
        transaction ids from ONE checkout (all sharing a single PRIMARY
        payment method), reallocates split_amount worth of them to
        split_method.

        Walks the transactions in id (creation) order, converting whole ones
        as they fit, then splits the boundary transaction via
        split_payment_method_locked() for any remainder that doesn't land on
        a transaction boundary — the same walk-and-split shape already
        proven by BarTab.settle_entries_amount_locked() for partial tab
        settlement. Total revenue across the whole batch is unchanged either
        way — this only ever relabels which channel collected which part,
        never creates or destroys revenue.

        No-ops silently when split_amount is None/<=0 (the normal case — no
        split requested; every checkout calls this, most will pass nothing).
        Raises ValueError if split_amount >= the batch total (would leave
        nothing on the primary method — not a genuine split).

        Returns the full list of transaction ids that now make up this sale
        — the original txn_ids PLUS any new sibling row created by the
        boundary split (found and fixed 2026-07-31, while wiring up receipt
        split-payment display: split_payment_method_locked() creates a NEW
        Transaction row for the split-off remainder rather than reusing an
        existing id, so a caller that keeps using the original txn_ids list
        for payment_split_breakdown() would silently miss that new row —
        the exact case a single-item cart hits every time, since a lone
        transaction can only be split via the boundary path, never the
        whole-transaction-conversion path). Callers MUST use this return
        value (falling back to the original txn_ids when None/no split
        happened) rather than their own original list.
        """
        if not txn_ids or split_amount is None:
            return None
        split_amount = float(split_amount)
        if split_amount <= 0:
            return None
        from django.db import transaction as _txn
        with _txn.atomic():
            txns = list(
                cls.objects.select_for_update()
                .filter(id__in=txn_ids, business=business, type='Issue')
                .order_by('id')
            )
            total = sum(float(t.revenue()) for t in txns)
            if split_amount >= total:
                raise ValueError('Kiasi cha mgawanyo lazima kiwe kidogo kuliko jumla ya mauzo.')

            all_ids = [t.id for t in txns]
            remaining = split_amount
            for t in txns:
                if remaining <= 0:
                    break
                if t.payment_method not in ('cash', 'mpesa') or t.payment_method == split_method:
                    continue
                amt = float(t.revenue())
                if amt <= 0:
                    continue
                if remaining >= amt - 0.005:
                    t.payment_method = split_method
                    t.save(update_fields=['payment_method'])
                    remaining = round(remaining - amt, 2)
                else:
                    _orig, new_txn = cls.split_payment_method_locked(
                        txn_id=t.id, business=business,
                        split_amount=Decimal(str(remaining)), new_method=split_method,
                        staff_user=staff_user,
                    )
                    all_ids.append(new_txn.id)
                    remaining = 0
            return all_ids

    @classmethod
    def split_to_credit_locked(cls, txn_id, business, split_amount, recipient, staff_user=None):
        """UBA P0-A — boundary-split sibling of split_payment_method_locked(),
        for a direct sale's UNPAID remainder becoming credit rather than a
        second cash/mpesa channel (Kibanda's "Lipa kidogo" gap: KES 100
        mboga, customer hands over 60, the other 40 becomes an ordinary
        debt against their name, in the SAME checkout action — no tab
        needed, matching the bar/kitchen tab-based partial-settle-to-debt
        feature Quick Sell never got).

        Deliberately a SEPARATE method rather than widening
        split_payment_method_locked() itself — that function is narrowly,
        deliberately cash/mpesa-only and shared with the post-hoc
        correction endpoint (split_transaction_payment_method); adding a
        credit destination there would need a wider regression sweep than
        this warrants. This mirrors its shape instead.

        Reduces the original transaction's amount to (original -
        split_amount), keeping its existing cash/mpesa payment_method, and
        creates a NEW sibling transaction for split_amount tagged
        payment_method='credit', recipient=recipient — the exact shape the
        debt tracker already expects, so no debt-tracker code changes.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            txn = cls.objects.select_for_update().get(pk=txn_id, business=business)
            try:
                has_tab_entry = txn.tab_entry is not None
            except Exception:
                has_tab_entry = False
            if txn.type != 'Issue' or has_tab_entry:
                raise ValueError('Muamala huu hauwezi kugawanywa — si mauzo ya moja kwa moja.')
            if txn.payment_method not in ('cash', 'mpesa'):
                raise ValueError('Njia ya malipo ya sasa haiwezi kugawanywa.')
            if not recipient:
                raise ValueError('Jina la mteja linahitajika kwa deni.')

            original_amount = float(txn.revenue())
            split_amount = float(split_amount)
            if split_amount <= 0 or split_amount >= original_amount:
                raise ValueError('Kiasi cha deni lazima kiwe kati ya 0 na jumla ya mauzo.')

            remaining = round(original_amount - split_amount, 2)
            txn.sale_amount = Decimal(str(remaining))
            txn.save(update_fields=['sale_amount'])

            new_txn = cls.objects.create(
                item=txn.item, business=txn.business, type='Issue',
                qty=Decimal('0'), sale_amount=Decimal(str(round(split_amount, 2))),
                payment_method='credit', recipient=recipient,
                invoice_no=txn.invoice_no,
                recorded_by=staff_user or txn.recorded_by,
                date=txn.date,
                keg_barrel_id=txn.keg_barrel_id,
                produce_bunch_id=txn.produce_bunch_id,
                kitchen_batch_id=txn.kitchen_batch_id,
            )
            return txn, new_txn

    @classmethod
    def split_credit_paid_unpaid_locked(cls, txn_id, business, paid_amount, paid_method, staff_user=None):
        """2026-08-16 live request (Roy): "customer acquisition of an item
        and partial payment is going to the owner" — a customer buys
        something already on credit (no tab, e.g. a plain Quick Sell
        "Deni"), pays PART of it themselves right now, and the OWNER
        agrees to cover the rest (see OwnerConsumptionTransferRequest.
        propose_to_owner_partial_locked, the caller that wires this in).

        The non-tab mirror of BarTabEntry.split_paid_unpaid_locked's shape:
        reduces the original Transaction in place to paid_amount (payment_
        method flips to paid_method — a REAL payment, right now), and
        creates a NEW sibling transaction for the remainder, still tagged
        payment_method='credit' under the SAME recipient — an ordinary,
        still-owed debt, exactly like the tab-linked case's remainder entry
        stays on the source tab until a transfer request is accepted. The
        caller then proposes THAT remainder transaction to the owner via
        the normal propose_to_owner_locked path — rejecting it needs zero
        reversal, since the money never left the customer's name until
        accepted.

        Only ever operates on a genuinely non-tab credit transaction (no
        tab_entry) — a tab-linked one must go through split_paid_unpaid_
        locked instead, which already handles the SETTLED-tab (debt-
        converted) case correctly; this method deliberately rejects one.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            txn = cls.objects.select_for_update().get(pk=txn_id, business=business)
            try:
                has_tab_entry = txn.tab_entry is not None
            except Exception:
                has_tab_entry = False
            if txn.type != 'Issue' or has_tab_entry:
                raise ValueError('Muamala huu hauwezi kugawanywa hivi — ni wa tab, si deni la moja kwa moja.')
            if txn.payment_method != 'credit':
                raise ValueError('Muamala huu si deni.')
            if paid_method not in ('cash', 'mpesa'):
                raise ValueError('Njia ya malipo si sahihi.')

            original_amount = float(txn.revenue())
            paid_amount = float(paid_amount)
            if paid_amount <= 0 or paid_amount >= original_amount:
                raise ValueError('Kiasi cha kulipa lazima kiwe kati ya 0 na jumla ya deni.')

            remaining = round(original_amount - paid_amount, 2)
            recipient = txn.recipient
            txn.sale_amount = Decimal(str(round(paid_amount, 2)))
            txn.payment_method = paid_method
            txn.save(update_fields=['sale_amount', 'payment_method'])

            new_txn = cls.objects.create(
                item=txn.item, business=txn.business, type='Issue',
                qty=Decimal('0'), sale_amount=Decimal(str(remaining)),
                payment_method='credit', recipient=recipient,
                invoice_no=txn.invoice_no,
                recorded_by=staff_user or txn.recorded_by,
                date=txn.date, created_at=txn.created_at,
                keg_barrel_id=txn.keg_barrel_id,
                produce_bunch_id=txn.produce_bunch_id,
                kitchen_batch_id=txn.kitchen_batch_id,
            )
            return txn, new_txn

    @classmethod
    def apply_checkout_partial_credit_locked(cls, txn_ids, business, amount_paid, recipient, staff_user=None):
        """UBA P0-A — Kibanda split-tender-at-checkout: one direct sale,
        customer pays only `amount_paid` (already recorded as cash/mpesa on
        every transaction in txn_ids — the caller checks out normally with
        a single primary payment_method first, exactly like a plain
        cash/mpesa/credit sale), and the shortfall becomes ordinary credit
        debt against `recipient` — the exact payment_method='credit'/
        recipient=name shape the debt tracker already reads, so no
        debt-tracker code changes at all.

        Walks txn_ids from the end (same walk-and-split shape as
        apply_split_payment_locked, just converting the LAST-recorded
        lines to credit first — an arbitrary but deterministic choice;
        which specific line becomes credit never matters, only that the
        totals reconcile), converting whole transactions to credit once
        the credit remainder is covered, then boundary-splits the
        transaction that straddles the paid/credit line via
        split_to_credit_locked().

        No-ops (returns None) when amount_paid is None or >= the batch
        total — nothing left to convert; that was a full cash/mpesa sale,
        not a partial one.

        The CALLER is responsible for gating the CREDIT REMAINDER (not the
        full sale total) through core.credit_policy.evaluate_credit()
        BEFORE calling this — computed against (total - amount_paid), the
        actual new debt being extended, same as the existing full-credit
        checkout gate but against the smaller, real number rather than the
        whole cart.
        """
        if not txn_ids or amount_paid is None:
            return None
        amount_paid = float(amount_paid)
        if amount_paid < 0:
            raise ValueError('Kiasi kilicholipwa hakiwezi kuwa hasi.')
        if not recipient:
            raise ValueError('Jina la mteja linahitajika kwa deni.')

        from django.db import transaction as _txn
        with _txn.atomic():
            txns = list(
                cls.objects.select_for_update()
                .filter(id__in=txn_ids, business=business, type='Issue')
                .order_by('id')
            )
            total = sum(float(t.revenue()) for t in txns)
            if amount_paid >= total:
                return None  # fully paid — nothing to convert

            all_ids = [t.id for t in txns]
            remaining_credit = round(total - amount_paid, 2)
            for t in reversed(txns):
                if remaining_credit <= 0:
                    break
                if t.payment_method not in ('cash', 'mpesa'):
                    continue
                amt = float(t.revenue())
                if amt <= 0:
                    continue
                if remaining_credit >= amt - 0.005:
                    t.payment_method = 'credit'
                    t.recipient = recipient
                    t.save(update_fields=['payment_method', 'recipient'])
                    remaining_credit = round(remaining_credit - amt, 2)
                else:
                    _orig, new_txn = cls.split_to_credit_locked(
                        txn_id=t.id, business=business,
                        split_amount=Decimal(str(remaining_credit)), recipient=recipient,
                        staff_user=staff_user,
                    )
                    all_ids.append(new_txn.id)
                    remaining_credit = 0
            return all_ids

    @classmethod
    def payment_split_breakdown(cls, txn_ids, business):
        """Sum revenue per payment_method across the given transaction ids —
        2026-07-30 live report: "split payments are working well... but the
        receipt does not show the same information." apply_split_payment_
        locked() only ever touches the underlying Transaction rows; Receipt
        itself is a static snapshot (lines + one overall payment_method)
        taken at checkout time and never re-reads them, so a split was
        completely invisible on the receipt. Called right after apply_
        split_payment_locked() succeeds so the true final split (however
        the walk-and-split algorithm distributed it across possibly
        several items) can be embedded in Receipt.meta. Returns {} when
        there's nothing to show (no split happened) — {'cash': 20.0,
        'mpesa': 80.0} style dict otherwise, only methods with a positive
        total included.
        """
        if not txn_ids:
            return {}
        txns = cls.objects.filter(id__in=txn_ids, business=business, type='Issue')
        breakdown = {}
        for t in txns:
            amt = float(t.revenue())
            if amt <= 0:
                continue
            breakdown[t.payment_method] = breakdown.get(t.payment_method, 0.0) + amt
        if len(breakdown) < 2:
            return {}  # no real split to show — one method covered everything
        return {k: round(v, 2) for k, v in breakdown.items()}

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

    Currently ORPHANED (found in the analytics module audit, 2026-07-21):
    the management commands that used to populate this (forecast.py,
    precompute_forecasts.py) were deleted in commit ad99715 ("purge: delete
    old pandas/matplotlib forecast infrastructure completely"). The live
    "Run Forecast" button on the analytics dashboard now calls forecast_api
    (core/views.py) -> core/forecast_engine.py, which computes on demand and
    never persists a Forecast row. Nothing in the codebase currently creates
    one. Kept (not deleted) in case a future caching/snapshot layer revives
    it — do not assume rows exist here.
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
        ('entertainment', _('Entertainment / DJ / MC Fees')),
        ('security', _('Security & Facilitation')),
        ('petty_cash', _('Petty Cash (Counter Drawdowns)')),
        ('other', _('Other')),
    ]

    STATION_CHOICES = [
        ('bar', _('Bar')),
        ('kitchen', _('Kitchen')),
    ]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)
    # 2026-08-09 live request (Roy): a one-off expense recorded from a
    # specific counter's own reconciliation area (e.g. Kitchen Board) so it
    # shows up in that counter's own picture — blank for whole-business
    # expenses (rent, recurring rules, DJ/MC payouts) which predate this
    # field and were never meant to be attributed to one station.
    # Deliberately NEVER read by till_expected_cash() (the continuous "right
    # now" dashboard tile) or _reconcile() itself (the live in-progress shift
    # panel / moment-of-close comparison) — an ad-hoc expense must never move
    # today's LIVE expected drawer, and never a day it wasn't dated for.
    # Same-day follow-up (2026-08-09, Roy's explicit confirmation): Shift
    # History and the Z-report DO fold a same-day entry in, additively, at
    # DISPLAY time only — see shift_views._ad_hoc_expense_total_for_shift()'s
    # docstring for the exact mechanism and why it's a separate helper rather
    # than a change to _reconcile() itself.
    station = models.CharField(max_length=10, choices=STATION_CHOICES, blank=True)
    recorded_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='expenses_recorded',
    )
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
        # 2026-08-11 live request (Roy): every OTHER reason above already
        # auto-mirrors into an Expense Intelligence BusinessExpense on
        # approval (see review_petty_cash()'s linked_expense block, built
        # 2026-07-26) — Roy didn't realize this, and was manually double-
        # entering ingredient/utility purchases into both Counter Cash and
        # Matumizi. The gap he actually surfaced runs the OTHER direction:
        # cash handed to a PERSON (police, chama, a personal loan) is a
        # real till outflow but NOT a business operating expense, and
        # nothing distinguished it from an ordinary purchase — every
        # approved entry got mirrored regardless. This reason is the one
        # explicit exception review_petty_cash() checks for: it still
        # correctly reduces till_expected_cash() (money really left the
        # drawer) but is deliberately excluded from the auto-mirror.
        ('cash_disbursement', _('Fedha kwa Mtu (polisi, chama, n.k.) — SI gharama ya biashara')),
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

    # ── 2026-07-26 (item 1) — staff explanation + expense linkage ───────────────
    # staff_note: the RECORDING staffer's own explanation — editable while still
    # pending, and (separately) appendable even after a rejection so they can
    # respond to the owner's review_note without needing a new entry.
    staff_note    = models.TextField(blank=True)
    staff_note_at = models.DateTimeField(null=True, blank=True)
    # linked_expense: created ONLY when this entry is approved (mirrors it into
    # Expense Intelligence as a real cost), deleted if later reversed back to
    # rejected — see review_petty_cash() for the sync logic. Never a source of
    # truth on its own; PettyCash.status is always authoritative.
    linked_expense = models.ForeignKey(
        'BusinessExpense', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='petty_cash_entries',
    )

    # ── 2026-07-27 — station attribution ─────────────────────────────────────
    # Which till this cash physically came out of. Blank/unset on older rows
    # (pre-migration) and on business-wide withdrawals with no clear counter
    # (e.g. an owner paying rent from the safe, not either till) — those are
    # deliberately excluded from BOTH stations' till math (shift_views.
    # till_expected_cash) rather than guessed into one, since attributing them
    # wrongly would make that till's expected figure wrong, not just vague.
    STATION_CHOICES = [
        ('bar',     _('Bar')),
        ('kitchen', _('Kitchen')),
    ]
    station = models.CharField(max_length=10, choices=STATION_CHOICES, blank=True)

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
    debt_customer = models.ForeignKey(
        'Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='stk_payments',
        help_text='Customer FK for staff-initiated debt STK Push from the debt tracker page.',
    )
    kitchen_cart = models.JSONField(
        null=True, blank=True,
        help_text='Serialised cart for kitchen STK push server-side settlement.',
    )
    kitchen_settled = models.BooleanField(
        default=False,
        help_text='True once kitchen_cart has been processed (by callback or JS poll).',
    )
    tab_entry_ids = models.JSONField(
        null=True, blank=True,
        help_text='List of BarTabEntry IDs for partial tab STK settlement. Null = FIFO full-tab.',
    )
    receipt_token = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text='Receipt token for customer-initiated STK push from public receipt page.',
    )
    qs_cart = models.JSONField(
        null=True, blank=True,
        help_text='Serialised Quick Sell cart for checkout STK push server-side settlement.',
    )
    qs_settled = models.BooleanField(
        default=False,
        help_text='True once qs_cart has been processed (by callback or JS poll).',
    )
    debt_settled = models.BooleanField(
        default=False,
        help_text=(
            'True once this payment\'s debt/receipt settlement (entry-selection mode, '
            'debt-block mode, or staff-initiated debt STK) has been processed (by '
            'callback or JS poll).'
        ),
    )
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
    awarded_bid = models.ForeignKey(
        'SupplierBid', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_orders',
        help_text='Set when this PO was auto-created from a procurement award — '
                   'the only prior link was a free-text note in `notes`.',
    )
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
    PAYMENT_TYPE_CHOICES = [
        ('full',    _('Full Payment')),
        ('partial', _('Partial Payment')),
        ('advance', _('Salary Advance')),
    ]
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_type = models.CharField(
        max_length=10, choices=PAYMENT_TYPE_CHOICES, default='full',
        help_text="'full' = complete salary; 'partial' = instalment toward the period's salary.",
    )
    due_date = models.DateField()
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='cash', blank=True)
    notes = models.CharField(max_length=255, blank=True)
    staff_note = models.CharField(
        max_length=500, blank=True,
        help_text='Optional note shown to the staff member on their Kazi Yangu page.',
    )
    recorded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='salary_payments_recorded',
    )
    # 2026-07-26 (item 8) — staff-side acknowledgement, closing the loop the
    # owner's record_salary_payment already started (SMS notice) but never
    # confirmed was actually received. A staffer disputing a payment now has
    # somewhere concrete to say so, rather than a silent "did they get it?".
    confirmed_by_staff = models.BooleanField(default=False)
    confirmed_at       = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period', '-paid_at', 'staff']
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


class SalaryAdvanceRequest(models.Model):
    """Staff-initiated request for an emergency salary advance ahead of the
    normal pay cycle (2026-07-26 live request) — with a reason, an owner
    approve/reject decision, and a direct link to the SalaryPayment created
    when the owner actually disburses it (payment_type='advance'), so it
    counts against that period's remaining balance the same as any other
    payment — "follow-up... upon partial payments or remaining balance" is
    answered by that shared remaining-balance calculation, not a separate
    tracking mechanism.
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        ('pending',  _('Inasubiri')),
        ('approved', _('Imeidhinishwa')),
        ('rejected', _('Imekataliwa')),
    ]

    business         = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='salary_advance_requests')
    staff            = models.ForeignKey('accounts.UserProfile', on_delete=models.CASCADE, related_name='salary_advance_requests')
    amount_requested = models.DecimalField(max_digits=12, decimal_places=2)
    reason           = models.TextField()
    period           = models.CharField(max_length=7, help_text="Period this advance counts against, YYYY-MM.")
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    requested_at     = models.DateTimeField(auto_now_add=True)
    reviewed_by      = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='salary_advances_reviewed')
    reviewed_at      = models.DateTimeField(null=True, blank=True)
    review_note      = models.CharField(max_length=300, blank=True)
    # Set at approval time — the actual disbursement record. Nullable/blank
    # for the (rare) case an advance is approved in principle before the
    # owner has physically paid it out.
    salary_payment   = models.ForeignKey(
        'SalaryPayment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='advance_request',
    )

    class Meta:
        ordering = ['-requested_at']
        verbose_name = _('Salary Advance Request')
        verbose_name_plural = _('Salary Advance Requests')

    def __str__(self):
        return f"{self.staff} — KES {self.amount_requested:,.0f} ({self.period}) [{self.status}]"


# ────────────────────────────────────────────────
# WRITE-OFF APPROVAL WORKFLOW  (Sprint WO1)
# ────────────────────────────────────────────────

class WriteOffRequest(models.Model):
    """Approval workflow for voiding a credit transaction (debt write-off).

    Staff request → owner/manager notified → owner makes final call.
    Manager verdict is advisory; owner decision (approve/reject) is FINAL.
    Rejection creates a SalaryDeduction against the requesting staff member.
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES  = [
        ('pending',  _('Inasubiri Idhini')),
        ('approved', _('Imeidhinishwa')),
        ('rejected', _('Imekataliwa')),
    ]

    # 2026-07-31 — a genuinely mistaken debt-section entry (wrong item/
    # customer, item never actually given out) is a DIFFERENT situation from
    # a real, uncollectable debt: it must restore stock on approval, must
    # never flag the customer as a defaulter, and (per Roy's explicit call)
    # is approvable by a manager granted UserProfile.can_approve_debt_erase,
    # not owner-only like a real write-off. Reuses this same model/request/
    # approve/reject lifecycle rather than a parallel one — the only
    # behavioral differences are branched on this field in approve_write_off.
    TYPE_WRITEOFF      = 'writeoff'
    TYPE_ERASE_MISTAKE = 'erase_mistake'
    TYPE_CHOICES = [
        ('writeoff',      _('Write-off — Deni Halisi')),
        ('erase_mistake', _('Ilikuwa Kosa — Bidhaa Haikutolewa')),
    ]
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_WRITEOFF)

    transaction = models.OneToOneField(
        'Transaction',
        on_delete=models.CASCADE,
        related_name='write_off_request',
    )
    requested_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='write_off_requests',
    )
    reason = models.CharField(max_length=500)
    # Cache the customer name so we can restore recipient if owner reverses a void
    customer_name_cache = models.CharField(max_length=100, blank=True)

    # Manager recommendation — sets manager_verdict but does NOT execute void
    manager_verdict = models.CharField(max_length=20, blank=True)  # 'approved'|'rejected'|''
    manager_by      = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='write_off_manager_reviews',
    )
    manager_at = models.DateTimeField(null=True, blank=True)

    # Owner decision — FINAL: executes void (approved) or triggers Haki deduction (rejected)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='write_off_reviews',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    haki_deduction_created = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Write-off Request'
        verbose_name_plural = 'Write-off Requests'

    def __str__(self):
        item = self.transaction.item.description if self.transaction_id and self.transaction.item_id else '?'
        who  = self.requested_by.get_full_name() or self.requested_by.username if self.requested_by else '?'
        return f"Write-off: {item} [{self.status}] by {who}"

    @property
    def effective_status_display(self):
        if self.status != WriteOffRequest.STATUS_PENDING:
            return self.get_status_display()
        if self.manager_verdict == 'approved':
            return 'Meneja: Aidhinishwa (Inasubiri Mmiliki)'
        if self.manager_verdict == 'rejected':
            return 'Meneja: Amekataa (Inasubiri Mmiliki)'
        return 'Inasubiri Idhini'


# ────────────────────────────────────────────────
# UBA §7.3 (Sprint R2) — Returns/refunds, the genuinely new primitive retail forces
# ────────────────────────────────────────────────

class Return(models.Model):
    """A customer return against ONE original sale Transaction — reverses
    stock, revenue, revenue-target contribution and (if the original sale
    was credit) the debt ledger, all via the SAME existing aggregate
    machinery those surfaces already use, not a parallel mechanism:

    - Stock reversal is a plain `type='Receipt'` Transaction (qty=+returned)
      created directly via the ORM (bypassing add_transaction()'s VIEW-layer
      cost-price-update logic entirely, since that only runs inside the view
      — so a return can never perturb Item.cost_price, preserving "exactly
      ONE designed writer").
    - Revenue reversal is a `type='Issue'` Transaction with qty=0 (no
      additional stock effect — the Receipt leg above already handled that)
      and a NEGATIVE `sale_amount`, inheriting the original sale's
      `payment_method`/`recipient`. Because `Transaction.revenue()` already
      returns `sale_amount` verbatim (never abs()'d) for any `type='Issue'`
      row, this negative contribution flows automatically through EVERY
      existing `type='Issue'`-filtered revenue aggregate in the app —
      analytics, `_reconcile()`'s cash/mpesa/credit sums, revenue targets,
      daily_sales, home dashboard tiles, AND the debt tracker's
      `_get_customer_debt_data()` (same `payment_method='credit'` +
      `recipient` match) — with ZERO changes needed to any of those
      functions. Known, honestly-scoped limitation: `qty=0` means
      `Transaction.cost()` returns 0 for this leg too, so COGS is NOT
      reversed — a return currently leaves net_profit slightly overstated
      by the original sale's recognized cost. R-AC-RET (spec §7.3) does not
      require cost/margin reversal, only stock/revenue/revenue-target/debt,
      so this is deferred rather than solved here — flagged for a future
      pass if margin-accuracy-after-returns becomes a real complaint.
    """
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Inasubiri Idhini'),
        (STATUS_APPROVED, 'Imeidhinishwa'),
        (STATUS_REJECTED, 'Imekataliwa'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='returns')
    original_transaction = models.ForeignKey(
        'Transaction', on_delete=models.CASCADE, related_name='return_requests'
    )
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    qty_returned = models.DecimalField(max_digits=12, decimal_places=3)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_APPROVED)
    processed_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='returns_processed',
    )
    approved_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='returns_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    stock_txn = models.ForeignKey(
        'Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    revenue_txn = models.ForeignKey(
        'Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Return of {self.qty_returned} {self.item.description} (KES {self.refund_amount})"

    @classmethod
    def _already_returned_qty(cls, original_transaction):
        agg = cls.objects.filter(
            original_transaction=original_transaction, status=cls.STATUS_APPROVED,
        ).aggregate(total=models.Sum('qty_returned'))
        return agg['total'] or Decimal('0')

    @classmethod
    def process_locked(cls, original_transaction_id, business, qty_returned, reason,
                        processed_by=None, force_approve=False):
        """The one entry point. Raises ValueError for an invalid request
        (wrong business, not a plain Issue sale, qty exceeds what's left to
        return). Returns a Return instance — status='pending' (no
        transactions created yet) if `business.return_approval_threshold`
        is set and the computed refund exceeds it and `force_approve` is
        False; otherwise status='approved' with both reversal transactions
        created and linked. `force_approve=True` is the owner/manager
        approval action's own call (see approve_return()), never a
        self-service bypass — the view layer enforces that permission
        check, matching this app's own "model enforces state, view enforces
        who" convention."""
        from django.db import transaction as _tx
        with _tx.atomic():
            orig = Transaction.objects.select_for_update().get(
                id=original_transaction_id, business=business, type='Issue',
            )
            qty_returned = Decimal(str(qty_returned))
            if qty_returned <= 0:
                raise ValueError('Kiasi cha kurudisha lazima kiwe zaidi ya sifuri.')
            already = cls._already_returned_qty(orig)
            if qty_returned > (abs(orig.qty) - already):
                raise ValueError('Kiasi kinachorudishwa ni zaidi ya kilichouzwa.')

            original_qty_sold = abs(orig.qty) or Decimal('1')
            refund_amount = (Decimal(str(orig.revenue())) * qty_returned / original_qty_sold)
            refund_amount = refund_amount.quantize(Decimal('0.01'))

            threshold = business.return_approval_threshold
            if threshold and refund_amount > threshold and not force_approve:
                return cls.objects.create(
                    business=business, original_transaction=orig, item=orig.item,
                    qty_returned=qty_returned, refund_amount=refund_amount, reason=reason,
                    status=cls.STATUS_PENDING, processed_by=processed_by,
                )

            stock_txn = Transaction.objects.create(
                business=business, item=orig.item, type='Receipt', qty=qty_returned,
                recipient=reason, invoice_no='[RETURN]', payment_method='',
                recorded_by=getattr(processed_by, 'user', None),
            )
            revenue_txn = Transaction.objects.create(
                business=business, item=orig.item, type='Issue', qty=Decimal('0'),
                sale_amount=-refund_amount, payment_method=orig.payment_method,
                recipient=orig.recipient, invoice_no='[RETURN]',
                recorded_by=getattr(processed_by, 'user', None),
            )
            return cls.objects.create(
                business=business, original_transaction=orig, item=orig.item,
                qty_returned=qty_returned, refund_amount=refund_amount, reason=reason,
                status=cls.STATUS_APPROVED, processed_by=processed_by,
                approved_by=processed_by if force_approve else None,
                approved_at=timezone.now() if force_approve else None,
                stock_txn=stock_txn, revenue_txn=revenue_txn,
            )

    def approve(self, approved_by):
        """Owner/manager approves a pending (above-threshold) return —
        the view layer is responsible for the permission check. Creates the
        same pair of reversal transactions process_locked() would have
        created immediately, had the threshold not applied."""
        if self.status != self.STATUS_PENDING:
            raise ValueError('Ombi hili tayari limeshughulikiwa.')
        from django.db import transaction as _tx
        with _tx.atomic():
            ret = Return.objects.select_for_update().get(pk=self.pk)
            if ret.status != self.STATUS_PENDING:
                raise ValueError('Ombi hili tayari limeshughulikiwa.')
            orig = ret.original_transaction
            stock_txn = Transaction.objects.create(
                business=ret.business, item=ret.item, type='Receipt', qty=ret.qty_returned,
                recipient=ret.reason, invoice_no='[RETURN]', payment_method='',
                recorded_by=getattr(approved_by, 'user', None),
            )
            revenue_txn = Transaction.objects.create(
                business=ret.business, item=ret.item, type='Issue', qty=Decimal('0'),
                sale_amount=-ret.refund_amount, payment_method=orig.payment_method,
                recipient=orig.recipient, invoice_no='[RETURN]',
                recorded_by=getattr(approved_by, 'user', None),
            )
            ret.status = self.STATUS_APPROVED
            ret.approved_by = approved_by
            ret.approved_at = timezone.now()
            ret.stock_txn = stock_txn
            ret.revenue_txn = revenue_txn
            ret.save(update_fields=['status', 'approved_by', 'approved_at', 'stock_txn', 'revenue_txn'])
            self.status = ret.status
            self.stock_txn = ret.stock_txn
            self.revenue_txn = ret.revenue_txn
            return ret

    def reject(self, rejected_by):
        if self.status != self.STATUS_PENDING:
            raise ValueError('Ombi hili tayari limeshughulikiwa.')
        self.status = self.STATUS_REJECTED
        self.approved_by = rejected_by
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'approved_by', 'approved_at'])


class ItemPriceHistory(models.Model):
    """UBA §7.3 (Sprint R2) — the margin guard's "Sasisha bei" one-tap price
    update writes here. NOT a general price-change log for every possible
    edit_item save — only for the margin-guard-suggested update, so the
    reason is always known and this stays small and meaningful."""
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='price_history')
    old_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    new_price = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=100, default='margin_guard')
    changed_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.item.description}: {self.old_price} → {self.new_price}"


# ────────────────────────────────────────────────
# UBA §7.4 (Sprint R3) — cycle counting (ABC) + retail shrinkage
# ────────────────────────────────────────────────

class StockCountSession(models.Model):
    KIND_CYCLE = 'CYCLE'
    KIND_FULL = 'FULL'
    KIND_SPOT = 'SPOT'
    KIND_CHOICES = [
        (KIND_CYCLE, 'Hesabu ya kila siku'),
        (KIND_FULL, 'Hesabu kamili'),
        (KIND_SPOT, 'Hesabu ya ghafla'),
    ]
    STATUS_OPEN = 'OPEN'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Wazi'), (STATUS_CLOSED, 'Imefungwa')]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='stock_count_sessions')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, null=True, blank=True)
    kind = models.CharField(max_length=6, choices=KIND_CHOICES, default=KIND_CYCLE)
    scope_note = models.CharField(max_length=100, blank=True, help_text="e.g. 'Class A — 12 items'")
    started_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=6, choices=STATUS_CHOICES, default=STATUS_OPEN)
    shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.get_kind_display()} — {self.started_at:%Y-%m-%d}"


class StockCountLine(models.Model):
    session = models.ForeignKey(StockCountSession, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    book_qty = models.DecimalField(max_digits=12, decimal_places=3, help_text='Snapshot at count time — never recomputed later.')
    counted_qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    variance_qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    variance_kes = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reason = models.CharField(max_length=100, blank=True, help_text="'imeharibika' | 'imeibiwa' | 'kosa la kuandika' | ...")
    attributed_shift = models.ForeignKey(
        'Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text='Which shift is believed accountable for this variance — see '
                  'core.cycle_count.record_count_line(), reuses shift_views.attribute_variance_shift().'
    )
    counted_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    counted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.item.description}: book {self.book_qty}, counted {self.counted_qty}"


# ────────────────────────────────────────────────
# UBA §12.1 (Sprint X1) — Payables: the missing half of the cash picture
# ────────────────────────────────────────────────

class SupplierInvoice(models.Model):
    """What THIS business owes a supplier — the mirror image of the debt
    tracker's Customer/CustomerDebtPayment (receivables). Same aging
    buckets, same UI patterns, opposite direction — see core/payables.py,
    which deliberately reuses the debt tracker's own bucket boundaries
    (current / 30 / 60 / 90+) rather than inventing new ones."""
    STATUS_DUE = 'DUE'
    STATUS_PARTIAL = 'PARTIAL'
    STATUS_PAID = 'PAID'
    STATUS_DISPUTED = 'DISPUTED'
    STATUS_CHOICES = [
        (STATUS_DUE, 'Inadaiwa'), (STATUS_PARTIAL, 'Imelipwa Kiasi'),
        (STATUS_PAID, 'Imelipwa'), (STATUS_DISPUTED, 'Ina Utata'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='supplier_invoices')
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(
        'accounts.Business', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='issued_invoices', help_text='A platform supplier, if this business trades with one via the marketplace.'
    )
    supplier_name = models.CharField(max_length=200, blank=True, help_text='Off-platform distributor name — most cases.')
    invoice_no = models.CharField(max_length=60, blank=True)
    purchase_order = models.ForeignKey(
        'PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='supplier_invoices'
    )
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DUE)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', '-invoice_date']

    def __str__(self):
        return f"{self.supplier_name or self.supplier}: KES {self.amount} ({self.get_status_display()})"

    @property
    def outstanding(self):
        return max(Decimal('0'), self.amount - self.paid_amount)

    def record_payment_locked(self, amount, method, recorded_by, reference='', paid_on=None):
        from django.db import transaction as _tx
        with _tx.atomic():
            inv = SupplierInvoice.objects.select_for_update().get(pk=self.pk)
            SupplierPayment.objects.create(
                invoice=inv, amount=amount, method=method, reference=reference,
                paid_on=paid_on or timezone.localdate(), recorded_by=recorded_by,
            )
            inv.paid_amount = inv.paid_amount + Decimal(str(amount))
            if inv.paid_amount >= inv.amount:
                inv.status = SupplierInvoice.STATUS_PAID
            elif inv.paid_amount > 0:
                inv.status = SupplierInvoice.STATUS_PARTIAL
            inv.save(update_fields=['paid_amount', 'status'])
            self.paid_amount = inv.paid_amount
            self.status = inv.status
            return inv


class SupplierPayment(models.Model):
    METHOD_CHOICES = [('cash', 'Cash'), ('mpesa', 'M-Pesa'), ('bank', 'Bank'), ('other', 'Nyingine')]
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=100, blank=True)
    paid_on = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on']

    def __str__(self):
        return f"KES {self.amount} on {self.invoice}"


# ────────────────────────────────────────────────
# UBA §6.2 (Sprint P0-B) — payment plans (layaway / deposit / instalments)
# ────────────────────────────────────────────────

class PaymentPlan(models.Model):
    """A deposit/layaway/instalment/booking plan — deliberately NOT the debt
    tracker. A plan is money the business is HOLDING against a future
    completion, not credit extended to a customer; `PaymentPlanEntry`
    payments never create a revenue-bearing Transaction (deposits are a
    LIABILITY, not revenue — see `pay_locked()`'s docstring), and the debt
    ledger machinery is never touched by any method here, so a plan
    correctly never appears in the deni ledger by construction — no
    exclusion filter needed anywhere else in the app.

    Known, documented limitation (not solved this pass): a cash/M-Pesa
    deposit payment is not yet reflected in `shift_views.till_expected_cash()`
    / `_reconcile()` — that machinery is this app's single most
    money-sensitive function (its own module docstring calls it exactly
    that), and integrating a second cash-affecting event into it needs its
    own careful, dedicated pass rather than being folded into this one.
    """
    KIND_LAYAWAY = 'LAYAWAY'
    KIND_DEPOSIT = 'DEPOSIT'
    KIND_INSTALMENT = 'INSTALMENT'
    KIND_BOOKING = 'BOOKING'
    KIND_CHOICES = [
        (KIND_LAYAWAY, 'Lipa pole pole'), (KIND_DEPOSIT, 'Amana'),
        (KIND_INSTALMENT, 'Malipo ya awamu'), (KIND_BOOKING, 'Booking'),
    ]
    STATUS_OPEN = 'OPEN'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FORFEITED = 'FORFEITED'
    STATUS_REFUNDED = 'REFUNDED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Wazi'), (STATUS_COMPLETED, 'Imekamilika'),
        (STATUS_FORFEITED, 'Amana Imepotea'), (STATUS_REFUNDED, 'Imerejeshwa'),
        (STATUS_CANCELLED, 'Imefutwa'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='payment_plans')
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True)
    customer = models.ForeignKey('Customer', on_delete=models.CASCADE, related_name='payment_plans')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_LAYAWAY)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    due_date = models.DateField(null=True, blank=True)
    hold_expires_on = models.DateField(null=True, blank=True, help_text='Layaway: goods held until this date.')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    forfeit_policy = models.CharField(
        max_length=200, blank=True,
        help_text='Human-readable, snapshotted from Business.layaway_forfeit_policy at creation '
                  'time and shown on the deposit receipt — never retro-applied if the business '
                  'setting changes later.'
    )
    reserved_item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name='payment_plan_reservations')
    reserved_qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    created_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_kind_display()} — {self.customer.name} (KES {self.paid_amount}/{self.total_amount})"

    @property
    def balance(self):
        return max(Decimal('0'), self.total_amount - self.paid_amount)

    @staticmethod
    def describe_forfeit_policy(business):
        """The exact Swahili sentence printed on the deposit receipt at the
        moment money is taken — never discovered later (§6.2's ethics note)."""
        if business.layaway_forfeit_policy == 'full_refund':
            return 'Ukikosa kumaliza malipo, amana yako yote itarejeshwa.'
        if business.layaway_forfeit_policy == 'full_forfeit':
            return 'Ukikosa kumaliza malipo, amana yako haitarejeshwa kabisa.'
        return (
            f'Ukikosa kumaliza malipo, amana yako itarejeshwa ukitoa ada ya '
            f'asilimia {business.layaway_forfeit_pct:g}%.'
        )

    @classmethod
    def create_locked(cls, business, customer, kind, total_amount, created_by,
                       store=None, due_date=None, hold_expires_on=None,
                       reserved_item=None, reserved_qty=None, initial_payment=None,
                       initial_method='cash', initial_reference=''):
        """The one entry point — snapshots the forfeit policy text NOW so a
        later change to Business.layaway_forfeit_policy can never silently
        alter a plan already sold to a customer."""
        plan = cls.objects.create(
            business=business, customer=customer, kind=kind, total_amount=total_amount,
            store=store, due_date=due_date, hold_expires_on=hold_expires_on,
            reserved_item=reserved_item, reserved_qty=reserved_qty,
            forfeit_policy=cls.describe_forfeit_policy(business), created_by=created_by,
        )
        if initial_payment:
            plan.pay_locked(initial_payment, initial_method, created_by, reference=initial_reference)
        return plan

    def pay_locked(self, amount, method, recorded_by, reference=''):
        """Records a PaymentPlanEntry and updates paid_amount. Deliberately
        creates NO Transaction — a deposit is a liability, not revenue,
        until the plan actually completes (convert_to_sale_locked())."""
        from django.db import transaction as _tx
        with _tx.atomic():
            plan = PaymentPlan.objects.select_for_update().get(pk=self.pk)
            if plan.status != self.STATUS_OPEN:
                raise ValueError('Mpango huu si wazi tena.')
            entry = PaymentPlanEntry.objects.create(
                plan=plan, amount=amount, method=method, mpesa_ref=reference, recorded_by=recorded_by,
            )
            plan.paid_amount = plan.paid_amount + Decimal(str(amount))
            plan.save(update_fields=['paid_amount'])
            self.paid_amount = plan.paid_amount
            return entry

    def convert_to_sale_locked(self, converted_by):
        """Plan → real sale on completion (spec's own "inverse action":
        plan ↔ convert to sale). Creates the actual Issue transaction(s) for
        the reserved item NOW — this is the ONE moment a plan's money
        becomes real recognised revenue, not at any individual deposit/
        instalment payment."""
        from django.db import transaction as _tx
        with _tx.atomic():
            plan = PaymentPlan.objects.select_for_update().get(pk=self.pk)
            if plan.status != self.STATUS_OPEN:
                raise ValueError('Mpango huu si wazi tena.')
            if plan.balance > 0:
                raise ValueError('Malipo bado hayajakamilika.')
            txn = None
            if plan.reserved_item_id and plan.reserved_qty:
                txn = Transaction.objects.create(
                    business=plan.business, item=plan.reserved_item, type='Issue',
                    qty=-abs(plan.reserved_qty), sale_amount=plan.total_amount,
                    payment_method='cash', recorded_by=getattr(converted_by, 'user', None),
                )
            plan.status = self.STATUS_COMPLETED
            plan.closed_at = timezone.now()
            plan.save(update_fields=['status', 'closed_at'])
            self.status = plan.status
            self.closed_at = plan.closed_at
            return txn

    def refund_locked(self, refunded_by, amount=None):
        """Refunds some or all of paid_amount (default: everything paid so
        far) and releases any stock reservation. No Transaction is created —
        no sale ever happened, so there is nothing for a Return to reverse."""
        from django.db import transaction as _tx
        with _tx.atomic():
            plan = PaymentPlan.objects.select_for_update().get(pk=self.pk)
            if plan.status != self.STATUS_OPEN:
                raise ValueError('Mpango huu si wazi tena.')
            plan.status = self.STATUS_REFUNDED
            plan.closed_at = timezone.now()
            plan.save(update_fields=['status', 'closed_at'])
            self.status = plan.status
            self.closed_at = plan.closed_at
            return plan

    def release(self, released_by):
        """Cancel a hold with no money movement question (e.g. customer
        never paid a deposit at all yet) — the reserved item becomes
        available again the instant status leaves OPEN, since
        Item.reserved_qty() only ever sums OPEN plans."""
        if self.status != self.STATUS_OPEN:
            raise ValueError('Mpango huu si wazi tena.')
        self.status = self.STATUS_CANCELLED
        self.closed_at = timezone.now()
        self.save(update_fields=['status', 'closed_at'])

    def forfeit_locked(self, forfeited_by):
        """Applies Business.layaway_forfeit_policy — never retro-applied,
        always the policy snapshotted onto forfeit_policy at creation time.
        full_forfeit/minus_percent both close the plan as FORFEITED (the
        business keeps some or all of paid_amount); full_refund closes it
        as REFUNDED instead, since nothing was actually kept."""
        from django.db import transaction as _tx
        with _tx.atomic():
            plan = PaymentPlan.objects.select_for_update().get(pk=self.pk)
            if plan.status != self.STATUS_OPEN:
                raise ValueError('Mpango huu si wazi tena.')
            policy = plan.business.layaway_forfeit_policy
            if policy == 'full_refund':
                plan.status = self.STATUS_REFUNDED
            else:
                plan.status = self.STATUS_FORFEITED
            plan.closed_at = timezone.now()
            plan.save(update_fields=['status', 'closed_at'])
            self.status = plan.status
            self.closed_at = plan.closed_at
            return plan


class PaymentPlanEntry(models.Model):
    METHOD_CHOICES = [('cash', 'Cash'), ('mpesa', 'M-Pesa'), ('other', 'Nyingine')]
    plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE, related_name='entries')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='cash')
    mpesa_ref = models.CharField(max_length=40, blank=True)
    recorded_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    receipt = models.ForeignKey('Receipt', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"KES {self.amount} ({self.method}) on {self.plan}"


# ────────────────────────────────────────────────
# UBA §8.4 (Sprint A3) — fitting-room log (optional, high-shrinkage boutiques)
# ────────────────────────────────────────────────

class FittingRoomLog(models.Model):
    """Lightweight: a counter per staff per shift. Pieces out → pieces back.
    Variance feeds StaffShrinkage via core.accountability's registry (a
    second real caller for the pattern M0-7's VarianceResult contract was
    built for)."""
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='fitting_room_logs')
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True)
    staff = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True)
    pieces_out = models.PositiveIntegerField(default=0)
    pieces_back = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def variance(self):
        return self.pieces_out - self.pieces_back

    def __str__(self):
        return f"{self.pieces_out} out / {self.pieces_back} back (variance {self.variance})"


# ────────────────────────────────────────────────
# UBA §9.2 (Sprint S1) — Salon: services, recipes, the side-client detector
# ────────────────────────────────────────────────

class Service(models.Model):
    """A sold SERVICE, not a physical good. `Transaction.item` is
    non-nullable throughout this codebase (VERIFY-ME confirmed: every
    `item = models.ForeignKey(Item, ...)` in this file has no `null=True`) —
    per the spec's own explicit recommendation, this uses the lower-risk
    shadow-Item approach (`shadow_item`, `stock_model='SERVICE'`, balance
    never checked/meaningful) rather than making `Transaction.item`
    nullable, which the spec calls out as needing a full app-wide audit of
    every existing `item=`-assuming reader. See `core.salon.
    get_or_create_shadow_item()` and `complete_service_locked()`."""
    COMMISSION_CHOICES = [('NONE', 'Hakuna'), ('PERCENT', 'Asilimia'), ('FIXED', 'Kiasi Maalum')]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='services')
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=60, blank=True, help_text="'Nywele'|'Kucha'|'Ngozi'.")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = models.PositiveIntegerField(default=30)
    buffer_minutes = models.PositiveIntegerField(default=0)
    commission_type = models.CharField(max_length=10, choices=COMMISSION_CHOICES, default='NONE')
    commission_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    requires_booking = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    rebook_after_days = models.PositiveIntegerField(
        null=True, blank=True, help_text='UBA §9.3 rebooking nudge — e.g. 42 for a 6-week retouch cycle.'
    )
    display_order = models.IntegerField(default=0)
    shadow_item = models.OneToOneField(
        Item, on_delete=models.SET_NULL, null=True, blank=True, related_name='service_for',
        help_text='Auto-created — see core.salon.get_or_create_shadow_item(). Never sold via '
                  'the normal item-balance path; its own balance is meaningless/untracked.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name

    def commission_amount(self, sale_amount):
        if self.commission_type == 'PERCENT':
            return (Decimal(str(sale_amount)) * self.commission_value / Decimal('100')).quantize(Decimal('0.01'))
        if self.commission_type == 'FIXED':
            return self.commission_value
        return Decimal('0')


class ServiceSupplyLine(models.Model):
    """The recipe. THIS is what makes the accountability engine work for
    salons — see core.salon.expected_consumption()."""
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='supplies')
    item = models.ForeignKey(Item, on_delete=models.CASCADE, help_text='A MEASURE or UNIT stock item.')
    qty_expected = models.DecimalField(max_digits=10, decimal_places=3, help_text='e.g. 60 (ml of relaxer).')
    tolerance_pct = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('25.0'),
        help_text='Hair varies — be generous or you cry wolf.'
    )

    def __str__(self):
        return f"{self.service.name}: {self.qty_expected} {self.item.unit} of {self.item.description}"


# ────────────────────────────────────────────────
# UBA §9.3 (Sprint S2) — bookings, walk-ins, chair queue
# ────────────────────────────────────────────────

class Appointment(models.Model):
    STATUS_BOOKED = 'BOOKED'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_ARRIVED = 'ARRIVED'
    STATUS_IN_SERVICE = 'IN_SERVICE'
    STATUS_DONE = 'DONE'
    STATUS_NO_SHOW = 'NO_SHOW'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_BOOKED, 'Imewekwa'), (STATUS_CONFIRMED, 'Imethibitishwa'),
        (STATUS_ARRIVED, 'Amefika'), (STATUS_IN_SERVICE, 'Inaendelea'),
        (STATUS_DONE, 'Imekamilika'), (STATUS_NO_SHOW, 'Hakuja'),
        (STATUS_CANCELLED, 'Imefutwa'),
    ]
    SOURCE_CHOICES = [
        ('walkin', 'Walk-in'), ('phone', 'Simu'), ('whatsapp', 'WhatsApp'), ('online', 'Mtandaoni'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='appointments')
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    staff = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='appointments', help_text='Requested stylist.'
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_BOOKED)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='walkin')
    deposit_plan = models.ForeignKey('PaymentPlan', on_delete=models.SET_NULL, null=True, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_at']

    def __str__(self):
        return f"{self.customer_name or (self.customer.name if self.customer else '?')} — {self.start_at:%d %b %H:%M}"


class AppointmentService(models.Model):
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='services')
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    price_at_booking = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.service.name} @ KES {self.price_at_booking}"


# ────────────────────────────────────────────────
# UBA §10 (Sprints L1-L2) — Rentals: property + equipment, one primitive
# ────────────────────────────────────────────────
# Pre-answered decision #2: property first; equipment mode in the SAME
# sprint since it costs no extra models — RentalUnit.kind is the only
# thing that differs, exactly as the spec itself frames it ("one
# primitive, two markets").

class RentalUnit(models.Model):
    KIND_CHOICES = [('property', 'Nyumba/Chumba'), ('equipment', 'Kifaa')]
    STATUS_CHOICES = [
        ('AVAILABLE', 'Iko wazi'), ('RESERVED', 'Imewekwa'), ('OCCUPIED', 'Ina mtu'),
        ('OUT', 'Imetoka'), ('MAINTENANCE', 'Inatengenezwa'), ('RETIRED', 'Imeondolewa'),
    ]
    RATE_PERIOD_CHOICES = [('day', 'Siku'), ('week', 'Wiki'), ('month', 'Mwezi')]

    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='rental_units')
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True, help_text='Property/portfolio.')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='property')
    code = models.CharField(max_length=30, help_text="'A12' — doubles as the M-Pesa Paybill account number.")
    name = models.CharField(max_length=120, blank=True, help_text="'Bedsitter A12'.")
    description = models.TextField(blank=True)
    default_rate = models.DecimalField(max_digits=12, decimal_places=2, help_text='Per month (property) or per day (equipment).')
    rate_period = models.CharField(max_length=6, choices=RATE_PERIOD_CHOICES, default='month')
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    quantity = models.PositiveIntegerField(default=1, help_text='Equipment: 300 chairs = one unit, qty 300.')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='AVAILABLE')
    is_metered = models.BooleanField(default=False)
    is_published = models.BooleanField(default=False, help_text='Storefront listing (Phase 6).')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('business', 'code')]
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name or self.get_kind_display()}"

    def committed_qty(self, on_date=None):
        """UBA L3-AC1 — equipment: how many of this unit's total quantity
        are already committed to an ACTIVE agreement covering `on_date`
        (default: today). Property units are always quantity=1, so this is
        naturally 0 or 1 for them."""
        from django.utils import timezone as _tz
        on_date = on_date or _tz.localdate()
        agreements = self.agreements.filter(
            status=RentalAgreement.STATUS_ACTIVE, start_date__lte=on_date,
        ).filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=on_date))
        return sum(a.quantity for a in agreements)

    def available_qty(self, on_date=None):
        return max(0, self.quantity - self.committed_qty(on_date))


class RentalAgreement(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_ENDED = 'ENDED'
    STATUS_TERMINATED = 'TERMINATED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Rasimu'), (STATUS_ACTIVE, 'Inaendelea'),
        (STATUS_ENDED, 'Imeisha'), (STATUS_TERMINATED, 'Imesitishwa'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='rental_agreements')
    unit = models.ForeignKey(RentalUnit, on_delete=models.CASCADE, related_name='agreements')
    customer = models.ForeignKey('Customer', on_delete=models.CASCADE, related_name='rental_agreements', help_text='Tenant/hirer — reuses debt + credit score.')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    rate_period = models.CharField(max_length=6, choices=RentalUnit.RATE_PERIOD_CHOICES, default='month')
    quantity = models.PositiveIntegerField(default=1, help_text='e.g. 200 of the 300 chairs.')
    deposit_held = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    deposit_plan = models.ForeignKey('PaymentPlan', on_delete=models.SET_NULL, null=True, blank=True)
    billing_day = models.PositiveSmallIntegerField(default=5)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    terms_note = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.unit.code} — {self.customer.name} ({self.get_status_display()})"

    def terminate(self, ended_by=None, reason=''):
        self.status = self.STATUS_TERMINATED
        self.end_date = timezone.localdate()
        if reason:
            self.terms_note = (self.terms_note + f'\nImesitishwa: {reason}').strip()
        self.save(update_fields=['status', 'end_date', 'terms_note'])


class RentalInvoice(models.Model):
    STATUS_DUE = 'DUE'
    STATUS_PARTIAL = 'PARTIAL'
    STATUS_PAID = 'PAID'
    STATUS_WAIVED = 'WAIVED'
    STATUS_CHOICES = [
        (STATUS_DUE, 'Inadaiwa'), (STATUS_PARTIAL, 'Imelipwa Kiasi'),
        (STATUS_PAID, 'Imelipwa'), (STATUS_WAIVED, 'Imesamehewa'),
    ]
    agreement = models.ForeignKey(RentalAgreement, on_delete=models.CASCADE, related_name='invoices')
    period_start = models.DateField()
    period_end = models.DateField()
    rent_amount = models.DecimalField(max_digits=12, decimal_places=2)
    utilities_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    other_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    other_note = models.CharField(max_length=200, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DUE)
    issued_at = models.DateTimeField(auto_now_add=True)
    receipt = models.ForeignKey('Receipt', on_delete=models.SET_NULL, null=True, blank=True)
    rent_txn = models.ForeignKey(
        'Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text='UBA §10.3 — the credit Transaction the debt tracker actually reads. '
                  '"Arrears ARE debt" — no parallel aging engine; see core.rentals.py.'
    )

    class Meta:
        unique_together = [('agreement', 'period_start')]
        ordering = ['due_date']

    def __str__(self):
        return f"{self.agreement.unit.code} — {self.period_start} to {self.period_end} (KES {self.total})"


class MeterReading(models.Model):
    KIND_CHOICES = [('water', 'Maji'), ('electricity', 'Umeme')]
    unit = models.ForeignKey(RentalUnit, on_delete=models.CASCADE, related_name='meter_readings')
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    reading = models.DecimalField(max_digits=12, decimal_places=2)
    read_on = models.DateField(default=timezone.localdate)
    read_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    rate_per_unit = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['-read_on']

    def __str__(self):
        return f"{self.unit.code} {self.get_kind_display()}: {self.reading} ({self.read_on})"


class MaintenanceTicket(models.Model):
    """UBA §10.4 — tenant reports, caretaker updates, owner sees cost and
    history per unit; feeds the per-unit P&L (rent − maintenance − vacancy)
    the spec calls "the number no Kenyan landlord currently knows"."""
    STATUS_OPEN = 'OPEN'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Wazi'), (STATUS_IN_PROGRESS, 'Inashughulikiwa'), (STATUS_CLOSED, 'Imefungwa'),
    ]
    unit = models.ForeignKey(RentalUnit, on_delete=models.CASCADE, related_name='maintenance_tickets')
    reported_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    description = models.TextField()
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"{self.unit.code}: {self.description[:40]} ({self.get_status_display()})"

    def close(self, closed_by, cost=None):
        if cost is not None:
            self.cost = cost
        self.status = self.STATUS_CLOSED
        self.closed_at = timezone.now()
        self.closed_by = closed_by
        self.save(update_fields=['cost', 'status', 'closed_at', 'closed_by'])


# ────────────────────────────────────────────────
# SALARY DEDUCTIONS  (Sprint WO1)
# ────────────────────────────────────────────────

class SalaryDeduction(models.Model):
    """Records a deduction from a staff member's salary.

    Currently created when a write-off request is rejected by the owner,
    indicating the staff member's request was fraudulent or erroneous in a
    way that would have cost the business money.
    """
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='salary_deductions',
    )
    staff = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.CASCADE, related_name='salary_deductions',
    )
    period = models.CharField(
        max_length=7,
        help_text="Period in YYYY-MM format. Deduction counts against this period's salary.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=500)
    created_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, related_name='salary_deductions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    write_off = models.ForeignKey(
        WriteOffRequest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='deductions',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Salary Deduction'
        verbose_name_plural = 'Salary Deductions'

    def __str__(self):
        name = self.staff.user.get_full_name() or self.staff.user.username
        return f"Deduction: {name} KES {self.amount:,.2f} [{self.period}]"


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

    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Per-unit cost for THIS specific cut/preset — used when several presets '
                  'under one shared item (e.g. Kuku: Bawa/Paja/Kifua) are bought at genuinely '
                  'different unit costs, so item.cost_price alone cannot represent them (2026-07-25, '
                  'Kitchen Stock Receipt). Written ONLY by KitchenStockReceiptLine at receiving time — '
                  'deliberately never editable from the item form itself (Roy\'s explicit instruction). '
                  'Item.cost_price is left untouched for these presets and stays whatever it was.',
    )

    tracks_stock_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tracked_by',
        help_text=(
            'This preset is a different-sized cut of the SAME physical lot as '
            'the preset pointed to here — e.g. "Half Chicken Leg" tracks_stock_of '
            '"Full Chicken Leg" (a full leg cut and sold as two halves is still '
            'one leg out of what was received). 2026-08-09 live request: without '
            'this, a preset that was never itself received under its own name '
            '(only its "parent" cut was) either shows as permanently out of '
            'stock, or sells invisibly without ever decrementing the parent '
            "cut's own received-vs-sold tally. Blank = counts against itself "
            '(the default, unchanged behavior for every preset that doesn\'t '
            'need this). Set from Edit Item, existing presets only.'
        ),
    )

    restock_anchor_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            'A pure TILE-VISIBILITY cursor — never touches revenue, cost, or '
            'transaction history. When set, the received-vs-sold "Iliyobaki" '
            'tally for this preset (see kitchen_board()) only counts receiving '
            'and sales dated on/after this point, ignoring anything earlier — '
            'even genuinely-preserved old history the owner deliberately kept. '
            '2026-08-12 live request (Roy, Monsoon Inn): the received/sold '
            'tally is otherwise a true LIFETIME sum with no cutoff at all; a '
            'tether (tracks_stock_of) added after old sales already happened '
            'retroactively pulls those old sales into the anchor\'s running '
            'total the moment the tether exists, permanently suppressing a '
            'fresh restock\'s "remaining" even when Roy explicitly does NOT '
            'want that old history deleted (Kitchen Item Reset\'s own destructive '
            'wipe is the wrong tool for this — it would erase real revenue '
            'history he wants to keep). Stamped automatically by Kitchen Item '
            'Reset\'s confirm step on the item\'s own anchor presets (never on a '
            'tethered preset\'s own field — stock_tracking_anchor_id() never '
            'resolves to one). Blank = today\'s exact lifetime-sum behavior, '
            'unchanged, for every preset that has never been through a reset.'
        ),
    )

    class Meta:
        ordering = ['display_order', 'price']
        verbose_name = 'Item Portion Preset'
        verbose_name_plural = 'Item Portion Presets'

    def __str__(self):
        return f"{self.item.description}: {self.label} — KES {self.price}"

    def stock_tracking_anchor_id(self):
        """The preset id whose received-vs-sold tally this preset's sales
        count against — itself unless tracks_stock_of is set. One hop only
        (deliberately not recursive — an owner misconfiguring a longer chain
        is an edge case, not something worth chasing at read time)."""
        return self.tracks_stock_of_id or self.id


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

    # ── UBA §8.3 (Sprint A2) — generalised envelope (keeps this model name,
    # never breaks produce_bunch_id, CLAUDE.md's own flagged discriminator) ──
    KIND_CHOICES = [
        ('produce', 'Mboga/Matunda'), ('bale', 'Bale ya mitumba'),
        ('carcass', 'Nyama'), ('sack', 'Gunia'),
    ]
    kind = models.CharField(
        max_length=16, default='produce', choices=KIND_CHOICES,
        help_text='Same revenue-envelope maths for every kind — only the vocabulary/label '
                  'shown to the owner should differ per business profile, never the maths.'
    )
    grade = models.CharField(max_length=12, blank=True, help_text="Mitumba grading: '1st'|'2nd'|'3rd'.")
    label = models.CharField(max_length=60, blank=True, help_text="e.g. 'Bale ya jeans — Gikomba 28/07'.")

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
    def record_sale(self, amount, payment_method='cash', recipient='', recorded_by=None, created_at=None):
        """
        Deplete this bunch by `amount` shillings. Creates the stock Transaction
        (Issue, real cash on sale_amount) and updates the envelope. Returns the
        Transaction. Selling past target is allowed — the surplus is tracked.

        created_at (2026-08-12 live request, Roy): catch-up/backdated posting
        for a grill-batch sale, mirroring the plain portion-item checkout's
        own backdate support — previously this ALWAYS stamped "now" with no
        override at all, silently ignoring whatever backdate the checkout
        form was set to. None (the default) behaves exactly as before.
        """
        amount = Decimal(str(amount))
        if amount <= 0:
            return None
        when = created_at or timezone.now()
        txn = Transaction.objects.create(
            item=self.item,
            business=self.business or self.item.business,
            type='Issue',
            qty=-self._fraction(amount),
            sale_amount=amount,
            payment_method=payment_method or 'cash',
            recipient=recipient or '',
            produce_bunch=self,
            recorded_by=recorded_by,
            # 2026-08-12 live report (Roy) — Transaction.date defaults to
            # timezone.now() AT CREATION TIME, completely independent of any
            # created_at= override, so a backdated sale with no matching
            # date= silently kept date=today — every `date=`-filtered
            # "today's revenue" query (Kitchen Board's own "Leo" tile, the
            # daily summary email, etc.) then wrongly counted it. Set both
            # together so they can never drift apart.
            **({'created_at': created_at, 'date': timezone.localtime(created_at).date()} if created_at else {}),
        )
        self.revenue_collected = (self.revenue_collected or Decimal('0')) + amount
        if self.opened_on is None:
            self.opened_on = when
        if self.remaining() <= 0 and self.status == 'OPEN':
            self.status = 'DEPLETED'
            self.closed_on = when
        self.save(update_fields=['revenue_collected', 'opened_on', 'status', 'closed_on'])
        return txn

    @classmethod
    def record_sale_locked(cls, bunch_id, business, amount, payment_method='cash',
                            recipient='', recorded_by=None, created_at=None):
        """Thread-safe wrapper around record_sale using SELECT FOR UPDATE — mirrors
        KegBarrel.record_sale_locked. Single lock-safe entry point for all call
        sites (Quick Sell greens/mix, kitchen board grill batches, both STK
        settlement callbacks) so the same envelope-sale race class KegBarrel
        already closed can't reopen at any one of them. Returns None if the
        bunch was depleted/closed between being listed and being locked."""
        from django.db import transaction as _txn
        with _txn.atomic():
            try:
                bunch = cls.objects.select_for_update().get(
                    id=bunch_id, business=business, status='OPEN',
                )
            except cls.DoesNotExist:
                return None
            return bunch.record_sale(
                amount, payment_method, recipient, recorded_by=recorded_by, created_at=created_at,
            )

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
    def sell_mix(cls, business, mix_group, amount, payment_method='cash', recipient='', item_ids=None, recorded_by=None):
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
            # Locked re-fetch at sale time — `b` above was read outside a lock
            # purely to compute the proportional split; the actual envelope
            # mutation must go through record_sale_locked so a concurrent sale
            # against the same bunch (another mix order, a direct order, or an
            # STK settlement) can't clobber this one's revenue_collected update.
            t = cls.record_sale_locked(
                b.id, business, share, payment_method=payment_method,
                recipient=recipient, recorded_by=recorded_by,
            )
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
    confirmed_at  = models.DateTimeField(
        null=True, blank=True,
        help_text='When this shift was actually confirmed (Thibitisha) — used by '
                   'station_revenue_window_start() as the reset point for the live '
                   "dashboard revenue tile, so a station's revenue keeps accruing "
                   'past midnight/closing time until someone actually signs off.',
    )
    notes         = models.TextField(blank=True)
    auto_closed   = models.BooleanField(
        default=False,
        help_text='True when the shift was closed automatically by the business-hours sweep.',
    )

    # ── Cash variance accountability (2026-07-25) ───────────────────────────────
    # A cash shortfall/surplus at close is caused by exactly two things: unlogged
    # petty cash (fixed at the source by folding approved petty cash into
    # _reconcile()'s expected_cash — see shift_views.py) or a real explanation that
    # needs a paper trail (most often: the owner told the staffer by phone/SMS to
    # send the drawer's cash to M-Pesa instead of holding it). variance_note is the
    # staff's own account, captured via reason chips right after they see the
    # number — never required, per this app's "never block" chips contract.
    # variance_review_* is the owner/manager's side of the same conversation.
    VARIANCE_REVIEW_CHOICES = [
        ('acknowledged', 'Acknowledged — explanation accepted'),
        ('flagged',      'Flagged — needs follow-up'),
    ]
    variance_note = models.CharField(
        max_length=300, blank=True,
        help_text="Staff's own explanation for a cash variance at close, captured via reason chips.",
    )
    variance_mpesa_ref = models.CharField(
        max_length=40, blank=True,
        help_text='M-Pesa transaction code, when the explanation was "sent the cash to M-Pesa".',
    )
    variance_reviewed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='shift_variances_reviewed',
    )
    variance_reviewed_at = models.DateTimeField(null=True, blank=True)
    variance_review_note = models.CharField(max_length=300, blank=True)
    variance_review_status = models.CharField(
        max_length=15, blank=True, choices=VARIANCE_REVIEW_CHOICES,
    )

    # ── Continuous till accountability (2026-07-27) ─────────────────────────────
    # Previously "Kiasi kilichoondolewa / kubanked" was accepted at open_shift() and
    # folded into an audit NOTE string only — never stored, so it was invisible to
    # any real reconciliation math. banked_amount is cash physically removed from
    # this station's till between the PREVIOUS shift's close and THIS shift's open
    # (an owner banking excess cash, for example) — see shift_views.
    # till_expected_cash(), which subtracts it when carrying the running balance
    # forward across the gap. Can go NEGATIVE — review_opening_variance() also uses
    # this same field to fold in a later-acknowledged correction (a shortfall
    # increases it, a surplus decreases it below zero), so till_expected_cash()
    # needs no separate mechanism for "explained after the fact" vs "declared at
    # open time" — both are the same fact, just discovered at different times.
    # ── Explicit station (2026-07-28 live report) ───────────────────────────────
    # Which counter this shift was actually opened FROM (captured from the
    # request path at open_shift() time — 100% reliable, unlike inferring it
    # from staff.userprofile.role, which breaks for a manager or any
    # cross-access staffer (can_access_kitchen/can_access_bar) working the
    # OTHER counter than their nominal role. Confirmed live: a Monsoon Inn
    # till showed KES 1400 attributed to Bar with zero bar sales that day —
    # traced to till_expected_cash()'s anchor query picking up a
    # non-kitchen-role staffer's KITCHEN shift close as if it were a bar
    # close, because station was inferred from role alone. Blank on rows
    # created before this field existed — see shift_views._shift_station()
    # and _station_q(), which fall back to role-based inference ONLY for
    # those old, blank rows.
    STATION_CHOICES = [('bar', 'Bar'), ('kitchen', 'Kitchen')]
    station = models.CharField(max_length=10, choices=STATION_CHOICES, blank=True)
    banked_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Net cash removed (or, if negative, added) from this till since the previous shift closed.',
    )
    # What the system computed the till SHOULD have held at the moment this shift
    # opened (till_expected_cash() as of just before creation) — frozen here so the
    # audit trail stays stable even as later activity changes the live figure.
    # opening_variance = opening_float - expected_opening_cash; null until computed.
    expected_opening_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    opening_variance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # ── Opening-variance accountability (2026-07-27, live report) ───────────────
    # Roy's own real scenario: staff opens with float=0 because the till's real
    # cash was already taken by the owner (e.g. deposited to his own M-Pesa)
    # before this shift started, but nobody declared it as banked_amount at open
    # time. The system correctly flags the resulting variance (see open_shift()),
    # but without a way to ACKNOWLEDGE it as legitimate, till_expected_cash()
    # would keep carrying the old, now-explained figure forward into every later
    # calculation — same shape as the close-side variance conversation
    # (variance_note / variance_review_*), mirrored here for the opening side.
    # review_opening_variance() folds an acknowledged amount into banked_amount
    # (reversible on re-review) so the running till reflects the correction
    # immediately, not just once this shift eventually closes.
    opening_variance_note = models.CharField(
        max_length=300, blank=True,
        help_text="Staff's own explanation for an opening-cash variance, captured via reason chips.",
    )
    opening_variance_mpesa_ref = models.CharField(
        max_length=40, blank=True,
        help_text='M-Pesa transaction code, when the explanation was "sent/deposited to M-Pesa".',
    )
    opening_variance_reviewed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='shift_opening_variances_reviewed',
    )
    opening_variance_reviewed_at = models.DateTimeField(null=True, blank=True)
    opening_variance_review_note = models.CharField(max_length=300, blank=True)
    opening_variance_review_status = models.CharField(
        max_length=15, blank=True, choices=VARIANCE_REVIEW_CHOICES,
    )

    # ── Close-on-behalf-of accountability (2026-08-02, live request) ────────────
    # Roy: "give the owner the ability to close shift on behalf of the manager
    # or staff in shift at any point in time" — for the exact scenario where
    # business hours have passed, real sales were made, but the staffer simply
    # forgot to close on the app before leaving. Rather than always waiting on
    # the auto-close inactivity sweep (_SHIFT_AUTO_CLOSE_INACTIVITY_HOURS),
    # the owner (or a manager with the same can_confirm_shifts delegation
    # already used by confirm_shift() — never for another manager's shift,
    # same rule as everywhere else in this app) may close it immediately.
    # closed_by is set on EVERY close, self or on-behalf — closed_by == staff
    # is the ordinary case; closed_by != staff means it was force-closed on
    # someone else's behalf, and force_close_reason (optional, reason-chips
    # or a prompt) explains why, matching this app's wording/accountability
    # standard of naming who acted, when, and why.
    closed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='shifts_closed',
        help_text='Who actually submitted the close — same as staff for an '
                   'ordinary self-close, different when an owner/manager '
                   'closed it on someone else\'s behalf.',
    )
    force_close_reason = models.CharField(
        max_length=300, blank=True,
        help_text='Optional explanation when closed_by differs from staff — '
                   'why the owner/manager closed this shift on their behalf.',
    )

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Shift'
        verbose_name_plural = 'Shifts'

    def __str__(self):
        return f"{self.staff.get_full_name() or self.staff.username} — {self.started_at.strftime('%d %b %Y %H:%M')} ({self.status})"


class TillCount(models.Model):
    """Owner/manager confirms the actual cash physically at a counter RIGHT
    NOW — 2026-07-30 live request: "ensure the owner can confirm cash at
    counters at any given moment for the system to know." Before this,
    shift_views.till_expected_cash()'s only anchor source was a shift
    CLOSE (Shift.closing_cash_counted) — the continuous till figure could
    only ever be reset by someone closing a shift, never by the owner
    simply walking up to a counter and counting the drawer mid-shift (or
    with no shift open at all, since the owner sells freely with no gate).
    This model is a second, independent anchor source: till_expected_cash()
    picks whichever of (last shift close, last TillCount) is more recent
    for a given station, so a spot confirmation immediately becomes the new
    baseline for every later calculation — the running total resets
    cleanly from that point forward regardless of how many cash sales had
    already accumulated before the confirmation, since the formula is
    always `this anchor's amount + movements strictly AFTER it`.

    expected_amount/variance are a snapshot of what the system expected at
    the moment of counting (before this row itself becomes the new
    anchor) — purely for the audit trail on this SPECIFIC confirmation;
    they play no role in any later calculation."""
    STATION_CHOICES = [('bar', 'Bar'), ('kitchen', 'Kitchen')]
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='till_counts',
    )
    station = models.CharField(max_length=10, choices=STATION_CHOICES)
    counted_amount = models.DecimalField(max_digits=10, decimal_places=2)
    expected_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counted_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, related_name='till_counts',
    )
    counted_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-counted_at']
        verbose_name = 'Till Count'
        verbose_name_plural = 'Till Counts'

    def __str__(self):
        who = self.counted_by.get_full_name() or self.counted_by.username if self.counted_by else '—'
        return f"{self.get_station_display()} — KES {self.counted_amount} by {who} ({self.counted_at.strftime('%d %b %Y %H:%M')})"


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
    tapped_by  = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='kegs_tapped',
        help_text='Who tapped (opened) this barrel — 2026-08-11 live request '
                   '(Roy): visible on the tile alongside received_by, same '
                   'accountability trail this app already keeps for every '
                   'other stock action.',
    )
    closed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='kegs_closed',
        help_text='Who closed this barrel — deplete_barrel() (Imekwisha) or '
                   'discard() (Tupa), whichever happened.',
    )
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
            self.tapped_by = user
            self.save(update_fields=['status', 'tapped_at', 'tapped_by'])

    def close(self, reason='', closed_by=None):
        if self.status in ('SEALED', 'TAPPED'):
            self.status = 'RETURNED' if reason else 'DEPLETED'
            self.closed_at = timezone.now()
            self.closed_by = closed_by
            update_fields = ['status', 'closed_at', 'closed_by']
            if reason:
                self.note = (self.note + ' | ' if self.note else '') + reason
                update_fields.append('note')
            self.save(update_fields=update_fields)
            if self.status == 'DEPLETED':
                _refresh_keg_baseline(self)

    def record_sale(self, preset, qty, payment_method, recorded_by, tab=None, server_name='',
                     created_at=None):
        """
        One pour. Creates Transaction(type=Issue) and updates the envelope.
        If tab is provided, payment_method is set to 'credit' and a BarTabEntry is created.
        Auto-DEPLETED when envelope reached AND latest weight ≤ tare + 0.5 kg.

        created_at (2026-08-12 live request, Roy): catch-up/backdated posting
        for a keg pour, mirroring ProduceBunch.record_sale()'s/KitchenBatch.
        record_sale()'s own backdate support — previously Bar Board's
        checkout had no backdate path at all, silently stamping "now"
        regardless of what the checkout form was set to. None (the default)
        behaves exactly as before.
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
            recorded_by=recorded_by,
            **({'created_at': created_at, 'date': timezone.localtime(created_at).date()}
               if created_at else {}),
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
                           recorded_by, tab=None, server_name='', created_at=None):
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
                                      tab=tab, server_name=server_name, created_at=created_at)

    @classmethod
    def record_owner_draw_locked(cls, barrel_id, business, preset, qty, recorded_by, created_at=None):
        """2026-08-16 live request (Roy): "bar board has no back dating,
        same as mmiliki alichukua modal" — Quick Sell's Mmiliki Alichukua
        modal deliberately excludes keg items entirely (a plain qty×price
        draw makes no sense for a keg — the physical unit sold is a preset
        pour, e.g. a pint, not the item's own base unit), so a keg pour the
        owner takes had NO way to be recorded as a draw at all, anywhere.

        Deliberately reuses record_sale_locked() UNCHANGED rather than
        writing separate envelope-update math — the barrel's revenue_
        collected/volume_dispensed_ml/cup-pint-jug counters and its own
        target-reached/auto-deplete logic all update EXACTLY as they would
        for a real paid pour, so "how much is physically left in this keg"
        stays accurate regardless of whether a pour was sold or given away
        free — this app's own keg-accounting is document-flagged as its
        most sensitive area, so this intentionally introduces zero new
        envelope math. The one difference: the resulting Transaction is
        immediately reclassified 'Issue' → 'OwnerConsumption' (the same
        in-place type flip OwnerConsumptionTransferRequest._accept_to_owner
        already trusts), which is what makes it invisible to revenue/cash/
        analytics aggregates by construction — Transaction.cost()/
        loss_value() still correctly attribute a proportional cost share to
        it via their existing keg_barrel branch, so it still shows up
        (as a cost, not a sale) on the Owner Drawings analytics tile.
        """
        txn = cls.record_sale_locked(
            barrel_id, business, preset, qty, payment_method='',
            recorded_by=recorded_by, created_at=created_at,
        )
        if txn is None:
            return None
        txn.type = 'OwnerConsumption'
        txn.payment_method = ''
        txn.recipient = 'Mmiliki'
        txn.save(update_fields=['type', 'payment_method', 'recipient'])
        return txn


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
    SOURCE_CHOICES = [('bar', 'Bar'), ('kitchen', 'Kitchen'), ('qs', 'Quick Sell')]
    source        = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='bar')
    status        = models.CharField(max_length=8, choices=STATUS_CHOICES, default='OPEN')
    opened_at     = models.DateTimeField(auto_now_add=True)
    settled_at    = models.DateTimeField(null=True, blank=True)
    void_reason   = models.CharField(max_length=120, blank=True)
    tab_receipt_token = models.CharField(max_length=32, blank=True, default='')
    tab_pin           = models.CharField(max_length=6,  blank=True, default='')
    cash_requested_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set when a customer taps "Lipa Cash" on their live receipt — no money '
                   'has moved, this just flags staff to expect them at the counter. Cleared '
                   'the moment staff settles any entry on the tab.'
    )

    class Meta:
        ordering = ['-opened_at']
        verbose_name = 'Bar Tab'
        verbose_name_plural = 'Bar Tabs'
        constraints = [
            models.UniqueConstraint(
                fields=['business', 'tab_pin'],
                condition=models.Q(status='OPEN') & ~models.Q(tab_pin=''),
                name='unique_open_tab_pin_per_business',
            )
        ]

    def __str__(self):
        return f"Tab — {self.customer_name} ({self.status})"

    @staticmethod
    def new_credentials(business):
        """Generate a receipt token + business-unique 4-digit PIN for a new tab.

        Single source of truth for all BarTab creation sites (bar board, kitchen,
        Quick Sell) so BillScan lookup (find_tab_search, tab_live) never sees a
        tab with a blank or colliding PIN. The read-then-return here has no DB
        lock between the read and the eventual save, so two concurrent tab-opens
        could still race onto the same PIN — the unique_open_tab_pin_per_business
        constraint is the real guarantee; create_with_credentials() below retries
        on the resulting IntegrityError.
        """
        import random
        import secrets
        existing_pins = set(
            BarTab.objects.filter(business=business, status='OPEN').values_list('tab_pin', flat=True)
        )
        pin = str(random.randint(1000, 9999))
        while pin in existing_pins:
            pin = str(random.randint(1000, 9999))
        return secrets.token_urlsafe(20), pin

    @classmethod
    def create_with_credentials(cls, **fields):
        """Create a BarTab with a fresh token/PIN, retrying once on a PIN collision.

        Single retry point for all 3 creation sites (bar board, kitchen, Quick
        Sell) — see new_credentials() for why the collision is possible at all
        despite the pre-check.
        """
        from django.db import IntegrityError, transaction as _db_transaction
        token, pin = cls.new_credentials(fields['business'])
        try:
            with _db_transaction.atomic():
                return cls.objects.create(tab_receipt_token=token, tab_pin=pin, **fields)
        except IntegrityError:
            token, pin = cls.new_credentials(fields['business'])
            return cls.objects.create(tab_receipt_token=token, tab_pin=pin, **fields)

    def total(self):
        result = self.entries.aggregate(t=models.Sum('amount'))['t']
        return result or Decimal('0')

    def unpaid_total(self):
        result = self.entries.filter(is_paid=False).aggregate(t=models.Sum('amount'))['t']
        return result or Decimal('0')

    @classmethod
    def settle_entries_amount_locked(cls, tab_id, business, entry_ids, amount, payment_method, recorded_by=None):
        """Settle up to `amount` KES across the given entries (ascending id
        order — oldest first), splitting the boundary entry via BarTabEntry.
        split_paid_unpaid_locked() when `amount` doesn't land exactly on an
        entry boundary. Any entries beyond what `amount` covers are left
        completely untouched — still selectable in a later call (e.g. the
        rest paid via a different method).

        2026-07-25 live request (theft-prevention): a customer paying 70 of
        an 80 tab via M-Pesa had no correct way to be recorded before this —
        staff could only settle selected entries in full or not at all, so
        the shortfall either got silently marked as fully paid (losing KES
        10 with no trace) or the whole payment was refused. Every KES of
        `amount` is now applied to some entry; the exact shortfall (if the
        selected entries total more than `amount`) stays as an ordinary
        unpaid balance on this same tab — never silently written off, never
        auto-converted to debt (staff can still do that separately via the
        existing → Deni action if that's what's actually happened).

        Two calls with different payment_method values handle "part M-Pesa,
        part cash" cleanly — the second call just selects whatever's left.

        Raises ValueError if no matching unpaid entries, amount <= 0, or
        amount exceeds the selected entries' total (can't "pay" more than
        what's owed in one call — that's an overpayment, out of scope here).
        Returns (fully_paid_entries, split_remainder_entry_or_None).
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            tab = cls.objects.select_for_update().get(id=tab_id, business=business)
            entries = list(
                BarTabEntry.objects.select_for_update().select_related('transaction')
                .filter(tab=tab, id__in=entry_ids, is_paid=False)
                .order_by('id')
            )
            if not entries:
                raise ValueError('Hakuna kiingilio kilichochaguliwa.')

            total_selected = sum((e.amount for e in entries), Decimal('0'))
            amount = Decimal(str(amount))
            if amount <= 0:
                raise ValueError('Kiasi lazima kiwe zaidi ya 0.')
            if amount > total_selected:
                raise ValueError('Kiasi ni zaidi ya deni la vitu vilivyochaguliwa.')

            remaining = amount
            fully_paid = []
            split_remainder = None
            now = timezone.now()
            for entry in entries:
                if remaining <= 0:
                    break
                if remaining >= entry.amount:
                    entry.is_paid = True
                    entry.paid_at = now
                    entry.payment_method = payment_method
                    entry.settled_by = recorded_by
                    entry.save(update_fields=['is_paid', 'paid_at', 'payment_method', 'settled_by'])
                    if entry.transaction_id:
                        entry.transaction.payment_method = payment_method
                        entry.transaction.save(update_fields=['payment_method'])
                    fully_paid.append(entry)
                    remaining -= entry.amount
                else:
                    split_remainder = BarTabEntry.split_paid_unpaid_locked(
                        entry, remaining, payment_method, recorded_by,
                    )
                    fully_paid.append(entry)  # now IS the paid portion, amount reduced in place
                    remaining = Decimal('0')
        return fully_paid, split_remainder

    def settle_and_partial_convert_to_debt(self, cash_amount, mpesa_amount, customer_name, phone='', staff_user=None):
        """One-shot "partial payment now, remainder becomes debt" for a tab
        that was just created for a single walk-up sale (2026-07-31 live
        request — Roy: "customer paid cash 120... there is a remainder",
        "mpesa 100 then 20 cash and there is a remainder" — Bar Board and
        Kitchen Board's direct checkout had no way to record this without
        first opening a tab, partially settling it via the tabs drawer, and
        separately converting the rest to debt — three separate manual
        steps for what should be one checkout action).

        Deliberately built by CHAINING the exact same, already-proven
        mechanisms used everywhere else in this app for these two steps —
        settle_entries_amount_locked() (the 2026-07-25 theft-prevention
        partial-tab-payment feature, itself just fixed 2026-07-31 for the
        Transaction.payment_method-stuck-on-'credit' bug) and
        core.keg_views._convert_tab_to_debt_core() (the same customer/SMS/
        notify behaviour convert_tab_to_debt's own "→ Deni" button uses) —
        never a new, separately-invented payment-splitting mechanism. That
        reuse is deliberate: a NEW ad-hoc splitting mechanism is exactly how
        the Transaction.payment_method bug fixed this same session was
        introduced in the first place.

        Settles up to cash_amount then mpesa_amount across every unpaid
        entry on this tab (oldest-first, same walk order
        settle_entries_amount_locked already uses), then converts whatever
        remains unpaid to debt under customer_name/phone.

        Raises ValueError if cash_amount+mpesa_amount <= 0 (nothing
        collected — use the plain Deni checkout instead) or >= the tab's
        current unpaid total (nothing left to convert — use the plain
        cash/mpesa/split checkout instead), or if customer_name is blank.
        Returns (customer, unpaid_total_left_as_debt).
        """
        cash_amount = Decimal(str(cash_amount or 0))
        mpesa_amount = Decimal(str(mpesa_amount or 0))
        collected = cash_amount + mpesa_amount
        if collected <= 0:
            raise ValueError('Kiasi kilicholipwa lazima kiwe zaidi ya 0.')
        if not (customer_name or '').strip():
            raise ValueError('Jina la mteja anayedaiwa deni linahitajika.')

        total_unpaid = self.unpaid_total()
        if collected >= total_unpaid:
            raise ValueError('Kiasi kilicholipwa hakiwezi kuwa sawa au zaidi ya jumla — hakuna deni la kubaki.')

        entry_ids = list(self.entries.filter(is_paid=False).values_list('id', flat=True))
        if cash_amount > 0:
            BarTab.settle_entries_amount_locked(
                self.id, self.business, entry_ids, cash_amount, 'cash', staff_user,
            )
        if mpesa_amount > 0:
            entry_ids = list(self.entries.filter(is_paid=False).values_list('id', flat=True))
            BarTab.settle_entries_amount_locked(
                self.id, self.business, entry_ids, mpesa_amount, 'mpesa', staff_user,
            )

        from core.keg_views import _convert_tab_to_debt_core
        self.refresh_from_db()
        return _convert_tab_to_debt_core(self, self.business, customer_name, phone)


class BarTabEntry(models.Model):
    tab         = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='entries')
    transaction = models.OneToOneField(
        Transaction, on_delete=models.CASCADE, related_name='tab_entry'
    )
    description    = models.CharField(max_length=80)
    amount         = models.DecimalField(max_digits=10, decimal_places=2)
    is_paid        = models.BooleanField(default=False)
    amount_paid    = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    # ↑ 2026-08-15 — a debt-converted tab entry can be paid PARTIALLY (see
    # settle_tab's own amount= param / _do_settle_debt_payment's FIFO walk).
    # is_paid alone can't express "KES 200 of KES 480 covered, 280 still
    # owed" — this tracks that remainder so _get_customer_debt_data can
    # correctly report the TRUE outstanding amount for a still-unpaid
    # (is_paid=False) entry, instead of assuming the full original amount
    # is owed just because is_paid hasn't flipped to True yet.
    paid_at        = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=10, blank=True)
    settled_by     = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    # ↑ 2026-08-06 live request (Monsoon Inn) — a waitress with cross-station
    # access can now clear bills on either counter; Roy asked for "a trail
    # that Sarah the waitress cleared a certain bill in a certain manner."
    # Set only on STAFF-initiated settlement (tick_entry, settle_tab's plain
    # loop, settle_entries_amount_locked's fully-paid entries, and the paid
    # portion of split_paid_unpaid_locked) — deliberately left None for a
    # customer's own STK/QR self-pay (mpesa_views.py, receipt_views.py),
    # since no staff member acted there and attributing it to whoever
    # happened to be logged in would be a false trail, not a true one.

    class Meta:
        ordering = ['id']
        verbose_name = 'Bar Tab Entry'
        verbose_name_plural = 'Bar Tab Entries'

    def __str__(self):
        status = 'paid' if self.is_paid else 'open'
        return f"{self.tab.customer_name} — {self.description} KES {self.amount} ({status})"

    @classmethod
    def split_and_transfer_locked(cls, entry_id, business, paid_amount, paid_method,
                                   dest_tab_id, staff_user, source_kept_paid=True):
        """
        Split one entry into a paid portion (settled here, on its own tab) and an
        unpaid remainder proposed as a transfer onto a DIFFERENT customer's tab —
        e.g. Roy's 600 Smirnoff: he pays 400 now, his friend Bosco's tab picks up
        the remaining 200 (2026-07-23 live request).

        The remainder is created as an ORDINARY unpaid BarTabEntry on the SOURCE
        tab (not the destination) plus a TabTransferRequest tracking row — it
        only actually moves to the destination tab when that request is
        accepted (TabTransferRequest.accept()). This is deliberate: a rejection
        then needs zero reversal logic, since the entry never left the source
        tab in the first place; every existing surface (receipts, analytics,
        debt conversion, Z-reports) sees a completely ordinary unpaid entry the
        whole time it's pending, because that's exactly what it is.

        The new Transaction for the remainder carries qty=0 (no additional
        stock left the shelf — this re-bills an already-sold item, it isn't a
        new sale) and copies the original's keg_barrel/produce_bunch/
        kitchen_batch FK (if any) so Transaction.cost()'s existing proportional
        formula correctly attributes the remaining cost share. It must NOT be
        created via KegBarrel.record_sale()/KitchenBatch.record_sale()/
        ProduceBunch.record_sale_locked() — those increment the envelope's
        revenue_collected, which was already correctly incremented once, at
        the original sale; incrementing it again here would inflate that
        envelope's apparent revenue and understate cost() for every OTHER sale
        drawn from the same barrel/batch, not just this one.

        paid_amount=0 (2026-07-25 live request) is a FULL-item transfer —
        Bosco covers the whole item, Roy pays nothing towards it — handled as
        its own short-circuit path below with no splitting at all, since
        there's no "paid portion" to carve off; the original entry itself,
        unmodified, is what the pending request points at.

        source_kept_paid (2026-07-30 live request) — Roy's exact scenario:
        a KES 225 Captain Morgan half, Roy takes it on debt (pays nothing
        right now), Bosco covers 100 of it, leaving Roy owing 125. This is
        NOT the paid_amount>0 case above (which records a REAL payment by
        the source customer) — the 125 that stays behind must remain an
        ordinary UNPAID balance on Roy's own tab, not a payment. When
        source_kept_paid=False, `paid_amount` is reinterpreted as "the
        amount that stays on the source tab" (still unpaid, no method
        required) rather than "the amount the source pays now" — the
        transfer amount is still entry.amount - paid_amount either way.
        See split_kept_unpaid_locked() for the mechanism.

        Raises ValueError on any validation failure (caller renders as a JSON
        error) — insufficient/invalid amount, tab not open, same tab picked
        twice, or an in-flight STK payment already referencing this entry.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            entry = cls.objects.select_for_update().select_related('transaction', 'tab').get(
                id=entry_id, tab__business=business,
            )
            if entry.is_paid:
                raise ValueError('Kiingilio hiki tayari kimelipwa.')
            # 2026-08-11 live request (Roy): a source customer already
            # converted to debt (tab.status='SETTLED') must still be
            # transferable — entry.is_paid=False above already guarantees
            # this is a genuine, still-owed item; SETTLED is allowed
            # alongside OPEN, everything else (VOID) is not.
            if entry.tab.status not in ('OPEN', 'SETTLED'):
                raise ValueError('Tab ya kiingilio hiki haiko wazi wala haijawa deni.')

            paid_amount = Decimal(str(paid_amount))
            if paid_amount < 0 or paid_amount >= entry.amount:
                raise ValueError(
                    'Kiasi cha kulipa lazima kiwe 0 au zaidi, na pungufu ya jumla ya kiingilio.'
                )
            if source_kept_paid and paid_amount > 0 and paid_method not in ('cash', 'mpesa'):
                raise ValueError('Njia ya malipo si sahihi.')

            dest_tab = BarTab.objects.select_for_update().get(id=dest_tab_id, business=business)
            # 2026-08-11: a destination already converted to debt (SETTLED)
            # is now a valid target too — see accept()'s own comment for how
            # the underlying Transaction.recipient gets synced so the debt
            # ledger correctly attributes the moved item to its new owner.
            if dest_tab.status not in ('OPEN', 'SETTLED'):
                raise ValueError('Tab lengwa haiko wazi wala haijawa deni.')
            if dest_tab.id == entry.tab_id:
                raise ValueError('Huwezi kuhamisha kwenye tab iyo hiyo.')

            # In-flight STK guard: an entry mid-settlement via a pending Payment must
            # not be split — the eventual callback would resolve entry_id -> tab using
            # stale linkage once part of it has moved. tab_entry_ids is a JSONField
            # list; checked in Python rather than a __contains ORM lookup because that
            # lookup is unsupported on SQLite (see core/tab_receipts.py's
            # _safe_linked_query for the same class of guard, same reason).
            _pending = Payment.objects.filter(
                bar_tab__business=business, status='pending', tab_entry_ids__isnull=False,
            )
            for _p in _pending:
                if entry.id in (_p.tab_entry_ids or []):
                    raise ValueError('Malipo ya STK yanaendelea kwa kiingilio hiki — subiri kwanza.')

            if paid_amount == 0:
                # Full-item transfer (2026-07-25 live request): Bosco covers
                # the WHOLE item, Roy pays nothing towards it at all. Unlike a
                # partial split there is no "paid portion" to carve off and
                # leave behind — the entry itself, unmodified, is what the
                # pending request points at; it only actually moves tabs on
                # accept(), exactly like the split-remainder case already
                # does, so rejection still needs zero reversal.
                #
                # Unlike the paid_amount>0 branch below (which self-protects
                # against a duplicate retry by flipping entry.is_paid=True),
                # this path leaves the entry completely unmodified — so a
                # retry would otherwise sail through every check again and
                # create a second pending request on the same entry. Same
                # explicit guard propose_whole_tab_locked() already uses
                # (2026-07-25 audit finding).
                if TabTransferRequest.objects.filter(entry_id=entry.id, status='PENDING').exists():
                    raise ValueError('Kiingilio hiki tayari kina ombi la uhamisho linalosubiri.')
                transfer = TabTransferRequest.objects.create(
                    business=business, entry=entry,
                    source_tab=entry.tab, dest_tab=dest_tab, amount=entry.amount,
                    paid_amount=Decimal('0'), requested_by=staff_user, note=entry.description,
                )
                return entry, transfer

            if source_kept_paid:
                new_entry = cls.split_paid_unpaid_locked(entry, paid_amount, paid_method, staff_user)
            else:
                new_entry = cls.split_kept_unpaid_locked(entry, paid_amount, staff_user)
            transfer = TabTransferRequest.objects.create(
                business=business, entry=new_entry,
                source_tab=entry.tab, dest_tab=dest_tab, amount=new_entry.amount,
                paid_amount=(paid_amount if source_kept_paid else Decimal('0')),
                requested_by=staff_user, note=entry.description,
            )
        return new_entry, transfer

    @classmethod
    def split_paid_unpaid_locked(cls, entry, paid_amount, paid_method, recorded_by=None):
        """Split an entry into a paid portion (reduced in place to
        paid_amount, marked paid via paid_method) and a NEW unpaid remainder
        entry on the SAME tab. Caller must already hold the row lock
        (select_for_update) and have validated 0 < paid_amount < entry.amount.

        Shared building block: split_and_transfer_locked() (above) uses this
        for its split step, then proposes the remainder to a DIFFERENT
        customer's tab; BarTab.settle_entries_amount_locked() (2026-07-25 —
        theft-prevention: a customer paying 70 of an 80 tab via M-Pesa had no
        correct way to be recorded before this) uses it too, leaving the
        remainder as an ordinary unpaid entry on THIS SAME tab instead —
        same split mechanic, different destiny for the remainder.

        The new Transaction carries qty=0 (no additional stock left the
        shelf — this re-bills an already-sold item) and copies the
        original's keg_barrel/produce_bunch/kitchen_batch FK so Transaction.
        cost()'s proportional formula still attributes correctly — see
        split_and_transfer_locked()'s docstring for the full reasoning.
        Returns the new remainder BarTabEntry (unpaid, same tab).
        """
        remainder = entry.amount - paid_amount
        orig_txn = Transaction.objects.select_for_update().get(pk=entry.transaction_id)

        # payment_method MUST move off 'credit' here too (found 2026-07-31,
        # live report: Hezzy's tab — 50 total, 40 paid via mpesa, 10 left
        # owing — showed BOTH 40 and 10 as still-owed once converted to
        # debt). Every tab-item Transaction starts as payment_method='credit'
        # (see KegBarrel.record_sale's `pay = 'credit' if tab else ...`) —
        # this method only ever updated the BarTabEntry's own payment_method
        # below, leaving the underlying Transaction (what the debt tracker's
        # credit_qs and _reconcile()'s cash/mpesa/credit aggregates both read
        # directly, independent of BarTabEntry.is_paid) stuck reporting
        # 'credit' forever for the KEPT/PAID portion — so a genuinely-settled
        # mpesa/cash payment kept counting as unpaid debt AND kept
        # understating the shift's real cash/mpesa collected. The sibling
        # non-split branch in settle_entries_amount_locked() (a few lines up)
        # already gets this right (`entry.transaction.payment_method =
        # payment_method`); revoke_payment_locked() also depends on the two
        # staying in sync. split_kept_unpaid_locked() is correctly NOT
        # touched here — its kept portion is still genuinely unpaid, so
        # 'credit' remains the right tag for it.
        orig_txn.sale_amount = paid_amount
        orig_txn.payment_method = paid_method
        orig_txn.save(update_fields=['sale_amount', 'payment_method'])

        entry.amount = paid_amount
        entry.is_paid = True
        entry.payment_method = paid_method
        entry.paid_at = timezone.now()
        entry.settled_by = recorded_by
        entry.save(update_fields=['amount', 'is_paid', 'payment_method', 'paid_at', 'settled_by'])

        # payment_method='credit' EXPLICITLY (found 2026-07-25, live Monsoon
        # Inn cash-reconciliation report: system showed KES 2980 expected,
        # physical count KES 1700). Transaction.payment_method's model field
        # default is 'cash' — omitting this kwarg here silently tagged every
        # STILL-UNPAID remainder (money not yet collected — either sitting as
        # an ordinary open balance on this same tab, or a split-transfer
        # pending a different customer's acceptance) as if it were a
        # completed cash sale. core.shift_views._reconcile()'s cash_sales
        # aggregate reads Transaction.payment_method directly, with no
        # awareness of the sibling BarTabEntry.is_paid flag, so every such
        # remainder inflated expected_cash for money genuinely still sitting
        # unpaid — exactly the "not tabs" money Roy's shift math must
        # exclude. 'credit' matches the convention every other unpaid/on-a-
        # tab Transaction in the app already uses (see KegBarrel.record_sale:
        # `pay = 'credit' if tab else ...`), so it's correctly excluded from
        # cash_sales/mpesa_sales and instead counted in credit_sales.
        new_txn = Transaction.objects.create(
            item=orig_txn.item, business=orig_txn.business, type='Issue',
            qty=Decimal('0'), sale_amount=remainder, payment_method='credit',
            keg_barrel=orig_txn.keg_barrel, produce_bunch=orig_txn.produce_bunch,
            kitchen_batch=orig_txn.kitchen_batch,
            date=orig_txn.date, created_at=orig_txn.created_at,
            recorded_by=recorded_by,
        )
        return cls.objects.create(
            tab=entry.tab, transaction=new_txn,
            description=entry.description, amount=remainder, is_paid=False,
        )

    @classmethod
    def split_kept_unpaid_locked(cls, entry, kept_amount, recorded_by=None):
        """Mirror of split_paid_unpaid_locked, for the case where NOBODY
        pays anything at split time — 2026-07-30 live request: Roy's KES
        225 Captain Morgan half, taken entirely on debt; Bosco covers 100
        of it, Roy is left owing 125. Unlike split_paid_unpaid_locked (which
        marks the kept portion PAID), this leaves the kept portion exactly
        as unpaid as it already was — reduced in amount only, no payment
        flag ever touched. The carved-off transfer_amount becomes the new
        unpaid entry proposed to the destination tab, same as the sibling
        method. Caller must already hold the row lock and have validated
        0 <= kept_amount < entry.amount.
        """
        transfer_amount = entry.amount - kept_amount
        orig_txn = Transaction.objects.select_for_update().get(pk=entry.transaction_id)

        orig_txn.sale_amount = kept_amount
        orig_txn.save(update_fields=['sale_amount'])
        entry.amount = kept_amount
        entry.save(update_fields=['amount'])
        # entry.is_paid / payment_method deliberately untouched — it was
        # already an ordinary unpaid tab charge and stays exactly that,
        # just for a smaller amount now that part of it has moved away.

        new_txn = Transaction.objects.create(
            item=orig_txn.item, business=orig_txn.business, type='Issue',
            qty=Decimal('0'), sale_amount=transfer_amount, payment_method='credit',
            keg_barrel=orig_txn.keg_barrel, produce_bunch=orig_txn.produce_bunch,
            kitchen_batch=orig_txn.kitchen_batch,
            date=orig_txn.date, created_at=orig_txn.created_at,
            recorded_by=recorded_by,
        )
        return cls.objects.create(
            tab=entry.tab, transaction=new_txn,
            description=entry.description, amount=transfer_amount, is_paid=False,
        )

    @classmethod
    def revoke_payment_locked(cls, entry_id, business, reason, staff_user):
        """Revert a mistakenly-settled entry back to unpaid — e.g. staff
        tapped M-Pesa when the customer actually paid cash, marked something
        paid that hasn't been paid at all yet, or settled the wrong
        customer's tab entirely (2026-07-25 live request). Staff then just
        re-settles correctly through the ordinary settle flow — this method
        only ever undoes the payment flags, it never re-settles anything
        itself.

        Deliberately does NOT touch Transaction.qty or sale_amount — the
        item was genuinely served and stock genuinely left the shelf at ADD-
        TO-TAB time, completely independent of whether/how it was paid for;
        only payment_method/is_paid/paid_at are reverted. Revenue totals
        (which read sale_amount, never touched here) and stock counts (which
        read qty, also never touched here) are therefore unaffected by a
        revoke — this is a payment-record correction, not a sale reversal
        (that's what remove_tab_entry / void_tab are for, a different
        action for a different mistake — the item was wrong, not the
        payment). Reopens the tab if this was its last paid entry, so the
        customer's receipt goes live again immediately.

        Blocked on a 'void' entry (that's a permanent removal, a different
        lifecycle — see remove_tab_entry) and on an already-unpaid entry
        (nothing to revoke). Raises ValueError on any validation failure.
        Returns the reverted BarTabEntry.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            entry = cls.objects.select_for_update().select_related(
                'transaction', 'tab',
            ).get(id=entry_id, tab__business=business)
            if not entry.is_paid:
                raise ValueError('Kiingilio hiki bado hakijalipwa.')
            if entry.payment_method == 'void':
                raise ValueError('Kiingilio hiki kimefutwa — hakiwezi kurudishwa hivi.')

            previous_method = entry.payment_method
            tab = entry.tab

            was_stk_confirmed = Payment.objects.filter(
                bar_tab=tab, status='completed', method='mpesa',
            ).exists()

            entry.is_paid = False
            entry.payment_method = ''
            entry.paid_at = None
            entry.save(update_fields=['is_paid', 'payment_method', 'paid_at'])
            if entry.transaction_id:
                entry.transaction.payment_method = ''
                entry.transaction.save(update_fields=['payment_method'])

            # Reopen the tab if reverting this entry means it's no longer
            # fully settled — the receipt/wall-QR page must see it live again.
            if tab.status == 'SETTLED':
                tab.status = 'OPEN'
                tab.settled_at = None
                tab.save(update_fields=['status', 'settled_at'])

            TabPaymentRevocation.objects.create(
                business=business, tab=tab, entry=entry,
                item_description=entry.description, amount=entry.amount,
                previous_payment_method=previous_method,
                was_stk_confirmed=was_stk_confirmed,
                reason=(reason or '').strip(), revoked_by=staff_user,
            )
        return entry

    def transfer_reason_note(self):
        """If this entry's balance was ever proposed as a split-bill transfer
        to a different customer's tab and that didn't go through — rejected,
        or cancelled because the source tab was converted to debt/voided
        before anyone responded — return a short explanation of why. Used
        wherever this entry later needs to explain itself with no other
        context on hand: a debt statement line, the owner-facing debt
        ledger, or a tabs-drawer note (2026-07-24 live request: "so when Roy
        later comes on... the receipt shows him how the debt occurred").
        Computed fresh from the permanent TabTransferRequest audit trail each
        time, never baked into `description` — so it can't go stale and
        every surface reads the same live answer. Returns '' when this entry
        has no such history (the ordinary case).

        Wording note (2026-07-24, live correction): "itafunikwa" (lit. "will
        be covered/capped", as in a lid on a pot) is wrong for a payment
        obligation — this is a financial transaction, not a physical object
        being covered. Uses "inafaa kulipwa" (ought to be paid) instead. Also
        addresses the reader directly ("wewe mwenyewe", not just "mwenyewe")
        since this note is read BY the debtor on THEIR OWN statement — a
        second-person "you paid it yourself", not a third-person aside — and
        includes exactly when that payment happened, not just how much.
        """
        tfr = self.transfer_requests.filter(
            status__in=['REJECTED', 'CANCELLED']
        ).order_by('-resolved_at').first()
        if not tfr:
            return ''
        who = tfr.dest_tab.customer_name
        if tfr.paid_amount:
            when = timezone.localtime(tfr.requested_at)
            paid_bit = (
                f' (ulishalipa KES {tfr.paid_amount:,.0f} wewe mwenyewe'
                f' tarehe {when.strftime("%d %b %Y")} saa {when.strftime("%H:%M")})'
            )
        else:
            paid_bit = ''
        if tfr.status == 'REJECTED':
            return f'Ilikuwa inafaa kulipwa na {who}, alikataa kulipa{paid_bit}'
        return f'Ilikuwa inafaa kulipwa na {who}, hakujibu kwa wakati{paid_bit}'


class TabTransferRequest(models.Model):
    """
    Tracks a proposed move of one BarTabEntry's balance from the tab it's
    currently (ordinarily) sitting on, onto a DIFFERENT customer's open tab —
    created by BarTabEntry.split_and_transfer_locked(). See that method's
    docstring for why accept() is a one-field mutation and reject() needs no
    reversal at all.
    """
    STATUS_CHOICES = [
        ('PENDING',   'Pending'),
        ('ACCEPTED',  'Accepted'),
        ('REJECTED',  'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]
    business     = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='tab_transfer_requests')
    entry        = models.ForeignKey(BarTabEntry, on_delete=models.CASCADE, related_name='transfer_requests')
    source_tab   = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='transfer_requests_out')
    dest_tab     = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='transfer_requests_in')
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount  = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Snapshot of what the source customer paid themselves at split '
                  'time (e.g. Roy\'s 50 of an 80 KES cup) — captured here so the '
                  'pending banner and any later debt-reasoning note can say "X '
                  'already paid Y himself" without a fragile join back to the '
                  'sibling entry that was reduced at split time.',
    )
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    requested_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='tab_transfer_requests_made')
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at  = models.DateTimeField(null=True, blank=True)
    note         = models.CharField(max_length=80, blank=True)
    batch_id     = models.CharField(
        max_length=32, blank=True, default='', db_index=True,
        help_text='Shared by every row created by one "transfer whole tab" '
                  'action (2026-07-25) — blank for an ordinary single-item '
                  'transfer. accept()/reject() cascade to every PENDING '
                  'sibling sharing this id so a whole-tab transfer resolves '
                  'as ONE decision, not one per item.',
    )

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Transfer #{self.id}: {self.source_tab.customer_name} -> {self.dest_tab.customer_name} (KES {self.amount}, {self.status})"

    def accept(self):
        """Move the entry onto the destination tab. A single-field reassignment —
        no new Transaction, no envelope revenue_collected change, nothing else
        touched — see split_and_transfer_locked()'s docstring for why.

        A whole-tab transfer (batch_id set — see propose_whole_tab_locked())
        must resolve as ONE decision, not one per item: if this row belongs
        to a batch, every PENDING sibling sharing batch_id is accepted in the
        same atomic block, all-or-nothing (2026-07-25 live request).

        Pre-existing bug found while adding batching: this used to mutate
        only a separately-fetched `fresh` row, never `self` — every call
        site (respond_tab_transfer, receipt_respond_tab_transfer,
        tab_respond_tab_transfer) calls bare `transfer.accept()` without
        capturing a return value, then reads `transfer.status` straight
        after, which was always still the stale 'PENDING' it started with.
        Now syncs `self` too so every existing call site reads correctly
        with no call-site changes needed."""
        from django.db import transaction as _txn
        with _txn.atomic():
            fresh = TabTransferRequest.objects.select_for_update().get(pk=self.pk)
            if fresh.status != 'PENDING':
                raise ValueError('Ombi hili tayari limeshughulikiwa.')
            siblings = [fresh]
            if fresh.batch_id:
                siblings = list(
                    TabTransferRequest.objects.select_for_update()
                    .filter(batch_id=fresh.batch_id, business=fresh.business_id, status='PENDING')
                    .order_by('id')
                )
            result = fresh
            for row in siblings:
                dest_tab = BarTab.objects.select_for_update().get(pk=row.dest_tab_id)
                # 2026-08-11 live request (Roy): a destination already
                # converted to debt (SETTLED) is now a valid target too.
                if dest_tab.status not in ('OPEN', 'SETTLED'):
                    raise ValueError('Tab lengwa haiko wazi tena wala haijawa deni.')
                entry = BarTabEntry.objects.select_for_update().get(pk=row.entry_id)
                entry.tab = dest_tab
                entry.save(update_fields=['tab'])
                # If the destination is a debt-converted tab, there is no
                # future convert_tab_to_debt() call coming to attribute this
                # moved item correctly — convert_tab_to_debt()'s own entry
                # loop only runs ONCE, at the moment of conversion, and that
                # already happened before this item arrived. Sync the
                # underlying Transaction's recipient/payment_method right
                # here instead, mirroring exactly what conversion itself
                # does, so the debt tracker attributes it to its new owner.
                # An OPEN destination needs no such sync — either it stays
                # open (irrelevant to the debt ledger, correctly excluded by
                # _get_customer_debt_data()'s own tab__status='OPEN'
                # exclusion) or it gets converted later, at which point that
                # future conversion sets recipient correctly on its own.
                if dest_tab.status != 'OPEN':
                    txn = entry.transaction
                    txn.recipient = dest_tab.customer_name
                    txn.payment_method = 'credit'
                    txn.save(update_fields=['recipient', 'payment_method'])
                row.status = 'ACCEPTED'
                row.resolved_at = timezone.now()
                row.save(update_fields=['status', 'resolved_at'])
                if row.pk == self.pk:
                    result = row

            # 2026-07-25 live report: after every entry on the source tab moved
            # away (or was already paid — e.g. Roy's own 400 of a split 600),
            # the source tab had nothing left to collect but its status stayed
            # OPEN forever, since nothing ever re-checked it — it lingered in
            # the tabs drawer indefinitely with a zero balance. Same "is there
            # anything left unpaid" check _finish_settle_tab() already uses,
            # and the same VOID-with-explanation closing pattern
            # _merge_tab_into() already established for an emptied-out tab
            # shell (this isn't a real cancellation — nothing went wrong).
            source_tab = BarTab.objects.select_for_update().get(pk=fresh.source_tab_id)
            if source_tab.status == 'OPEN' and not source_tab.entries.filter(is_paid=False).exists():
                dest_names = ', '.join(sorted({row.dest_tab.customer_name for row in siblings}))
                source_tab.status = 'VOID'
                source_tab.settled_at = timezone.now()
                source_tab.void_reason = (
                    f'Bili yote ilihamishiwa kwa {dest_names} — hakuna iliyobaki kulipwa hapa'
                )[:120]
                source_tab.save(update_fields=['status', 'settled_at', 'void_reason'])
        self.status = result.status
        self.resolved_at = result.resolved_at
        return result

    def reject(self):
        """Decline the transfer. The entry never left the source tab, so there is
        nothing to reverse — it just stays there, ordinary and unpaid, exactly
        as it already was. Cascades to every PENDING sibling sharing batch_id,
        same reasoning as accept() above — a whole-tab transfer is one decision.
        Also syncs `self` — see accept()'s docstring for why."""
        from django.db import transaction as _txn
        with _txn.atomic():
            fresh = TabTransferRequest.objects.select_for_update().get(pk=self.pk)
            if fresh.status != 'PENDING':
                raise ValueError('Ombi hili tayari limeshughulikiwa.')
            siblings = [fresh]
            if fresh.batch_id:
                siblings = list(
                    TabTransferRequest.objects.select_for_update()
                    .filter(batch_id=fresh.batch_id, business=fresh.business_id, status='PENDING')
                    .order_by('id')
                )
            now = timezone.now()
            result = fresh
            for row in siblings:
                row.status = 'REJECTED'
                row.resolved_at = now
                row.save(update_fields=['status', 'resolved_at'])
                if row.pk == self.pk:
                    result = row
        self.status = result.status
        self.resolved_at = result.resolved_at
        return result

    def cancel(self):
        """Used by the inverse-action safeguard when the source tab is voided or
        converted to debt while this request is still pending — the entry it
        refers to is about to leave the open-tab lifecycle, so a pending
        request against it no longer makes sense. No-op if already resolved."""
        from django.db import transaction as _txn
        with _txn.atomic():
            fresh = TabTransferRequest.objects.select_for_update().get(pk=self.pk)
            if fresh.status != 'PENDING':
                return fresh
            fresh.status = 'CANCELLED'
            fresh.resolved_at = timezone.now()
            fresh.save(update_fields=['status', 'resolved_at'])
        return fresh

    @classmethod
    def propose_whole_tab_locked(cls, source_tab_id, dest_tab_id, business, staff_user):
        """Propose transferring EVERY currently-unpaid entry on source_tab
        onto dest_tab, as ONE bundled accept/reject decision — e.g. Bosco
        offers to cover Roy's whole tab, not just one item (2026-07-25 live
        request: Bosco is on a bar keg tab, Roy's tab is on Quick Sell —
        this works across all three counters, station-scoping is enforced
        by the calling view, not here).

        Each entry gets its own full-item TabTransferRequest row (the same
        zero-paid-amount path split_and_transfer_locked() uses — no
        splitting, no payment, entries move as-is on accept), all sharing
        one batch_id so the destination customer sees and resolves ONE
        request, not N separate ones. accept()/reject() already cascade
        across every row sharing batch_id.

        Snapshot semantics: only entries unpaid AT PROPOSAL TIME are
        included. A new sale added to source_tab afterward is a completely
        ordinary new entry, not silently swept into an already-pending
        decision.

        Raises ValueError on any validation failure — tab not open, same
        tab picked twice, nothing to transfer, an entry already has a
        pending transfer of its own, or an in-flight STK payment references
        one of the entries.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            source_tab = BarTab.objects.select_for_update().get(id=source_tab_id, business=business)
            dest_tab = BarTab.objects.select_for_update().get(id=dest_tab_id, business=business)
            # 2026-08-11 live request (Roy): either side may already be
            # converted to debt (SETTLED) — see split_and_transfer_locked()'s
            # matching comment and accept()'s recipient-sync for the full
            # reasoning.
            if source_tab.status not in ('OPEN', 'SETTLED'):
                raise ValueError('Tab chanzo haiko wazi wala haijawa deni.')
            if dest_tab.status not in ('OPEN', 'SETTLED'):
                raise ValueError('Tab lengwa haiko wazi wala haijawa deni.')
            if source_tab.id == dest_tab.id:
                raise ValueError('Huwezi kuhamisha kwenye tab iyo hiyo.')

            entries = list(
                BarTabEntry.objects.select_for_update()
                .filter(tab=source_tab, is_paid=False)
                .order_by('id')
            )
            if not entries:
                raise ValueError('Hakuna vitu vya kuhamisha kwenye tab hii.')

            entry_ids = [e.id for e in entries]
            if TabTransferRequest.objects.filter(entry_id__in=entry_ids, status='PENDING').exists():
                raise ValueError(
                    'Baadhi ya vitu kwenye tab hii tayari vina ombi la uhamisho '
                    'linalosubiri — kamilisha hilo kwanza.'
                )

            # Same in-flight-STK guard as split_and_transfer_locked(), checked
            # across every entry being bundled — see that method's docstring
            # for why this matters (a callback resolving mid-transfer would
            # use stale tab linkage once part of the tab has moved).
            _stk_locked_ids = set()
            for _p in Payment.objects.filter(
                bar_tab=source_tab, status='pending', tab_entry_ids__isnull=False,
            ):
                _stk_locked_ids.update(_p.tab_entry_ids or [])
            if _stk_locked_ids & set(entry_ids):
                raise ValueError('Malipo ya STK yanaendelea kwa baadhi ya vitu — subiri kwanza.')

            import uuid as _uuid
            batch_id = _uuid.uuid4().hex
            requests = []
            for e in entries:
                tfr = TabTransferRequest.objects.create(
                    business=business, entry=e,
                    source_tab=source_tab, dest_tab=dest_tab, amount=e.amount,
                    paid_amount=Decimal('0'), requested_by=staff_user, note=e.description,
                    batch_id=batch_id,
                )
                requests.append(tfr)
        return batch_id, requests


class OwnerConsumptionTransferRequest(models.Model):
    """Tracks a proposed reclassification of a single item/whole-bill
    between a customer's tab/debt and the owner's own "Mmiliki Alichukua"
    ledger (2026-08-12 live request, Roy) — either direction, with an
    accept/reject step on whichever side receives it.

    Deliberately NOT a reuse of TabTransferRequest (that model is tightly
    coupled to BarTabEntry/BarTab on both ends) — mirrors its lifecycle
    shape only. Deliberately whole-item/whole-bill only, no partial split,
    matching Roy's own wording ("transfer an item or bill whether one or
    all").

    The move itself is a single in-place Transaction.type flip between
    'Issue' and 'OwnerConsumption' (see accept() below) — the same
    "excluded/included by construction" mechanism this app already trusts
    for Draw/Transfer types, so the reclassified row instantly and
    correctly disappears from (or appears in) every existing revenue/debt/
    analytics aggregate with zero new exclusion logic to write or audit.
    """
    DIRECTION_TO_OWNER = 'to_owner'
    DIRECTION_FROM_OWNER = 'from_owner'
    DIRECTION_CHOICES = [
        (DIRECTION_TO_OWNER, 'To Owner'),
        (DIRECTION_FROM_OWNER, 'From Owner'),
    ]
    STATUS_CHOICES = [
        ('PENDING',   'Pending'),
        ('ACCEPTED',  'Accepted'),
        ('REJECTED',  'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]

    business  = models.ForeignKey('accounts.Business', on_delete=models.CASCADE,
                                   related_name='owner_consumption_transfer_requests')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    # to_owner: the customer's own Transaction (tab-linked or a plain debt
    #           txn) being reclassified as the owner's draw.
    # from_owner: the OwnerConsumption Transaction being handed to a customer.
    source_txn = models.ForeignKey('Transaction', on_delete=models.CASCADE,
                                    related_name='owner_transfer_requests')
    # Only meaningful for from_owner — who it's being offered to. Resolved
    # against an existing open tab by name (same auto-detect-by-name
    # guarantee the cross-counter merge feature already established), or a
    # brand-new tab is opened for them on accept.
    dest_customer_name = models.CharField(max_length=100, blank=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    requested_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='owner_consumption_transfer_requests_made')
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at  = models.DateTimeField(null=True, blank=True)
    note         = models.CharField(max_length=80, blank=True)
    batch_id     = models.CharField(
        max_length=32, blank=True, default='', db_index=True,
        help_text='Shared by every row created by one whole-bill transfer — '
                  'blank for a single-item transfer. accept()/reject() '
                  'cascade to every PENDING sibling sharing this id.',
    )

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"OwnerTransfer #{self.id}: {self.direction} (KES {self.source_txn.sale_amount or 0}, {self.status})"

    def _siblings(self):
        if self.batch_id:
            return OwnerConsumptionTransferRequest.objects.filter(
                batch_id=self.batch_id, status='PENDING',
            )
        return OwnerConsumptionTransferRequest.objects.filter(id=self.id)

    def accept(self, resolved_dest_tab=None):
        """Resolve every PENDING sibling sharing batch_id (a whole-bill
        transfer resolves as ONE decision), in one atomic block."""
        from django.db import transaction as _txn
        with _txn.atomic():
            siblings = list(self._siblings().select_for_update())
            for req in siblings:
                if req.direction == self.DIRECTION_TO_OWNER:
                    req._accept_to_owner()
                else:
                    req._accept_from_owner(resolved_dest_tab)
                req.status = 'ACCEPTED'
                req.resolved_at = timezone.now()
                req.save(update_fields=['status', 'resolved_at'])
        self.refresh_from_db()

    def reject(self):
        """No reversal needed either direction — nothing moves until
        accepted, matching the split-transfer feature's own contract."""
        from django.db import transaction as _txn
        with _txn.atomic():
            siblings = list(self._siblings().select_for_update())
            for req in siblings:
                req.status = 'REJECTED'
                req.resolved_at = timezone.now()
                req.save(update_fields=['status', 'resolved_at'])
        self.refresh_from_db()

    def cancel(self):
        from django.db import transaction as _txn
        with _txn.atomic():
            siblings = list(self._siblings().select_for_update())
            for req in siblings:
                req.status = 'CANCELLED'
                req.resolved_at = timezone.now()
                req.save(update_fields=['status', 'resolved_at'])
        self.refresh_from_db()

    def _accept_to_owner(self):
        txn = Transaction.objects.select_for_update().get(id=self.source_txn_id)
        try:
            tab_entry = txn.tab_entry
        except Exception:
            tab_entry = None
        txn.type = 'OwnerConsumption'
        txn.payment_method = ''
        txn.recipient = 'Mmiliki'
        txn.save(update_fields=['type', 'payment_method', 'recipient'])
        if tab_entry is not None:
            # Same "close out an entry we're finished with" pattern
            # remove_tab_entry uses — the item is leaving this tab's own
            # unpaid-balance accounting entirely, not being paid on it.
            tab_entry.is_paid = True
            tab_entry.save(update_fields=['is_paid'])

    def _accept_from_owner(self, resolved_dest_tab=None):
        txn = Transaction.objects.select_for_update().get(id=self.source_txn_id)
        dest_tab = resolved_dest_tab
        if dest_tab is None:
            dest_tab = BarTab.objects.filter(
                business=self.business, customer_name__iexact=self.dest_customer_name,
                status='OPEN',
            ).first()
            if dest_tab is None:
                dest_tab = BarTab.create_with_credentials(
                    business=self.business, customer_name=self.dest_customer_name,
                    status='OPEN', source='qs',
                )
        txn.type = 'Issue'
        txn.payment_method = 'credit'
        txn.recipient = dest_tab.customer_name
        txn.save(update_fields=['type', 'payment_method', 'recipient'])
        BarTabEntry.objects.create(
            tab=dest_tab, transaction=txn,
            description=txn.item.description if txn.item_id else '',
            amount=txn.sale_amount or Decimal('0'),
        )

    @classmethod
    def propose_to_owner_locked(cls, txn_ids, business, requested_by, note=''):
        """Propose one or more customer debt/tab transactions as the
        owner's own draw. Accepts a single id or a list.

        2026-08-13 live request (Roy) — widened from a single-item-only
        signature to support two new bulk flows: linking a Customer record
        as an owner alias (moves everything currently unpaid under that
        name in one action) and the cross-customer search/bulk-transfer
        page. Unlike propose_from_owner_locked's sibling method — which
        errors out entirely if ANY given id already has a pending request
        — this SKIPS ids that already have one instead of raising, so the
        "resync" action on an already-linked customer is a safe, idempotent
        no-op for anything already proposed/transferred, only picking up
        genuinely new unpaid debt each time it's pressed. Only raises when
        NOTHING in the given set is actually eligible.
        """
        if isinstance(txn_ids, (str, int)):
            txn_ids = [txn_ids]
        txns = list(Transaction.objects.filter(
            id__in=txn_ids, business=business, type='Issue', payment_method='credit',
        ))
        if not txns:
            raise ValueError('Transaction haipatikani au si deni linaloweza kuhamishwa.')
        already_pending_ids = set(
            OwnerConsumptionTransferRequest.objects.filter(
                source_txn__in=txns, status='PENDING',
            ).values_list('source_txn_id', flat=True)
        )
        txns = [t for t in txns if t.id not in already_pending_ids]
        if not txns:
            raise ValueError('Ombi la kuhamisha item(s) hizi tayari lipo.')
        import uuid as _uuid
        batch_id = _uuid.uuid4().hex if len(txns) > 1 else ''
        requests = []
        for txn in txns:
            requests.append(cls.objects.create(
                business=business, direction=cls.DIRECTION_TO_OWNER,
                source_txn=txn, requested_by=requested_by, note=note[:80], batch_id=batch_id,
            ))
        return requests

    @classmethod
    def propose_to_owner_partial_locked(cls, txn_id, business, paid_amount, paid_method, requested_by, note=''):
        """2026-08-16 live request (Roy): "customer acquisition of an item
        and partial payment is going to the owner" — the customer pays
        PART of a credit item/debt themselves, right now, and only the
        REMAINDER is proposed to the owner (still needs his accept, same
        as every other transfer). Mirrors the customer-to-customer split-
        transfer feature's own paid_amount>0 shape (BarTabEntry.split_
        and_transfer_locked), but the destination is the owner's own
        "Mmiliki Alichukua" ledger instead of another customer's tab.

        Works for BOTH a tab-linked transaction (splits via BarTabEntry.
        split_paid_unpaid_locked — the remainder stays an ordinary unpaid
        entry on the SAME tab until accepted, exactly like the customer-
        to-customer feature) and a plain non-tab direct credit sale
        (splits via Transaction.split_credit_paid_unpaid_locked). Either
        way, the customer's own paid portion is a REAL payment recorded
        immediately; only the remainder becomes a pending proposal —
        rejecting it needs zero reversal, since the remainder never left
        the customer's name until accepted.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            try:
                txn = Transaction.objects.get(
                    id=txn_id, business=business, type='Issue', payment_method='credit',
                )
            except Transaction.DoesNotExist:
                raise ValueError('Transaction haipatikani au si deni linaloweza kuhamishwa.')
            try:
                entry = txn.tab_entry
            except Exception:
                entry = None

            if paid_method not in ('cash', 'mpesa'):
                raise ValueError('Njia ya malipo si sahihi.')

            if entry is not None:
                entry = BarTabEntry.objects.select_for_update().select_related('tab').get(id=entry.id)
                if entry.is_paid:
                    raise ValueError('Kiingilio hiki tayari kimelipwa.')
                if entry.tab.status not in ('OPEN', 'SETTLED'):
                    raise ValueError('Tab ya kiingilio hiki haiko wazi wala haijawa deni.')
                paid_amount_dec = Decimal(str(paid_amount))
                if paid_amount_dec <= 0 or paid_amount_dec >= entry.amount:
                    raise ValueError('Kiasi cha kulipa lazima kiwe kati ya 0 na jumla ya kiingilio.')
                new_entry = BarTabEntry.split_paid_unpaid_locked(entry, paid_amount_dec, paid_method, requested_by)
                new_txn_id = new_entry.transaction_id
            else:
                _orig, new_txn = Transaction.split_credit_paid_unpaid_locked(
                    txn.id, business, paid_amount, paid_method, staff_user=requested_by,
                )
                new_txn_id = new_txn.id
                # 2026-08-16: unlike the tab-linked branch (where is_paid is
                # 100% authoritative for outstanding, no total_paid pool
                # involved at all), a non-tab transaction's outstanding is
                # computed from total_credit MINUS total_paid — and
                # total_credit deliberately never shrinks (it includes any
                # transaction that ever transitioned off 'credit', via
                # was_credit — see _get_customer_debt_data's own 2026-08-15
                # fix). Without a matching CustomerDebtPayment recorded here,
                # the now-paid-off original portion would still count toward
                # total_credit with nothing offsetting it, overstating what
                # the customer still owes by exactly the amount they already
                # paid. Mirrors _do_settle_debt_payment's own record-then-
                # sync shape, just for a single specific transaction instead
                # of a FIFO walk across several.
                customer = Customer.objects.filter(
                    business=business, name__iexact=(txn.recipient or ''),
                ).first()
                if customer is not None:
                    is_kitchen = bool(txn.item and txn.item.store and txn.item.store.is_kitchen)
                    CustomerDebtPayment.objects.create(
                        customer=customer, business=business,
                        amount_paid=Decimal(str(paid_amount)), payment_method=paid_method,
                        source=('kitchen' if is_kitchen else 'bar'),
                        notes='Malipo ya sehemu — kilichobaki kimehamishiwa mmiliki',
                        recorded_by=requested_by,
                    )

            reqs = cls.propose_to_owner_locked([new_txn_id], business, requested_by, note=note)
            return reqs[0]

    @classmethod
    def propose_from_owner_locked(cls, txn_ids, business, dest_customer_name, requested_by, note=''):
        txns = list(Transaction.objects.filter(
            id__in=txn_ids, business=business, type='OwnerConsumption',
        ))
        if not txns:
            raise ValueError('Hakuna rekodi za Mmiliki Alichukua zilizopatikana.')
        if OwnerConsumptionTransferRequest.objects.filter(
            source_txn__in=txns, status='PENDING',
        ).exists():
            raise ValueError('Ombi la kuhamisha item hii tayari lipo.')
        import uuid as _uuid
        batch_id = _uuid.uuid4().hex if len(txns) > 1 else ''
        requests = []
        for txn in txns:
            requests.append(cls.objects.create(
                business=business, direction=cls.DIRECTION_FROM_OWNER,
                source_txn=txn, dest_customer_name=dest_customer_name.strip(),
                requested_by=requested_by, note=note[:80], batch_id=batch_id,
            ))
        return requests


class TabPaymentRevocation(models.Model):
    """Audit trail row for BarTabEntry.revoke_payment_locked() (2026-07-25 live
    request): "Roy has a bill of 200, staff selected M-Pesa when it was cash,
    or it wasn't paid yet, or staff confused the tab for another customer" —
    every one of these needs the SAME fix: flip the entry back to unpaid so
    it can be corrected. Kept as its own row (not just overwritten fields on
    the entry) so a business owner can later see WHO reverted WHAT, WHEN, and
    WHY — the entry itself only ever shows its current state, not its
    history, same reasoning as StaffNameChangeLog/SalesResetLog."""
    business           = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='tab_payment_revocations')
    tab                = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='payment_revocations')
    entry              = models.ForeignKey(BarTabEntry, on_delete=models.CASCADE, related_name='payment_revocations')
    item_description   = models.CharField(max_length=80)
    amount             = models.DecimalField(max_digits=10, decimal_places=2)
    previous_payment_method = models.CharField(max_length=10, blank=True)
    was_stk_confirmed  = models.BooleanField(
        default=False,
        help_text='True if a completed Payment (Safaricom-confirmed STK) record was found '
                  'for this tab at revoke time — a hint to whoever re-settles that real '
                  'M-Pesa money may already be involved, shown so they check before assuming '
                  'nothing was actually paid.',
    )
    reason      = models.CharField(max_length=200, blank=True)
    revoked_by  = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='tab_payment_revocations')
    revoked_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-revoked_at']

    def __str__(self):
        return f"Revoked payment: {self.item_description} KES {self.amount} ({self.previous_payment_method})"


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
    """Shift stock take: staff records physical item counts for peace-of-mind reconciliation.

    2026-07-30: gained `phase` (opening/closing) so a shift can carry BOTH an
    opening-time baseline count AND a closing-time count for the same item
    without one silently overwriting the other (previously unique_together
    was just (shift, item) — a closing count would clobber that shift's own
    opening count for the same item). Every consumer that sums these rows
    into a loss/variance figure (keg_metrics.staff_shrinkage's bottle loss,
    bar_z_report's day_bottle_variance_kes) is built around a CLOSING count
    specifically — comparing book balance against what's physically left
    after a day's sales — so those queries filter to phase='closing' only;
    an opening count would be a meaningless (usually ~zero) input there and
    double-count the same shift if left unfiltered. _missed_tasks_for_shift's
    "did you do your stock take" reminder is about the closing count too.
    """
    PHASE_CHOICES = [
        ('opening', 'Opening'),
        ('closing', 'Closing'),
    ]
    shift       = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='stock_counts')
    item        = models.ForeignKey('Item', on_delete=models.SET_NULL, null=True, related_name='stock_counts')
    phase       = models.CharField(max_length=10, choices=PHASE_CHOICES, default='closing')
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
        unique_together = [('shift', 'item', 'phase')]

    def __str__(self):
        return f"Shift #{self.shift_id} — {self.item} ({self.phase}: {self.actual_count} / book {self.book_balance})"

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
    payment_method = models.CharField(
        max_length=10, choices=PAYMENT_CHOICES, default='cash', blank=True,
        help_text='Legacy field, kept for schema history — no longer read to set the '
                   'final sale\'s payment method (see tab below). Payment is now decided '
                   'when the table\'s tab is settled, not guessed at order-placement time.',
    )
    # ── Table-service redesign (2026-08-05 live request) ────────────────────────
    # Roy: "check all ways an order might be placed when it comes to payment or
    # bills." Before this, payment_method was locked in AT ORDER TIME — before the
    # food/drinks were even served — with no way to correct it, no support for a
    # table running up several rounds on one bill, and none of the payment
    # machinery (partial settle, split-transfer, debt conversion, wall-QR/PIN,
    # STK push, revoke) this app already built for BarTab. Rather than reinventing
    # any of that a second time inside TableOrder, SERVED now bills the order's
    # items onto the table's own running BarTab (found-or-created by table_label,
    # same anonymous-tab-by-name pattern every other counter already uses) — so a
    # table's bill IS a tab, gets the exact same wall-QR/PIN + full payment
    # toolkit as any other tab, and multiple rounds for the same table
    # automatically accumulate on one bill for free.
    tab            = models.ForeignKey(
        'BarTab', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='table_orders',
        help_text="The table's running tab — this order's items are billed here "
                  "once SERVED. Payment (cash/mpesa/split/debt) happens by settling "
                  "this tab, not at order-placement time.",
    )
    notes          = models.CharField(max_length=200, blank=True)
    # 2026-08-06 live request (Monsoon Inn) — a waitress with cross-station
    # access (UserProfile.can_access_kitchen) can now facilitate orders on
    # EITHER counter from the Order Desk; this captures which one she was
    # toggled to at placement time, same explicit-field-captured-from-
    # context pattern already established by Shift.station (never
    # inferred from item type after the fact — see that field's own
    # docstring for why). Blank for orders placed before this field
    # existed; table_order_queue_api() shows those on BOTH boards rather
    # than guessing, matching PettyCash.station's blank-row convention.
    station        = models.CharField(
        max_length=10, blank=True,
        choices=[('bar', 'Bar'), ('kitchen', 'Kitchen')],
    )
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    served_at      = models.DateTimeField(null=True, blank=True)
    # Cancellation trail (2026-07-24 wording/accountability audit) — cancel used to be
    # a bare status flip with no reason and no notification, on both cancel paths
    # (the waitress-side cancel_table_order() and the bar-board oqUpdate(...,'CANCELLED')
    # shortcut) — same gap already closed for PerformerSession the same day.
    cancel_reason  = models.CharField(max_length=200, blank=True)
    cancelled_by   = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='table_orders_cancelled',
    )
    cancelled_at   = models.DateTimeField(null=True, blank=True)

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
    source_item       = models.ForeignKey(
        'Item', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_batches_drawn',
        help_text='Raw-material item this batch\'s cost was drawn from, if opened via '
                  'the sack-tracking flow (item.raw_material_source set). Null for '
                  'batches opened with a manually typed cost.',
    )
    source_qty_drawn  = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text='Quantity drawn from source_item to open this batch, if applicable.',
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

    def record_sale(self, amount, payment_method='cash', recipient='', preset=None, recorded_by=None,
                     created_at=None):
        """Sell from this batch. Creates Transaction, updates revenue_collected + khaki count.

        created_at (2026-08-12 live request, Roy — Chipo backdated catch-up
        sales weren't taking effect, always landing on today): mirrors the
        plain portion-item checkout's own backdate support, which never
        reached this batch/envelope path before. None (the default) behaves
        exactly as before — always "now"."""
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
            recorded_by=recorded_by,
            # See ProduceBunch.record_sale()'s identical 2026-08-12 comment —
            # Transaction.date defaults independently of created_at, so both
            # must be set together or a backdated batch sale silently keeps
            # date=today.
            **({'created_at': created_at, 'date': timezone.localtime(created_at).date()} if created_at else {}),
        )
        self.revenue_collected = (self.revenue_collected or Decimal('0')) + amount
        if preset:
            if preset.khaki_type == 'SMALL':
                self.khaki_small_used = (self.khaki_small_used or 0) + 1
            elif preset.khaki_type == 'LARGE':
                self.khaki_large_used = (self.khaki_large_used or 0) + 1
        self.save(update_fields=['revenue_collected', 'khaki_small_used', 'khaki_large_used'])
        return txn

    @classmethod
    def open_batch(cls, business, store, item, recorded_by, cost_total=None,
                    cost_note='', note='', draw_qty=None, received_on=None):
        """
        Single entry point for opening a new KitchenBatch — used by both
        kitchen_receive()'s kitchen_batch mode and kitchen_batch_receive()
        (kitchen-module raw-material sack-tracking feature, 2026-07-22).

        Two mutually exclusive cost paths:
          - item.raw_material_source is set: draw_qty (kg/etc used today) is
            required. Locks the raw item, validates it has enough balance,
            creates a 'Draw' Transaction on it (an internal stock movement,
            NOT a sale — Transaction.cost() returns 0 for type='Draw', so this
            never double-counts against the batch's own cost below), and
            derives cost_total = draw_qty * raw_item.cost_price.
          - Otherwise: cost_total must be supplied directly — the original
            manual-entry flow, unchanged.

        received_on (2026-08-12 live request, Roy): backdate the batch itself
        — a `date` — so a raw-material draw/batch opened to catch up a past
        day's fries doesn't land on "today" with no way to correct it (this
        was the other half of the same complaint as record_sale()'s
        created_at param above). Also backdates the raw-material Draw
        transaction's own created_at to match, so the sack's own history
        (avg_daily_issues, reorder alerts) reflects the real day the kg was
        actually drawn, not the day it was typed into the system. None (the
        default) behaves exactly as before — both stamp "today"/"now".

        Always sets item.cost_price = cost_total afterwards — discard()'s
        wastage Transaction relies on that (see its own docstring).

        Raises ValueError (caller renders as a JSON error) on any validation
        failure — insufficient raw balance, non-positive cost/qty, etc.

        Multiple simultaneously OPEN batches for the same item are a
        deliberate, tested, ALLOWED scenario (the "multi-pot" case — she may
        genuinely have more than one pot of chips going on a busy day; see
        KitchenBatchOpenBatchDrawTest.test_sequential_draws_deduct_balance_
        correctly, which locks this in on purpose) — NOT something this
        method should guard against. 2026-08-12 live report (Roy): the real
        bug this uncovered lives in kitchen_board()'s TILE, which only ever
        reads the newest open_batches[0], silently hiding any OTHER
        still-open batch's own cost/revenue — fixed there (kitchen_board.html)
        instead, by surfacing every open batch beyond the first with its own
        real numbers and a direct close/discard action, rather than blocking
        a legitimate multi-pot day here.
        """
        from django.db import transaction as _txn
        source_item = None
        source_qty = None
        draw_created_at = None
        if received_on is not None:
            from datetime import datetime as _dt, time as _time
            draw_created_at = timezone.make_aware(
                _dt.combine(received_on, _time.min), timezone.get_current_timezone(),
            )
        with _txn.atomic():
            if item.raw_material_source_id:
                if draw_qty is None:
                    raise ValueError('Weka kiasi ulichotumia (kg).')
                draw_qty = Decimal(str(draw_qty))
                if draw_qty <= 0:
                    raise ValueError('Kiasi kilichotumika lazima kiwe zaidi ya 0.')
                source_item = Item.objects.select_for_update().get(id=item.raw_material_source_id)
                available = source_item.current_balance()
                if draw_qty > available:
                    raise ValueError(
                        f'{source_item.description} ina {available:g}{source_item.unit} pekee '
                        f'iliyobaki — huwezi kutumia {draw_qty:g}{source_item.unit}.'
                    )
                cost_total = (draw_qty * (source_item.cost_price or Decimal('0'))).quantize(Decimal('0.01'))
                Transaction.objects.create(
                    item=source_item, business=business, type='Draw',
                    qty=-draw_qty,
                    recipient=f'Kitchen batch: {item.description}'[:200],
                    recorded_by=recorded_by,
                    # See ProduceBunch.record_sale()'s 2026-08-12 comment —
                    # avg_daily_issues() reads type__in=['Issue','Draw'], so a
                    # backdated draw needs the same date/created_at sync.
                    **({'created_at': draw_created_at, 'date': timezone.localtime(draw_created_at).date()} if draw_created_at else {}),
                )
                source_qty = draw_qty
            else:
                cost_total = Decimal(str(cost_total if cost_total is not None else '0'))

            if cost_total <= 0:
                raise ValueError('Gharama lazima iwe zaidi ya 0.')

            batch = cls.objects.create(
                business=business, store=store, item=item,
                cost_total=cost_total, cost_note=cost_note, note=note,
                recorded_by=recorded_by,
                source_item=source_item, source_qty_drawn=source_qty,
                **({'received_on': received_on} if received_on else {}),
            )
            # See the matching comment in discard() — its wastage math relies
            # on item.cost_price == cost_total (one batch = one unit here).
            item.cost_price = cost_total
            item.save(update_fields=['cost_price'])
        return batch

    def deplete(self):
        """Mark batch as sold out."""
        if self.status != 'OPEN':
            return
        from django.utils import timezone as _tz
        self.status = 'DEPLETED'
        self.closed_on = _tz.now()
        self.save(update_fields=['status', 'closed_on'])

    def discard(self, reason=''):
        """Write off the unrecovered cost of this batch as wastage.

        Kitchen-module audit finding, 2026-07-19: this used to only flip status
        — unlike ProduceBunch.discard() (the sibling revenue-envelope model),
        it never created a Wastage Transaction. A pot of chips or stew thrown
        out went completely unrecorded: invisible to analytics' wastage_loss
        (which only sums Transaction(type='Wastage')), invisible to net_profit,
        invisible to the owner — food wastage is a marquee metric for a food
        business and this was silently dropping it. Mirrors ProduceBunch's
        fraction-of-envelope approach: qty is the UNRECOVERED fraction of
        cost_total (not the whole batch) so a batch that already sold past its
        cost before being tossed correctly records zero loss.
        """
        if self.status == 'DISCARDED':
            return None
        unrecovered = max(Decimal('0'), self.cost_total - (self.revenue_collected or Decimal('0')))
        txn = None
        if unrecovered > 0 and self.cost_total > 0:
            fraction = (unrecovered / self.cost_total).quantize(Decimal('0.0001'))
            txn = Transaction.objects.create(
                item=self.item,
                business=self.business,
                type='Wastage',
                qty=-fraction,
                sale_amount=Decimal('0'),
                recipient=(reason or 'Discarded')[:200],
                kitchen_batch=self,
            )
        from django.utils import timezone as _tz
        self.status = 'DISCARDED'
        self.closed_on = _tz.now()
        self.note = (self.note + ' | ' if self.note else '') + (reason or 'Discarded')
        self.save(update_fields=['status', 'closed_on', 'note'])
        return txn

    @classmethod
    def split_by_date_locked(cls, batch_id, business, cutoffs, requested_by):
        """2026-08-16 live request (Roy): a kitchen staffer forgot to tap
        "Imekwisha" between buckets of fries — she kept selling straight
        through buckets 2 and 3 without ever closing bucket 1 or opening a
        new batch for the ones after it, so every sale from all THREE
        physical buckets landed on the one still-open batch, and only
        bucket 1's own raw-material draw was ever recorded (buckets 2/3
        used real potatoes with no matching deduction — Raw Potatoes'
        balance was overstated by exactly that much). Roy already has the
        real cutoff moments written down in the paper sales book.

        Splits `batch_id` into len(cutoffs)+1 batches along those exact
        moments: the ORIGINAL batch keeps everything before cutoffs[0] and
        closes as DEPLETED (bucket 1, done); one NEW batch is created per
        cutoff for everything from that cutoff up to the next one (or now,
        for the last) — each via open_batch() UNCHANGED, so it gets its
        own real raw-material draw (same kg as the original batch drew,
        the recommended default — "same cost as bucket 1's"; self-corrects
        Raw Potatoes' overstated balance as a side effect) and its own
        cost_total; every earlier resulting batch closes DEPLETED, only
        the LAST (the currently-active bucket) stays OPEN. Every
        Transaction (sale AND any Wastage from a discard) with kitchen_
        batch_id pointing at the original batch is reassigned by its own
        created_at into whichever segment it actually falls in — the
        receipt/profit picture for each bucket "adjusts accordingly" for
        free, since KitchenStockReceipt.total_revenue() and the analytics
        Kitchen Performance table both read straight from Transaction.

        Raises ValueError on any validation failure — batch not OPEN,
        wrong business, cutoffs not strictly increasing, a cutoff outside
        the batch's own history, or (for a raw-material-tracked item) not
        enough raw balance left for the extra draws.
        """
        from django.db import transaction as _txn
        with _txn.atomic():
            batch = cls.objects.select_for_update().select_related('item', 'store').get(
                id=batch_id, business=business,
            )
            if batch.status != 'OPEN':
                raise ValueError('Batch hii si wazi — haiwezi kugawanywa.')
            if not cutoffs:
                raise ValueError('Weka angalau tarehe moja ya mgawanyo.')

            # Deliberately NOT auto-sorted — cutoffs must be given in the
            # real chronological order the buckets actually happened in
            # (bucket 2 started, then bucket 3 started); silently re-
            # ordering a mistyped list would mask a real data-entry error
            # instead of catching it.
            for i in range(1, len(cutoffs)):
                if cutoffs[i] <= cutoffs[i - 1]:
                    raise ValueError('Tarehe za mgawanyo lazima ziwe kwa mfuatano wa nyakati (kila moja baada ya iliyotangulia).')

            from datetime import datetime as _dt, time as _time
            batch_start = timezone.make_aware(
                _dt.combine(batch.received_on, _time.min), timezone.get_current_timezone(),
            )
            if cutoffs[0] <= batch_start:
                raise ValueError('Tarehe ya kwanza lazima iwe baada ya batch hii kuanza.')

            all_txns = list(
                Transaction.objects.select_for_update()
                .filter(kitchen_batch_id=batch.id)
                .order_by('created_at')
            )

            # len(cutoffs) NEW batches are created — one per cutoff, each
            # representing the segment STARTING at that cutoff (open-ended,
            # up to the NEXT cutoff or "now" for the very last one). Together
            # with the original batch (which keeps everything before
            # cutoffs[0]) that's len(cutoffs)+1 segments in total — e.g. 2
            # cutoffs (bucket 2 started, bucket 3 started) → 3 total batches.
            draw_qty = batch.source_qty_drawn
            new_batches = []
            for idx, cutoff in enumerate(cutoffs):
                new_batch = cls.open_batch(
                    business=business, store=batch.store, item=batch.item,
                    recorded_by=requested_by,
                    cost_total=(None if batch.item.raw_material_source_id else batch.cost_total),
                    cost_note=batch.cost_note,
                    note=f'Imegawanywa kutoka batch #{batch.id} (kipindi {idx + 2})',
                    draw_qty=draw_qty,
                    received_on=timezone.localtime(cutoff).date(),
                )
                new_batches.append((cutoff, new_batch))

            # Reassign each transaction into whichever segment it falls in —
            # walked from the LAST (largest) cutoff backwards; the first
            # cutoff a transaction's created_at is on/after is its segment.
            # A transaction older than every cutoff is left untouched (it
            # already belongs to the original batch).
            for txn in all_txns:
                target_batch = None
                for cutoff, nb in reversed(new_batches):
                    if txn.created_at >= cutoff:
                        target_batch = nb
                        break
                if target_batch is not None:
                    txn.kitchen_batch = target_batch
                    txn.save(update_fields=['kitchen_batch'])

            # Recompute revenue_collected for the original + every new batch
            # from the transactions that actually ended up on each, rather
            # than incrementally adjusting a running counter — safest,
            # most auditable way to do a retroactive correction like this.
            def _recompute_revenue(b):
                total = Transaction.objects.filter(
                    kitchen_batch=b, type='Issue',
                ).aggregate(total=models.Sum('sale_amount'))['total'] or Decimal('0')
                b.revenue_collected = total
                b.save(update_fields=['revenue_collected'])

            _recompute_revenue(batch)
            for _cutoff, nb in new_batches:
                _recompute_revenue(nb)

            # Close out every segment except the last (still-active) one.
            batch.note = (batch.note + ' | ' if batch.note else '') + (
                f'Imegawanywa {timezone.localtime(timezone.now()).strftime("%d %b %Y, %H:%M")} '
                f'na {requested_by.get_full_name() or requested_by.username if requested_by else "—"} '
                f'— mauzo ya baadaye ya {cutoffs[0].strftime("%d %b, %H:%M")} yamehamishiwa batch mpya.'
            )
            batch.status = 'DEPLETED'
            batch.closed_on = timezone.now()
            batch.save(update_fields=['note', 'status', 'closed_on'])

            for i in range(len(new_batches) - 1):
                _cutoff, nb = new_batches[i]
                nb.status = 'DEPLETED'
                nb.closed_on = timezone.now()
                nb.save(update_fields=['status', 'closed_on'])

            return [batch] + [nb for _c, nb in new_batches]


class KitchenStockReceipt(models.Model):
    """One supplier delivery covering MULTIPLE portion items pooled under one
    cost basis for profit tracking — e.g. one Meatco order: wings, legs, and
    drumsticks bought together, each a completely ordinary Item with its own
    stock balance and its own preset-based sales, unchanged. This header only
    exists to answer "was this whole delivery profitable", not to gate or
    block individual sales (2026-07-25 live request).

    Deliberately does NOT hook into the sale/checkout path (unlike
    KegBarrel/ProduceBunch/KitchenBatch, which increment revenue_collected
    per sale) — profit is computed on demand from ordinary Issue transactions
    on the receipt's own items, in the window since the receipt was created.
    This matches the confirmed real workflow: one delivery is fully sold
    through (staff keeps selling — a big leg cut in half and sold as two
    drumsticks means the sellable count can exceed what's nominally on the
    receipt, so there is no reliable stock-balance cutoff to gate on) before
    the next one is ordered, so overlapping receipts for the same item are
    not the normal case. Closing is always a deliberate staff action
    ("the calculation should go on until she says done"), never automatic.
    """
    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('DONE', 'Done'),
    ]
    business    = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='kitchen_stock_receipts',
    )
    store       = models.ForeignKey(
        'Store', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_stock_receipts',
    )
    supplier    = models.CharField(max_length=100, blank=True, help_text='e.g. Meatco')
    invoice_no  = models.CharField(max_length=50, blank=True, help_text='e.g. Order #A25533')
    received_on = models.DateField(default=timezone.localdate)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    note        = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_stock_receipts_recorded',
    )
    created_at  = models.DateTimeField(default=timezone.now)
    closed_at   = models.DateTimeField(null=True, blank=True)
    closed_by   = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_stock_receipts_closed',
    )

    class Meta:
        ordering = ['-received_on', '-id']
        verbose_name = 'Kitchen Stock Receipt'
        verbose_name_plural = 'Kitchen Stock Receipts'

    def __str__(self):
        return f"{self.supplier or 'Receipt'} #{self.invoice_no or self.id} — {self.get_status_display()}"

    @property
    def total_cost(self):
        return sum((l.line_cost for l in self.lines.all()), Decimal('0'))

    def total_revenue(self):
        """Sum of Issue-transaction revenue for this receipt's own items,
        in the window since this receipt was RECEIVED (or up to closed_at
        once closed). Excludes void sales, matches Transaction.revenue()'s
        own sale_amount-preferred convention.

        2026-08-09, reverted back to plain item-level matching same-day —
        Roy: "if you can't fix the receipt issue based on the recordings of
        previous days when it comes to stock count and sales, just leave it
        be." A same-day attempt at per-preset attribution (only count sales
        of the exact preset a receipt line covers) plus a preset=None
        historical-sale fallback chased a real precision problem but never
        fully resolved what Roy was seeing, and added complexity on top of
        complexity during an already long session. Reverted to the
        original, plain item-level match: any Issue-type sale of an item
        this receipt received counts, regardless of preset.

        2026-08-11 live report (Roy), now confirmed with real evidence via
        the hidden-presets diagnostic: a receipt he'd just typed in
        ("Kamau") showed KES 0 Mapato / -100% Faida despite 32.5 units of
        genuinely recorded, backdated chicken sales existing for its own
        items. Root cause: the window floor was `self.created_at` — the
        moment the RECEIPT ROW was typed into the system, always "now" at
        creation time, never backdatable — while the sales against it can
        (and, for a catch-up posting, routinely do) carry a BACKDATED
        `created_at` pointing to before the receipt was ever entered. Every
        one of those genuinely-happened sales was structurally invisible to
        this window, no matter how the item-vs-preset matching logic reads.
        Fixed by anchoring the window's start to `self.received_on`
        (the DATE the delivery physically arrived — already user-editable,
        already the field Roy fills in at receive time) instead of
        `self.created_at` — a backdated sale correctly counts as long as
        its own date is on or after the day the stock it's selling actually
        arrived, matching how every other backdated-sale-aware figure in
        this app already reasons about "did this happen after the goods
        existed." `received_on` is a plain DateField — combined with
        midnight and localized to the project timezone so the comparison
        is against a real datetime, same convention already used
        elsewhere in this app for a date-only field feeding a datetime
        window. Known, accepted limitation carried over unchanged: a
        stock-count correction (Rekebisha) recorded against the item does
        NOT create revenue here, by design — Rekebisha has no concept of a
        selling price, only a physical count, so "sales reconciled via a
        count correction" will never show real Mapato; only an actual
        recorded sale (with a real amount) does."""
        from datetime import datetime, time as _time
        from django.db.models import Sum, Case, When, F, Value, DecimalField as _DF
        from django.db.models.functions import Abs, Coalesce
        item_ids = list(self.lines.values_list('item_id', flat=True))
        if not item_ids:
            return Decimal('0')
        start = timezone.make_aware(
            datetime.combine(self.received_on, _time.min), timezone.get_current_timezone(),
        )
        end = self.closed_at or timezone.now()
        _rev = Case(
            When(sale_amount__isnull=False, then=F('sale_amount')),
            default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
            output_field=_DF(max_digits=12, decimal_places=2),
        )
        total = Transaction.objects.filter(
            business=self.business, item_id__in=item_ids, type='Issue',
            created_at__gte=start, created_at__lte=end,
        ).exclude(payment_method='void').aggregate(t=Sum(_rev))['t']
        return total or Decimal('0')

    @property
    def profit(self):
        return self.total_revenue() - self.total_cost

    @property
    def profit_pct(self):
        cost = self.total_cost
        if not cost or cost <= 0:
            return None
        return round(float(self.profit) / float(cost) * 100, 1)

    def close(self, user):
        if self.status == 'DONE':
            return
        self.status = 'DONE'
        self.closed_at = timezone.now()
        self.closed_by = user
        self.save(update_fields=['status', 'closed_at', 'closed_by'])

    def reopen(self):
        """Undo a mistaken/premature close — total_revenue()'s window ends
        at closed_at, so a receipt closed before any sales were rung up
        (2026-08-09 live report: Roy closed "Kamau" — 23 Full Chicken Leg
        — while it still showed KES 0 revenue, before he'd resumed selling)
        is permanently frozen at whatever it earned by that moment, with no
        way for later real sales to ever count toward it again. Clearing
        closed_at/closed_by re-opens the window through to "now" (see
        total_revenue()'s own end = self.closed_at or timezone.now()).
        A no-op if already OPEN."""
        if self.status == 'OPEN':
            return
        self.status = 'OPEN'
        self.closed_at = None
        self.closed_by = None
        self.save(update_fields=['status', 'closed_at', 'closed_by'])


class KitchenStockReceiptLine(models.Model):
    """One item within a KitchenStockReceipt — e.g. '20 wings @ KES 98 each'.
    Creates a completely ordinary Receipt Transaction on `item` at line-
    creation time (ONE cost entry, exactly Roy's ask — see
    KitchenStockReceipt.total_revenue() for how selling stays untouched).

    `preset` (2026-07-25, live request): for a single catalogued item that's
    really several differently-priced pre-cut pieces sold via presets under
    one shared name (e.g. Kuku → Bawa/Paja/Kifua presets, bought pre-cut from
    a butcher, NOT whole birds cut on-site — presets carry no stock balance
    of their own, they all deduct from the same shared item balance) — this
    optionally records WHICH preset a line represents, so its unit cost can
    be written to preset.cost_price instead of the shared item.cost_price
    (which cannot represent several different per-cut costs at once). Null
    for an ordinary item with no per-cut cost split."""
    receipt      = models.ForeignKey(
        KitchenStockReceipt, on_delete=models.CASCADE, related_name='lines',
    )
    item         = models.ForeignKey(
        'Item', on_delete=models.PROTECT, related_name='kitchen_stock_receipt_lines',
    )
    preset       = models.ForeignKey(
        'ItemPortionPreset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='kitchen_stock_receipt_lines',
    )
    qty_received = models.DecimalField(max_digits=10, decimal_places=2)
    line_cost    = models.DecimalField(max_digits=10, decimal_places=2)
    transaction  = models.ForeignKey(
        'Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['id']
        verbose_name = 'Kitchen Stock Receipt Line'
        verbose_name_plural = 'Kitchen Stock Receipt Lines'

    def __str__(self):
        label = f" ({self.preset.label})" if self.preset_id else ""
        return f"{self.item.description}{label} × {self.qty_received:g}"

    @property
    def unit_cost(self):
        return (self.line_cost / self.qty_received) if self.qty_received else Decimal('0')


class KitchenConsumableLog(models.Model):
    """
    Tracks purchases of kitchen consumables that are pooled business-wide:
    khaki bags (1/4 and 1/2 sizes), tomato sauce, and cooking oil.
    Electricity/gas are excluded — infrastructure overhead, not logged here.
    """
    CONSUMABLE_CHOICES = [
        ('KHAKI_SMALL', '1/4 Khaki bags'),
        ('KHAKI_LARGE', '1/2 Khaki bags'),
        ('SAUCE_TOMATO', 'Tomato sauce (jerrican)'),
        ('OIL_COOKING', 'Cooking Oil (litres)'),
        ('OTHER', 'Other'),
    ]
    business         = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='kitchen_consumable_logs',
    )
    consumable_type  = models.CharField(max_length=16, choices=CONSUMABLE_CHOICES)
    qty              = models.DecimalField(
        max_digits=8, decimal_places=1,
        help_text='Units bought: pieces for khaki, jerricans for sauce, litres for oil.',
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


# ── UBA §5.2 — Stock transfers between stores ────────────────────────────────
# Goods in transit is the single biggest shrinkage hiding place in a multi-
# outlet business — "nilipeleka Kahawa branch" with no receipt on the other
# end is how stock evaporates. DISPATCH creates an Issue transaction at
# from_store; RECEIVE creates a Receipt transaction at to_store; the gap
# between them is the in-transit window, and the StockTransfer record itself
# is the audit trail. Transaction.transfer links both legs and is used by
# Transaction.revenue()/cost() to exclude transfers from revenue everywhere —
# a transfer is a stock movement, never a sale.

class StockTransfer(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Rasimu'),
        ('DISPATCHED', 'Imetumwa'),
        ('RECEIVED', 'Imepokelewa'),
        ('DISPUTED', 'Ina Utata'),
        ('CANCELLED', 'Imefutwa'),
    ]
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='stock_transfers'
    )
    transfer_number = models.PositiveIntegerField(
        help_text='Gap-free per business — same guarantee as Receipt.receipt_number. '
                  'Display via .reference (e.g. "TRF-0001").'
    )
    from_store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='transfers_out')
    to_store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='transfers_in')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='DRAFT')
    dispatched_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_dispatched',
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_received',
    )
    received_at = models.DateTimeField(null=True, blank=True)
    rider = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_ridden',
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('business', 'transfer_number')]
        ordering = ['-created_at']

    @property
    def reference(self):
        return f"TRF-{self.transfer_number:04d}"

    def __str__(self):
        return f"{self.reference} ({self.from_store} → {self.to_store})"

    @classmethod
    def create_draft_locked(cls, business, from_store, to_store, lines, note='', created_by=None):
        """Create a DRAFT transfer with its lines. `lines` is a list of
        {item_id, qty_sent}. Gap-free transfer_number via the same
        select_for_update() + order_by().first() pattern Receipt.issue()
        already uses (select_for_update() + aggregate() is rejected by
        PostgreSQL — this avoids that entirely)."""
        from django.db import transaction as _tx
        with _tx.atomic():
            latest = cls.objects.select_for_update().filter(business=business).order_by('-transfer_number').first()
            last = latest.transfer_number if latest else 0
            transfer = cls.objects.create(
                business=business, transfer_number=last + 1,
                from_store=from_store, to_store=to_store, note=note, created_by=created_by,
            )
            for line in lines:
                StockTransferLine.objects.create(
                    transfer=transfer, item_id=line['item_id'],
                    qty_sent=Decimal(str(line['qty_sent'])),
                )
            return transfer

    def dispatch_locked(self, dispatched_by=None):
        """DRAFT -> DISPATCHED. Creates one type='Transfer' Transaction per
        line at from_store — a real stock-out (current_balance() sums qty
        across every type, so the balance still deducts correctly) but,
        per the same precedent as KitchenBatch's 'Draw' type, NOT type=
        'Issue' — so it is excluded BY CONSTRUCTION from every existing
        type='Issue'-filtered revenue/analytics/reconciliation query across
        the whole app, with no per-report exclusion list to find and
        maintain. An early design of this used type='Issue' + a transfer_id
        exclusion instead; ruled out after finding it would have required
        auditing every revenue aggregate app-wide (shift reconciliation,
        Staff Pouring League, Kibanda Produce Performance...) one at a time
        to add the exclusion — exactly the failure mode the Draw-type
        precedent already exists to avoid."""
        from django.db import transaction as _tx
        with _tx.atomic():
            transfer = StockTransfer.objects.select_for_update().get(pk=self.pk)
            if transfer.status != 'DRAFT':
                raise ValueError('Uhamisho huu tayari umeshughulikiwa.')
            for line in transfer.lines.select_related('item').all():
                Transaction.objects.create(
                    business=transfer.business, item=line.item, type='Transfer',
                    qty=-abs(line.qty_sent), transfer=transfer,
                    recorded_by=getattr(dispatched_by, 'user', None),
                )
            transfer.status = 'DISPATCHED'
            transfer.dispatched_by = dispatched_by
            transfer.dispatched_at = timezone.now()
            transfer.save(update_fields=['status', 'dispatched_by', 'dispatched_at'])
            self.status = transfer.status
            self.dispatched_by = transfer.dispatched_by
            self.dispatched_at = transfer.dispatched_at
            return transfer

    def receive_locked(self, lines_received, received_by=None):
        """DISPATCHED -> RECEIVED (or DISPUTED if any line arrived short).
        `lines_received` is {line_id: qty_received}. Creates one type=
        'Transfer' Transaction per line at to_store for the counted
        quantity — never the sent quantity, so a shortfall is never
        silently absorbed. Not type='Receipt' — same reasoning as
        dispatch_locked(): excluded by construction from every revenue
        query, and critically, Receipt-type transactions are Item.
        cost_price's ONE designed writer path (add_transaction's Receipt
        flow) elsewhere in this app — a transfer must never look like a
        real purchase and silently perturb the item's cost price. A short
        line's shortfall is left for the owner to explain via
        resolve_dispute_locked()."""
        from django.db import transaction as _tx
        with _tx.atomic():
            transfer = StockTransfer.objects.select_for_update().get(pk=self.pk)
            if transfer.status != 'DISPATCHED':
                raise ValueError('Uhamisho huu si tayari kupokelewa.')
            any_short = False
            shortfall_kes = Decimal('0')
            short_items = []
            for line in transfer.lines.select_related('item').all():
                qty_received = Decimal(str(lines_received.get(line.id, line.qty_sent)))
                line.qty_received = qty_received
                if qty_received < line.qty_sent:
                    any_short = True
                    missing = line.qty_sent - qty_received
                    shortfall_kes += missing * (line.item.cost_price or Decimal('0'))
                    short_items.append(f"{line.item.description} ({missing} {line.item.unit})")
                line.save(update_fields=['qty_received'])
                if qty_received > 0:
                    Transaction.objects.create(
                        business=transfer.business, item=line.item, type='Transfer',
                        qty=qty_received, transfer=transfer,
                        recorded_by=getattr(received_by, 'user', None),
                    )
            transfer.status = 'DISPUTED' if any_short else 'RECEIVED'
            transfer.received_by = received_by
            transfer.received_at = timezone.now()
            transfer.save(update_fields=['status', 'received_by', 'received_at'])
            self.status = transfer.status
            self.received_by = transfer.received_by
            self.received_at = transfer.received_at
            # UBA M3 §5.3 — a dispute is exactly the kind of ad-hoc alert
            # BusinessException exists to unify, and M2-AC1 ("owner got exactly
            # one notification") was never actually satisfied when the M2
            # sprint shipped model-layer-only with no view wired up yet. Fire
            # both here: the durable feed row (Maduka Yangu's exception feed)
            # and the one-time owner/manager notification, mirroring the
            # attribution rule in the spec ("against the dispatching shift, not
            # the receiver — the receiver is the whistle") as closely as this
            # app's data allows — StockTransfer has no shift FK of its own
            # (dispatch/receive are staff actions, not shift-scoped, unlike bar/
            # kitchen sales), so `staff` is set to the DISPATCHER, never the
            # receiving staffer who is only reporting the shortfall.
            if any_short:
                try:
                    detail = (
                        f"{transfer.reference}: {', '.join(short_items)} — "
                        f"{transfer.from_store.name} → {transfer.to_store.name}"
                    )
                    BusinessException.raise_exception(
                        business=transfer.business, kind='transfer_dispute', severity='warn',
                        title=f"Uhamisho {transfer.reference} una utata",
                        detail=detail, store=transfer.to_store,
                        staff=transfer.dispatched_by, amount_kes=shortfall_kes,
                        link_url='/maduka/',
                    )
                    from .notifications import normalize_ke_phone as _nkp, send_sms_notification_async as _ssms
                    from accounts.models import UserProfile as _UP
                    msg = (
                        f"⚠️ Uhamisho {transfer.reference} una utata — {detail}. "
                        f"Angalia dukamwecheche.co.ke/maduka/"
                    )
                    for _op in _UP.objects.filter(
                        business=transfer.business, role__in=['owner', 'manager']
                    ).select_related('user'):
                        Notification.objects.create(
                            user=_op.user, title='⚠️ Utata wa Uhamisho', message=msg,
                            notification_type='warning', link_url='/maduka/',
                        )
                        if _op.phone:
                            try:
                                _ssms(msg, _nkp(_op.phone))
                            except Exception:
                                pass
                except Exception:
                    pass
            return transfer

    def resolve_dispute_locked(self, resolved_by=None):
        """DISPUTED -> RECEIVED: the owner has looked into the shortfall and
        is writing it off — creates a Wastage Transaction at from_store for
        each short line's missing quantity (a real, now-explained loss),
        then closes the transfer as RECEIVED. Never auto-called — an owner
        decision every time, matching this app's own Rekebisha/write-off
        conventions elsewhere."""
        from django.db import transaction as _tx
        with _tx.atomic():
            transfer = StockTransfer.objects.select_for_update().get(pk=self.pk)
            if transfer.status != 'DISPUTED':
                raise ValueError('Uhamisho huu hauna utata wa kutatua.')
            for line in transfer.lines.select_related('item').all():
                qty_received = line.qty_received or Decimal('0')
                shortfall = line.qty_sent - qty_received
                if shortfall > 0:
                    Transaction.objects.create(
                        business=transfer.business, item=line.item, type='Wastage',
                        qty=-shortfall, transfer=transfer,
                        recorded_by=getattr(resolved_by, 'user', None),
                        invoice_no='[TRF-LOSS]',
                    )
            transfer.status = 'RECEIVED'
            transfer.save(update_fields=['status'])
            self.status = transfer.status
            return transfer

    def cancel_locked(self, cancelled_by=None):
        """Only while DRAFT or DISPATCHED, only by the dispatcher or the
        owner (permission check is the caller's responsibility, matching
        every other *_locked() method in this app — the model layer
        enforces STATE, the view layer enforces WHO). A DRAFT cancel has no
        Transactions to reverse (nothing moved yet). A DISPATCHED cancel
        creates a compensating Receipt at from_store for each line's full
        qty_sent — the goods never actually left, or came back — so the
        stock movement must reverse; this is the transfer's own inverse
        action, per CLAUDE.md's Cause-&-Effect protocol."""
        from django.db import transaction as _tx
        with _tx.atomic():
            transfer = StockTransfer.objects.select_for_update().get(pk=self.pk)
            if transfer.status not in ('DRAFT', 'DISPATCHED'):
                raise ValueError('Uhamisho huu hauwezi kufutwa katika hali hii.')
            if transfer.status == 'DISPATCHED':
                for line in transfer.lines.select_related('item').all():
                    Transaction.objects.create(
                        business=transfer.business, item=line.item, type='Transfer',
                        qty=abs(line.qty_sent), transfer=transfer,
                        recorded_by=getattr(cancelled_by, 'user', None),
                        invoice_no='[TRF-CANCEL]',
                    )
            transfer.status = 'CANCELLED'
            transfer.save(update_fields=['status'])
            self.status = transfer.status
            return transfer


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    qty_sent = models.DecimalField(max_digits=12, decimal_places=3)
    qty_received = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    variance_note = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.item.description} × {self.qty_sent} ({self.transfer.reference})"


class BusinessException(models.Model):
    """UBA §5.3 — the single owner-facing exception feed backing "Maduka Yangu"
    (core/maduka_views.py). Every accountability check in the app should write
    a row here instead of inventing its own ad-hoc alert shape, so the owner
    sees ONE prioritised, cross-store feed instead of scattered per-module
    banners. Deliberately ADDITIVE, not a replacement for the existing
    per-user Notification/SMS delivery mechanisms — a BusinessException is the
    durable, queryable audit-trail/feed record; Notification/SMS stay the
    per-user real-time delivery channel for the same underlying event. Two of
    them fire side-by-side at the same call site (see close_shift()'s keg/cash
    variance alerts and StockTransfer.receive_locked()'s dispute path) rather
    than one replacing the other.
    """
    KIND_CHOICES = [
        ('shrinkage', 'Upungufu wa Bidhaa'),
        ('cash_variance', 'Tofauti ya Fedha'),
        ('transfer_dispute', 'Utata wa Uhamisho'),
        ('below_cost', 'Bei Chini ya Gharama'),
        ('credit_blocked', 'Deni kwa Mteja Aliyezuiliwa'),
        ('no_sales', 'Hakuna Mauzo'),
        ('stock_count_variance', 'Tofauti ya Hesabu ya Stock'),
        ('till_not_counted', 'Till Haijahesabiwa'),
        ('other', 'Nyingine'),
    ]
    SEVERITY_CHOICES = [
        ('info', 'Taarifa'),
        ('warn', 'Onyo'),
        ('danger', 'Hatari'),
    ]
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='exceptions')
    store = models.ForeignKey(
        Store, on_delete=models.SET_NULL, null=True, blank=True, related_name='exceptions'
    )
    shift = models.ForeignKey(
        'Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='exceptions'
    )
    staff = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='exceptions_caused',
    )
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default='other')
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='info')
    amount_kes = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    link_url = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='exceptions_acknowledged',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"

    @classmethod
    def raise_exception(cls, business, kind, severity, title, detail='', store=None,
                         shift=None, staff=None, amount_kes=None, link_url=''):
        """The one way any accountability check in the app should record an
        exception — never instantiate BusinessException directly elsewhere,
        matching this file's own *_locked()/create_*() single-entry-point
        convention. Never raises — a feed-write failure must never block the
        real state change (shift close, transfer receive, etc.) it accompanies;
        callers should still wrap this in their own try/except per this app's
        established defensive-notification pattern, since a DB-level failure
        (rare, but possible under load) is still theoretically possible here.
        """
        return cls.objects.create(
            business=business, kind=kind, severity=severity, title=title, detail=detail,
            store=store, shift=shift, staff=staff, amount_kes=amount_kes, link_url=link_url,
        )

    def acknowledge(self, by):
        if self.acknowledged_at:
            return
        self.acknowledged_by = by
        self.acknowledged_at = timezone.now()
        self.save(update_fields=['acknowledged_by', 'acknowledged_at'])


# ── DJ / MC Performer Management ─────────────────────────────────────────────

class Performer(models.Model):
    TYPE_DJ   = 'DJ'
    TYPE_MC   = 'MC'
    TYPE_BOTH = 'BOTH'
    TYPE_CHOICES = [
        (TYPE_DJ,   _('DJ')),
        (TYPE_MC,   _('MC')),
        (TYPE_BOTH, _('DJ & MC')),
    ]

    CONTRACT_ONE_OFF  = 'ONE_OFF'
    CONTRACT_RETAINER = 'RETAINER'
    CONTRACT_CHOICES  = [
        (CONTRACT_ONE_OFF,  _('Per session (one-off)')),
        (CONTRACT_RETAINER, _('Monthly retainer')),
    ]

    business       = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='performers')
    name           = models.CharField(max_length=100)
    performer_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_DJ)
    phone          = models.CharField(max_length=20, blank=True)
    genre          = models.CharField(max_length=50, blank=True, help_text='e.g. Afrobeats, House, Gospel')
    contract_type  = models.CharField(max_length=10, choices=CONTRACT_CHOICES, default=CONTRACT_ONE_OFF)
    standard_rate  = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                         help_text='Per-session fee (ONE_OFF) or monthly rate (RETAINER)')
    is_active      = models.BooleanField(default=True)
    notes          = models.TextField(blank=True)
    photo_url      = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Public image URL — shown on promo page and roster',
    )
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_performer_type_display()})"

    def session_count(self):
        return self.sessions.exclude(status='CANCELLED').count()

    def avg_staff_rating(self):
        from django.db.models import Avg as _Avg
        result = self.sessions.filter(
            staff_rating__isnull=False
        ).aggregate(avg=_Avg('staff_rating'))['avg']
        return round(result, 1) if result else None

    def avg_customer_rating(self):
        from django.db.models import Avg as _Avg
        result = PerformerFeedback.objects.filter(
            session__performer=self
        ).aggregate(avg=_Avg('rating'))['avg']
        return round(result, 1) if result else None


class PerformerSession(models.Model):
    STATUS_SCHEDULED            = 'SCHEDULED'
    STATUS_PENDING_APPROVAL     = 'PENDING_APPROVAL'
    STATUS_PENDING_CONFIRMATION = 'PENDING_CONFIRMATION'
    STATUS_ACTIVE               = 'ACTIVE'
    STATUS_COMPLETED            = 'COMPLETED'
    STATUS_CANCELLED            = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_SCHEDULED,            _('Scheduled / upcoming')),
        (STATUS_PENDING_APPROVAL,     _('Pending owner approval')),
        (STATUS_PENDING_CONFIRMATION, _('Awaiting confirmation')),
        (STATUS_ACTIVE,               _('Active / in progress')),
        (STATUS_COMPLETED,            _('Completed')),
        (STATUS_CANCELLED,            _('Cancelled / no-show')),
    ]

    PAYMENT_PENDING = 'PENDING'
    PAYMENT_PAID    = 'PAID'
    PAYMENT_CHOICES = [
        (PAYMENT_PENDING, _('Unpaid')),
        (PAYMENT_PAID,    _('Paid')),
    ]

    business       = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='performer_sessions')
    performer      = models.ForeignKey(Performer, on_delete=models.SET_NULL, null=True, related_name='sessions')
    # Duo support: optional second performer (e.g. DJ + MC booked together)
    second_performer = models.ForeignKey(
        Performer, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='second_performer_sessions',
    )
    shift          = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='performer_sessions')
    date           = models.DateField()
    status         = models.CharField(max_length=22, choices=STATUS_CHOICES, default=STATUS_PENDING_CONFIRMATION)
    started_at     = models.DateTimeField(null=True, blank=True)
    ended_at       = models.DateTimeField(null=True, blank=True)
    agreed_fee           = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    second_performer_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Agreed fee for the second performer (duo sessions only)',
    )
    expected_hours = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Agreed session duration in hours — shown as accountability timer on home dashboard',
    )
    payment_status = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default=PAYMENT_PENDING)
    payment_method = models.CharField(max_length=10,
                                      choices=[('cash', _('Cash')), ('mpesa', _('M-Pesa'))],
                                      default='cash')
    paid_at        = models.DateTimeField(null=True, blank=True)
    expense        = models.ForeignKey('BusinessExpense', on_delete=models.SET_NULL, null=True, blank=True)
    staff_rating   = models.IntegerField(null=True, blank=True,
                                         choices=[(i, i) for i in range(1, 6)])
    staff_notes    = models.TextField(blank=True)

    # Primary performer self-check-in (public URL, no login)
    performer_checked_in = models.BooleanField(default=False)
    performer_checkin_at = models.DateTimeField(null=True, blank=True)
    performer_ended_at   = models.DateTimeField(null=True, blank=True)
    checkin_token        = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Second performer self-check-in (duo sessions)
    second_performer_checked_in  = models.BooleanField(default=False)
    second_performer_checkin_at  = models.DateTimeField(null=True, blank=True)
    second_performer_ended_at    = models.DateTimeField(null=True, blank=True)
    second_performer_checkin_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Staff duty confirmation (on-ground staff corroborates session has started)
    staff_confirmed    = models.BooleanField(default=False)
    staff_confirmed_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='dj_confirmations',
    )
    staff_confirmed_at = models.DateTimeField(null=True, blank=True)

    feedback_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    scheduled_start_time = models.TimeField(null=True, blank=True)
    notes          = models.TextField(blank=True)
    created_by     = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='performer_sessions_created')
    created_at     = models.DateTimeField(auto_now_add=True)

    # Cancellation trail (2026-07-24 wording/accountability audit) — cancel used to be a
    # bare status flip with no reason, no notification, no confirmation message at all.
    cancel_reason  = models.CharField(max_length=200, blank=True)
    cancelled_by   = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='performer_sessions_cancelled')
    cancelled_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-started_at']

    def __str__(self):
        name = self.performer.name if self.performer else 'Unknown'
        return f"{name} — {self.date}"

    @property
    def all_confirmed(self):
        """True when P1 has confirmed presence AND staff has corroborated.
        P2 check-in is tracked for accountability timestamps but does not gate
        session activation or payment — the DJ may be playing before MC arrives."""
        return self.performer_checked_in and self.staff_confirmed

    @property
    def performer_is_active(self):
        return self.status == self.STATUS_ACTIVE and self.performer_ended_at is None

    @property
    def second_performer_is_active(self):
        return (
            self.second_performer_id is not None
            and self.status == self.STATUS_ACTIVE
            and self.second_performer_ended_at is None
        )

    @property
    def duration_hours(self):
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds() / 3600, 1)
        return None

    @property
    def duration_hours_p1(self):
        if not self.started_at:
            return None
        end = self.performer_ended_at or self.ended_at or timezone.now()
        return round((end - self.started_at).total_seconds() / 3600, 1)

    @property
    def duration_hours_p2(self):
        if not self.second_performer_id or not self.started_at:
            return None
        end = self.second_performer_ended_at or self.ended_at or timezone.now()
        return round((end - self.started_at).total_seconds() / 3600, 1)

    @property
    def avg_customer_rating(self):
        from django.db.models import Avg as _Avg
        result = self.customer_feedback.aggregate(avg=_Avg('rating'))['avg']
        return round(result, 1) if result else None

    @property
    def total_customer_ratings(self):
        return self.customer_feedback.count()

    @property
    def checkin_short_code(self):
        return str(self.checkin_token).replace('-', '')[:6].upper()

    @property
    def second_performer_checkin_short_code(self):
        return str(self.second_performer_checkin_token).replace('-', '')[:6].upper()


class PerformerFeedback(models.Model):
    """Customer rating submitted via QR code — no login required."""
    session      = models.ForeignKey(PerformerSession, on_delete=models.CASCADE, related_name='customer_feedback')
    rating       = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    comment      = models.TextField(blank=True, max_length=500)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"⭐{self.rating} — {self.session}"


# ── Promo / Broadcast Module ───────────────────────────────────────────────────

class PromoMessage(models.Model):
    """A promotional broadcast sent to a segment of the business's customer database."""

    SEGMENT_ALL      = 'all'
    SEGMENT_DEBTORS  = 'debtors'
    SEGMENT_TAB      = 'tab_customers'
    SEGMENT_REGULARS = 'regulars'
    SEGMENT_BIRTHDAY = 'birthday'
    SEGMENT_CUSTOM   = 'custom'
    SEGMENT_CHOICES = [
        (SEGMENT_ALL,      'Wateja Wote'),
        (SEGMENT_DEBTORS,  'Wadeni Tu'),
        (SEGMENT_TAB,      'Wateja wa Tab'),
        (SEGMENT_REGULARS, 'Wateja wa Kawaida (waliokuja ≥3×)'),
        (SEGMENT_BIRTHDAY, 'Siku ya Kuzaliwa (wiki hii)'),
        (SEGMENT_CUSTOM,   'Nambari Maalum'),
    ]

    CHANNEL_SMS    = 'sms'
    CHANNEL_INAPP  = 'in_app'
    CHANNEL_BOTH   = 'both'
    CHANNEL_CHOICES = [
        (CHANNEL_SMS,   'SMS tu'),
        (CHANNEL_INAPP, 'In-App tu'),
        (CHANNEL_BOTH,  'SMS + In-App'),
    ]

    business        = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='promo_messages')
    sent_by         = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    subject         = models.CharField(max_length=120, blank=True, help_text='Short internal label for this promo (not sent to customer).')
    message         = models.TextField(help_text='The text sent to customers.')
    segment         = models.CharField(max_length=20, choices=SEGMENT_CHOICES, default=SEGMENT_ALL)
    custom_phones   = models.TextField(blank=True, help_text='Comma-separated phone numbers for SEGMENT_CUSTOM.')
    channel         = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default=CHANNEL_SMS)
    recipient_count = models.PositiveIntegerField(default=0)
    sent_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.subject or self.segment} — {self.sent_at.date()} ({self.recipient_count} wateja)"


# ── Restock Request Module ─────────────────────────────────────────────────────

class StockRequest(models.Model):
    """
    Staff raises a StockRequest when they notice an item is empty.
    The request notifies the owner via SMS + in-app. When any Receipt transaction
    is later recorded for the same item, the request is auto-resolved and the owner
    receives a "stock received" confirmation.
    """
    STATUS_PENDING  = 'pending'
    STATUS_ORDERED  = 'ordered'
    STATUS_RECEIVED = 'received'
    STATUS_CHOICES  = [
        ('pending',  'Inasubiri'),
        ('ordered',  'Imeagizwa'),
        ('received', 'Imepokelewa'),
    ]

    business     = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='stock_requests')
    item         = models.ForeignKey('Item', on_delete=models.CASCADE, related_name='stock_requests')
    requested_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='restock_requests')
    note         = models.CharField(max_length=200, blank=True)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    received_at  = models.DateTimeField(null=True, blank=True)
    received_by  = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='restock_received')
    received_qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    resolved_txn = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.item.description} — {self.get_status_display()} ({self.requested_at.date()})"


# ── Guided Stock Reconciliation ────────────────────────────────────────────────

class StockTake(models.Model):
    """Header for one stock-count session (standalone, optionally linked to a shift)."""
    STATUS_SUBMITTED  = 'submitted'
    STATUS_RECONCILED = 'reconciled'
    STATUS_CHOICES = [
        ('submitted',  'Submitted'),
        ('reconciled', 'Reconciled'),
    ]

    business     = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='stock_takes')
    store        = models.ForeignKey('Store', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_takes')
    conducted_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='stock_takes_conducted')
    shift        = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_takes')
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='submitted')
    taken_at     = models.DateTimeField(auto_now_add=True)
    notes        = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-taken_at']

    def __str__(self):
        name = self.conducted_by.get_full_name() if self.conducted_by else '?'
        return f"Stock Take by {name} on {self.taken_at.date()}"


class StockVarianceQuery(models.Model):
    """
    One row per item with a non-zero variance from a StockTake session.
    Holds the full accountability lifecycle: detection → staff response → owner review.
    """
    DECREASE = 'decrease'   # actual < book — likely unrecorded sale
    INCREASE = 'increase'   # actual > book — likely unrecorded receipt
    DIRECTION_CHOICES = [('decrease', 'Decrease'), ('increase', 'Increase')]

    PENDING   = 'pending'
    RESPONDED = 'responded'
    RESOLVED  = 'resolved'
    STATUS_CHOICES = [
        ('pending',   'Pending Staff Response'),
        ('responded', 'Staff Responded'),
        ('resolved',  'Resolved'),
    ]

    RESP_CASH        = 'cash'
    RESP_MPESA       = 'mpesa'
    RESP_CREDIT      = 'credit'
    RESP_RECEIPT     = 'receipt'
    RESP_NO_INTERNET = 'no_internet'
    RESP_UNKNOWN     = 'unknown'
    RESPONSE_CHOICES = [
        ('cash',        'Cash sale'),
        ('mpesa',       'M-Pesa sale'),
        ('credit',      'Credit / Deni sale'),
        ('receipt',     'Unrecorded receipt'),
        ('no_internet', 'No internet at the time'),
        ('unknown',     'Unknown'),
    ]

    stock_take        = models.ForeignKey(StockTake, on_delete=models.CASCADE, related_name='variances')
    item              = models.ForeignKey('Item', on_delete=models.SET_NULL, null=True, related_name='variance_queries')
    item_name_cache   = models.CharField(max_length=200)
    book_balance      = models.DecimalField(max_digits=12, decimal_places=3)
    actual_count      = models.DecimalField(max_digits=12, decimal_places=3)
    direction         = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    estimated_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    queried_staff     = models.ForeignKey('accounts.UserProfile', on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='variance_queries')
    # 2026-07-25: WHICH shift is believed accountable, separate from queried_staff
    # (who is asked to explain — now always the attributed shift's own staff, not
    # just "whoever's shift happened to be open when the stock take was run"). Set
    # by shift_views.attribute_variance_shift() at creation time. None when no shift
    # context exists (owner/manager stock take with no linked shift) or when no
    # prior shift could be identified either (e.g. an overnight/unattended gap).
    attributed_shift  = models.ForeignKey('Shift', on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='attributed_variance_queries')
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    response_type     = models.CharField(max_length=20, choices=RESPONSE_CHOICES, blank=True)
    response_customer = models.CharField(max_length=100, blank=True)
    response_note     = models.CharField(max_length=300, blank=True)
    responded_at      = models.DateTimeField(null=True, blank=True)

    owner_accepted    = models.BooleanField(null=True)
    owner_action_by   = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                           null=True, blank=True, related_name='variance_reviews')
    owner_acted_at    = models.DateTimeField(null=True, blank=True)
    # 2026-07-25 (reason-chips redesign): the owner's OWN note at review time —
    # distinct from response_note (the staffer's explanation). Previously only
    # baked into the corrective Transaction.recipient on 'accept' (and lost
    # entirely on 'dismiss', which had no note capture in either JS or backend).
    owner_note        = models.CharField(max_length=300, blank=True)

    corrective_txn    = models.ForeignKey('Transaction', on_delete=models.SET_NULL,
                                           null=True, blank=True, related_name='variance_correction')
    compliance_noted  = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def variance(self):
        return self.actual_count - self.book_balance

    def __str__(self):
        return f"{self.item_name_cache} ({self.direction}: {abs(self.variance)}) — {self.status}"


class SalesResetLog(models.Model):
    """Audit trail for the owner-triggered 'Reset Sales & Analytics' action —
    mirrors accounts.AccountDeletionLog's pattern of recording the event
    BEFORE the destructive action runs, but scoped to a business (not an
    account) since this wipes sales/analytics history while keeping the
    business, staff, and item catalog intact."""
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='sales_reset_logs'
    )
    business_name_cache = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, related_name='sales_resets_performed'
    )
    performed_by_username_cache = models.CharField(max_length=150, blank=True)
    reason = models.TextField(blank=True)
    counts_snapshot = models.JSONField(default=dict, help_text='Per-model row counts captured immediately before delete')
    backup_filename = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business_name_cache} reset by {self.performed_by_username_cache} — {self.created_at:%Y-%m-%d}"


class CatalogUploadBatch(models.Model):
    """Job/audit header for one supplier price-list upload — business-scoped
    and owner-facing, distinct from the internal admin-only ImportJob used
    by import_products.py/import_taxonomy.py (which isn't business-scoped).
    Uses the shared core.catalog_classify engine, same as the one-time
    BAR_CATALOG enrichment (enrich_liquor_catalog management command)."""
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='catalog_upload_batches'
    )
    uploaded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, related_name='catalog_uploads'
    )
    original_filename = models.CharField(max_length=255, blank=True)
    rows_total = models.IntegerField(default=0)
    rows_parsed = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    skipped_examples = models.JSONField(default=list, help_text='Capped sample of unparseable raw row text')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business.name} — {self.original_filename} ({self.rows_parsed}/{self.rows_total})"


class SupplierCatalogEntry(models.Model):
    """One parsed catalog entry from a business's own uploaded supplier
    price list — coexists with the static business_profiles.py catalog;
    the 'Add from Catalogue' bulk-add screen merges both. Schema mirrors
    the static catalog's dict shape (name/unit/volume_ml/category/
    cost_price/presets) so both sources render identically in the UI."""
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='supplier_catalog_entries'
    )
    source_upload = models.ForeignKey(
        CatalogUploadBatch, on_delete=models.CASCADE, null=True, blank=True, related_name='entries'
    )
    name = models.CharField(max_length=200)
    raw_name = models.CharField(max_length=200, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    volume_ml = models.PositiveIntegerField(null=True, blank=True)
    category = models.CharField(max_length=30, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    default_reorder_level = models.IntegerField(default=0)
    default_reorder_quantity = models.IntegerField(default=0)
    presets_json = models.JSONField(default=list, help_text='Same shape as the static catalog entries’ "presets" key')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.business.name} — {self.name}"


class SupplierCatalogEntryPriceLog(models.Model):
    """One observed price point for a SupplierCatalogEntry, recorded whenever
    a re-upload changes its cost_price (catalog_upload_process overwrites
    the entry's own cost_price in place, so without this the previous value
    would just be gone — no way to see "this went from KES 800 to 950").

    Also carries the resolve workflow for the price-variance report
    (catalog_upload_batch_detail): a detected change is a CAUSE that needs
    an EFFECT — the owner must either Apply (push the new price onto the
    live Item(s) this catalogue entry represents) or Dismiss (acknowledge,
    keep the item's recorded cost as-is). Tracking that state directly on
    the log row that represents the event — rather than a separate join
    table — mirrors how WriteOffRequest carries its own review state."""
    entry = models.ForeignKey(
        SupplierCatalogEntry, on_delete=models.CASCADE, related_name='price_logs'
    )
    business = models.ForeignKey(
        'accounts.Business', on_delete=models.CASCADE, related_name='catalog_price_logs'
    )
    source_upload = models.ForeignKey(
        CatalogUploadBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='price_changes',
    )
    previous_cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    applied = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    dismissed = models.BooleanField(default=False)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.entry.name}: {self.previous_cost_price} → {self.cost_price}"

    @property
    def is_resolved(self):
        return self.applied or self.dismissed

    @property
    def delta_pct(self):
        if not self.previous_cost_price or self.cost_price is None:
            return None
        try:
            return round(
                (float(self.cost_price) - float(self.previous_cost_price))
                / float(self.previous_cost_price) * 100, 1
            )
        except ZeroDivisionError:
            return None


# ────────────────────────────────────────────────
# STAFF ↔ OWNER STRUCTURED REQUESTS (item 5, 2026-07-26)
# ────────────────────────────────────────────────

class StaffRequest(models.Model):
    """The Maombi ↔ Maagizo channel — two directions sharing one model and
    one review lifecycle, deliberately not two separate models (2026-07-30
    redesign, folding in the original 2026-07-26 staff→owner-only version):

      direction='request'     — staff asks the owner something (unchanged
                                 from the original design): permission
                                 overrides, corrections, a plain question,
                                 or a stock-receipt confirmation.
      direction='instruction' — the owner directs a staff member (or all
                                 staff, if unassigned) to DO something
                                 concrete: run a stock take, receive a
                                 delivery, confirm the count on a specific
                                 item. Still not a generic FK to every model
                                 (restock/write-off/variance keep their own
                                 dedicated flows) — this is for tasking real
                                 people with real, boundable actions.

    Reuses status/reviewed_by/reviewed_at/review_note for BOTH directions
    rather than adding a parallel set of fields — 'approved' means
    "granted" for a request and "done" for an instruction; 'rejected'
    means "declined" for a request and "cancelled" for an instruction.
    Only the WHO-can-transition-it and the on-screen label differ by
    direction (see review_staff_request / staff_requests.html) — the
    underlying state machine is identical, so one shared undo-friendly
    lifecycle serves both without duplicating it.

    Cause-and-effect wiring: every task_type maps to a real, already-built
    screen in the app via action_url() below — an instruction is never a
    disconnected to-do note. See that method's docstring for the mapping.
    """
    CATEGORY_RESTOCK       = 'restock'
    CATEGORY_PERMISSION    = 'permission'
    CATEGORY_CORRECTION    = 'correction'
    CATEGORY_STOCK_CONFIRM = 'stock_confirm'
    CATEGORY_GENERAL       = 'general'
    CATEGORY_CHOICES = [
        ('restock',       _('Ombi la Stock')),
        ('permission',    _('Ruhusa Maalum')),
        ('correction',    _('Marekebisho')),
        ('stock_confirm', _('Uthibitisho wa Oda')),
        ('general',       _('Jambo Lingine')),
    ]

    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        ('pending',  _('Inasubiri')),
        ('approved', _('Imeidhinishwa')),
        ('rejected', _('Imekataliwa')),
    ]

    DIRECTION_REQUEST     = 'request'
    DIRECTION_INSTRUCTION = 'instruction'
    DIRECTION_CHOICES = [
        ('request',     _('Ombi — kutoka kwa mfanyakazi')),
        ('instruction', _('Agizo — kutoka kwa mmiliki')),
    ]

    TASK_GENERAL       = 'general'
    TASK_STOCK_TAKE    = 'stock_take'
    TASK_RECEIVE_GOODS = 'receive_goods'
    TASK_CONFIRM_COUNT = 'confirm_count'
    TASK_TYPE_CHOICES = [
        ('general',       _('Maelezo / Kazi Nyingine')),
        ('stock_take',    _('Fanya Hesabu ya Stock')),
        ('receive_goods', _('Pokea Bidhaa')),
        ('confirm_count', _('Thibitisha Idadi ya Bidhaa')),
    ]

    business     = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='staff_requests')
    requested_by = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='staff_requests_made')
    category     = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='general')
    subject      = models.CharField(max_length=150)
    description  = models.TextField(blank=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by  = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_requests_reviewed')
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    review_note  = models.CharField(max_length=300, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    # 2026-07-26 (live follow-up) — the ONE deliberate exception to "no generic
    # FK": a stock-receipt confirmation is inherently about a specific Receipt
    # Transaction (owner ordered stock remotely, isn't present to witness
    # delivery — a second person, present staff or manager, confirms the
    # recorded receipt is accurate). Reusing this same channel/review flow
    # rather than building a parallel model for one extra field.
    related_transaction = models.ForeignKey(
        'Transaction', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_requests',
    )

    # ── 2026-07-30 redesign: owner-issued instructions ──────────────────────
    direction = models.CharField(max_length=12, choices=DIRECTION_CHOICES, default='request')
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES, default='general')
    assigned_to = models.ForeignKey(
        'accounts.UserProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_requests_assigned',
        help_text='Instruction only. Blank = every staff member sees it and any one of them may complete it (a broadcast task, e.g. "everyone do a stock count today").',
    )
    related_item = models.ForeignKey(
        'Item', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_requests',
        help_text='For confirm_count / receive_goods instructions about a specific item.',
    )
    due_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Staff Request')
        verbose_name_plural = _('Staff Requests')

    def __str__(self):
        return f"[{self.get_category_display()}] {self.subject} — {self.requested_by} ({self.status})"

    def action_url(self):
        """The real, already-built screen this instruction's task_type maps
        to — cause and effect, not a floating to-do note. Empty string for
        task_type='general' (nothing to deep-link; just acknowledge it) or
        for a plain request (direction='request' has no action of its own —
        it's the OWNER who acts, via review_staff_request)."""
        if self.direction != self.DIRECTION_INSTRUCTION:
            return ''
        if self.task_type == self.TASK_CONFIRM_COUNT and self.related_item_id:
            return f'/stock/?adjust_item={self.related_item_id}'
        if self.task_type == self.TASK_RECEIVE_GOODS:
            return f'/add-transaction/?item={self.related_item_id}' if self.related_item_id else '/add-transaction/'
        if self.task_type == self.TASK_STOCK_TAKE:
            profile = self.assigned_to
            if profile and getattr(profile, 'is_kitchen_staff', False):
                return '/kitchen/'
            if getattr(self.business, 'has_keg', False):
                return '/bar/'
            return '/stock/'
        return ''

    def action_label(self):
        return {
            self.TASK_CONFIRM_COUNT: _('🔢 Thibitisha Sasa'),
            self.TASK_RECEIVE_GOODS: _('📦 Pokea Sasa'),
            self.TASK_STOCK_TAKE:    _('📊 Fanya Hesabu Sasa'),
        }.get(self.task_type, '')
