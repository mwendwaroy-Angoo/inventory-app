from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import BusinessType, County, SubCounty, Ward  # updated
from django.contrib.auth.signals import user_logged_in, user_logged_out
import math
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Business(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_businesses', null=True, blank=True)
    name = models.CharField(max_length=255, unique=True)
    business_type = models.ForeignKey(BusinessType, on_delete=models.PROTECT, null=True, blank=True)
    # Optional curated categories for this business. If set, item category choices will be restricted to these.
    categories = models.ManyToManyField('core.Category', blank=True, related_name='businesses')
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True)
    sub_county = models.ForeignKey(SubCounty, on_delete=models.PROTECT, null=True, blank=True)  # renamed
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True)  # new
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Operating Hours ──
    opening_time = models.TimeField(null=True, blank=True, help_text='e.g. 08:00')
    closing_time = models.TimeField(null=True, blank=True, help_text='e.g. 18:00')
    is_open_override = models.BooleanField(null=True, blank=True, default=None,
        help_text='Manual override: True=force open, False=force closed, None=use hours')

    # ── GPS Coordinates ──
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # ── Delivery Settings ──
    offers_delivery = models.BooleanField(default=False)
    delivery_radius_km = models.DecimalField(max_digits=5, decimal_places=1, default=5, help_text='Max delivery distance in km')
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base delivery fee in KES')
    delivery_fee_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional fee per km')
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base minimum order in KES')
    min_order_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional minimum per km of distance')

    # ── Payment Receiving Settings ──
    PAYMENT_CHANNEL_CHOICES = [
        ('till', _('Till Number (Buy Goods)')),
        ('paybill', _('Paybill')),
        ('pochi', _('Pochi la Biashara')),
        ('phone', _('Personal M-Pesa')),
    ]
    mpesa_till = models.CharField(max_length=20, blank=True, help_text='Lipa Na M-Pesa Till Number (Buy Goods)')
    mpesa_paybill = models.CharField(max_length=20, blank=True, help_text='Paybill Business Number')
    mpesa_paybill_account = models.CharField(max_length=50, blank=True, help_text='Paybill Account Number')
    mpesa_pochi = models.CharField(max_length=20, blank=True, help_text='Pochi la Biashara phone number')
    mpesa_phone = models.CharField(max_length=20, blank=True, help_text='Personal M-Pesa phone number for receiving')
    preferred_payment_channel = models.CharField(
        max_length=10, choices=PAYMENT_CHANNEL_CHOICES, blank=True,
        help_text='Default payment channel customers should use')

    # ── Daraja API credentials (per-business, for C2B URL registration) ──
    daraja_consumer_key = models.CharField(
        max_length=200, blank=True,
        help_text='Safaricom Daraja API Consumer Key for this business shortcode')
    daraja_consumer_secret = models.CharField(
        max_length=200, blank=True,
        help_text='Safaricom Daraja API Consumer Secret for this business shortcode')
    daraja_passkey = models.CharField(
        max_length=200, blank=True,
        help_text='Safaricom Daraja Passkey for STK Push (issued at Daraja go-live)')
    daraja_c2b_registered = models.BooleanField(
        default=False,
        help_text='True once C2B confirmation URL has been registered with Safaricom')
    daraja_environment = models.CharField(
        max_length=20,
        choices=[('sandbox', 'Sandbox'), ('production', 'Production')],
        default='sandbox',
        help_text='Select Production only after Safaricom has approved your go-live request for this shortcode.',
    )

    # ── Pre-App Business History ──
    pre_app_cumulative_profit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        blank=True,
        help_text='Total profit earned before starting to use Duka Mwecheche. '
                  'Used to give accurate break-even calculations for existing businesses.'
    )
    business_start_date = models.DateField(
        null=True,
        blank=True,
        help_text='When did this business first open? '
                  'Used to show the full break-even timeline including pre-app history.'
    )

    # ── Credit Settings & Discipline Policy (K3.C) ──────────────────────────
    credit_window_days = models.PositiveIntegerField(
        default=30,
        help_text='Maximum days a customer debt may remain outstanding before it is flagged as overdue.',
    )
    credit_policy_enabled = models.BooleanField(
        default=True,
        help_text='Enforce the credit discipline gate at every issuance point.',
    )
    debt_cycle = models.CharField(
        max_length=10,
        choices=[('rolling', 'Rolling'), ('monthly', 'Monthly')],
        default='rolling',
        help_text='Rolling = always-on window. Monthly = reset at month-end.',
    )
    debt_cutoff_days_before_month_end = models.PositiveIntegerField(
        default=5,
        help_text='Monthly cycle only: block new credit in the last N days of the month.',
    )
    block_if_overdue = models.BooleanField(
        default=True,
        help_text='Block new credit while the customer has any debt overdue past the window.',
    )
    overdue_grace_days = models.PositiveIntegerField(
        default=0,
        help_text='Extra days beyond the credit window before a debt is treated as blocking.',
    )
    late_repayment_strikes = models.PositiveIntegerField(
        default=3,
        help_text='Block after this many significantly-late repayments.',
    )
    late_threshold_days = models.PositiveIntegerField(
        default=7,
        help_text='A repayment is "significantly late" if it lands this many days past the credit window.',
    )
    defaulter_permanent = models.BooleanField(
        default=False,
        help_text='Permanently block customers whose debt was written off as bad debt.',
    )
    cooldown_days = models.PositiveIntegerField(
        default=14,
        help_text='Clean days required after clearing all debt before credit resumes (for repeat late-payers).',
    )
    default_credit_limit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Business-wide default max outstanding credit (KES) applied to every customer '
                   'who has no limit of their own set on their profile. Blank = no default limit '
                   '(a customer with no per-customer limit either can borrow without a KES cap, '
                   'subject to the other credit policy gates). A per-customer limit on Customer.'
                   'credit_limit always overrides this default for that one customer.',
    )
    debt_erase_requires_approval = models.BooleanField(
        default=False,
        help_text="2026-07-31 — for a genuinely mistaken debt-section entry (wrong item/"
                  "customer, not a real uncollectable debt), staff can mark it 'Ilikuwa "
                  "Kosa' to restore stock the same way removing a tab entry already does. "
                  "Default False = self-service (any staff with an open shift can execute "
                  "it immediately, matching the tab-side Futa behavior). True = the same "
                  "request goes through owner/manager approval first (see UserProfile."
                  "can_approve_debt_erase) before stock is restored — an owner-activated "
                  "opt-in for tighter oversight, not the default.",
    )

    last_txn_sms_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp of last transaction SMS sent. Used for 10-minute bundling window.'
    )
    last_daily_summary_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp of the last daily summary SMS/email sent. Prevents a duplicate '
                   'cron fire or retry from re-sending the same day\'s summary.'
    )

    # ── UBA §3 decision #7 — dormant pricing-tier hook, gates nothing yet ──
    PLAN_CHOICES = [
        ('standard', 'Standard'),
        ('multi', 'Multi-Store'),
        ('pro', 'Pro'),
    ]
    plan = models.CharField(
        max_length=10, default='standard', choices=PLAN_CHOICES,
        help_text='UBA pricing tier. Dormant hook only per the execution order — '
                  'nothing in the app currently reads or gates on this field.'
    )

    # ── UBA §7.2 / §3 decision #3 — GlobalProduct / MarketPriceIndex privacy ──
    contribute_market_data = models.BooleanField(
        default=True,
        help_text='Share product NAMES, barcodes and pack sizes (never prices) into the '
                  'cross-tenant GlobalProduct dictionary when adding a new item by barcode. '
                  'On by default — opt out any time; never shares this business\'s prices '
                  'either way.'
    )
    contribute_price_data = models.BooleanField(
        default=False,
        help_text='Opt-IN only. Lets this business\'s cost/selling prices feed the county-level '
                  'MarketPriceIndex benchmark (median only, sample_size>=5, never a single '
                  'business\'s figure) — and, in exchange, lets this business SEE that '
                  'benchmark. Opting out loses the benchmark, not just the contribution.'
    )

    # ── Keg Bar Settings ──────────────────────────────────────────────────
    keg_variance_tolerance_pct = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal('3.0'),
        help_text='Allowed % gap between weight-implied revenue and recorded keg sales before a shift is flagged.'
    )
    keg_default_gross_kg = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('60.00')
    )
    keg_default_tare_kg = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('10.00')
    )
    keg_revenue_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('1.50'),
        help_text='Suggested barrel target = cost × this. 5000 × 1.5 = 7500, matching common owner targets.'
    )
    keg_alerts_enabled = models.BooleanField(
        default=True,
        help_text='Send in-app + SMS alerts when keg variance crosses the danger threshold.'
    )
    keg_alert_min_litres = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal('5.0'),
        help_text='Minimum litres dispensed before a SPOT variance alert fires (prevents false alarms on tiny volumes).'
    )
    keg_loss_baseline_pct = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        help_text='Cached average loss % learned from fully-depleted barrels with weight readings.'
    )
    keg_loss_baseline_sample = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Number of depleted barrels in the current baseline sample.'
    )
    weighs_kegs = models.BooleanField(
        default=False,
        help_text='Bar has a scale. Enables weight-based auto-depletion and light-at-tap theft detection. '
                  'Without weighing the app tracks recorded sales + envelope only and cannot detect fully off-book theft.'
    )
    block_sales_past_target = models.BooleanField(
        default=False,
        help_text='Block all sales once a barrel hits its revenue target. '
                  'Default off — staff are prompted to close or continue knowingly instead.'
    )
    cups_per_pint = models.PositiveIntegerField(
        default=0,
        help_text='How many 300ml cups are used per pint — whether to measure the pour, '
                  'serve the customer, or both. 0 = served in a glass or mug (no cups used).'
    )
    cups_per_jug  = models.PositiveIntegerField(
        default=6,
        help_text='How many 300ml cups are used per jug — whether to measure the pour '
                  '(e.g. 3 cups = 900ml) or serve customers at the table. '
                  'Set to 3 if you fill 3 cups to measure before pouring a jug.'
    )
    cup_low_notified_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Last time a cup low-stock alert was fired. Reset when new cups are logged.'
    )

    # ── KRA / eTIMS ──────────────────────────────────────────────────────────
    kra_pin = models.CharField(
        max_length=20, blank=True, default='',
        help_text='KRA PIN for eTIMS invoice submission (e.g. P051234567M). Leave blank until eTIMS integration is live.'
    )

    # ── Kitchen / Grill Side Venture ─────────────────────────────────────────
    has_kitchen = models.BooleanField(
        default=False,
        help_text='Enable the Kitchen / Grill module. Auto-creates a Kitchen store on first enable.'
    )

    # ── Haki (Staff Fairness) module ─────────────────────────────────────────
    haki_enabled = models.BooleanField(
        default=True,
        help_text='Enable the Haki staff contribution + salary transparency module.',
    )

    # ── Recurring Expenses ────────────────────────────────────────────────────
    last_expense_review_date = models.DateField(
        null=True, blank=True,
        help_text='Date owner last reviewed and confirmed recurring expenses.'
    )

    # ── DJ / MC Performer Module ──────────────────────────────────────────────
    event_sms_enabled = models.BooleanField(
        default=False,
        help_text='SMS the owner when a DJ/MC session is started by counter staff.'
    )
    performer_approval_threshold = models.PositiveIntegerField(
        default=0,
        help_text='Sessions with agreed_fee >= this value require owner approval before going ACTIVE. '
                  '0 = disabled (all sessions start immediately).'
    )

    def __str__(self):
        return self.name

    def is_open(self):
        """Check if business is currently open based on Nairobi time."""
        if self.is_open_override is not None:
            return self.is_open_override
        if not self.opening_time or not self.closing_time:
            return True  # No hours set = always open
        now = timezone.localtime(timezone.now()).time()
        if self.opening_time <= self.closing_time:
            return self.opening_time <= now <= self.closing_time
        else:
            # Overnight hours (e.g. 22:00 - 06:00)
            return now >= self.opening_time or now <= self.closing_time

    def distance_to(self, lat, lng):
        """Haversine distance in km to a given lat/lng."""
        if not self.latitude or not self.longitude or not lat or not lng:
            return None
        R = 6371  # Earth radius in km
        lat1, lon1 = math.radians(float(self.latitude)), math.radians(float(self.longitude))
        lat2, lon2 = math.radians(float(lat)), math.radians(float(lng))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def calc_delivery_fee(self, distance_km):
        """Calculate delivery fee based on distance."""
        return float(self.delivery_fee or 0) + float(self.delivery_fee_per_km or 0) * distance_km

    def calc_min_order(self, distance_km):
        """Calculate minimum order amount based on distance."""
        return float(self.min_order_amount or 0) + float(self.min_order_per_km or 0) * distance_km

    def recommend_delivery_tier(self, distance_km):
        """Return the best delivery tier for the given distance, or None."""
        tiers = self.delivery_tiers.filter(
            max_distance_km__gte=distance_km
        ).order_by('max_distance_km')
        return tiers.first()


class DeliveryTier(models.Model):
    MODE_CHOICES = [
        ('foot', _('🚶 On Foot')),
        ('bicycle', _('🚲 Bicycle')),
        ('boda', _('🏍️ Boda Boda')),
        ('vehicle', _('🚗 Vehicle')),
    ]
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='delivery_tiers')
    mode = models.CharField(max_length=15, choices=MODE_CHOICES)
    max_distance_km = models.DecimalField(max_digits=5, decimal_places=1, help_text='Max range for this mode in km')
    base_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base fee for this mode')
    fee_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional fee per km')

    class Meta:
        ordering = ['max_distance_km']
        unique_together = ['business', 'mode']

    def __str__(self):
        return f"{self.get_mode_display()} — up to {self.max_distance_km}km"

    def calc_fee(self, distance_km):
        return float(self.base_fee) + float(self.fee_per_km) * distance_km


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('owner',    _('Owner')),
        ('manager',  _('Manager')),
        ('staff',    _('Staff')),
        ('waitress', _('Waitress / Waiter')),
        ('kitchen',  _('Kitchen / Grill Staff')),
        ('rider',    _('Rider')),
        ('supplier', _('Supplier')),
    ]
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('sw', 'Kiswahili'),
        ('ki', 'Gĩkũyũ'),
        ('luo', 'Dholuo'),
        ('kln', 'Kalenjin'),
        ('kam', 'Kĩkamba'),
        ('luy', 'Luhya'),
        ('guz', 'Ekegusii'),
        ('mer', 'Kĩmĩrũ'),
        ('mas', 'Maa (Maasai)'),
        ('tuv', "Ng'aturkana"),
        ('so', 'Soomaali'),
        ('dav', 'Kitaita'),
        ('pko', 'Pokot'),
        ('teo', 'Ateso'),
        ('saq', 'Samburu'),
        ('ebu', 'Kĩembu'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business = models.ForeignKey(Business, on_delete=models.CASCADE,
                                  related_name='users', null=True, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    phone = models.CharField(max_length=20, blank=True)
    has_seen_tutorial = models.BooleanField(default=False)
    onboarding_sections_seen = models.JSONField(
        default=list,
        blank=True,
        help_text='List of section IDs whose product tour has been seen. e.g. ["dashboard", "stores", "items"]'
    )
    preferred_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default='en')

    # ── Staff Permissions ──────────────────────────────────────────────
    can_input_cost_price = models.BooleanField(
        default=False,
        help_text='Staff can input cost price when receiving goods. They see the input field but never the previous cost price.'
    )
    can_override_restrictions = models.BooleanField(
        default=False,
        help_text='Staff can sell restricted items without triggering an owner approval request.'
    )
    can_access_kitchen = models.BooleanField(
        default=False,
        help_text='Bar/general staff may access the Kitchen Board. Off by default — grant explicitly.'
    )
    can_access_bar = models.BooleanField(
        default=False,
        help_text='Kitchen staff may access the Bar Board. Grant if they also serve bar customers.'
    )
    kitchen_requires_shift = models.BooleanField(
        default=False,
        help_text='Kitchen staff must open a shift before they can work. Off by default — kitchen staff normally bypass shift enforcement.'
    )
    can_receive_kitchen_stock = models.BooleanField(
        default=False,
        help_text='Kitchen staff may receive stock (Pata Stok) on the kitchen board. Off by default — grant explicitly when owner trusts staff with stock intake.'
    )
    can_receive_stock = models.BooleanField(
        default=True,
        help_text=(
            'Staff may record a Receipt (stock-in) via Add Transaction — the bar/general '
            "stock intake flow, and Quick Sell's own \"+ Pata Stok\" shortcut, which share "
            'this one gate. On by default (matches how every business has always worked); '
            'revoke for a specific staff member the owner does not trust to log deliveries '
            'accurately. Owner and manager are always exempt from this gate. Distinct from '
            "can_receive_kitchen_stock, which is the kitchen board's own separate toggle."
        ),
    )

    can_authorize_tab_accumulation = models.BooleanField(
        default=False,
        help_text='Staff may approve tab orders for customers who already have outstanding debt. Owner-only by default.'
    )

    # ── Manager-only delegated oversight (2026-07-30) ──────────────────
    can_review_petty_cash = models.BooleanField(
        default=False,
        help_text=(
            'Manager may approve/reject petty cash entries. Owner-only by default. '
            'A manager can never review their own submitted entry regardless of this flag.'
        ),
    )
    can_confirm_shifts = models.BooleanField(
        default=False,
        help_text=(
            'Manager may confirm (Thibitisha) a CLOSED shift for staff/waitress/kitchen roles. '
            "Does not extend to a manager's own shift, or another manager's shift — those "
            'always require the owner to confirm.'
        ),
    )
    can_approve_debt_erase = models.BooleanField(
        default=False,
        help_text=(
            "Manager may approve/reject a debt-section 'Ilikuwa Kosa' (mistaken entry) "
            'erase request when Business.debt_erase_requires_approval is enabled. Owner-only '
            'by default. Never extends to a real Write-off (uncollectable debt) request — '
            'that final decision always requires the owner.'
        ),
    )

    # ── Session Control ────────────────────────────────────────────────
    current_session_key = models.CharField(
        max_length=40, blank=True,
        help_text='Session key of the most recent login. Used to enforce single active session per user.'
    )
    allow_concurrent_sessions = models.BooleanField(
        default=False,
        help_text='If True, this user may be logged in from multiple devices at once (e.g. for dev/testing).'
    )

    # ── Staff Departure (soft-delete) ─────────────────────────────────────
    # 2026-07-25: what was `delete_staff` used to hard-delete the User row —
    # UserProfile.user is OneToOneField(CASCADE), which in turn cascaded
    # through Shift.staff, SalaryPayment.staff, SalaryDeduction.staff, and
    # ItemSaleApproval.requested_by (all CASCADE), destroying exactly the
    # shift-hours/salary-paid/revenue history a "staff journey" report needs
    # — and every other FK pointing at User/UserProfile is SET_NULL with no
    # name-cache field, so even the rows that survived became unattributable.
    # Soft-delete closes this without touching any of those FKs: the User row
    # is never destroyed, just deactivated (login blocked via User.is_active,
    # already respected by Django's own AuthenticationForm), so every existing
    # revenue/hours/salary aggregator (_staff_contribution, staff_shrinkage,
    # staff_duty_log) keeps working unmodified for a departed staffer.
    DEPARTURE_REASON_CHOICES = [
        ('resigned',        _('Alijiuzulu mwenyewe')),
        ('terminated',      _('Aliachishwa kazi')),
        ('transferred',     _('Alihamishiwa tawi lingine')),
        ('end_of_contract', _('Mkataba uliisha')),
        ('other',           _('Nyingine')),
    ]
    departed_at = models.DateTimeField(null=True, blank=True)
    departure_reason = models.CharField(max_length=20, choices=DEPARTURE_REASON_CHOICES, blank=True)
    departure_note = models.CharField(max_length=300, blank=True)
    departed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_departures_recorded',
    )
    reactivated_at = models.DateTimeField(null=True, blank=True)
    reactivated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_reactivations_recorded',
    )

    # ── UBA §5.1 — Store scoping ─────────────────────────────────────────
    home_store = models.ForeignKey(
        'core.Store', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='home_staff',
    )
    stores = models.ManyToManyField(
        'core.Store', blank=True, related_name='assigned_staff',
        help_text='Stores this staff member may access. Empty means unrestricted '
                  '(see accessible_stores()) — an owner narrows this per staff member.',
    )

    def accessible_stores(self):
        """UBA §5.1 — which stores this profile may see/act on.

        Owner always sees every active store. A staffer with an explicit
        `stores` assignment is scoped to exactly that set; falling back to
        `home_store` when `stores` is empty (single-store assignment,
        simpler than the M2M for the common case).

        With NEITHER set — true for every staff member that existed before
        this field did, and for any newly-added staffer before an owner
        assigns them anywhere — this deliberately returns ALL of the
        business's active stores, matching today's completely unscoped
        behavior. This differs from the spec's own illustrative pseudocode
        (which falls back to Store.objects.none() here): that version would
        lock every existing staff member out of every store-scoped page the
        instant require_store_access() is wired into any view, before any
        owner has assigned anyone to a specific store — see the M1 entry in
        docs/UBA_PROGRESS.md for the full reasoning. Access only narrows
        once an owner actively assigns a staffer to specific store(s).
        """
        from core.models import Store
        if self.role == 'owner':
            return Store.objects.filter(business=self.business, is_active=True)
        assigned = self.stores.filter(is_active=True)
        if assigned.exists():
            return assigned
        if self.home_store_id:
            return Store.objects.filter(pk=self.home_store_id, is_active=True)
        return Store.objects.filter(business=self.business, is_active=True)

    @property
    def is_departed(self):
        return not self.user.is_active

    def __str__(self):
        return f"{self.user.username} ({self.business.name if self.business else 'No Business'}) - {self.role}"

    @property
    def is_owner(self):
        return self.role == 'owner'

    @property
    def is_manager(self):
        return self.role == 'manager'

    @property
    def is_owner_or_manager(self):
        return self.role in ('owner', 'manager')

    @property
    def is_staff_member(self):
        return self.role == 'staff'

    @property
    def is_rider(self):
        return self.role == 'rider'

    @property
    def is_supplier(self):
        return self.role == 'supplier'

    @property
    def is_waitress(self):
        return self.role == 'waitress'

    @property
    def is_kitchen_staff(self):
        return self.role == 'kitchen'


class StaffNameChangeLog(models.Model):
    """Records every display-name/username change made via edit_staff, since
    that view otherwise silently overwrites first_name/last_name/username with
    no trace. Kept simple (CASCADE on `staff`, unlike SalesResetLog/
    AccountDeletionLog's defensive SET_NULL+cache pattern) because under the
    soft-delete design (see UserProfile departure fields) the `staff` User row
    is never actually destroyed — that defensive pattern exists specifically
    to survive a real delete, which doesn't apply here."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='staff_name_changes')
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='name_change_history')
    old_username = models.CharField(max_length=150, blank=True)
    new_username = models.CharField(max_length=150, blank=True)
    old_display_name = models.CharField(max_length=200, blank=True)
    new_display_name = models.CharField(max_length=200, blank=True)
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_name_changes_made',
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.old_display_name} → {self.new_display_name} ({self.changed_at:%Y-%m-%d})"


class AccountDeletionLog(models.Model):
    """Records why users chose to delete their accounts (user is deleted after this is saved)."""
    REASON_CHOICES = [
        ('not_useful', _('The platform is not useful for my business')),
        ('too_complex', _('Too complex / hard to use')),
        ('found_alternative', _('Found a better alternative')),
        ('closing_business', _('Closing my business')),
        ('privacy', _('Privacy / data concerns')),
        ('temporary', _('Just taking a break')),
        ('other', _('Other')),
        ('admin_removed', _('Removed by platform admin')),
    ]

    username = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=20)
    business_name = models.CharField(max_length=255, blank=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    details = models.TextField(blank=True, help_text='Optional additional details')
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"{self.username} ({self.role}) — {self.get_reason_display()} — {self.deleted_at:%Y-%m-%d}"


# Safely save profile if it exists — does NOT auto-create
@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    try:
        instance.userprofile.save()
    except UserProfile.DoesNotExist:
        pass

@receiver(user_logged_in)
def on_user_login(sender, request, user, **kwargs):
    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        return
    try:
        if profile.business and not profile.is_owner:
            from core.notifications import notify_staff_login
            notify_staff_login(user, profile.business, 'logged in')
    except Exception:
        pass

@receiver(user_logged_out)
def on_user_logout(sender, request, user, **kwargs):
    if not user:
        return
    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        return
    try:
        if profile.business and not profile.is_owner:
            from core.notifications import notify_staff_login
            notify_staff_login(user, profile.business, 'logged out')
    except Exception:
        pass