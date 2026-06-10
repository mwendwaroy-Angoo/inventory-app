# Duka Mwecheche — Claude Project Context

## Project Overview
Multi-tenant Django inventory and business management web application for Kenyan SMEs.
Live at: https://www.dukamwecheche.co.ke
GitHub: https://github.com/mwendwaroy-Angoo/inventory-app
Deployed on: Render (free tier web service) with PostgreSQL database

## Developer
- Name: Collins (goes by Roy), based in Nairobi, Kenya
- Business account username on live app: RoyMwendwa
- Staff test account: Morrine
- Learning Django through building — explain concepts when introducing new patterns

---

## Tech Stack
- Python 3.13+
- Django 4.2.x
- Bootstrap 5 via django-bootstrap5
- Chart.js (dashboards and analytics)
- Driver.js 1.3.5 (product tours / spotlight onboarding)
- WhiteNoise (static files)
- dj-database-url (database config)
- openpyxl (Excel exports)
- africastalking (SMS — live account, username: dukamwecheche)
- resend (email API — replaces Gmail SMTP which is blocked on Render free tier)
- Twilio (WhatsApp — disabled pending production number)
- Select2 (searchable dropdowns)
- Leaflet.js (maps in business settings)
- PostgreSQL (production), SQLite (local dev)

---

## Django Apps
1. `core` — items, transactions, stores, customers, notifications, compliance, debt, analytics
2. `accounts` — business registration, user profiles, staff management

---

## URL Structure
- `accounts/` — Django built-in auth URLs
- `business/` — custom accounts app URLs
- `business/ajax/subcounties/` — AJAX endpoint for county dropdowns
- `business/ajax/wards/` — AJAX endpoint for ward dropdowns

---

## Key Models

### accounts.Business
```python
name, role (owner/supplier/rider), business_type, phone, email, address
county, sub_county, ward  # FK to seeded Kenya geography models
latitude, longitude
opening_time, closing_time, is_open_override
offers_delivery, delivery_radius_km, delivery_fee, delivery_fee_per_km
min_order_amount, min_order_per_km
mpesa_till, mpesa_paybill, mpesa_paybill_account, mpesa_pochi, mpesa_phone
preferred_payment_channel
business_start_date, pre_app_cumulative_profit
credit_window_days          # PositiveIntegerField, default 30
last_txn_sms_at             # DateTimeField null=True — 10-min SMS bundling window
```

### accounts.UserProfile
```python
user (FK), business (FK), role (owner/staff/rider/supplier)
phone
has_seen_tutorial           # BooleanField — modal tutorial shown on first login
onboarding_sections_seen    # JSONField — list of Driver.js tour section IDs seen
can_input_cost_price        # BooleanField default False — staff can enter cost price
can_override_restrictions   # BooleanField default False — staff bypasses item approval
# Properties: is_owner, is_staff_member, is_rider, is_supplier
```

### core.Item
```python
business (FK), store (FK), description, material_number
unit, selling_price, cost_price
reorder_level, reorder_quantity
is_yield_item (BooleanField)    # loses weight/volume during processing
yield_factor (Decimal 0-1)       # e.g. 0.65 = 65% usable after processing
is_restricted (BooleanField)     # staff need owner approval to sell
restriction_notes (CharField)    # reason — owner only
restricted_quantity (PositiveIntegerField default 0)
# 0 = ALL sales need approval
# N = staff can sell freely until balance would drop below N

# ── Kibanda Produce Module fields (migration 0041) ──────────────────────
is_produce (BooleanField default False)
# True = portion-based selling. Owner sets price presets in ItemPortionPreset.

PRODUCE_MODE_CHOICES = [('PORTION', ...), ('BUNCH', ...)]
produce_mode (CharField max_length=10 default='PORTION')
# PORTION = fixed qty per price (cabbage, gorogoro, multi-piece like tatu mbao)
# BUNCH   = each bunch is a money target depleted by price-point sales
#           (sukuma, spinach, kienyeji greens)

mix_group (CharField max_length=40 blank=True)
# Tag for greens that can be sold as one generic "mboga za X" order.
# Items sharing the same tag pool into a mix tile in Quick Sell.
# Blank = sold only by name (sukuma, spinach).

revenue_multiplier (DecimalField max_digits=4 decimal_places=2 default=1.70)
# Suggests bunch target from cost (1.75 → 40/= bunch targets 70/=).
# Overridable per bunch at receive time.

def default_bunch_target(self, cost): ...
# Returns cost × revenue_multiplier, quantized to 1 shilling.
```

### core.ItemPortionPreset
```python
item (FK), label (CharField), price (DecimalField), quantity_consumed (DecimalField)
display_order (IntegerField default 0)
# PORTION mode: label = "Kimoja", "Tatu mbao", "Quarter head"; qty_consumed = pieces/fraction
# BUNCH mode: label = "KES 20" (auto-filled if blank); qty_consumed ignored
```

### core.Transaction
```python
business (FK), item (FK), type (Receipt/Issue/Wastage)
qty (DecimalField), recipient, invoice_no
date, recorded_by
payment_method (CharField choices cash/mpesa/credit)

# ── Kibanda Produce Module additions (migration 0041) ─────────────────
sale_amount (DecimalField null=True blank=True)
# Actual cash for this sale line. Set for:
# (a) BUNCH greens: portion sale (e.g. 20/= from a 70/= envelope)
# (b) PORTION presets: preset price when it differs from qty × selling_price
#     (e.g. Tatu mbao: 3 onions for KES 20, not 3 × KES 10 = KES 30)
# revenue() prefers sale_amount when set.

produce_bunch (FK ProduceBunch null=True blank=True)
# Links bunch-mode sales to the specific ProduceBunch they depleted.
# Used as discriminator in analytics _units() — see analytics_views.py.

def revenue(self): ...  # Returns sale_amount if set, else selling_price × abs(qty)
def cost(self): ...     # Uses produce_bunch.cost_price for bunch sales, else item.cost_price × qty
def profit(self): ...   # revenue() - cost()
```

### core.ProduceBunch
```python
# A single physical bunch (shada/fungu) of greens bought at the market.
# Models a REVENUE ENVELOPE: bought at cost, depletes by price-point sales,
# closed when target_revenue is reached. Stems never enter the system.
item (FK Item), business (FK accounts.Business)
size (CharField choices SMALL/MEDIUM/LARGE)
cost_price (DecimalField)       # what this bunch cost at market
target_revenue (DecimalField)   # must earn this before bunch is "finished"
revenue_collected (DecimalField default=0)
status (CharField choices OPEN/DEPLETED/DISCARDED)
received_on (DateField default=today)
opened_on, closed_on (DateTimeField null=True)
note (CharField blank=True)

# Key methods:
def remaining(self):     → max(0, target - collected)
def is_sold_out(self):   → remaining() <= 0
def realized_markup(self): → revenue_collected / cost_price
def age_days(self):
def is_wilting(self, threshold_days=1): → open and older than threshold
def record_sale(self, amount, payment_method, recipient): → creates Transaction, updates envelope
def discard(self, reason): → writes off unsold remainder as Wastage transaction

@classmethod
def sell_mix(cls, business, mix_group, amount, payment_method, item_ids=None):
    # "Mboga za kienyeji ya 20" — spreads amount across OPEN bunches in the mix group,
    # weighted by remaining envelope. item_ids = optional subset (kibanda lady's selection).
```

### core.Store
```python
business (FK), name, description
# Store.__str__ must handle null business gracefully
```

### core.Customer
```python
business (FK), name, phone, location, county (FK core.County, SET_NULL)
credit_approved (BooleanField default False)
credit_limit (DecimalField nullable)
expected_payment_days (PositiveIntegerField nullable)
```

### core.CustomerDebtPayment
```python
customer (FK), business (FK)
amount_paid, payment_method (cash/mpesa)
paid_at, notes, recorded_by (FK User)
# Outstanding = sum(credit Issue transactions) - sum(payments) — FIFO logic
```

### core.RevenueTarget
```python
business (FK), store (FK nullable — null = business-wide)
target_type (daily/weekly/monthly), amount
unique_together: (business, target_type, store)
```

### core.ItemSaleApproval
```python
business (FK), item (FK), requested_by (FK User)
quantity, recipient, invoice_no, payment_method
status (pending/approved/denied)
denial_reason, requested_at, decided_at, decided_by (FK User)
transaction (FK Transaction nullable — created on approval)
```

### core.Notification
```python
business (FK), user (FK), message, is_read, created_at
related_name='app_notifications'  # ALWAYS use this related_name
```

---

## Kibanda Produce Module (BUILT — migrations 0041, 0042)

### Design Philosophy
A kibanda sells produce in three distinct models:

**BUNCH mode (greens / mboga):** The mama mboga does NOT count stems. She buys a bunch
at market cost and expects the bunch to earn a target revenue before it's "finished."
Each sale is a price point ("ya 20") — the system tracks money in/out of the bunch
envelope. Examples: sukuma, spinach, managu, terere, kunde.

**PORTION mode (multi-piece / gorogoro):** Fixed qty per price point. Examples:
- Cabbage: "Quarter head" / KES 40 / quantity_consumed 0.25
- Onions: "Kimoja" / KES 10 / qty 1; "Tatu mbao" / KES 20 / qty 3
- Potatoes: gorogoro sizes with KES prices per tin

### Selling Flow (Quick Sell)
- Bunch items appear in the **🥬 Mboga / Greens** board at the top of Quick Sell,
  SEPARATE from the normal item grid (they're excluded from the grid via
  `.exclude(is_produce=True, produce_mode='BUNCH')` in the view query).
- Each bunch tile shows: remaining/target meter, depletion bar, "uza kwanza" badge if wilting.
- Mix tile: items sharing a mix_group appear as one "Mboga za kienyeji" tile.
  Tap → member chip selector → choose price → proportional sell_mix().
- Cart: Add stays open (modal persists), Done closes. "↩ Futa" undo link after each add.
- Portion items appear in the normal grid and open the existing "Select Portion" modal.

### Endpoints (produce_views.py)
```
GET  /stock/produce/board/           → produce_board (greens tile data; can_receive=is_owner)
POST /stock/produce/receive/         → receive_bunches (owner only; creates ProduceBunch + Receipt)
POST /stock/produce/bunch/<id>/discard/ → discard_bunch (writes wastage)
```

### Key Business Rules
- Selling PAST target is allowed (tracks surplus as bonus margin)
- Bunch items deplete in fractional bundle units: qty = -sale_amount / target_revenue
- sell_mix(): proportional split across open bunches weighted by remaining envelope
- Chips in mix modal start UNSELECTED; no selection = all with stock (auto mode)
- Staff never sees "+From market" or bunch discard (QS_IS_OWNER from template context)

### Analytics (_units discriminator — analytics_views.py)
```python
def _units(t):
    # Bunch greens: produce_bunch_id is set → count as 1 customer portion (not the qty fraction)
    if getattr(t, 'produce_bunch_id', None) is not None:
        return 1.0
    # All other items (incl. portion presets): use qty (e.g. Tatu mbao = 3 onions)
    return float(abs(t.qty or 0))
```
Analytics section "🛒 Kibanda Produce Performance" shows:
- Greens (BUNCH): ProduceBunch data — bunches in/done, revenue, cost, markup×, wastage
- Other produce (PORTION): Transaction data — units sold, revenue, cost, margin%

### Label Dropdown (item_form.html — fbff5b4)
Preset label field includes optgroup "Kibanda / Multi-piece":
Kimoja, Mbili, Tatu kumi, Tatu mbao, Nne kumi, Nne mbao, Tano mbao, Sita mbao, Custom.
"Custom" triggers `_toCustomInput()` which replaces the select with a text field.

### Known Watch Points for Produce Module
- NEVER confuse `sale_amount` discriminator with `produce_bunch_id` discriminator.
  sale_amount is set for BOTH bunch and portion preset sales (since fbff5b4).
  produce_bunch_id is ONLY set for bunch greens. Use produce_bunch_id to identify greens.
- float() * Decimal() raises TypeError. stock_value() uses float(self.current_balance()).
  Always cast both operands when mixing float and Decimal arithmetic.
- The greens board fetch (AJAX to /stock/produce/board/) uses QS_IS_OWNER from
  Django template context, NOT from the AJAX response, to avoid race conditions
  where staff saw the owner's receive modal before the board had loaded.

---

## Notification System (Complete)

### Channels
- **SMS**: Africa's Talking live (AT_USERNAME=dukamwecheche). Phone normalization:
  `normalize_ke_phone()` in `core/notifications.py` converts 07XXXXXXXX → +254XXXXXXXX
- **Email**: Resend API (RESEND_API_KEY env var). Domain verified: dukamwecheche.co.ke.
  Sends from `notifications@dukamwecheche.co.ke`. Function: `send_email_notification()`
- **WhatsApp**: Disabled (`send_whatsapp_notification()` is a no-op, logs warning)
- **In-app**: `Notification.objects.create()` — bell icon, 30s polling

### Notification Router (`core/notifications.py`)
Central routing via `route_notification(event_type, business, owner_phone, owner_email, sms_msg, email_subject, email_html)`.

Routing table:
```
TRANSACTION_ISSUE   → SMS (rate-limited 10min) + Email
TRANSACTION_RECEIPT → None (cost price reminder handled separately)
LOW_STOCK           → Email only
REORDER             → Email only
STAFF_LOGIN         → SMS + Email (audit trail)
STAFF_LOGOUT        → None
CUSTOMER_ORDER      → SMS (urgent) + Email
DAILY_SUMMARY       → SMS nudge + Email
```

### SMS Bundling
`Business.last_txn_sms_at` — DateTimeField tracks last transaction SMS.
`_sms_allowed_by_rate_limit(business)` — returns True if >10 minutes since last SMS,
updates timestamp. Prevents transaction SMS spam on busy trading days.

### Render Free Tier Note
Outbound SMTP (port 587) is BLOCKED on Render free tier. Always use Resend API for email.
Never reintroduce `send_mail()` or `EmailMultiAlternatives` — they will silently fail.

---

## Staff Permissions System

Per-staff permission toggles managed at `/staff/<id>/permissions/` (owner only).

| Permission | Field | Default | Effect |
|---|---|---|---|
| Cost Price Input | `can_input_cost_price` | False | Sees input field (not previous cost) on Receipt |
| Restricted Override | `can_override_restrictions` | False | Sells restricted items without approval |

---

## Reserved / Protected Items System

Owner marks items as restricted in Edit Item form.
- `Item.is_restricted = True` + `Item.restriction_notes` + `Item.restricted_quantity`
- Staff selling restricted items → `ItemSaleApproval` created → owner notified (in-app + SMS + email)
- Owner approves (transaction auto-created) or denies (with reason) from `/approvals/`
- Staff sees live-polling waiting screen (10s interval)
- `restricted_quantity = 0` → ALL sales need approval
- `restricted_quantity = N` → staff can sell freely until balance would drop below N

---

## Product Tours (Driver.js)

Infrastructure:
- `UserProfile.onboarding_sections_seen` JSONField — tracks completed tour IDs
- Context processor `core/context_processors.py` injects `tour_sections_seen` into every template
- `core/onboarding_views.py` — POST `/onboarding/seen/` marks section as seen
- `window.startTour(sectionId, steps)` and `window.markSeen(sectionId)` in base.html
- Dark luxury theme overrides for Driver.js popovers (gold border, onyx background)

Tours implemented across 17 templates (owner, supplier, rider sections).

---

## UI Theme — "Duka Mwecheche Dark Luxury"
```css
:root {
    --onyx: #1a1a1a;
    --onyx-card: #2a2a2a;
    --gold: #c9a84c;
    --gold-light: #e2c36e;
    --pearl: #f0ece4;
    --raspberry: #c0395a;
    --raspberry-dark: #8b1a35;
}
```
Fonts: Playfair Display (headings), DM Sans (body)

### CRITICAL THEME RULES — Never Violate
1. NEVER `class="text-muted"` → use `style="color: #b0b0b0"`
2. NEVER Bootstrap bg classes on cards (`bg-light`, `bg-dark`, `bg-white`, etc.)
3. NEVER `{% trans 'string' %}` wrapped across lines by formatters
4. NEVER Gmail SMTP — use Resend API only
5. NEVER `{% trans %}` tags inside single-quoted JS strings (apostrophes crash parser)
   → Always use double-quoted JS strings: `"{% trans \"You're done\" %}"`
6. All translated strings with apostrophes must use double-quoted JS string wrapper
7. `btn-gold` for primary actions, never `btn-primary`
8. `style="color: #b0b0b0"` for hint/muted text, never `var(--muted)` (#888 is invisible)
9. `.dropdown-menu` has `max-height: 80vh; overflow-y: auto` — never remove this
10. Mobile navbar collapse has `max-height: 75vh; overflow-y: auto` — never remove

---

## Coding Preferences
- **Always output complete files** — never use `...`, `# unchanged`, `# rest of code`
- **One file at a time** — show result, state what changed, then move to next
- **Never truncate** — complete every file fully before stopping
- **No Django template formatters** — Prettier breaks `{% trans %}` tags

---

## Geography
- All 47 Kenya counties, sub-counties, wards seeded via data migrations
- `County` model lives in `core` app (not accounts)
- `Customer.county` is FK to `core.County`, SET_NULL, optional
- Dynamic dropdowns: `/business/ajax/subcounties/` and `/business/ajax/wards/`

---

## Features Built (Complete)

### Core Inventory
- Stock list with store/status filters
- Add Transaction (Receipt/Issue/Wastage) with cost price, landed cost, yield processing
- Transaction history with Excel export
- Quick Sell POS (cart-based, M-Pesa/cash/credit)

### Kibanda Produce Module (BUILT — see section above)
- BUNCH mode: revenue-envelope selling for greens (ProduceBunch model)
- PORTION mode: multi-piece + gorogoro presets (ItemPortionPreset model)
- Greens board in Quick Sell: tiles, mix picker, member chip selection, Done/Futa UX
- +From market modal: owner receives bunches, sets cost + target per bunch
- Wastage tracking: discard bunches, write off remainder
- Wilting alerts: "uza kwanza" badge on old open bunches

### Analytics & Reporting
- Sales & P&L dashboard (daily bar chart, top items)
- Analytics with ETS/Holt-Winters demand forecasting
- 🛒 Kibanda Produce Performance section (greens by ProduceBunch + portion produce by Transaction)
- Break-even analysis, Capital investments tracker
- County-level sales heatmap (Leaflet choropleth)

### Revenue Targets
- Daily/weekly/monthly targets per business and per store
- Dashboard widget — colour-coded progress bars computed in view via `_build_target_data()`

### Debt Tracker
- Dashboard, customer debt profile, FIFO balance, aged buckets, credit score engine
- Record Payment modal, SMS reminder, per-customer credit settings

### Staff Permissions Panel
- `/staff/<id>/permissions/` — per-staff toggles: can_input_cost_price, can_override_restrictions

### Reserved / Protected Items
- Full approval workflow with approve/deny, auto-transaction on approval

### Business Management
- Multi-store support, staff management, role-based access
- Business settings with Leaflet map

### Supply Chain
- Supplier portal, rider portal, procurement system (POs, bids, bid scoring)

### Payments
- Till, Paybill, Pochi la Biashara, Personal M-Pesa settings
- STK Push integration, payment method tracking per transaction

### Onboarding
- Modal tutorial overlay (role-specific, 4 variants, one-time)
- Driver.js spotlight tours (17 templates, auto-trigger first visit, never repeat)

---

## Pending / In Progress

### Sack-to-Portion Yield Model (PLANNED)
Potatoes (sack → gorogoro), carrots (pile/sack → bundles), beans, maize — bought in bulk,
sold in portions. Proposed: extend "+From market" modal to PORTION items:
- Fields: "Units you received" + "Total batch cost"
- Creates: Receipt transaction for units, updates item.cost_price = total/units
- No new model needed for MVP; owner enters yield count directly

### Comprehensive Kibanda Produce Analytics (IN PROGRESS)
Currently: greens (ProduceBunch) + PORTION produce (Transaction).
Next: better wastage tracking for PORTION items, per-day PORTION breakdown.

---

## Environment Variables (Render)
- `AT_USERNAME` = dukamwecheche (live account)
- `AT_API_KEY` = live key
- `RESEND_API_KEY` = re_... (production key)
- `DEFAULT_FROM_EMAIL` = Duka Mwecheche <notifications@dukamwecheche.co.ke>
- `DATABASE_URL` = PostgreSQL connection string
- `SECRET_KEY` = Django secret key

## Deployment
- Render auto-deploys on every git push to main
- `reset_superuser` management command runs on every deploy
- Static files served by WhiteNoise
- Free tier: worker SIGKILL risk on memory-heavy operations — use `iterator(chunk_size=10)`
- Free tier: NO shell access. No SMTP. Spin-down on inactivity (~50s cold start).

---

## Important Patterns

### Multi-tenancy
Every queryset scoped to `request.user.userprofile.business`. Never query without business filter.

### Notification Creation
```python
Notification.objects.create(
    business=business, user=user, message="...",
)
# Query: user.app_notifications.filter(is_read=False)
```

### Template Structure
```html
{% extends "base.html" %}
{% block title %}{% endblock %}
{% block extra_css %}<style>...</style>{% endblock %}
{% block content %}{% endblock %}
{% block page_tour %}{% endblock %}  # Driver.js tour for this page
{% block extra_js %}<script>...</script>{% endblock %}
```

### Phone Normalization
```python
from core.notifications import normalize_ke_phone
phone = normalize_ke_phone('0712345678')  # returns '+254712345678'
```

### Revenue Target Colors
Always compute in view via `_build_target_data(actual, target)` returning `color` and `pct`.
Never use `{% widthratio %}` for color comparisons — unreliable in Django templates.

---

## Known Issues / Watch Points
- `Store.__str__` must handle null business (causes crashes if not guarded)
- `Notification` model uses `related_name='app_notifications'` — always use this
- `{% trans %}` tags break if any formatter wraps them across lines
- `{% trans "You're..." %}` must use double-quoted JS string wrapper
- Render free tier blocks SMTP — never use Django's email backend directly
- Daily cron uses `iterator(chunk_size=10)` to prevent SIGKILL
- `UserInBlacklist` AT error on Safaricom = no Sender ID registered (KES 8,700 one-time)
- AT default sender works for Airtel only without Sender ID
- `float * Decimal` raises TypeError in Python 3. Always cast both sides:
  `float(x) * float(y)` — never `float(x) * self.current_balance()` since
  current_balance() returns Decimal when transactions exist.
- `_units()` in analytics_views.py uses `produce_bunch_id` (not `sale_amount`) to
  identify bunch greens. Both bunch and portion preset sales have `sale_amount` set
  (since fbff5b4), so `sale_amount` is no longer a unique discriminator.
- `analytics_dashboard` view must have `@login_required` and `@owner_required`
  decorators directly above it — never insert helper functions between the decorators
  and the view function or the decorators apply to the helper, not the view.
