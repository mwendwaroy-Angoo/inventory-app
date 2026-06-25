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
current_session_key (CharField max_length=40 blank)  # updated on every login by user_logged_in signal
allow_concurrent_sessions (BooleanField default False)  # set True via Django admin for dev/testing bypass
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
- NEVER name any variable `_` anywhere in Python code (loop unpacking, get_or_create
  results, etc.) — `_` is reserved for `gettext_lazy as _`. Reusing it silently shadows
  the translation function and causes `TypeError: 'X' object is not callable` deep in
  unrelated code later in the same file, often nowhere near the actual mistake. This
  caused at least three separate production crashes (Sprints 9-11: send_debt_reminder,
  a get_or_create unpack, a produce IIFE guard). Always use a real name: `_unused`,
  `_created`, `_discard`.
- When a bug takes more than one attempted fix across sessions, record the actual ROOT
  CAUSE in the Known Issues section below once found — not just the symptom — so the
  next session doesn't re-walk the same dead ends. Example: the bar preset dropdown was
  patched three different ways (CSS class hiding, a Django template guard, a JS
  ternary) before discovering the real cause: jQuery/Select2 was never loaded on
  item_form.html at all.
- **Before marking any fix "done" — regression sweep**: Search the whole codebase for
  every call site that reads or writes whatever model field, settings value, or shared
  function you just changed — not just the one you were fixing. If you changed
  `_get_urls()`, grep every caller. If you changed a model field default, grep every
  reader. Confirm each still behaves correctly before calling the sprint done. This is
  what caught the `MPESA_ENV` routing bug (Sprint 18): three functions all called
  `_get_urls()` with no env awareness, silent until audited together.
- **Everything in this app is connected — audit ALL surfaces, not just the one you touched**:
  When a field value (e.g. `current_balance`) is changed or the meaning of its data
  changes (e.g. can now be negative), grep every template and view that reads that field
  and verify each surface behaves correctly. A "display fix" in Quick Sell is incomplete
  if the same field is also shown in analytics, stock velocity ranking, expiring items,
  reorder table, sales dashboard, item detail, and add_transaction dropdown — they must
  ALL be audited in the same change. Example: fixing the negative-balance display in
  quick_sell.html (showing "Out of Stock" instead of -22) without also checking
  analytics_views.py left -22.0 showing in the Stock Velocity Ranking "Current Stock"
  column. Roy noticed. Before closing any fix, run:
    `grep -rn "current_balance\|\.balance" templates/`
  and inspect every hit. The rule of thumb: one logical bug has N display surfaces —
  fix them all or the inconsistency will confuse a business owner who uses multiple
  pages of the same app.
- **Run `python manage.py test` before every push** (baseline suite in `core/tests.py`).
  Highest-priority paths: STK Push URL/env routing per business, Receipt gap-free
  numbering, Quick Sell checkout (all three payment methods), keg sale + reconciliation
  arithmetic, bar tab settlement. Add a test whenever a silent regression costs real
  money or a client's trust — not after the fact.
- **Always commit and push at the end of every task, without exception.** Do not wait
  to be asked. After the last code change is made and tests pass: `git add` the
  changed files, commit with a descriptive message following the repo style
  (`feat:`/`fix:` prefix), then `git push origin main`. This is the final step of
  every task — treat it the same as running tests.
- **When adding any new module or feature, proactively audit ALL connected app surfaces
  before marking it done — do not wait for Roy to notice gaps.** The surfaces to check
  for every new selling/payment feature are: (1) Debt tracker — does credit flow
  produce Transaction(payment_method='credit', recipient=name) so the debt tracker
  picks it up? (2) Receipts — does cash/mpesa/credit issue a Receipt and appear in the
  receipts list? (3) SMS — does credit send the debt confirmation SMS to the customer
  (same as Quick Sell does)? (4) Analytics — does revenue appear in the correct section
  and NOT bleed into unrelated sections (e.g. kitchen batch items must not appear in
  Kibanda Produce Performance)? (5) Home dashboard — does today's revenue show on the
  right tile, not merged with a different module's figure? (6) Revenue targets — does
  revenue count toward the owner's daily/weekly/monthly targets? (7) Expiry alerts —
  do items in the new store/module show in expiry warnings? (8) Tabs → debt conversion
  — if the feature has tabs, is there a "Convert to Deni" path? Root cause of the
  Sprint 21 gap: kitchen module launched without a direct Deni option and without a
  "Convert to Deni" button on food tabs — Roy had to point it out.

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
DEBUG = False (fixed 2026-06-17). Watch point: SECRET_KEY falls back to a hardcoded
insecure default if the SECRET_KEY env var isn't set on Render — confirm it's set in
the Render dashboard env vars; never rely on the fallback in production.

---

## Geography
All 47 Kenya counties, sub-counties, wards seeded via data migrations.
County model lives in core (not accounts). Customer.county FK to core.County, SET_NULL.

---

## Features Built (Complete)

### Core Inventory
- Stock list with store/status/expiry filters; expiry column (EXPIRED/EXP SOON/OK badges)
- Add Transaction (Receipt/Issue/Wastage) with cost price, landed cost, yield processing, expiry date
- Transaction history with Excel export
- Quick Sell POS (cart-based, M-Pesa/cash/credit); preset modal for spirits/non-produce items

### Kibanda Produce Module (COMPLETE — see full section above)
All features built and deployed including:
- ProduceBunch revenue-envelope model (greens AND sack goods)
- PORTION mode multi-piece pricing (tatu mbao, nne mbao, gorogoro pre-portioned)
- Greens board, mix tile with kienyeji chip selector, Done/Futa cart UX
- +From market modal with gunia/gorogoro distinction for sack items
- Smart unit hints in item form (UNIT_MAP lookup by description)
- Analytics "Kibanda Produce Performance" (BUNCH by ProduceBunch + PORTION by Transaction)

### Keg Bar Module (COMPLETE)
- Bar board POS: keg tapping, pint/jug/cup presets, tab management, waitress order queue
- Shift handover: middleware enforcement, barrel weigh-in at shift change, offline/backdated sales
- Keg reconciliation (/bar/reconciliation/): per-barrel P&L, wastage %, book vs scale variance
- Bar Performance analytics: per-barrel table, pouring league, tab aging buckets
- Daily bar report: cups/pints/jugs/revenue per barrel, waitress performance, staff/shift performance
- Shift history, active waitress on-duty panel

### Recurring Expenses & Expense Intelligence
- RecurringExpense model (MONTHLY/QUARTERLY/ANNUAL, per-staff salary lines)
- Period review flow (confirm + auto-post BusinessExpense idempotently)
- Home page gold banner at first login each period; SMS+email on confirm
- Expense Intelligence page (/analytics/expenses/report/): 12-month trend chart, category stacked bar, insight flags

### Digital Receipts (COMPLETE)
- Receipt model (token, receipt_number, lines JSONField, customer_name/phone, payment_method)
- Public receipt page (/r/<token>/): QR code, Print, Share, Send SMS
- Receipts list (/receipts/): month/year/customer-name filter, accessible to all staff
- Auto-issued on: Quick Sell, bar board sales, debt payments
- Partial payment "⚠️ Bado unalipa KES X" block (qty=-1 line variant, raspberry styling)
- "Powered by Duka Mwecheche" footer on public receipt (hidden on print)

### Debt Tracker (COMPLETE)
- FIFO balance, aged buckets (current/30/60/90+), credit score, per-customer expected_payment_days
- Credit sales in Quick Sell: recipient set, Customer auto-created, SMS confirmation to customer
- Keg tab sales: recipient + Customer auto-created, payment_method='credit' on receipt
- Debt payment receipt: FIFO line items showing original transactions, post-payment credit score,
  "umelipa leo / umelipa siku N baadaye (kiwango siku W)" days label
- send_debt_reminder: uses send_sms_notification (AT live), Swahili message
- Per-customer credit settings accessible to all staff (not owner-only)

### Expiry Date Tracking (COMPLETE)
- Transaction.expiry_date DateField (migration 0056), set on Receipt batches
- Add Transaction form: date picker visible for Receipt type only
- Stock list: Min(expiry_date) per item annotated; EXPIRED/EXP SOON/OK badges; expiring filter
- /stock/expiring/: full report grouped EXPIRED → EXPIRING SOON → OK, with balance + days label
- Home dashboard: raspberry EXPIRED alert + amber EXPIRING SOON alert, visible to all staff

### Analytics & Reporting
- Sales & P&L dashboard, ETS/Holt-Winters forecasting
- Kibanda Produce Performance, Bar Performance sections
- Break-even analysis, Capital investments tracker
- County-level sales heatmap (Leaflet choropleth)
- Expense Intelligence page

### Revenue Targets — daily/weekly/monthly per business and per store

### Staff Permissions, Reserved Items, Business Management (multi-store, role-based)

### Supply Chain — supplier portal, rider portal, procurement (POs, bids, scoring)

### Payments — Till/Paybill/Pochi/M-Pesa, STK Push, payment method tracking

### Business-Type Profiles (Sprint 8)
- business_profiles.py registry (8 profiles + item catalogs)
- Context processor injects biz_profile into every template
- Navbar gating: Bar Board/Shifts only for keg businesses
- Quick Sell redirect for bar; item form Select2 catalog picker

### Onboarding — modal tutorial (4 role variants) + Driver.js spotlight tours (17 templates)

---

## M-Pesa / Payments Architecture — read before touching any payment code

Hard boundary, never cross it: Duka Mwecheche must NEVER hold, pool, or pass customer
money through any account it controls, not even briefly. The moment money from
multiple different businesses' customers flows through one Duka-Mwecheche-owned
Paybill/account before reaching the business, that crosses into Central Bank of Kenya
Payment Service Provider territory (National Payment System Act 2011), which requires
a CBK PSP authorization with real capital requirements (KES 5M+ depending on category)
and a full regulatory application. Not appropriate for this app — ever, unless the
business model fundamentally changes. Money always settles directly into the
individual business owner's own Till/Paybill/Pochi. Duka Mwecheche is a reconciliation
and prompting layer on top of payments the owner already receives directly, never an
intermediary holding funds.

Two payment tiers, in priority order:

Tier 0 — static M-Pesa QR (build first): generate a standard EMVCo
Merchant-Presented-Mode QR code client-side, encoding the business's own
Till/Paybill (+ account number for Paybill) and the exact sale amount. Customer scans
with their own M-Pesa app — no Daraja API call, no go-live process, no consumer
key/secret, works the moment a business has ANY Till or Paybill (nearly all already
do). This should replace the current "QR links to a payment instructions page"
approach with a true EMVCo payload the M-Pesa app decodes directly, saving the
customer a step. Reconciliation stays manual (staff marks payment_method=mpesa +
optional transaction code) — already built, already fine for this tier.

Tier 1 — per-owner Daraja STK Push / C2B (optional upgrade, built Sprint 13): each
business owner goes through Safaricom's go-live process for THEIR OWN shortcode (never
Duka Mwecheche's) and pastes their resulting consumer key/secret into Payment Settings.
Duka Mwecheche calls RegisterURL/STK Push using the OWNER's credentials, so settlement
still goes straight to them — Duka Mwecheche itself never needs a production shortcode
under this model. This unlocks real-time auto-reconciliation but has real Safaricom
paperwork friction per business (more for Paybill than Till). Treat it as an opt-in
upgrade a technical owner can self-serve, or that Roy walks a less technical owner
through personally as part of onboarding — never a requirement to use the rest of the
app.

---

## Next Sprint Candidates
1. **Business-type theming** — per-type accent color, icon sets, home hero personalisation (Sprint 13+). Bar first, then kibanda, then rest. See session prompt in sprint log notes.
2. **Business-type aware UI Phase B** — dynamic form labels/fields by business type (6-8 sprints, new session)
3. **FIFO batch depletion** — per-batch stock tracking for pharmacy/perishables (follow-on to expiry tracking)
4. Payments Tier 0 — static M-Pesa EMVCo QR generator (replaces link-based QR on the
   payment page and bar board success modal). See M-Pesa / Payments Architecture
   section above before starting.
5. **Quick Sell cart → STK Push (Daraja Tier 1 — PENDING)**: When customer selects
   M-Pesa at Quick Sell checkout, initiate STK Push for the cart total and
   auto-complete the sale on callback. Architecture:
   - Create a draft Order from the cart before initiating STK Push
   - Pass order_id to stk_push_view → Payment.order FK set
   - On mpesa_callback success: existing _settle_order_from_payment() (needs writing)
     creates Issue transactions for each cart line, issues Receipt, clears the cart
   - Mirror of what bar tab STK Push does (Sprint 15) — that's the working template
   - Prerequisite: business must have daraja_consumer_key + daraja_secret + daraja_passkey
     saved in Payment Settings (Business.daraja_* fields, migration 0029). Already stored.
   - Reminder: remind Roy to start this sprint when a business requests STK-at-checkout

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
- `Customer` has NO `unique_together` on `(business, name)`. Never use `get_or_create(business=x, name=y)` — if duplicate Customer rows exist, Django raises `MultipleObjectsReturned`. Always use `filter(business=x, name=y).first()` and create only if None. ROOT CAUSE of the production 500 on keg tab sales (2026-06-19): bar_board used get_or_create, production DB had two Customer rows with same business+name from earlier test sessions.
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
- item_form.html does NOT load jQuery/Select2. Any picker/dropdown/typeahead UI on
  that template must be vanilla JS (see the catalog picker rewrite, commit c4020e3) —
  do not add new Select2() calls there.
- NEVER put `@login_required` on JSON/AJAX endpoints (notifications_count, API views).
  When an unauthenticated AJAX poll hits such an endpoint, Django sets `?next=<endpoint>`
  on the login URL. After login the user gets redirected to a JSON response instead of
  the dashboard. ROOT CAUSE of the 2026-06-17 login loop: `notifications_count` had
  `@login_required` → 30-second poll expired session → `?next=/notifications/count/`
  on login page → user redirected to JSON after login. Fix: return `{"count":0}` for
  unauthenticated requests instead.
- Service Worker MUST NOT cache redirected responses. If a SW `fetch()` follows a
  redirect (e.g. server redirects to `/accounts/login/`), `response.redirected === true`.
  Caching that response with `cache.put(originalRequest, response)` stores the login
  page HTML at the original URL key. ALWAYS guard caching with `!response.redirected`.
  Fixed in duka-v6 SW (both navigate and stale-while-revalidate handlers).
- SW PRECACHE_URLS must not include auth-gated URLs (e.g. `/`). During SW install
  the user may not be logged in; `cache.addAll(['/'])` would then store the login-redirect
  response at `/`. Removed `/` from PRECACHE_URLS in duka-v6.
- iOS PWA ("Add to Home Screen"): iOS Safari NEVER fires `beforeinstallprompt`.
  The iOS install banner must detect iOS UA + Safari + non-standalone and show manual
  instructions ("Tap Share ⬆️ then Add to Home Screen"). The existing Android banner
  (based on `beforeinstallprompt`) does nothing on iOS.
- iOS PWA manifest icons: do NOT use `"purpose": "any maskable"` (combined value).
  Split into two separate entries — one `"purpose": "any"` and one `"purpose": "maskable"`.
  The combined value causes rendering issues on some iOS Safari versions.
- EMVCo QR (generate_emv_qr_string in mpesa.py): builds a Safaricom MPMQR TLV string with
  CRC16-CCITT. MANDATORY before marking done: Roy must test-scan the generated QR with
  real M-Pesa app, KES 1 transaction, verify correct till number and amount prefill. The
  Daraja Dynamic QR API (sandbox creds with prod till) fails in prod; EMVCo is the real
  Path 2 fallback. If scan doesn't work, check: tag 26 sub-tag domain string, CRC calc,
  or try static initiation method 11 → 12.
- `bar_board.html` `post()` helper sends form-encoded data (URLSearchParams + CSRF token).
  `/mpesa/stk-push/` expects JSON (json.loads). Tab STK Push uses raw `fetch` with
  Content-Type:application/json instead of the `post()` helper — this is correct and
  intentional. Do not convert it to use `post()`.
- Daraja per-business STK Push (post-Sprint 18): `initiate_stk_push()`,
  `query_stk_status()`, and `register_c2b_url()` in mpesa.py now accept an `env`
  kwarg ('sandbox'|'production') alongside the per-business credential kwargs.
  `_get_urls(env=None)` and `_get_access_token_for(..., env=None)` both thread env
  through. `stk_push_view`, `payment_status`, and `register_business_c2b` all pass
  `env=business.daraja_environment`. `Business.daraja_environment` (accounts migration
  0031, default='sandbox') is toggled in Payment Settings. ROOT CAUSE of the original
  bug: `_get_urls()` was called without env awareness so all API calls went to sandbox
  even when per-business production credentials were configured.
- Daraja TransactionType for Till (Buy Goods) = `CustomerBuyGoodsOnline`. For Paybill
  = `CustomerPayBillOnline`. mpesa.py currently uses `CustomerBuyGoodsOnline` in
  initiate_stk_push — correct for Till. If a business has only a Paybill (no Till),
  the TransactionType must change to `CustomerPayBillOnline`. Add logic when building
  the payload: check whether shortcode matches mpesa_till or mpesa_paybill.

## End-of-sprint ritual:
run python manage.py check and makemigrations --check, commit as 'Sprint N: summary', push to main, append a one-line status update to this file."

## Sprint Status Log
- Sprint 4 (2026-06-13): Shift Handover Module complete — middleware enforcement, barrel weigh-in at shift change (SHIFT_CLOSE/SHIFT_OPEN), offline sales capture (Option A: shift-level adjustment), backdated transaction entry (Option B: created_at override), shift history with reconciliation. Next: Waitress Order Queue.
- Sprint 5 (2026-06-13): Waitress Order Queue complete — TableOrder + TableOrderItem models, 'waitress' role, mobile Order Desk screen (table chips, item/preset tiles, cart, place order), bar board queue drawer (Accept→Ready→Served, auto-poll 20s, badge count), SERVED auto-creates Issue transactions. Next: Expiry Date Tracking or Business-type aware UI.
- Sprint 5 fixes (2026-06-14): Jug tracking (ItemPortionPreset.is_jug + KegBarrel.jugs_dispensed, position-based save, bar board panel); add-staff role field rendered (fixed blank-password validation loop); Quick Sell preset modal for non-produce items with presets (spirits quarters/halves); selling_price auto-fills full-unit preset price on item form.
- Sprint 5 fixes cont. (2026-06-14): serving_type field (cup/pint/jug) on ItemPortionPreset + pints_dispensed on KegBarrel + keg_serving/keg_qty on Transaction; daily bar report with cups/pints/jugs/revenue per barrel; waitress performance table (orders served + revenue); staff/shift performance table (duration, cups/pints/jugs/revenue per shift window); shift gate blocks waitress orders when no OPEN shift; bar board shows active waitresses on-duty panel.
- Sprint 6 (2026-06-14): Keg Bar Reconciliation complete — /bar/reconciliation/ with date/status filters, per-barrel P&L (wastage L/KES/%, book vs scale), barrel detail page (theoretical max from presets, target assessment shortfall card, per-shift weight-bracketed variance, weight readings log), target recommendation hint in receive modal. Next: Digital Receipts or Business-type profiles.
- Sprint 7 (2026-06-15): Recurring Expenses complete — RecurringExpense model (MONTHLY/QUARTERLY/ANNUAL, per-staff salary lines), last_expense_review_date on Business, full CRUD manage page, period review flow (confirm + auto-post BusinessExpense idempotently), home page gold banner at first login each period, SMS+email on confirm, monthly investment nudge. Expense Intelligence page (/analytics/expenses/report/) added: 12-month trend chart (revenue vs expenses), category stacked bar, per-line history table (trend %, avg % of revenue, colour-coded badges), auto-generated insight flags.
- Sprint 8 (2026-06-15): Business-Type Profiles complete — business_profiles.py registry (8 profiles + catalogs), context processor, migration 0054 (new business types), navbar gating (Bar Board/Shifts only for keg businesses), Quick Sell redirect for bar, item form Select2 catalog picker.
- Sprint 9 (BAR_MODULE_SPEC Sprint 6, 2026-06-15): Kibanda kg fixes (Kg UNIT_MAP entries for nyanya kg/vitunguu kg/omena/sukari kg before generic piece entries), cost-price hiding on item form for produce/keg items (costPriceHint div + window._updateCostPriceVisibility), Bar Performance analytics enhancements — per-barrel P&L table with book-vs-scale shrinkage %, pouring league (staff keg revenue), tabs aging buckets (same-day / 1-3 / 4-7 / 7+ days). Next: RECEIPTS_BARCODE_SPEC Sprint 7 (Digital Receipts).
- Sprint 10+11 (2026-06-15): Digital Receipts + Debt Tracker parity — Receipt model (token, QR, SMS send); Quick Sell credit sales linked to debt tracker (recipient set, Customer auto-created); keg tab sales linked to debt tracker (recipient + Customer auto-created, payment_method='credit' on Receipt); debt payment receipt: FIFO line items, redirect to receipt page, auto-SMS customer, score computed post-payment, days label "umelipa leo/siku N baadaye (kiwango siku W)"; send_debt_reminder fixed to use send_sms_notification; Receipts history page (/receipts/) with month/year/customer filter, accessible to staff; partial payment "Bado unalipa KES X" block on receipt; "Powered by Duka Mwecheche" on public receipt; credit settings form open to staff. Next: Expiry Date Tracking.
- Sprint 12 (2026-06-15): Expiry Date Tracking — Transaction.expiry_date (migration 0056); Add Transaction form shows date picker for Receipt type; stock_list annotates items with earliest expiry (single Min query), EXPIRED/EXP SOON/OK badges in Expiry column, expiring filter link; /stock/expiring/ report grouped EXPIRED→EXPIRING SOON→OK with balance + days label; home dashboard raspberry/amber alert banners linking to report, visible all staff. Next: Themes discussion, then Business-Type Aware UI Phase B (new session).
- Sprint 13 (2026-06-16): Bar business-type visual theming (whiskey amber #C8752A accent via --biz-accent CSS vars, biz-bar body class, bar hero Tonight stats, navbar 🍺 prefix + "Bar Orders"); stock_list underscore template variable fix (_expiry_status→expiry_status); shift reconciliation revenue fix (SQL CASE/WHEN replaces Sum('sale_amount') which missed non-preset sales); dashboard revenue targets now show actual KES even without target set; bar hero revenue from DB context not JS. M-Pesa C2B registration — Business.daraja_consumer_key/secret/c2b_registered fields (migration 0028), register_c2b_url() in mpesa.py, register_business_c2b view, payment settings UI with per-business Daraja credentials + one-click "Register with Safaricom" button. Next: Business-Type Aware UI Phase B or per-type theming for kibanda.
- Sprint 14 (2026-06-17): Login loop fix — removed @login_required from notifications_count (returns {"count":0} for anon), bumped SW to duka-v6 with !response.redirected guard, removed "/" from SW precache. Bar QR Scan-to-Pay (Tier 0 static EMVCo QR via Daraja Dynamic QR API, fallback URL QR); bar tab unified for keg+spirits; Quick Sell bar "Tab" vs "Deni" split. Bar tab now accepts table number as customer identifier (placeholder updated in both quick_sell and bar_board). ShiftStockCount model (migration 0057) + stock_take_api view (/bar/shift/<id>/stock-take/) — end-of-shift physical item count with book vs actual vs variance, triggered from shift close modal. iOS PWA: manifest icons split "any maskable" → separate "any"/"maskable" entries, added 120x120 apple-touch-icon, fixed 167x167 to use icon-192, iOS-specific "Tap Share → Add to Home Screen" install banner (detects iOS UA + non-standalone). Next: Business-Type Aware UI Phase B or per-type kibanda theming.
- Sprint 15 (2026-06-18): STK Push pipeline fixes — (1) Bridge: mpesa_callback + payment_status now call _bridge_stk_to_prompt() to create PendingTransactionPrompt for manual STK pushes (no order/tab); idempotent via mpesa_receipt guard. (2) Poll timeout: extended from 12×5s (60s) to 24×5s (2 min) with visible amber message on timeout in both pending_prompts.html and business_payment_page.html. (3) Pay-tab STK Push: Payment.bar_tab FK (migration 0058), stk_push_view accepts tab_id, _settle_tab_from_payment() does FIFO BarTabEntry settlement + Receipt.issue on full settlement; tabs drawer "📲 STK Push" button + tabStkModal with 2-min polling. (4) EMVCo QR: generate_emv_qr_string() in mpesa.py builds Safaricom MPMQR TLV string (CRC16-CCITT); mpesa_qr_view returns mode=emv between Daraja img fail and URL fallback; payment page renders with qrcodejs — Roy must test-scan with real M-Pesa app. Next: EMVCo scan test, then Business-Type Aware UI Phase B.
- Sprint 16 (2026-06-18): Per-business Daraja credentials complete — Business.daraja_passkey (accounts migration 0029); initiate_stk_push() + query_stk_status() in mpesa.py now accept per-business consumer_key/secret/shortcode/passkey kwargs, fall back to global settings; use_till flag sets correct TransactionType (Buy Goods vs PayBill); stk_push_view + payment_status pass business credentials; Payment Settings UI adds Passkey field; channels form now preserves daraja fields via hidden inputs (was silently erasing them on save). Receipt + auto-SMS on prompt confirmation; portion presets in confirm form + sale_amount fix. Pending: Quick Sell cart → STK Push (see Next Sprint Candidates #5).
- Sprint 17 (2026-06-18): Bar board mobile layout fix — header buttons now wrap on small screens (title on its own line, flex-wrap on button row) so Reconciliation/Daily Report/Pokea Barrel no longer overflow on phone. Single-session enforcement — UserProfile.current_session_key + allow_concurrent_sessions (accounts migration 0030); accounts/signals.py writes session key on user_logged_in; SingleSessionMiddleware in accounts/middleware.py kicks stale sessions on next request with bilingual warning; Django superusers always exempt. Roy must set allow_concurrent_sessions=True on his own UserProfile via Django admin (/admin/) to allow multi-device dev testing. STK Push in tabs: bar board tabs have full STK push (Sprint 15). Quick Sell tabs = credit/deni only, no STK push (that is Sprint Candidates #5). Debt reminder confirmed correct (send_sms_notification, message first param).
- Sprint 18 (2026-06-18): M-Pesa env routing fix — Business.daraja_environment CharField (accounts migration 0031, default='sandbox'); _get_urls(env=None), _get_access_token_for(..., env=None), initiate_stk_push/query_stk_status/register_c2b_url all accept env kwarg; stk_push_view + payment_status + register_business_c2b pass env=business.daraja_environment; Payment Settings UI adds Sandbox/Production toggle with explanation. Baseline automated test suite (core/tests.py): 12 tests covering STK Push URL routing per env, OAuth token cluster, query_stk_status routing, Receipt sequential numbering and per-business isolation. Regression discipline added to CLAUDE.md (sweep all callers before marking done; run tests before push).
- Sprint 19 (2026-06-21): Revenue bug fixes + QS tab actions parity. (1) bar_today_revenue: switched date filter from _ddate.today() (UTC) to timezone.localdate() (Nairobi) — fixes 0-revenue after midnight Nairobi; added payment_method__in=['cash','mpesa'] filter so open/credit tabs are excluded until settle_tab marks them paid. (2) QS tabs drawer: added STK Push, Deni (→ debt), and Void actions matching bar board parity — three modals + JS functions (qsOpenTabStk/qsSendTabStk/_qsPollTabStk, qsOpenTabDebt/qsDoTabDebt, qsOpenTabVoid/qsDoTabVoid); Void is owner-only via QS_IS_OWNER guard. (3) CLAUDE.md: added commit-and-push-always principle to Coding Preferences.
- Sprint 20 (2026-06-22): Kitchen / Grill Module complete — Business.has_kitchen + Store.is_kitchen + BarTab.source='kitchen' (migrations 0062/accounts 0032); UserProfile role='kitchen' + is_kitchen_staff property; KITCHEN_CATALOG in business_profiles.py (chipo presets, chicken portions, smokies, samosas, nyama choma/mutura as BUNCH batch items); core/kitchen_views.py (kitchen_board GET/POST, kitchen_receive, kitchen_tabs_list, toggle_kitchen); kitchen_board.html (tile grid, batch envelope tiles, cart panel, cash/mpesa/food-tab/bar-tab payments, +Pata Stok receive modal, food tabs offcanvas); navbar 🍗 Kitchen link in all 4 sections gated on biz_profile.modules.kitchen; dedicated kitchen-staff navbar (Kitchen + Receipts only); Business Settings enable/disable toggle; add-staff form exposes kitchen role. SW bumped to duka-v7 with /bar/tabs/ in network-first list (Sprint 19 fix for stale-cache drawer bug). 21 tests pass.
- Sprint 21 (2026-06-25): Concurrent shifts + cross-counter tab merge + kitchen module audit. (1) Concurrent shifts: open_shift() constraint changed from per-business to per-staff so bar + kitchen counters run simultaneously; _reconcile() scoped to correct store type (kitchen vs bar) so shift revenues don't bleed; active_shift_api() returns d.shift (user's own) + d.all_shifts (all active); owner dashboard "Active Shifts" badge + per-shift meter strip with 🍺 Bar / 🍗 Kitchen / 🍺+🍗 Both labels and per-shift revenue. (2) Kitchen audit: can_access_kitchen default changed True→False (new staff opt-in); kitchen receipt source='kitchen' tag; kitchen-only staff see only their own receipts; cross-authorized staff see Bar Board link in navbar; receipt list + public receipt show "Served by" staff name. (3) Cross-counter tab merge: kitchen staff adding food tab for a customer who already has an open bar tab sees inline prompt "Ongeza kwa Bar tab hiyo / Fungua Food tab mpya"; bar staff vice versa for open kitchen tabs; merge adds BarTabEntry rows to the EXISTING tab (no new tab created); SMS sent to customer's phone after merge with updated tab total. No new migrations. 12 tests pass.
- Sprint F1 (2026-06-25): Bar tab debt-integrity fixes — STK settlement, void_tab, and convert_tab_to_debt all now flip underlying Transaction.payment_method off 'credit' so phantom debts never persist; void also clears recipient=''. record_sale_locked() classmethod added (select_for_update) — both bar_board and order SERVED handler use it. bar_board() now resolves Customer FK before BarTab creation. Analytics/revenue-targets exclude payment_method='void' throughout. Repo: db.sqlite3/bak/test logs untracked, .gitignore updated. 16 tests pass. Next: Sprint F2 (shrinkage leaderboard + push alerts).
- Sprint B0 (2026-06-25): keg_metrics.py drop-in — centralized book-vs-scale math module with void exclusion in internal queries. Refactored keg_reconciliation (barrel_variance()), keg_barrel_detail per-shift loop (shift_barrel_variance()) and overall wastage, weigh_barrel flag (variance_flag()). Fixed two void-exclusion gaps missed in F1: bar_daily_report and keg_barrel_detail txns queries. 31 tests pass.
- Sprint F2 (2026-06-25): Staff shrinkage leaderboard + push alerts — Business.keg_alerts_enabled + keg_alert_min_litres (migration 0036); _fire_keg_alert() helper (in-app Notification + SMS, 10-min bundling); SPOT alerts gated on volume threshold (F2-AC3); SHIFT_CLOSE always alerts on danger; SHIFT_OPEN overnight mismatch alert > 1.0 kg; bar_shrinkage_report at /bar/shrinkage/ with date-range filter, trend column, coverage% and attribution honesty explainer. 31 tests pass. Next: Sprint F3 (learned foam/spillage baseline).
