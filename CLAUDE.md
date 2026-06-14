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
- Python 3.13+, Django 4.2.x
- Bootstrap 5 via django-bootstrap5
- Chart.js (dashboards and analytics)
- Driver.js 1.3.5 (product tours / spotlight onboarding)
- WhiteNoise (static files), dj-database-url (database config)
- openpyxl (Excel exports)
- africastalking (SMS — live account, username: dukamwecheche)
- resend (email API — replaces Gmail SMTP which is BLOCKED on Render free tier)
- Twilio (WhatsApp — disabled pending production number)
- Select2 (searchable dropdowns), Leaflet.js (maps in business settings)
- PostgreSQL (production), SQLite (local dev)

---

## Django Apps
1. `core` — items, transactions, stores, customers, notifications, compliance, debt, analytics
2. `accounts` — business registration, user profiles, staff management

---

## Key Models

### accounts.Business
```python
name, role (owner/supplier/rider), business_type, phone, email, address
county, sub_county, ward  # FK to seeded Kenya geography models
latitude, longitude
opening_time, closing_time, is_open_override
offers_delivery, delivery_radius_km, delivery_fee, delivery_fee_per_km
mpesa_till, mpesa_paybill, mpesa_paybill_account, mpesa_pochi, mpesa_phone
preferred_payment_channel
business_start_date, pre_app_cumulative_profit
credit_window_days          # PositiveIntegerField, default 30
last_txn_sms_at             # DateTimeField null=True — 10-min SMS bundling window
```

### accounts.UserProfile
```python
user (FK), business (FK), role (owner/staff/rider/supplier), phone
has_seen_tutorial, onboarding_sections_seen (JSONField)
can_input_cost_price (BooleanField default False)
can_override_restrictions (BooleanField default False)
```

### core.Item
```python
business (FK), store (FK), description, material_number
unit, selling_price, cost_price
reorder_level, reorder_quantity
is_yield_item (BooleanField), yield_factor (Decimal 0-1)
is_restricted (BooleanField), restriction_notes, restricted_quantity

# ── Kibanda Produce Module fields (migration 0041) ──────────────────────
is_produce (BooleanField default False)

produce_mode (CharField choices PORTION/BUNCH default='PORTION')
# PORTION = fixed qty per price point (cabbage, onions, tomatoes, pre-portioned gorogoros)
# BUNCH   = revenue-envelope model — each "bunch" (shada, gunia/sack) is bought at cost,
#           depletes by price-point sales until target_revenue is reached.
#           Used for: leafy greens (sukuma, spinach, managu etc.) AND
#           sack goods (potatoes, beans, maize, rice, ndengu, flour, carrots).
#           The key question: "do you know the count upfront?" No → BATCH. Yes → PORTION.

mix_group (CharField max_length=40 blank=True)
# Greens sharing this tag pool into one "Mboga za X" tile in Quick Sell.

revenue_multiplier (DecimalField default=1.70)
# Auto-suggests target = cost × multiplier when receiving from market.

def default_bunch_target(self, cost): ...
```

### core.ItemPortionPreset
```python
item (FK), label (CharField), price (DecimalField), quantity_consumed (DecimalField)
display_order (IntegerField default 0)
# PORTION mode: label="Kimoja", price=10, qty_consumed=1  OR  label="Tatu mbao", price=20, qty_consumed=3
# BUNCH mode:   label="Small Gorogoro", price=80 → price-point tile. qty_consumed IGNORED.
# Same preset rows serve both modes. BUNCH mode ignores stock-used column.
```

### core.Transaction
```python
business (FK), item (FK), type (Receipt/Issue/Wastage)
qty (DecimalField), recipient, invoice_no, date, recorded_by
payment_method (cash/mpesa/credit)

sale_amount (DecimalField null=True)
# Set for: (a) BUNCH sales — actual KES from the envelope (e.g. 20/= from a 70/= target)
#          (b) PORTION preset sales — preset price (e.g. 20 for Tatu mbao vs 3×10=30)
# revenue() prefers sale_amount when set.

produce_bunch (FK ProduceBunch null=True)
# DISCRIMINATOR: set ONLY for bunch-mode sales. Use produce_bunch_id to identify greens/batch.
# DO NOT use sale_amount as discriminator — it is set for both bunch AND portion preset sales.
```

### core.ProduceBunch
```python
# REVENUE ENVELOPE for one physical "batch" bought at market.
# For greens: a bunch of sukuma (shada). For dry goods: a sack of potatoes (gunia).
# Depletes by price-point sales until target_revenue is reached.
item (FK), business (FK)
size (CharField SMALL/MEDIUM/LARGE)
# For sack goods received as gunia: size='LARGE' (a sack is always LARGE)
# For gorogoro pre-portioned: size = gorogoro size (SMALL/MEDIUM/LARGE)
# For greens: size = physical bunch size
cost_price (DecimalField)       # market purchase cost
target_revenue (DecimalField)   # must earn this to close the batch
revenue_collected (DecimalField default=0)
status (OPEN/DEPLETED/DISCARDED)
received_on (DateField), opened_on, closed_on (DateTimeField null)
note (CharField blank)

def remaining(self):       → max(0, target - collected)
def is_sold_out(self):     → remaining() <= 0
def realized_markup(self): → revenue_collected / cost_price
def is_wilting(self, threshold_days=1): → open and older than threshold
def record_sale(amount, payment_method, recipient): → Transaction + envelope update
def discard(reason): → Wastage transaction for remaining value

@classmethod
def sell_mix(cls, business, mix_group, amount, payment_method, item_ids=None):
    # Spreads amount across open bunches in the group, weighted by remaining envelope.
```

---

## Kibanda Produce Module (BUILT — migrations 0041, 0042)

### The Two Selling Modes

**BUNCH / BATCH mode** (revenue envelope — no unit counting):
She buys at market cost, expects to earn a target. Sells by price point ("ya 20"). The
system tracks money in/out. She never counts stems, gorogoros, or bundles — she just
sells until the batch is "done."

| Item Type | Bought As | Sold As | Mode |
|---|---|---|---|
| Sukuma, spinach, managu, terere, kunde | Bunch (shada) | Price points (ya 10, ya 20) | BUNCH |
| Potatoes (viazi) | Sack/gunia | Gorogoro (S/M/L) | BATCH |
| Beans (maharagwe), ndengu, maize (mahindi), rice (mchele), flour (unga), sugar (sukari) | Sack | Gorogoro | BATCH |
| Carrots (karoti) | Pile/sack | Small bundle | BATCH |

**PORTION / PIECE mode** (unit-counted — she knows the count):
Fixed qty per price point. Each sale deducts a known quantity from stock.
Used when the owner CAN count her inventory upfront.

| Item Type | Sold As | Presets |
|---|---|---|
| Cabbage (kabichi) | Quarter/half/full head | 0.25, 0.5, 1.0 quantity_consumed |
| Onions, tomatoes, avocado, etc. | Kimoja / Tatu mbao / Nne mbao | qty_consumed = 1/3/4 |
| Pre-portioned gorogoros (bought already measured) | Gorogoro | qty_consumed = 1 |

The key question: **"Do you know the count before you start selling?"** Yes → PORTION. No (whole sack) → BATCH.

### Item Form Intelligence (item_form.html)
When description is typed, `UNIT_MAP` JS lookup suggests the correct unit AND mode:
- Type "Potatoes/viazi" → suggests Gorogoro, note: "use Batch mode (not Portion)"
- Type "Tomatoes/nyanya" → suggests Pcs, note: "Portion mode is correct"
- Type "Sukuma/kale" → suggests Bunch, note: "use Batch/Bunch mode"
- Similar for: maharagwe, ndengu, mahindi, mchele, unga, sukari, karoti, kabichi, vitunguu, onions, dhania, pilipili, mangoes, avocado, banana, etc.

Toggle labels:
- `📦 Batch / Bunch — greens, sacks (viazi, maharagwe...)`
- `Portion / piece — cabbage, pieces, pre-portioned`

Bunch preset hint (in Batch mode): explains that rows = price tiles, Stock Used column ignored.

### +From Market Modal (Quick Sell, owner only)

Unified modal for ALL produce (BUNCH/BATCH + PORTION items).
Item dropdown = greens board items + PORTION items from produce_board.

**If PORTION item selected:** units received + total batch cost → Receipt transaction, updates item.cost_price = total/units.

**If BATCH item selected (greens, unit=Bunch):** existing bunch fields:
- Bunch size (S/M/L), Cost/bunch, How many bunches, Target (optional)
- Creates N ProduceBunches at the given size and cost

**If BATCH item selected (sack/dry goods, unit=Gorogoro/Bundle/etc.):**
Radio toggle "Ulinunua vipi sokoni?":
- 🛍 Gunia/Sack: How many gunias? + Cost per gunia → creates N ProduceBunches (size=LARGE each)
- 🥫 Gorogoro (pre-portioned): Size (S/M/L) + How many + Total cost → creates 1 ProduceBunch for the batch

### Quick Sell Board
- BATCH items → greens board tiles (excluded from normal grid)
- PORTION items → normal grid → Select Portion modal
- Mix tile: items sharing mix_group pool into one "Mboga za kienyeji" tile
- Cart UX: Add stays open, Done closes, ↩ Futa undo link persists until next add or Done
- Staff: never sees "+From market" or discard button (QS_IS_OWNER from template context)

### Board API (produce_views.py)
`GET /stock/produce/board/` returns:
```json
{
  "greens": [{id, name, mix_group, presets, open_bunches, remaining, target_open,
               wilting, oldest_bunch_id, has_history, item_balance, cost_price, unit}],
  "mixes": [{mix_group, remaining, presets, members, has_history}],
  "can_receive": bool,
  "portion_items": [{id, name, unit, produce_mode, cost_price}]
}
```
`unit` in greens is critical — used by the receive modal to detect greens (unit=Bunch) vs sack items.

### Analytics
`_units()` in analytics_views.py uses `produce_bunch_id` (NOT `sale_amount`) to discriminate:
- produce_bunch_id set → batch/greens sale → count as 1 customer portion
- produce_bunch_id null → regular item or portion preset → use qty

Analytics section "🛒 Kibanda Produce Performance":
- Greens/Batch (BUNCH): from ProduceBunch — revenue, cost, markup×, wastage
- Other produce (PORTION): from Transaction — units sold, revenue, cost, margin%

---

## Notification System (Complete)
- SMS: Africa's Talking live (normalize_ke_phone: 07XX → +254XX)
- Email: Resend API only (SMTP port 587 BLOCKED on Render free tier — never use send_mail)
- In-app: Notification.objects.create() with related_name='app_notifications'
- NotificationRouter: route_notification() with event-type → channel rules
- SMS bundling: Business.last_txn_sms_at, 10-min rate limit

---

## Staff Permissions
Per-staff toggles at /staff/<id>/permissions/:
- can_input_cost_price: staff sees cost input on Receipt (not previous cost)
- can_override_restrictions: staff bypasses ItemSaleApproval workflow

---

## Reserved / Protected Items
Item.is_restricted → staff → ItemSaleApproval (pending/approved/denied) → owner notified.
restricted_quantity=0: ALL sales need approval. N: staff free until balance drops below N.

---

## UI Theme — Dark Luxury
```css
--onyx: #1a1a1a; --onyx-card: #2a2a2a; --gold: #c9a84c; --gold-light: #e2c36e;
--pearl: #f0ece4; --raspberry: #c0395a; --raspberry-dark: #8b1a35;
```
Fonts: Playfair Display (headings), DM Sans (body)

### CRITICAL THEME RULES
1. NEVER `class="text-muted"` → use `style="color: #b0b0b0"`
2. NEVER Bootstrap bg classes on cards
3. NEVER `{% trans 'string' %}` wrapped across lines by formatters
4. NEVER Gmail SMTP — Resend API only
5. NEVER `{% trans %}` in single-quoted JS strings → use double-quoted JS strings
6. `btn-gold` for primary actions, never `btn-primary`
7. `style="color: #b0b0b0"` for muted text (var(--muted) is invisible)
8. `.dropdown-menu` has `max-height: 80vh; overflow-y: auto` — never remove
9. Mobile navbar collapse has `max-height: 75vh; overflow-y: auto` — never remove

---

## Coding Preferences
- Always output COMPLETE files — never use `...` or `# unchanged`
- One file at a time — state what changed
- Never truncate — complete every file fully
- No Django template formatters — Prettier breaks `{% trans %}` tags

---

## Settings (stockapp/settings.py)
```python
CSRF_TRUSTED_ORIGINS = [
    'https://dukamwecheche.co.ke',
    'https://www.dukamwecheche.co.ke',
    'https://stock-made-simpler-sms.onrender.com',
]
SESSION_COOKIE_AGE = 86400        # 24 hours (retail owners leave app open all day)
SESSION_SAVE_EVERY_REQUEST = True  # Prevents CSRF token mismatch after cold starts
```
DEBUG = True currently (should be changed to False for production eventually).

---

## Geography
All 47 Kenya counties, sub-counties, wards seeded via data migrations.
County model lives in core (not accounts). Customer.county FK to core.County, SET_NULL.

---

## Features Built (Complete)

### Core Inventory
- Stock list with store/status filters
- Add Transaction (Receipt/Issue/Wastage) with cost price, landed cost, yield processing
- Transaction history with Excel export
- Quick Sell POS (cart-based, M-Pesa/cash/credit)

### Kibanda Produce Module (COMPLETE — see full section above)
All features built and deployed including:
- ProduceBunch revenue-envelope model (greens AND sack goods)
- PORTION mode multi-piece pricing (tatu mbao, nne mbao, gorogoro pre-portioned)
- Greens board, mix tile with kienyeji chip selector, Done/Futa cart UX
- +From market modal with gunia/gorogoro distinction for sack items
- Smart unit hints in item form (UNIT_MAP lookup by description)
- Analytics "Kibanda Produce Performance" (BUNCH by ProduceBunch + PORTION by Transaction)

### Analytics & Reporting
- Sales & P&L dashboard, ETS/Holt-Winters forecasting
- Kibanda Produce Performance section
- Break-even analysis, Capital investments tracker
- County-level sales heatmap (Leaflet choropleth)

### Revenue Targets — daily/weekly/monthly per business and per store

### Debt Tracker — FIFO balance, aged buckets, credit score, payment recording

### Staff Permissions, Reserved Items, Business Management (multi-store, role-based)

### Supply Chain — supplier portal, rider portal, procurement (POs, bids, scoring)

### Payments — Till/Paybill/Pochi/M-Pesa, STK Push, payment method tracking

### Onboarding — modal tutorial (4 role variants) + Driver.js spotlight tours (17 templates)

---

## Next Sprint Candidates
1. **Keg bar reconciliation** — yield-based items for bars (keg → pints, waste-adjusted profit)
2. **Shift handover module** — opening float, closing balance, cash reconciliation per shift
3. **Expiry date tracking** — pharmacy/perishables module
4. **Business-type aware UI** — dynamic form labels/fields by business type (Phase B)

---

## Important Patterns

### Multi-tenancy
Every queryset scoped to `request.user.userprofile.business`. Never query without business filter.

### Notification Creation
```python
Notification.objects.create(business=business, user=user, message="...")
# Query: user.app_notifications.filter(is_read=False)
```

### Revenue Target Colors
Compute in view via `_build_target_data(actual, target)` → {color, pct}.
Never use `{% widthratio %}` — unreliable in Django templates.

### Template Structure
```html
{% extends "base.html" %}
{% block title %}{% endblock %}
{% block extra_css %}<style>...</style>{% endblock %}
{% block content %}{% endblock %}
{% block page_tour %}{% endblock %}
{% block extra_js %}<script>...</script>{% endblock %}
```

---

## Known Issues / Watch Points
- `Store.__str__` must handle null business gracefully
- `Notification` uses `related_name='app_notifications'` — always use this
- `{% trans %}` tags break if formatter wraps them across lines
- `{% trans "You're..." %}` must use double-quoted JS string wrapper
- Render free tier blocks SMTP — never use Django's email backend
- `iterator(chunk_size=10)` for memory-heavy operations (SIGKILL risk)
- `UserInBlacklist` AT error = no Sender ID for Safaricom (KES 8,700 one-time fee)
- `float * Decimal` raises TypeError — always cast: `float(x) * float(y)`
- `_units()` uses `produce_bunch_id` (not `sale_amount`) to identify batch sales.
  Both bunch AND portion preset sales have `sale_amount` set (since commit fbff5b4).
- `analytics_dashboard` decorators (`@login_required`, `@owner_required`) must be
  DIRECTLY above the view function — never insert helpers between them.
- `produce_board()` must include `unit` in the greens dict for the receive modal to
  correctly detect greens (unit=Bunch) vs sack items (unit=Gorogoro) and show the
  appropriate gunia/gorogoro toggle.

## End-of-sprint ritual:
run python manage.py check and makemigrations --check, commit as 'Sprint N: summary', push to main, append a one-line status update to this file."

## Sprint Status Log
- Sprint 4 (2026-06-13): Shift Handover Module complete — middleware enforcement, barrel weigh-in at shift change (SHIFT_CLOSE/SHIFT_OPEN), offline sales capture (Option A: shift-level adjustment), backdated transaction entry (Option B: created_at override), shift history with reconciliation. Next: Waitress Order Queue.
- Sprint 5 (2026-06-13): Waitress Order Queue complete — TableOrder + TableOrderItem models, 'waitress' role, mobile Order Desk screen (table chips, item/preset tiles, cart, place order), bar board queue drawer (Accept→Ready→Served, auto-poll 20s, badge count), SERVED auto-creates Issue transactions. Next: Expiry Date Tracking or Business-type aware UI.
- Sprint 5 fixes (2026-06-14): Jug tracking (ItemPortionPreset.is_jug + KegBarrel.jugs_dispensed, position-based save, bar board panel); add-staff role field rendered (fixed blank-password validation loop); Quick Sell preset modal for non-produce items with presets (spirits quarters/halves); selling_price auto-fills full-unit preset price on item form.
- Sprint 5 fixes cont. (2026-06-14): serving_type field (cup/pint/jug) on ItemPortionPreset + pints_dispensed on KegBarrel + keg_serving/keg_qty on Transaction; daily bar report with cups/pints/jugs/revenue per barrel; waitress performance table (orders served + revenue); staff/shift performance table (duration, cups/pints/jugs/revenue per shift window); shift gate blocks waitress orders when no OPEN shift; bar board shows active waitresses on-duty panel.
- Sprint 6 (2026-06-14): Keg Bar Reconciliation complete — /bar/reconciliation/ with date/status filters, per-barrel P&L (wastage L/KES/%, book vs scale), barrel detail page (theoretical max from presets, target assessment shortfall card, per-shift weight-bracketed variance, weight readings log), target recommendation hint in receive modal. Next: Digital Receipts or Business-type profiles.
- Sprint 7 (2026-06-15): Recurring Expenses complete — RecurringExpense model (MONTHLY/QUARTERLY/ANNUAL, per-staff salary lines), last_expense_review_date on Business, full CRUD manage page, period review flow (confirm + auto-post BusinessExpense idempotently), home page gold banner at first login each period, SMS+email on confirm, monthly investment nudge. Expense Intelligence page (/analytics/expenses/report/) added: 12-month trend chart (revenue vs expenses), category stacked bar, per-line history table (trend %, avg % of revenue, colour-coded badges), auto-generated insight flags.
