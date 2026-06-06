# Duka Mwecheche — Claude Project Context

## Project Overview
Multi-tenant Django inventory and business management web application for Kenyan SMEs.
Live at: https://www.dukamwecheche.co.ke
GitHub: https://github.com/mwendwaroy-Angoo/inventory-app
Deployed on: Render (free tier web service) with PostgreSQL database

## Developer
- Name: Collins (goes by Roy), based in Nairobi, Kenya
- Business account username on live app: RoyMwendwa
- Learning Django through building — explain concepts when introducing new patterns

---

## Tech Stack
- Python 3.13+
- Django (latest)
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
```

### core.Transaction
```python
business (FK), item (FK), type (Receipt/Issue/Wastage)
qty, recipient, invoice_no
date, recorded_by
# revenue() method returns selling_price * abs(qty) for Issue
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

Add Transaction (Receipt) cost price section is three-state:
1. Owner → full section (previous cost + input + delivery fee)
2. Staff with `can_input_cost_price` → input only (no previous cost shown)
3. Staff without → hidden entirely

---

## Reserved / Protected Items System

Owner marks items as restricted in Edit Item form.
- `Item.is_restricted = True` + `Item.restriction_notes` + `Item.restricted_quantity`
- Staff selling restricted items → `ItemSaleApproval` created → owner notified (in-app + SMS + email)
- Owner approves (transaction auto-created) or denies (with reason) from `/approvals/`
- Staff sees live-polling waiting screen (10s interval)
- `restricted_quantity = 0` → ALL sales need approval
- `restricted_quantity = N` → staff can sell freely until balance would drop below N
- Three-state warning in add_transaction template: all-restricted (red), partial with free units (amber), at/below threshold (red)

---

## Product Tours (Driver.js)

Infrastructure:
- `UserProfile.onboarding_sections_seen` JSONField — tracks completed tour IDs
- Context processor `core/context_processors.py` injects `tour_sections_seen` into every template
- `core/onboarding_views.py` — POST `/onboarding/seen/` marks section as seen
- `window.startTour(sectionId, steps)` and `window.markSeen(sectionId)` in base.html
- Dark luxury theme overrides for Driver.js popovers (gold border, onyx background)

Tours implemented across 17 templates:
- Owner: dashboard, stock_list, add_transaction, quick_sell, history, sales, analytics,
  stores, items, fulfillment, payments, debt_tracker, debt_profile
- Supplier: supplier_home, browse_requests, my_bids
- Rider: rider_home

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
6. All translated strings with apostrophes must use double-quoted JS strings
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

### Analytics & Reporting
- Sales & P&L dashboard (daily bar chart, top items)
- Analytics with ETS/Holt-Winters demand forecasting
- Break-even analysis
- Capital investments tracker
- County-level sales heatmap (Leaflet choropleth)

### Revenue Targets
- Daily/weekly/monthly targets per business and per store
- Dashboard widget — colour-coded progress bars (≥100% green, ≥50% amber, <50% red)
  Colors computed in view via `_build_target_data()` — NOT in template (widthratio unreliable)
- `core/templatetags/dict_extras.py` — `get_item` filter + `store_target` tag

### Debt Tracker
- Dashboard at `/debt/` — all customers with outstanding balances, aged debt
- Customer debt profile — FIFO balance, aged buckets, credit score engine
- Record Payment modal, Send SMS Reminder
- Per-customer credit settings (limit, expected_payment_days)
- Toggle credit approval (owner only); staff can record payments and send reminders

### Staff Permissions Panel
- `/staff/<id>/permissions/` — owner manages per-staff toggles
- `can_input_cost_price`: staff sees cost price input but not previous cost
- `can_override_restrictions`: staff bypasses restricted item approval

### Reserved / Protected Items
- Owner marks items restricted in Edit Item form
- Staff intercepted → approval request created → owner notified
- Full approval workflow with approve/deny, auto-transaction on approval
- Reserved quantity threshold for partial restrictions

### Business Management
- Multi-store support
- Staff management with Permissions button per staff member
- Role-based access (owner/staff/rider/supplier)
- Business settings with Leaflet map

### Compliance System
- 182+ requirements across 60+ business types
- Tier system: micro/semi-formal/formal

### Supply Chain
- Supplier portal, rider portal
- Procurement system (POs, bids, bid scoring)

### Payments
- Till, Paybill, Pochi la Biashara, Personal M-Pesa settings
- Payment prompts (confirm/dismiss M-Pesa)
- STK Push integration

### Onboarding
- Modal tutorial overlay (role-specific, 4 variants, one-time)
- Driver.js spotlight tours (17 templates, auto-trigger first visit, never repeat)
- Both systems coexist — modal on first login, spotlight tours on each section

---

## Pending Features — Next Sprint

### Kibanda / Produce Module (PLANNED — NOT YET BUILT)

**Business context:** Kibanda (vegetable stall) operators sell produce using three models:

**Model 1 — Value-based (cabbage, skuma, spinach):**
Customer requests by value ("niongezee mboga za 40 bob"). Seller portions accordingly.
Cabbage bought for KES 30/head, total sold portions = KES 100-120.

**Model 2 — Count-based greens (kale, skuma stems):**
Bundle bought whole, sold by stem count. 4 stems = KES 10, 8 stems = KES 20.

**Model 3 — Unit-conversion dry goods (potatoes, beans, maize):**
Bought by sack, sold by gorogoro (recycled 2kg tin). 1 sack ≈ 40 small gorogoros.

**Single piece items:** Bell pepper KES 10/piece, coriander KES 10/bunch, beetroot KES 10/piece.

**Multi-piece pricing:** Big onion/tomato KES 10/piece; small ones 3 for KES 20-25.

**Carrots:** Sold in bundles of a few for KES 20. Bought per kg from market.

**Models to build:**
- `Item.is_produce` (BooleanField) — enables portion-based selling mode
- `ItemPortionPreset` — price points per item:
  - `item` (FK), `label` (e.g. "Quarter head"), `price` (KES amount), `quantity_consumed` (fraction of stock unit), `order` (display order)

**Units to add:** Bundle, Bunch, Heap, Piece, Gorogoro (Small/Medium/Large)

**UI changes:**
- Item form: produce toggle + portion preset table (owner builds price menu)
- Quick Sell: produce items show price-point buttons instead of quantity field
- Add Transaction: produce items show preset selector
- Fractional stock display (0.75 of a head remaining)

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

---

## Important Patterns

### Multi-tenancy
Every queryset scoped to `request.user.userprofile.business`. Never query without business filter.

### Notification Creation
```python
Notification.objects.create(
    business=business,
    user=user,
    message="...",
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

### Cost Price Sections in add_transaction.html
Three-state based on context variables:
- `is_owner=True` → full section
- `show_cost_price_input_only=True` → input only
- Neither → hidden

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
