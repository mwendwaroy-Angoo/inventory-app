# Duka Mwecheche — Claude Project Context

## Project Overview
Multi-tenant Django inventory and business management web application for Kenyan SMEs.
Live at: https://www.dukamwecheche.co.ke
GitHub: https://github.com/mwendwaroy-Angoo/inventory-app
Deployed on: Render (Starter tier web service) with PostgreSQL database

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
- **Tabs drawer parity — all three drawers must always be in sync.** The bar board
  tabs drawer (`renderTabs` in bar_board.html), the Quick Sell tabs drawer
  (`qsRenderTabs` in quick_sell.html), and the kitchen tabs section
  (kitchen_board.html) share the same `/bar/tabs/` data source and the same UX
  contract. When you fix or enhance ANY ONE of them (stale-tab banner, receipt link,
  per-entry remove, partial settle, cross-notice, etc.) you MUST apply the same fix
  to ALL THREE in the same commit. Never fix one and leave the others with the same
  gap — Roy will notice every time.
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
- **Station Scoping Principle — enforce on every new feature:**
  Bar-only staff (`role in ('staff','waitress')` without `can_access_kitchen=True`) must
  NEVER see kitchen items, kitchen revenue, kitchen shifts, or kitchen tabs. Kitchen-only
  staff (`role == 'kitchen'` without `can_access_bar=True`) must NEVER see bar items, bar
  revenue, bar shifts, or bar tabs. The owner (`is_owner`) and any staff granted
  cross-access (`can_access_kitchen=True` or `can_access_bar=True`) see both/consolidated.
  Enforce at the VIEW layer (queryset filter) AND the TEMPLATE layer (conditional blocks).
  When adding any new feature that touches items, revenue, shifts, tabs, reorder, or
  analytics — ask: "does my queryset and template respect this scoping?" before marking
  the task done. Use the `_station_scope(up)` helper in `core/views.py` which returns
  `(show_bar, show_kitchen)` booleans. The discriminator for items/transactions is always
  `item.store.is_kitchen`; for shifts it is `shift.store.is_kitchen`.

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

### Reset Sales & Analytics (COMPLETE)
- Owner-only, permanent wipe of a business's sales/transaction/analytics history for a genuine
  clean slate, without deleting the business/staff/item catalog (/stock/reset-sales/)
- Two-step: backup workbook download (required first) → type business name to confirm → atomic
  delete across 24 models + zeroed item balances
- Fresh Stock Count checklist (/stock/fresh-count/) guides a real physical recount afterward via
  the existing ⚖️ Rekebisha tool — balances are never frozen from the pre-reset computed value
- SalesResetLog audit trail; marketplace/cross-business models explicitly excluded from the wipe

### Liquor/Spirits Catalogue (COMPLETE)
- BAR_CATALOG enriched from ~60 to 894 entries using a real supplier price list (core/
  liquor_pricelist_catalog.py), via a shared parsing engine (core/catalog_classify.py: column
  detection, volume/category inference, price-tier reorder-level defaults)
- Reusable per-business supplier price-list upload (/stock/catalog/upload/) — any owner can
  upload their OWN Excel/CSV price list at any time; format-independent column detection,
  idempotent re-upload
- Bulk "Add from Catalogue" screen (/stock/catalog/bulk-add/) — search and create several items
  at once (mixing static + uploaded catalog entries) with per-item cost-price confirm/edit and
  an "add portion presets" toggle for pour-by-the-glass items
- `enrich_liquor_catalog` management command for re-running the enrichment against a new price
  list in future; PDF price-list support deferred (see Next Sprint Candidates)

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

### Kitchen Batch Module — Raw Material Sack Tracking (COMPLETE)
Two-level tracking for cooked-to-batch items (chips, stew) so "the sack/gunia is empty" is never
confused with "today's batch is done" — the exact gap Roy flagged after a real Meatco chicken
delivery and an ongoing potato sack. `Item.raw_material_source` (self-FK, optional) points a
batch item (e.g. Chipo) at a real, ordinary trackable Item (e.g. "Potatoes (Raw)", unit=Kg) —
received/tracked via the completely normal Receipt/Issue flow, reusing `current_balance()`,
reorder-level restock alerts, and Rekebisha correction with zero new mechanism. Opening a new
KitchenBatch for such an item asks for "kg used today" instead of a typed cost guess:
`KitchenBatch.open_batch()` (single locked entry point, used by both `kitchen_receive` and
the sibling `kitchen_batch_receive` endpoint) validates the sack has enough balance, creates a
new `Draw`-type Transaction on the raw item (an internal stock movement, NOT a sale — excluded
by construction from every `type='Issue'`-filtered report in the app, no per-report exclusion
list to maintain), and derives `cost_total = kg_drawn × raw_item.cost_price`. Items without
`raw_material_source` set keep the original manual cost-entry flow unchanged — fully opt-in.
Kitchen Board shows the sack's remaining balance directly on the batch tile, independent of
whether today's batch is open; the "Imekwisha" confirm now explicitly says "BATCH YA LEO" to
avoid the same confusion in the confirmation dialog itself. Also fixed while building this: a
real, pre-existing bug in `Transaction.cost()` — no branch existed for `kitchen_batch_id` (only
`keg_barrel_id`/`produce_bunch_id` did), so every sale from a batch returned the batch's WHOLE
`cost_total` instead of a proportional share, overcounting Kitchen Performance / overall COGS by
N× for any batch sold more than once (the normal case). Fixed with the same proportional-share
approach as `keg_barrel_id`, using `revenue_collected` (actual) instead of a fixed target since
KitchenBatch has none. See the Known Issues entry below for the full mechanism.

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
6. **PDF supplier price-list upload** — extend `catalog_upload_process` (core/catalog_views.py)
   to accept a PDF supplier price list, not just Excel/CSV, feeding the same
   `core.catalog_classify` engine (`detect_name_price_columns`/`classify_row`) once the raw
   name/price pairs are extracted. Deliberately deferred out of the 2026-07-21 Liquor Catalogue
   sprint — PDF layouts are far less structured than spreadsheet columns (no reliable cell
   grid to read), needing either a table-extraction library (e.g. `pdfplumber`/`camelot`) or an
   AI-vision-based parse of a scanned/photographed price list, and deserves its own QA pass
   rather than a rushed bolt-on. Start this when a business owner specifically has only a PDF
   price list and no Excel/CSV alternative.

---

## Important Patterns

### Multi-tenancy
Every queryset scoped to `request.user.userprofile.business`. Never query without business filter.

### Notification Creation
```python
Notification.objects.create(user=user, title="...", message="...", notification_type='info')
# No `business` kwarg — Notification has no business field (see Known Issues).
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
- **`Item.cost_price` has exactly ONE designed writer: Add Transaction's Receipt flow
  (`core/views.py:add_transaction`, the "COST PRICE UPDATE (Receipt only)" block).** It
  computes landed cost (unit price + delivery fee ÷ qty), creates a real stock-in
  `Transaction`, notifies the owner, and already shows its own live variance pill/note
  comparing the entered price against the item's previous cost
  (`templates/core/add_transaction.html`, `updateVariancePill()`). No other feature may
  write `item.cost_price` directly — Roy's explicit correction (2026-07-21, building the
  price-variance/reconciliation report): "the add transaction section supersedes
  everything when it comes to receipt info regarding new stock and old stock, just as we
  designed it." A silent field write from anywhere else is an orphaned cost change with
  no stock movement behind it, and risks fighting a real receipt recorded through the
  normal flow. The correct pattern for any feature that *detects* a cost signal (e.g. a
  re-uploaded supplier price list, `core/catalog_views.py:catalog_variance_apply`) is to
  hand off to Add Transaction — pre-fill the item + a suggested "Delivered Unit Price" via
  query params (`?item=<id>&suggested_cost=<price>`, read by an additive, opt-in-only
  block in `add_transaction.html`'s item-typeahead IIFE) — and let the owner complete the
  actual update themselves through the one real mechanism. Never add a second code path
  that writes this field.
  **Pre-existing, deliberate exception**: `KitchenBatch.open_batch()` (formerly inlined in
  `kitchen_receive`) sets `item.cost_price = cost_total` for kitchen batch items specifically
  — one batch IS the per-unit cost here (see `KitchenBatch.discard()`'s docstring, which relies
  on this to price its wastage Transaction correctly). This predates and is unrelated to the
  rule above — batch items never go through Add Transaction's Receipt flow at all. Do not
  "fix" this to match the rule; it would break `discard()`'s wastage math.
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
- **`Transaction.cost()` — kitchen_batch_id must use a proportional formula, never
  `abs(qty) * item.cost_price` (found 2026-07-22, fixed same day, while designing
  raw-material sack tracking).** `KitchenBatch.record_sale()` writes a constant `qty=-1`
  on every sale, and `item.cost_price` is deliberately set to the batch's WHOLE
  `cost_total` (not a per-unit price — `discard()`'s wastage math relies on this). Before
  the fix, `cost()` had no `kitchen_batch_id` branch and fell through to the generic
  `abs(qty) * item.cost_price` path, so EVERY sale from a batch reported cost =
  the entire `cost_total`, repeated per sale — Kitchen Performance and overall COGS were
  overcounting by N× for any batch sold more than once (the normal case), corrupting
  `net_profit` on any business using the Kitchen Batch module. Fixed with the same
  proportional-share pattern already used for `keg_barrel_id`
  (`sale_amount * cost_total / revenue_collected` — using `revenue_collected`, not a
  fixed target, since KitchenBatch has none). `type='Draw'` transactions (raw material
  moved into a batch, not sold) already return 0 from the very first line of `cost()`
  (`if self.type != 'Issue': return 0`) — no special case needed there.
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
- Django template engine BLOCKS access to any attribute whose name starts with `_`.
  Accessing `{{ obj._attr }}` raises `TemplateSyntaxError: Variables and attributes may
  not begin with underscores` → instant 500. ROOT CAUSE of the DJ/MC performer_list 500
  (2026-06-29): view attached `p._sc`, `p._asr`, `p._acr` to model instances; template
  couldn't read them. Fix: always use plain names (`p.stat_count`, `p.stat_staff`, etc.)
  when attaching ad-hoc attributes to objects that will be passed to a template.
- **Business model field bloat (planned refactor — do not do yet):**
  `accounts.Business` currently has ~87 substantive fields covering M-Pesa credentials, keg settings,
  credit policy, cup config, performer settings, SMS flags, and more. This will reach ~120+ fields
  within a few more feature sprints.

  Planned resolution: introduce a `BusinessSettings` model (OneToOneField from Business) that holds all
  feature-config toggles and operational settings, keeping Business itself to identity/structural fields
  (name, type, owner, county, contacts, bank/mpesa shortcodes). Each feature sprint that currently
  adds fields directly to Business should instead add them to BusinessSettings.

  **Do not do this refactor mid-feature.** Schedule it as a standalone migration sprint when the
  next natural break occurs. Until then: continue adding fields to Business as today, but note each
  new feature-config field here as a candidate for the eventual move.

  Current candidates for BusinessSettings: keg_alerts_enabled, keg_alert_min_litres, weighs_kegs,
  block_sales_past_target, cups_per_pint, cups_per_jug, cup_low_notified_at, keg_loss_baseline_pct,
  keg_loss_baseline_sample, credit_policy_enabled, debt_cycle, debt_cutoff_days_before_month_end,
  block_if_overdue, overdue_grace_days, late_repayment_strikes, late_threshold_days, cooldown_days,
  defaulter_permanent, haki_enabled, event_sms_enabled, performer_approval_threshold.
- **`Notification.objects.create()` — widespread `business=` kwarg bug (found 2026-07-15,
  FIXED Sprint K9 2026-07-17).** `core.models.Notification` (core/models.py:189) has no
  `business` field — only `user, title, message, notification_type, is_read, created_at` —
  and `title` has no default. All 8 remaining call sites (`core/shift_views.py:480` —
  also missing `title=` entirely — and `core/debt_views.py:843,971,1038,1110,1120,1203,1224`)
  fixed in Sprint K9; regression-locked by `NotificationShiftOpenTest` +
  `NotificationWriteOffTest` in core/tests.py. Correct call shape:
  `Notification.objects.create(user=X, title=Y, message=Z, notification_type='info'|'warning'|...)`
  — no `business` kwarg. Grep `Notification.objects.create\(\s*\n?\s*business=` before adding any
  new call site — should return zero results.
- **`receipt.meta.get('tab_id')` alone is NOT the test for "does this receipt have a live
  tab" — always use `core.receipt_views._receipt_all_tab_ids(receipt)` instead (found
  2026-07-19 from a real production report: a customer's brand-new, still-open tab showed
  as already paid on their live receipt).** `resolve_master_receipt()` (core/tab_receipts.py)
  can link a tab into a receipt's `meta.linked_tab_ids` (Priority 2/3/4) even when that
  receipt has no `tab_id` of its own — e.g. Priority 4 matches ANY same-day, same-name
  receipt, including an earlier, unrelated, already-completed one-off cash sale. Every
  function that only checked `meta.get('tab_id')` treated such a receipt as "not live" /
  "not a tab": `_get_live_tab_state`, `_get_station_debt_data`, and `receipt_pay` (all in
  core/receipt_views.py) fell back to the OLD receipt's stale static snapshot instead of
  recomputing from the new tab, and `receipt_pay`'s gate 400'd every payment attempt — STK,
  QR, AND cash — outright. Worse: `mpesa_views._create_debt_payment_from_receipt` (the STK
  callback for debt-mode payments) had the identical gate, meaning a debt payment could
  complete on Safaricom's side — the customer's money moves — and then be silently dropped
  by this check, never recorded. All fixed via the shared `_receipt_all_tab_ids()` helper.
  `mpesa_views._settle_tab_from_payment` (staff-initiated full-tab STK settlement) had a
  related but separate bug: it unconditionally issued a brand-new receipt on every full
  settlement instead of checking for an existing master receipt first, orphaning the
  customer's already-known PIN/link — fixed to call `resolve_master_receipt()` like every
  other receipt-issuing call site. Regression-locked by `LinkedOnlyReceiptLiveStateTest` and
  `SettleTabFromPaymentReusesReceiptTest` in core/tests.py. Before adding any new code that
  reads a receipt's tab, grep `meta.get('tab_id')` / `meta\['tab_id'\]` — every READ (not
  write) should go through `_receipt_all_tab_ids()`.

## Cause-&-Effect Protocol (run for EVERY feature or module)

**The map is a required deliverable, not a reading task.** At the start of every sprint, produce
the filled-in Cause-&-Effect Map as the first output — a markdown table with every surface, whether
it is touched, and how. Do not write any code until the map is produced. Roy reviews the map before
code review. This is not optional and is not satisfied by reading this section.

A feature is not its happy path — it is its happy path PLUS every consequence. Before writing code, write a
**Cause-&-Effect Map** in the sprint notes / PR description: a table of every connected surface, whether this
feature touches it, and how. Do not start coding until the map is filled. Missing a row here is the root cause
of nearly every "you forgot X" regression in this project (kitchen debt with no payment path; kitchen shift
with no open/close UI; debt module blind to kitchen vs bar; kitchen M-Pesa routed to the bar till).

**The two dimensions most often missed — check these first:**
1. **Inverse / counterpart actions** — every CREATE needs its RESOLVE, every state its exit:
   debt→record payment · open shift→close shift (with the UI on the right navbar) · open tab→settle/void/
   convert-to-debt · receive stock→discard/adjust · enable→disable. Cause without effect = broken by definition.
2. **Access & visibility scoping** — for every new data surface answer, in the map:
   **Who can SEE it?** **Who can ACT on it?** **Is it partitioned by role AND store AND source?** Respect
   `is_owner`, `is_kitchen_staff`, `can_access_bar`, `can_access_kitchen`. A kitchen-only staffer must never
   see or act on bar data, and vice versa — on the view AND the URL.
3. **Discriminator consistency** — if a separation exists ANYWHERE, reuse the SAME key everywhere. Kitchen vs
   bar = `item.store.is_kitchen`. Bunch vs portion = `produce_bunch_id`. One source of truth.

**Standard surfaces to walk every time** (extend per feature): Debt tracker · Receipts · SMS/notifications ·
Analytics (right section, no bleed) · Home dashboard tiles · Revenue targets · Expiry alerts · Tabs→debt ·
Shift open/close · Navbar links (per role) · Access gate on view AND URL · M-Pesa routing (per counter) ·
Staff contribution/Haki ledger · The inverse action.

Fill the map, implement every "yes" row, then run the regression-sweep grep before marking done.

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
- Sprint F3 (2026-06-25): Learned keg loss baseline — Business.keg_loss_baseline_pct + keg_loss_baseline_sample (migration 0037); _refresh_keg_baseline() fires on KegBarrel.close() (DEPLETED) and auto-DEPLETED in record_sale(), caches result via targeted Business.objects.update(); reconciliation Waste % cell now shows vs-baseline deviation inline (raspberry ▲>5%, amber 0-5%, green ✓≤baseline); barrel detail Spillage card has "vs Learned baseline" row and "Still learning N/3" until min_sample. 35 tests pass. Next: Sprint F4 (Z-report / end-of-day summary).
- Sprint F4 (2026-06-25): End-of-night Z-report — bar_z_report at /bar/z-report/ and bar_z_report_share at /bar/z-report/share/; owner sees all bar shifts for the day, staff sees own shift only; per-shift table: opening float, cash/mpesa/credit, petty cash out, expected drawer, counted cash, variance; day summary tiles: total sales + channels + open tabs KES + keg variance KES; prev/next/today date navigation; Share SMS sends day summary to owner phone; 🧾 Z-Report link added to bar board header; F3-AC1 gap fixed — reconciliation header now shows learned baseline label. 38 tests pass. Next: Sprint F5 (bottle & spirits revenue envelope).
- Sprint F6 (2026-06-25): M-Pesa cross-check + eTIMS-ready receipts — Receipt.etims_receipt_no/etims_url/etims_submitted_at (migration 0066, nullable stubs); Business.kra_pin (accounts migration 0038); M-Pesa cross-check tile in Z-report: Payment(mpesa, completed) for day vs day_mpesa, signed gap, shown only when STK data exists; KRA PIN reminder card in Z-report when kra_pin set; public receipt shows eTIMS receipt no + KRA verify link; Business Settings form gains KRA/eTIMS section. 42 tests pass.
- FINAL (2026-06-25): SPRINT_TEST_GUIDE.md produced — 42 automated tests listed with class/method/sprint; manual smoke tests for F1–F6 with pass/fail criteria for each step. BAR_MODULE_MASTER_SPEC.md deleted (all sprints confirmed shipped). Bar module sequence complete.
- Sprint F5 (2026-06-25): Bottle & spirits revenue envelope — Item.bottle_envelope/tot_ml/tots_per_unit (migration 0065); bottle_expected_revenue_per_unit() = tots_per_unit × avg preset price; stock_take_api GET returns bottle fields, POST returns variance_kes; StaffShrinkage.bottle_loss_kes + total_loss_kes; staff_shrinkage() aggregates ShiftStockCount for bottle_envelope items by date range; leaderboard adds Bottle/Spirits Loss column; Z-report shows day_bottle_variance_kes tile when > 0; item form gains Spirits Accountability section (keg businesses) with auto-calc tots_per_unit from volume ÷ tot_ml. 42 tests pass. Next: Sprint F6 (M-Pesa cross-check + eTIMS-ready receipts).
- Sprint 0 (2026-06-26): Cause-&-Effect Protocol appended to CLAUDE.md Coding Preferences — inverse actions, access/visibility scoping, discriminator consistency, and standard surfaces checklist. No code changes.
- Sprint K1 (2026-06-26): Source-scoped debt — CustomerDebtPayment.source CharField ('bar'|'kitchen', default='bar', migration 0067 + 0068 backfill); _debt_scope(profile, business) helper returns 'bar'/'kitchen'/'all' based on staff role + business.has_kitchen; debt_views.py rewritten: all list/payment queries scoped by _debt_scope; owner sees dual sub-ledger tabs on customer profile; kitchen staff only see kitchen debts; Payment modal sets hidden debt_source field per ledger. 51 tests pass.
- Sprint K2a (2026-06-26): Per-counter M-Pesa — Store-level M-Pesa override fields (migration 0069: has_own_mpesa, till/paybill/pochi, daraja creds); Payment.store FK + source (migration 0069); resolve_mpesa_config(business, store) single resolver (store override wins if has_own_mpesa=True, else business fallback); resolve_account_by_shortcode(shortcode) checks Store overrides first for C2B attribution; mpesa_views.py updated: stk_push_view + payment_status + c2b_confirmation + mpesa_qr_view all use resolver; payment_settings.html gains Kitchen M-Pesa section. 51 tests pass.
- Sprint H1-H4 (2026-06-26): Haki module — Business.haki_enabled (accounts migration 0040); SalaryPayment model (migration 0070, unique_together business+staff+period, days_overdue property); haki_views.py: staff_contribution_report /staff/contribution/ (H1), record_salary_payment /staff/<id>/salary/ with SMS to employee (H2), my_work_and_pay /me/ staff self-service (H3), haki_recognition_statement /staff/<id>/statement/ with print + SMS (H4), _check_and_fire_recognition() deduplicated milestone nudge to owner; Haki nav links added to all staff role sections (mobile + desktop) gated on haki_enabled; 18 new tests (K1/K2a/H), 51 total. All pass.
- Sprint K3 (2026-06-26): Credit Discipline Gate — (A) Kitchen staff now in expense/salary lists (STAFF_PAY_ROLES constant covers staff/waitress/kitchen); (B) staff can generate/share their own Haki statement (privacy gate: owner_required lifted, self-only guard added), Kazi Yangu "🌟 Taarifa Yangu" button added; (C) evaluate_credit() in core/credit_policy.py — non-bypassable system gate checks: policy on/off, credit_approved, is_defaulter+permanent block, overdue window, late-repayment strikes+cooldown, credit_limit, monthly cutoff; gates wired at Quick Sell, Add Transaction, Kitchen Board food_tab/credit; void_tab stamps is_defaulter=True; record_debt_payment stamps last_cleared_at on full clearance; credit standing card on customer profile; Payment Settings "Sera ya Deni" form with _section discriminator in accounts/views.py to avoid M-Pesa fields being erased; migrations 0041 (9 credit policy fields on Business), 0071 (is_defaulter+last_cleared_at on Customer), 0072 (backfill credit_approved=True for existing customers). 61 tests pass. **Correction (Debt Tracker Module Audit, 2026-07-21): this entry's original wording claimed the gate was wired at "Bar Board tab creation" — it never was. evaluate_credit() has never been called anywhere in keg_views.py or shift_views.py; a tab is opened without a credit check, and converting that tab to debt (convert_tab_to_debt/bulk_convert_tabs_to_debt/shift-close auto-convert) is likewise ungated. This is by design, not a gap to close the same way — see that sprint's entry for the reasoning (a WARNING, not a hard block, since by conversion time the goods are already served).**
- Sprint K4 (2026-06-26): Customer-Facing Accountability Receipts — (1) Receipt.meta JSONField (migration 0073) added to Receipt model; Receipt.issue() gains meta= param; (2) _build_credit_receipt_meta() helper in debt_views.py computes score/outstanding/due_date/warn from _get_customer_debt_data after txns written; (3) meta populated on: Quick Sell deni receipts (bar scope), bar settle_tab credit receipts (source scope), kitchen direct credit receipts (kitchen scope), debt payment receipts (post-payment score+remaining); (4) receipt_public.html: credit standing badge (green=reliable, amber=new/moderate, red=high_risk) after total; running total+due_date block for credit receipts; warn-bar amber alert for K3 warn-tier; statement header with aged-bucket chips when meta.is_statement; (5) customer_debt_statement view at /debt/<id>/statement/ (POST, scope-aware, _debt_scope gates kitchen-only staff); issues statement Receipt (payment_method='statement', lines=FIFO unpaid txns, meta with aged buckets) and redirects to /r/<token>/; "📄 Taarifa" button on customer_debt_profile.html; privacy: score/outstanding only appears on that customer's own receipt/statement token. Migration 0073. 72 tests pass.
- Sprint SG (2026-06-26): Universal shift gate enforcement — get_active_staff_shift(user_profile, business) helper in shift_views.py (None=owner bypass, Shift=proceed, False=block); gates applied to: Quick Sell POST, Add Transaction POST, kitchen_checkout, kitchen_receive, bar tick_entry/settle_tab/convert_tab_to_debt/record_breakage; kitchen_board.html seeds `_myShiftOpen` from server-side has_my_shift context so tiles blocked immediately without waiting for async fetch; addToCart() shows toast + shift banner when `_myShiftOpen`=false; owner always bypasses all gates. Bug fix: debt payment receipt remaining_balance now uses post_data['outstanding'] (recomputed after payment) instead of stale pre-payment data['outstanding']. SPRINT_TEST_GUIDE.md updated with K1/K2a/H1-H4/SG manual smoke test sections. 51 tests pass.
- Fix K3/K4 (2026-06-27): Pre-test audit fixes — (1) keg_views: init linked_customer=None before tab block; in merge-tab path set linked_customer=active_tab.customer so cross-counter merge-tab receipts are issued correctly (NameError was caught by outer try/except but silently skipped receipt issuance); (2) credit_policy: rewrote _count_late_repayments with FIFO simulation using cumulative_paid so already-paid txns don't generate unfair strikes on subsequent payments; (3) accounts/views: removed unused Store import in credit_policy POST branch. 72 tests pass.
- Sprint K5 (2026-06-27): Barrel depletion, theft controls, shift gate. (A) accounts.Business.weighs_kegs + block_sales_past_target (migration 0042); KegBarrel.record_sale branches on weighs_kegs — weighing bar: weight<=tare+0.5 auto-depletes; non-weighing bar: no auto-depletion (envelope boundary handled in frontend). (B) tap_barrel accepts starting_weight_kg POST param; creates SPOT reading; fires _fire_owner_alert_msg if > 2 kg missing vs gross_weight. bar_board_api adds envelope_reached per keg + weighs_kegs/block_sales_past_target at root; bar_board.html: openSellModal envelope gate (block toast or Funga Pipa/Endelea confirm); tap modal shows weight input for weighing bars; confirmTap sends weight. deplete_barrel endpoint + URL (no wastage tx, DEPLETED status, F3 baseline refresh). (C) StaffShrinkage.void_count + void_kes; staff_shrinkage() queries BarTab(VOID) by served_by + BarTabEntry sum; bar_shrinkage.html adds Voids column. (D) staff_permissions view computes debt_scope_label from role+access flags; template shows read-only 🧾 Debt Ledger Visibility badge. (E) record_debt_payment + send_debt_reminder both gate on get_active_staff_shift for non-owner staff. SPRINT_TEST_GUIDE.md updated (84 tests, K5 smoke tests). 84 tests pass.
- Sprint K6 (2026-06-27): Partial tab settlement + debt ledger UX. (A) settle_tab in keg_views.py: accepts optional entry_ids[] POST param; settles only selected entries; tab stays OPEN if unpaid entries remain; receipt covers only settled entries; returns tab_settled/partial/settled_amount; bar_board.html: entry checkboxes now updateTabSelectionUI() instead of tickEntry(); entries container gets id="tab-entries-{tab.id}"; selection row shows running total + "Lipa — Cash/M-Pesa" partial buttons (disabled until ≥1 checked); settleTabPartial() function; settleTab() updated to handle d.partial toast; kitchen_board.html: entries get checkboxes with updateKbSelection(); openKitchenTabSettle() shows "KES X (kati ya KES Y)" when entries pre-selected; settleKitchenTab() collects checked entry_ids and appends to request. (B) customer_debt_profile.html: outstanding stat tile shows 🍺/🍗 breakdown when owner and both ledgers have balance; dual-section gate extended from has_kitchen only to also show when both ledgers have outstanding; hidden debt_source replaced with visible radio (Bar/Kitchen) in payment modal; _debtLedgerChange() JS updates amount max on ledger switch; single "Record Payment" button always visible (radio in modal handles ledger selection); dual-section card buttons now set radio. 6 new K6 tests. 99 tests pass.
- Sprint K6.C (2026-06-27): Business-level cup pool — BarCupLog.barrel changed to nullable SET_NULL (migrations 0074/0043); item + recorded_by FKs added; Business.cups_per_pint/cups_per_jug/cup_low_notified_at added; business_cup_pool() helper in keg_metrics.py aggregates bought (SUM BarCupLog.qty) minus consumed (pints×cpp + jugs×cpj + cups direct); add_cups view loses barrel_id from URL/signature, now accessible to bar staff with open shift (not owner-only), barrel optional context for cost allocation; URL changed from bar/barrel/<id>/cups/ to bar/cups/add/; bar_board_api drops per-barrel cup stats, adds cup_pool at root; bar_board.html: per-keg cup panel removed, single business cup tile (_renderCupPoolTile) above keg grid, [+ Log Purchase] for staff+owner, low-stock amber warning when remaining < 30; keg_barrel_detail: per-barrel cup cost row added (allocated logs only), pool balance note references Bar Board; payment_settings.html: Cup Consumption section (cups_per_pint/cups_per_jug) gated on biz_profile.modules.bar; _section=cup_config handler in accounts/views.py; low-stock in-app Notification to owner when pool < 30 (gated by cup_low_notified_at, reset on healthy restock). 12 new K6.C tests. 111 tests pass.
- Sprint DJ1 (2026-06-29): DJ/MC Performer Session Management — Performer + PerformerSession + PerformerFeedback models (migrations 0080 core / 0045 accounts); Business.event_sms_enabled + performer_approval_threshold; core/performer_views.py (performer CRUD, session start/end/pay/checkin-poll, public check-in + feedback); anti-fraud: performer self-check-in via QR (/p/<checkin_token>/checkin/ — no login), server-timestamped, bar board polls 30s, owner alert if session ends unverified; approval gate: sessions above threshold start PENDING_APPROVAL; pay → auto-creates BusinessExpense(category='entertainment') → Expense Intelligence P&L; Z-report: paid KES tile + amber unpaid line; bar board: 🎤 button with 3-state JS modal; templates: performer_list, performer_form, session_list, performer_checkin_public + performer_feedback_public (both standalone, no base extension); 🎤 DJ/MC nav link in owner keg navbar (mobile + desktop). Note: 117 tests run, failures are all pre-existing K5/K6/SG/K4 trailing-slash 301 issues unrelated to this sprint.
- Sprint DJ2 (2026-06-30): Pre-scheduled DJ/MC sessions + shareable promo page. PerformerSession.STATUS_SCHEDULED + scheduled_start_time TimeField (migrations 0082/0083); session_schedule view (owner-only POST, creates SCHEDULED session for future date, validates date > today); session_promo_page view (public, /p/<token>/promo/) — standalone dark luxury poster with OG tags for WhatsApp preview, QR code (qrcodejs), WhatsApp share link, copy-link, auto-print (?print=1), print CSS; activate action in session_update flips SCHEDULED→ACTIVE on the night; session_today_api returns upcoming[] (next 7 SCHEDULED sessions); bar_board.html: ratiba ijayo section at top of DJ/MC modal (Share/Promo/Anza/Cancel per entry); owner "Panga kwa siku nyingine" toggle reveals date+time scheduling form; _djActivateSession/_djToggleSchedule/_djScheduleSession JS. Also in this sprint: feedback page localStorage dedup replacing IP-hash (fixes shared-WiFi false "already voted"); dynamic tag chips on feedback page; staff cannot see DJ/MC agreed fee (IS_OWNER gate). 121 tests run, same pre-existing trailing-slash failures.
- Sprint K7 (2026-06-30): Hotfix + Cleanup. (1) Removed agreed_fee from public performer check-in page — fee was visible to performer before negotiation, to customers scanning the QR, and to anyone forwarded the URL. (2) Dropped dead ip_hash field from PerformerFeedback (migration 0084) — superseded by localStorage dedup in DJ2; removed hashlib import from performer_views.py. (3) BusinessSettings refactor plan documented in Known Issues / Technical Debt — Business model approaching ~87 fields, planned OneToOneField split when next break occurs; 21 current candidates listed. Fix(tests): SECURE_SSL_REDIRECT now gated on not TESTING (sys.argv check) — was causing 301 on all HTTP test-client requests when DEBUG=False; all 121 tests now pass cleanly.
- Sprint T1 (2026-07-05): Tab integrity, station scoping sweep, prior-debt gate, promo module. (1) Bug fix: kitchen "Convert to Deni" was 404 — endpoint called /convert-to-debt/ but URL is /debt/; fixed in kitchen_board.html. (2) close_shift() returns open_tabs list; bar board shows open tabs warning + "Geuza Zote Deni" bulk-convert button after shift close; bulk_convert_tabs_to_debt endpoint converts all open tabs to debt in one action. (3) settle_tab auto-creates Customer record for any payment method (not just credit). (4) tab_check_api extended: returns prior_debt (outstanding KES, is_defaulter) + similar_names; bar board + kitchen board blur handler shows debt warning and blocks tab creation for defaulters or staff without can_authorize_tab_accumulation. (5) can_authorize_tab_accumulation BooleanField on UserProfile (accounts migration 0046); toggle in staff_permissions.html. (6) stock_list() station-scoped: kitchen staff see only kitchen items, bar staff see only bar items, ?station=kitchen param supported. (7) home.html: Kegs Running Low tile gated on show_bar; DJ/MC widget gated on show_bar; stat card links fork by station. (8) shift_history() scopes shifts by station. (9) Promo module: PromoMessage model + Customer.dob/notes (core migration 0089); promo_views.py with promo_customer_db, customer_update, promo_compose, promo_history; 6 segments; SMS+in-app channels; {name} personalisation; quick-message templates; owner navbar links. 126 tests pass.
- Sprint DJ4 (2026-07-03): DJ/MC UX fixes + rate individualization + photo + insights. (1) all_confirmed relaxed to P1+staff only — DJ can go ACTIVE before MC arrives; P2 checkin_at still timestamped for accountability (migration 0086). (2) Rate individualization: second_performer_fee field on PerformerSession; duo start form shows separate fee inputs for DJ + MC, each auto-fills from performer's standard_rate; session_pay creates one BusinessExpense with per-performer fee breakdown in description. (3) QR codes now render on first modal open (setTimeout 50ms wrap so browser lays out DOM before QRCode computes dimensions). (4) Staff confirmation picked up immediately (setTimeout 300ms delay in _djStaffConfirm before _loadState; cache-bust ?_=timestamp on both _loadState and checkin-status polls). (5) ACTIVE duo sessions show P2 pending QR + "hajajibu bado" badge so late MC arrival can scan; poll stops on COMPLETED/CANCELLED instead of all_confirmed so late P2 is tracked. (6) Performer photo: Performer.photo_url CharField; performer_form adds URL field with live preview; performer_list shows circular avatar; promo page shows performer photo with fallback. (7) Performer insights: performer_list computes stat_total_paid + insight badge (Book Again / Angalia / Mpya) from combined ratings; top-performer recommendation callout card at top of list. (8) entertainment BusinessExpense already flows to Expense Intelligence (CATEGORY_CHOICES confirmed). 126 tests pass.
- Sprint DJ3 (2026-07-03): Duo support + two/three-step confirmation + payment privacy. (1) PerformerSession model gains: second_performer FK, second_performer_checked_in/at/token, staff_confirmed/confirmed_by/confirmed_at, STATUS_PENDING_CONFIRMATION (max_length 20→22), all_confirmed property, second_performer_checkin_short_code (migration 0085 — two-step for unique UUID on existing rows). (2) Session lifecycle: always starts PENDING_CONFIRMATION; _maybe_activate() auto-flips to ACTIVE only when P1 checked in + P2 checked in (duo) + staff on-duty confirmed — preventing fake/unapproved sessions from being paid. High-fee gate still uses PENDING_APPROVAL as before, then drops to PENDING_CONFIRMATION after owner approves. (3) session_pay gated on all_confirmed — cannot pay until all three parties confirm. (4) _send_payment_sms() fires to each performer (primary + second) on pay — no amount disclosed. (5) session_today_api: fee + payment_status returned only when is_owner; all_confirmed/staff_confirmed/second_performer fields included for all authenticated callers. (6) Public check-in URL handles both checkin_token (primary) and second_performer_checkin_token via same view; shows correct performer name; shows payment status badge (Yamethibitishwa / Yanasubiri) after confirmation so performer can bookmark URL to track payment. (7) Bar board: PENDING_CONFIRMATION state shows checklist (P1 ✓/○, P2 if duo ✓/○, staff ✓/○), QRs for each unconfirmed performer, "Thibitisha Ufika" staff confirm button; poll now reloads on ANY confirmation change (not just P1); duo toggle in start form sends second_performer_id; _djStaffConfirm() function. (8) session_list: shows "& SecondName" + combined type badge + PENDING_CONFIRMATION badge. (9) Promo page: duo performer names in title, OG meta, body, and JS WhatsApp message. 126 tests pass.
- Tab drawer visual audit (2026-07-05): Four UX bugs fixed in bar_board.html + keg_views.py. (1) Wrong icon — every BarTabEntry now carries is_kitchen_item flag (computed from item.store.is_kitchen in tabs_list); renderTabs uses 🍽 for kitchen/food entries and 🍺 for bar/drink entries — Smokies no longer showed with beer icon. (2) Paid entries hidden — renderTabs now filters is_paid=True entries before rendering; only unpaid items shown, total always matches sum of visible items; "Vitu vyote vimelipwa ✓" placeholder when all entries settled. (3) Mixed Tab badge — tabs_list adds cross_notice for food-sourced tabs when bar entries present (and vice versa); renderTabs shows amber "🔀 Mixed Tab" badge instead of "🍽 Food Tab" when cross_notice is set; food tabs also render cross_notice banner. (4) "Vileo tu" note gated — tabs_list returns bar_only_view: not _see_all; stored as window._barOnlyView on fetch; sub-label "Vileo tu vinaonyeshwa hapa" only shown when bar_only_view is true; owner/cross-access sees plain timestamp. No migrations. 126 tests pass.
- Mixed tab counter settlement fix (2026-07-05): kitchen_views.py + bar_board.html. ROOT CAUSE: kitchen_tabs_list `_see_all` branch returned ALL entries (including bar items) for food tabs, so owner saw Kikombe/Jug in kitchen settlement. Simultaneously, bar board rendered ALL food tab entries as read-only with "Lipa kwenye Kitchen Board" — so bar items merged into a food tab had no settable board at all. FIX: (1) kitchen_tabs_list now always filters to kitchen entries only (both owner and kitchen-only staff paths unified); bar_count in cross_notice reads "settle at Bar Board" for cross-access viewers. (2) bar_board.html renderTabs for food tabs now splits entries by is_kitchen_item: kitchen items render read-only, bar items render with checkboxes + partial Cash/M-Pesa settle buttons inside #tab-entries-{id} so updateTabSelectionUI works. Footer note "🍽 Chakula → Lipa kwenye Kitchen Board" appears only when kitchen items are present. No migrations. 126 tests pass.
- Sprint Restock (2026-07-05): Staff Restock Notification + Receipt Acknowledgement — StockRequest model (migration 0090, pending/ordered/received states); restock_views.py (request_restock POST with shift gate + owner SMS + in-app, restock_list owner page, restock_mark_ordered); add_transaction auto-resolve hook closes StockRequests when any Receipt is recorded for the item, fires "stock received" SMS to owner, suppresses duplicate cost-price SMS; add_transaction ?quick=1 mode returns JsonResponse for AJAX; stock_list annotates has_pending_restock → 🔔 Notify / 📦 Requested chips for staff; home.html owner badge → /stock/restock/ when requests pending; bar board 🔔 Notify on empty keg tiles and <20% fill tiles (staff only); kitchen board 🔔 Notify Owner on oos portion/batch tiles (staff only); Quick Sell "+📦 Pata Stok" owner-only modal posts to add_transaction?quick=1; fixed latent bug: cost-price notification block used undefined `business` variable (now user_profile.business); Notification added to top-level model imports. 126 tests pass.
- Sprint RD1 (2026-07-07): Cross-module Receipt Deduplication — customers now always receive ONE receipt URL per day regardless of where they buy (bar tab, kitchen tab, Quick Sell deni, or a mix). Three-pronged fix: (1) core/views.py QS credit path: before issuing a new receipt, query for an existing receipt today for same customer name (excluding statements); if found, append new lines + update total, skip SMS (avoid double-send). (2) core/keg_views.py: added Priority 4 to bar board receipt resolution — when Priorities 1-3 return no master receipt, check for any today's receipt for same customer from any module (QS-deni-first, then bar-tab-second scenario); link bar tab into that receipt's meta.linked_tab_ids. (3) core/kitchen_views.py: same dedup logic added before the credit receipt block — checks for existing today's receipt and appends lines rather than issuing new; gates credit SMS on not _kitchen_rcpt_reused. No new models or migrations. 126 tests pass.
- Sprint M1 (2026-07-07): Manager Role + Owner Consumption Tracking — (1) UserProfile.role='manager' (accounts migration 0047); is_manager/is_owner_or_manager properties; AddStaffForm gains Manager choice; add_staff auto-sets can_access_bar/kitchen/override_restrictions/authorize_tab=True for managers; purple badge-manager CSS in base.html; config links (Add Staff, Payment Settings, Business Settings) gated on is_owner in Manage dropdown — managers see full operational navbar but no settings. (2) owner_or_manager_required decorator in core/views.py; all operational @owner_required decorators in core/* bulk-replaced with @owner_or_manager_required (analytics, keg, haki, shift, performer, restock, restricted items); accounts/decorators.py owner_required stays strict (config views only). (3) OwnerConsumption Transaction type (core migration 0094); owner_consumption_views.py — shift-gated for staff, bypass for owner/manager, stores qty=-qty, payment_method=''; URL at /stock/owner-consumption/. (4) Quick Sell "🥃 Mmiliki Alichukua" modal — all staff see button, item dropdown filters non-keg non-produce items, AJAX POST. (5) Z-report owner consumption tile + itemised list (raspberry, qty|slice:"1:" strips leading minus). (6) Quick Sell is_owner context updated to is_owner_or_manager so managers see Pata Stok, From Market, Void tab. (7) home view is_owner checks for pending_restocks + expense_review_due updated to is_owner_or_manager. Bugfixes: payment_method=None → '' (CharField not nullable); barrel hard-block mode now shows owner a confirm-to-deplete dialog instead of dead-end toast (bar_board.html openSellModal). 126 tests pass.
- Owner reporting audit + gap fixes (2026-07-08): Full audit of all 12 owner-facing surfaces for bar businesses. Two bugs fixed, four design gaps closed. No migrations. Bug 1: Z-report keg variance was cumulative (all barrels ever) — fixed to TAPPED + closed-today barrels only; field name was closed_on but KegBarrel uses closed_at. Bug 2: bar daily report staff revenue included voided pours — added .exclude(payment_method='void'). Gap 2: DJ/MC SMS (session start + unverified alert) now sends to each owner's UserProfile.phone instead of business.phone — same loop pattern as keg alerts. Gap 4: cup low-stock alert now fires SMS to owner alongside the existing in-app notification, gated by same cup_low_notified_at cooldown. Gap 1: shift close cash variance > KES 500 now fires in-app + SMS to owner with direction (upungufu/ziada); threshold hardcoded 500, to be made configurable later if noisy. Gap 3: Bar Performance pouring league replaced BarTabEntry/served_by attribution (tab sales only) with shift-window attribution — for each bar shift, sum ALL Issue transactions (keg, non-kitchen, non-void) during the shift window; both tab AND walk-up cash/mpesa sales attributed to the shift's staff member. Manager on duty strip added to home dashboard (purple row, last_login today, owner-only). 126 tests pass.
- Tab UX fixes + staff duty log (2026-07-08): (1) receipt_public.html — live tab checkboxes start all unchecked on first render; Chagua Yote selects all; subsequent live-poll re-renders preserve user selection (tracked via _checkedIds Set). (2) keg_views.py tabs_list — batch-fetch receipt tokens for open tabs and return receipt_url + opened_date per tab. (3) bar_board.html renderTabs — amber stale-tab banner on any tab opened on a previous calendar date with one-click Geuza Deni button; receipt link (Angalia / Tuma Risiti) shown when receipt_url is present. (4) Staff/manager duty log — /staff/<id>/duty-log/?date=YYYY-MM-DD shows shifts, transactions, receipts, and tabs for any staff or manager on a given date; linked from Haki contribution report. No migrations. 121 tests pass. Auto-convert tabs at shift close: fires only when business.is_open() returns False (past closing_time) or no closing_time set; intentional mid-shift tab survival when bar is still open.
- Fix: live receipt DEBT state (2026-07-08): _get_live_tab_state now returns effective_status='DEBT' + is_live=True when tab.status='SETTLED' but unpaid entries still exist (fingerprint of bulk_convert_tabs_to_debt). Receipt shows amber "Tab imekuwa Deni — KES X bado haijafunguliwa" banner + "LIPA DENI" pay section. Customer pays via STK/QR directly from the receipt URL; _settle_receipt_entries_from_payment already handles is_paid=False debt entries and updates transaction.payment_method credit→mpesa. Live poll: when all debt paid, shows "Deni limeliwa — asante!". No migrations. 121 tests pass.
- Debt receipt full flow (2026-07-08): (1) Receipt shows ALL items — paid ones strikethrough + green "✓ Imelipwa", unpaid with checkboxes; total label changes to "Bado Kulipa" in DEBT state; JS renderLines updated for is_paid flag. (2) Station-aware M-Pesa routing in receipt_pay — if all selected entries are from one store with has_own_mpesa=True, routes STK/QR to that store's config; otherwise business fallback. (3) Notifications on customer receipt payment: _settle_receipt_entries_from_payment now notifies original serving staff (tab.served_by), current on-shift staff, owners, and managers via in-app + SMS; message varies for partial vs full clearance. (4) send_debt_reminder SMS now includes a direct pay link to the customer's latest tab receipt — customer can pay without visiting business. 121 tests pass.
- Bar ops audit (2026-07-12): Systematic audit of all 2522 lines of keg_views.py. Three bugs fixed: (1) update_tab_phone was missing @login_required + @require_POST — every other mutation endpoint has both, this one accepted GET requests from unauthenticated callers. (2) bar_daily_report staff performance did not skip kitchen-staff shifts (unlike Z-report which already had the exclusion) and did not filter item__store__is_kitchen=False — kitchen revenue bled into bar staff stats on multi-counter businesses. (3) convert_tab_to_debt sent no SMS to the customer confirming the debt conversion — Quick Sell credit sales send a confirmation; tab→debt conversion is the same event and now does too. Customer name rename + tabs drawer edit (same session): update_tab_name view added in keg_views.py propagates new name to tab.customer_name, Customer.name, and Transaction.recipient; name edit row added to renderTabs/qsRenderTabs/renderFoodTabs in all three tabs drawers. Stock take POST "Hitilafu ya mtandao" fixed: POST was returning redirect() which JS fetch followed as HTML; changed to always return JsonResponse. 121 tests pass.
- Stock ops + P&L consistency (2026-07-13): (1) Adjust Stock Balance button (⚖️ Rekebisha) added to stock list for countable non-keg/non-produce items — owner enters physical count, system creates Wastage (shortage) or Receipt (surplus) transaction. (2) Stock variance "Kubali na Sababu" — pending variances now have an inline form to specify cause (Cash/M-Pesa/Credit/Kipotea) with optional customer name; blank-name credit shows amber warning that debt won't be tracked. (3) P&L audit found 3 critical sign bugs: Wastage and Issue corrective transactions had qty=abs() (positive) instead of negative — balances moved the wrong direction after any adjustment. Fixed to qty=-abs(). (4) Adjustment surplus Receipts tagged invoice_no='[ADJ]' to suppress false "missing cost price" home alert. (5) Transaction history pill colours: Wastage=amber, OwnerConsumption=purple (both were red like Issue). (6) Analytics P&L: net_profit now deducts wastage_loss (Wastage at cost_price) and void_loss (voided Issue at cost_price) — previously voided tabs and stock adjustments were invisible to the P&L. Losses tile added to analytics dashboard showing Wastage and Void breakdown. 126 tests pass.
- Sprint BillScan (2026-07-15): Scan to View Your Bill — bar wall QR + 4-digit tab PIN. BarTab.tab_pin (migration 0103, 4-digit PIN auto-generated at tab creation for bar board and kitchen tabs); tabs_list API returns tab_pin in both result paths (all-staff and bar-only); PIN shown in all three tabs drawers (bar_board.html renderTabs — food tab + regular tab paths, quick_sell.html qsRenderTabs, kitchen_board.html renderFoodTabs) — visible to all staff, no owner gate; find_tab_public + find_tab_search views (/bar/find-tab/<id>/ + /bar/find-tab/<id>/search/) — public name-or-PIN lookup page, 5 calls/min per IP Django-cache rate limit, PIN match redirects directly to token URL; tab_live_view (/tab/<token>/) — public live bill, no @login_required, 20s auto-refresh, 🍺/🍽 item icons, outstanding (raspberry) + total (gold) tiles, settled banner; Wall Tab QR card in Payment Settings (keg businesses only) — qrcodejs-rendered QR pointing to find-tab page, Print button with print-only CSS. New templates: find_tab.html + tab_live.html (both standalone, no {% extends "base.html" %}). 117 tests pass.
- Sprint K8 (2026-07-15): Audit fixes. P&L wastage double-deduction claim reviewed and REJECTED —
  code trace (Transaction.cost() returns 0 for non-Issue types) confirms wastage_loss was already
  deducted exactly once via total_losses, matching the intentional 2026-07-13 fix; formula left
  unchanged, regression test added (NetProfitWastageDeductionTest) to lock it in. BillScan tab
  backfill: core/management/commands/backfill_tab_tokens.py fills blank tab_receipt_token/tab_pin
  on OPEN tabs (per-business PIN uniqueness), run once per deployed environment after migration
  0103. text-muted cleanup in analytics.html (4 occurrences) and delete_item.html (1) → inline
  `style="color: #b0b0b0"`. tab_live.html "Bado kulipa" tile now hidden when outstanding=0. Local
  dev venv Django synced 6.0.3→4.2.29 to match the existing requirements.txt pin (was already
  correct; only the installed package was stale). Cause-&-Effect Protocol section in this file now
  opens with a "map is a required deliverable" paragraph. 133 tests pass (7 new).
- Fix: Quick Sell tabs had no PIN/token (2026-07-15): Roy caught this live — after running
  backfill_tab_tokens, new tabs opened via Quick Sell's "Tab" checkout still came out with a blank
  tab_pin/tab_receipt_token, invisible to the BillScan wall-QR lookup. ROOT CAUSE: BarTab creation
  exists at three call sites (bar board, kitchen, Quick Sell) but Sprint BillScan only added
  token/PIN generation to bar board and kitchen — Quick Sell's tab-sale path (core/views.py) was
  missed entirely. Fix: added BarTab.new_credentials(business) classmethod (core/models.py) as the
  single source of truth — generates a receipt token plus a PIN checked for uniqueness against that
  business's other open tabs (the two existing call sites also lacked collision-checking). All three
  creation sites (core/views.py, core/keg_views.py, core/kitchen_views.py) now call it. 3 new tests
  (BarTabNewCredentialsTest) including an end-to-end POST /quick-sell/ regression lock. No
  migrations. 127 tests pass.
- Fix: checkout double-submit safety net (2026-07-15): Roy saw a Quick Sell tab entry double
  (KES 1000 -> KES 2000 in the tabs drawer) after a possible double-tap / slow-network moment —
  not fully reproducible, but the doubled amount ruled out a pure display glitch. ROOT CAUSE
  CLASS: all three checkout surfaces (Quick Sell, Bar Board, Kitchen) relied only on client-side
  JS (button disable, a JS flag) to prevent double-submission — that protection only stops a
  second click on the same live page, not a real duplicate request reaching the server (network
  retry, back-button resubmission of a real <form>, a double tap that both landed before the
  button could disable). FIX: core/idempotency.py — claim_checkout_token(business_id, token, ttl)
  atomically claims a client-supplied random token via cache.add(); a second POST with the same
  token is treated as a duplicate and skipped rather than re-processed. Wired into all three
  checkout views (core/views.py quick_sell, core/keg_views.py bar_board, core/kitchen_views.py
  _kitchen_checkout) in the same change, per the existing tabs-drawer "fix one, fix all three"
  rule for these counters. Also closed an asymmetry found while auditing: Bar Board's checkout
  form was missing the form-level submit guard that Quick Sell already had (only had button
  disable) — added to match. 2 new tests (CheckoutIdempotencyTest): duplicate token does not
  double-book a sale; two genuinely different tokens both go through as real, separate sales (the
  guard must not suppress legitimate repeat purchases). No migrations. 129 tests pass.
- Feature: Pay-Cash-at-Counter from the live tab receipt (2026-07-15): Roy scanned the BillScan
  wall QR and wanted the same payment options the tabs drawer has — including a Cash option that
  doesn't process payment, just tells staff the customer is coming to the counter. (1) Routing:
  find_tab_search now resolves the customer's PIN/name match to their existing Receipt
  (/r/<token>/) via new _resolve_tab_public_url() helper, reusing the fully-built STK/QR/checkbox
  payment UI on receipt_public.html instead of the bare read-only /tab/<token>/ page; falls back
  to the old page only for a tab with zero sales yet (no receipt issued). (2) BarTab.cash_requested_at
  (migration 0104, nullable datetime) — set when a customer taps "Lipa Cash"; cleared the moment
  staff settles any entry on the tab (settle_tab, tick_entry, STK settlement, void_tab all clear
  it) — per Roy's explicit requirement, the tab is NOT auto-cleared, only a real counter payment
  clears it. (3) receipt_pay() gains type='cash': resolves the same entry_ids/debt-mode amount as
  STK does, but creates no Payment — just sets the flag (entry-mode only, not debt-mode, since debt
  isn't tied to a live BarTab) and fires _fire_cash_payment_request() — in-app + SMS to serving
  staff/on-shift staff/owners/managers, mirroring the debt-payment notification recipient pattern.
  (4) Persistent "💵 Anataka kulipa Cash" badge added to all three tabs drawers (bar_board.html,
  quick_sell.html, kitchen_board.html) via cash_requested in tabs_list/kitchen_tabs_list JSON.
  (5) Card/PDQ deliberately deferred — Cash only this sprint (Roy's call), no new Business field.
  Three pre-existing bugs found and fixed while working in this exact code (all now covered by
  tests or manually verified): (a) receipt_public.html had TWO functions both named `stkStatus` —
  a 3-arg debt-mode version (line 770) and a 2-arg OPEN-tab version (line 931) — JS function
  hoisting meant only the LAST one won, so every debt-mode status call silently passed the wrong
  arguments; renamed the debt one to `debtStkStatus` and fixed its 12 call sites. (b) My own edit
  briefly misplaced `@csrf_exempt` onto a helper function instead of `receipt_pay` — caught and
  fixed before commit. (c) `core.models.Notification` has no `business` field and requires `title`
  (see Known Issues below) — mpesa_views.py's `_settle_receipt_entries_from_payment` (the function
  this feature's notification code mirrors) has been silently failing to notify staff of debt
  payments since the 2026-07-08 sprint, masked by a broad except; fixed it alongside the new code
  since it's the same block. 4 new tests (CashPaymentRequestTest). Also: "Tawi la" (mistranslated
  "branch") on tab_live.html renamed to "Bill ya" per Roy's request — clearer for customers. 133
  tests pass.
- Fix: cross-counter receipt linking was asymmetric (2026-07-16). Roy asked for a diagnosis of
  PIN generation + receipt flow across all three counters. PIN generation was already correct
  (single BarTab.new_credentials() source since the 2026-07-15 fix). Receipt/bill unification was
  NOT: each counter had its own hand-copied master-receipt lookup and they'd drifted — Bar Board
  checked everything (own receipt, linked_tab_ids, kitchen tab, any same-day receipt from any
  source), Kitchen only checked Bar (never Quick Sell), and Quick Sell's tab flow checked nothing
  beyond its own tab. Net effect: a customer's tab opened at Bar or Kitchen first, then rung up
  again at Quick Sell, got a SECOND separate receipt and PIN instead of joining their existing
  bill. FIX: core/tab_receipts.py — resolve_master_receipt(business, tab) is now the single
  source of truth for all three counters (core/views.py quick_sell, core/keg_views.py bar_board,
  core/kitchen_views.py food_tab), collapsing the old per-counter priority chains into one:
  (1) own receipt, (2) already linked elsewhere, (3) another OPEN tab for the same customer on
  ANY counter that already has a receipt, (4) any receipt issued today for that customer name on
  any counter. Bar Board's two near-duplicate "linked" SMS blocks (one per old priority 3/4)
  collapsed into one; Quick Sell gained the same "bidhaa imeongezwa" cross-link SMS Bar Board and
  Kitchen already had, for parity. Bug found while building this: `meta__linked_tab_ids__contains`
  (used by the old code and carried into the new helper) is a JSONField `contains` lookup that
  Django does not support on SQLite, only PostgreSQL — production was never at risk (Postgres),
  but it meant this code path had apparently never been exercised by any test in the project's
  history; wrapped it in `core/tab_receipts.py:_receipt_linked_to()` to degrade to "no match"
  under SQLite instead of raising, with zero behavior change on Postgres. 4 new tests
  (CrossCounterReceiptLinkingTest): direct priority-chain coverage plus one end-to-end regression
  lock (Bar tab first, Quick Sell second, for the same customer, must reuse one receipt). No
  migrations. 137 tests pass.
- Sprint K9 (2026-07-17): Four bug fixes from a targeted audit. (1) SQLite NotSupportedError
  guard: `core/keg_views.py` (`_resolve_tab_public_url`, `tabs_list` Pass 2) and
  `core/kitchen_views.py` (`kitchen_tabs_list` Pass 2) each had their own unguarded
  `meta__linked_tab_ids__contains` Q() chain — a guaranteed 500 on SQLite (local dev/tests) the
  moment any tab had no directly-owned receipt. New `_safe_linked_query()` in
  `core/tab_receipts.py` is now the single guarded entry point for all 4 call sites (including
  the pre-existing `_receipt_linked_to`). Root-cause note for next time: a `try/except
  NotSupportedError` around `qs.filter(...)` alone does NOT work — Django querysets are lazy, so
  the exception only fires when the caller evaluates the queryset later (`.first()`, iteration),
  by which point it has escaped the guard. `_safe_linked_query()` forces evaluation
  (`list(qs.filter(q))`) inside its own try block and returns a materialized list, which is what
  actually catches it; this bug briefly reappeared in this sprint's own first draft of the fix
  before being caught by the test suite. (2) `Notification.objects.create(business=...)`: fixed
  all 8 remaining sites (`shift_views.py:480` — also missing `title=` — plus 7 in
  `debt_views.py`); the misleading `## Notification Creation` pattern example earlier in this
  file (showing `business=` as correct) also fixed. (3) `cash_requested_at` not cleared on debt
  conversion: fixed `convert_tab_to_debt` + `bulk_convert_tabs_to_debt` (the sprint's named
  targets) plus two more found by the regression sweep — `mpesa_views._settle_tab_from_payment`
  (STK full-tab settlement) and the shift-close auto-convert-tabs-to-debt loop in
  `shift_views.py` — same bug pattern, not mentioned in the brief. The sprint brief's claim of a
  separate "kitchen settle path" gap was investigated and found incorrect: kitchen board settles
  food tabs through the same shared `/bar/tabs/<id>/settle/` endpoint bar board uses, which
  already cleared the flag. (4) `BarTab` gained a partial `UniqueConstraint` on
  `(business, tab_pin)` for `status='OPEN'` rows, closing the race in `new_credentials()` between
  reading existing PINs and saving. New `BarTab.create_with_credentials()` classmethod is the
  single retry point (one retry on `IntegrityError`) used by all 3 tab-creation sites (bar board,
  kitchen, Quick Sell), replacing each site's own `new_credentials()` + `objects.create()` pair.
  15 new tests. 152 tests pass.
- Post-K9 commit audit (2026-07-18): Reviewed the 5 most recent commits (BillScan
  Pay-Cash-at-Counter arc through Sprint K9) diff-by-diff against current code rather than
  trusting commit messages. Idempotency backstop, cross-counter receipt-link collapse, and tabs-
  drawer parity all checked out correct. Found and fixed two real gaps in the Pay-Cash-at-Counter
  feature (`c9e7829`, not caught before merge): (1) Station Scoping Principle violation —
  `_fire_cash_payment_request` (`core/receipt_views.py`) looped over every on-shift staff member
  for the business with no bar/kitchen filter, so a kitchen-only staffer got an in-app + SMS ping
  about a bar tab's cash request and vice versa. Fixed by threading `BarTab.source` (or
  `debt_source` for debt-mode calls, which have no live tab) through `_station_scope()`, the same
  helper `home()`/`shift_history()` already use — 'qs' tabs and unknown sources stay unscoped
  since Quick Sell isn't station-partitioned. (2) No rate limit on repeated "Lipa Cash" taps — this
  endpoint is public/unauthenticated and its button carries no idempotency token (unlike every
  checkout form, fixed one commit earlier in the same arc), so a customer double-tapping fired a
  fresh SMS to every recipient on every tap with zero cost control, unlike every other SMS path in
  this app (`Business.last_txn_sms_at` 10-min bundling). Fixed with a 10-minute
  `django.core.cache` cooldown keyed per receipt token, gating only the notification fan-out — the
  `cash_requested_at` flag itself still refreshes on every tap so the tabs-drawer badge stays
  accurate. 2 new tests (`CashRequestStationScopingTest`, `CashRequestCooldownTest`). No
  migrations. 154 tests pass.
- Fix: QR-scan/PIN receipt showed a live tab as already paid (2026-07-19). Roy reported the
  exact production incident live: opened a tab, customer scanned the wall QR, entered their
  PIN, and the receipt showed the item as paid when it was still open. Root-caused via full
  code trace (see Known Issues entry above for the complete mechanism):
  `resolve_master_receipt()` can hand a brand-new tab a receipt that has no `meta.tab_id` of
  its own — only `linked_tab_ids` — most plausibly Priority 4 matching an EARLIER, unrelated,
  already-completed one-off cash sale for the same customer name earlier that day (an
  everyday scenario for a repeat customer, not an edge case). Every function reading
  `receipt.meta.get('tab_id')` directly treated that receipt as "not live," so the page fell
  back to the OLD sale's stale static snapshot. New shared helper
  `core.receipt_views._receipt_all_tab_ids()` is now the single way to read a receipt's tab
  references; audited and fixed every call site: `_get_live_tab_state`,
  `_get_station_debt_data`, `receipt_pay` (all core/receipt_views.py — display AND the
  STK/QR/cash payment gate itself, which previously 400'd outright for these receipts),
  `mpesa_views._create_debt_payment_from_receipt` (the debt-mode STK callback — this one was
  the most severe: a completed, Safaricom-confirmed M-Pesa charge could be silently dropped
  and never recorded), and `debt_views.send_debt_reminder`'s SMS pay-link lookup. Separately,
  while auditing "the STK flow across all counters" as requested:
  `mpesa_views._settle_tab_from_payment` (staff-initiated full-tab "📲 STK Push") always
  issued a brand-new receipt on settlement regardless of whether the tab already had a master
  receipt, orphaning the customer's known PIN/link — fixed to call `resolve_master_receipt()`
  first, matching every other receipt-issuing site. Kitchen-cart and Quick-Sell-cart STK
  (`_settle_kitchen_order_from_payment`, `_settle_qs_from_payment`) were audited and are
  correctly out of scope — they're anonymous walk-up checkouts with no tab/customer to
  consolidate into. 7 new tests (`LinkedOnlyReceiptLiveStateTest`,
  `SettleTabFromPaymentReusesReceiptTest`). No migrations. 161 tests pass.
- Bar/Keg Module Systemic Audit (2026-07-19). Roy requested a comprehensive, theme-by-theme
  audit of the whole bar business scheme — this is the deferred systemic audit from
  `[[project_systemic_audit_deferred]]`, scoped to bar/keg. Ran three staged themes across
  `keg_views.py`, `shift_views.py`, `performer_views.py`, and their templates/tests (three
  separate commits, each independently tested and pushed):
  **Theme 1 (money-path idempotency):** `tick_entry()`/`settle_tab()` (the staff-side tab
  settlement paths — far more common than customer-initiated STK) unconditionally issued a
  brand-new `Receipt` on every settlement even when the tab already had a master receipt from
  when it was opened; on a full settlement via `settle_tab` the new receipt carried no
  `tab_id` at all, a permanent orphan. This was hitting nearly every everyday tab, not an
  edge case. Fixed both to reuse the master receipt via `resolve_master_receipt()`, same
  pattern as `mpesa_views._settle_tab_from_payment`. Also fixed: `session_pay()` (DJ/MC
  payout) had no lock — a double-tap during a rushed end-of-night payout could create two
  `BusinessExpense` rows for one session, double-counting a real cost in the P&L; fixed with
  `select_for_update()` inside `atomic()`, same pattern as `KegBarrel.record_sale_locked`.
  `record_breakage()`, `add_cups()`, `receive_barrel()` had no idempotency guard at all
  against a duplicate/retried request silently double-recording wastage, cup purchases, or
  received stock; fixed by reusing `core.idempotency.claim_checkout_token`. 10 new tests.
  **Theme 2 (state-transition completeness):** `_auto_close_expired_shifts()` — the safety
  net that force-closes a shift when staff forgot and business hours have passed — flipped
  `shift.status` to `CLOSED` directly, completely bypassing the tab-to-debt conversion sweep
  a manual `close_shift()` performs. This is precisely the scenario most likely to also have
  forgotten open tabs, and the missed-tasks reminder shown afterward only checks stock-take
  and barrel-weight readings, never tabs — an abandoned tab from an auto-closed shift had no
  automatic resolution path and no visibility anywhere. Extracted the conversion logic into a
  shared `_convert_open_tabs_to_debt_for_shift()` helper now called from both close paths so
  they can never drift apart again. 2 new tests. **Theme 3 (access-control scoping):**
  `tabs_list()` (read/GET) already scoped correctly by station via `_station_scope()`, but
  every WRITE endpoint on tabs (`tick_entry`, `settle_tab`, `update_tab_name`,
  `update_tab_phone`, `convert_tab_to_debt`) filtered only by business — a kitchen-only
  staffer could act directly on a bar tab via the API even though the UI never shows them
  one, because hiding a button in the template is not the same as gating the endpoint.
  `bulk_convert_tabs_to_debt` was worse: no permission check of any kind beyond being logged
  into the business — any staff member could bulk-convert arbitrary tab IDs regardless of
  role or station. Fixed with a shared `_allowed_tab_sources(up)` helper; `settle_tab` checks
  each entry's own station (`item.store.is_kitchen`) rather than the tab's overall `source`,
  since a bar-only staffer must still be able to settle just the bar-item entries within a
  mixed/cross-counter-merged tab (an existing, intentional feature). 8 new tests. **Verified
  already correct and not touched:** `bar_board` checkout idempotency, `KegBarrel.record_
  sale_locked`'s `select_for_update`, `void_tab`/`remove_tab_entry` (owner/manager-only,
  correctly see both stations by design), `_auto_complete_stale_sessions` (DJ/MC — properly
  wired up, not dead code), `KegBarrel.is_stale()` (informational-only by design — a
  lingering tapped barrel isn't customer debt), all report views (`keg_reconciliation`,
  `keg_barrel_detail`, `bar_shrinkage_report`, `voided_tabs_list`), DJ/MC public pages (no fee
  leak, matching the Sprint K7 fix). **Noted then folded in same day (2026-07-19):**
  `kitchen_wastage()` had the analogous smaller station gap — `get_active_staff_shift()`
  only checks for ANY open shift, not specifically a kitchen one, so a bar-only staffer
  (no `can_access_kitchen`) with an open BAR shift could still POST directly to
  `/kitchen/wastage/` and log kitchen wastage, even though the kitchen board is never
  shown to them. Fixed with the same `_station_scope()` check used throughout the bar-side
  fixes. 3 more tests (`KitchenWastageStationScopingTest`). 20 new tests from the three
  staged themes + 3 from this follow-up = 23 new tests total, 184 tests pass. Four commits:
  `366c4c7`, `9c38b23`, `5a2543f`, plus this follow-up.
- Kitchen-Module Systemic Audit (2026-07-19). Same night, same session — Roy greenlit
  continuing straight into the next module rather than waiting. Same three-theme structure
  against `kitchen_views.py` and `models.py` (`KitchenBatch`), two commits, each independently
  tested and pushed. **Theme 1 (money-path idempotency):** `_kitchen_checkout()`'s
  `KitchenBatch.record_sale()` and `ProduceBunch.record_sale()` calls fetched the envelope via
  a plain `.get()` with no `select_for_update()` — unlike `KegBarrel.record_sale_locked()`. Two
  near-simultaneous sales from the same pot/batch (two staff ringing up at once, or a
  network-retry racing a fresh request) could both read the same stale `revenue_collected` and
  the last save wins, silently discarding one sale's contribution to the envelope. Locked both
  call sites the same way kegs already were. `kitchen_receive()` (all modes),
  `kitchen_batch_receive()`, `kitchen_consumable_add()` had no idempotency guard at all — same
  gap already fixed for `receive_barrel`/`add_cups`/`record_breakage` in the bar module. Fixed
  by reusing `core.idempotency.claim_checkout_token`. 6 new tests. **Theme 2 (state-transition
  completeness):** `KitchenBatch.discard()` used to only flip status — unlike
  `ProduceBunch.discard()` (the sibling revenue-envelope model), it never created a Wastage
  Transaction. A pot of chips or stew thrown out went completely unrecorded: invisible to
  analytics' `wastage_loss`, invisible to `net_profit`, invisible to the owner — food wastage is
  a marquee metric for a food business. Fixed to mirror `ProduceBunch`'s fraction-of-envelope
  approach (`qty` = unrecovered fraction of `cost_total`, so a batch that already sold past its
  cost before being tossed correctly records zero loss). Also fixed `kitchen_receive()`'s
  `kitchen_batch` mode (and the `kitchen_batch_receive()` duplicate endpoint, dead code from the
  UI but still a live URL) to set `item.cost_price = cost_total` at receive time — without it the
  new wastage Transaction's `qty * cost_price` would always price out to KES 0 regardless of how
  much was actually lost, since that mode never touched `item.cost_price` unlike the
  `portion`/`batch` receive modes. 6 new tests. **Theme 3 (access-control scoping):** `_kb_gate()`
  — the shared gate for `kitchen_batch_receive`, `deplete_kitchen_batch`, `discard_kitchen_batch`,
  and `kitchen_consumable_add` — only checked for ANY open shift, not specifically a kitchen one.
  `kitchen_batch_receive` happened to be separately protected by its own
  `can_receive_kitchen_stock` check; the other three had no protection at all — a bar-only
  staffer could deplete/discard a kitchen batch or log a kitchen consumable purchase directly.
  Fixed once at the shared gate. Also added the same check to `kitchen_stats_api` and
  `kitchen_consumable_pool_api` (read-only, lower stakes, but the Station Scoping Principle
  explicitly calls out revenue visibility). 9 new tests. **Verified already correct:**
  `kitchen_tabs_list` (already scoped via `_station_scope()`), `deplete_kitchen_batch`/
  `discard_kitchen_batch`'s own idempotent status guards, `KitchenBatch.days_open` staleness
  display (informational only by design, matching `KegBarrel.is_stale()`),
  `_auto_complete_stale_sessions` and the shared shift/tab machinery (already fixed generically
  for both stations in the bar-module audit). 21 new tests total, 205 tests pass. Two commits:
  `fa36514`, `23d14b0`.
- Fix: anonymous tab creation across all three counters (2026-07-19). Before resuming the
  remaining systemic-audit scope, Roy asked to first verify the original business requirement
  that motivated building the wall-QR + PIN system in the first place: during high-traffic
  sales, staff often have no time to type a customer's name into a tab, so the tab must still
  open — the customer identifies themselves later by scanning the wall QR and entering their
  PIN. Traced the code and found all three counters silently broke this. `bar_board`
  (keg_views.py): `if payment_method == 'tab' and tab_customer:` skipped tab creation entirely
  on a blank name; `KegBarrel.record_sale`'s `pay = 'credit' if tab else (payment_method or
  'cash')` then fell through to the literal string `'tab'` — not a recognized payment_method —
  with no BarTab, no PIN, no way to ever find the sale again via BillScan. `_kitchen_checkout`
  (kitchen_views.py): identical pattern, `elif payment_method in ('food_tab', 'bar_tab') and
  tab_customer:` — blank name meant `txn_pm` fell back to the literal `'food_tab'`/`'bar_tab'`
  string. Quick Sell (views.py): `payment_method_qs` was already correctly `'credit'`
  regardless of name, but the tab-creation block was gated on `credit_recipient` truthy — a
  blank name meant no BarTab was created and the per-line Transactions (already saved earlier
  in the loop with `recipient=''`) became an orphaned, unattributed credit/debt entry. Fixed
  all three the same way: never search for an existing tab by a blank name (that would
  silently merge two different anonymous customers' bills into one) — always create a
  brand-new tab, then backfill `customer_name = f'Tab #{tab.id}'` immediately so the tab is
  still fully usable by name, findable via wall-QR PIN lookup, and convertible to debt like any
  other tab. Quick Sell additionally backfills the already-saved `Transaction.recipient` fields
  once the fallback name exists. 9 new tests (`AnonymousBarTabTest`, `AnonymousKitchenTabTest`,
  `AnonymousQuickSellTabTest`). No migrations. 214 tests pass. Next: resume remaining systemic
  audit scope (Quick Sell, supply chain/procurement, debt tracker, analytics).
- Quick-Sell-Module Audit Theme 1 (2026-07-19): money-path idempotency. First theme of the
  next module in the systemic-audit queue. Headline finding: `ProduceBunch.record_sale()` had
  no single lock-safe entry point shared across all its callers. `KegBarrel` had
  `record_sale_locked` from the start, and the kitchen-module audit locked kitchen board's own
  `bunch_id` branch — but two more call sites were still racing: `produce_views.py`'s
  `_sell_item_amount`/`handle_bunch_cart_entry` (Quick Sell's own greens/mix cart lines, a
  separate call path from kitchen board's) and `ProduceBunch.sell_mix()` itself, plus **both**
  STK settlement callbacks in `mpesa_views.py` (kitchen and Quick Sell cart STK settle) called
  `record_sale()` directly with no lock at all — an STK callback racing a counter sale of the
  same bunch is a realistic scenario (Safaricom retries, or staff selling the last of a batch
  while a customer's payment confirms), not just a double-tap. Added
  `ProduceBunch.record_sale_locked()` (mirrors `KegBarrel.record_sale_locked`) as the single
  classmethod entry point and routed all five call sites through it, including refactoring
  kitchen board's own inline `atomic()` block to use it instead of duplicating the lock logic.
  Also found and fixed three missing idempotency guards, same gap class already closed for
  `receive_barrel`/`add_cups`/`kitchen_receive` in prior audits: `produce_views.receive_bunches()`
  (the "+From Market" modal — creates ProduceBunch envelopes or a PORTION Receipt transaction),
  `owner_consumption_views.record_owner_consumption()` (the "🥃 Mmiliki Alichukua" modal — a
  duplicate would double-deduct stock as an owner draw with no sale to match it against), and
  `views.add_transaction()`'s AJAX `quick=1` branch (the "+📦 Pata Stok" modal, built for fast
  restocking mid-shift under time pressure — the same busy-counter conditions behind every other
  idempotency fix in this app; scoped to the AJAX branch only, the normal full-page form is
  untouched). Quick Sell's own main checkout (`quick_sell()`) already had a guard from the
  2026-07-15 sprint — verified still correct, not re-touched. 8 new tests. No migrations. 222
  tests pass. Next: Theme 2 (state-transition completeness) for Quick Sell.
- Quick-Sell-Module Audit Theme 2 (2026-07-19): state-transition completeness. Two findings.
  (1) Restock-notify parity gap: bar board and kitchen board both let staff raise a restock
  request ("🔔 Notify") directly from an out-of-stock tile without leaving the point-of-sale
  screen mid-shift; Quick Sell — the busiest, most general-purpose selling surface — was the
  only one of the three counters missing this, forcing staff to navigate away to Stock List.
  `quick_sell()` now annotates items with `has_pending_restock` the same way `stock_list()`
  already does, and the item grid shows the same "🔔 Notify" / "📦 Requested" affordance,
  wired to the same `/stock/restock/request/` endpoint. (2) Silent bunch/mix sale failure: a
  regular out-of-stock item already gets a `messages.warning` ("Skipped X: only Y in stock"),
  but a depleted/closed `ProduceBunch` cart line (greens/mix sales) failed completely silently
  — no success, no error, the line just vanished. The client already blocks adding an empty
  bunch tile to the cart, but that check is against a snapshot fetched when the greens board
  last loaded, not at checkout time, so a concurrent sale can still deplete the bunch in the
  gap between tap and checkout — if it was the only line in the cart, the whole checkout
  attempt produced zero feedback to the cashier. Added the same warning-message pattern
  already used for regular items. 3 new tests. No migrations. 225 tests pass. Next: Theme 3
  (access-control scoping) for Quick Sell.
- Quick-Sell-Module Audit Theme 3 (2026-07-19): access-control scoping. Third and final theme
  of the Quick Sell audit. **CRITICAL finding — cross-tenant item write in `add_transaction()`**:
  the target `Item` was fetched via `get_object_or_404(Item, id=item_id)` with NO business
  filter at all. Any authenticated staff member of ANY business could submit another business's
  `item_id` and write bogus Receipt/Issue/Wastage transactions straight into a stranger's stock
  records — corrupting their balances, P&L, and triggering false restock/expiry alerts.
  Reachable via the normal Add Transaction form AND Quick Sell's "+📦 Pata Stok" `quick=1` AJAX
  path. A full grep sweep confirmed every OTHER `get_object_or_404(Item, id=...)` call site in
  the codebase already scopes by `store__business`/`business` — this one was an isolated miss,
  not a systemic pattern. Fixed to `store__business=user_profile.business`, matching the
  established pattern everywhere else. 3 new tests including a direct two-tenant regression
  lock. Manager access gap: Sprint M1 made Quick Sell's "+From market" button visible to
  managers (`QS_IS_OWNER = is_owner_or_manager`), but `receive_bunches()` and
  `produce_board()`'s `can_receive` flag were both left as strict `is_owner` — a manager could
  see and open the receive modal, submit it, and be silently rejected by the server with a 403.
  Fixed both to `is_owner_or_manager`, matching `receive_barrel`/`kitchen_receive`. Shift-gate
  gap: `discard_bunch()` (write off a wilted/unsold `ProduceBunch` as wastage) was missed by the
  Sprint SG universal shift-gate sweep entirely — sibling wastage actions (bar's
  `record_breakage`, kitchen's `discard_kitchen_batch`) both require an open shift for non-
  owner/manager staff; `discard_bunch` had no gate at all. Fixed to match. 7 new tests. No
  migrations. 232 tests pass. **Quick Sell module audit complete** (all 3 themes). Next: resume
  remaining scope — supply chain/procurement, debt tracker, analytics.
- Sprint Reset (2026-07-21): "Reset Sales & Analytics" — owner-only, permanent wipe of a
  business's sales/transaction/analytics history, for businesses (starting with a bar client
  hit by weeks of staff non-compliance) that need a genuine clean slate without deleting the
  account. Deliberately a HARD delete, not a soft cutover-date filter — a soft filter would need
  a new query condition threaded through dozens of separate analytics/dashboard call sites
  app-wide, too large and too easy to miss one. `SalesResetLog` (core/models.py, migration 0106)
  mirrors `accounts.AccountDeletionLog`'s pattern — created BEFORE the destructive delete runs,
  inside the same `transaction.atomic()` block. Two-step flow (`core/reset_views.py`): Step 1
  downloads a full backup workbook (one sheet per wiped model, reusing the existing
  `openpyxl.Workbook()` export pattern) and sets a session flag; Step 2 requires typing the
  business's own name (not a fixed phrase — disambiguates if the owner has ever run more than
  one business) and re-checks the session flag server-side. 24 models wiped via
  `.filter(business=business).delete()` (Transaction, Receipt, BarTab, Shift, KegBarrel,
  ProduceBunch, KitchenBatch, KitchenConsumableLog — has its own direct business FK, does NOT
  cascade from KitchenBatch — Payment, CustomerDebtPayment, Customer, PerformerSession,
  StockRequest, BusinessExpense, PettyCash, SalaryPayment, SalaryDeduction, StockTake, Order,
  Forecast, TableOrder, BarCupLog, ProduceOverhead, ItemSaleApproval,
  PendingTransactionPrompt) plus Notification (no direct business FK, scoped via
  `user__userprofile__business`). Explicitly kept: Item, ItemPortionPreset, Store, Category,
  Business + settings, UserProfile, RecurringExpense (rule definitions), Performer (roster),
  RevenueTarget (goal config), CapitalInvestment (durable business fact, shown but not wiped).
  Explicitly excluded as out-of-scope marketplace/cross-business data: Feedback (has both
  from_business/to_business FKs), SupplierRelationship/SupplierBid/SupplierApplication/
  ProcurementRequest/PurchaseOrder — wiping one side of a two-business relationship for a
  single-business reset would orphan the other side's copy. Stock balances: deliberately
  zeroed (`opening_bin_balance`/`opening_physical` bulk-set to 0), NOT frozen from the
  pre-reset computed `current_balance()` — Roy's own catch during planning: the computed
  balance reflects the non-compliant period and would just enshrine bad data. Instead a new
  Fresh Stock Count checklist (`fresh_stock_count_checklist` + `mark_item_recounted`) guides
  the owner through the EXISTING "⚖️ Rekebisha" adjust-stock-balance tool
  (`core/stock_take_views.py:adjust_stock_balance`, unchanged) for every non-keg/non-produce
  item with no transaction since the reset; `stock_list.html` now supports
  `?adjust_item=<id>` to auto-open that exact modal when linked from the checklist (no
  duplicated modal logic). `mark_item_recounted` handles the one gap in reusing Rekebisha
  as-is: an item genuinely still at zero produces `no_change` there and would never leave the
  checklist, so this creates an explicit qty=0 `[ADJ]`-tagged Transaction instead, reusing the
  exact same convention `adjust_stock_balance` already uses to stay invisible to the "missing
  cost price" home alert. Owner-only nav entry added to both desktop and mobile Manage
  dropdowns (base.html) next to Business Settings. Housekeeping: CLAUDE.md's "Render (free tier
  web service)" corrected to Starter — Roy has since upgraded. 12 new tests including a direct
  two-business isolation regression lock (the critical one for a feature like this) and a
  marketplace-exclusion regression lock. No app-visible migrations beyond `SalesResetLog`. 244
  tests pass. Next: Liquor/Spirits Catalogue (Feature 2 of this sprint — one-time price-list
  enrichment of the existing static BAR_CATALOG plus a reusable per-business supplier-list
  upload feature).
- Sprint Reset, Feature 2 (2026-07-21): Liquor/Spirits Catalogue. Roy uploaded a real supplier
  price list (846 raw SKU lines) and wanted it turned into a proper bar catalogue — both a
  one-time enrichment of the app's existing catalog AND a reusable "upload your own supplier
  list" feature for any business, going forward. **Shared engine** (`core/catalog_classify.py`,
  pure functions, no Django dependency): `detect_name_price_columns()` scores each column by
  text-ratio vs numeric-ratio rather than assuming a fixed layout, so a re-labelled/re-ordered
  sheet still parses; `extract_volume_ml()` handles the confirmed messy real cases (750ML, 70CL,
  1LT/LTR/LITRE, 1.5LT, `700ML(BMC)` distributor tags, `1/4`·`1/2`·`3/4` fraction notation, a
  confirmed `750M` typo); `classify_category()` is a Python port of `BAR_CAT_CONFIG` from
  item_form.html — **caught a real bug during testing**: naive substring matching misclassified
  "BAILEYS ORIGINAL" as a gin, because "gin" is literally a substring of "original" — fixed to
  word-boundary regex matching; `infer_reorder_defaults()` implements Roy's own judgment call
  (cheap/high-turnover items like Dallas/Blue Ice/Chrome quarters get bigger reorder buffers than
  slow-moving premium bottles); `classify_row()` builds its result via the *existing*
  `_spirit()`/`_beer()`/`_soda()`/`_cig()` helpers so every generated entry is schema-identical to
  the hand-curated catalog. **One-time enrichment**: `enrich_liquor_catalog` management command
  (preview-first, mirrors `import_products.py`'s convention — never writes directly to
  business_profiles.py or the DB) parsed the real file, deduped 12 matches against the existing
  BAR_CATALOG, and produced 834 new entries — spot-checked against Roy's own named examples
  (Dallas, Blue Ice, Chrome all correctly classified as spirits with size-appropriate reorder
  tiers) before commit. Result lives in its own file, `core/liquor_pricelist_catalog.py` (kept
  separate from business_profiles.py purely for size — 800+ literal dict entries would make that
  file unwieldy), imported and appended onto `BAR_CATALOG` (60 → 894 entries, `LIQUOR_CATALOG`'s
  existing filter-derivation needed no code change). ~78% of rows fall back to a generic 'other'
  category (no literal type keyword in the raw brand name) — expected and documented, not a bug;
  still valid sellable entries, just without spirit-specific pour presets. Uploaded spreadsheet
  deleted once consumed. **Reusable per-business upload** (`core/catalog_views.py`): new
  `CatalogUploadBatch` (job/audit header, business-scoped — distinct from the internal admin-only
  `ImportJob`) and `SupplierCatalogEntry` (one parsed entry per business, schema mirrors the
  static catalog's dict shape) models, migration 0107. `catalog_upload_process()` reuses the same
  classification engine and is idempotent (`update_or_create` keyed on business+raw_name — re-
  uploading updates in place, never duplicates); unparseable rows are counted with a capped
  sample kept on the batch, never silently dropped. **Bulk "Add from Catalogue" screen**
  (`catalog_bulk_add`, at `/stock/catalog/bulk-add/`) — `add_item` only ever creates one Item per
  POST, so this is a genuinely new bulk-create path: search/pick several catalogue entries at
  once (merging static + a business's own uploaded entries), confirm or edit the suggested cost
  price per item, optionally toggle "add portion presets" per item, submit once, all created
  atomically. Server re-resolves every selection against the same merged catalog rather than
  trusting client-supplied data; reuses `add_item`'s own `_resolve_category()` and sequential
  `MAT-####` material_no scheme (one counter across the whole batch, to avoid collisions); copies
  preset `qty` values straight through since the catalog entries already bake in the correct
  fraction-of-bottle math for their own size — no separate fraction-math port needed. **Two more
  real bugs caught by the test suite before ship**: an empty/missing `store_id` crashed with a
  bare 500 (`ValueError` from the ORM) instead of a graceful 400 — fixed to validate as an integer
  first; and `ItemPortionPreset.price` has no null option, unlike the catalog's own `'price':
  None` convention — `add_item` itself silently *skips* a preset row with a blank price rather
  than saving one, and dropping every generated preset would have defeated the whole point of the
  toggle, so this now creates them at an explicit `KES 0` placeholder (surfaced in the success
  message) for the owner to fill in via Edit Item. PDF price-list support explicitly deferred —
  logged as Next Sprint Candidate #6, not built now. 4 commits (`bacf39a` classify engine,
  `a8a2c9b` enrichment, `f62151a` upload, `c509a74` bulk-add), 23 new tests across the whole
  feature. 267 tests pass.
- Supply-Chain/Procurement Module Audit (2026-07-21). Next module in the systemic-audit queue
  after Quick Sell — this module (`procurement_views.py`, `marketplace_views.py`, PO/GoodsReceipt
  views in `views.py`) had never been through any C&E pass before; confirmed via a full research
  agent pass before touching code. Same three-theme structure, three commits. **Theme 1
  (money-path idempotency):** the module had neither of the codebase's two established
  double-submit protections anywhere — zero uses of `claim_checkout_token`, zero uses of
  `select_for_update()`/`transaction.atomic()`. `receive_goods()` — the most severe gap, matching
  exactly the "double-process a goods receipt" failure mode: its only re-entry guard was checked
  once against a plain fetch, never re-checked under a lock before the write, so two
  near-simultaneous submissions could each independently create a `GoodsReceiptLine`, increment
  `quantity_received`, and write a stock-in `Transaction` — double-counting one physical delivery.
  Fixed with `claim_checkout_token` + `transaction.atomic()` + `select_for_update()` on the PO and
  each line, re-checking status under the lock. `award_bid()` had no guard against a bid already
  being `accepted` before proceeding — a double-click could create a second draft `PurchaseOrder`
  with duplicated lines and re-fire supplier notifications; fixed with `select_for_update()` +
  a status check scoped tightly around just the DB-critical writes (notifications/PO-creation
  correctly stay outside the lock, but are now unreachable on retry since gated behind it).
  `purchase_order_create`/`edit` got idempotency tokens too, for consistency. 5 new tests.
  **Theme 2 (state-transition completeness):** three of `notifications.py`'s procurement
  functions (`notify_new_bid_opportunity`, `notify_supplier_bid_received`,
  `notify_supplier_bid_awarded`) referenced fields that don't exist on `ProcurementRequest`
  (`item_description`/`quantity`/`unit`/`budget`/`location` — the real fields are
  `title`/`description`/`budget_min`/`budget_max`) and filtered `SupplierApplication` on a
  nonexistent `business` field (real pairing: `applicant`/`target_business`) — every call site
  wraps these in a blanket `try/except`, so they've silently no-op'd on every call since written;
  suppliers have never actually been notified of a new opportunity. Fixed all three.
  `PurchaseOrder.status` was a directly user-editable form field offering all five
  `STATUS_CHOICES` including `part_received`/`received`/`cancelled` — none of which are supposed
  to be reachable except as a *consequence* of `receive_goods()` actually processing a delivery;
  a PO could be hand-set to "received" with zero stock ever moving. `PurchaseOrderForm` now
  restricts the field to `draft`/`ordered` only. Neither `ProcurementRequest` nor `PurchaseOrder`
  had a cancel path despite both defining `cancelled` in `STATUS_CHOICES` — added
  `cancel_purchase_order()` and `cancel_procurement()`, both idempotent, both wired into their
  detail templates. Added `PurchaseOrder.awarded_bid` FK (migration 0108) — the only prior link
  from an auto-created draft PO back to the bid/procurement that spawned it was a free-text
  sentence in `notes`. Bid-completion (`confirm_delivery`/`confirm_payment`) and PO-receiving
  (`receive_goods`) are two entirely separate state machines that could silently diverge — an
  owner could confirm delivery and let the procurement close as "done" while the linked PO sat
  unreceived forever with zero stock added; not auto-linking them (would silently move stock the
  owner never reviewed) but `confirm_delivery` now uses the new FK to warn visibly when this has
  happened. 12 new tests. **Theme 3 (access-control scoping):** **CRITICAL** —
  `purchase_order_edit()`'s `item` field queryset was only ever restricted to the current
  business in the GET/re-render path, never before `formset.save()` on a successful POST (unlike
  `purchase_order_create()`, which has an explicit manual guard for exactly this) — an
  authenticated user could inject a `PurchaseOrderLine` referencing ANY other business's `Item`,
  which `receive_goods()` would then use to write a real stock-in `Transaction` against a
  stranger's `Item`, corrupting their balance. Fixed by restricting the queryset before
  validation in both views. `procurement_detail()` had zero business scoping at all — any
  authenticated user could view any business's procurement request by guessing/incrementing
  `pk`, leaking title/description/budget/deadline for closed/cancelled requests never meant to
  be publicly browsable; fixed to allow the buyer always, plus any supplier that has actually bid
  on it, redirecting everyone else to the properly-scoped browse page. Also found:
  `procurement_views.py`/`marketplace_views.py` were the only two files in the app that never
  received Sprint M1's `owner_or_manager_required` sweep — every operational action here was
  still hard-gated to `profile.is_owner` only; replaced all 13 occurrences with
  `profile.is_owner_or_manager`. 8 new tests including a direct cross-tenant regression lock (the
  critical one) and a direct scoping-leak regression lock. Three commits (`1c3c799` Theme 1,
  `81d0832` Theme 2, `305a973` Theme 3), 25 new tests total, 292 tests pass. **Supply chain/
  procurement module audit complete** (all 3 themes). Next: resume remaining scope — debt
  tracker, analytics.
- Debt Tracker Module Audit (2026-07-21). Next module in the systemic-audit queue after supply
  chain/procurement. Same three-theme structure against `debt_views.py`, `mpesa_views.py`,
  `keg_views.py`, `shift_views.py`, and `credit_policy.py`. **Theme 1 (money-path idempotency):**
  the three debt-settlement functions in `mpesa_views.py` that create a `CustomerDebtPayment`
  from an STK Push (`_create_debt_payment_from_receipt`, `_settle_debt_customer_from_payment`,
  `_settle_receipt_entries_from_payment`) are each called from BOTH `mpesa_callback` (the Daraja
  webhook) and `payment_status` (the JS poll) — a realistic race (Safaricom retries, or the poll
  landing moments before the callback), not just a double-tap. Their only guard was "skip if a
  `CustomerDebtPayment` already exists with this mpesa_ref in its notes" — silently skipped
  entirely whenever `mpesa_ref` was blank, which is exactly the callback/poll race window before
  the receipt number has been captured. New `Payment.debt_settled` BooleanField (migration 0109)
  + `select_for_update()`, mirroring `kitchen_settled`/`qs_settled` exactly, closes this for all
  three — they're mutually exclusive per Payment row (routed by `if payment.receipt_token: ...
  elif payment.bar_tab_id: ... elif payment.debt_customer_id: ...`), so one shared flag is
  correct. Also found two STK-*initiation* gaps with no protection at all against a rapid
  double-tap firing two separate M-Pesa prompts to the same phone (a real double-charge risk if
  the customer approves both, not just a duplicate record): `debt_stk_push` (staff-initiated, the
  debt tracker page's "Send STK" button) had neither a client-side button-disable NOR a
  server-side guard — fixed with `core.idempotency.claim_checkout_token` plus a client-side
  in-flight flag; `receipt_pay`'s STK branch (customer-initiated from the public receipt/BillScan
  page, both debt-block and entry-selection modes) already disabled its button client-side but
  had no server-side backstop — added `claim_checkout_token` there too for parity with every
  other checkout surface in the app. `record_debt_payment` (the plain `<form>` "Record Payment"
  button) had no submit guard at all — a double-click or back-button resubmission would create a
  second real `CustomerDebtPayment`; fixed with a hidden `idempotency_token` field (refreshed on
  each modal open) plus a submit-time button-disable, matching the write-off request form's
  existing `_woSubmitted` pattern. 8 new tests. **Theme 2 (state-transition completeness):** none
  of the three tab-to-debt conversion sites (`convert_tab_to_debt`, `bulk_convert_tabs_to_debt` in
  `keg_views.py`, `_convert_open_tabs_to_debt_for_shift` in `shift_views.py`) ever called
  `evaluate_credit()` — confirmed this is by design, not a gap to close the same way the K3 hard
  gate closes new-credit-issuance points: by the time a tab exists to convert, the goods are
  already served, so blocking the conversion would only make the debt invisible, not undo the
  sale (same non-blocking reasoning as the procurement audit's `confirm_delivery` warning). New
  `notify_owners_of_conversion_risk()` in `credit_policy.py` calls `evaluate_credit()` post-hoc and
  fires a non-blocking in-app + SMS heads-up to owners/managers when the customer is already
  blocked-tier (revoked/permanent defaulter/overdue/strikes/limit/cutoff) or warn-tier — wired
  into all three conversion sites, so a compounding risk that was previously invisible now
  surfaces without changing the conversion's outcome. Also fixed a real inconsistency:
  `convert_tab_to_debt`'s auto-created `Customer` was missing `credit_approved=True`, unlike its
  two sibling sites — meaningless noise otherwise, since a brand-new customer would trivially
  "fail" `evaluate_credit()`'s check #1 for never having been asked to pre-approve credit in the
  first place. Separately: `approve_write_off` (an unrecoverable, uncollectable credit loss — the
  business eats it) never set `Customer.is_defaulter=True`, unlike the equally-final `void_tab`
  path recording the exact same real-world fact ("this debt was never repaid"); fixed to match.
  Also corrected this file's own Sprint K3 entry, which incorrectly claimed the credit gate was
  wired at "Bar Board tab creation" — it never was; annotated in place rather than rewritten, so
  the historical record stays intact. 6 new tests. **Theme 3 (access-control scoping):**
  `request_write_off` had no station gate at all — a bar-only staffer could pass any `txn_id` and
  both see (item name, amount, customer name) AND act on a kitchen credit transaction, and vice
  versa, even though the write-off button in the UI only ever renders for same-station lines.
  Fixed using the existing `_station_scope(up)` helper (`core/views.py`), same discriminator
  (`item.store.is_kitchen`) already used everywhere else in the app; owner/manager unaffected
  (always see both). Everything else audited and found already correct: `_debt_scope()`,
  `customer_debt_statement`, `clear_defaulter`, `toggle_credit_approval`,
  `update_customer_credit_settings` (intentionally all-staff per this file's own conventions),
  `manager_review_write_off`/`reject_write_off`. 4 new tests. 18 new tests total across all three
  themes, 310 tests pass. **Debt tracker module audit complete** (all 3 themes). Next: resume
  remaining scope — analytics (the final module in this audit series).
- Analytics Module Audit (2026-07-21) — **final module of the systemic Cause-and-Effect audit
  series.** Against `core/analytics_views.py`, `core/views.py` (analytics-adjacent), `core/
  recurring_expense_views.py`, `core/notifications.py`, `core/api_views.py`. Research done via a
  full Explore-agent pass (1922-line analytics_views.py + adjacent files read in full) then
  independently verified before fixing. **Theme 1 (money-path idempotency):**
  `recurring_expense_confirm` — the only real "write" in the whole analytics surface — had a
  check-then-create race with no lock: `already_posted_this_period()` was a plain `.exists()`
  query, so two near-simultaneous "Confirm & Post" submits could both pass it before either
  `BusinessExpense.objects.create()` committed, double-posting a recurring line (often a salary or
  rent — this module's biggest cost lines) straight into `net_profit`. Fixed with
  `claim_checkout_token` (this app's standard form-double-submit backstop) plus
  `select_for_update()` on each `RecurringExpense` row, re-checking the state under the lock.
  Separately: `daily_summary_webhook`/`send_daily_summary` (the module's one scheduled/cron job)
  had zero dedup state — a duplicate cron fire, a manual retry, or anyone hitting the webhook with
  the documented hardcoded-fallback `CRON_SECRET` would re-send today's summary SMS+email to every
  business's owner. New `Business.last_daily_summary_sent_at` (accounts migration 0048, same
  convention as `last_txn_sms_at`'s bundling window) now blocks a same-day resend. 4 new tests.
  **Theme 2 (state-transition completeness):** the `Forecast` model is fully orphaned — `git log`
  confirms its populating management commands were deliberately deleted in commit `ad99715`
  ("purge: delete old pandas/matplotlib forecast infrastructure"); the live "Run Forecast" button
  now calls `forecast_api` → `forecast_engine.run_ets/run_regression`, which compute on demand and
  never persist. Nothing in the codebase creates a `Forecast` row. Not a "cause without effect" bug
  in the usual sense (nothing is left dangling — it's simply 100% dead code), so left in place
  rather than deleted (a future caching layer may revive it) but annotated in the model docstring
  so a future reader doesn't re-walk the same investigation. Everything else in this theme —
  `RecurringExpense`'s review→confirm cycle, `RevenueTarget` as persistent config rather than a
  period job — checked out already complete. **Theme 3 (access-control scoping):** three read
  endpoints were JSON/API siblings of pages that ARE correctly gated, but had no gate of their
  own — the exact "read-only sibling has weaker gating than its page" shape this audit series
  already caught once before (`kitchen_consumable_pool_api`, 2026-07-19 Kitchen audit):
  `analytics_api` (JSON trends, sibling of `analytics_dashboard`) and `forecast_api` (the literal
  endpoint `analytics.html`'s owner/manager-gated "Run Forecast" button POSTs to) both gained an
  inline `is_owner_or_manager` check — not `owner_or_manager_required`, which redirects on failure
  and is wrong for a JSON endpoint (see the Known Issues entry on `@login_required` + AJAX); DRF's
  `business_summary` (returns `today_profit`, the single most sensitive figure by this app's own
  convention) gained a new `IsOwnerOrManager` DRF permission class alongside the existing `IsOwner`/
  `HasBusiness`. Also found: `daily_sales` (`/daily/`, intentionally open to all staff, already
  correctly station-scoped) rendered its aggregate wastage cost-lost KES figure to every role with
  no gate — inconsistent with `UserProfile.can_input_cost_price`'s own convention that non-owner
  staff never see cost price; fixed by wrapping just that KES span in `{% if is_owner %}` in
  `daily_summary.html` (the wastage list itself — item/qty/notes — stays visible to staff, who
  already log it themselves). And a station-scoping inconsistency inside `analytics_dashboard`
  itself: `keg_barrels_period` (feeding both the Bar/Keg Analytics table and Per-barrel P&L table)
  had no `item__store__is_kitchen` exclusion, while the Staff Pouring League section a few lines
  later — over the same conceptual "this business's keg barrels this period" — already excludes
  kitchen explicitly. Nothing currently prevents `Item.is_keg=True` under a kitchen store, so a
  future kitchen-side keg feature (or a data-entry mistake) would have silently double-counted that
  barrel's revenue into both the Bar Performance and Kitchen Performance sections, corrupting the
  owner's own Bar-vs-Kitchen split; fixed by adding the same exclusion. 9 new tests. 13 new tests
  total, 323 tests pass. **Analytics module audit complete (all 3 themes) — this closes out the
  full systemic Cause-and-Effect audit series** covering bar/keg, kitchen, Quick Sell, supply
  chain/procurement, debt tracker, and analytics (2026-07-19 through 2026-07-21).
- Post-audit live fixes (2026-07-22, commits `19fe724`→`79a4191`, log entries backfilled
  2026-07-22): five live production reports from Roy, each fixed and pushed same-day.
  `reconcile_kitchen_stores` hardened to check activity beyond just `Item` count (a Monsoon
  Inn store pair with zero items each on both sides was reported AMBIGUOUS by the original
  command). Fresh Stock Count checklist fixed to never include items created after the
  reset (new `Item.created_at`, migration 0111, null for pre-existing items and treated as
  "old enough"); Rekebisha's `?adjust_item=` deep-link and `mark_item_recounted`'s zero-count
  path both confirmed already correct. Tab-name blocking bug fixed at all three counters
  (bar board, kitchen board, Quick Sell) — anonymous tab creation (Sprint "anonymous tab
  creation," 2026-07-19) was implemented backend-only; the frontend JS in each counter's
  `completeSale()`/`doCheckout()` still separately blocked submission on a blank name,
  never actually reaching the backend path built to handle it — a frontend/backend split
  invisible to the backend test suite. Wall QR scan-to-view-bill fixed for two gaps: debt-
  converted tabs (status flipped to SETTLED with unpaid entries remaining) weren't found by
  `find_tab_search`'s plain `status='OPEN'` filter — new `_findable_tabs_qs()` helper
  (mirrors `receipt_views._get_live_tab_state`'s "effective status" reasoning) used by both
  PIN and name lookup; kitchen-only businesses' Wall Tab QR card was gated on
  `biz_profile.modules.keg` alone, hidden from a business with `has_kitchen` but no keg
  module — widened to `modules.keg or modules.kitchen`. Wall Tab QR print-to-PDF fixed —
  the CSS used `body > *:not(#wallQrBox) { display:none }`, which only hides DIRECT children
  of `<body>`; since the QR box is nested several levels deep, this hid an ancestor wrapper
  whose own `display:none` no descendant `display:block` can override — switched to a
  `visibility`-based isolation pattern (inherited but explicitly resettable at any nesting
  depth), the same class of fix already used elsewhere for print isolation. Kitchen Board
  quick-receive gained a "Muuzaji / Order No" field (reuses `Transaction.invoice_no`, the
  same field Add Transaction's Receipt flow already uses for this) after Roy shared a real
  Meatco chicken-pieces delivery receipt with no way to record the supplier — portion-mode
  receive only; staff already had `can_receive_kitchen_stock` from Sprint 20, confirmed
  functional, no new permission needed.
- Kitchen Batch raw-material sack tracking (2026-07-22 — 2026-07-23). Roy's own recurring
  complaint, escalated to "map it out properly": an ongoing sack of potatoes and "Imekwisha"
  (today's batch done) were being conflated — no visibility into how much of the SACK itself
  remained, separate from whether today's cooked batch was sold out. Cause-and-Effect map
  produced and reviewed before any code, per this file's own protocol; Roy's call was the
  full version, not the smaller MVP alternative also offered. `Item.raw_material_source`
  (self-FK, opt-in, migration 0112) lets a batch item (Chipo) point at a real trackable Item
  (Potatoes (Raw), unit=Kg) — received via the completely ordinary Receipt flow, so
  `current_balance()`, reorder-level restock alerts, and Rekebisha correction all apply with
  zero new mechanism. `KitchenBatch.open_batch()` (single locked classmethod, replaces
  duplicated inline logic in both `kitchen_receive` and the sibling `kitchen_batch_receive`
  endpoint) is the one entry point for opening a batch: if `raw_material_source` is set, it
  locks the raw item, validates enough balance exists, derives
  `cost_total = kg_drawn × raw_item.cost_price`, and logs the draw as a NEW Transaction type
  (`'Draw'`) rather than `'Issue'` — deliberately, so it's excluded BY CONSTRUCTION from
  every existing `type='Issue'`-filtered report across the app (Sales & P&L, Kitchen
  Performance, monthly COGS, `avg_daily_issues()`) with no per-report exclusion list to find
  and maintain — the `[ADJ]`/`[KBDRAW]`-tag pattern used elsewhere was considered and
  rejected here specifically because the blast radius (~40 call sites) made "audit every
  filter" far riskier than a type the ORM can't accidentally match. `avg_daily_issues()`
  broadened to `type__in=['Issue','Draw']` so raw-material reorder recommendations reflect
  real kitchen depletion. Items without `raw_material_source` keep the original manual
  cost-entry flow completely unchanged. **Found and fixed in the same effort — a real,
  pre-existing bug, not part of the original ask**: `Transaction.cost()` had no
  `kitchen_batch_id` branch, so every sale from a batch reported cost = the WHOLE
  `cost_total`, not a proportional share (see the Known Issues entry above for the full
  mechanism) — this was corrupting Kitchen Performance and `net_profit` for any business
  selling a batch more than once, independent of whether the sack-tracking feature is
  adopted. Kitchen Board: sack balance shown directly on the batch tile regardless of
  today's batch state; "Imekwisha" confirm reworded to say "BATCH YA LEO" explicitly.
  18 new tests (`KitchenBatchOpenBatchDrawTest`, `TransactionCostKitchenBatchProportionalTest`,
  `RawMaterialSackTrackingViewTest`, `ItemFormRawMaterialSourceTest`). 387 tests pass.
- Tabs drawer bug fixes (2026-07-23), from a live Roy report: two symptoms — visual
  "overlap/stain" when selecting one item to pay on a multi-item tab (with payment then
  applying to more than the selected item), and "Geuza Deni" (convert to debt) failing
  with "Hitilafu ya mtandao" — investigated across all three tabs drawers per this file's
  parity rule. **Root cause of the debt-conversion error, confirmed and fixed**:
  `_allowed_tab_sources(up)` (`core/keg_views.py`) never returned `'qs'` — by original
  design, meant to exclude Quick Sell tabs from the bar/kitchen station wall entirely, but
  the exclusion was implemented as "not in the allowed set" rather than "always allowed".
  `convert_tab_to_debt`, `update_tab_name`, `update_tab_phone`, and `tick_entry` all filter
  their object lookup directly on `tab.source` against this set (unlike `settle_tab`, which
  checks per-entry station instead and was unaffected) — meaning **every** Quick Sell tab
  404'd on "→ Deni" / rename / save-phone, for every user including the owner, since the
  feature shipped. Fixed by changing the set to always start with `{'qs'}` before adding
  `'bar'`/`'kitchen'` per station — matches how `tabs_list()`'s read side already treats
  'qs' tabs as unrestricted. 6 new tests (`TabStationScopingTest`), including a regression
  guard that the bar/kitchen station wall itself is unaffected. **Contributing/adjacent
  finding, also fixed**: `quick_sell.html`'s `qsSettleTab`/`qsSettleTabPartial`/
  `qsDoTabDebt`/`qsDoTabVoid` were the only tab-action handlers of the three drawers that
  threw away the response body on a non-2xx status before parsing JSON — masking every
  real `{ok:false, error:'...'}` response (shift-required, station-scope 403s, etc.) behind
  a generic "Hitilafu ya mtandao", unlike `bar_board.html`/`kitchen_board.html` which
  already parse JSON regardless of status. Fixed to match. **Root cause of the visual
  "overlap/stain", best available explanation** (JS/CSS rendering issues aren't directly
  testable by this suite — reasoned from code, not reproduced live): two compounding
  issues. (1) `quick_sell.html` and `kitchen_board.html` scattered `new bootstrap.Modal(el)
  .show()` calls at every modal-open site instead of reusing an existing instance, unlike
  `bar_board.html`'s already-correct `showModal(id)`/`hideModal(id)` singleton helper —
  calling `new bootstrap.Modal()` a second time on an element whose first instance hasn't
  finished `hide()`-ing (a realistic double-tap, or reopening the shared STK/settle/debt
  modal for a different tab in the same drawer session) leaks the first instance's
  `.modal-backdrop` permanently, since nothing ever calls `hide()` on it again — repeated
  opens stack up increasingly dark, stuck overlay layers behind whichever modal is actually
  interactive. Added the same singleton helper to both files and converted all 14 remaining
  raw `new bootstrap.Modal()` call sites (7 each) to use it; also fixed `kitchen_board.html`'s
  `submitKitchenTabDeni` unconditionally hiding its modal before checking `d.ok`, which
  buried its own error message on failure. (2) In both `bar_board.html` and
  `quick_sell.html` (not `kitchen_board.html`, which uses a different, modal-mediated
  settle flow that doesn't have this shape), the inline partial-selection row ("💰 Cash /
  📱 M-Pesa / 📲 STK" for checked items) sits directly above the full-tab "Lipa Yote — Cash /
  Lipa Yote — M-Pesa / STK Push" row, both always visible at once with similarly-labelled
  buttons — the most direct explanation found for "payment goes for both the selected and
  the unselected item": the backend's entry-filtering logic was re-traced twice and is
  correct, so a mis-tap on the wrong (but correctly-functioning) button is the more
  plausible mechanism than a hidden logic bug. Fixed by hiding the "Lipa Yote" row entirely
  whenever the partial-selection row is showing (`qs-tab-full-pay-<id>` / `tab-full-pay-
  <id>`, toggled in `updateQsSelectionUI`/`updateTabSelectionUI`) — Deni/Void stay outside
  this group since they aren't payment actions and have no partial equivalent to confuse
  them with. No migrations. 393 tests pass.
- Wall Tab QR standalone print page (2026-07-23), from a live Roy report: printing the QR
  from `payment_settings.html` produced 4 blank pages then a tiny QR on page 5, instead of
  one page with a large, centered QR. **Root cause**: the 2026-07-22 fix (documented in
  Known Issues below) correctly made the QR box itself printable via `visibility` instead
  of `display`, but `visibility:hidden` — unlike `display:none` — still reserves layout
  space, so the full height of the (very long) Payment Settings page survived into the
  print output and the browser paginated across however many pages that height spans. The
  box was then pulled to "the top" via `position:absolute`, but `absolute` positions an
  element relative to its nearest POSITIONED ancestor, not the page — some Bootstrap
  card/container between `<body>` and the QR box has its own `position:relative`, so the
  box anchored there instead, landing wherever that ancestor sits in the long page (near
  the bottom, since the Wall Tab QR card is one of the last sections) — hence a tiny QR on
  a late page rather than a large one on page one. **Fix**: new standalone page at
  `/stock/wall-qr/print/` (`wall_qr_print_page` in `core/keg_views.py`, owner-only,
  `templates/core/wall_qr_print.html` — no `{% extends "base.html" %}`, same proven
  standalone-page pattern already used by `session_promo_page.html`'s poster print) with
  nothing else on the page to interfere with pagination or positioning: bold "SCAN TO VIEW
  YOUR BILL" header at both the top and bottom (Roy's explicit ask), a single large QR
  (500px on screen, 480px in print — comfortably fits one A4 page with `@page { size: A4;
  margin: 10mm; }` and `page-break-inside: avoid` on the poster), and the same PIN-lookup
  hint text. `?print=1` triggers `window.print()` automatically 500ms after load (same
  convention as `session_promo_page`'s `?print=1`), giving QR generation time to finish
  first. Payment Settings' "🖨️ Print QR" button now opens this page in a new tab with
  `?print=1` instead of calling `window.print()` on itself; the old broken
  `visibility`/`position:absolute` print CSS block in `payment_settings.html` was removed
  as dead code — the small `#wallQrBox` preview there stays, unchanged, for an on-screen
  confirmation the QR looks right before printing. 6 new tests
  (`WallQrPrintPageTest`). No migrations. 399 tests pass.
- Split bill across two customers' tabs (2026-07-23), live request: Roy buys a 600 KES
  Smirnoff on his own tab, pays 400 himself, and his friend Bosco — who has his own,
  separate, already-open tab — agrees to cover the remaining 200 on his own tab instead.
  Nothing in the app could split one entry's amount or move any part of a bill onto a
  DIFFERENT customer's tab before this. Confirmed with Roy: any staff with an open shift
  can do this (not owner/manager-only — needs to work mid-shift without the owner
  present); the customer picking up the extra charge must be able to accept or reject it,
  either via SMS (phone kept optional) or on his own running tab/receipt; if rejected, the
  amount must revert to whoever proposed the transfer, with no extra work.
  **Design — why this needed no "reversal" logic and no double-counting risk**: split the
  entry immediately (paid portion settled on the source tab; unpaid remainder created as
  an ORDINARY unpaid `BarTabEntry` — new model `TabTransferRequest`,
  `PENDING`/`ACCEPTED`/`REJECTED`/`CANCELLED`) but keep the remainder sitting on the
  SOURCE customer's own tab (Roy's) until the destination customer (Bosco) actually
  accepts. Rejecting then needs zero reversal — the 200 never left Roy's tab in the first
  place, so nothing about the entry changes; existing surfaces (receipts, analytics, debt
  conversion, Z-reports) see a completely ordinary unpaid entry the whole time it's
  pending, because that's exactly what it is. Accepting is a single-field mutation
  (`entry.tab_id` reassignment, `BarTabEntry.transferred` → `TabTransferRequest.accept()`)
  — no new `Transaction` at accept time, so zero risk of double-counting revenue or
  re-incrementing a keg/produce/kitchen-batch envelope's `revenue_collected`. The ONLY
  new `Transaction`/`BarTabEntry` pair is created once, at split time, on the source tab:
  `qty=Decimal('0')` (re-billing an already-sold item, not a new sale — no additional
  stock left the shelf) and, if the original had a `keg_barrel`/`produce_bunch`/
  `kitchen_batch` FK, that FK is copied onto the new transaction too so
  `Transaction.cost()`'s EXISTING proportional-share formula correctly attributes the
  remaining cost share, without ever calling `record_sale()` again — a real gap a design-
  review pass caught: those three envelope models track revenue via a stored running
  counter incremented exactly ONCE at sale time; re-selling through the normal path would
  have inflated that counter and understated `cost()` for every OTHER sale drawn from the
  same barrel/batch, not just this one. `BarTabEntry.split_and_transfer_locked()` (single
  locked classmethod, `core/models.py`) is the one entry point; `TabTransferRequest.
  accept()`/`reject()`/`cancel()` complete the lifecycle. Inverse-action safeguard: if the
  source tab is voided or converted to debt while a transfer is still pending (added to
  `void_tab`, `convert_tab_to_debt`, `bulk_convert_tabs_to_debt`, and shift-close/auto-
  close's `_convert_open_tabs_to_debt_for_shift`), the pending request auto-cancels — the
  entry it refers to is leaving the ordinary open-tab lifecycle, so a pending request
  against it no longer makes sense. New endpoints in `core/keg_views.py`
  (`split_and_transfer_entry`, `respond_tab_transfer` — staff-side accept/reject, for when
  the customer confirms verbally without a phone) and `core/receipt_views.py`
  (`receipt_respond_tab_transfer` — public, token-authenticated, same security model
  `receipt_pay` already uses, no new token system needed); both station-scope-check
  `_allowed_tab_sources` against **both** the source and destination tab, matching the
  class of gap fixed earlier the same day (2026-07-23) in `tick_entry`/`settle_tab`/
  `convert_tab_to_debt`, which only ever checked one side. Pending requests surface in all
  three tabs drawers (`bar_board.html`, `quick_sell.html`, `kitchen_board.html` — a new
  🔀 "Gawanya" icon per unpaid entry, plus a Kubali/Kataa banner on the destination tab
  card) per the tabs-drawer-parity rule, and on the destination customer's own live
  receipt page (`receipt_public.html`, self-contained accept/reject that also picks up a
  newly-arrived request via the existing 20s live poll without a page reload).
  Notifications reuse established patterns exactly: SMS to the destination customer if a
  phone is on file (optional, never required) mirroring the cross-counter-merge SMS
  shape; in-app + SMS fan-out to the requesting staff member, everyone currently on
  shift, and owners/managers on accept/reject, mirroring `_fire_cash_payment_request`'s
  recipient pattern — a REJECTED transfer especially needs this, since the money is still
  sitting unresolved on the source customer's own tab and someone needs to go collect it
  from them directly. Migration `0113_tabtransferrequest`. 14 new tests
  (`SplitAndTransferEntryTest`). 413 tests pass.
- Split-transfer to a customer with no tab yet (2026-07-24), same-day follow-up: "Roy
  buys an 80 KES cup, pays 50, his friend Bosco — in the premises but not drinking right
  now, so nothing to pick from the destination list — covers the remaining 30." The split
  modal's destination picker (all three drawers) gained a "➕ Mtu asiye na tab" option that
  reveals a plain name field instead of the tab dropdown; the backend
  (`split_and_transfer_entry`, `core/keg_views.py`) now accepts `dest_customer_name` as an
  alternative to `dest_tab_id` — first checked against any already-open tab under that
  exact name (the SAME auto-detect-by-name pattern the cross-counter-merge feature already
  uses, so this can never silently create a duplicate tab for someone who already has
  one), and only opens a brand-new `BarTab` (via the existing
  `BarTab.create_with_credentials()`, source matched to the SOURCE tab's own station) if
  none is found. A brand-new destination tab has no `Receipt` yet at all, so
  `receipt_respond_tab_transfer` (keyed off a Receipt token) doesn't apply to it — added a
  parallel `tab_respond_tab_transfer` keyed off the tab's own `tab_receipt_token` instead,
  reachable from the bare `tab_live_view` page (`/tab/<token>/`, the fallback BillScan
  already uses for a tab with zero sales) via a new pending-transfer banner there, mirroring
  `receipt_public.html`'s. Refactored `_pending_transfers_in(receipt)` into a shared
  `_pending_transfers_for_tabs(business, tab_ids)` so both the receipt-based and bare-tab
  pages read from one source of truth. 6 new tests. Migration-free (reuses `0113`'s model).
  419 tests pass.
- Local dev migration hygiene note (2026-07-24, live report: "3 unapplied migrations" seen
  running the dev server): `python manage.py test` always creates and migrates its own
  separate, temporary test database — it never touches the real `db.sqlite3` `runserver`
  uses. Every session that ends with `makemigrations` + a green test run still needs an
  explicit `python manage.py migrate` against the real local DB before `runserver` will see
  the new tables/fields — this was skipped across a few sessions in a row (0111/0112/0113
  all landed unapplied locally, though each was committed with its migration file and had
  already been verified via the test suite's own isolated DB). Not a migration authoring
  bug; just a reminder this project's own end-of-sprint ritual should include it going
  forward for local dev, same as CI/Render already require via their own deploy-time
  `migrate` step.
- Debt-reasoning trail for split-transfers (2026-07-24), same-day follow-up to a live
  clarifying question: Roy pushed back on "what is Bosco's tab even FOR" and specifically
  asked that if the remainder is never resolved and becomes Roy's own debt, the system
  should explain WHY — "so when Roy later comes on... the receipt shows him how the debt
  occurred" — not just a bare "Kikombe — KES 30" line with no context. Root design
  question: `_get_customer_debt_data`/`customer_debt_statement`/`customer_debt_profile.html`
  all read `txn.item.description` (the item's fixed catalog name) for line items, NOT
  `BarTabEntry.description` — so an earlier idea of mutating the entry's description text
  on reject/cancel would have been invisible on every debt-facing surface; verified this by
  reading the actual template code before writing any fix, not assumed. Instead:
  `BarTabEntry.transfer_reason_note()` (`core/models.py`) reads the entry's own
  `transfer_requests` relation live (never bakes anything into stored text, so it can't go
  stale and every surface gives the same answer) and returns a short Swahili explanation
  — "Ilikuwa itafunikwa na Bosco, alikataa kulipa (ulishalipa KES 400 mwenyewe)" — for any
  entry whose most recent terminal-status `TabTransferRequest` was REJECTED or CANCELLED;
  empty string for the ordinary case for an entry with no such history, and — deliberately
  — also empty while still PENDING (nothing to explain yet). New `TabTransferRequest.
  paid_amount` field snapshots what the source customer paid at split time (Roy's 400 of
  an original 600) purely for display — avoids a fragile join back to the sibling entry
  that was reduced in place at split time — used both in this reason note and to enrich
  every "someone wants to add money to your bill" banner across all three tabs drawers,
  `receipt_public.html`, `tab_live.html`, and the request SMS with "X alishalipa KES Y
  mwenyewe" context, matching Roy's own example dialogue almost verbatim. Wired into
  `customer_debt_statement` (the line text customers actually see when they scan their own
  QR) and `customer_debt_profile.html` (the owner-facing ledger, same reasoning, so staff
  aren't left guessing either). Also surfaced live in the tabs drawer itself on any entry
  with a resolved-but-unaccepted transfer history (a `🔀` amber note next to the entry, extending the same
  JSON already used for the live pending badge) — deliberately reusing the model method at
  read time rather than duplicating its wording inline in the query-building code, so the
  Swahili phrasing only ever needs to be right in one place. 7 new tests. Migration adds
  only the one new field (`paid_amount`) to the existing `TabTransferRequest` table. 426
  tests pass.
- Wording/accountability audit (2026-07-24), same-day follow-up: Roy caught a real wording
  bug in the split-transfer debt-reasoning note itself — "Ilikuwa itafunikwa na Bosco"
  used "-funika" (the verb for capping a bottle or covering a plate) for a payment
  obligation, and generalized the complaint into a standing instruction: this app is a
  conversation with its users, on a transactional level as much as a literal one, and every
  reject/approve/reconcile flow should both reconcile figures correctly AND explain why in
  wording that actually fits the situation. Fixed the specific bug —
  `BarTabEntry.transfer_reason_note()` now uses "Ilikuwa inafaa kulipwa na {who}" (ought to
  have been paid by), addresses the reader as "wewe mwenyewe" (2nd person, since the debtor
  reads this on their OWN statement), and states the exact date+time the source customer
  paid their share, not just the amount — then ran a dedicated audit pass across every other
  reject/approve/reconcile flow in the app for the same two things: (1) natural,
  grammatically-correct wording — no literal/awkward word choices, correct language
  (Swahili-first, not English-only in an otherwise Swahili flow), correct grammatical
  person; (2) a comprehensive reasoning trail — who acted, when, why, and (where money/stock
  moved) what changed, surfaced to everyone the decision affects, not just the actor.
  Fixed, each independently tested: **DJ/MC session cancel** (`performer_views.py`) — used
  to be a bare status flip with no reason and no notification; `PerformerSession` gains
  `cancel_reason`/`cancelled_by`/`cancelled_at` (migration 0115), `session_update`'s cancel
  action captures an optional reason and notifies whoever booked it + owners/managers; fixed
  a real typo in the same pass — the approve button read "✓ Idhibiti" ("control/regulate")
  instead of "✓ Idhinisha" ("approve"); approve now also returns a `message`. **Produce
  discard** (`produce_views.py`/`quick_sell.html`) — `discardBunch()` hardcoded
  `reason=Wilted` regardless of what the confirm dialog asked, and the confirm dialog itself
  was in English; now prompts for a real Swahili reason and echoes it back in the success
  toast. **Petty cash review** (`petty_cash_views.py`/`petty_cash_list.html`) —
  `review_petty_cash()` returned a bare `{'new_status': ...}` and never told the staffer who
  recorded the entry whether it was approved or rejected; now builds a message with amount,
  reason, reviewer name and timestamp, notifies `entry.recorded_by`, and the reviewing
  owner sees the same message via a toast + a persisted "Imekaguliwa na X — timestamp" line
  on the card. **Shift-close auto-conversion** (`bar_board.html`/`kitchen_board.html`) —
  `close_shift()` already computed `auto_converted_names` (customers whose open tab was
  silently converted to debt because the shift closed past business hours) but neither
  board ever displayed it, unlike the sibling `open_tabs` (still-open) warning which bar
  board already showed; added the same raspberry-toned banner to both boards (kitchen board
  was additionally missing the `open_tabs` warning entirely — added for parity) explaining
  which customers were converted and why, plus stale-banner cleanup so a previous close's
  notice doesn't linger when the modal reopens. **Write-off approval** (`debt_views.py`) —
  `approve_write_off()`'s JSON response was missing a `message` key entirely, unlike sibling
  `reject_write_off` — the owner-facing JS already did `d.message || 'Imeidhinishwa.'` and
  had been silently falling back to the generic text on every approval since it shipped;
  fixed. Separately, `_mark_receipt_write_off()` used to just hide the matching receipt
  line (`display:none`) with zero trace — a line vanishing from a customer's own bill reads
  as a bug, not as "the business cleared this for you"; now the line stays visible, struck
  through, with a "✕ Imefutwa na biashara" badge and the exact write-off date/time, on both
  the static receipt block and the live-polling debt-tab render path (which required
  splitting `payableLines` from the display `lines` so a written-off line is shown but
  never payable and never counted in the outstanding total). **Tab void wording**
  (`keg_views.py`/`bar_board.html`/`quick_sell.html`) — voiding a tab used "Imetupwa"
  ("thrown away," the verb for a physical object like litter) for a financial cancellation;
  same bug class as the split-transfer fix. Changed to "Futa"/"Imefutwa" (cancel/cancelled)
  across the button labels, modal copy, and default reasons in all three call sites (bar
  board, Quick Sell, plus a stray English "Void" badge on `staff_duty_log.html` and two more
  English "Void" buttons on `quick_sell.html`/`kitchen_board.html`). **Discard defaults** —
  `discard_barrel`'s default reason (a real physical object, correctly kept as "-rudisha"/
  returned) was mixed English/Swahili ("Imerudishwa / discarded"); kitchen batch discard's
  JS fallback was the bare noun "Taka" (not a real sentence); both now send a real Swahili
  "sababu haikuelezwa" (no reason given) default, matching the pattern used everywhere else
  a reason is optional. **Bottle/stock breakage** (`keg_views.py`/`bar_board.html`) —
  `record_breakage()` was entirely English-only end to end ("Item not found", "Invalid
  quantity", "Please select an item.", "Wastage recorded.") and returned bare `{"ok": True}`
  with no notification at all for a real stock/money-loss event; now Swahili throughout,
  returns a message with item, qty, estimated KES loss, reporter name and timestamp, and
  notifies owners/managers (mirroring the petty-cash notification shape). **Credit
  actions** (`debt_views.py`) — `clear_defaulter()`'s notification said the customer
  "amesamehewa deni la zamani" (has been forgiven the old debt) — this action only lifts the
  defaulter block and re-approves credit; any actual balance is untouched and still owed,
  a separate write-off decision. Rewrote to say plainly that this does NOT forgive any
  debt, with reviewer name + timestamp. `toggle_credit_approval()` was an English-only
  django-i18n string in an otherwise all-Swahili file; replaced with a Swahili f-string,
  also carrying reviewer + timestamp. **Stock variance review**
  (`stock_take_views.py:review_variance`) — neither accept nor dismiss named who acted or
  when, and only `dismiss` notified the staffer who reported the variance — `accept` (an
  equally final decision on the same reported explanation) left them with no idea their
  explanation had been accepted; both branches now include reviewer + timestamp in the
  returned message, and `accept` notifies the reporting staffer just like `dismiss` already
  did. **Reset Sales reason** — `SalesResetLog.reason` was captured and stored at reset
  time but never displayed anywhere; `reset_sales_complete.html` now shows it (plus who
  performed the reset) on the confirmation page the owner lands on immediately after. **Table
  order cancel** (`order_views.py`/`waitress_screen.html`/`bar_board.html`) — had two
  separate cancel paths (`cancel_table_order` for the waitress screen, and
  `update_table_order`'s CANCELLED transition for the bar-board queue drawer's `oqUpdate`
  shortcut) and neither captured a reason, notified anyone, or spoke Swahili in its error
  messages ("Order not found", "Order cannot be cancelled", "Permission denied"); the
  bar-board cancel button additionally had no confirm dialog at all. `TableOrder` gains
  `cancel_reason`/`cancelled_by`/`cancelled_at` (migration 0116, same shape as
  PerformerSession's); both paths now prompt for an optional reason, and a shared
  `_notify_order_cancelled()` helper tells whichever side of the order didn't do the
  cancelling — the waitress who placed it, or the on-duty bar staff/owner/manager — noting
  explicitly when an already-ACCEPTED/READY order (mid-prep) was cancelled. 34 new tests
  across all of the above (`PerformerSessionCancelApproveTest`,
  `DiscardBunchShiftGateTest` additions, `PettyCashReviewMessageTest`,
  `CloseShiftAutoConvertedNamesResponseTest`, `WriteOffApprovalExplainsItselfTest`,
  `RecordBreakageExplainsItselfTest`, `DebtReasoningWordingTest`,
  `StockVarianceReviewWordingTest`, `TableOrderCancelReasonTest`, plus the reset-sales
  reason-display test). Two migrations (0115, 0116), both additive. 455 tests pass.
- Live bug triage (2026-07-24), same day: three items Roy flagged before continuing to the
  next feature. (1) Tabs drawer "stain" — the 3-button partial-selection payment row
  ("💰 Cash / 📱 M-Pesa / 📲 STK" shown after checking one entry) borrowed
  `.tab-action-btn`/`.qs-tab-btn` — classes designed for 2-3 buttons that EACH fill an even
  share of a FULL-WIDTH row (`flex: 1 1 calc(50% - 6px)` / `flex:1; min-width:0`). Squeezed
  into the small partial-selection row via fragile inline `flex:0` overrides with no
  `white-space:nowrap` (quick_sell.html had none at all) or overflow guard, three buttons
  fighting over width they don't have rendered as a cramped, visually overlapping blob
  instead of clean separate pills. New dedicated `.tab-partial-btn` (bar_board.html) /
  `.qs-partial-btn` (quick_sell.html) classes — sized to their own content
  (`flex:0 0 auto; white-space:nowrap`), never force-grown or force-shrunk — replace the
  borrowed classes on all 6 button instances (2 render paths × 3 buttons each); the
  disable-during-submit `querySelectorAll` calls were updated to match both class names so
  the existing "disable all tab buttons while a request is in flight" guard still covers
  them. Not independently visually verified in a live browser (no browser tool available
  in this environment) — root-caused from the actual CSS/JS, but Roy should confirm the fix
  looks right. (2) Partial payment safety — verified, not a bug: traced `settle_tab()`
  (`core/keg_views.py`) end to end — `entry_ids[]` sent by the frontend only ever contains
  checked checkboxes' own distinct `data-entry-id` (confirmed each entry gets its OWN id,
  not a shared one), and the backend's `entries_to_settle` list is filtered strictly to
  `e.id in selected_ids`; unselected entries are never touched. Already locked in by an
  existing passing test, `PartialTabSettleTest.test_partial_settle_marks_only_selected_entry_paid`.
  No code change needed — reported back to Roy as verified-safe rather than assumed. (3)
  Staff rename didn't change the login username — `edit_staff()` (`accounts/views.py`) only
  ever wrote `first_name`/`last_name`/`email`/`phone`/`role`; `User.username` (what staff
  actually type to log in, chosen once at `add_staff` time) was never editable after
  creation — renaming "Dush Master" to "Jack Musau" changed the display name everywhere but
  he still had to log in as "dush". `edit_staff.html` gains a username field (prefilled,
  clearly labelled as separate from the display name); the view validates uniqueness
  (case-insensitive, excluding self) and, on an actual change, updates `User.username` and
  tells the affected staffer via SMS + in-app notification what their new login handle is —
  SMS specifically because the whole point of the notice is they may not be able to log in
  to see an in-app one. 4 new tests (`EditStaffUsernameTest`), no migration needed
  (`User.username` already existed). 464 tests pass.
- Fix: missing Haki module toggle (2026-07-24). Roy reported Haki (staff fairness/pay
  module) had vanished from the navbar, both staff and owner side. Traced
  `Business.haki_enabled` (accounts/models.py): defaults to `True`, and no application
  code anywhere ever writes to it — the only way it becomes `False` is a direct DB
  change, and there was no owner-facing toggle to see or correct its state, unlike
  every other optional module (`Kitchen` has `toggle_kitchen`). Added the equivalent
  `toggle_haki` view + Business Settings UI section, mirroring `toggle_kitchen`'s exact
  pattern (idempotency guard via `claim_checkout_token`, owner-only). 4 new tests. No
  migration (field already existed). 468 tests pass.
- Staff journey / soft-delete (2026-07-25), planned via a dedicated research pass (2
  Explore agents mapping every FK to User/UserProfile plus every existing per-staff
  performance data source, then a Plan agent) after Roy asked: when a staff member is
  fired or renamed, the owner should still be able to see their full tenure — duration
  worked, revenue handled, salary paid, performance over time — as a report he can
  actually interpret, not a raw log. Research confirmed this was **structurally
  impossible** before this sprint: `delete_staff()` did `staff_profile.user.delete()`,
  a true hard delete; `UserProfile.user` is `OneToOneField(CASCADE)`, which cascaded
  through `Shift.staff`, `SalaryPayment.staff`, `SalaryDeduction.staff`, and
  `ItemSaleApproval.requested_by` (all CASCADE) — destroying exactly the shift-hours,
  salary-paid, and revenue-attribution data a journey report needs. Every other FK to
  User/UserProfile (`Transaction.recorded_by`, `BarTab.served_by`, `Receipt.created_by`,
  `CustomerDebtPayment.recorded_by`, `WriteOffRequest.requested_by`, etc. — a long
  SET_NULL tail) survived as rows but lost staff attribution, since none of them have a
  name-cache field (the only precedent anywhere in the codebase,
  `SalesResetLog.performed_by_username_cache`, is for an unrelated feature). There was
  also no soft-delete/`is_active` concept for staff at all, and no history of
  first/last-name or username changes (`edit_staff`'s username fix from the previous
  session silently overwrites with zero trace).

  **Design decision — soft-delete instead of retrofitting the CASCADE/SET_NULL graph.**
  Traced `_staff_contribution()` (haki_views.py) directly: it and
  `keg_metrics.staff_shrinkage()` already query off the live `User`/`Shift` objects with
  no "active roster" assumption baked in — meaning if the `User` row is simply never
  destroyed, every existing revenue/hours/salary aggregator keeps working for a departed
  staffer with zero code changes, for free. `deactivate_staff()` (renamed from
  `delete_staff`) now flips `User.is_active=False` + stamps departure metadata instead of
  deleting anything — Django's own `AuthenticationForm` already blocks `is_active=False`
  at login with no extra code, and `SingleSessionMiddleware` (accounts/middleware.py)
  gained a 4-line check so deactivation also takes effect on the very next request for an
  already-logged-in session, not just the next login attempt. New `reactivate_staff`
  (owner-only) reverses it; new `departed_staff_list` (owner/manager) is the roster for
  who's gone. `UserProfile` gains `departed_at`/`departure_reason`/`departure_note`/
  `departed_by`/`reactivated_at`/`reactivated_by` (migration 0049) — a single most-recent
  departure slot, not a full multi-cycle append-only log (a real limitation for staff who
  leave and come back more than once — noted as a future extension if boomerang re-hires
  turn out to be common, not built now). Deactivating also auto-pauses (`is_active=False`,
  never deletes) any of that staffer's active `RecurringExpense` salary rule so no new
  pay-run expectations get generated for someone who's left, while their
  `SalaryPayment`/`SalaryDeduction` history stays completely untouched. Every roster-LIST
  query across the app (`staff_list`, `edit_staff`, `staff_permissions`,
  `reset_staff_password`, `staff_contribution_report`'s loop, the `RecurringExpense`
  staff-picker in `recurring_expense_list`) now filters `user__is_active=True`, so a
  departed staffer genuinely disappears from every day-to-day management surface —
  deliberately NOT applied to `staff_duty_log`/`record_salary_payment` (single-person
  lookups by ID), which must keep working unmodified for a departed staffer, e.g. to
  record their final salary payment after they've left.

  **Rename history**: new `StaffNameChangeLog` (migration 0050) — unlike
  `SalesResetLog`/`AccountDeletionLog`'s defensive `SET_NULL` + cache-field pattern (which
  exists specifically to survive a REAL delete), this uses a plain `CASCADE` on `staff`
  since under soft-delete the User row is never actually destroyed, so that defensive
  complexity doesn't apply here. Wired into `edit_staff`: snapshots the old display name
  before the overwrite (the view already captured `old_username` from the earlier
  session's fix), creates one log row only when username or display name actually
  changed (a role/phone-only edit doesn't log).

  **New `staff_journey` report** (`core/haki_views.py`, `/staff/<id>/journey/`,
  owner-or-manager, co-located with `staff_contribution_report`/`staff_duty_log`) —
  the actual "readable story," reusing rather than rebuilding: calls
  `_staff_contribution()` over the full tenure window (earliest `Shift`/`Transaction` →
  now, or → `departed_at`) for revenue/hours/debts-recovered/milestones, pulls the
  matching `keg_metrics.staff_shrinkage()` row for keg-handling detail, lists complete
  `SalaryPayment`/`SalaryDeduction` history (small per-staff tables, no date filtering
  needed), and shows `StaffNameChangeLog` entries. Deliberately looks up `UserProfile`
  with **no** active-state filter (the one place in the app that intentionally reaches
  past the "active roster only" filter added everywhere else), so it renders identically
  for a current or departed staffer — locked in by a test asserting the exact same
  revenue/shift numbers appear before and after deactivation. Linked from `staff_list.html`
  (per active staffer), `departed_staff_list.html` (per departed staffer), and
  `haki_contribution.html` (next to the existing Duty Log link). Bar/kitchen
  station-split revenue breakdown (for staff with cross-station access) deliberately
  deferred as a nice-to-have, not built this pass. 25 new tests across both apps
  (`DeactivateStaffSoftDeleteTest`, `ReactivateStaffTest`, `DepartedStaffListTest`,
  `StaffNameChangeLogTest`, `DeactivatedStaffMiddlewareTest`, `StaffJourneyTest`). Two
  migrations (0049, 0050), both additive. 488 tests pass.
- Quick-reason chips (2026-07-25). Roy's follow-up to the wording/accountability audit:
  during a busy shift there's no time to type free text into a `prompt()`, so most
  reject/cancel/discard flows should offer 3-5 quick-tap PRESET reason chips (with a
  "Nyingine" fallback to free text) instead, and the action must never be blocked
  waiting on a reason. New `window.openReasonChips({anchorEl, title, chips, onSelect})`
  — a small anchored popover, copy-pasted verbatim into `bar_board.html`,
  `quick_sell.html`, `kitchen_board.html`, and `waitress_screen.html` (no shared JS
  bundle in this app, same convention as the existing `showModal`/`hideModal`
  duplication). Contract: tapping any chip fires `onSelect(text)` immediately — the tap
  *is* the confirm, no second submit step; a "Ruka — bila sababu" (skip) chip is always
  present and calls `onSelect('')`, letting each flow's existing backend default
  ("sababu haikuelezwa" etc.) fill in server-side exactly as before; dismissing the
  popover (tap outside) is treated as skip, never an abort. Where an existing
  `confirm()` "are you sure" step already existed (discard/void/cancel), kept it as the
  accidental-tap guard, then replaced the trailing `prompt()`/text-input with the chip
  popover. Chip wording is drawn directly from each flow's own prior placeholder/example
  text — not invented — except barrel discard, tab void, and breakage, which had only
  1 grounded example each; Roy should sanity-check those three specifically.

  Converted: DJ/MC session cancel (`_djCancelSession`); tab void — **replaced the
  Bootstrap modal entirely** on both `bar_board.html` (`_doVoid`/`openVoidModal`) and
  `quick_sell.html` (`qsDoTabVoid`/`qsOpenTabVoid`) with `confirm()` + chips, since a
  modal-then-type flow is exactly the multi-tap friction this feature removes; table
  order cancel — unified the two previously-inconsistent example sets from
  `waitress_screen.html`'s `cancelOrder` and `bar_board.html`'s `oqUpdate` into one
  shared 4-chip set; kitchen batch discard (`kbDiscardBatch`) — also fixed a real "never
  block" bug where dismissing the old `prompt()` (`reason === null`) aborted the whole
  discard action instead of proceeding with no reason; barrel discard
  (`openDiscardModal`) — same dismiss-aborts bug fixed, plus the pre-filled prompt
  default became the first chip; bunch discard (`discardBunch`); bottle/stock breakage
  — the whole `breakageModalBackdrop` modal was still **entirely English**
  ("Record Breakage", "Quantity", "Cancel" etc., missed by the earlier wording audit
  since that pass only touched the JS-level messages, not this modal's static labels)
  — translated throughout and the note field became a chip-trigger button; kitchen
  wastage (`kitchen_wastage()`, core/kitchen_views.py) — same English-modal gap plus a
  bare `{"ok": True}` response with an English `"Food wastage"` default and every error
  string in English (`"Item not found"` etc.) — fixed to mirror `record_breakage()`
  exactly: Swahili throughout, a reasoning message (item, qty, KES loss, reporter,
  timestamp), and an owner/manager notification, closing the same gap class found and
  fixed for the bar module's breakage flow in an earlier sprint but never carried over
  to kitchen's sibling endpoint. 9 new tests
  (`KitchenWastageExplainsItselfTest`). No migrations — every flow's `reason`/`note`
  field already existed; this sprint is a frontend UX change plus the two backend fixes
  above. 491 tests pass.
- Quick-reason chips, remaining flows (2026-07-25): stock variance dismiss + petty cash
  reject. `StockVarianceQuery.owner_note` (migration 0117, additive) — `review_variance()`
  (`core/stock_take_views.py`) already read `owner_response_note` from POST on the accept
  branch for the corrective-transaction `recipient` text, but never persisted it as its
  own field for later display; dismiss never captured a note at all. Both branches now
  save it to `owner_note` and echo it back in the JSON `message`; `dismiss` specifically
  gets a 2-chip set (`'Hesabu ya awali ilikuwa sahihi'`, `'Maelezo hayakubaliki'`) via
  `openReasonChips` in `stock_variances_pending.html`, wired through a new
  `_submitReviewVariance(varId, action, note)` helper — `accept` keeps its existing
  owner-response-type flow untouched, only `dismiss` was blocking on free text before.
  `stock_variances_pending.html` gained its own copy of the `openReasonChips` component
  (this app's established per-template copy-paste convention, no shared JS bundle) and a
  `{% if v.owner_note %}` display line in the resolved-variances list. Petty cash reject
  (`petty_cash_list.html`) — same pattern: `reviewEntry()` now branches on `action`;
  `'approve'` keeps the existing shared `pcReviewModal` free-text flow unchanged,
  `'reject'` bypasses the modal entirely and opens `openReasonChips` with
  (`'Hakuna risiti'`, `'Kiasi hakiendani na madai'`), submitting via a new
  `_submitPettyCashReview()` helper straight to the same `/petty-cash/<id>/review/`
  endpoint — no backend change needed there, since it already accepted `review_note` from
  any POST regardless of which UI produced it. Both templates' reject/dismiss buttons now
  pass `this` as the popover's anchor element. 6 new tests
  (`StockVarianceReviewWordingTest` +3, `PettyCashReviewMessageTest` +2 — including a
  skip-leaves-note-blank case for each, matching this feature's never-block contract). No
  new migrations beyond 0117. This completes Feature 1's originally-scoped flow list —
  every reject/cancel/discard/dismiss surface identified now offers reason chips over a
  blocking free-text prompt. 496 tests pass. Next (deferred, not started this session):
  Slice 4 — backfill affordances for existing blank-reason records + reason-breakdown
  additions to `voided_tabs_list`, `daily_summary.html`, Expense Intelligence,
  `performer_list.html`, `bar_shrinkage_report`.
- Fix: petty cash review had no undo (2026-07-25), live report: Roy tapped "Kataa" on a
  petty cash entry by mistake and had no way to reverse it. Root cause was UI-only —
  `review_petty_cash()` (`core/petty_cash_views.py`) never actually blocked re-reviewing an
  already-reviewed entry, but `petty_cash_list.html` only ever rendered the Kubali/Kataa
  buttons `{% if entry.status == 'pending' %}` and JS's `_pcApplyResult` permanently
  `.remove()`d the actions div after the first review — the undo path existed server-side
  and was simply never exposed. Fix is UI-only plus a wording upgrade, no schema change:
  `.pc-actions` now always renders (hidden via inline `style="display:none"` when not
  pending) instead of being conditionally rendered/removed; a "↺ Badilisha uamuzi"
  (reconsider) toggle appears on any reviewed entry — owner-only, since the whole page
  already is — revealing the same Kubali/Kataa buttons to correct a decision, any number
  of times, not just once. `review_petty_cash()` now detects when a re-review actually
  flips the decision (`is_reversal`) vs. just edits the note on an unchanged decision, and
  the flipped case gets an explicit "MAREKEBISHO: ... ilikataliwa na X tarehe Y — sasa
  imekubaliwa na Z tarehe W" message (naming both the original AND the corrected decision,
  per this app's wording/accountability standard) instead of reading like a fresh,
  unrelated approval; the recorder's notification title changes to "↺ Uamuzi Umebadilishwa"
  for a reversal so they don't mistake it for a second independent review. Confirmed via a
  live-query trace (not assumed) that no separate reconciliation step is needed once the
  status flips back: `bar_z_report`'s shift reconciliation (`core/keg_views.py`) is the only
  other consumer of `PettyCash.status` anywhere in the app, reads `status='approved'` live
  on every render with nothing cached/frozen, and `shift_views.py`'s shift-close/reconcile
  path doesn't reference `PettyCash` at all — so correcting the status is the entire fix,
  which a new end-to-end test locks in by walking approve → mistaken-reject → re-approve
  and asserting the Z-report's petty-cash deduction for that shift disappears and reappears
  live at each step with no other action taken. 5 new tests (`PettyCashReviewUndoTest`). No
  migrations. 501 tests pass.
- Kitchen batch cost correction + owner navbar fix (2026-07-25), two live reports in one
  message. (1) **Fix: owner/manager saw no 🍗 Kitchen link in their own navbar after
  enabling `has_kitchen`.** Root cause: `base.html`'s owner/manager catch-all navbar block
  (the `{% else %}` reached only by `is_owner`/`is_manager`, both mobile and desktop
  duplicates) gated the Kitchen link on `biz_profile.modules.kitchen AND
  user.userprofile.can_access_kitchen` — but `can_access_kitchen` is a staff-only
  cross-access flag (`default=False`, "Bar/general staff may access the Kitchen Board")
  that nothing ever sets for an owner or manager. `kitchen_board()` itself already bypasses
  this exact check for `is_owner_or_manager` (`core/kitchen_views.py`), so the navbar and
  the view had silently drifted apart — Roy could always reach the board via Business
  Settings' "Nenda Kitchen →" button (gated only on `has_kitchen`), which is why the view
  itself was never in question. Fixed the 2 owner/manager occurrences (of 4 total —
  the other 2, inside the regular-staff `is_staff_member` block, correctly keep the
  `can_access_kitchen` requirement) to `{% if biz_profile.modules.kitchen %}`, matching the
  view's own bypass exactly. (2) **Kitchen Batch cost correction** — `KitchenBatch` had no
  edit path for `cost_total` once opened; only ✓ Imekwisha (deplete) and 🗑 Tupa (discard)
  existed, so a mistyped raw-material cost at receive time (e.g. Roy meant 1500, typed 800)
  had no fix short of discarding the whole batch. New `edit_kitchen_batch_target`
  (`core/kitchen_views.py`, `/kitchen/batch/<id>/edit-target/`) — deliberately
  owner/manager-only, stricter than `_kb_gate`'s any-open-shift-staff gate used by
  receive/deplete/discard, since `cost_total` drives `profit()`/`profit_pct`,
  `discard()`'s wastage math, AND mirrors into `item.cost_price` (see `open_batch()`'s
  docstring) — the same sensitivity tier as every other financial-figure correction in
  this app (`adjust_stock_balance`, petty cash review, stock variance review, all
  owner/manager-only). Locks the batch row with `select_for_update()`, rejects non-positive/
  non-numeric input, re-mirrors the corrected figure into `item.cost_price` so
  `discard()` and `Transaction.cost()`'s proportional-share formula never price against a
  stale figure, and appends a system-generated audit line to `batch.note` ("Gharama
  ilibadilishwa kutoka KES X kwenda KES Y na {who} — {when}") — only restricted to `OPEN`
  batches, matching deplete/discard's own status guards. Kitchen board tile gains a
  "✏️ Hariri Gharama" button next to the existing two actions, owner/manager-only
  (`IS_OWNER` JS flag, already used for other owner-gated buttons on this board); uses a
  plain `prompt()` — deliberately simpler than the reason-chips components elsewhere in
  this file, since this is a single number entered rarely and intentionally by the owner,
  not a reason captured under mid-service time pressure. 9 new tests
  (`EditKitchenBatchTargetTest`, `KitchenNavbarOwnerVisibilityTest`). No migrations. 510
  tests pass.
- Fix: tab rename created a duplicate instead of reconciling (2026-07-25), live report.
  Real scenario: staff opens a tab for "Roy"; later, during a busy moment, opens a SECOND
  order for him without typing a name — the anonymous-tab path (2026-07-19) deliberately
  always creates a brand-new tab in that case rather than guessing a name match, so this
  correctly produces a second tab named "Tab #47". When she later corrects that name to
  "Roy" via the tabs drawer's rename field, `update_tab_name()` (`core/keg_views.py`) used
  to just blindly overwrite `customer_name` with zero check for a collision — leaving TWO
  open "Roy" tabs side by side that never reconciled, exactly what Roy saw. Fixed:
  `update_tab_name()` now searches for another OPEN tab with the same name (case-
  insensitive, scoped to `_allowed_tab_sources(up)` — the same station-visibility scope
  already used to fetch the tab being renamed, so a bar-only staffer's rename can never
  silently pull in kitchen-only revenue they aren't allowed to see) and, if found, calls
  new `_merge_tab_into(source_tab, target_tab)` instead of renaming in place. The merge is
  a plain `BarTabEntry.tab_id` reassignment for every entry (same mechanism
  `split_and_transfer_locked()` already uses) — no new `Transaction`, no envelope
  `revenue_collected` touched, so total revenue and stock balances are provably unaffected
  (locked in by a test summing entry amounts before/after). The now-empty source tab closes
  as `VOID` with an explanatory `void_reason` ("Imeunganishwa na tab ya X (#N) — majina
  yalifanana") — deliberately reusing the existing VOID status rather than a new one (no
  schema change, and every existing `status='VOID'` reader already treats an empty-entries
  tab as inert) while making clear in the reason text this was a reconciliation, not a real
  cancellation, matching this app's wording/accountability standard. Any `PENDING`
  `TabTransferRequest` referencing the tab being merged away — as EITHER `source_tab` or
  `dest_tab` (the existing inverse-action safeguard, `_cancel_pending_transfers_for_tab`,
  only ever covered `source_tab`, since void/convert-to-debt can't be a transfer
  destination in practice; a merge target search can) — is auto-cancelled so no pending
  split-bill request is left pointing at an entry that has silently moved tabs. An
  unresolved "customer wants to pay cash" flag (`cash_requested_at`) carries across to the
  target tab if it doesn't already have one — no money moved either way, so nothing to
  reconcile there beyond keeping the flag visible. Tabs-drawer parity rule: all three
  rename handlers (`saveTabName` bar_board.html, `qsSaveTabName` quick_sell.html,
  `saveKbTabName` kitchen_board.html — all three POST the same shared
  `/bar/tabs/<id>/rename/` endpoint) now show the merge confirmation message
  (`d.message`) instead of the generic "✓ Jina limebadilishwa" when `d.merged` is true, so
  staff isn't left wondering why an entry they just renamed vanished from the drawer under
  its own card. 7 new tests (`TabRenameMergeTest`) — merge-vs-plain-rename, revenue
  preservation, both transfer-cancellation directions, `cash_requested_at` carry-over, and
  station-scoping. No migrations. 517 tests pass.
- Full-item + whole-tab transfer, cross-counter (2026-07-25), live request: the existing
  split-bill transfer (2026-07-23) only let a destination customer cover PART of one item
  (source customer pays some, remainder transfers) and could only pick a destination tab
  native to the current drawer. Roy's exact scenario — Bosco offers to cover a whole item,
  or Roy's ENTIRE tab, and Bosco's keg tab is on Bar Board while Roy's is on Quick Sell —
  needed three things none of which existed: (1) a full-item transfer (destination pays
  the WHOLE thing, source pays nothing), (2) a whole-TAB transfer (every unpaid entry at
  once, as one accept/reject decision), (3) a destination picker that reaches every open
  tab across all three counters, not just the current drawer's own list.
  **Full-item**: `BarTabEntry.split_and_transfer_locked()`'s validation relaxed from
  `paid_amount <= 0` to `paid_amount < 0` (0 now allowed) — when 0, a new short-circuit
  path skips the split entirely (no new Transaction, no reduced original) and points the
  `TabTransferRequest` straight at the existing, unmodified entry; `paid_method` becomes
  irrelevant and optional in this branch since no real payment happens at split time.
  Same view (`split_and_transfer_entry`), same URL — the caller just sends
  `paid_amount=0`. **Whole-tab**: new `TabTransferRequest.propose_whole_tab_locked()`
  creates one full-item `TabTransferRequest` PER currently-unpaid entry on the source
  tab, all sharing a new `batch_id` field (migration 0118) — snapshot semantics, a sale
  added to the source tab AFTER proposing is never silently swept in. New view
  `transfer_whole_tab` (`/bar/tabs/<id>/transfer-whole/`), same permission tier as
  split-transfer (any staff with an open shift). `TabTransferRequest.accept()`/`reject()`
  now cascade to every PENDING sibling sharing `batch_id` inside one atomic block
  (all-or-nothing) — meaning **zero new accept/reject URL routes were needed**: every
  existing respond endpoint (`respond_tab_transfer`, `receipt_respond_tab_transfer`,
  `tab_respond_tab_transfer`) already resolves a whole batch from any single row's id.
  **Real bug found while adding the cascade** (pre-existing, unrelated to batching):
  `accept()`/`reject()` mutated only a separately re-fetched `fresh` row, never `self` —
  every one of those three respond endpoints calls bare `transfer.accept()` with no
  captured return value, then reads `transfer.status` immediately after, which was
  therefore always still the stale `'PENDING'` it started with. Fixed by syncing `self`'s
  `status`/`resolved_at` from the resolved row before returning — every existing call
  site now reads correctly with no call-site changes. **Cross-counter picker**: new
  read-only `transferable_tabs_api` (`/bar/tabs/transferable/`) returns every OPEN tab
  the requesting staffer can see across bar/kitchen/qs (via `_allowed_tab_sources`),
  deliberately broader than each drawer's own `tabs_list()`/`kitchen_tabs_list()` (which
  stay scoped to what that counter opened — unchanged); all three drawers' split-transfer
  AND new whole-tab-transfer modals now populate their destination dropdown from this
  endpoint instead of a cached local tab list, closing the exact gap the original
  split-transfer plan flagged as a "v1 limit." Shared `_resolve_transfer_dest_tab()`
  helper factored out of `split_and_transfer_entry` (unchanged behavior, just
  de-duplicated) and reused by `transfer_whole_tab`. **Display**: `_pending_transfers_
  for_tabs()` (customer-facing, `core/receipt_views.py`) and the equivalent server-side
  grouping in `tabs_list()` (staff-facing, `core/keg_views.py`) both group rows by
  `batch_id` into ONE bundled dict (`is_whole_tab`, aggregate `amount`, `transfer_ids`)
  instead of rendering N separate confusing cards for one proposal — any single id in the
  group resolves the whole thing. `receipt_public.html`, `tab_live.html` (customer wall-QR
  pages), and all three tabs drawers render "Roy anataka kuhamisha tab yake yote (vitu N,
  jumla KES X)" for a whole-tab proposal instead of the per-item "anataka kuongeza X
  kwenye bili yako" wording. `_notify_tab_transfer_resolved` sums across the whole batch
  for its staff notification instead of reporting only the first row's amount. Split
  modal (all 3 drawers) gains a "Mteja mwingine analipa YOTE" checkbox that hides the
  paid-amount/method inputs and submits `paid_amount=0`. New "🔀 Tab Yote" button added
  next to Deni/Futa on every tab card with an unpaid balance, across all three drawers —
  intentionally NOT added to bar_board's separate mixed (food+bar) tab-card branch,
  matching that branch's existing precedent of excluding Deni/Futa too (kitchen items
  settle at Kitchen Board, not here). 28 new tests
  (`FullItemAndWholeTabTransferTest`) — full-item split/accept/reject, whole-tab
  propose/accept-cascade/reject-cascade, revenue preservation, empty-tab and same-tab and
  already-pending-entry rejection, the `accept()`/`reject()` self-sync regression lock,
  notification aggregation, display grouping, cross-counter view-level and station-scoping
  coverage, and one end-to-end HTTP test proving `respond_tab_transfer` resolves a whole
  batch from any single row's id. No regressions in the 48 pre-existing split-transfer/
  tab-rename/station-scoping tests. 545 tests pass.
- Wall-QR duplicate-receipt search fix + partial-amount tab payment (2026-07-25), two live
  reports. **(1) Duplicate search results**: Roy has orders on both Bar Board and Bar
  Orders/Quick Sell under the same name — scanning the wall QR and searching "Roy" showed
  TWO result rows, confusing, even though cross-counter receipt-linking
  (`resolve_master_receipt`, confirmed already wired into all three checkout views) already
  consolidates both tabs into ONE shared receipt. Root cause: `find_tab_search`'s name-
  search loop (`core/keg_views.py`) added one result PER matching `BarTab` row with no
  de-duplication, so two already-linked tabs still produced two identical-destination rows.
  Fixed by deduping on the resolved URL — two tabs that land on the same receipt now show as
  ONE result; two genuinely separate customers still show as two (regression-locked by a
  dedicated sanity-check test). **(2) Partial-amount tab payment — theft-prevention**: a
  customer paying 70 of an 80 KES tab via M-Pesa had no correct way to be recorded — staff
  could only settle selected entries in FULL or not at all, so the shortfall either got
  silently marked as fully paid (losing KES 10 with no trace) or the payment had to be
  refused outright. Traced further while fixing this and found the STK Push callback
  (`_settle_tab_from_payment`, `core/mpesa_views.py`) had an even more severe version of
  the SAME gap in its full-tab-FIFO branch: it `continue`d past any entry costing more than
  what was left of `payment.amount`, silently DROPPING that leftover money with zero
  record anywhere — a real, Safaricom-confirmed charge could vanish from the tab's
  tracking entirely if it didn't land exactly on an entry boundary, the same class of bug
  this app's own Known Issues already flags as critical for STK callbacks. Fixed both
  through one shared mechanism: extracted `BarTabEntry.split_paid_unpaid_locked()` (the
  split step `split_and_transfer_locked()` already used, now reusable) and built
  `BarTab.settle_entries_amount_locked(tab_id, business, entry_ids, amount, payment_method,
  recorded_by)` on top of it — walks selected entries in ascending-id order, fully pays as
  many as fit, and splits the boundary entry (paid portion settled now, remainder stays as
  an ORDINARY unpaid entry on the same tab — never silently written off, never auto-
  converted to debt) when `amount` doesn't land exactly on an entry boundary. `settle_tab()`
  gained an optional `amount` POST param routing through this when less than the selected
  total (`_finish_settle_tab()` extracted as a shared tail so both paths issue an identical
  receipt/notification); `_settle_tab_from_payment` now routes its full-tab-FIFO branch
  through the same method instead of its own broken skip-based loop. "Part M-Pesa, part
  cash" is just two calls: first settles 50 via mpesa (splits the entry, 30 left owing),
  second settles the remaining 30 via cash — no debt ever created, both payment methods
  correctly recorded on separate line items. Frontend: all three tabs drawers gained an
  editable "Kiasi" amount field in their partial-settle UI (bar_board/quick_sell: inline
  next to the selection total; kitchen_board: in the existing settle modal), defaulting to
  the full selected total and only sent when edited down — untouched behaves exactly as
  before. Quick Sell's STK modal already had an editable amount field wired to the STK
  push amount; the backend fix alone makes that already-existing input correctly handle a
  partial STK amount, no frontend change needed there. 12 new tests
  (`PartialAmountSettleTest`, `SettleTabFromPaymentPartialAmountTest`,
  `FindTabSearchDedupTest`) including the critical regression lock proving a confirmed
  STK charge is never silently dropped. No migrations. 557 tests pass.
- Revoke mistaken tab payments + kitchen "Futa" widened to staff (2026-07-25), live
  request: a customer's mpesa/cash tap can be mis-tapped, or a settlement can be applied
  to the wrong customer's tab entirely, with no way to undo it; separately, kitchen staff
  had no way at all to erase a tab entry placed by mistake (`✕ Futa` was owner/manager-
  only, unlike bar/Quick Sell where the same gate already blocked ordinary staff too).
  **Revoke**: new `BarTabEntry.revoke_payment_locked()` (`core/models.py`) — locked,
  reverts a paid entry back to `is_paid=False`/`payment_method=''`/`paid_at=None`,
  clears the mirrored `Transaction.payment_method`, and reopens the tab
  (`status='OPEN'`) if the entry being revoked was the one that had closed it as
  SETTLED — the live receipt/wall-QR page (`_get_live_tab_state`) picks this up for
  free with zero receipt-side changes, since it already reads tab status live. New
  `TabPaymentRevocation` audit model (migration 0119) records what changed, who did it,
  why, and whether the entry had a real Safaricom-confirmed `Payment` behind it
  (`was_stk_confirmed`) — surfaced back to staff in the response as an explicit warning,
  since reversing a genuinely-paid STK entry needs the money physically returned outside
  the app, unlike reversing a mis-tapped cash/mpesa label. Deliberately does NOT touch
  `Transaction.qty` (stock) or create/delete any Transaction — verified by direct trace
  and a dedicated test that a payment-only correction leaves stock balance and revenue
  totals completely unaffected at every step, satisfying Roy's explicit "stock counts,
  payment data and overall transactional metrics respond accordingly" requirement (the
  correct response for stock/revenue here is "no change," since the sale itself was
  real — only which payment record it's tagged with was wrong). **The "settled to the
  wrong customer" case** (the tab can fully close and vanish from the open-tabs view)
  is handled by a new read-only `recent_settled_tabs_api` — tabs `SETTLED` in the last 6
  hours, station-scoped via `_allowed_tab_sources` — powering a "🕐 Malipo ya Hivi
  Karibuni" collapsible panel added to all three tabs-drawer offcanvas headers, so staff
  can find and revoke a settlement even after its tab has disappeared from the normal
  view. **Permission model**: both `revoke_entry_payment` and the widened
  `remove_tab_entry` use the same shift-gated + station-scoped pattern already
  established by `settle_tab`/`tick_entry`/`split_and_transfer_entry` — ANY staff with
  an open shift, not owner/manager-only (confirmed against this app's own precedent:
  correcting an in-progress mistake needs to work mid-shift without the owner present,
  same reasoning already applied to split-transfer). New shared `_entry_station(entry)`
  helper (station discriminator, `item.store.is_kitchen`) and `_notify_tab_correction()`
  fan-out helper (in-app + SMS to owner/manager + on-shift staff, excluding the actor)
  mirror this file's established notification-recipient pattern
  (`_fire_cash_payment_request`, etc.) — both reject and revoke actions now explain
  themselves to everyone else who needs to know, per the wording/accountability
  standard. **Kitchen Futa gap**: `kitchen_board.html`'s `removeBtn` was gated
  `IS_OWNER && !e.is_paid`; widened to `!e.is_paid` to match the same shift+station
  backend gate — this was the literal reported bug, a UI-only gap (the backend endpoint
  was never owner-restricted in the first place for kitchen's own remove path, only the
  button was hidden). Reason capture for both remove and revoke uses the existing
  `openReasonChips` popover (never blocks on a skipped reason) in all three drawers per
  the tabs-drawer-parity rule. **Test-suite fragility found and fixed while running the
  full suite before push** (unrelated to this feature, pre-existing bugs from earlier
  sessions, surfaced only because real wall-clock time crossed midnight mid-session):
  (1) `core.haki_views.staff_journey` computed `tenure_end =
  staff_profile.departed_at.date()` — calling `.date()` directly on a timezone-AWARE
  datetime returns the date in whatever timezone the datetime is internally stored in
  (UTC, under `USE_TZ=True`), not the project's local timezone; for a departure logged
  between 00:00-03:00 Nairobi time (UTC+3) this silently returns YESTERDAY's date,
  excluding that final day's revenue/shifts from the whole journey report — a real bug
  for a bar-hours business where staff can plausibly be deactivated in the small hours.
  Fixed to `timezone.localtime(staff_profile.departed_at).date()`, matching the
  project's own convention everywhere else (`timezone.localdate()` for "today").
  Regression-swept: this was the only `.date()`-on-an-aware-field call site in the app.
  (2) `PettyCashReviewUndoTest.test_zreport_reconciliation_self_corrects_after_reversal`
  hardcoded `started_at=timezone.now() - timedelta(hours=2)` to mean "earlier today" —
  false in the first 2 hours after local midnight, when that lands on yesterday and
  falls outside `bar_z_report`'s (correctly timezone-aware) "today" window; this is a
  test-authoring bug, not an application bug — fixed by anchoring to
  `timezone.localtime().replace(hour=0, minute=1, ...)` (start of today), which is
  always within-today regardless of when the suite happens to run. 27 new tests
  (`RevokePaymentAndRemoveEntryTest`). One migration (0119, additive). 584 tests pass.
- Accountability overhaul — six increments in one session (2026-07-25), run
  autonomously overnight per Roy's request before he went to sleep, each independently
  tested and ready to commit. **(1) Shift visibility scoping**: `shift_history()`
  (`core/shift_views.py`) already station-scoped (bar vs kitchen) but had no identity
  scoping at all — any staff member saw every OTHER staff member's shifts within their
  station. Added `staff=request.user` filter for non-owner/manager, matching the
  `is_owner_or_manager` convention used everywhere else in this app; also fixed the
  template's `is_owner` context var, which was strictly `up.is_owner` (excluding
  managers) even though the view's own station-scoping logic already correctly used
  `is_owner_or_manager` two lines above it — managers could see all shifts but the
  template variable powering the Confirm button (and now the variance-review buttons,
  see below) didn't reflect that. **(2) Universal + per-customer credit limit**:
  `Customer.credit_limit` (per-customer, nullable) already existed; added
  `Business.default_credit_limit` (accounts migration 0051) as a business-wide
  fallback. `evaluate_credit()`'s limit check now resolves `customer.credit_limit if
  not None else business.default_credit_limit` — a personal limit always overrides
  the default, blank on both means no KES cap (other policy gates still apply). New
  field exposed in Payment Settings' "Sera ya Deni" section (`_section=credit_policy`
  handler in `accounts/views.py`) and as a placeholder/hint on the per-customer field
  in `customer_debt_profile.html` when no personal limit is set. **(3) Cash variance
  at shift close**: Roy's own diagnosis was exactly right — a shift-close cash gap has
  only two legitimate causes, unlogged petty cash or something needing a real
  explanation (most often: owner tells staff by phone to send the drawer's cash to
  M-Pesa). Root cause of cause #1: `_reconcile()`'s `expected_cash` never subtracted
  approved petty cash — only the LATER-built Z-report did its own separate
  subtraction — so the FIRST number staff ever saw at close (and the >KES 500 owner
  alert, which fires from `close_shift()` itself) was already wrong, a false shortfall
  that petty cash fully explains. Folded petty cash into `_reconcile()` itself (now
  returns `petty_cash` too) so every consumer — `close_shift`, `active_shift_api`
  (staff's own live shift panel), `shift_history`, `bar_z_report` — shares one correct
  number; removed the Z-report's now-duplicate calculation. For cause #2, new
  `Shift.variance_note`/`variance_mpesa_ref` (staff's explanation, captured via reason
  chips right after they see the number, M-Pesa ref prompted only when relevant) and
  `Shift.variance_review_status`/`variance_review_note`/`variance_reviewed_by/_at`
  (owner/manager's acknowledge-or-flag decision, re-reviewable with a MAREKEBISHO
  correction message on reversal — same pattern as `review_petty_cash`'s undo). New
  endpoints `add_shift_variance_note`/`review_shift_variance` (migration 0120),
  wired into both `bar_board.html` and `kitchen_board.html`'s close-shift result
  panel (tabs-drawer-parity rule applies here too — both boards have their own
  near-identical close-shift modal) and a review UI in `shift_history.html`. Drive-by
  fix: the >KES 500 cash-variance alert and the keg overnight-loss alert both
  notified `role='owner'` only, excluding managers — widened to
  `role__in=['owner','manager']` to match this app's own convention, since managers
  are exactly who Roy described handling this in practice. **(4) Stock variance
  shift-boundary attribution** — the core of "two people have questions to answer":
  `start_stock_take()` used to set `queried_staff` to whichever shift happened to be
  linked to the stock take, with no check for whether that shift had actually done
  anything to the item in question. New `shift_views.attribute_variance_shift(
  business, current_shift, item=None, keg_barrel=None)` — the fairness test: has the
  current shift recorded ANY transaction on this specific item/barrel since it
  started? If yes, attribute to them (plausibly theirs, or at least happened on their
  watch). If zero activity, walk back to the most recent PRIOR shift and attribute
  there instead (`None` if no prior shift exists — "kabla ya zamu yoyote
  iliyorekodiwa", never guessed). Applied PER ITEM (not per stock-take), since one
  stock take can correctly split blame across two different people in the same
  submission — new `StockVarianceQuery.attributed_shift` FK (migration 0121) records
  which shift is believed responsible, separate from `queried_staff` (who's actually
  asked). Notifications now correctly route: the attributed (queried) staff gets
  asked to explain with wording that says "zamu yako ILIYOPITA" when redirected
  (never "leo"/today when it wasn't), AND the current on-duty staff gets a separate
  informational "si zamu yako, hakuna hatua inayohitajika" notice for any item
  redirected away from them — so both people in the story hear from the app, not just
  the one being asked. `stock_variance_respond.html` and `stock_variances_pending.html`
  both surface the attribution reasoning inline. **(5) Keg SPOT weigh-in attribution
  fix** — the identical bug, worse in practice: `weigh_barrel()`'s SPOT alert always
  named `request.user` — whoever pressed "weigh" — even when their shift had sold
  nothing yet from that barrel, meaning the loss necessarily predates them (the
  scenario Roy described almost verbatim: open shift, weigh immediately, previous
  night's tally doesn't match, staff hasn't sold anything). Reused the same
  `attribute_variance_shift()` helper (`keg_barrel=barrel`); when redirected, the
  alert names the attributed shift's staff with an explicit "(zamu ya {when} — KABLA
  ya zamu ya sasa)" suffix instead of the current weigher, and the weigher gets the
  same "si zamu yako" clearing notice as the stock-take case (only when they ARE the
  current shift's own staffer — an owner/manager spot-checking someone else's barrel
  has no "their shift" to be cleared on). Note: `confirm_barrel_weights()` (the
  SHIFT_OPEN vs last SHIFT_CLOSE overnight-loss check, Sprint 4/F2) was ALREADY
  correctly non-blaming — it compares against the prior shift's own closing reading
  and alerts the owner about an "overnight" loss, never naming the incoming staffer;
  only the separate mid-shift SPOT-check path had the bug. **(6) Clickable
  notifications** — new `Notification.link_url` (migration 0122, blank-by-default,
  fully backward compatible — every existing call site keeps working unchanged) plus
  a `create_in_app_notification(..., link_url="")` param on the shared helper.
  `notifications.html` renders a notification as an `<a>` wrapping the whole card
  when `link_url` is set (with a "Angalia →" affordance), a plain `<div>` otherwise.
  Swept essentially every `Notification.objects.create()`/`create_in_app_notification()`
  call site in the codebase (~45 across `core/notifications.py` — the busiest single
  file, covering transaction/low-stock/reorder/marketplace-order/procurement-bid/
  rider notifications — plus `keg_views.py`, `shift_views.py`, `debt_views.py`,
  `stock_take_views.py`, `mpesa_views.py`, `receipt_views.py`, `restricted_items_
  views.py`, `restock_views.py`, `petty_cash_views.py`, `order_views.py`,
  `credit_policy.py`, `kitchen_views.py`, `views.py`, `haki_views.py`,
  `performer_views.py`, `procurement_views.py`, `customer_ussd.py`,
  `whatsapp_bot.py`) and gave each a real destination — debt/write-off notifications
  go to the customer's debt profile or the write-off queue, stock/keg variance
  alerts go to the variance page or barrel reconciliation, restock/petty-cash/
  approval notifications go to their respective action queues, procurement/bid
  notifications go to the specific procurement request, etc. Two deliberately left
  unlinked (a username-change notice and a staff login/logout ping) where no
  destination page makes sense. `_fire_keg_alert()` and `_fire_owner_alert_msg()`
  gained an optional `barrel_id`/`link_url` param threaded through their existing
  callers (`weigh_barrel`, `close_shift`, `tap_barrel`) rather than duplicating the
  notify logic. Cause-and-Effect notes: none of the six increments needed new
  migrations beyond the additive ones listed (0051 accounts; 0120, 0121, 0122 core);
  all six were verified with targeted test runs plus one full `core + accounts`
  suite pass before commit, per this file's own end-of-sprint ritual. Also queued for
  the same session: a fresh systemic audit of all bar processes (Roy's mid-session
  ask, similar in spirit to the 2026-07-19 bar/keg systemic audit but covering
  everything shipped since).
- Follow-up audit pass (2026-07-25, same overnight session) — scoped to the newest/
  most complex bar-money code (the six increments above plus the 2026-07-23–25 split/
  whole-tab transfer feature) using the same money-path-idempotency lens as the
  2026-07-19 systemic audit, rather than re-walking the whole module from scratch.
  Found and fixed three real gaps, each with dedicated regression tests. **(1)
  `weigh_barrel()`** had none of this app's standard double-submit protection — a
  retry would create a second near-identical `KegWeightReading` (perturbing the
  shift-bracketed variance math `staff_shrinkage()` relies on) and could double-fire
  the danger alert/SMS for one physical weigh-in. Added the same `claim_checkout_token`
  backstop already used by `receive_barrel`/`record_breakage`/`add_cups`, plus a
  client-side token in `bar_board.html`'s `submitWeigh()`. **(2) `start_stock_take()`**
  (the full accountability-lifecycle stock take — distinct from `stock_take_api`'s
  `ShiftStockCount`, which is already self-idempotent via `update_or_create`'s
  `unique_together`) had the identical gap: a retry would create a second `StockTake`
  header with duplicate `StockVarianceQuery` rows and double-notify both the queried
  staff and the owner for one physical count. Same guard added, plus a token in
  `stock_take_form.html`. **(3) The full-item transfer path** (`BarTabEntry.
  split_and_transfer_locked()`'s `paid_amount=0` branch) was the most interesting
  find: the sibling `paid_amount>0` branch is self-protecting against a retry — it
  flips the original entry's `is_paid=True`, so a second call immediately hits the
  "already paid" guard — but the zero-paid-amount branch deliberately leaves the
  entry completely unmodified until accept(), so a retry sailed through every check
  again and created a SECOND pending `TabTransferRequest` on the same entry (duplicate
  SMS to the destination customer, a confusing doubled pending banner). Its sibling
  method, `TabTransferRequest.propose_whole_tab_locked()`, already had the correct
  guard (`if TabTransferRequest.objects.filter(entry_id__in=entry_ids,
  status='PENDING').exists(): raise ValueError(...)`) — added the same explicit
  model-layer check to `split_and_transfer_locked()`'s zero-amount branch (defense-in-
  depth, not just relying on the token), plus `claim_checkout_token` guards on both
  `split_and_transfer_entry` and `transfer_whole_tab` views (the latter was already
  self-protected by its model-layer check — added anyway for consistency with every
  other checkout-shaped endpoint in this app) and matching idempotency tokens in all
  three tabs drawers' `_doSplitTransfer`/`_doTransferWholeTab` JS functions
  (tabs-drawer-parity rule). Verified against the full pre-existing
  `FullItemAndWholeTabTransferTest` suite (31 tests, all still pass) to confirm none
  of this broke the already-shipped, heavily-tested transfer feature. 8 new tests
  (`WeighBarrelIdempotencyTest`, `StartStockTakeIdempotencyTest`,
  `TransferIdempotencyTest`). No migrations. Everything else audited in this pass —
  `revoke_entry_payment`/`remove_tab_entry` (already guarded, prior session),
  `add_shift_variance_note`/`review_shift_variance` (no duplicate-creation risk —
  single-field overwrites, not append-only) — checked out already correct.
- Live report fixes, Monsoon Inn (2026-07-25): five distinct issues from a single
  morning's real usage. **(1) Stock-take-accept revenue was inflating "today's" live
  dashboard before any real sale.** Root cause: `review_variance()`'s accept action
  creates a corrective `Issue` Transaction dated `svq.stock_take.taken_at.date()` —
  correct for record-keeping (the discrepancy WAS discovered today), but that same
  `date=today` + `payment_method='cash'/'mpesa'` meant it flowed straight into every
  "today so far" live-tracking surface: `home()`'s `bar_today_revenue`/
  `kitchen_today_revenue`, the `dashboard_revenue_api` AJAX poll that refreshes those
  same tiles every few seconds, the daily revenue-target progress bar, AND (if a
  shift happened to be open at accept time) `_reconcile()`'s shift cash/mpesa totals
  — the last one meaning a stock-take acceptance mid-shift could make an accurate
  physical cash count look like a false shortage. Fixed by tagging the corrective
  Issue transaction `invoice_no='[SVQ]'` (same convention `adjust_stock_balance`
  already uses for `[ADJ]`) and excluding it from all four live-tracking queries —
  deliberately NOT excluded from weekly/monthly revenue targets, item transaction
  history, analytics/P&L, or the debt tracker (if the explanation was credit), since
  it's real revenue, just discovered late, and Roy was explicit it must stay
  "accounted for in the system" somewhere. Stock balance correction is unaffected —
  `current_balance()` still reflects the corrective transaction's qty exactly as
  before; only which LIVE revenue tiles it counts toward changed. **(2) Petty cash
  transparency at shift close.** The previous session's fix already correctly nets
  approved petty cash out of `_reconcile()`'s `expected_cash`/`variance` — verified
  the math is sound — but `close_shift()`'s JSON response never actually sent the
  petty-cash amount to the frontend, so staff/owner saw only the final number with
  no way to verify it already accounts for petty cash. Added `petty_cash` to
  `close_shift()`, `active_shift_api()`'s own-shift AND owner-proxy-shift branches,
  and a new amber info box in both `bar_board.html`/`kitchen_board.html`'s
  close-shift result panel showing "Petty cash iliyokubaliwa (tayari imepunguzwa
  hapa chini): KES X" right above the existing offline-sales note. **(3) A tab
  didn't disappear from the drawer after its transfer was accepted and paid.**
  `TabTransferRequest.accept()` moves an entry's `tab_id` to the destination but
  never checked whether the SOURCE tab had anything left — a customer whose entire
  tab (or whose last unpaid entry) got transferred away kept `status='OPEN'`
  forever with a zero balance, lingering in the tabs drawer indefinitely. Fixed by
  checking `not source_tab.entries.filter(is_paid=False).exists()` after the move
  (same check `_finish_settle_tab()` already uses) and closing it `status='VOID'`
  with an explanatory `void_reason` — the exact precedent `_merge_tab_into()`
  already established for an emptied-out tab shell ("not a real cancellation,
  nothing went wrong"). Correctly handles the partial-split case too (Roy pays 400
  of 600 himself — marked paid, stays on his tab — Bosco covers the other 200 —
  once accepted, Roy's tab has nothing UNPAID left even though it still holds one
  already-paid entry, and now correctly closes) and correctly stays OPEN when an
  unrelated unpaid entry remains untouched by the transfer. `reject()` needs no
  equivalent — nothing ever left the source tab in that path. **(4) No discoverable
  way to revoke a single mistakenly-paid entry on an otherwise-still-open,
  multi-item tab.** Traced two compounding facts: `renderTabs()` deliberately hides
  `is_paid=True` entries from a tab's own card (2026-07-23 tab-drawer visual audit),
  and the prior session's `recent_settled_tabs_api` only queried
  `BarTab.objects.filter(status='SETTLED', ...)` — a tab with one paid entry and
  other still-unpaid entries never reaches SETTLED, so it could never appear there
  either. Net effect: that one entry was invisible everywhere, with no revoke path
  at all. Rewrote the endpoint to query `BarTabEntry.objects.filter(is_paid=True,
  paid_at__gte=cutoff, ...)` directly instead of tabs, grouping by `entry.tab` —
  this covers BOTH the settled-whole-tab case (unchanged) and the new still-open
  case in one query, with a `tab_open` flag so the panel can show "(bado wazi —
  bidhaa zingine hazijalipwa)" for the latter. Same fix applied to all three tabs
  drawers per the tabs-drawer-parity rule; `revokeEntryPayment` itself needed no
  changes (already worked per-entry regardless of tab status). **(5) "Stock
  balances should only update after a real cash/mpesa/tab sale"** — audited and
  confirmed already correctly true everywhere (Quick Sell, keg pours, kitchen,
  tab-add, and both transfer paths — `split_paid_unpaid_locked()`'s remainder
  Transaction is deliberately `qty=0`, and the zero-paid-amount full-item-transfer
  path creates no Transaction at all); the ONE place revenue timing needed
  correcting was the live-dashboard leak fixed in (1) above, not stock balance
  itself. 16 new tests across four test classes
  (`StockTakeVarianceDashboardExclusionTest`, `PettyCashVisibleAtCloseShiftTest`,
  `TransferAcceptClosesEmptySourceTabTest`, `RecentPaymentsSurfacesOpenTabEntryTest`).
  No migrations.
- Backfill for the pre-fix stock-take-variance dashboard leak (2026-07-25, same day):
  the `[SVQ]` exclusion above only tags NEW corrective transactions going forward —
  Monsoon Inn's actual this-morning variance was accepted before that fix existed, so
  it stayed untagged and kept showing on the live dashboard even after the fix
  deployed. `backfill_svq_invoice_tags` management command (same one-time-backfill
  pattern as `backfill_tab_tokens`) retroactively tags historical corrective
  transactions via their `StockVarianceQuery.corrective_txn` FK — precise, no
  guessing which transactions came from this flow. `--dry-run` flag to preview
  first; safe to re-run (skips already-tagged rows). Run once per deployed
  environment via Render's Shell tab. 6 new tests. No migrations.
- Fix: Receipts list showed an open tab as a completed "Cash" sale (2026-07-25 live
  report). Root cause: when an item is added to a tab, its receipt is issued with
  `payment_method='tab'` (a literal string — matches the payment method CHOSEN at
  the point of sale, and `'tab'` is a real option alongside cash/mpesa) — correct
  for the individual receipt detail page (`receipt_public.html`, confirmed already
  handles `'tab'` with its own distinct badge), but `receipts_list.html`'s badge
  logic only branched on `'mpesa'`/`'credit'`, silently falling through to
  `{% else %}` → labelled and coloured exactly like a completed cash sale for
  EVERY other value, including `'tab'`. An open, unpaid, still-accumulating tab
  looked indistinguishable from money already in the drawer. Fixed
  `receipts_list()` to batch-resolve (one extra query, not N) whether each
  receipt's underlying tab(s) — via the same `_receipt_all_tab_ids()` helper the
  public receipt page already treats as the single source of truth — are still
  `status='OPEN'`, tagging each receipt `is_open_tab`. New `pay-open` badge ("🔶
  Tab Wazi") replaces the wrong Cash label. Also delivers the efficiency ask in
  the same report ("staff and business owner want ... what they have sold
  already, not what is on tabs"): a `?status=` filter — `sold` (new default,
  excludes open-tab receipts entirely), `open` (open tabs only), `all` — plus a
  banner on the default view naming how many open tabs are hidden with a direct
  link to see them, and filter-aware empty-state copy. A plain Quick-Sell credit
  sale (no BarTab at all) is correctly unaffected — `_receipt_all_tab_ids()`
  returns empty for it, so it stays in the default "sold" view exactly as before.
  **Adjacent bug found while tracing this**: converting a tab to debt (Geuza
  Deni) already correctly flips every underlying Transaction to
  `payment_method='credit'`, but never touched the tab's master Receipt — so
  even after conversion, `/receipts/` kept showing the stale `'tab'` value
  (rendered as "Cash", same bug, different trigger). New shared
  `_sync_master_receipt_payment_method(business, tab, payment_method)` helper
  (`core/keg_views.py`, next to `_cancel_pending_transfers_for_tab` which every
  conversion site already calls) resolves the tab's master receipt via
  `resolve_master_receipt()` and syncs it — wired into all three conversion call
  sites (`convert_tab_to_debt`, `bulk_convert_tabs_to_debt`,
  `_convert_open_tabs_to_debt_for_shift`), matching this file's own "fix one, fix
  every copy" rule for that trio. Never raises — a receipt display glitch must
  never block the actual conversion. 14 new tests
  (`ReceiptsListOpenTabDistinctionTest`,
  `TabToDebtConversionSyncsReceiptPaymentMethodTest`). No migrations. 660 tests
  pass (core + accounts).
- Kitchen Stock Receipt — pooled-cost multi-item delivery tracking (2026-07-25), live
  request with a real Meatco chicken delivery receipt attached (Order #A25533: wings,
  legs, drumsticks bought together for one combined invoice total). Two parts. **(1)
  Diagnosed, not a bug**: Roy's "idadi 10 → balance shows 9.375" report traced to
  `Item.is_yield_item`/`yield_factor` — an existing, working feature (Receipt-type
  transactions in `add_transaction`, `core/views.py`, auto-create an offsetting Wastage
  transaction for `qty × (1 − yield_factor)`) — misconfigured on the affected chicken
  item(s) for what Roy actually wanted (piece-counted cuts, not a yield percentage).
  Remedy communicated: open Edit Item for the affected item(s) and untick Yield Item (or
  set it to 100%) — no code change, since this mechanism is correct for its designed
  use case (e.g. a whole chicken → usable meat after cleaning) and other items may
  legitimately rely on it. **(2) New feature**: one supplier delivery (e.g. wings + legs
  + drumsticks) needs its cost entered ONCE, pooled for a single profit figure, while
  each cut keeps completely ordinary, independent stock and sells via the existing
  preset mechanism — confirmed via clarifying questions: (a) "separate count per cut,
  shared cost only" (no pooled single balance — each `Item` keeps its own
  `current_balance()`, unchanged), (b) closing is a purely manual staff action that must
  NEVER hard-block on stock-balance math, since physical splitting (a big leg cut in
  half and sold as two drumstick-sized pieces) can make the sellable count legitimately
  exceed the nominal received count — "the calculation should go on until she says
  done." New `KitchenStockReceipt` (header: supplier, invoice_no, received_on, status
  OPEN/DONE, closed_at/closed_by) + `KitchenStockReceiptLine` (`core/models.py`,
  migration 0123) — each line creates one completely ordinary Receipt `Transaction` on
  its item (same shape as `kitchen_receive()`'s existing portion-mode branch) and sets
  `item.cost_price = line_cost / qty_received` (a `Item.cost_price` "one designed
  writer" exception, same documented category as `KitchenBatch.open_batch()` and
  `kitchen_receive()`'s own portion branch — noted in the model docstring). Deliberately
  does NOT hook into the sale/checkout path the way `KegBarrel`/`ProduceBunch`/
  `KitchenBatch` do (no `record_sale()` counter) — `total_revenue()` instead sums
  ordinary Issue transactions on the receipt's own items in the window since the
  receipt was created (or up to `closed_at` once closed), reusing the same
  sale_amount-preferred `Case/When` revenue formula used elsewhere in the app. Chosen
  specifically because it needed zero changes to kitchen checkout code, at the accepted
  cost of imprecise attribution if two receipts for the same item ever overlap in time —
  fine given Roy's confirmed real workflow (one delivery sells through before the next
  is ordered). Three views in `core/kitchen_views.py`
  (`kitchen_stock_receipt_create`/`_list`/`_close`), reusing the exact `_kb_gate()` +
  `can_receive_kitchen_stock` + `claim_checkout_token` idempotency pattern already
  established by `kitchen_batch_receive()` — same permission tier, same station
  scoping, same double-submit protection, no new pattern invented. Close accepts an
  OPTIONAL per-line wastage write-off (creates a Wastage `Transaction`, clamped to
  available balance) — never forced; closing with zero write-offs, or with a line
  already oversold past its nominal received qty, both succeed cleanly. Kitchen Board
  UI: new "🧾 Stock Receipt" button beside "+ Pata Stok" (same `can_receive_stock` gate)
  opens a multi-line modal (item picker from `_portionItems` + qty + cost per row, +
  Ongeza bidhaa to add rows); a live "Open Kitchen Stock Receipts" panel shows each open
  receipt's per-line unit costs and running profit-so-far, with a "✓ Fungwa" close flow
  offering the optional per-line write-off inputs. **(3) Per-cut analytics — verified
  already satisfied, nothing new built**: because each cut is tracked as its own
  `Item` (per the confirmed design above), the existing "🍗 Kitchen Performance" table
  in `core/analytics_views.py`/`analytics.html` (already grouped by `item_id`, already
  sorted by revenue descending, already shows units + revenue + cost + margin%) already
  answers "which cut sells more" per-item, and the page's existing 7/30/90/365-day
  period selector (the same mechanism every other analytics section on this page uses)
  already covers week/month/quarter/year framing — building a second, parallel
  reporting mechanism would have duplicated this for no reason. 18 new tests
  (`KitchenStockReceiptTest`), including the real Meatco figures as a fixture. One
  migration (0123, additive). 678 tests pass (core + accounts).
- Stale-tab rounding fix + hidden Yield toggle + per-preset chicken costing
  (2026-07-25, live reports). Two bugs found and fixed, one feature added, all
  same session. **(1) Stale zero-balance tab stuck in the drawer** — root-caused
  via a background investigation agent rather than guessed: all six backend
  "close the tab once nothing is unpaid" paths (`tick_entry`, `settle_tab` →
  `_finish_settle_tab`, `_settle_tab_from_payment`,
  `_settle_receipt_entries_from_payment`, `TabTransferRequest.accept()`,
  `_merge_tab_into`) were already correct and consistent. The actual bug was
  client-side, identically replicated in all three tabs drawers: the "pay it
  all" amount box pre-filled with `Math.round(total)`, then
  `settleTabPartial()`/`qsSettleTabPartial()`/`settleKitchenTab()` compared
  that rounded value back against the exact (often fractional, from
  proportional keg pricing or a split remainder) `total` to decide whether
  staff had edited it. Whenever the cents portion was < .50, rounding DOWN
  made `editedAmount < total` spuriously true even though nobody touched the
  field — silently sending a partial-settle request a few shillings short.
  `BarTab.settle_entries_amount_locked()` correctly never writes off that
  shortfall (by design) — it splits the boundary entry and leaves a genuine
  unpaid remainder, so `tab.status` correctly stayed OPEN, but the
  now-near-zero balance displayed as "KES 0" once the UI's own rounding
  formatter ran, looking exactly like a fully-paid tab stuck in the drawer.
  Fixed by tracking "still the untouched pre-fill" via an explicit
  `dataset.autofilled` flag (set on programmatic pre-fill, cleared by an
  `oninput` handler) instead of comparing values — applied identically to
  `bar_board.html` (cash/mpesa AND the inline STK path), `quick_sell.html`,
  and `kitchen_board.html` (cash/mpesa AND its own modal STK path) per the
  tabs-drawer-parity rule. No backend changes needed — the server-side
  behavior was already correct throughout. **(2) Yield/Processing section
  invisible for kitchen items** — Roy reported never seeing the Yield Item
  toggle for "Kuku" and never having set it, casting doubt on the earlier
  9.375-balance diagnosis. Traced `item_form.html`'s
  `{% if not biz_profile.modules.keg %}` guard around the whole section:
  `business_profiles.get_profile()` sets `modules['kitchen']` dynamically from
  `business.has_kitchen` but leaves `modules['keg']` fixed from the static
  business-type profile — so a bar-type business with a kitchen add-on
  (`modules.keg=True` AND `modules.kitchen=True` simultaneously) hid this
  section for EVERY item business-wide, not just keg items, even though the
  intent was only ever "kegs track waste via KegBarrel/keg_metrics instead,
  don't confuse owners with a second mechanism for those." Fixed: condition
  widened to `{% if not biz_profile.modules.keg or kitchen_store_ids_json %}`
  so the section always renders for a business with any kitchen store, then
  JS-toggled per the already-established `isKitchenStore()` pattern (same one
  driving `costPriceSection`/`kegSettingsBlock`) inside `applyKitchenMode()` —
  hidden only when `YIELD_BIZ_HAS_KEG` is true AND the currently selected
  store is NOT a kitchen store, leaving non-keg businesses and kitchen-store
  items on combo businesses fully unaffected either way. This also means the
  earlier "turn off Yield Item" remedy for the 9.375 report was never
  reachable through the UI and is now understood to be the wrong diagnosis —
  most likely explanation, given point (3) below, is ordinary fractional
  preset consumption under a shared item balance, not yield_factor at all.
  2 new tests (`ItemFormYieldSectionVisibilityTest`). **(3) Per-cut chicken
  costing** — Roy's real catalog for chicken is NOT separate items per cut
  (Wing/Leg/Drumstick as built in the Kitchen Stock Receipt sprint above) —
  it's ONE item ("Kuku") with presets per cut (Bawa/Paja/Kifua), since pieces
  arrive pre-cut from a butcher, not as whole birds processed on-site.
  Presets share one stock balance by design (unchanged) but previously had no
  cost of their own, so a pooled Stock Receipt against "Kuku" could only ever
  write one blended `item.cost_price`, hiding that wings/legs/drumsticks cost
  genuinely different amounts. Confirmed via AskUserQuestion (financial-
  correctness decision, not guessable) — Roy chose to keep the one-item
  catalog and add per-preset costing, explicit that cost must be settable
  ONLY from the Stock Receipt side, never the item form. New
  `ItemPortionPreset.cost_price` (nullable, migration 0124) — written
  exclusively by `kitchen_stock_receipt_create()`, never exposed on
  `item_form.html`. `KitchenStockReceiptLine.preset` (nullable FK, same
  migration) records which cut a line represents. The "Chagua Bidhaa" picker
  in the Stock Receipt modal now expands any item WITH presets into one
  option per preset (`ItemName — PresetLabel`, encoded as `itemId:presetId`)
  so receiving matches how a real supplier invoice actually itemises pre-cut
  pieces; a preset-costed line still creates one ordinary Receipt Transaction
  adding qty to the SAME shared item balance (physical stock genuinely is
  shared — unchanged) but writes its unit cost to `preset.cost_price` instead
  of `item.cost_price`, which is left untouched for such items. A line with
  no `preset_id` (an ordinary item) keeps the original single-cost-price
  behavior exactly as before — fully backward compatible. Cross-item preset
  safety: a `preset_id` that doesn't actually belong to the given `item_id`
  is silently rejected (same defensive pattern as the item-store check).
  **Known, explicitly-flagged limitation**: `Transaction` has no `preset` FK
  today, so while per-cut COST is now tracked accurately, per-cut SOLD
  REVENUE still cannot be reconstructed from sales history — a sale of Bawa
  vs Paja both just look like an ordinary "Kuku" Issue transaction. True
  "wings earned X, drumsticks earned Y" analytics would need `Transaction`
  to record which preset triggered a sale, touched at every PORTION-mode
  checkout call site app-wide (Quick Sell, kitchen board, bar board) — out of
  scope for this session, flagged to Roy rather than silently built or
  silently omitted. 7 new tests (`KitchenStockReceiptPresetCostingTest`). Two
  migrations (0124; the tabs/yield fixes needed none). 687 tests pass.
- Cash-reconciliation bug — split-remainder transactions silently tagged
  'cash' (2026-07-25, live Monsoon Inn report: system showed KES 2980
  expected cash for a shift, physical count was KES 1700, staff insisted
  entries were correct and no money had left the drawer). Traced
  `core.shift_views._reconcile()` (feeds `close_shift`, `active_shift_api`,
  `shift_history`, the Z-report — every "expected cash" figure in the app):
  its `cash_sales` aggregate reads `Transaction.payment_method` directly, with
  zero awareness of the sibling `BarTabEntry.is_paid` flag. Root cause:
  `BarTabEntry.split_paid_unpaid_locked()` (`core/models.py`) — the shared
  building block behind BOTH the 2026-07-25 "theft-prevention" partial-amount
  settle feature (`BarTab.settle_entries_amount_locked`) AND the "split bill
  to a different customer" transfer feature (`split_and_transfer_locked`) —
  creates the STILL-UNPAID remainder as a new `Transaction` with no
  `payment_method=` kwarg. `Transaction.payment_method`'s model field default
  is `'cash'` (unlike `BarTabEntry.payment_method`, which correctly defaults
  to blank) — so every split-remainder, whether sitting unpaid on the same
  tab or a split-transfer still pending the OTHER customer's acceptance, was
  silently counted as a completed cash sale the instant it was created, even
  though no money had changed hands. Fixed by setting `payment_method='credit'`
  explicitly — matching the convention every other "on a tab, not yet
  collected" `Transaction` in the app already uses (`KegBarrel.record_sale`:
  `pay = 'credit' if tab else ...`), so it now correctly lands in
  `_reconcile()`'s `credit_sales` bucket, which the shift math already
  excludes from `expected_cash`. One shared fix in `split_paid_unpaid_locked()`
  covers both features at once, since both route through it. Regression-swept
  every other `Transaction.objects.create()` call site in `core/` for the
  same "Issue-type transaction created with no explicit payment_method"
  pattern — all others either specify it explicitly or are non-Issue types
  (Receipt/Wastage/Draw, not counted by `_reconcile()`'s Issue-only filter)
  and are unaffected; two lower-traffic surfaces (`api_views.py`'s DRF cart
  checkout, the legacy USSD stock-logging flow) share the same omission but
  are separate from the bar/kitchen/Quick-Sell flow this incident traced
  through — flagged as a follow-up, not fixed in this pass.
  Also fixed in the same session: a stray leading `;` before `restock_views.py`'s
  module docstring was crashing the ENTIRE app (any request touching
  `stockapp/urls.py`, which imports from it) — caught via `manage.py check`
  failing with a `SyntaxError` mid-session; removed.
- Recent Payments date picker (2026-07-25, same-day follow-up): Roy reported
  the "🕐 Malipo ya Hivi Karibuni" revoke-payment panel only ever showed "a
  few" transactions and he couldn't find today's confirmed sales to correct a
  mistaken payment method. Root cause: `recent_settled_tabs_api`
  (`core/keg_views.py`) used a fixed rolling 6-hour window plus a hard cap of
  the newest 20 tabs / 100 entries — exactly matching his report. Replaced
  with an explicit `?date=YYYY-MM-DD` param (defaults to today, LOCAL
  calendar day via `timezone.localdate()`/`make_aware()`, matching every
  other "today" surface in this app) and removed the artificial cap entirely
  — a single business's one-day paid-entry count is never large enough to
  need one. Added a `<input type="date">` picker to the panel header, wired
  to reload on change, in all three tabs drawers (`bar_board.html`,
  `quick_sell.html`, `kitchen_board.html`) per the tabs-drawer-parity rule —
  each defaults to today via a small `_todayIso()` helper (one copy per
  file, matching this app's established per-template JS convention) and only
  auto-fills the date input the first time the panel is opened, never
  overwriting a date the owner has already picked. 11 new tests
  (`RecentPaymentsDatePickerTest`). No migrations.
- Direct-sale payment correction + Receipts/Transaction History date filters
  (2026-07-25, same-day follow-up). Roy reported a "Viceroy" sale visible in
  Receipts and Transaction History but missing from the Recent Payments
  panel he'd just gotten a date picker for. Root cause: `recent_settled_
  tabs_api` only ever queried `BarTabEntry` — but a DIRECT checkout (Quick
  Sell / bar board / kitchen board cash-or-mpesa sale with no tab involved
  at all) creates a plain `Transaction` with no `BarTabEntry`, so it was
  invisible to that panel and had NO correction path at all, tab or
  otherwise. Added a second `direct` list to the same endpoint —
  `Transaction.objects.filter(type='Issue', payment_method__in=['cash',
  'mpesa'], tab_entry__isnull=True, ...)` for the same selected day,
  station-scoped the same way. New `correct_transaction_payment_method`
  view (`/bar/transactions/<id>/correct-payment/`) is the direct-sale
  sibling of `revoke_entry_payment` — same shift-gate + station-scope
  pattern, but simpler: a direct sale has no "unpaid" state to revert to
  (the sale is genuinely complete), so it just relabels cash↔mpesa, never
  touching `qty`/`sale_amount`. New `_notify_direct_correction()` helper
  mirrors `_notify_tab_correction`'s exact recipient fan-out (on-shift staff
  + owners/managers, in-app + SMS) without a `BarTab` to key off. All three
  tabs drawers render a new "Mauzo ya Moja kwa Moja (si tab)" section in the
  same panel with a "🔄 Cash/M-Pesa" toggle button per direct sale, reusing
  the same `openReasonChips` pattern. Separately, added the requested date
  picker + running count to two more surfaces: `receipts_list` (`?date=
  YYYY-MM-DD`, takes priority over month/year when given, new
  `receipt_count` context var, a visible "🧾 N receipt(s)" banner) and
  Transaction History (`templates/core/transaction_history.html` — a
  `<input type="date">` combined via AND logic with the existing free-text
  search, both driving one shared `applyFilters()` that updates a live "X /
  Y transactions" count banner; each row's `data-date` attribute uses
  `|date:'Y-m-d'` so the date input's ISO value matches directly). 15 new
  tests (`DirectSalePaymentCorrectionTest`, `ReceiptsListDateFilterTest`).
  No migrations — confirmed and told to Roy directly, since he asked
  whether anything was needed on Render Shell: nothing, this round is
  code-only, ordinary git push + Render's normal auto-deploy is sufficient.
- Fix: stale-tab-stuck-forever bug (2026-07-25, same-day follow-up, live
  screenshot). Roy reported a specific tab ("Muya") still stuck in the Quick
  Sell drawer after the earlier rounding-flag fix — tapping "Lipa Yote —
  M-Pesa" did nothing, every time. Traced the exact mechanism from the
  screenshot: the tab's own header total (KES 0) was correct — every entry
  on it was already `is_paid=True` — but `tab.status` had never flipped to
  `'SETTLED'`. Root cause found in `settle_tab()` (`core/keg_views.py`): its
  `if not entries_to_settle:` guard (fires when there is nothing new to
  settle) unconditionally returned a 400 error, with no check for "is the
  WHOLE tab actually already fully paid" — so a tab that drifted into this
  state (exact drift path not reproduced live, but the effect is directly
  observable) could NEVER self-heal; every future tap hit the same guard
  before ever reaching `_finish_settle_tab()`, the only code path that
  actually closes a tab. Fixed by checking
  `tab.entries.filter(is_paid=False).exists()` inside that guard — if
  nothing is owed ANYWHERE on the tab, call `_finish_settle_tab()` right
  there instead of erroring, so the exact same "Lipa Yote" tap Roy was
  already making now closes the stuck tab for good. Deliberately narrow: if
  `entry_ids` were given and those specific entries are already paid while a
  DIFFERENT entry elsewhere on the tab is still genuinely unpaid, this still
  errors as before — that's a wrong-selection mistake, not a stuck tab, and
  must never silently close a tab with real money still owed.
  **Also found while tracing this (not fixed — display-only, lower
  priority, noted for a future pass)**: `bar_board.html`'s `renderTabs()`
  filters `is_paid=True` entries out of the normal card entirely (matching
  the 2026-07-23 tab-drawer visual audit's stated intent), but
  `quick_sell.html` and `kitchen_board.html` both still render them
  (checked+disabled, struck-through) — a tabs-drawer-parity gap where only
  one of the three drawers actually hides settled items from the main
  card. Cosmetic once this fix ships (the entry disappears the moment the
  tab closes), not touched this pass. 3 new tests
  (`StaleFullyPaidTabSelfHealsOnSettleTest`). No migrations.
- Accountability overhaul II — 9-item request from a real KES 4000-vs-1700
  Monsoon Inn reconciliation gap (2026-07-26). Roy's own framing: build
  toward 100% realistic, practical cash accountability, not just fix the one
  number. Cause-and-Effect map produced first per this file's own protocol.
  **Fix 0 (found mid-session, higher priority than the request itself):**
  root-caused the actual 4000-vs-1700 gap — `open_shift()` only blocks the
  SAME staffer from opening two shifts, so two different staff can have
  overlapping OPEN shifts on the same bar counter (a forgotten handover
  close, or two people genuinely sharing one till); `bar_z_report`'s day
  totals (and `bar_z_report_share`'s SMS) summed each shift's own
  `_reconcile()` into the day figure with no overlap check, double-counting
  every sale made during the overlap — inflating cash AND mpesa identically,
  exactly matching the report. `home()`'s dashboard tile was never affected
  (already a single deduped day-level query). Fixed both to compute the
  owner's day total the same way — one deduped query — plus a non-blocking
  overlap banner listing which staff overlapped, and a heads-up (never a
  block) at `open_shift()` when another staffer already has that station
  open. **Item 1 (petty cash truthfulness):** `_reconcile()` gained
  `debt_recovered_cash/mpesa` (a customer paying back an OLD debt in cash is
  a different model — `CustomerDebtPayment` — from a NEW credit sale, and was
  previously invisible to `expected_cash` entirely — this is also the item-3
  answer: "Credit" = new debt given out, never debt recovered) and
  `petty_cash_pending/rejected`, so the shift-close screen shows the full
  chain (cash sales → petty cash pending → remaining if approved/rejected)
  instead of three disconnected numbers, in both `bar_board.html` and
  `kitchen_board.html`. `PettyCash` gained `staff_note`/`staff_note_at`
  (migration 0125) — staff can edit their own still-pending entry
  (`edit_petty_cash`), or explain themselves after a rejection
  (`respond_petty_cash`, notifies the owner, never changes status on its
  own) — the whole `/petty-cash/` list page was owner-only before this;
  widened so staff see (and can act on) only their own entries. Non-blocking
  mismatch flag at record time when an entry would exceed the shift's
  available cash (warns, still records — this app never hard-blocks on a
  figure that could be legitimate). `BusinessExpense` gained a `petty_cash`
  category; `linked_expense` FK created only on approval, deleted if later
  reversed back to rejected (symmetric with the existing approve/reject
  undo). `_staff_contribution()`/`staff_journey.html` gained
  `petty_cash_rejected`/`petty_cash_pending_kes` with the owner's own
  `review_note` shown — the storytelling Roy asked for. **Items 2+4 (direct-
  sale payment split):** `Transaction.split_payment_method_locked()` (mirrors
  `BarTabEntry.split_paid_unpaid_locked`'s qty=0 remainder pattern) splits a
  mis-tagged direct sale (no tab) across two payment methods — e.g. 500
  entered as mpesa, actually 200 cash + 300 mpesa — via a new
  `split_transaction_payment_method` endpoint alongside the existing whole-
  amount `correct_transaction_payment_method`; wired into bar_board.html and
  quick_sell.html's Recent Payments panel (kitchen_board.html never had the
  base direct-correction feature at all — pre-existing gap, not widened
  here). Receipt reflection is a best-effort, precise-match-or-skip
  heuristic (`Receipt.lines` has no stored link back to a Transaction) —
  same business/day, one line matching item name + pre-split subtotal
  exactly; ambiguous (0 or 2+ candidates) silently skips rather than
  guessing. **Item 6 (stock-take variance item lock, scoped to "just the
  specific item" per Roy's own answer):** `item_has_pending_variance()`
  (`core/stock_take_views.py`) blocks selling ONE item — never the whole
  business — while it has an unresolved `StockVarianceQuery` (pending OR
  responded; only an actual owner `review_variance()` accept/dismiss, which
  is already owner/manager-only, sets RESOLVED and lifts it — "only
  revocable on the owner's side" for free, no new unlock endpoint needed).
  Wired into Quick Sell's cart loop (skip-with-message, other lines still
  sell), `add_transaction` (Issue only — Receipt/Wastage are how a variance
  often gets resolved, so left open), and kitchen board's portion-item
  branch. Bar board's keg pours are a different, already-built reconciliation
  mechanism (barrel weight, not per-Item stock-take) — correctly out of
  scope. **Item 7:** `STAFF_PAY_ROLES` in `recurring_expense_views.py` was
  missing `'manager'` entirely since Sprint M1 gave managers full operational
  access — one-line fix, confirmed via grep it's the single definition.
  **Item 8b (wastage/variance attribution):** `_staff_contribution()` had
  zero wastage or stock-variance-loss figures — added `wastage_kes` (Wastage
  transactions by `recorded_by`) and `variance_loss_kes` (via
  `StockVarianceQuery.attributed_shift`), surfaced in `staff_journey.html`.
  Found and fixed a real `FieldError` in this new aggregate during the full
  suite run: `Abs(F('qty')) * Coalesce(F('item__cost_price'), Value(0))` with
  no explicit `output_field` mixed DecimalField and Value's default
  IntegerField — added `output_field=DecimalField(...)`, matching the `_rev`
  pattern already used everywhere else in this app. **Item 5 (structured
  request/approval, scope confirmed via clarifying question):** new
  `StaffRequest` model (migration 0126) — category
  (restock/permission/correction/general), subject, description, status,
  reviewed_by/at/note — deliberately NOT a generic FK to every model (that
  would duplicate StockRequest/WriteOffRequest/StockVarianceQuery's own
  dedicated machinery); this is for everything else. `core/
  staff_request_views.py` + `/staff-requests/` page (staff see only their
  own; owner/manager see and review all, always with a reason back to the
  requester, matching this app's wording/accountability standard). **Item 8
  (Haki payroll, scoped after discovering `SalaryPayment` already supports
  partial payments and a `staff_note` field from an earlier, undocumented
  change — did not rebuild what already existed):** `SalaryPayment` gained
  `confirmed_by_staff`/`confirmed_at` (migration 0127) — a "✓ Nimepokea"
  button on Kazi Yangu's pay history closes the loop `record_salary_payment`'s
  SMS notice started but never confirmed; idempotent (re-confirming is a
  no-op), notifies whoever recorded the payment. New `run_payroll` view
  (`/staff/payroll-run/`, owner/manager) — one pass across all pay-eligible
  staff for a period, pre-filled from each staff's configured
  `RecurringExpense` salary line, creating one `SalaryPayment` per selected
  row via the same creation shape `record_salary_payment` already uses (not
  a duplicated code path). 736 pre-existing core+accounts tests confirmed
  green before this sprint's own ~60 new tests were added on top. Three
  migrations (0125, 0126, 0127), all additive.
- Accountability overhaul II, follow-up (2026-07-26, same day): Roy's live
  correction on operational model + two feature asks. **Manager shift gate
  clarified**: the owner sells freely at all times, no gate; a manager
  supervises and may do EVERY oversight/corrective action (settle, void,
  revoke, restock, receive stock, breakage, approvals) without opening a
  shift, but to actually SELL (create a new Issue transaction) a manager
  must open their OWN shift, exactly like ordinary staff — `get_active_
  staff_shift()` previously returned "no gate" for `is_owner_or_manager`
  unconditionally, silently letting managers sell without ever opening a
  shift. Added a `manager_must_have_shift` kwarg (default False — every
  existing oversight call site is completely unaffected) and threaded
  `manager_must_have_shift=True` through the four real "new sale" entry
  points only: `quick_sell()`, `add_transaction()` (Issue only — Receipt/
  Wastage are oversight, left ungated for managers, required restructuring
  to parse `trans_type` before the gate instead of after), `bar_board()`'s
  keg-cart checkout, and `_kitchen_checkout()` (both had their own inline
  `is_owner_or_manager`-named-`is_owner` checks, not routed through the
  shared helper at all — fixed to call it properly). 8 new tests
  (`ManagerMustHaveOwnShiftToSellTest`); all 724 pre-existing tests
  confirmed still green (oversight actions untouched). **Stock receipt
  confirmation**: owner orders stock remotely and isn't present to witness
  delivery — whoever DOES receive it (staff on shift, or a manager without
  one) can now tick "📦 Hii ni oda ya mmiliki (hayupo)" at Add Transaction's
  Receipt step, which auto-creates a `StaffRequest` (new `stock_confirm`
  category + a `related_transaction` FK — the one deliberate exception to
  "no generic FK", since this request is inherently about one specific
  Transaction) linking straight to the recorded Receipt; a second person
  (owner reviewing remotely, or a manager) confirms accurate or disputes via
  the SAME existing `/staff-requests/<id>/review/` flow — no new endpoint
  needed. Never prompted when the owner receives stock personally (nothing
  to confirm). 4 new tests (`StockReceiptConfirmationTest`). **Salary
  advance requests**: new `SalaryAdvanceRequest` model (amount, reason,
  period, status, reviewed_by/at/note, `salary_payment` FK set on approval)
  — staff submit via a "🆘 Omba Advance ya Dharura" button on Kazi Yangu;
  owner/manager approve (immediately creates the actual disbursement —
  `SalaryPayment(payment_type='advance')`, a new payment type alongside
  full/partial — reducing that period's remaining balance right away, same
  as any other payment) or reject (reason required back to the staffer, no
  money moves). New shared `_salary_period_balance(business, staff, period)`
  helper — expected (from the staff's configured `RecurringExpense` salary
  line, if any) minus paid-so-far (every `SalaryPayment` for the period,
  all types combined) — is the single source of truth used at payment
  confirmation, Kazi Yangu's display, and advance approval, so "remaining
  balance" is never computed two different ways. Kazi Yangu shows the
  remaining balance figure, an advance-request history list with each
  request's outcome and reviewer, and the request modal; owner's Haki
  contribution report surfaces pending advance requests inline for
  approve/reject. 7 new tests (`SalaryAdvanceRequestTest`). Migration 0129
  (SalaryPayment.payment_type gains 'advance'; new SalaryAdvanceRequest
  table); migration 0128 (StaffRequest.related_transaction +
  stock_confirm category). 19 new tests total this follow-up, on top of
  the same-day sprint above.
- Continuous till accountability (2026-07-27): `shift_views.till_expected_cash(business,
  station)` — a live, continuous "what should be in this till right now" figure, anchored
  on the last shift that physically closed with a counted balance for that station, then
  adding every cash sale/cash debt-recovery and subtracting approved petty cash + banked
  amounts since, purely by time + station (never shift boundaries) — so cash the owner
  sells directly with no shift open is automatically included, and any staff opening next
  (not just the same person who closed last) sees the correct expected float. `open_shift()`
  now stores `Shift.expected_opening_cash`/`opening_variance`/`banked_amount` and alerts
  owner/manager (mirroring the existing >KES 500 close-shift alert) on a material mismatch
  between what staff physically counted and what the till expected. Fixed a real pre-existing
  gap while building this: `_reconcile()`'s petty cash queries had no station filter at all,
  so a kitchen withdrawal could silently reduce a bar shift's expected cash on combo
  bar+kitchen businesses — `PettyCash.station` (new field, auto-derived from the recording
  staffer's role, explicit via the shared petty-cash modal on bar_board/kitchen_board) fixes
  this. home() dashboard gains a live "expected counter cash" tile per station (owner sees
  all, staff see their own, visible before opening shift); shift_history.html now shows the
  petty-cash approved/pending/rejected breakdown and opening-variance figures on a CLOSED
  shift's card so a later petty-cash review is reflected before clicking Thibitisha. Applied
  identically to bar_board.html and kitchen_board.html per this file's counter-parity rule.
  16 new tests. Migration 0130 (additive). 792 tests pass.
- Till reset bug, opening-variance acknowledge, cross-counter Recent Payments leak
  (2026-07-27, three live reports from Roy). (1) The till appeared to "reset" at business
  closing hours — `_auto_close_expired_shifts()` force-closes an OPEN shift with
  `closing_cash_counted` left at `None` (nobody ever counted the drawer), and
  `till_expected_cash()`'s anchor query only checked `ended_at`, so `float(None or 0)`
  silently treated "we never counted" as "the till held exactly zero." Added
  `closing_cash_counted__isnull=False` to the anchor filter — real cash now correctly
  carries through an unattended auto-close; a deliberate manual close with an explicit `0`
  (a real, submitted data point) still anchors correctly. (2) Opening-variance
  acknowledge/flag mechanism, mirroring the existing close-side `variance_note`/
  `variance_review` pair: when an owner acknowledges an explained opening variance (e.g. "I
  deposited that 2000 to my own M-Pesa before this shift opened, staff correctly counted
  0"), the amount folds into `Shift.banked_amount` — the same field `till_expected_cash()`
  already subtracts — so the running till reflects the correction immediately rather than
  waiting for the shift to eventually close with a real count. Reversible (re-flagging
  undoes the fold-in), same undo pattern as petty cash review. New
  `add_opening_variance_note`/`review_opening_variance` views, reason-chips capture in
  bar_board.html/kitchen_board.html right after a variance is detected at open, and an
  acknowledge/flag block in shift_history.html mirroring the closing-side UI. (3) "Recent
  Payments" panel (🕐 Malipo ya Hivi Karibuni) was showing cross-counter sales — kitchen
  sales visible from Bar Board, bar sales visible from Kitchen Board/Quick Sell ("Bar
  Orders"). Root cause: all three templates hit the exact same `/bar/tabs/recent-settled/`
  URL with no indication of which counter was asking, so the endpoint fell back to
  `_allowed_tab_sources(up)` — an IDENTITY check (what this viewer is PERMITTED to see) —
  as if it were also a DISPLAY-SCOPE check; for an owner/manager both stations are always
  permitted, so nothing was ever actually excluded. Added an explicit `?station=` param
  (still intersected with the permission check, never bypassing it), now sent by all three
  templates. The dashboard's per-station till tiles were independently verified NOT to
  share this bug — each call passes an explicit station parameter with no identity-based
  fallback. Also fixed a pre-existing flaky test (`BarZReportOverlappingShiftsTest`) that
  hardcoded shift start times as `now() - timedelta(hours=N)`, crossing back into the
  previous LOCAL calendar day when run in the first few hours after local midnight — same
  bug class already documented above (`PettyCashReviewUndoTest`, 2026-07-25). 12 new tests.
  Migration 0131 (additive).
- CSRF login-failure dead-end fix (2026-07-27), live report: ~80% of client logins hit a
  raw Django "Forbidden (403) — CSRF verification failed" page, requiring the customer to
  clear phone cache and reopen the app icon to recover; also asked to make cross-device
  login "boot" the prior session automatically instead of erroring. Root-caused to the
  Service Worker: `sw.js`'s general navigation handler could serve a stale cached copy of
  an auth-adjacent page (login/signup) on a slow/flaky mobile connection, and a stale page
  carries a stale CSRF token — the very next POST (login submit) fails verification with
  Django's default raw 403 page, which has no "try again" affordance, hence the
  clear-cache-and-reopen workaround clients had found on their own. `SingleSessionMiddleware`
  (the cross-device "boot the other session" mechanism, Sprint 17) was independently
  confirmed already correct and unrelated to this bug — it only fires post-login, this
  failure was pre-login. Two-layer fix: (1) `sw.js` (bumped `duka-v10`→`duka-v11`) — new
  early-exit block in the fetch handler makes any navigation whose URL contains
  `/accounts/` or `/signup/` network-only, falling back to the offline page (never a stale
  cached copy of the same URL) if the network truly is unreachable; (2) new
  `CSRF_FAILURE_VIEW = "core.views.csrf_failure_view"` (`stockapp/settings.py`) — a safety
  net for the residual case (token genuinely expired mid-session, or a network hiccup during
  the POST itself): shows a friendly Swahili+English explanation via `messages.warning` and
  redirects straight back into the app (home if already authenticated, login otherwise)
  instead of Django's raw unstyled dead-end page — so even when a CSRF failure still
  happens, it self-recovers with one tap instead of requiring a cache-clear. 2 new tests
  (`CsrfFailureViewTest`, using `Client(enforce_csrf_checks=True)`). No migrations.
- Shift station misattribution fix + checkout-time split payment, all 3 counters
  (2026-07-27–28), from a live Monsoon Inn report with screenshots: "no bar sales recorded
  yesterday before they closed but kitchen ones were made, so the counter cash entry there
  is wrongly placed," plus two feature gaps — kitchen board's Recent Payments panel had no
  split-payment correction (bar board already did), and no counter anywhere let a customer
  paying straight cash/mpesa (not a tab) split across both methods at the point of sale
  itself ("if chipo is 100 the customer may pay 40 cash and 60 mpesa... physical cash and
  mpesa should be exactly accurate to what is in the system, this applies to all
  counters"). **Root cause of the till mis-attribution**: `_shift_station()`,
  `till_expected_cash()`'s anchor query, `_reconcile()`, and
  `_convert_open_tabs_to_debt_for_shift()` all discriminated bar-vs-kitchen purely from
  `staff.userprofile.role == 'kitchen'` — correct for an ordinary single-station staffer,
  wrong for a manager or any cross-access staff member (`can_access_bar`+
  `can_access_kitchen`) actually working the OTHER counter that shift; their role never
  changes, so every till/reconciliation/shift-history calculation silently attributed their
  kitchen-counter shift to the bar (or vice versa) — exactly the Monsoon Inn symptom.
  `shift_history()`'s station filter had a second, independent bug in the same area: it
  filtered on `Shift.store`, which is always `business.stores.first()` regardless of which
  counter the shift actually ran, not a real per-shift signal at all. Fixed by adding an
  explicit `Shift.station` field (migration 0132, `'bar'`/`'kitchen'`, blank for
  pre-migration rows) captured once at `open_shift()` time from the URL prefix the request
  actually came in on (`/bar/...` vs `/kitchen/...` — 100% reliable, unlike role) and stored
  on the Shift permanently; new `_shift_station(shift)` prefers this explicit field,
  falling back to the old role-based inference only for blank legacy rows, and new
  `_station_q(is_kitchen)` gives the same logic as a Q-object for queryset-level filtering.
  Propagated through every call site that had the bug: `till_expected_cash()`'s anchor +
  banked-amount queries, `_reconcile()` (txns/petty-cash/debt all three), `open_shift()`'s
  overlap check, `active_shift_api()`, `_convert_open_tabs_to_debt_for_shift()`, and
  `shift_history()`'s station filter (rewritten from the broken `store`-based filter to
  `_station_q()`). **Adjacent cross-counter leak, same investigation**: the "🕐 Malipo ya
  Hivi Karibuni" (Recent Payments) correction panel in all three tabs drawers was showing
  the OTHER counter's sales — root cause was `recent_settled_tabs_api()` using
  `_allowed_tab_sources(up)` (an IDENTITY check — what this viewer is PERMITTED to see) as
  its DISPLAY-SCOPE filter too; for an owner/manager both stations are always permitted, so
  nothing was ever actually excluded regardless of which drawer was asking. Fixed with an
  explicit `?station=` param sent by all three templates, intersected with (never
  bypassing) the existing permission check. **Kitchen split-payment parity**: confirmed the
  backend (`split_transaction_payment_method`) was already station-agnostic and correctly
  gated — this was a frontend-only gap; added the same "✂️ Gawanya" button/JS to
  `kitchen_board.html`'s Recent Payments panel that bar board and Quick Sell already had.
  **New: checkout-time split payment** — new `Transaction.apply_split_payment_locked(cls,
  txn_ids, business, split_amount, split_method, staff_user=None)` (`core/models.py`,
  mirrors the existing correction-time `split_payment_method_locked()`'s qty=0 remainder
  pattern) is the single model-layer entry point: given the just-created direct-sale Issue
  transaction ids from ONE checkout, recolors whole transactions to `split_method` first
  (walking oldest-first) and, if the split lands mid-transaction, delegates to
  `split_payment_method_locked()` for that one boundary transaction — so a multi-line cart
  splits correctly no matter how many lines it takes to cover the split amount. Wired into
  all three checkout views identically: `quick_sell()` (`core/views.py`), `bar_board()`'s
  keg-cart checkout (`core/keg_views.py`), and `_kitchen_checkout()` (`core/kitchen_views.py`)
  each collect `created_txn_ids` for direct (non-tab) sales only and apply the split — never
  for tab/credit sales, and never blocking the checkout itself (the sale already happened;
  a `ValueError` from an invalid split amount just shows a warning message, the sale
  stands). Frontend: a "✂️ Gawanya malipo" toggle + amount input added next to the
  cash/mpesa payment selector in all three checkout UIs (`bar_board.html`,
  `kitchen_board.html`, `quick_sell.html`), each validating the split amount client-side
  against the cart total before submit, per the tabs-drawer-parity convention applied here
  to checkout UI as well. 4 new test classes (`ApplySplitPaymentLockedTest` — model-level:
  single split, split≥total raises, zero/blank no-op, multi-line split, same-method no-op;
  `QuickSellCheckoutSplitPaymentTest`, `BarBoardCheckoutSplitPaymentTest`,
  `KitchenBoardCheckoutSplitPaymentTest` — end-to-end POST tests, each also confirming the
  split is never applied to a tab/credit sale). One migration (0132, additive). 823 tests
  pass (core + accounts).
- Till "not yet established" fix (2026-07-28), same-day follow-up to the station
  misattribution fix above — Roy pushed back with screenshots: Bar showed KES 1400
  expected counter cash with ZERO bar sales that day (kitchen had all the activity,
  cash KES 100 which kitchen staff took as lunch and logged as pending petty cash).
  Station misattribution was already ruled out (this business genuinely has no bar
  shift ever closed with a real physical count). ROOT CAUSE, distinct from the
  previous fix: `till_expected_cash()`'s docstring always said "0 if this station has
  never closed a shift," but the CODE didn't match — when `anchor` was `None`,
  `window_start` was also `None`, which left the cash-sales/debt-recovered/petty-cash
  queries completely unfiltered by time, summing EVERY cash Issue transaction ever
  recorded against that station since the business was created — including sales from
  long before this till-tracking feature existed. That unbounded historical sum is
  what produced 1400 for Bar: not a bug in reading which station a shift belongs to,
  but a bug in what "no anchor yet" means. Fixed by returning early with
  `expected_cash: None, anchor_established: False` whenever no shift has ever closed
  for that station with a real counted amount — "unknown, not yet tracked" instead of
  a guessed number. Propagated the `anchor_established` flag through every caller:
  `open_shift()` now sets `expected_opening_cash`/`opening_variance` to `None` (both
  fields already nullable) and skips the >KES 500 opening-variance alert entirely when
  no anchor exists yet — nothing trustworthy to compare against; the shift's own
  eventual close (with a real physical count) becomes the first anchor for every
  future computation, matching the existing closing-anchor design exactly. The
  open-shift-modal float suggestion (`last_closing`) and the JSON response's
  `expected_opening_cash`/`opening_variance` already had `!== null` guards on the
  frontend and in `shift_history.html`'s template (defensively written ahead of this
  exact scenario, it turned out) — only the JSON response builder itself needed a
  `float(x) if x is not None else None` guard to avoid a `TypeError` on `float(None)`.
  `home.html`'s till tile now shows "Bado haijawekwa" (not yet set) per station
  instead of a number when `anchor_established` is false, with an explanation that
  tracking begins once that station's first shift is properly closed with a real
  cash count — never silently displaying a fabricated figure. Four pre-existing tests
  had encoded the old (buggy) "sum all history" behavior as their expected result and
  needed updating to the new correct behavior; two new regression-lock tests added
  (`test_no_prior_shift_means_till_is_not_yet_established`,
  `test_kitchen_established_bar_not_matches_dashboard_scenario` — the latter
  reproduces the exact dashboard shape of the live report: kitchen has a real anchor
  and real sales, bar has neither). No migrations. 825 tests pass (core + accounts).
- Home dashboard cache hardening (2026-07-28), same-day follow-up: after the till fix
  above deployed, Roy still saw the stale KES 1400 figure on screen. The server-side fix
  was correct — this was a caching problem, not a data problem. `home()` (`core/views.py`)
  had no explicit Cache-Control headers at all, so a dashboard this volatile (live shift
  status, till figures, revenue, notifications) was exposed to being served stale by any
  layer between the browser and the view — the phone's own HTTP disk cache, a mobile
  carrier's transparent compression proxy (common on Kenyan mobile data), or an edge case
  in the service worker's cache — even though the SW's own navigate handler is already
  network-first for "/". Added `@never_cache` (Django's standard decorator) to `home()`,
  forcing `Cache-Control: no-cache, no-store, must-revalidate` on every response so this
  page can never be served stale by any caching layer again, without relying on the user
  to manually clear their cache after every deploy. 825 tests pass (core + accounts). No
  migrations.
- Home dashboard till breakdown disclosure (2026-07-28), same-day follow-up: Roy cleared
  cache five times and the KES 1400 Bar figure never moved — strong evidence it was a
  genuine, correctly-computed number from real historical data, not a caching bug at all
  (confirmed moments later: Christine Nyakundi's Saturday shift closed with a real physical
  count, and nothing had closed on Bar since, so the till correctly kept carrying that
  forward). Rather than keep diagnosing live production numbers blind (no direct DB
  access), added an owner-only `<details>` disclosure under each station's KES figure on
  the home dashboard — "🍺 Bar — vipi hesabu hii ilipatikana?" — surfacing
  `till_expected_cash()`'s own `anchor_label` and full `breakdown` dict (base, cash sales
  since anchor, debt recovered, petty cash deducted, banked deducted) directly on screen,
  so a live "where did this number come from" question is self-answerable without a Render
  shell session. 2 new tests (`HomeDashboardTillBreakdownTest` — owner sees the breakdown
  with correct figures, staff does not see it at all). No migrations. 827 tests pass (core
  + accounts).
- Per-preset sale-time cost attribution + custom-price presets (2026-07-29). Roy's live
  design question, with a real Meatco chicken receipt as the concrete case: one shared
  "Kuku" item sells wings/legs/drumsticks via presets (built 2026-07-25's Kitchen Stock
  Receipt sprint), but a chicken leg split in half and sold at the SAME price as a
  drumstick (KES 150) does NOT cost the same as a drumstick — and `Transaction.cost()` had
  no way to know WHICH preset made a sale, only which item, so every preset sale was costed
  against one blended `item.cost_price`. This is exactly the gap flagged (but not closed)
  when per-preset `cost_price` was built. **Recommendation given and built**: no "sub-preset"
  structure needed — flat presets with fractional `quantity_consumed` already model "half a
  leg" fine (same mechanism used elsewhere, e.g. fractional cabbage portions); the missing
  piece was purely cost attribution at sale time. New `Transaction.preset` FK (migration
  0133, nullable, `SET_NULL` — fourth sale-attribution discriminator alongside
  `keg_barrel`/`produce_bunch`/`kitchen_batch`) records which preset actually sold;
  `Transaction.cost()` gained a branch using `preset.cost_price` (already written by Kitchen
  Stock Receipt) when set, falling back to `item.cost_price` unchanged for every preset that
  doesn't opt in — fully backward compatible. Wired into every checkout/settlement path that
  creates a portion-item sale: `quick_sell()`, `_kitchen_checkout()`'s portion-item branch,
  `_settle_qs_from_payment()`, `_settle_kitchen_order_from_payment()`'s item_id branch,
  `confirm_prompt()`, and table-order SERVED conversion
  (`_create_transactions_for_order()`) — the last four had ALREADY resolved the `preset` for
  some other purpose (khaki counting, label text, quantity) but silently dropped it instead
  of attaching it to the `Transaction` they created; a genuine pre-existing latent gap found
  and closed in the same sweep, not just new code. **Custom-price presets**: for a small leg
  too small to split (sold whole at a variable 150–200 depending on size), added the
  convention that `price=0` on a preset means "ask staff for the amount at the point of
  sale" instead of a fixed price — deliberately a sentinel on the existing required `price`
  field rather than a new model field + checkbox in `item_form.html`'s preset table, which
  has a documented history of exactly this kind of change causing subtle breakage (multiple
  independent JS row-builders for bunch/keg/kitchen-batch/generic modes). Kitchen Board and
  Quick Sell's preset tap handlers now show "Bei Yoyote" for a price=0 preset and `prompt()`
  for the amount (never forces — cancelling adds nothing to cart); the backend already
  trusted whatever `amount` a cart entry supplied with zero cross-check against the preset's
  configured price, so this needed no backend validation change, only the frontend prompt
  and display. Audited `add_transaction.html`'s preset picker (gated on `item.is_produce`,
  which Kuku is not, so it never reaches the preset UI for this item — unaffected) and
  `waitress_screen.html` (keg items only, kitchen items never reach the Order Desk —
  unaffected) — confirmed no other selling surface needed the same guard. 16 new tests
  (`TransactionPresetCostTest`, `KitchenBoardPresetCheckoutTest`,
  `QuickSellPresetCheckoutTest`, `PresetAttributionLatentGapFixesTest` — the last locking in
  all four latent-gap fixes). One migration (0133, additive). 839 tests pass (core +
  accounts). Concrete setup given to Roy for Paja: Paja Nzima (250, qty 1.0), Paja Nusu (150,
  qty 0.5), Paja Ndogo (price 0 → custom, qty 1.0) — all addable through the existing item
  edit screen, no code change needed to configure. Leg stock backfill: both procedures he
  asked for (count-what's-left-and-receive-only-that, or receive-the-full-original-amount-
  then-Rekebisha-down) already work unmodified with existing tools (Kitchen Stock Receipt +
  Rekebisha) — no new feature needed for either path.
- Bar Board: Imekwisha button + owner-action-row overflow hardening (2026-07-29). Two live
  reports. (1) A barrel that physically kicks BEFORE reaching its revenue target had no
  direct way to close it out — `deplete_barrel()` (`/stock/bar/deplete/<id>/`) already
  existed and is the correct mechanism (closes with NO wastage transaction, unlike Tupa/
  discard which writes off the shortfall as a loss), but was only reachable indirectly, via
  a confirm dialog buried inside the sell-modal's `envelope_reached` gate — i.e. only once
  the barrel had ALREADY hit target. Added a direct "Imekwisha" button to the tapped-barrel
  action row (owner/manager only, next to Hariri/+Barrel/Tupa) calling the same endpoint
  directly — this brings KegBarrel to parity with KitchenBatch, which already has its own
  unconditional Imekwisha/Tupa pair (see the 2026-07-25 Kitchen Batch cost-correction
  entry). No backend change — `deplete_barrel()` already had no `envelope_reached`
  requirement of its own, only the JS confirm flow did. (2) Roy reported the action-button
  row overlapping on an untapped keg tile ("Hariri" + "Fungua Barrel (N sealed)"). Traced
  via a live Playwright render at a 360px viewport (device screenshot showed an itel
  A675L, 720×1600 physical): `.keg-owner-btn` uses `white-space:nowrap` with NO
  `overflow:hidden` — measured the two buttons filling their container with the two
  buttons landing at 171.6px + 149.4px = 321px inside a 326px container (zero slack).
  Confirmed the offcanvas-drawer width itself is NOT the bug on Bar Board — `#tabsDrawer`
  already uses `width:min(440px,100vw)`, correctly capped for narrow viewports. While
  auditing this, found `quick_sell.html`'s `#qsTabsDrawer` still using an uncapped
  `max-width:420px` with no 100vw guard (`kitchen_board.html`'s `#kbTabsDrawer` was already
  correct at `width:340px;max-width:95vw`) — fixed to the same `width:min(420px,100vw)`
  pattern for parity, even though it wasn't the reported symptom, since it's the exact same
  latent bug on a narrower device. With effectively zero horizontal margin, a longer sealed-count label, a slightly wider
  system font, or a marginally narrower real device pushes this over into the text
  overflowing its own flex-shrunk box and visually spilling onto the neighbouring button —
  the exact overlap Roy described. Added `overflow:hidden; text-overflow:ellipsis` to
  `.keg-owner-btn` as a safety net (never triggers in the normal case, only truncates under
  genuine space pressure instead of overlapping). Separately investigated the reported
  Tabs-drawer text-clipping on that same itel device (customer names showing "arley"
  instead of "Marley", "b #196" instead of "Tab #196") — found no CSS bug in `.tab-card`/
  `.tab-cust-name` or the drawer width (already correctly responsive); the device's status
  bar showed a very slow connection (5.57 KB/s), and `sw.js`'s HTML-navigation handler is
  network-first with a cache fallback only on a failed/timed-out fetch — the more likely
  explanation is a stale cached snapshot of the page from before an earlier tabs-drawer fix,
  served because that one device's fetch failed on poor signal, not a live code bug. No fix
  applied for that one (nothing to fix in current code); told Roy to have that device clear
  its site cache / reinstall the "Add to Home Screen" icon and reload on better signal —
  matches this app's own established pattern for single-device stale-cache symptoms (see
  the SW cache-related entries in Known Issues). No migrations (template-only change).
- Manager delegated-oversight toggles + opening-shift stock take + Recent Sales access
  (2026-07-30). Two live requests handled together. **(1) Manager toggles**: Roy wants
  petty cash review and shift-closing confirmation delegable to specific managers, not
  automatic for the whole role, with one rule: a manager's own shift close — and any other
  manager's — must always go through the owner. New `UserProfile.can_review_petty_cash` +
  `can_confirm_shifts` (accounts migration 0052, both default False). `review_petty_cash()`
  widened from strict `is_owner` to `_can_review_petty_cash(up)` (owner, or a toggled
  manager) with an explicit self-review block (`entry.recorded_by_id == request.user.id`)
  that applies regardless of the toggle — the whole point of delegated review is a second
  pair of eyes. `petty_cash_list()` widened the same way for business-wide visibility;
  `petty_cash_list.html`'s Kubali/Kataa buttons now key off the new `can_review` context var
  AND a per-row self-entry check, falling through to the existing staff self-service
  (edit/explain) block when it's the viewer's own entry. `confirm_shift()` gained
  `_can_confirm_shift(up, shift)` — owner always; a toggled manager may confirm CLOSED
  staff/waitress/kitchen shifts, but never a shift whose staff is ALSO a manager (covers
  both "their own" and "another manager's" in one check, matching Roy's literal
  instruction). `shift_history()` now computes `can_confirm`/`needs_owner` per row instead
  of the previous page-wide `is_owner_or_manager` gate; `shift_history.html`'s Thibitisha
  button is per-row now, with a "🔒 Inahitaji mmiliki" hint explaining an absent button to a
  manager rather than silent dead space. Both toggles surface in Staff Permissions only for
  `role == 'manager'`, following the same opt-in pattern as every other per-staff toggle in
  this app. **(2) Opening-shift stock take**: "Hesabu ya Stock" (the physical item-count
  form, `stock_take_api` / `ShiftStockCount`) was only ever reachable from a button inserted
  after CLOSING a shift — no equivalent existed at OPEN time. Naively reusing the same
  form/endpoint would have silently clobbered data: `ShiftStockCount` had
  `unique_together=(shift, item)`, so an opening count and a closing count for the same item
  in the same shift would overwrite each other. New `ShiftStockCount.phase`
  ('opening'/'closing', default 'closing', core migration 0134) with
  `unique_together=(shift, item, phase)` lets both coexist. Every consumer that SUMS these
  rows into a loss/variance figure — `keg_metrics.staff_shrinkage()`'s bottle loss,
  `bar_z_report`'s `day_bottle_variance_kes` — now filters `phase='closing'` explicitly,
  since those are built around "book balance vs what's left after a day's sales"; an opening
  count is a baseline with nothing sold yet and would double-count the same shift if left
  unfiltered. `_missed_tasks_for_shift`'s "did you do your stock take" reminder (fires on an
  auto-closed shift) is about the closing count specifically, same fix. `stock_take_api`'s
  POST now accepts an optional `phase` param defaulting to 'closing' — every pre-existing
  caller (the close-shift modal) keeps writing exactly what it always has, zero behavior
  change there. Bar Board and Kitchen Board's open-shift flow: instead of closing the modal
  immediately on a successful open (across all three exit paths — no tapped barrels, barrel
  weigh-in skipped, or barrel weights confirmed — now unified through one
  `_showOpenShiftDoneScreen()` helper), shows a brief "✓ Shift Imefunguliwa" screen with a
  "📦 Hesabu Stock" button (opens the same modal/JS as close-shift, `openStockTake()` now
  takes a `phase` param and relabels the modal title/intro text accordingly) alongside
  "Maliza". **(3) Recent Sales access**: added a direct "🧾 Mauzo ya Karibuni" / "Recent
  Sales" button to the Bar Board, Kitchen Board, and Quick Sell headers (in the section
  already visible to all staff, not gated to owner) linking to the existing Receipts page
  (`/receipts/` — already staff-accessible, already has a date filter) — previously only
  reachable through the hamburger nav, which staff found cumbersome to navigate to
  mid-shift. Auditing `receipts_list()` while adding this surfaced a real pre-existing
  Station Scoping Principle gap: kitchen-only staff were already correctly restricted to
  `source='kitchen'` receipts, but a bar-only staffer had no complementary exclusion and
  could see kitchen receipts too (`Receipt.source` is `'kitchen'` for kitchen sales and
  blank for bar/Quick Sell — there's no separate `'bar'` value, so "bar-only" is simply "not
  kitchen"). Fixed using the existing `_station_scope(up)` helper instead of the narrower
  ad-hoc check that was there before. One caught-and-fixed mistake during this session: an
  HTML comment added to `bar_board.html` literally contained the text `{% if is_owner %}` as
  plain English inside `<!-- -->` — Django's template parser doesn't respect HTML comment
  boundaries and read it as a real, never-closed tag, breaking every view that renders that
  template; caught by the full test suite (5 failures, `TemplateSyntaxError`) before push,
  fixed by rewording the comment to avoid literal `{% %}` syntax. 21 new tests
  (`ManagerPettyCashReviewToggleTest`, `ManagerConfirmShiftToggleTest`,
  `ShiftStockCountPhaseTest`, `OpenShiftIncludesStockTakeAccessTest`,
  `ReceiptsListStationScopingTest`). Two migrations (accounts 0052, core 0134), both
  additive. 859 tests pass (core + accounts).
- Maombi ↔ Maagizo redesign — owner-issued instructions (2026-07-30). Roy's ask: integrate
  and redesign the existing staff→owner "Maombi" request channel (Sprint "Accountability
  overhaul II," `StaffRequest`, 2026-07-26) so the owner can ALSO issue instructions to
  staff — stock takes, goods receipt, item-count confirmations — with every instruction
  type wired to a real cause-and-effect action in the app, not a disconnected to-do note;
  asymmetric framing (owner's side reads as instructions going out, staff's side reads as
  requests going up); and staff salaries/performance confirmed visible in Haki. **Design:
  one model, two directions, not a parallel system.** `StaffRequest` gained `direction`
  ('request'/'instruction', default 'request' — the original flow is direction='request'
  unchanged), `task_type` ('general'/'stock_take'/'receive_goods'/'confirm_count'),
  `assigned_to` (FK UserProfile, null=blank/broadcast to all staff), `related_item` (FK
  Item, for count/receipt instructions), `due_date` (migration 0135). Deliberately reuses
  status/reviewed_by/reviewed_at/review_note for BOTH directions instead of a parallel
  field set — 'approved' means "granted" for a request and "done" for an instruction,
  'rejected' means "declined" vs "cancelled" — only the label and the WHO-can-transition
  rule differ by direction, so one shared undo-friendly lifecycle serves both. **Cause-
  and-effect wiring**: `StaffRequest.action_url()` maps every task_type to a real,
  already-built screen instead of leaving an instruction as text: `confirm_count` →
  `/stock/?adjust_item=<id>` (the exact Rekebisha auto-open deep link the 2026-07-21
  Reset sprint's Fresh Stock Count checklist already uses), `receive_goods` →
  `/add-transaction/?item=<id>` (the same item-prefill query param the price-variance
  report already uses), `stock_take` → the assignee's own board (`/kitchen/` for kitchen
  staff, `/bar/` for a keg business, `/stock/` fallback — reusing this same session's
  opening-shift stock-take work, not a new mechanism). `general` has no deep link — just
  something to read and acknowledge. Every instruction card on the Maombi page and the new
  Kazi Yangu widget render this as a "🚀 [Fanya Sasa]" button pointing straight at the
  real action, not just a list entry. **Permission model**: `create_instruction`
  (`/staff-requests/instruct/`) is owner/manager-only. Completing an instruction
  (`action=approve`, relabeled "Nimetimiza" in the UI) is open to the specific assignee,
  ANY staff member if broadcast (assigned_to blank), or owner/manager on a staff member's
  behalf — self-declared completion, same trust model as any other staff-recorded action
  in this app. Cancelling (`action=reject`, relabeled "Futa") is owner/manager only — a
  regular staffer can complete an instruction but can never cancel one, matching Roy's
  literal framing that instructions flow one direction in authority even though the
  completion signal flows back. `review_staff_request` now branches its permission check
  on `sr.direction` while keeping the REQUEST path (owner/manager-only approve/reject)
  byte-for-byte unchanged — regression-locked by the pre-existing `StaffRequestTest`
  suite, all of which passed unmodified against the new code. **Redesigned
  `staff_requests.html`**: owner/manager see two tabs — "📤 Maagizo Niliyotoa" (given,
  default landing tab) and "📥 Maombi Niliyopokea" (received) — with "📋 Toa Agizo" as the
  prominent gold primary action and "➕ Ombi Jipya" demoted to a secondary outline button
  (kept, since a manager may still need to ask the actual owner something — Roy's
  "is_owner_or_manager" convention for this app doesn't collapse that need away). Staff
  see the mirror image — "📥 Maagizo Kwangu" (default landing tab, assigned-to-them or
  broadcast) and "📤 Maombi Yangu" — with "➕ Ombi Jipya" as the sole prominent action;
  they never see a way to issue an instruction. Each instruction card shows who it's
  assigned to (or "Wafanyakazi Wote"), an optional due date, the deep-link action button,
  and — for owners — both Nimetimiza/Futa; staff only ever see Nimetimiza on their own
  list, matching the one-directional-cancel-authority rule above. **Haki integration**:
  `my_work_and_pay` (Kazi Yangu) gained a `pending_instructions` query — the exact same
  assigned-or-broadcast filter the Maombi page's staff instructions tab uses — rendered as
  a "📋 Maagizo Kwangu" card directly under the page header, above the existing
  "📊 Mchango Wako Mwezi Huu" (revenue/hours/debts-recovered/milestones) and
  "💵 Mshahara" (salary status/payment history/deductions/remaining balance/advance
  requests) cards — both of which were already fully built (2026-07-26 "item 8" sprint)
  and needed no rework, just confirmation they're genuinely there; this creative link
  means a staffer checking "what am I owed and how am I doing" also immediately sees
  "what does the owner need from me," in one place. Learned from the 2026-07-29
  HTML-comment-breaks-template-parsing mistake (same day, earlier entry below): the new
  template was smoke-tested via `get_template()` before the full suite ran this time,
  catching any similar `{% %}`-in-comment slip before it could reach test collection. 27 new tests
  (`StaffInstructionTest`, `HakiPendingInstructionsTest`) plus the full pre-existing
  `StaffRequestTest` suite passing unmodified. One migration (0135, additive). 877 tests
  pass (core + accounts).
- Sticky list headers + store-scoped analytics navigation (2026-07-30). Two UX requests
  from Roy. **(1) Sticky headers**: rather than touch all 28 templates with a `<thead>`
  individually (high blast-radius, and this codebase's own history shows per-template
  edits are where mistakes creep in), found that `.table thead th` in `base.html` is
  ALREADY a single global rule styling every `.table` header cell app-wide — adding
  `position: sticky` there once covers stock list, receipts, transaction history, sales/
  analytics, and every other data table in the app for free, present and future, with one
  change. The correct `top` offset can't be hardcoded — `.site-header` (navbar +
  `.secondary-header`, itself already `position: sticky; top: 0`) is a different height on
  mobile (no secondary-header row, `d-none d-lg-flex`) vs desktop — so a small script at
  the bottom of `base.html` measures `.site-header.offsetHeight` on load/resize and writes
  it to a new `--sticky-top` CSS custom property, which `.table thead th` reads. Two
  deliberate opt-outs, both real edge cases found by grepping for non-page-scroll
  scenarios rather than guessed: `.modal-body .table thead th { position: static; }` (a
  table inside a Bootstrap modal scrolls within the modal, not the page — a page-relative
  sticky offset there looks broken) and a new `.table-responsive-scroll` class (added to
  the two `catalog_upload_form.html`/`catalog_upload_batch_detail.html` preview tables,
  the only tables in the app with their own fixed `max-height + overflow-y:auto` —
  `.table-responsive-scroll thead th { top: 0; }` sticks to THAT box's own top, not the
  page's, since sticky's `top` is relative to the nearest scrolling ancestor).
  `receipts_list.html` (a card list, not a `<table>` — confirmed by grep before assuming
  it needed the same treatment) got its own `.filter-bar { position: sticky; top:
  var(--sticky-top); }` instead, the closest analogue to a table header for that layout.
  **(2) Store-scoped analytics navigation**: `analytics.html` already had four
  conditionally-rendered sections — Kibanda Produce Performance (`{% if greens_items %}`),
  Bar Performance — Keg Analytics (`{% if keg_item_rows %}`), Kitchen Performance (`{% if
  kitchen_rows %}`), and a Store Performance revenue-by-store table (`{% if store_list %}`
  — this one's per-`Store` breakdown is the literal answer to "which store is making
  what") — confirmed via `analytics_views.py` that `store_list` needed zero view changes,
  it was already built and simply never surfaced as a jump target. Added an `id` anchor to
  each section-title and a `.store-jump-nav` button row right under the period filter,
  each button wrapped in the SAME `{% if %}` guard as its target section so a business
  without a bar/kitchen/produce module never sees a dead link. `.section-title` gained
  `scroll-margin-top: calc(var(--sticky-top) + 0.5rem)` so the anchor-jump (native browser
  `#anchor` + this app's existing global `html { scroll-behavior: smooth; }`) doesn't tuck
  the section title under the sticky header — no new JS needed, this app already had
  smooth-scroll enabled site-wide. Verified every template in `templates/` still parses
  (`get_template()` sweep across all `.html` files, zero errors — the lesson from the
  2026-07-29 HTML-comment mistake applied proactively this time before running the full
  suite) and `manage.py check`/`makemigrations --check` are clean. No migrations (CSS/JS +
  template-only change). 877 tests pass (core + accounts, unchanged from before this
  sprint — no test-suite-affecting code touched).
- Edit shift opening float — "staff forgot to input cash at hand" (2026-07-30). Roy's
  exact scenario: `open_shift()` silently defaults `opening_float` to `Decimal('0')` when
  nothing is typed (`request.POST.get('opening_float', '0')`), so a staffer who forgets to
  physically count and enter the counter float leaves that shift's `_reconcile()` expected-
  cash/variance math permanently wrong. Deliberately distinct from the existing opening-
  variance acknowledge flow (`review_opening_variance`, 2026-07-27) — that flow EXPLAINS why
  0 is legitimately correct (the cash was already banked elsewhere before the shift opened);
  this new one CORRECTS a number that was simply never entered. New `_can_edit_opening_float`
  +  `edit_shift_opening_float` (`core/shift_views.py`) — while a shift is OPEN, the staffer
  who opened it may fix their own entry (needs to work mid-shift without hunting down the
  owner, same reasoning as split-transfer); once CLOSED it becomes a retroactive correction
  touching figures other reports may already show, so owner/manager only from that point,
  matching every other financial-figure correction in this app; a CONFIRMED shift is treated
  as fully signed off and not editable here. `select_for_update()` + `transaction.atomic()`
  around the read-modify-write. Recomputes the derived `opening_variance` so it stays
  consistent with the corrected float, and — since any review already done (opening-variance
  OR the close-side `variance_review_status`) was necessarily based on the OLD, wrong number —
  resets such a review back to unreviewed rather than leaving a stale "✓ Imethibitishwa" stamp
  next to a figure that just changed, naming the reset in the audit line so nobody has to
  guess why a prior review disappeared. Does NOT touch `till_expected_cash()`'s running
  ledger — that anchors on `closing_cash_counted`, never `opening_float`, so correcting a past
  shift's float doesn't retroactively move the continuous till figure, only that shift's own
  report (locked in by a dedicated regression test). One shared view backs both counters via
  the same dual-URL-name mirror convention already used for every other bar/kitchen shift
  action in this file (`/bar/shift/<id>/edit-float/` and `/kitchen/shift/<id>/edit-float/`,
  both routing to the identical station-agnostic view). Audit trail appended to `Shift.notes`
  (old → new, who, when, optional reason) rather than a new model, matching the lightweight
  in-place trail pattern already used for `KitchenBatch` cost corrections; notifies whoever
  else needs to know (the shift's own staff if someone else corrected it, owners/managers if
  the staffer corrected their own entry) via in-app + SMS, mirroring every other shift-cash
  correction's recipient pattern. UI: a small ✏️ next to "Float" on the live shift panel in
  both `bar_board.html` and `kitchen_board.html` (visible to the shift's own staffer while
  OPEN, or owner/manager for any visible shift) — `prompt()` for the amount + the existing
  `openReasonChips` popover for an optional reason, never blocking on either; plus a durable
  ✏️ on `shift_history.html`'s "Float ya Kuanza" stat-box (gated per-row by the same
  `can_edit_float` context, matching that page's own existing `window.prompt()` convention
  rather than importing the chips component this page never had). 13 new tests
  (`EditShiftOpeningFloatTest`) — self-edit while open, owner/manager-any-shift, unrelated-
  staff blocked, closed-needs-owner, confirmed-not-editable, opening/closing review resets,
  no-op on unchanged value, negative/invalid amount rejected, and the till-independence
  regression lock. No migrations (uses existing `Shift.opening_float`/`opening_variance`
  fields). 890 tests pass (core + accounts).
- Notification cross-counter leak + owner spot-confirm cash (2026-07-30), same-day follow-up
  to the opening-float sprint above, three live reports. **(1) "Kitchen staff is getting bar
  notifications and vice versa, only the business owner is supposed to get them all."**
  Traced to four correction-fan-out helpers that each independently looped over EVERY
  on-shift staffer for the whole business with no station filter at all:
  `_notify_tab_correction`/`_notify_direct_correction` (`core/keg_views.py`),
  `_settle_receipt_entries_from_payment` (`core/mpesa_views.py`),
  `_notify_tab_transfer_resolved` (`core/receipt_views.py`). One correct reference
  implementation already existed — `_fire_cash_payment_request`
  (`core/receipt_views.py`, 2026-07-25) — already station-scoping its "on-shift staff" fan-out
  via `_station_scope()`. Factored that same logic into a new shared
  `core.views.scoped_on_shift_targets(business, sources)` and routed all five call sites
  through it (including `_fire_cash_payment_request` itself, de-duplicating what was there
  before). `_notify_direct_correction` gained an optional `source` kwarg (both call sites in
  `keg_views.py` already had `is_kitchen` computed right before calling it — threaded
  straight through). `_notify_tab_transfer_resolved` scopes by BOTH `source_tab.source` and
  `dest_tab.source` since a transfer can legitimately cross counters (Roy pays on his Bar tab,
  Bosco covers it from his own Kitchen tab). Owner/manager unaffected — `_station_scope()`
  always shows them both, matching "only the business owner [and manager] sees everything."
  5 new tests (`NotificationStationScopingSweepTest`), including a direct unit test of the
  new shared helper. **(2) "Ensure that once I correct [the opening float] it will adjust
  accordingly irrespective of cash sales made for that counter."** Traced and confirmed
  already mathematically true — `_reconcile()`'s `expected_cash = opening_float + cash_sales
  + ...` sums `cash_sales` over the WHOLE shift window regardless of when within it the float
  gets corrected, so the correction lands identically whether it happens before or after
  sales have accumulated. Locked in with a dedicated regression test
  (`test_correction_applies_correctly_regardless_of_sales_already_made`) proving the same
  total lands whether 3 sales happen before the correction or a 4th happens after (caught and
  fixed a real test-authoring bug while writing this: an artificially future `created_at` on
  the fixture transactions pushed them outside `_reconcile()`'s own `[started_at, now()]`
  window for a still-OPEN shift — the sale timestamps just needed to be real, not offset).
  **(3) "Ensure the owner can confirm cash at counters at any given moment for the system to
  know."** New `TillCount` model (migration 0136) — a SECOND, independent anchor source for
  `shift_views.till_expected_cash()`'s running ledger, alongside the existing shift-close
  anchor (`Shift.closing_cash_counted`). Before this, the only way to reset the continuous
  till figure was closing a shift; now the owner/manager can walk up to either counter at any
  moment, count the drawer, and establish a fresh baseline on the spot — `till_expected_cash()`
  picks whichever of (last shift close, last TillCount) is more recent as the anchor, so a
  spot confirmation immediately supersedes an older shift close, and a shift closed AFTER the
  last confirmation supersedes it in turn; everything downstream (cash sales/debt
  recovered/petty cash/banked since the anchor) was already written generically off
  `window_start` and needed zero changes. New `confirm_till_count` view (owner/manager only —
  deliberately stricter than the self-correctable `opening_float`, since a spot count is BY
  DESIGN meant to verify staff, not something staff verify themselves) at a single
  station-agnostic `/till/confirm/` endpoint (station is a POST field, not part of the URL,
  since — unlike Shift actions — this isn't tied to one board). Snapshots what the system
  expected just before the confirmation (for that row's own audit trail only, no role in any
  later calculation) and notifies station-scoped on-shift staff (via the same
  `scoped_on_shift_targets` helper from fix #1) plus owner/manager. Surfaced as a
  "💰 Thibitisha Pesa Sasa" button per station right on the home dashboard's existing till
  tiles (`home.html`) — also works as the FIRST-EVER confirmation for a station that has
  never had a shift close with a real count, bootstrapping tracking immediately instead of
  waiting on one. 9 new tests (`TillCountTest`) — bootstrap-from-nothing, supersedes-an-older-
  shift-close, later-shift-close-supersedes-an-older-confirmation, station independence,
  manager-can/staff-cannot, invalid station/negative amount rejected, and the station-scoped
  notification fan-out. One migration (0136, additive). 905 tests pass (core + accounts).
- Partial debt transfer to an existing tab, verify partial-settle-to-debt flow, revert debt
  to tab, duplicate-payment detection (2026-07-30, urgent same-day follow-up, four items).
  **(1) "Unable to transfer partial tab payment to an existing tab"** — Roy's exact case: a
  KES 225 Captain Morgan half, Roy takes it on debt (pays nothing right now), Bosco (who has
  his own running tab) covers 100, leaving Roy owing 125. `BarTabEntry.
  split_and_transfer_locked()` only ever supported two shapes — `paid_amount>0` (the SOURCE
  customer pays that much NOW, a real payment) or `paid_amount=0` (destination covers the
  WHOLE item) — neither fits "destination covers PART, the rest stays UNPAID on the source's
  own tab." New `source_kept_paid` kwarg (default `True`, fully backward compatible) selects
  between the existing `split_paid_unpaid_locked()` (marks the kept portion PAID) and new
  `split_kept_unpaid_locked()` (leaves the kept portion exactly as unpaid as it already was —
  reduced in amount only, `is_paid`/`payment_method` never touched). `TabTransferRequest.
  paid_amount` is `0` in this mode (already correctly skips the "X alishalipa mwenyewe" phrase
  in every notification that reads it, no changes needed there). `split_and_transfer_entry`
  view reads `source_kept_paid` from POST (`'0'`/`'1'`); all three tabs drawers gained a
  "Kiasi kinachobaki bado hakijalipwa (deni)" checkbox in the split-transfer modal, hiding the
  now-irrelevant cash/mpesa method picker when checked, per the tabs-drawer-parity rule. 4 new
  tests (`SplitTransferKeptUnpaidTest`) including an explicit backward-compat lock. **(2)
  "Verify the flow"** — a KES 480 running tab, customer paid 200 via mpesa (a real partial
  `settle_tab` call, splitting the boundary entry), Roy then found it in the debt tracker even
  though the receipt already correctly showed 200 paid + 280 remaining, and wasn't sure if
  that was a bug. Traced end to end: `convert_tab_to_debt()` only ever touches `tab.entries.
  filter(is_paid=False)` — the already-paid 200 portion is untouched by design — and
  `_get_customer_debt_data()`'s query already correctly reads only the unpaid 280 (`revenue()`
  on the split remainder). This is CORRECT, DESIGNED behavior for a tab that was partially
  settled and then separately converted to debt (most plausibly via a manual "→ Deni" click,
  or the shift-close auto-convert sweep, sometime after the mpesa payment) — not a bug. Locked
  in with `test_partial_settle_then_convert_then_revert_only_touches_unpaid_portion`, which
  exercises the exact real sequence (settle 200 of 480 → convert 280 to debt → revert) end to
  end through the real endpoints and asserts the paid 200 is untouched at every step. **(3) "A
  way of reverting tabs sent to debt back to the tab drawers they came from in case of a
  mistake... owner's and manager's side... all counters."** New `revert_tab_from_debt` view
  (`core/keg_views.py`) — the exact inverse of `convert_tab_to_debt`: conversion's ONLY real
  effect is setting `Transaction.recipient` on still-unpaid entries (payment_method was
  already `'credit'` on every ordinary open-tab charge from the moment it was added, see
  `KegBarrel.record_sale`'s `pay = 'credit' if tab else ...` — conversion doesn't actually
  change it) plus `tab.status`/`settled_at`; revert clears `recipient` back to blank and flips
  the tab back to `OPEN`. Owner/manager only (same tier as write-off approval), one shared
  station-agnostic endpoint covers all three counters. Refuses to revert (409) once the
  customer has already made a `CustomerDebtPayment` since the conversion — that model has no
  natural link to specific transactions, so silently un-converting at that point risks
  desyncing the FIFO ledger for potentially unrelated debts too; the owner resolves that case
  manually. New `debt_converted_tabs_api` (mirrors `_findable_tabs_qs()`'s "effective DEBT
  status" fingerprint — `status='SETTLED'` with a still-unpaid balance — the discriminator
  between "settled because everything got paid" and "settled via debt conversion") powers a
  new "↩️ Marejesho ya Deni" collapsible panel added to all three tabs drawers, owner/manager-
  only, mirroring the existing "Recent Payments" panel pattern. Syncs the tab's master receipt
  `payment_method` back to `'tab'` on revert (mirrors `_sync_master_receipt_payment_method`'s
  own 2026-07-25 fix for the forward direction). 9 new tests (`RevertTabFromDebtTest`).
  **(4) Duplicate-payment detection** — same-turn follow-up: Roy wasn't sure if the 200 above
  became "a double payment" since he separately remembered ticking it off in the tabs drawer.
  `record_debt_payment()`'s existing idempotency token only catches a resubmit of the SAME
  form load, not a staffer separately re-entering a payment already handled elsewhere.
  `CustomerDebtPayment` has no natural link back to a specific tab entry to check against, so
  the best available signal is: another payment of the SAME amount, for the SAME customer,
  within the last 24 hours. Deliberately NEVER blocks — an early draft hard-blocked a <2-minute
  match, but that directly contradicted this app's own established rule (see
  `RecordDebtPaymentIdempotencyTest.test_different_tokens_both_go_through`, already asserting
  two genuinely separate payments of the same round amount must both succeed) — caught by
  running that pre-existing test before pushing. Final version only flags: an immediate
  `messages.warning()` to the recording staffer plus an in-app + SMS notification to owner/
  manager (`_flag_possible_duplicate_debt_payment`), the payment itself always still records.
  5 new tests (`DuplicateDebtPaymentFlagTest`). No migrations for any of the four items. 923
  tests pass (core + accounts).
