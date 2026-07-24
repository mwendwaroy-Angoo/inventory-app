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
