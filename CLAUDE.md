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
- can_receive_stock: staff may record a Receipt via Add Transaction (bar/general stock
  intake + Quick Sell's "+ Pata Stok"). Default True — revoke per-staff if the owner
  doesn't trust them to log deliveries accurately. Owner/manager always exempt. Distinct
  from can_receive_kitchen_stock (kitchen board's own separate receive flow).

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
- **UBA architecture:** All post-2026-08 feature work follows `docs/UBA_MASTER_SPEC.md`
  (capability composition model) and the queue in `docs/UBA_EXECUTION_ORDER.md`. Read both
  at the start of any session that adds a feature or a business type. Current progress:
  `docs/UBA_PROGRESS.md`.
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
- UBA S1/S2/S3 (2026-08-02): Salon/Barbershop/Spa (spec §9), closing out
  Phase 3 (real `'salon'` profile registration deferred to the same
  future rollup sprint as apparel — "Salon & Barbershop" is likewise a
  live BusinessType). **S1 — Services, recipes, side-client detector**:
  confirmed `Transaction.item` is non-nullable throughout, so per the
  spec's own recommendation built the shadow-Item approach instead of an
  app-wide nullable-FK audit — `Service.shadow_item` (auto-created,
  `stock_model='SERVICE'`) is what a completed service actually posts
  against, so every existing receipt/analytics/debt/target path works
  unmodified. `complete_service_locked()` creates the shadow-item Issue
  (real revenue) plus one `type='Draw'` transaction per recipe line for
  the real supply item — reusing the EXISTING Draw precedent (built for
  KitchenBatch) avoids a real bug: a naive recipe-consumption transaction
  with no sale_amount would double-count as a second sale the moment the
  supply item also has a retail selling_price, since revenue()'s fallback
  multiplies by it — Draw already returns 0 from revenue()'s first check.
  Free redo (zero revenue, excluded from the variance denominator) needs
  no extra logic — it simply never creates the shadow-item Issue, and the
  denominator only counts services via exactly that transaction. New
  `'recipe_variance'` accountability engine — the THIRD real caller for
  the VarianceResult contract — reuses R3's `StockCountLine` as-is for
  the "actual" side rather than inventing a parallel count tracker;
  coverage_pct=0 (never accuses) when the supply was never physically
  counted. Full learned-baseline system deliberately deferred. **S2 —
  Bookings**: new `Appointment`/`AppointmentService`; double-booking
  refused via a direct overlapping-window query; no-show tracking on
  `Customer.no_show_count`; T-24h reminder SMS deferred (needs a
  scheduler, same documented limitation as every deferred-cron feature
  this session). **S3 — Commission**: deliberately reuses the EXISTING
  Haki module rather than a parallel payment mechanism — commission_
  report() reuses `_salary_period_balance()`'s "sum every SalaryPayment"
  logic, and recording a payment is literally the existing `record_
  salary_payment()` view (already sends the H2 employee SMS confirmation).
  Dedicated salon_board.html deferred. 15 new tests. 1355 tests pass.
  This completes Phase 3 in full. See `docs/UBA_PROGRESS.md`. Next:
  Phase 4 (Rentals) — L1, L2, L3.
- UBA A1/A2/A3 (2026-08-02): Apparel/boutique/mitumba (spec §8.2–§8.4),
  closing out Phase 2 (photos excluded per the standing blocker table —
  `ItemPhoto` left unbuilt, Render's ephemeral filesystem). Logged a new
  `docs/UBA_BLOCKERS.md` entry first: the real seeded `BusinessType`
  already includes "Clothing & Apparel" (a live type), so registering an
  `'apparel'` `business_profiles.py` entry now would be a real behavior
  change for existing businesses, unlike M0-AC3's inert stub — deferred
  to its own dedicated rollup sprint; every A1-A3 mechanism built as
  general/type-agnostic instead, same as all of Phase 1. **A1 —
  Variants**: `Item.parent`/`variant_label`/`variant_attrs`/
  `is_variant_parent` — parent/child Items per the spec's own explicit
  recommendation, NOT a separate `ItemVariant` table (confirmed rationale:
  a separate model would need auditing every balance reader/analytics
  query/receipt line/reorder table/Quick-Sell tile/debt line/shrinkage
  calc in the app; parent/child Items reuse 100% of existing machinery
  for free). New `core/variants.py::create_variant_matrix()` with
  collision-safe auto-SKU generation. Matrix-creator UI deferred — JSON
  endpoint only. **A2 — Bale envelope**: `ProduceBunch` gains `kind`/
  `grade`/`label` — deliberately keeps the model name (`produce_bunch_id`
  is THE discriminator, never to be broken); `realized_markup()`/
  `is_wilting()`/`sell_mix()` work completely unchanged, confirmed by a
  direct test. **A3 — Aging/markdown + fitting room** (layaway itself
  already built in P0-B): new `core/markdown_engine.py` reuses
  `ItemPriceHistory` (built in R2) with `reason='markdown'` — one model,
  two producers. New `FittingRoomLog` + a NEW `'fitting_room'`
  accountability engine — the second real caller for the contract M0-7
  deliberately left `attribute()` unbuilt for until a second engine
  needed it. Boutique-intelligence UI dashboard deferred. 16 new tests.
  1340 tests pass. This completes Phase 2 in full. See
  `docs/UBA_PROGRESS.md`. Next: Phase 3 (Salon) — S1, S2, S3.
- UBA P0-B (2026-08-02): Payment plans (layaway/deposit/instalments/
  booking), spec §6.2, first sprint of Phase 2 (Apparel). New
  `PaymentPlan`/`PaymentPlanEntry` models + `Business.layaway_forfeit_
  policy`/`layaway_forfeit_pct` (decision #5: default `minus_percent` at
  10%, never a silent full-forfeit default). Deliberately NOT the debt
  tracker — `pay_locked()` creates a `PaymentPlanEntry` and updates
  `paid_amount` but NEVER creates a revenue-bearing `Transaction`, so
  deposits correctly never appear in the deni ledger or count as revenue
  anywhere, satisfying "deposits are a liability" purely by not creating
  the one record every revenue aggregate reads. `convert_to_sale_locked()`
  is the one moment a plan becomes real recognised revenue, refusing to
  run while `balance > 0`. New `Item.reserved_qty()`/`available_balance()`
  implement "reserved stock is not available stock" — deliberately wired
  into only ONE surface (Quick Sell's checkout stock check), not swept
  across every template that shows a balance (documented deferral, same
  discipline as every prior UBA sprint), confirmed byte-identical for
  every item with zero reservations. All four inverse actions built: pay,
  refund (releases reservation), release (cancel with no money question),
  forfeit (applies the business's policy — `full_refund` closes as
  REFUNDED, others as FORFEITED). `forfeit_policy` text is SNAPSHOTTED at
  creation time and never retro-applied if the business setting changes
  later (the ethics note's own explicit requirement) — verified directly.
  New "Amana Zilizoshikiliwa" dashboard tile wired directly into home.html
  this pass (not deferred, matching X1's cash-position precedent).
  Known, documented limitation: a cash/mpesa deposit is not yet reflected
  in `till_expected_cash()`/`_reconcile()` — that function is this app's
  own documented single most money-sensitive function, so integrating a
  second cash-affecting event into it needs its own dedicated pass. Hold-
  expiry reminders + bulk management command, not auto-scheduled (same
  deferred-cron pattern). A dedicated layaway UI page deferred — JSON
  endpoints only. 17 new tests. 1324 tests pass. See
  `docs/UBA_PROGRESS.md`.
- UBA R4 (2026-08-02): Retail intelligence (spec §7.5), closing out Phase 1
  (Retail/Minimart) entirely — R1 through R4 and X1 all done. No new
  models — pure read-only report functions over existing data. New
  `core/retail_reports.py`: **dead stock report** (R4-AC1) sorted by
  capital tied up, `transfer_available` true only when the business has
  >1 active store (M2's `StockTransfer` is the real action, not wired
  into the function itself); **"Order ya leo"** reuses `Item.needs_
  reorder()`/`recommended_order_qty()` (already existed) rather than
  reimplementing reorder math, each row pre-drafted as a Swahili WhatsApp/
  SMS message ("most dukas order by phone, meet them there"); **basket
  affinity** reuses `Receipt.lines` (an existing per-sale JSONField
  snapshot) as the natural basket unit — no new grouping mechanism — top
  10 co-occurring pairs only, per the spec's own "do not build a
  recommender" instruction; **hour-of-day heatmap**, 24 fixed buckets.
  New `core/retail_reports_views.py` — 4 read-only JSON endpoints, owner/
  manager only. A dedicated retail-intelligence dashboard UI (charts) is
  deferred and documented, same discipline as `retail_board.html`/
  `payables_dashboard.html`. 7 new tests. 1307 tests pass. See
  `docs/UBA_PROGRESS.md`. Next: Phase 2 (Apparel) — P0-B, A1, A2, A3.
- UBA X1 (2026-08-02): Payables — the missing half of the cash picture
  (spec §12.1). New `SupplierInvoice`/`SupplierPayment` models —
  deliberately the mirror image of the debt tracker's `Customer`/
  `CustomerDebtPayment`, same aging bucket boundaries (current/30/60/90+),
  opposite direction. `core/payables.py::payables_aging_summary()` reuses
  the EXACT same threshold logic `debt_views.py`'s `_get_customer_debt_
  data()` already uses. `SupplierInvoice.record_payment_locked()`
  (select_for_update) flips status DUE→PARTIAL→PAID as payments
  accumulate. **Cash position tile** ("Hali Halisi ya Pesa" — Receivables
  − Payables, the spec's own "most honest number in the app"): reuses
  `debt_dashboard()`'s existing per-customer iteration for the
  receivables side. Wired directly into `home()` AND `home.html` this
  pass — unlike most prior UBA dashboard work this was NOT deferred,
  since the spec explicitly calls it out as a priority dashboard-visible
  figure and the addition is a small, self-contained stat card with no
  other page changes. New `core/payables_views.py` — owner/manager only
  (recording what the business owes suppliers is a step above an
  everyday counter action, matching the tier used for Rekebisha/petty-
  cash-review, not ordinary sales/returns). Payment due reminders go to
  the OWNER, never the supplier (spec's own explicit framing — missing a
  distributor payment is existential for a shop's credit line); new
  bulk management command, not auto-scheduled — same deferred-cron
  pattern as R1/R3/M3. A dedicated payables dashboard UI page is
  deferred (same discipline as `retail_board.html`) — JSON endpoints
  only, fully testable without it. 10 new tests. 1300 tests pass. See
  `docs/UBA_PROGRESS.md`.
- UBA R3 (2026-08-02): Cycle counting (ABC) + retail shrinkage (spec §7.4).
  New `Item.abc_class` (A/B/C) + `Item.is_high_risk` fields; new
  `StockCountSession`/`StockCountLine` models. `core/cycle_count.py::
  classify_abc_all()` ranks items by 90-day revenue. **Real bug caught by
  the test suite**: the first draft classified by cumulative-percentage
  AFTER adding each item, which wrongly put a single dominant item (96% of
  all revenue alone) into class C, since adding its own share immediately
  blew past every threshold — fixed to check the cumulative percentage
  BEFORE the item (where it STARTS in the curve, not where it ends).
  Zero-revenue items are left unclassified rather than force-set to C —
  dead stock is R4's separate concern. New `classify_items_abc`
  management command, not auto-scheduled (same deferred-cron pattern as
  R1/M3). `select_items_for_cycle_count()` builds "today's N items"
  (high-risk items first unconditionally, then ABC-due items). `record_
  count_line()` computes variance from a book_qty snapshot and — for
  attribution — reuses `shift_views.attribute_variance_shift()`
  completely unchanged, finally giving that function's `item=` parameter
  a second real caller exactly as its own docstring anticipated. Also
  stamps `Item.balance_confirmed_at` per counted line (R1's "a real
  physical count" mechanism). Deliberately scoped down and documented:
  the spec's fuller "variance weighted across every shift since last
  count" is NOT built — single-shift attribution is used as-is, flagged
  as a separate future mechanism. New `core/cycle_count_views.py`
  endpoints; a dedicated UI page deferred (same discipline as
  `retail_board.html`). 10 new tests including the classification-bug
  regression lock. 1290 tests pass. See `docs/UBA_PROGRESS.md`.
- UBA R2 (2026-08-02): Retail POS board + margin guard (spec §7.3).
  `retail_board.html` itself deferred and documented (needs visual
  verification) — Quick Sell already serves as the de-facto general POS
  for non-bar/kitchen businesses and is where both new checks are wired.
  **Margin guard** (R2-AC1): `Business.margin_alert_pct` (default 15%) +
  new `ItemPriceHistory` model. `core/retail_intelligence.py::
  check_margin_guard()` called from `add_transaction()`'s EXISTING
  "COST PRICE UPDATE (Receipt only)" block — the one designed writer of
  `Item.cost_price` — right after a cost rise beyond threshold is
  detected; suggests a new selling price preserving the OLD margin ratio,
  fires exactly one `BusinessException(kind='cost_rise')` + one owner/
  manager Notification+SMS. New `apply_suggested_price()` view is the
  one-tap "Sasisha bei" action, writing an `ItemPriceHistory` row.
  **Sale-below-cost** (R2-AC2): wired into `quick_sell()`'s checkout loop
  for plain item lines — staff blocked outright, owner allowed through
  with a warning, either way logged as `BusinessException(kind='below_cost')`.
  **Returns/refunds** (R-AC-RET, "do not skip this") — new `Return` model.
  Design decision made to avoid an app-wide sweep: rather than inventing
  `Transaction.type='Return'` and then having to widen every
  `type='Issue'`-filtered revenue aggregate throughout the app (the exact
  failure mode M2's redesign avoided in the other direction), a return
  creates TWO ordinary transactions using types/fields every existing
  aggregate already understands: a `type='Receipt'` stock reversal
  (created directly via ORM, never through `add_transaction()`'s view, so
  it can never touch `Item.cost_price`) plus a `type='Issue'`, `qty=0`
  transaction with a NEGATIVE `sale_amount` inheriting the original sale's
  payment_method/recipient. Since `Transaction.revenue()` already returns
  `sale_amount` verbatim (never `abs()`'d), this flows automatically
  through every existing `type='Issue'`-filtered revenue query with ZERO
  code changes — including the debt tracker's `_get_customer_debt_data()`,
  satisfying "if credit, the debt ledger" reversal for free (verified:
  outstanding drops from KES 400 to KES 200 on a half-return with zero
  debt_views.py changes). Known, documented limitation: `qty=0` means
  `cost()` returns 0 too, so COGS isn't reversed — net_profit stays
  slightly overstated after a return; R-AC-RET's wording doesn't require
  cost reversal, so flagged as a future refinement, not solved here.
  `Return.process_locked()` validates against double-returning more than
  was sold and rejects a wrong-business transaction id; an owner-approval
  threshold (`Business.return_approval_threshold`) gates large refunds
  into a pending state until `approve()`/`reject()`. New
  `core/returns_views.py` — a return-processing UI page is likewise
  deferred, these are JSON endpoints a future retail_board flow would
  call. 17 new tests. 1280 tests pass. See `docs/UBA_PROGRESS.md`.
- UBA R1 (2026-08-02): Fast onboarding + barcode + the shared product
  catalog (spec §7.2) — first sprint of Phase 1 (Retail/Minimart), Phase 0
  now fully complete. New cross-tenant `GlobalProduct` (barcode/name/brand/
  pack_size/unit/category, `confirm_count`, `is_verified` at >=3) and
  `MarketPriceIndex` (county-level median cost/price, `sample_size`) models.
  Two `Business` fields implement pre-answered decision #3's SPLIT exactly
  (not the spec's single illustrative flag): `contribute_market_data`
  (default True, opt-out) gates whether new barcode-scanned items feed the
  shared name/brand/pack dictionary; `contribute_price_data` (default
  False, opt-IN only) gates BOTH contributing to AND seeing the
  `MarketPriceIndex` benchmark — "opting out loses the benchmark, not just
  the contribution" as one shared boolean. `core/market_price.py`:
  `lookup_global_product()` (always allowed — shared reference data, not
  one business's figures), `record_barcode_contribution()` (increments
  `confirm_count` only the first time a GIVEN business confirms a GIVEN
  barcode — the best available guard against self-inflation, since the
  spec's schema is a plain counter, not a per-business M2M),
  `recompute_market_price_index()` (median via `statistics.median`, DELETES
  any row below sample_size 5 rather than showing a stale thin benchmark),
  `get_market_price_benchmark()` (the one read gate). New management
  command for bulk recompute, not wired to an automatic schedule — same
  documented-deferral pattern as M3's daily digest. New
  `core/barcode_views.py` — `barcode_lookup()` (read-only) +
  `add_item_by_barcode()` (owner/manager, a deliberately SEPARATE small
  endpoint rather than widening the existing ~200-line `add_item()`/
  `ItemForm` machinery, which handles produce/keg/kitchen-batch complexity
  a barcode-scanned item doesn't need) — R1-AC1's "two taps to a stocked
  item." New `Item.barcode` (not unique per-business — the same barcode
  legitimately exists at many dukas) and `Item.balance_confirmed_at`
  fields implement "Anza bila kuhesabu": a barcode-scanned item with an
  unknown opening count starts unconfirmed (excluded from shrinkage
  attribution rather than a false accusation) and gets confirmed by two
  EXISTING mechanisms reused rather than a new endpoint invented: (1)
  `adjust_stock_balance()` (Rekebisha) now stamps it on every run,
  including no-change; (2) `add_transaction()`'s Receipt branch stamps it
  only when the Receipt is the item's first-ever transaction — a Receipt
  into an item with existing history does NOT imply total on-hand is now
  known, only that this one delivery is real. `stock_list.html` shows a
  "❓ Haijahesabiwa" badge, reusing the existing Rekebisha modal
  (`?adjust_item=` deep link) as the confirm mechanism. Deliberately
  deferred and documented: the camera scan UI itself (these endpoints are
  what a future scan UI would call, fully testable via HTTP without it),
  and wiring barcode/contribute flags into item_form.html/Business
  Settings UI. 22 new tests including R1-AC1/R1-AC2 as direct regression
  locks. 1263 tests pass. See `docs/UBA_PROGRESS.md` for full detail.
- UBA M3 (2026-08-02): Maduka Yangu owner console + `BusinessException` (spec
  §5.3). New `BusinessException` model (migration `0144_businessexception`) —
  `business`/`store`/`shift`/`staff` FKs, `kind`/`severity` choices, `amount_kes`,
  `title`/`detail`/`link_url`, `acknowledged_by`/`acknowledged_at`.
  `raise_exception()` is the one write path; `acknowledge()` is idempotent.
  Additive only — every existing per-user Notification/SMS mechanism stays
  unchanged, this is the durable feed row alongside it. Wired 3 real producers:
  (1) `StockTransfer.receive_locked()`'s DISPUTED path (M2 shipped model-layer-
  only with no view calling it yet) now fires `kind='transfer_dispute'` PLUS an
  owner/manager Notification+SMS — actually closes M2-AC1 ("owner got exactly
  one notification"), never satisfied when M2 shipped. Attribution set to the
  DISPATCHER, not the receiving staffer who is only reporting the shortfall.
  (2)/(3) `close_shift()`'s existing keg-variance-danger and >KES 500 cash-
  variance alerts now also write `kind='shrinkage'`/`kind='cash_variance'`
  rows. Both needed the correct `store` resolved explicitly rather than
  trusting `Shift.store` — traced and confirmed `Shift.store` is ALWAYS
  `business.stores.first()` regardless of the actual counter (the real
  per-shift discriminator is `Shift.station`) — attaching it directly would
  have imported a known bug into a brand-new feature. New `/maduka/` route
  (`core/maduka_views.py`, strict owner-only) renders a day strip (revenue vs
  combined target, open tabs KES, credit issued today), per-store cards sorted
  problem-first (unacknowledged exception count, then how far below target —
  never alphabetical), and an exception feed with one-tap acknowledge.
  "Who's on shift"/"cash expected" only render for a bar/kitchen-station store
  — the only stores with a working Shift/till concept today; a genuine
  N-outlet retail store honestly shows revenue-vs-target only rather than
  fabricating shift data. Navbar link gated on `is_owner and
  accessible_stores_list|length > 1`, reusing M1's context processor —
  directly satisfies "single-store business sees... no Maduka link."
  Deliberately deferred and documented (same discipline as M0-5/M0-6): the
  Chart.js compare view (needs visual verification this environment can't
  provide) and the daily digest SMS (needs its own dedup field + webhook,
  substantial enough for its own follow-up pass). 24 new tests. 1241 tests
  pass. See `docs/UBA_PROGRESS.md` for full detail.
- UBA M2 (2026-08-02): stock transfers between stores — `StockTransfer`/
  `StockTransferLine` models (migration `0142_stocktransfer_stocktransferline_
  transaction_transfer`) + a new `Transaction.transfer` FK. Gap-free
  `transfer_number` per business via `select_for_update().order_by(
  '-transfer_number').first()`, same pattern as `Receipt.receipt_number`.
  Lifecycle: `create_draft_locked()` (DRAFT) → `dispatch_locked()` (DRAFT→
  DISPATCHED, deducts `from_store`) → `receive_locked()` (DISPATCHED→RECEIVED
  if quantities match, else →DISPUTED per line) → `resolve_dispute_locked()`
  (DISPUTED→RECEIVED, books the shortfall as `type='Wastage',
  invoice_no='[TRF-LOSS]'`, same `[TAG]`-suppression convention as `[ADJ]`/
  `[SVQ]`). `cancel_locked()` reverses a DISPATCHED transfer's deduction via
  compensating `[TRF-CANCEL]` transactions. **Critical mid-sprint redesign,
  caught by investigation before shipping, not by a failing test**: the first
  design used ordinary `type='Issue'`/`type='Receipt'` transactions with a
  `transfer_id` field and `if self.transfer_id: return 0` guards in
  `Transaction.revenue()`/`.cost()`. Per this file's own "audit ALL surfaces"
  rule, grepped for every OTHER raw revenue/cost aggregate in the codebase
  before calling it done — found the identical `Abs(F('qty')) *
  Coalesce(F('item__selling_price'), Value(0))`-style pattern duplicated in
  `shift_views.py`'s `_reconcile()` (the money-critical function behind every
  till/Z-report/shift-close figure), plus `haki_views.py` (×2) and
  `analytics_views.py` (×2) — none of which checked `transfer_id`. A
  transfer's `type='Issue'` dispatch leg (payment_method defaulting to
  `'cash'`, the same bug class already fixed once for split-remainder
  transactions on 2026-07-25) would have silently inflated `_reconcile()`'s
  `cash_sales`/`expected_cash` — real till corruption, not a cosmetic P&L
  issue. Redesigned to a dedicated `type='Transfer'` value (migration
  `0143_alter_transaction_type`) instead of chasing an open-ended sweep — same
  "excluded by construction, no exclusion list to maintain" pattern already
  proven by `KitchenBatch`'s `type='Draw'`. Both transfer legs now use
  `type='Transfer'`, automatically invisible to every `type='Issue'`-filtered
  query app-wide with zero new checks needed; confirmed by 2 direct
  regression tests asserting a transfer never appears in `_reconcile()`'s
  totals or analytics' revenue figures, using those functions' own query
  shapes. 14 new tests (`StockTransferTest` ×12, `StockTransferExcludedFrom
  RevenueEverywhereTest` ×2). 1227 tests pass. Model + business-logic layer
  only this pass — dispatch/receive UI, transfer-request flow, and rider/POD
  integration deferred to a follow-up, matching M1 part 1's same discipline.
  See `docs/UBA_PROGRESS.md` for the full Cause-&-Effect detail.
- Rekebisha "not a real loss" (2026-08-01), same-day follow-up. Live report
  with screenshots: Roy corrected Chrome Brandy 250ml (10→5) and Gilbey's
  (2→1) via ⚖️ Rekebisha, reversing the duplicate-receipt idempotency bug
  fixed earlier the same day — but both corrections showed up on Daily Sales
  as "⚠ Wastage — KES 2430 cost lost", reading as if real stock had spoiled
  or broken. Root cause: `adjust_stock_balance()`'s shortage branch has
  always tagged EVERY downward correction `invoice_no='[ADJ]'` and
  `type='Wastage'` uniformly, with no way to distinguish two fundamentally
  different real-world causes that both land on "physical count is lower
  than book" — (a) a genuine recount discovering REAL loss never logged
  (breakage, theft, spoilage) — this legitimately IS a real cost and must
  count; vs (b) correcting a book balance that was NEVER physically real to
  begin with, because a software bug (or a data-entry mistake) inflated it —
  no real money was ever spent on the phantom units, so it should NOT count
  as a loss. `analytics_dashboard`'s `wastage_loss` (net profit),
  `daily_sales`'s wastage tile, and `haki_views._staff_contribution`'s
  `wastage_kes` (staff accountability) all blindly summed every `[ADJ]`
  Wastage transaction with no such distinction — despite Transaction
  History's OWN badge already describing `[ADJ]` as "not a real
  delivery/loss," the three cost-facing reports never honored that framing.
  Fixed: `adjust_stock_balance()` gains an explicit `no_real_loss` checkbox
  on the Rekebisha modal (shown only for a shortage, never inferred/
  auto-detected — an owner/manager decision every time) that tags the
  correction `[ADJ-NOLOSS]` instead of plain `[ADJ]`; all three cost
  aggregates now `.exclude(invoice_no='[ADJ-NOLOSS]')` while a plain
  `[ADJ]` shortage (the genuine-loss case) still counts exactly as before —
  the stock BALANCE correction itself is byte-identical either way, only
  whether it's treated as a financial loss differs. For entries already
  recorded before this existed (Roy's actual Chrome Brandy/Gilbey's rows),
  new `toggle_adjustment_no_loss` (owner/manager only, `/stock/adjustment/
  <txn_id>/toggle-no-loss/`) flips an existing `[ADJ]`↔`[ADJ-NOLOSS]`
  Wastage transaction after the fact, reversible either direction; strictly
  scoped to `type='Wastage'` + `invoice_no in ('[ADJ]','[ADJ-NOLOSS]')` so
  it can never touch a genuine Wastage entry or unrelated transaction. New
  "sio hasara halisi?" (not a real loss?) toggle link added next to every
  `[ADJ]`-tagged Wastage row in Transaction History (owner/manager only),
  and `[ADJ-NOLOSS]` rows get their own distinct green badge. 9 new tests
  (`AdjustmentNoRealLossTest`) — checkbox tagging both ways, exclusion from
  all three cost surfaces, the genuine-loss case still counting, the
  retroactive toggle both directions, staff blocked, and the toggle
  refusing to touch an unrelated transaction. No migrations (reuses the
  existing `invoice_no` string-tag convention already established for
  `[ADJ]`/`[SVQ]`/`[KBDRAW]`).
- Shift cash/mpesa completeness audit (2026-07-31), same-day follow-up. Roy:
  "make sure that all items in the kitchen counter be it chipo smokies or
  kuku... are factored in, in the sales... what is displayed in cash and
  mpesa for in the staff shift modal up there is what is actually there
  physically... small variances unaccounted for... irregardless of the
  staff's compliance all along the app." Audited every kitchen sale
  mechanism (portion items/Kuku, KitchenBatch/Chipo, ProduceBunch/grill-
  batch items like smokies) against `shift_views._reconcile()` (the ONE
  function every live shift panel and close-shift modal in bar_board.html/
  kitchen_board.html actually reads — confirmed all three funnel through
  `active_shift_api()`/`close_shift()`, no drift between "different
  modals") — all three sale mechanisms already create ordinary `type=
  'Issue'` Transactions with `item` set, and `_reconcile()`'s own `_rev`
  Case/When expression (`sale_amount` when set, else `abs(qty) *
  item.selling_price`) already covers all of them with no per-item-type
  gap. **Found two REAL, separate completeness bugs elsewhere, matching
  "all along the app" precisely — both hand-rolled, INCOMPLETE
  reimplementations of the one correct `_reconcile()` logic, not new
  bugs in `_reconcile()` itself:** (1) `haki_views.staff_duty_log()`'s
  shift-variance figure (the Duty Log / staff-accountability report) was a
  literal inline copy ("Replicate _reconcile logic inline (no import
  needed)") missing THREE things the real reconciliation always has — no
  `item__store__is_kitchen` station scoping at all (on a combo bar+kitchen
  business, a kitchen staffer's variance here was computed against the
  WHOLE business's cash sales, bar included, and vice versa), a raw
  `Sum('sale_amount')` with no fallback to `abs(qty)*item.selling_price`
  (SQL `SUM` skips NULLs, so any Issue transaction that never set
  `sale_amount` — every plain non-preset item sale — silently contributed
  KES 0), and no petty-cash/debt-recovered/offline-sales adjustment at all.
  Same file's `txn_revenue`/`txn_by_method` aggregates had the identical
  `sale_amount`-with-no-fallback gap. Fixed by calling the real
  `_reconcile(sh)` directly (local import, matching the established
  lazy-import convention already used by `petty_cash_views.py` for the
  same function) and using `Transaction.revenue()` (the canonical
  per-instance method with the same fallback) instead of raw
  `t.sale_amount`. (2) `bar_daily_report()`'s "Staff / shift performance"
  table — documented (2026-07-08 owner-reporting-audit entry, this file)
  as covering ALL bar Issue transactions during a shift window, tab AND
  walk-up cash/mpesa alike — had the same raw `Sum('sale_amount')` with no
  fallback, silently under-counting every plain (non-preset) Quick Sell
  item sale in a staffer's own daily-report revenue figure. Fixed with the
  same `_rev` Case/When pattern `_reconcile()` uses. Swept every other
  `Sum('sale_amount')` occurrence in the codebase
  (`analytics_views.py`, `keg_metrics.py`, two other `keg_views.py` sites)
  and confirmed each is legitimately scoped to a sale type that ALWAYS
  sets `sale_amount` (bunch/keg pours) — not a bug. Also confirmed Kitchen
  Performance analytics (`analytics_views.py`) already correctly uses
  `t.revenue()`, not raw `sale_amount` — not part of this gap. 4 new tests
  (`StaffDutyLogVarianceUsesRealReconcileTest`,
  `BarDailyReportStaffRevenueFallbackTest`) — including a direct
  regression lock asserting the Duty Log's variance now equals
  `_reconcile()`'s own output exactly. No migrations.
  **Test-authoring bug found the same day, same file**: these 4 tests
  passed in isolation but failed when the full suite ran during the
  UTC/Nairobi day-straddle window (`TIME_ZONE='Africa/Nairobi'`, UTC+3 —
  roughly 21:00-24:00 UTC, when the UTC calendar day and the Nairobi
  calendar day disagree). Root cause: the tests built their `?date=`
  query param from `shift.started_at.date()` — Python's naive `.date()`
  on a UTC-stored aware datetime extracts the UTC calendar day — while
  the view's own `Shift.objects.filter(started_at__date=report_date)`
  lookup, under `USE_TZ=True`, converts to the Nairobi-configured
  `TIME_ZONE` before extracting the date for the SQL comparison; during
  the straddle window these disagree by one day, so the view found zero
  matching shifts and `next(...)` raised `StopIteration`. Same bug class
  already documented twice before in this file (`PettyCashReviewUndoTest`,
  `BarZReportOverlappingShiftsTest`) — fixed by using
  `timezone.localtime(shift.started_at).date()` instead, matching this
  project's own established convention for "today" everywhere else.
- Four-item live audit: split-payment debt bug, customer merge, similar-name
  detection, partial-payment-now/remainder-as-debt checkout (2026-07-31). Roy's
  own framing: "everything is connected... cross-check basic functionalities."
  **(1) CRITICAL — split-paid entry's Transaction.payment_method stuck on
  'credit' forever.** Live report: Hezzy's tab — KES 50 total, 40 paid via
  mpesa, 10 left owing — showed BOTH 40 AND 10 as still-owed once converted to
  debt (should only show 10). Root cause: `BarTabEntry.split_paid_unpaid_locked()`
  (the 2026-07-25 partial-tab-settle split) correctly marked the kept/paid
  portion's `BarTabEntry.is_paid=True` + `.payment_method=<real method>`, but
  NEVER updated the underlying `Transaction.payment_method` away from 'credit'
  — every tab-item Transaction starts as `payment_method='credit'`
  (`KegBarrel.record_sale`'s `pay = 'credit' if tab else ...`), and the debt
  tracker's `credit_qs` plus `shift_views._reconcile()`'s cash/mpesa/credit
  totals both read `Transaction.payment_method` directly, completely
  independent of `BarTabEntry.is_paid`. A genuinely-settled mpesa/cash payment
  therefore kept counting as unpaid debt AND kept understating the shift's real
  cash/mpesa collected — for EVERY historical split-paid tab entry across every
  business, not just Hezzy's, whether or not it was later converted to debt.
  Fixed by also setting `orig_txn.payment_method = paid_method` in
  `split_paid_unpaid_locked()` — matching the sibling non-split branch in
  `settle_entries_amount_locked()` a few lines up, which already got this
  right. New `backfill_split_paid_txn_payment_method` management command
  retroactively fixes every historical row still stuck in the broken state
  (matched via `BarTabEntry.is_paid=True` + real `payment_method` but
  `transaction.payment_method` still 'credit') — run once per deployed
  environment. **(2) Customer merge — "🔀 Unganisha na Mteja Mwingine".** Live
  report: McKenzie has both a kitchen debt and a bar debt, but his wall-QR
  receipt only ever showed the bar item — the two were recorded under two
  differently-spelled Customer records (this app's own `__iexact` matching
  only catches a pure case difference, never a genuine spelling variant like
  "Jenerali" vs "Genro" from the same report). New `Customer.merge_locked(keep_id,
  absorb_id, business)` classmethod — owner-confirmed (the owner picks the two
  records by id, not auto-detected) — reassigns every place a customer is
  referenced onto the kept identity: `Transaction.recipient`,
  `BarTab.customer`/`.customer_name`, `CustomerDebtPayment.customer` (CASCADE —
  would be destroyed by a bare delete otherwise), `Payment.debt_customer`, and
  `Receipt.customer_name` PLUS a symmetric `linked_tab_ids` union across every
  receipt either name ever received (a receipt only shows the tabs listed in
  ITS OWN meta — `core.receipt_views._receipt_all_tab_ids` — so renaming alone
  wouldn't make an already-issued receipt start showing the other counter's
  tab too; every receipt in the merged group ends up pointing at the same
  combined tab set, so it no longer matters which specific QR/PIN the customer
  has). New "🔀 Unganisha na Mteja Mwingine" button + inline search modal on
  `customer_debt_profile.html` (owner-only), new `customer_search_api`/
  `merge_customer` views at `/debt/customers/search/` and `/debt/<id>/merge/`.
  **(3) Similar-name detection widened + made actionable.** `tab_check_api`'s
  `similar_names` check used to only compare against currently-OPEN tabs — the
  moment a tab converts to debt (status leaves OPEN) it silently stopped being
  checked against, even though catching the split BEFORE it recurs is the
  whole point; now also checks every `Customer` this business has ever
  recorded. The hint itself used to be a passive, unclickable text warning
  ("majina yanayofanana... hakikisha ni mteja sahihi") — now genuinely asks
  "Je, ni mtu huyu huyu?" with a clickable name that overwrites the
  customer-name field with the exact canonical spelling and re-triggers the
  blur check, in both `bar_board.html` and `kitchen_board.html`
  (`barConfirmSameName`/`kbConfirmSameName`). A real alias like
  "Jenerali"/"Genro" still can't be auto-detected by any string-similarity
  check (no shared characters worth matching on) — that case is what the new
  merge tool is for. **(4) Partial payment now / remainder as debt, one-shot
  checkout (Bar Board + Kitchen Board).** Live request: "customer paid cash
  120... there is a remainder" / "mpesa 100 then 20 cash and there is a
  remainder" — no way to record a direct-sale checkout that's only partially
  paid, with the rest becoming debt, without 3 separate manual steps (open as
  tab, partially settle via the tabs drawer, separately convert the rest to
  debt). New `BarTab.settle_and_partial_convert_to_debt()` instance method
  deliberately CHAINS the exact same already-proven primitives — `settle_
  entries_amount_locked()` (the fixed-this-session partial-tab-settle) and a
  newly-extracted `core.keg_views._convert_tab_to_debt_core()` (factored out
  of `convert_tab_to_debt()`'s body so the new path gets the identical
  customer/SMS/notify behaviour instead of a hand-rolled duplicate) — rather
  than inventing a new payment-splitting mechanism, precisely because a new
  ad-hoc splitting mechanism is how bug (1) above was introduced in the first
  place. Wired into `bar_board()`'s keg-cart checkout and `_kitchen_checkout()`
  behind a "Sehemu inalipwa sasa — iliyobaki ni deni" checkbox shown only when
  Tab/food_tab is selected — piggybacks entirely on the existing tab-creation
  cart path, then settles+converts right after the tab has all its entries but
  before the receipt is issued (so the receipt's own debt metadata reflects
  the true post-settlement state from the start). The tab's own "just opened"
  SMS is suppressed for this path (`_convert_tab_to_debt_core` already sends
  its own "Deni limeandikwa" SMS — firing both would be confusing). Quick Sell
  deliberately NOT covered this pass (Roy's report named bar board + kitchen
  specifically) — flagged as a fast-follow if wanted. 27 new tests
  (`SplitPaidTransactionPaymentMethodSyncTest`,
  `BackfillSplitPaidTxnPaymentMethodTest`, `CustomerMergeTest`,
  `TabCheckApiSimilarNamesWidenedTest`, `PartialPaymentDebtCheckoutTest`),
  plus the full pre-existing debt/tab/split-payment/anonymous-tab test
  classes re-run and confirmed passing unmodified. No migrations.
- Fix: plain Add Transaction form could double a stock receipt on a network drop
  (2026-07-31), live report: Chrome Brandy 250ml received as 5 came in as 10, Gilbey's
  received as 1 came in as 2 — both exactly doubled — during "an internet disruption"
  while the owner was recording the delivery. Root cause: `add_transaction()`'s
  double-submit protection (`claim_checkout_token`, `core/idempotency.py`) was
  deliberately scoped ONLY to the `?quick=1` AJAX branch (Quick Sell's "+📦 Pata Stok"
  modal) — the 2026-07-19 Quick-Sell-module audit that added it reasoned the normal
  full-page form already had enough "page-reload friction" against accidental
  resubmission. That assumption doesn't hold against a connection drop mid-POST: the
  page never reloads, and many mobile browsers show their own native "resend the data
  you submitted?" prompt on reconnect — tapping through replays the exact same POST
  body, invisible to the client-side `_txnFormSubmitted` JS guard (which only stops a
  second LIVE click, not a browser-level resend, per this app's own documented
  `claim_checkout_token` contract). The clean 2x on both items is strong corroborating
  evidence for a literal duplicate submission, not a data-entry mistake. Fix: the
  `claim_checkout_token` guard in `add_transaction()` now applies to every POST through
  the view, not just the AJAX branch; `add_transaction.html` gained the same
  page-load-scoped hidden `idempotency_token` field (generated ONCE via
  `crypto.randomUUID()` at page load, not per-click — critical, since a browser resend
  must carry the SAME token as the original attempt to be caught) that every other
  protected checkout surface in this app already uses (e.g. `receive_goods.html`). A
  blank/missing token (JS disabled, stale cached page) still gets no protection rather
  than being hard-blocked, matching `claim_checkout_token()`'s documented contract.
  Immediate correction path given to Roy for the two already-doubled balances: use the
  existing "⚖️ Rekebisha" (Adjust Stock Balance) tool on Stock List — enter the real
  physical count and the system auto-creates the correct adjusting transaction
  (`invoice_no='[ADJ]'`), with zero need to touch the item form's opening stock, which
  is exactly the correction mechanism this feature was already built for
  (`core/stock_take_views.py:adjust_stock_balance`, 2026-07-13). 5 new tests
  (`AddTransactionQuickIdempotencyTest` — 3 new plain-form cases alongside the 2
  pre-existing quick=1 cases: duplicate token blocks a second Receipt, different tokens
  both go through, a blank token is never hard-blocked). No migrations.
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
- Receipt split-payment display + duplicate-payment confirmation step (2026-07-31), same-day
  live follow-up: "split payments are working well across all counters... but the receipt
  does not show the same information, like the item was paid in split-form" and "on the
  duplicate payment detection, I would like for the system to ask the user if that... is
  true or not, basically just a small confirmation just to be sure." **(1) Receipt split
  display**: `Receipt.lines`/`meta`/`payment_method` are a static snapshot taken at checkout,
  never re-read from the underlying `Transaction` rows `apply_split_payment_locked()` later
  modifies — so a split sale always showed only its primary payment method on the customer's
  receipt. New `Transaction.payment_split_breakdown(txn_ids, business)` sums revenue per
  `payment_method` across a set of txn ids, returning `{}` unless ≥2 methods are genuinely
  represented; wired into `rcpt_meta['split_payment']`/`kitchen_meta['split_payment']` at all
  three checkout call sites (`core/views.py` Quick Sell, `core/keg_views.py` Bar Board,
  `core/kitchen_views.py` Kitchen) right after the split call succeeds.
  `templates/core/receipt_public.html` gains a `{% elif receipt.meta.split_payment %}`
  branch rendering one payment badge per method+amount instead of the single default badge;
  `receipts_list.html` gets a small "✂️ Split" badge. **Real bug found and fixed while
  wiring this up**: `apply_split_payment_locked()` calls `split_payment_method_locked()` for
  any split that doesn't land exactly on a transaction boundary — which creates a NEW sibling
  `Transaction` row for the split-off remainder — but discarded that new row's id, so a
  caller feeding the ORIGINAL `txn_ids` back into `payment_split_breakdown()` would silently
  miss it. This is the boundary-split path a lone-transaction cart (one item, split payment —
  the single most common real-world case Roy described) hits every time, since there's no
  whole transaction small enough to convert outright — meaning the receipt display would have
  come back empty in the typical case. Fixed by having `apply_split_payment_locked()` return
  the full list of transaction ids that now make up the sale (original ids plus any new
  sibling row); all three checkout call sites updated to feed that return value (falling back
  to the original list when no split happened) into `payment_split_breakdown()`. **(2)
  Duplicate-payment confirmation**: the prior same-day fix only flagged a possible duplicate
  debt payment via a background notification while still recording it immediately — Roy's
  follow-up asked for an explicit human confirmation first. `record_debt_payment()`
  (`core/debt_views.py`) now STASHES the pending payment in the session
  (`request.session[f'debt_dup_pending_{customer.id}']`) instead of writing it, shows a
  warning message, and redirects — no `CustomerDebtPayment` is created and no idempotency
  token is claimed for that attempt. A new confirmation banner on
  `customer_debt_profile.html` (rendered from a `pending_dup_confirm` context var) offers
  "✓ Ndiyo, malipo mapya — Rekodi" (POSTs `confirm_duplicate=1`, nothing else — the amount/
  method/notes are read back from the trusted session stash, never from the confirm form's
  own fields, so a tampered hidden input can't record a different amount than what was
  actually flagged) and "✕ Hapana, ghairi" (`?clear_dup_confirm=1`, discards the stash,
  records nothing). New `_notify_confirmed_duplicate_debt_payment()` gives owner/manager a
  background "this was double-checked and confirmed real" trail on confirm, mirroring
  `_flag_possible_duplicate_debt_payment()`'s notification shape. **Real ordering bug found
  by the test suite**: the confirm form only ever posts `confirm_duplicate=1` + an
  idempotency token (no `amount_paid`), but the amount-parsing/validation block originally
  ran BEFORE the `confirm_duplicate` session-pop override — so confirming always failed with
  "Please enter a valid payment amount" before ever reaching the stashed values, in both the
  test suite and production. Fixed by moving the session-pop (and its `debt_source` override
  for multi-scope businesses) to run first, with an early "nothing to confirm" redirect when
  the session stash is missing/already cleared (matches the cancel path's no-op contract).
  Also fixed two purely test-authoring bugs surfaced by the same debugging pass, unrelated to
  production code: hardcoded idempotency-token literals reused across every test method in a
  class collide via Django's process-global `LocMemCache` (never reset between tests) when
  combined with SQLite's tendency to reuse rowids after a rolled-back test transaction —
  every new/updated test in this sprint now generates a fresh `uuid.uuid4()` token per call.
  27 new tests (`PaymentSplitBreakdownTest`, `ReceiptSplitPaymentDisplayTest`,
  `DebtPaymentDuplicateConfirmationTest`), plus `DuplicateDebtPaymentFlagTest`'s and
  `RecordDebtPaymentIdempotencyTest`'s existing tests updated to match the new confirm-
  required flow. No migrations. 938 tests pass (core + accounts).
- Confirmed-vs-unpaid revenue conflation + owner petty-cash self-review gap (2026-07-31),
  urgent live report with screenshots: "cash sales and mpesa for the daily sales does not
  include confirmed unpaid bills and debts, only what was confirmed... there is a huge gap",
  plus "petty cash confirmation when the owner was the one selling throughout the whole day
  ... has a shift bypass so he just sells, ensure that the petty cash deducts accordingly...
  and that the petty cash review section disappear once the owner confirms." Mid-fix, Roy
  sent a live counter-count screenshot (physical KES 2900 vs a system figure he expected to
  be slightly LOWER, not higher) confirming the second bug live in production. **Bug 1 —
  `daily_sales()` (`/daily/`) "Total Revenue" headline tile was `cash_rev + mpesa_rev +
  credit_rev`** — an unpaid tab/deni sale (stock given out, not yet collected) silently
  inflated the figure a viewer reads as "how much have we actually taken in today," with no
  visual cue it included unpaid credit. New `confirmed_rev = cash_rev + mpesa_rev` is now the
  headline ("✅ Confirmed Sales"); `credit_rev` stays visible in its own tile, relabeled
  "Credit / Deni (unpaid)" with a "stock given out — not yet collected" sub-line; a small note
  below the tiles shows the credit-inclusive grand total only when `credit_rev > 0`, explicitly
  labeled as not-yet-collected. Same conflation pattern found and fixed on every other surface
  that reads `_reconcile()`'s `total_sales` (cash+mpesa+credit) as an unlabeled headline
  figure: `shift_views._reconcile()` gained `confirmed_sales` (cash+mpesa only) alongside the
  existing `total_sales`, threaded through `active_shift_api()`'s `all_shifts_data`/proxy/
  my-shift JSON blocks and `close_shift()`'s JSON response (which was also missing
  `credit_sales` entirely — added). `home.html`'s owner-dashboard "Active Shifts" live meter
  and `stock_list.html`'s shift-running-meter widget both showed Cash + M-Pesa + an unlabeled
  gold "total" figure with **zero indication credit was baked in** (worse than bar_board.html/
  kitchen_board.html's live shift panel, which already showed a "Mikopo Mapya" line with an
  explanatory tooltip before its own Jumla) — both fixed to show `s.confirmed_sales` as the
  gold headline and add the same conditionally-shown "Deni" line with the same tooltip
  ("Bidhaa zilizotolewa kwa deni jipya — SI mauzo ya cash/mpesa"). `bar_board.html`'s and
  `kitchen_board.html`'s close-shift RESULT panel (rendered right after clicking Funga Shift)
  had the identical gap — "Mauzo Yote" showed `d.total_sales` with no credit breakdown visible
  anywhere in that panel; relabeled to "Mauzo Yaliyothibitishwa" using the new
  `d.confirmed_sales`, with a conditional "Mikopo Mapya" stat box added alongside it.
  `shift_history.html` and the live (not-yet-closed) shift status panel in bar_board.html/
  kitchen_board.html were audited and left unchanged — both already show Cash/M-Pesa/Credit as
  separate, clearly-labeled stat boxes immediately before "Jumla," so nothing was silently
  hidden there. **Bug 2 — `petty_cash_list.html` template gap blocking the owner's own
  petty-cash confirmation**: `review_petty_cash()` (`core/petty_cash_views.py`) has ALWAYS
  correctly allowed the owner to self-review their own entry — the self-review block only
  applies `if not up.is_owner and entry.recorded_by_id == request.user.id` — but the TEMPLATE
  unconditionally routed any entry where `entry.recorded_by_id == my_user_id` into the
  staff-only edit/explain branch (`{% elif entry.recorded_by_id == my_user_id %}`), with no
  approve/reject ever rendered for that case, regardless of whether the viewer was the owner.
  An owner selling solo all day (shift bypass — `get_active_staff_shift()` never gates the
  owner) who also records his own petty cash therefore had **no way through the UI to ever
  confirm his own withdrawal** — it sat `status='pending'` forever, and since
  `till_expected_cash()`'s petty_qs only ever counts `status='approved'` rows, the till stayed
  permanently inflated by exactly that amount, with the "N pending review" banner never
  clearing either. Added a new template branch (`{% elif is_owner and
  entry.recorded_by_id == my_user_id %}`) that renders the same Kubali/Kataa buttons (worded
  "✓ Thibitisha (wewe mwenyewe)" to make clear it's a self-confirmation) plus the existing
  "↺ Badilisha uamuzi" reconsider toggle — reusing the exact same `reviewEntry()`/
  `_pcApplyResult()` JS and `/petty-cash/<id>/review/` endpoint already used for reviewing
  someone else's entry, so no backend change was needed at all. Explained to Roy live: the
  physical-vs-system cash gap in his screenshot (physical 2900, system higher than expected)
  is best explained by six of Bosco's petty-cash entries (KES 310 total — Karao, Maji,
  scrubber, straws, etc.) sitting unapproved — real money that had already left the drawer but
  wasn't yet subtracted from the system's expected-cash figure; reviewing those (a completely
  separate, already-working owner-reviewing-someone-else's-entry path) should close most of
  that gap, on top of this session's self-review fix for the owner's own entries. 12 new tests
  (`OwnerSelfReviewPettyCashTillDeductionTest`, `DailySalesConfirmedRevenueTest`,
  `ShiftReconcileConfirmedSalesTest`) — template-gap regression lock, till-deduction-on-
  approval, pending-indicator-clears, reject-never-deducts, confirmed_rev/credit_rev
  separation on `/daily/`, and `confirmed_sales` threading through `_reconcile()`/
  `active_shift_api()`/`close_shift()`. No migrations. 950 tests pass (core + accounts).
- Debt-erase-mistake feature + branch reconciliation onto main (2026-07-31). Roy: "when an
  item is out on a running tab or debt section, the system should consider it as a stock
  deduction... and when the item is erased the system should append the balances
  accordingly." Confirmed already true on the tab side (any Issue transaction deducts stock
  regardless of payment method; "✕ Futa" — `remove_tab_entry` — already zeroes qty to
  restore the balance, shared identically across all three counters). Found a real gap once
  a tab converts to debt: the only correction tool there was Write-off, which deliberately
  does NOT restore stock (a real, uncollectable debt — the goods really left the shelf).
  There was no way to correct a genuinely mistaken debt-section entry (wrong item/customer)
  without permanently leaving stock wrong. Asked Roy whether the fix should be self-service
  or approval-gated; his answer: self-service by default, with an owner-activatable
  approval-gated option that can also be delegated to specific managers. **Model**:
  `WriteOffRequest.request_type` (core migration 0137, default `'writeoff'` — fully backward
  compatible with every existing write-off test) distinguishes a real Write-off from
  `'erase_mistake'`. `Business.debt_erase_requires_approval` (accounts migration 0053,
  default False = self-service) and `UserProfile.can_approve_debt_erase` (same migration,
  manager opt-in, owner always) mirror the exact pattern already established by
  `can_review_petty_cash`/`can_confirm_shifts`. **Execution**: `_execute_write_off_approval()`
  extracted from `approve_write_off`'s body as a shared core — called either immediately
  (self-service, `request_write_off` when `is_mistake=1` and approval isn't required) or from
  the approval endpoint later; for `erase_mistake` it additionally zeroes `txn.qty` (restoring
  stock, exactly like `remove_tab_entry`) and skips flagging the customer as a defaulter
  (not their fault). `reject_write_off` skips the Haki salary-deduction penalty for
  `erase_mistake` rejections — being told a flagged "mistake" was actually real debt isn't
  the same failure as trying to write off real money. `_can_approve_debt_action(up, wo)` is
  the one permission check used by both `approve_write_off`/`reject_write_off`: owner always;
  a manager only for `erase_mistake` AND only with `can_approve_debt_erase` — never for a
  real Write-off, which stays owner-only exactly as before. Self-service execution still
  shift-gates non-owner/manager staff (matching `remove_tab_entry`'s own gate) and still
  creates a `WriteOffRequest` row (immediately `status='approved'`, `reviewed_by=self`) so it
  leaves the same audit trail an approved request would, rather than a silent, untracked
  mutation. **UI**: `customer_debt_profile.html`'s write-off modal gained a "🗑 Ilikuwa Kosa"
  checkbox with a live hint (self-service vs needs-approval, driven by the business setting);
  per-row approve/reject buttons now key off a per-request `can_i_approve` flag instead of a
  blanket owner/manager check. `write_offs_pending.html` got the same per-request permission
  branching (a manager with the erase permission now sees the FINAL Idhinisha/Kataa buttons
  for `erase_mistake` cards, but still only the advisory Pendekeza buttons for real Write-offs)
  plus a distinct "🗑 Ilikuwa Kosa" badge. New toggles: Payment Settings' Sera ya Deni section
  (business-wide approval requirement) and Staff Permissions' manager-only section (per-manager
  approval delegation), both following this app's exact existing toggle conventions. 19 new
  tests (`DebtEraseMistakeTest`) covering self-service execution + stock restoration, the
  shift gate, no-defaulter-flag, approval-gated pending state, manager-permission scoping in
  both directions (can approve erase_mistake, cannot approve a real write-off), owner-always,
  and the Haki-deduction distinction on rejection — plus all pre-existing write-off tests
  confirmed passing unmodified. **Branch reconciliation**: Roy flagged that nothing from this
  session (6 commits — sticky headers, opening-float correction, cross-counter notification
  fixes, split-transfer/revert-to-tab/duplicate-detection, receipt split-payment display,
  confirmed-sales/petty-cash-self-review) had ever reached `main`, all sitting on
  `claude/laptop-screen-damage-4r21vp` instead. Fast-forward merged the branch into `main`
  (zero divergence — main had no commits the branch didn't already contain), pushed, and
  confirmed all three refs (`main`, `origin/main`, `origin/claude/laptop-screen-damage-4r21vp`)
  point to the identical commit before touching anything. Deleted the now-fully-merged branch
  locally; the remote copy could not be deleted through git push (a 403 from this session's
  proxy — an organization egress-policy block on that operation, not a bug to route around)
  — reported to Roy to remove manually on GitHub if desired, since main already has
  everything from it. All work for the rest of this session, starting with this entry, goes
  directly to `main`. Two migrations (core 0137, accounts 0053), both additive. 962 tests
  pass (core + accounts).
- Sticky-header revert + authoritative preset stock deduction + kitchen custom-price
  (2026-07-31), live urgent report with 3 screenshots. **(1) Header/row overlap "across all
  templates."** Root cause confirmed from the Daily Sales + Transaction History screenshots:
  `position: sticky` on `.table thead th` (the 2026-07-30 "sticky list headers" sprint) is a
  well-known cross-browser landmine — on the affected mobile device the `<thead>` row's
  height collapsed and its cells rendered floating on top of the first `<tbody>` row instead
  of sitting above it, corrupting every `.table` in the app at once (this one global CSS rule
  was exactly why it hit "all templates" simultaneously). Fixed by fully reverting the sticky
  behavior in `base.html` (headers scroll with the table again, as before that sprint) rather
  than chase a per-browser workaround — a broken convenience feature hiding real sales/stock
  data is worse than no sticky header. **(2) Client-trusted stock quantity on preset sales —
  root cause of the KC Ginger 250ml half-bottle discrepancy class.** Roy's screenshot showed
  system stock 0.5 bottle higher than his own physical count, tracing to exactly one tab entry
  for a half-bottle preset sale. Audited every sale-recording path that accepts a `preset_id`
  from the client and found four (of five) trusted a client-supplied stock quantity instead of
  deriving it from the preset's own `quantity_consumed` — unlike `KegBarrel.record_sale()`
  (Bar Board's keg path), which already did this correctly. Fixed all four to resolve the
  preset from the database FIRST and use `preset.quantity_consumed × cart-tap-count` as the
  authoritative deduction, client value only as a fallback when no preset applies:
  `quick_sell()` checkout (`core/views.py`), `_kitchen_checkout()`'s portion-item branch
  (`core/kitchen_views.py`), and both STK settlement callbacks `_settle_qs_from_payment()` /
  `_settle_kitchen_order_from_payment()` (`core/mpesa_views.py`) — `confirm_prompt()` was
  audited and found already correct, no change needed. **Separate real bug found in the same
  sweep**: `core/order_views.py`'s `_create_transactions_for_order(order, up)` — the function
  that converts a SERVED waitress table-order into real stock-deducting Transactions — (a) had
  the identical client-trust gap for a non-keg preset (now multiplies the ordered count by
  `preset.quantity_consumed`), and (b) referenced an undefined `request` variable in its
  `recorded_by` fallback (`order.waitress or request.user` — the function's actual signature
  is `(order, up)`, `request` was never in scope) — silently swallowed by the loop's own
  `try/except Exception: continue`, meaning a served preset-tap order line could get ZERO
  stock effect if `order.waitress` were ever falsy; fixed to `order.waitress or up.user`,
  locked in by a dedicated regression test. Framed honestly to Roy: this closes the general
  class of risk matching the reported symptom shape across every plausible code path, not a
  certain diagnosis of that one incident's exact trigger (stale browser cache vs. a client JS
  bug can't be confirmed without direct production log access). **(3) Kitchen custom-price
  sale ("✏️ Bei Maalum")** — new feature, live request: "kitchen staff can input a custom
  selling price... when the item sold does not physically meet the quantity or size needed to
  sell at set price" (e.g. a chicken leg cut smaller than usual). Deliberately separate from
  the existing `price=0` custom-price-preset sentinel (2026-07-29, Kuku cuts) — that only
  applies to a preset explicitly configured with no fixed price; this new affordance lets
  staff override the price on ANY in-stock portion tile, preset-configured or not, without
  reconfiguring the item. New small ✏️ button on the top-right corner of every in-stock
  kitchen tile (mutually exclusive with the out-of-stock 🔔 Notify button — never both at
  once), calling `customPriceTileClick(itemId)`: a single-preset or no-preset item goes
  straight to a `prompt()` for the amount; a multi-preset item opens the existing preset modal
  via a new `openPresetModal(item, forceCustomPrice=true)` parameter, which shows "Bei Yoyote"
  for every preset regardless of its own configured price and always prompts on selection
  (`addPreset()` reads and resets a new `_presetModalForceCustom` flag). No backend change
  needed at all — the cart entry this pushes (`{item_id, preset_id, description, amount,
  qty}`) is byte-identical in shape to an ordinary preset tap's, and `_kitchen_checkout()`
  already treats the submitted `amount` as `sale_amount` verbatim while re-deriving the real
  stock deduction from the preset's own `quantity_consumed` regardless of price — i.e. fix
  (2) above is exactly what makes this feature safe: the custom price can never accidentally
  perturb how much stock the sale actually deducts. 10 new tests
  (`KitchenCustomPriceTest` ×3, `AuthoritativePresetStockDeductionTest` ×7 — the latter
  covering all five fixed/audited call sites plus the NameError regression lock). No
  migrations (CSS + client-trust hardening + a client-shape-compatible frontend feature only).
  972 tests pass (core + accounts).
- Fix: paid-off debt tabs kept "coming back" to the tabs drawer (2026-07-31), live report:
  "when the debt payment is recorded... it goes back to the tabs drawer... showing that
  payment was not recorded, or as if it was not just from the debt tracker." Root-caused via
  full code trace, not guessed: `BarTab.unpaid_total()` sums `BarTabEntry.is_paid=False`
  entries — a per-LINE-ITEM flag that only flips once a payment has cumulatively covered a
  line's FULL original amount (`_do_settle_debt_payment`'s FIFO reconciliation in
  `core/debt_views.py`, correct on its own terms). A genuine PARTIAL debt-tracker payment —
  the normal case for a single-line tab — never completes any one line, so `is_paid` never
  flips, leaving the tab's own `unpaid_total()` completely unchanged at its FULL original
  amount no matter how much was actually paid. This app already closed the equivalent gap for
  the CUSTOMER-facing receipt page once before (`_get_live_tab_state`'s DEBT-status
  `outstanding` correction, reading the true `_get_customer_debt_data` aggregate instead of
  the stale entry-level sum) — but the STAFF-facing "↩️ Marejesho ya Deni" (revert-from-debt)
  panel (`debt_converted_tabs_api`/`_debt_converted_tabs_qs`, `core/keg_views.py`) was never
  given the same fix, and it's the only place in the tabs drawer a debt-converted (SETTLED-
  with-unpaid-entries) tab is ever displayed — confirmed by grep that `tabs_list()`/
  `kitchen_tabs_list()`'s main "open tabs" listings strictly filter `status='OPEN'`, so a
  SETTLED tab can never resurface there. Fixed by narrowing `_debt_converted_tabs_qs()` to
  exclude any tab with a `CustomerDebtPayment` recorded since `settled_at` — the EXACT same
  condition `revert_tab_from_debt()` already uses to refuse a revert once real money has
  landed against a conversion — so panel visibility and revert-possibility can never drift
  apart again, and a tab drops out of the panel the instant the first real payment (partial
  or full) is recorded, whether via staff-recorded cash/mpesa or an STK callback (both share
  `_do_settle_debt_payment`). Regression swept every other `.unpaid_total()` call site in the
  app (`shift_views.py`, `keg_views.py` ×6 more, `kitchen_views.py`) — all operate on `OPEN`
  tabs or compute the figure fresh right after directly modifying the entries themselves, none
  share this staleness class. 4 new tests (`DebtConvertedTabsPanelPaymentExclusionTest`) —
  partial payment drops the tab from the panel immediately (the literal live scenario), full
  payment does too, an unpaid tab is still both listed and revertible, plus all 6 pre-existing
  `RevertTabFromDebtTest` tests confirmed passing unmodified. No migrations. 976 tests pass
  (core + accounts).
- Staff permission: stock receiving (2026-07-31), live request: "we have noticed some staff
  might add transactions that do not exist when it comes to receiving stock so we need to
  leave it to the business owners to allow which staff can receive stock and which can't."
  New `UserProfile.can_receive_stock` (accounts migration 0054, default **True** — a
  deliberate departure from every other staff-permission toggle in this app, which default
  to False/off. Add Transaction's Receipt flow has been open to every staff member since the
  app's earliest days with no gate at all; defaulting this to False would have silently
  locked every current staff member at every live business out of receiving stock the moment
  this deployed. Default True preserves existing behavior everywhere until the owner
  explicitly revokes it for a specific staff member — matching the request's own framing,
  "allow which staff can... and which can't," not "block everyone until re-enabled"). Gated
  in `add_transaction()` (`core/views.py`) for `trans_type == 'Receipt'` only — Issue/
  Wastage/OwnerConsumption are completely unaffected, so a restricted staffer can still sell
  normally. One gate covers both surfaces that route through this view: the full-page Add
  Transaction form and Quick Sell's own "+ Pata Stok" `?quick=1` AJAX shortcut (returns a
  JSON 403 there instead of a redirect, matching that path's existing error-response shape).
  Owner and manager are always exempt, matching every other staff-permission gate in the
  app. New "📦 Stock Receiving Access" toggle on `staff_permissions.html`, wired through the
  existing `staff_profile.can_receive_stock = request.POST.get(...) == 'on'` pattern shared
  by every other toggle on that form. Distinct from the pre-existing `can_receive_kitchen_
  stock` (the kitchen board's own separate receive flow, already owner-controlled) — not
  touched by this change. `receive_barrel`/`receive_bunches` (bar/produce receiving) were
  confirmed already owner/manager-only, so no new gate was needed there. 7 new tests
  (`StaffReceiveStockPermissionTest`). One migration (0054, additive). 983 tests pass
  (core + accounts).
- Sticky list headers, re-fix (2026-07-31), same-day follow-up: Roy re-requested the frozen-
  header-row behavior ("the same functionality I use in Excel — headers stay with you as you
  scroll") after the earlier same-day revert of the 2026-07-30 sticky-header feature. Root-
  caused this time instead of re-adding the same code: Bootstrap's Reboot sets
  `border-collapse: collapse` on every `<table>`, and WebKit (Safari, and every iOS browser —
  all of which are Safari under the hood on iOS) has a well-documented rendering bug where
  `position: sticky` on a `<th>` is unreliable specifically under `border-collapse: collapse`
  — the sticky row's height can collapse and render on top of the first data row instead of
  above it, matching the original screenshots exactly. Fixed with the standard, documented
  workaround: `border-collapse: separate; border-spacing: 0;` on `.table` in `base.html`,
  which restores correct sticky behavior in WebKit. Confirmed this introduces no visual
  change on its own — every row/cell border rule in this app's `.table` CSS only ever sets
  `border-bottom` (never `border-top`), so there is nothing for separate borders to double
  up at row boundaries. Re-added `position: sticky` (plus a `-webkit-sticky` fallback line)
  on `.table thead th`, keeping the same `--sticky-top` JS-measured offset and the
  `.modal-body`/`.table-responsive-scroll` opt-outs from the original sprint unchanged — only
  the `border-collapse` line is new. No way to visually verify this in this environment
  (no browser); Roy needs to confirm on the same device that showed the original overlap.
  Verified 0 template parse errors. No migrations (CSS-only).
- Sticky table headers — permanently abandoned (2026-07-31, same day, third report). Roy's
  own screenshots after the `border-collapse:separate` fix showed the header STILL not
  staying pinned while scrolling, plus a new partial-row visual artifact just below it. This
  was the second distinct live failure of the same feature in one day, both attempts reasoned
  from documented CSS/WebKit behavior rather than actually seen — this environment has no
  browser. Roy explicitly offered the out ("if this thing will prove to be a challenge we
  could just revert"), and two wrong blind guesses in a row is that signal. Reverted
  `.table thead th`'s `position: sticky` and the `border-collapse: separate` change in
  `base.html` back to plain scrolling headers, with a comment explaining why this should not
  be re-attempted without real browser access or a screen recording to actually see the
  failure. The `--sticky-top` CSS var + JS measurement script were left in place (harmless,
  unused) since other code may reference the var. No migrations (CSS-only).
- Kitchen Batch "Bei Maalum" (2026-07-31, same-day follow-up): the ✏️ Bei Maalum feature
  shipped earlier the same day only covered PORTION-mode items (e.g. Kuku cuts) — Roy's
  actual example was Chipo, a `KitchenBatch` revenue-envelope item: "sometimes the fries left
  by the day's end might be so little that it cannot be sold at 100." `KitchenBatch` tiles
  had no custom-price affordance at all — only fixed preset tiles ("Ya 100") plus
  Imekwisha/Tupa/Hariri Gharama. New "✏️ Bei Maalum" button added to the batch tile's action
  row (`kitchen_board.html`, `buildKitchenBatchGrid()`), calling a new `kbBatchCustomPrice()`
  which prompts for an amount and forwards to the existing `kbBatchSell()` with `preset_id:
  null`. No backend change needed — `KitchenBatch.record_sale()` (called from
  `_kitchen_checkout()`'s `batch_id` branch) already accepts any amount with an optional
  preset purely for khaki-label bookkeeping; omitting `preset_id` was already valid. Any
  staff with an open shift can use it (a sale action, like Imekwisha/Tupa — not an
  owner-only correction like Hariri Gharama, which corrects the batch's COST rather than
  what one sale collects). 3 new tests (`KitchenBatchCustomPriceTest`) — an amount below
  every configured preset price is accepted and credited exactly (the literal reported
  scenario), no preset/khaki bookkeeping is attached to a no-preset sale, and it accumulates
  correctly alongside ordinary preset sales on the same batch. No migrations.
- Dashboard revenue survives past midnight while a station's shift is still open
  (2026-07-31/08-01), live request with a concrete example: Monsoon Inn's business closing
  time is midnight; kitchen staff had already closed their shift, but bar sales were still
  happening past midnight, and the dashboard's "today's revenue" tiles reset to 0 at plain
  calendar midnight regardless — Roy's ask: revenue should keep counting for whichever
  station is still actively selling (shift open, or the owner/manager themselves selling)
  until that station is officially signed off, "regardless of the time." Root cause:
  `home()`'s `bar_today_revenue`/`kitchen_today_revenue` tiles and
  `dashboard_revenue_api()`'s live poll (the same endpoint those tiles refresh from every
  few seconds) both filtered strictly on `date=timezone.localdate()` — a hard reset at
  00:00 Nairobi time with no awareness of whether a "trading day" for that station was
  still actually in progress. New `station_revenue_window_start(business, is_kitchen,
  now=None)` (`core/shift_views.py`, right after `_station_q()`) generalizes this with one
  simple rule: find that station's most recently STARTED `Shift` (via the existing
  `_station_q()` discriminator); if it started before today's midnight, the revenue window
  extends backward to that shift's own `started_at` — covering both "still open, sales
  should keep counting" AND "closed, but nothing NEWER has started yet, so the tile stays
  frozen at its final total instead of snapping back to 0" (Monsoon Inn's exact kitchen-vs-
  bar scenario: kitchen's shift closed before midnight with nothing new opened, so its tile
  correctly freezes; bar's shift is still open, so its tile keeps counting). If the most
  recent shift for that station started AFTER today's midnight (the ordinary case), the
  window is just today's midnight as before — zero behavior change on a normal day.
  `home()` and `dashboard_revenue_api()` both switched from `date=today` to
  `created_at__gte=<window_start>` for cash+mpesa Issue transactions per station (still
  excluding void and `[SVQ]`-tagged corrective transactions, unchanged). **Known, explicitly
  disclosed scope limit**: a station with literally ZERO `Shift` rows ever created — pure
  owner-only selling with no shift ever opened on that counter — has no shift to anchor an
  extension on, so it still resets at plain midnight; the owner/manager should open even a
  nominal shift on that counter if they want the same continuity for their own late-night
  sales. 6 new tests (`StationRevenueWindowStartTest` — no-shift fallback, open-shift-
  spanning-midnight extension, closed-shift-frozen-not-reset, new-shift-after-midnight
  reset, station independence; `HomeDashboardRevenueSurvivesMidnightTest` — end-to-end
  through `/` and `/dashboard/revenue/` for a sale recorded under a shift that started the
  prior day). No migrations.
- Dashboard revenue reset gated on shift CONFIRMATION, not midnight or a new shift opening
  (2026-08-01, same-day follow-up to the fix above). Roy's precise clarification, using the
  exact live Monsoon Inn situation: "ensure that regardless of continuity of sales and
  business closing time setting, once either section is confirmed in the shift closing
  modal the revenue resets to 0 in regards to that section... the kitchen staff closing
  shift modal was not confirmed, so the revenue should still store on that kitchen side
  until confirmed." The first version of `station_revenue_window_start()` reset the window
  the instant a NEW shift opened for a station — which meant an unconfirmed CLOSED shift's
  revenue could still be silently swallowed the moment the next shift started, well before
  anyone actually signed off on it (Thibitisha/confirm is a separate, later step from
  close — see `confirm_shift()`, `Shift.status` CLOSED→CONFIRMED). Rewrote the function's
  rule entirely: the window now anchors on that station's most recently CONFIRMED shift's
  `confirmed_at` — a NEW `Shift.confirmed_at` field (migration 0138), stamped by
  `confirm_shift()` alongside the existing `confirmed_by`/`status` flip, since the model
  previously had no timestamp for the confirm act itself, only who did it. Everything sold
  since that last real sign-off counts toward the tile, no matter how many midnights or
  shift-open/close cycles pass in between — a station left unconfirmed for several days
  keeps accruing the whole span, exactly matching "regardless of... business closing time
  setting." If a station has never had ANY shift confirmed yet, falls back to that
  station's very first shift ever (not midnight) — deliberately unbounded, since "nothing
  has been signed off yet" is the whole point; only a station with literally NO shift
  record at all (pure owner-only selling, no shift ever opened) still falls back to plain
  local midnight — the same documented scope limit as before, unchanged. Flagged to Roy as
  a known tradeoff of this design: if a business habitually skips the confirm step for a
  station for days at a time, that station's "today's revenue" tile will keep climbing
  across that whole unconfirmed span rather than resetting daily — by design, since confirm
  is now the only reset signal, but worth knowing if a station's tile ever looks
  unexpectedly large. 5 new/rewritten tests in `StationRevenueWindowStartTest`
  (`test_confirming_shift_resets_window_to_confirm_time`,
  `test_unconfirmed_shift_spans_multiple_days_regardless_of_midnight`,
  `test_most_recent_confirmation_wins_over_an_older_one`,
  `test_new_shift_without_confirming_prior_does_not_reset_window` — rewritten from the old
  "new shift resets the window" assertion to its opposite) plus 1 new test on
  `ManagerConfirmShiftToggleTest` (`test_confirm_stamps_confirmed_at`) locking in that
  `confirm_shift()` actually stamps the new field. One migration (0138, additive).
- Dashboard revenue transparency disclosure (2026-08-01, same-day follow-up, live
  screenshots). Roy saw Bar Revenue at KES 7300+ with the bar counter fully closed and "no
  sales that side" happening right now, and said he "cannot trace the cause." Traced by
  hand against Shift History: the figure was CORRECT — an auto-closed 12-hour bar shift
  (cash 1775 + mpesa 5475 ≈ 7250) sitting unconfirmed, exactly the confirm-gated behaviour
  shipped minutes earlier in this same session — but nothing on the dashboard explained
  where the number came from, so a legitimate, working figure looked alarming. Not a bug;
  a transparency gap. New `station_revenue_window_info(business, is_kitchen, now=None)`
  (`core/shift_views.py`) is the human-facing sibling of `station_revenue_window_start()` —
  same anchor rule, but also returns an `anchor_label` (who confirmed what, when, or "never
  confirmed — since first shift ever," or "since midnight — no shift yet") and a
  `pending_shifts` list of every not-yet-CONFIRMED shift within the window that's holding
  the total open. Wired into `home()` (owner/manager only, matching the till breakdown's
  existing gate) as `bar_revenue_info`/`kitchen_revenue_info`; `home.html` gains a
  disclosure block right under the "Tonight at the Bar" hero — same `<details>`/`<summary>`
  "vipi hesabu hii ilipatikana?" pattern already used for the continuous-till tile — listing
  each pending shift's staff/time/status plus a "Nenda uthibitishe →" link straight to Shift
  History. Test-authoring bug caught by the suite itself: an HTML comment explaining this
  feature was written containing the literal phrase "vipi hesabu hii ilipatikana?" in its
  prose, which — being a plain `<!-- -->` comment, not a stripped `{# #}` Django comment —
  rendered into every page regardless of the owner/manager gate, causing
  `assertNotContains` to fail for a staff viewer; reworded the comment to avoid the
  collision (same lesson as the 2026-07-30 HTML-comment-breaks-template-parsing entry, a
  different failure mode of the same root cause: don't let prose inside HTML comments
  echo strings the app or its tests treat as meaningful). 5 new tests
  (`StationRevenueWindowInfoTest`) — no-shift anchor wording, pending shift appears with
  correct staff/status, a confirmed shift never appears in the pending list, the
  end-to-end home-page disclosure renders for owner with the confirm link, and is
  completely absent for ordinary staff. No migrations.
- Manager shifts exempt from business-hours auto-close (2026-08-01, same-day follow-up).
  Roy: a manager is often the one physically still running the counter after the
  configured closing time when the owner isn't around (a bar running late, an after-hours
  event) — the auto-close sweep (`_auto_close_expired_shifts()`) is a safety net for staff
  who genuinely forgot, not a rule that should force a manager off the till the instant the
  clock says so, especially since `manager_must_have_shift=True` (2026-07-30) already blocks
  a manager from ringing up a NEW sale once their shift is gone — auto-closing them just
  means friction (forced re-open, fragmented cash float) with no real benefit. Fixed by
  excluding `staff__userprofile__role='manager'` from the sweep's queryset — a manager's
  shift now stays OPEN indefinitely past business hours until they deliberately close it
  themselves, at which point the ordinary manual-close consequences (tab-to-debt sweep,
  owner-must-confirm per the existing manager-shift-needs-owner rule) apply exactly as
  before. Staff/waitress/kitchen shifts are completely unaffected — still swept exactly as
  before; owner shifts were never affected by this friction in the first place since owners
  always bypass the "must have an open shift to sell" gate regardless. One real nuance found
  while testing: tab-to-debt conversion is STATION-scoped, not shift-scoped
  (`_convert_open_tabs_to_debt_for_shift` converts every open tab on that counter, not just
  the closing shift's own) — so a manager's open tabs stay untouched only when no OTHER
  (non-exempt) shift on the same counter is also expiring at the same sweep; if a plain
  staff shift on the same counter genuinely did expire alongside them, those tabs still
  convert exactly as they should (that staff really did forget). Roy's second ask —
  "the shift auto close balances/transactional information adjusts automatically regardless
  of the auto shift close" — was verified already true for BOTH accountability surfaces
  built earlier the same day: `till_expected_cash()`'s anchor already requires
  `closing_cash_counted__isnull=False` (an auto-close's uncounted `None` never anchors it —
  locked in by the pre-existing `TillAnchorSkipsAutoClosedShiftTest`), and
  `station_revenue_window_start()`'s anchor is purely `Shift.confirmed_at` — an auto-close
  never confirms anything, so the revenue tile keeps extending back to the auto-closed
  shift's own start regardless. New `AutoCloseRevenueContinuityTest` runs the REAL
  `_auto_close_expired_shifts()` production path end to end (not a hand-built CLOSED shift)
  to prove this concretely: both sales made during an auto-closed, unconfirmed shift still
  count on the dashboard tile, the shift correctly appears in the new
  `station_revenue_window_info()` pending list, and confirming it afterward correctly
  clears the tile. 7 new tests (`ManagerShiftExemptFromAutoCloseTest` ×4,
  `AutoCloseRevenueContinuityTest` ×3). No migrations.
- Dashboard revenue disclosure, per-shift breakdown (2026-08-01, same-day second
  follow-up, live screenshots). Roy pushed back on the first disclosure: it named
  "Shavel Atis" as the one pending kitchen shift, but that shift's own Shift History card
  showed cash 100 + mpesa 770 = KES 870, while the dashboard tile showed KES 1770 — "where
  the hell is this 1770 coming from, from 870." The disclosure listed WHICH shifts were
  pending but never showed each one's OWN revenue figure, so there was no way to check the
  tile's math against Shift History the way Roy just tried to. New `_window_revenue()`
  helper (`core/shift_views.py`) computes cash+mpesa Issue revenue for one station over an
  arbitrary `[start, end)` window, using the identical filter shape as the tile itself (so
  the total and its own breakdown can never drift apart). `station_revenue_window_info()`
  now uses it three ways: `total_revenue` (the same figure the tile shows), a `revenue`
  field on every pending shift (computed over that shift's own `[started_at, ended_at-or-
  now]`, clipped to the overall anchor window), and a new `other_revenue` bucket —
  `total_revenue` minus the sum of all listed shifts' revenue, floored at 0 — capturing
  cash/mpesa sales that happened in the window but weren't covered by any listed shift at
  all (the most common cause: the owner or an exempt manager selling directly with no
  shift open, or a genuine gap between two shifts). `home.html`'s disclosure now shows each
  pending shift's own KES figure inline (directly comparable to its Shift History card) plus
  an explicit "Mauzo mengine bila shift iliyofunguliwa" line for the remainder, and a
  "Jumla" total tying it all together. New regression test reproduces Roy's exact live
  numbers end to end: a shift with 100+770=870 in its own window, plus a 900 sale made
  after that shift ended with no shift open at all, correctly splits into
  `pending_shifts[0].revenue=870` / `other_revenue=900` / `total_revenue=1770` — proving the
  mechanism was always correct, just never shown broken down. 2 new tests
  (`test_other_revenue_bucket_captures_sales_outside_any_shift`, plus the existing
  single-shift test extended to assert the new fields), 1 existing test extended
  (`AutoCloseRevenueContinuityTest`). No migrations.
- Kitchen Performance analytics: per-preset breakdown (2026-08-01). Live scenario:
  Monsoon Inn switched chicken suppliers away from Meatco over piece-size discrepancies;
  the new supplier delivers legs-only, sold whole via a new "Legi Nzima" preset on the
  existing shared `Kuku` item (the same per-cut-preset pattern built 2026-07-29 for Bawa/
  Paja/Kifua). Roy explicitly asked to keep ONE shared item (unified stock/selling/
  reporting) while ALSO getting per-supplier accountability/profit tracking — both, wired
  logically, not a choice between them. Root gap: Kitchen Performance
  (`core/analytics_views.py`) grouped strictly by `item_id`, so every preset sold under one
  item (Meatco-costed cuts and the new supplier's legs alike) was averaged into a single
  blended "Kuku" row — silently hiding exactly the per-supplier margin discrepancy this
  business is trying to watch for, despite `Transaction.preset` (built 2026-07-28
  specifically to fix per-preset COST attribution) already carrying the data needed to
  split it. Fixed by grouping on `(item_id, preset_id)` instead: a preset-attributed sale
  now gets its own row named "Item — Preset" (e.g. "Kuku — Legi Nzima"), with its own
  units/revenue/cost/margin, completely un-blended from the item's other presets; a plain
  item sale with no `preset_id` (the vast majority of non-Kuku items) keeps the exact
  original single-row-per-item behaviour — fully backward compatible, template needed no
  changes since `row.name` was already a plain display string. 2 new tests
  (`KitchenPerformancePerPresetBreakdownTest`) — presets of the same item get separate,
  correctly-costed rows and are never blended into an averaged item-level row; a plain
  item with no presets still groups as one row. No migrations.
- Customer identity correction: search, rename, and match in one action (2026-08-01).
  Live report with screenshots — Genro (KES 800 outstanding, debt tracker) is the same
  real customer as Jenerali (a receipt from 30 Jul, "Tab imekuwa Deni"). Roy: "create a
  name search, match and correct modal somewhere in the system whereby I can search for
  Genro and edit his name to General ... and match it to Jenerali ... and consolidate the
  two, just that simple." The merge tool from 2026-07-31 (`Customer.merge_locked()`,
  `🔀 Unganisha na Mteja Mwingine`) already did the search+match+consolidate part —
  its own docstring literally cites this exact Genro/Jenerali pair as the motivating
  case — but it only reused whichever of the two existing names was "kept," couldn't
  rename to a brand-new third spelling in the same action, and was only reachable by
  first navigating to one specific customer's own profile page. Closed both gaps.
  Extracted `Customer._propagate_name_change(business, old_name, new_name)` — the
  shared name-string-rewrite engine (`Transaction.recipient`, `BarTab.customer_name`,
  `Receipt.customer_name` + symmetric `linked_tab_ids` union) previously inlined only
  inside `merge_locked()` — and built `Customer.rename_locked(customer_id, business,
  new_name)` on top of it: a standalone correction with no second record absorbed,
  raising `ValueError` on a blank name or wrong business, no-op when unchanged.
  `merge_customer()` (the view) now accepts an optional `new_name` POST field alongside
  the existing `absorb_id` — either, both, or neither (error) in one submit: merges
  first if a match is given, then renames the resulting identity if `new_name` differs.
  New standalone page `/debt/customers/correct/` (`customer_identity_correct.html`,
  owner/manager-only, linked from the Debt Tracker dashboard toolbar as
  "🔀 Sahihisha Jina la Mteja") lets Roy search-first rather than requiring pre-
  navigation to a specific profile: pick a primary customer, edit their name inline
  (prefilled, editable), optionally search and pick a second record to consolidate,
  submit once — the form POSTs to the existing `merge_customer` endpoint with its
  action set dynamically once a primary is chosen, so no new POST handler was needed.
  The existing per-profile modal (`customer_debt_profile.html`) also gained the same
  "Jina Sahihi" field for the combined action when already on a specific profile.
  8 new tests (`CustomerRenameAndCombinedCorrectionTest`) — rename propagation to
  Transaction/BarTab, blank-name and wrong-business rejection, no-op on unchanged name,
  the combined rename+merge submit (the literal live scenario), rename-only with no
  match, requiring at least one field, and the new page's owner-only gate — plus all 7
  pre-existing `CustomerMergeTest` tests confirmed passing unmodified against the
  refactored `merge_locked()`. No migrations.
- Live receipt: "total paid so far" on a running tab (2026-08-01), same-day live
  request with a screenshot (Receipt #140 — 8 already-paid Kikombe rounds struck
  through, KES 800 worth, with only the current unpaid round showing in "Jumla"). Roy:
  "I need the customer to see a total of what he has paid so far regardless of any
  additions to the running tab." The live receipt's "Jumla" figure only ever shows
  what's still unpaid RIGHT NOW (by design, for the payment flow) — nothing summed the
  cumulative amount already settled across every round added to that same tab over
  time. `public_receipt()` (`core/receipt_views.py`) now computes `total_paid_so_far`
  from `receipt.lines` (already recomputed live for an open tab via
  `_get_live_tab_state`) — sum of every `is_paid` line's subtotal — and passes it to
  the template. `receipt_public.html` renders a new "Umeshalipa Hadi Sasa" row (green,
  above the existing Jumla row) whenever it's non-zero; the live-poll JS
  (`renderLines()`, already re-rendering `Jumla`/checkboxes every 20s from
  `receipt_live_status`) now also recomputes this figure from each fresh payload and
  toggles the row's visibility — so it keeps growing correctly as more rounds get added
  and paid, exactly mirroring how the outstanding total already stays live. Purely
  additive — no changes to payment logic, existing `Jumla`/checkbox behaviour untouched.
  3 new tests (`ReceiptTotalPaidSoFarTest`) — sums only paid lines, grows correctly
  after new items are added and paid to the same tab (the literal "regardless of any
  additions" scenario), and stays zero before anything's been paid. No migrations.
- Correct which preset/cut was actually sold, already-issued receipt included (2026-08-01),
  live report with screenshots: kitchen staff mistakenly rang up "Wing" instead of "Leg" on
  the shared `Kuku` item (no wings that day, only legs — the new-supplier switch from
  2026-08-01 earlier this session) — left the item's combined balance fractional/wrong
  (27.75 pcs) since Wing's `quantity_consumed` (0.25) differs from Legi Nzima's (1). Roy:
  "the receipt to show the correct item even though it is already sold and the balance to
  adjust automatically." New `correct_transaction_preset()` (`core/keg_views.py`) —
  reassigns BOTH `Transaction.preset` AND `Transaction.qty` together, never one without the
  other: `cost()` prices a preset-attributed sale as `abs(qty) * preset.cost_price`
  (2026-07-28), so leaving qty at the old preset's `quantity_consumed` while only swapping
  preset would misprice the sale under its new cut. Fixing both together also makes "the
  balance adjusts automatically" true for free — `current_balance()` just sums
  `Transaction.qty` directly, no separate `[ADJ]` Rekebisha-style correction transaction
  needed, since nothing was ever physically missing (the piece really was sold, just
  mislabeled). Also updates the display text everywhere it's cached as a string: a live
  `BarTabEntry.description` when tab-linked, and — same precise-match-or-skip heuristic
  already established by `split_transaction_payment_method` — a same-day Receipt line for a
  direct sale, so an already-printed/shared receipt shows the correct item. "Bei Maalum"
  (custom price) suffix detection compares what was actually charged against the OLD
  preset's own configured price (a mismatch, or price=0, means custom-priced) rather than
  reading `tab_entry.description` — the original approach silently failed for BOTH direct
  AND tab sales: `Transaction` has no `tab_entry_id` shadow field for a reverse OneToOne
  (only `BarTabEntry.transaction_id` exists forward), so that attribute access always raised
  and fell through to a swallowed exception, caught by the test suite. Any staff with an
  open shift may self-correct their own mistake — same permission tier as
  `remove_tab_entry`/`revoke_entry_payment`, not owner-only, since Roy explicitly asked for
  "kitchen staff" to be able to do this themselves. Wired into Kitchen Board's existing
  "🕐 Malipo ya Hivi Karibuni" panel as a new "🔄 Kipande" button (reuses `_portionItems`,
  already loaded client-side for the tile grid, for the preset picker — no extra fetch);
  `recent_settled_tabs_api`'s direct-sales query widened from cash/mpesa-only to also
  include `credit`, since Roy's actual sale was a Kitchen Deni receipt and the panel
  couldn't surface it at all before — the existing payment-method-correction/split buttons
  stay cash/mpesa-only (a credit sale isn't "paid" yet in the sense those assume). 10 new
  tests (`TransactionPresetCorrectionTest`) — preset+qty reassignment, balance auto-adjusts,
  cost reflects the new preset, receipt line renamed (custom-price suffix preserved), tab
  entry description renamed, kitchen staff self-correct, no-shift blocked, no-op on same
  preset, cross-item preset rejected, and the widened recent-settled API. No migrations.
- Haki navbar discoverability + app-wide `business` context bug (2026-08-01). Roy asked
  whether the owner has to keep going into Business Settings to view Haki. Traced two
  layered issues. **(1)** The "🌟 Haki — Staff" navbar link did already exist in the Manage
  dropdown, but was positioned far from the Staff/Waliohama/Add Staff group — buried past
  promo tools, right before Business Settings — making it easy to never notice. Moved to
  sit directly after "Add Staff" in both navbar copies (mobile + desktop), so it's grouped
  with the rest of staff management. **(2)** While testing the move, found the real root
  cause of why it wasn't rendering at all on the home page: `base.html` gates 8 navbar link
  instances — Kazi Yangu ×6, Haki — Staff/Payroll Run ×2 — on `{% if business.haki_enabled
  %}`, but **no context processor ever supplied a top-level `business` variable** — only
  ~18 individual views across the whole app happen to pass `'business': business` in their
  own context dict (`core/context_processors.py` only ever injected `biz_profile`). Every
  other view, including `home()` — the very first page after login — rendered with
  `business` undefined, so these links silently never appeared there regardless of
  `haki_enabled`'s real value, for every business, the whole time. Fixed by extending
  `business_profile()` (the existing context processor) to also inject `business` —
  Django context processors run first and any view's own explicit `'business'` key in its
  context dict still wins, so this only fills the gap for the majority of views that don't
  set it, never overrides the ~18 that already do. **(3)** Confirmed unrelated to this
  report but already working: staff-side salary-payment confirmation ("✓ Nimepokea" on
  Kazi Yangu, `SalaryPayment.confirmed_by_staff`) already exists from the 2026-07-26
  Accountability overhaul II sprint — no new work needed there, just verified and explained
  back to Roy. 5 new tests (`HakiNavbarGroupedWithStaffTest` — link present+grouped when
  enabled, absent when disabled; `BusinessProfileContextProcessorTest` — context processor
  returns the right business for an authenticated user, `None` for anonymous, and the Kazi
  Yangu link now actually appears on the home page for staff, the literal previously-broken
  case). No migrations.
- UBA §2.1/§2.4 — capability model foundation (2026-08-02, prior session, backfilled into
  this log retroactively once the standing work order in `docs/UBA_EXECUTION_ORDER.md`
  started requiring it). `Item.stock_model` CharField (migration 0140, commit `cad33e2`) —
  the 8-value UBA stock model (UNIT/MEASURE/ENVELOPE/VARIANT/SERIAL/LOT/SERVICE/ASSET),
  synced in `Item.save()` from the existing load-bearing discriminators
  (`is_produce`/`produce_mode` → ENVELOPE/UNIT, `is_keg` → MEASURE, `is_kitchen_batch` →
  ENVELOPE) with a data migration backfilling existing rows the same way. Purely additive —
  nothing outside `save()`'s own sync block read it at the time. `Capability` composition
  registry in `business_profiles.py` (commit `96eb454`) — a frozen dataclass
  (stock_models/sale_mechanics/accountability/modules/hides/vocabulary/board_template) plus
  a `CAPABILITIES` dict mapping all 8 existing profile keys to a composition drawn from the
  UBA spec's matrix, wired into `get_profile()` as `profile['capability']`. `PROFILES`/
  `DEFAULT_PROFILE`/every catalog left untouched verbatim (confirmed via diff — insertions
  only); nothing read `profile['capability']` yet either. See `docs/UBA_PROGRESS.md` for the
  full UBA sprint log going forward.
- UBA Sprint 0 (2026-08-02): persisted the UBA master spec and standing execution order into
  the repo as `docs/UBA_MASTER_SPEC.md` / `docs/UBA_EXECUTION_ORDER.md` (verbatim `cp` +
  `diff`-confirmed, not hand-transcribed), created `docs/UBA_PROGRESS.md` and
  `docs/UBA_BLOCKERS.md`, and added the UBA pointer above this entry in Coding Preferences.
  Every future session working this queue reads those three files first instead of Roy
  re-pasting the spec.
- UBA M0-4 (2026-08-02): vocabulary layer — `core/templatetags/uba_extras.py`'s `vocab`
  filter reads `biz_profile.capability.vocabulary` (§2.4). Deviates slightly from the spec's
  illustrative `{{ 'item'|vocab }}` syntax since a plain Django filter can't read template
  context — implemented as `{{ 'item'|vocab:biz_profile }}` instead (documented in the
  filter's docstring). No-op today: every one of the 8 real profiles has an empty
  `vocabulary` dict, and nothing calls the filter yet. Also backfilled dedicated tests for
  the M0-1/M0-2 work above, which had shipped without any. 12 new tests, 1143 total, OK.
- UBA M0-3 (2026-08-02): item form field gating via `biz_profile.capability.hides`
  (`templates/core/item_form.html`). Traced the real template structure first: "Produce
  Settings"/"Keg Settings"/"Spirits Accountability"/the preset table turned out to be ONE
  single `{% if user.userprofile.is_owner %}` block spanning ~1100 lines (JS in between
  cross-references both `is_produce` and `is_keg` DOM elements freely), not three separable
  sections as the spec's prose implies — splitting it would mean inserting new if/endif
  pairs deep inside that span, judged too risky without browser access to verify; used one
  `'produce_keg_settings'` hide key for the whole thing instead. Two cleanly bounded
  single-key wraps: `'yield'` and `'restricted_items'`. All three are additive outer
  `{% if 'X' not in biz_profile.capability.hides %}` wraps around unmodified content —
  verified balanced via a Python if/endif-depth script (the produce/keg span's true close
  was 5 lines further than a naive grep suggested) and via `get_template()` parsing with no
  `TemplateSyntaxError`. `hides` is empty for all 8 real profiles today, so this is a no-op
  — locked in by 2 tests confirming every affected section still renders for a bar and a
  kibanda owner, plus a 3rd proving the mechanism itself by patching `CAPABILITIES['bar']`
  to populate `hides` and asserting the sections disappear (then revert cleanly). M0-AC3
  (stub profile proof) deferred to its own commit now that this mechanism exists. 4 new
  tests, 1147 total, OK.
- UBA M0-7 (2026-08-02): `core/accountability.py` — the §2.3 accountability-engine
  contract. Explicitly NOT a rewrite of `core/keg_metrics.py` — per the execution order's
  own instruction, that module and every bar view calling it are completely untouched; this
  is a thin, additive facade re-exporting its public names (identity-tested:
  `accountability.barrel_variance is keg_metrics.barrel_variance`, etc.). New
  `VarianceResult` dataclass matches the spec exactly. Built a real, working
  `register_engine()`/`variance_for()` registry rather than a stub — one engine,
  `'keg_shift'`, wraps `keg_metrics.shift_barrel_variance()` verbatim (no math
  reimplemented), verified byte-for-byte against calling it directly on the same fixture
  the pre-existing `LeaderboardLossAggregatedInKesTest` uses. `leaderboard()` delegates to
  `keg_metrics.staff_shrinkage()`. Deliberately left `attribute()` (the spec's illustrative
  per-result attribution function) unbuilt — with only one registered engine, a generic
  shape for it would be speculative; noted in the module docstring for whichever future
  sprint adds a second engine (produce envelope, kitchen recipe, retail cycle count) and
  actually needs it. 5 new tests, 1152 total, OK. M0-5 (dashboard tile registry) and M0-6
  (analytics section registry) remain — next up in `docs/UBA_PROGRESS.md`.
- UBA M0-5 (2026-08-02): dashboard tile registry — `core/dashboard_tiles.py`'s
  `register_tile`/`build_tiles`. Read `home()` first (~520 lines, ~30 individually-named
  context keys each in its own try/except) — migrating all of them into the registry and
  rewiring `home.html` to consume it needs visual verification this environment doesn't
  have, so deferred as a follow-up (documented in the module's own docstring). Built the
  registry itself plus two real, capability-gated example tiles: `keg_variance` (bar-only,
  via `core.accountability.leaderboard()` from M0-7) and `pending_petty_cash` (universal).
  Wired into `home()` as an additive `context['uba_dashboard_tiles']` key `home.html`
  doesn't read yet — zero visible change, confirmed by a `/` render test. Query-savings
  claim is genuinely true for these 2 tiles (a builder is never called when its capability
  requirement is unmet — locked in by a test) but doesn't yet apply to the ~30 legacy
  home() tiles. 6 new tests, 1158 total, OK.
- UBA M0-6 (2026-08-02): analytics section registry — `core/analytics_sections.py`, same
  pattern and scoping discipline as M0-5. `analytics_dashboard()` (~975 lines, ~15
  sections gated by ad-hoc presence checks rather than capability — the "existing bleed
  risk" CLAUDE.md already flagged) gets one real example section, `keg_shrinkage`
  (requires `'WEIGH_IN' in capability.accountability`), reusing
  `core.accountability.leaderboard()` rather than new business logic. Wired into
  `analytics_dashboard()` as an additive `context['uba_analytics_sections']` key
  `analytics.html` doesn't read yet. Migrating the ~15 legacy sections + rewiring the
  template is deferred as a follow-up needing visual verification. 7 new tests, 1165
  total, OK. This closes out all of Phase 0's M0 capability-refactor sub-sprints (M0-1
  through M0-7) — M1 (multi-store) is next.
- UBA M0-AC3 (2026-08-02): stub profile proof, deferred from M0-3. `business_profiles.py`
  gains `'uba_stub_salon'` in both `PROFILES` and `CAPABILITIES` (`hides={'yield',
  'produce_keg_settings'}` so M0-3's gating mechanism has something concrete to prove
  itself against). Critical check done before writing any code: grepped the real seeded
  `BusinessType` data and found a genuine `'Salon & Barbershop'` type already exists — any
  live business already using that exact name falls through to `DEFAULT_PROFILE` today;
  naively naming the stub to match it would have been a real, live regression. Used a
  deliberately non-colliding match string instead. Phase 3's real Salon profile replaces
  this stub outright when that sprint starts. 5 new tests — home dashboard + navbar render
  for the stub type, item form hides the right sections, capability composes as declared,
  and the regression lock that matters most: a business under the REAL 'Salon &
  Barbershop' type is confirmed still falling through to DEFAULT_PROFILE/DEFAULT_CAPABILITY
  unchanged. 5 new tests, 1170 total, OK. Phase 0's M0 sub-sprints and AC gate fully closed.
- UBA P0-A (2026-08-02): split tender at checkout — Kibanda's "Lipa kidogo" gap (customer
  pays part of a direct sale now, the rest becomes credit, one action, no tab). Investigated
  current state first: this app already has extensive split-payment infrastructure
  (`Transaction.apply_split_payment_locked`/`split_payment_method_locked`,
  `BarTab.settle_entries_amount_locked`) but those two are hard cash/mpesa-only, and the
  tab-based partial-to-debt flow requires a tab — Quick Sell/produce board's direct
  checkout never got this. Rather than the spec's illustrative `SalePayment` model
  (would duplicate what already works), added two `Transaction` classmethods —
  `split_to_credit_locked()` (boundary-split sibling of `split_payment_method_locked`,
  deliberately separate rather than widening that shared, narrowly-scoped function) and
  `apply_checkout_partial_credit_locked()` (mirrors `apply_split_payment_locked`'s walk).
  Neither touches `Transaction.payment_method`'s semantics — the credit remainder is an
  ordinary `payment_method='credit', recipient=name` transaction, exactly what the debt
  tracker already reads. Wired into `quick_sell()` (Quick Sell IS the produce board for
  Kibanda): new `partial_credit_amount` field, `evaluate_credit()` gated on the REMAINDER
  specifically (caught a real bug wiring this: the frontend sends the OWED amount, the
  model method's parameter is the PAID amount — fixed by converting in the view). Bar
  board/kitchen board's direct checkouts deliberately not extended this pass (Kibanda was
  the spec's own motivating example); logged as a low-risk follow-up now that the pattern
  is proven. 14 new tests, 1184 total, OK.
- UBA M1 part 1 (2026-08-02): Store as first-class outlet — model layer + access gate
  primitive. `core/models.py` Store gains `store_type/code/is_outlet/manager/is_active/
  opening_time/closing_time/target_daily_revenue/phone/address_note/latitude/longitude`
  (migration 0141). **Critical bug caught before it shipped**: the spec's own illustrative
  `Store.save()` sync is bidirectional (`store_type=='kitchen' → is_kitchen=True`, `elif
  is_kitchen and store_type!='kitchen' → is_kitchen=False`) — copied it in verbatim, then
  grepped every `is_kitchen=True` call site before testing and found
  `kitchen_views.get_or_create_kitchen_store()` creates the kitchen Store via `is_kitchen=
  True` alone, never `store_type` — the bidirectional version would have silently flipped
  it back to `is_kitchen=False` the next time anything saved it, breaking the kitchen
  module for every business. Fixed to ONE-DIRECTIONAL sync (`is_kitchen` ground truth →
  `store_type` derived), same precedent as `Item.save()`. Migration backfills
  `store_type='kitchen'` for pre-existing rows. `accounts/models.py` UserProfile gains
  `home_store`/`stores` M2M/`accessible_stores()` (migration 0056); `Business.plan` dormant
  hook (§3 decision #7). **Second deviation**: `accessible_stores()`'s no-assignment
  fallback is ALL active stores, not the spec's own `Store.objects.none()` — that version
  would lock out every staff member that exists today the instant the gate is wired in;
  access only narrows once an owner assigns someone to specific store(s) (M1-AC2). New
  `core/access.py::require_store_access(profile, store)`. 15 new tests, 1199 total, OK.
- UBA M1 part 2 (2026-08-02): session store switcher + real view wiring.
  `core.context_processors.active_store_context` reads `request.session['active_store_id']`,
  resolves against `accessible_stores()` (never trusts the session value blindly); new
  `switch_active_store()` view validates access before writing the session key. Navbar
  switcher UI deliberately deferred (no template touched, same discipline as M0-5/M0-6).
  `require_store_access()` wired into `stock_list()`'s `?store=` filter and
  `add_transaction()`'s item resolution — the two most explicitly named in the spec. 14 new
  tests are direct M1-AC1 regression locks (staffer scoped to Store A gets 403 hitting
  Store B; unassigned staff/owner unaffected). 1213 total, OK. Remaining for a future pass:
  receipts list/shift open/debt views/analytics wiring, the switcher UI, M2, M3.
- UBA L1/L2 (2026-08-02): Rentals (spec §10), Phase 4. Shadow-Item pattern (established by
  S1 for Salon services) reused for rent itself: `core/rentals.py::get_or_create_rent_
  shadow_item()` makes ONE shared shadow Item per business that every `RentalInvoice`'s
  rent transaction posts against, so receipts/analytics/revenue-targets all work
  unmodified. New `RentalUnit` (with `committed_qty()`/`available_qty()` — supports
  multi-quantity equipment units without overbooking), `RentalAgreement`, `RentalInvoice`
  (`unique_together=('agreement','period_start')` is the idempotency guarantee itself),
  `MeterReading`, `MaintenanceTicket`. `generate_rent_roll()` creates one ordinary
  `payment_method='credit'` Transaction per invoice — arrears are just the EXISTING debt
  tracker's FIFO (`sync_invoice_payment_status()`/`apply_rent_payment_by_unit_code()` read
  and write through `debt_views`'s own aggregate, zero new aging logic). M-Pesa C2B
  `bill_ref_number` matched against `RentalUnit.code` in `mpesa_views.c2b_confirmation()`,
  checked before the generic bar/kitchen fallback; an unmatched paybill payment raises a
  `BusinessException` for owner visibility rather than being silently dropped, per this
  app's own "money must never vanish" standard. New `'caretaker'` role (accounts migration
  0060) may record meter readings/report maintenance but never alter agreements or close a
  maintenance ticket with a cost (owner/manager only, same tier as every other financial
  correction) — both directions regression-tested. Deposit deductions
  (`deduct_from_deposit()`, owner/manager only, itemised+reasoned) never create a
  Transaction, matching P0-B's "deposits are a liability" principle exactly. Dedicated
  `rental_board.html` (L3) deferred — every mechanism is a tested JSON endpoint today. 16
  new tests. 1371 total, OK. **Phase 4 (Rentals) L1/L2 complete.** Next: Phase 5 (Supply
  Chain) — X2 (Goods Received Note), X3 (rider POD/COD).
- Ad-hoc expense recording + tethered-preset receive-picker leak fix (2026-08-09). Live
  request: "can I record expenses for a certain day" — confirmed with Roy this must be
  station-scoped, backdatable, and NEVER touch today's expected drawer cash (bookkeeping
  only, not a till-affecting event like PettyCash). New `BusinessExpense.station`/
  `recorded_by` (migration 0158, additive); new `record_ad_hoc_expense`/
  `expense_day_total_api` (owner/manager only, idempotency-guarded via
  `claim_checkout_token`) in `core/recurring_expense_views.py`; new shared
  `expense_modal.html`/`expense_js.html` partials (same convention as
  `petty_cash_modal.html`/`petty_cash_js.html`), wired into Bar Board and Kitchen Board
  with a "💸 Matumizi ya Leo" readout + button, owner-only. Deliberately never read by
  `till_expected_cash()`/`_reconcile()` — locked in by
  `test_ad_hoc_expense_never_affects_till_expected_cash`. Roy's own follow-up message
  ("it should reconcile the relevant cash entries for that specific day inclusive of
  shifts data for that day but it should not touch the day it is been recorded if it is
  not for that day") appears to ask for MORE than pure bookkeeping — that a backdated
  entry should also factor into THAT PAST day's own historical shift/day reconciliation
  report, not just Expense Intelligence — a real nuance beyond what's built here; flagged
  to Roy for confirmation before touching `till_expected_cash()`/`_reconcile()` further,
  since those are this app's own documented most money-sensitive functions.
  Same session, separate live report: after receiving "Full Chicken Leg" via a
  preset-attributed Kitchen Stock Receipt line, Roy could not sell the Kuku tile — traced
  and fixed (real bug: `tileClick()`'s single-preset branch in kitchen_board.html never
  handled the price=0 custom-price sentinel). Roy's own follow-up, mid-fix, caught a
  second real bug from a screenshot of the "+Pata Stok" receive modal: "Half Chicken Leg"
  (tethered to "Full Chicken Leg" via `tracks_stock_of`) was still offered as its own line
  in TWO receive-style pickers — the "+Pata Stok" "Chagua Kipande" step AND the separate
  Kitchen Stock Receipt modal's "Chagua Bidhaa" step. A tethered preset has no independent
  physical delivery to receive against — picking it would silently misattribute the
  line's cost onto the tethered preset instead of its anchor. Fixed both pickers to source
  options from `all_presets` (already excludes tethered presets in the
  `portion_items` view builder, `core/kitchen_views.py`); Kitchen Stock Receipt's create
  endpoint also gained a server-side defensive resolve-to-anchor step
  (`preset.tracks_stock_of` fallback) for a stale cached client that still submits a
  tethered preset id. Tethered presets remain fully sellable on the ordinary tile grid —
  only the two receive pickers are affected. 15 new tests (`AdHocExpenseTest` ×14,
  `test_stock_receipt_create_resolves_tethered_preset_to_anchor` ×1 on
  `PresetStockTrackingTetherTest`). One migration (0158, additive). 1576 tests pass (core
  + accounts).
- Ad-hoc expense day-specific reconciliation (2026-08-09, same-day follow-up). Roy's
  explicit confirmation, asked directly: a backdated ad-hoc expense should reconcile
  against "both shift history and z report" for the SPECIFIC DAY it's dated for
  (inclusive of that day's shift data), never the day it happens to be recorded on if
  different, and never `till_expected_cash()` (the continuous "right now" dashboard tile,
  which stays untouched by design per the original sprint entry above). New
  `shift_views._ad_hoc_expense_total_for_shift(shift)` sums same-day, station-scoped
  `BusinessExpense` rows for a shift's own active window (`_shift_active_segments()`).
  Deliberately NOT folded into `_reconcile()` itself — that function also backs the LIVE
  in-progress shift panel (`active_shift_api`) and the moment-of-close comparison
  (`close_shift`), which must keep showing exactly the figure staff physically compared
  their count against at the time; folding it in there would have silently moved a number
  Roy explicitly said must never move. Instead wired in additively, at DISPLAY time, only
  in the two named surfaces: `shift_history()` (each row gains
  `ad_hoc_expenses`/`expected_cash_after_expenses`/`variance_after_expenses`; the
  colour-coded variance badge now reflects the after-expenses figure — equal to the
  original whenever no same-day expense exists — matching this app's own established
  precedent that a later correction should self-correct the live standing record, not stay
  frozen at close time, same as the petty-cash-review-undo mechanism) and `bar_z_report()`
  (same per-shift fields, plus a single DEDUPED day-level query — `report_date`,
  `station='bar'` — rather than summing per overlapping shift, mirroring the existing
  day_cash/day_mpesa dedup fix for two staff sharing one till). Regression-locked: a
  same-day expense leaves the live shift panel and close-time comparison completely
  unadjusted (`test_live_shift_panel_and_close_shift_unaffected_by_same_day_expense`); a
  backdated expense only shows up in Shift History/Z-report rows for its own date, never
  today's; the day-level Z-report total stays deduped across overlapping shifts. No new
  migrations. 9 new tests (`AdHocExpenseDayReconciliationTest`). 1585 tests pass (core +
  accounts).
- Ad-hoc expense edit/recover + Kitchen "Leo" credit conflation fix (2026-08-09, same-day
  follow-up). **(1)** Roy: "can i recover a counter cash entry placed on a wrong date
  mistakenly" — clarified via `AskUserQuestion` to mean the Matumizi/ad-hoc expense tool
  specifically (not petty cash). No edit path existed for an already-recorded
  `BusinessExpense` — only create. New `edit_ad_hoc_expense()`/`ad_hoc_expenses_list()`
  (`core/recurring_expense_views.py`, owner/manager only, matching the record permission
  tier) let an entry be found and corrected — most often its date. Answered Roy's
  follow-up question directly: no separate "recompute" step is needed after correcting the
  date — `shift_views._ad_hoc_expense_total_for_shift()` and the Z-report's day-level query
  both read `BusinessExpense` fresh on every render (see the entry immediately above), so
  moving an entry's date makes it disappear from the wrong day's Shift History/Z-report row
  and appear on the correct day's the very next time either is opened, automatically. New
  "📋 Historia ya Matumizi" collapsible panel (`templates/core/expense_history_panel.html`,
  same collapsible-panel convention as the "🕐 Malipo ya Hivi Karibuni" Recent Payments
  panel) added to both Bar Board and Kitchen Board — date picker + list + "✏️ Hariri" per
  entry, reusing the existing `expenseModal` in a new edit mode (`window.openExpenseModal
  (expense)` prefills and retitles the modal; `_submitExpense()` branches POST target/
  method on whether `window._expEditingId` is set). A future/invalid date submitted on
  edit falls back to the entry's OWN existing date, not today (deliberately different from
  `record_ad_hoc_expense()`'s own today-fallback) — an edit that only touches amount/
  description must never silently move an otherwise-correct date. **(2)** Same-day live
  report (Roy, Monsoon Inn, with screenshot): Kitchen Board's "🍽 Leo" header tile showed
  KES 2550 while the currently-open shift's own Cash Sales/M-Pesa were both KES 0 — "i have
  not [rung/confirmed] today's entries so that amount is inaccurate... the same applies to
  recent sales and receipts, since it affects all of them." Traced (not guessed) to
  `kitchen_board()`'s `kitchen_revenue_today` (and its live-poll sibling
  `kitchen_stats_api`) blending `payment_method__in=['cash','mpesa','credit']` into ONE
  number with no distinction — the exact "confirmed vs unpaid revenue" conflation bug
  already found and fixed on 2026-07-31 for `daily_sales()`/`home()`/`stock_list.html`/the
  close-shift result panel, but never extended to this specific tile — a genuinely separate
  code path that never calls `_reconcile()`, so the earlier fix's own sweep missed it.
  Verified Receipts list (`receipts_list.html`) already correctly badges a `payment_method
  ='credit'` receipt as "Credit" (not "Cash") from the 2026-07-25 sprint, and `daily_sales()`
  was already fixed — so "it affects all of them" was really one shared root cause (Kitchen
  Board's own header tile) making an otherwise-correct picture look inconsistent, not a
  spreading bug. Also ruled out (verified directly, not assumed) a `Transaction.date` UTC/
  Nairobi day-boundary bug — confirmed `DateField.get_prep_value()` already correctly
  resolves an aware `timezone.now()` default to the Nairobi-local calendar day via Django's
  own `to_python()`, matching `timezone.localdate()` exactly. Fixed: `kitchen_revenue_today`
  is now confirmed (cash+mpesa) only, with `kitchen_revenue_credit` split out separately;
  `kitchen_stats_api` returns both `revenue_today`/`revenue_credit` for its live poll.
  Kitchen Board shows "+ Deni: KES X" as its own badge (`kb-revenue-credit-badge`), hidden
  when zero, using the same "Mikopo Mapya" title-tooltip wording this page's own shift panel
  already established — never silently folded into "Leo" itself. Bar Board audited and
  confirmed it has no equivalent server-computed "today revenue" header tile to need the
  same fix. 15 new tests (`AdHocExpenseEditRecoverTest` ×10,
  `KitchenBoardRevenueConfirmedCreditSplitTest` ×6 — one test file addition covers both). No
  migrations. 1601 tests pass (core + accounts).
- Kitchen Board "Leo" tile, second gap: `[SVQ]` exclusion (2026-08-09, same-day follow-up).
  After the confirmed-vs-credit split shipped, Roy reported "Leo" STILL showed KES 2550
  against an open shift with KES 0 cash/mpesa of its own — "not the actual sales recorded
  for today." Found a second, independent gap in the same hand-rolled query: `kitchen_
  revenue_today`/`kitchen_stats_api` never excluded `invoice_no='[SVQ]'` — the corrective
  cash/mpesa transaction a stock-take variance ACCEPT creates on a physical recount
  discrepancy (2026-07-25 entry above). That earlier fix swept `home()`'s bar/kitchen_
  today_revenue, the dashboard poll, the revenue-target bar, and `_reconcile()`'s own
  cash/mpesa totals — but Kitchen Board's own header tile is a genuinely separate query
  that was never part of that sweep, so it silently kept counting stock-count corrections
  as if they were real sales. Given active Kuku/chicken stock corrections this session,
  this is the likely source of the figure — not a data-entry mistake needing a backfill.
  Fixed with the same `.exclude(invoice_no='[SVQ]')` `home()` already uses. 2 new tests. No
  migrations. 1603 tests pass (core + accounts).
- Stock Receipt profit precision + direct raw-material sack-cost editing (2026-08-09,
  same-day follow-up). **(1)** Roy: "ensure the stock receipt appends and adjusts sales/
  profits accordingly", then "chicken data i recorded for previous days for that given
  receipt have not reflected yet, but the stock has reduced." Three real gaps fixed in
  `KitchenStockReceipt.total_revenue()`. PER-PRESET attribution: a line for one specific
  preset (e.g. "Full Chicken Leg" on a Kuku item that also sells Wing/Bawa via other
  presets) previously matched on `item_id` alone, so a sale of a DIFFERENT preset of the
  same shared item — possibly received under a completely separate receipt — silently
  counted toward THIS receipt's own Mapato/Faida; now filtered per `(item_id, preset_id)`
  for a preset-specific line, unchanged for a plain no-preset line. `[SVQ]`-tagged stock-
  count corrective transactions now excluded, matching every other revenue computation in
  the app. Then, fixing those two surfaced a THIRD gap: the strict per-preset match
  silently excluded a real class of historical sale — the single-preset tile-tap path
  never attached `preset_id` to the Transaction it created until earlier the SAME day's
  fix (see the entry above), so a sale rung up before that shipped has `preset=None` even
  though it was genuinely sold as the receipt's own preset (often the only sellable one at
  the time, per the "cut visibility" gating). A `preset=None` sale of the receipt's item
  now also counts — can't be positively ruled out, and `preset=None` was the historical
  norm — while a sale explicitly tagged with a DIFFERENT, non-null preset (the original,
  confirmed bug) still stays excluded. New `KitchenStockReceipt.reopen()` +
  `kitchen_stock_receipt_reopen` view/URL (owner/manager only): undoes a close from before
  all its sales were rung up — `total_revenue()`'s window freezes at `closed_at`, so a
  prematurely-closed receipt could otherwise never earn revenue again. Kitchen Board now
  shows recently-closed receipts too (previously only open ones rendered, even though the
  API already returned both), with a "↩️ Fungua Tena" button. **(2)** Live report: "the
  pencil icon is directing me to the add transaction, that is bogus... put [the cost
  editor] in the raw potatoes tile so that when I put it in, it represents the 6 buckets
  equivalent to a whole sack division, it is easier that way" — rejecting the SAME
  session's own earlier design (hand off to Add Transaction, per the "one designed writer"
  rule for `Item.cost_price`). New `edit_raw_material_cost` view/URL — owner/manager only,
  scoped to items that ARE actually a `raw_material_source` for some batch item
  (`item.derived_batch_items.exists()`) — takes a sack's whole cost + units-per-sack
  (defaults to 6), divides, writes `item.cost_price` directly. A new, deliberately
  narrow exception to the "one designed writer" rule, same category as
  `KitchenBatch.open_batch()`'s pre-existing exception. The raw-material tile's pencil
  now calls this instead of linking to Add Transaction; "✏️ Hariri Gharama" is removed
  from a raw-material-tracked `KitchenBatch` tile specifically (`item.raw_source_id` set)
  — `open_batch()` already derives `cost_total` automatically from `kg_drawn ×
  raw_item.cost_price` for every future draw once the raw item's own cost is fixed, so
  correcting THAT item is now the right lever — kept for a batch with no raw material
  source, which has no other item to correct instead. 16 new tests
  (`KitchenStockReceiptRevenuePrecisionTest` ×10, `EditRawMaterialCostTest` ×6). No
  migrations. 1619 tests pass (core + accounts).
- Kitchen Board "Leo" revenue breakdown + delete a mistaken Stock Receipt (2026-08-09,
  same-day follow-up). **(1)** Roy: "for Leo to adjust from 2550 to today's figures as
  much as there are none yet which means zero, is that too hard surely." The credit split
  and `[SVQ]` exclusion fixed earlier the same day didn't move this figure at all — every
  check already made came back clean, meaning it's genuinely summing `type='Issue'`,
  cash/mpesa, non-`[SVQ]` transactions dated today. Rather than guess a third blind
  exclusion fix (the exact mistake this app's own "audit ALL surfaces" rule warns
  against), owner/manager now get a line-by-line breakdown (item/preset, amount, payment
  method, exact time) via a small ▾ toggle next to the "Leo" tile, rendered from the same
  page-load snapshot the figure itself is built from — so the real transactions behind the
  number can actually be inspected instead of guessed at again. **(2)** Live report: "you
  have made the previous receipt which was a mistake show up, I do not need it" — the
  2026-08-09 fix making recently-closed Stock Receipts visible on the board (to enable
  "Fungua Tena") surfaced an old, already-closed "Kamau" duplicate Roy never needed to see
  before. New `kitchen_stock_receipt_delete` view/URL + "🗑 Futa" button alongside "↩️
  Fungua Tena" on a closed receipt card. Deliberately safe: deletes only the
  `KitchenStockReceipt`/`KitchenStockReceiptLine` bookkeeping rows — the real Receipt
  `Transaction` each line created (which actually added stock) is a separate row entirely,
  referenced FROM the line via a forward FK, and is never touched by deleting the line, so
  an item's stock balance is completely unaffected. Owner/manager only, same tier as every
  other financial-record correction. Same session, live clarifying question: Roy also
  flagged "Chipo Faida" as possibly inaccurate — investigated and confirmed
  `KitchenBatch.revenue_collected` is a completely separate, pre-existing mechanism (a
  running counter incremented only at real `record_sale()` calls, never built from a live
  Transaction query) untouched by any of this session's fixes; no code changed there
  without concrete evidence of a specific discrepancy — asked Roy for one rather than
  guessing a fourth time. 8 new tests (`KitchenStockReceiptDeleteTest` ×5,
  `KitchenRevenueBreakdownTest` ×3). No migrations. 1627 tests pass (core + accounts).
- Fix: raw-material cost correction did not retroactively update an already-open batch
  (2026-08-09, same-day follow-up). Roy followed up on the "Chipo Faida" question with the
  concrete evidence asked for: "could it be realistic really when i had not put in the
  cost price for the gunia before, i put it just a few moments ago... i expected it to
  adjust in a certain way, not to stay the way it was before." Traced and confirmed a real
  gap: `KitchenBatch.cost_total` is a snapshot taken once, at `open_batch()` time
  (`draw_qty × raw_item.cost_price` AS IT WAS THEN) — it never dynamically re-reads the
  raw item's `cost_price` later. `edit_raw_material_cost()` correcting Raw Potatoes' own
  cost therefore only ever affected FUTURE draws (exactly as its own original docstring
  said) — but left the CURRENTLY OPEN Chipo batch permanently frozen at its old, wrong
  placeholder cost, correspondingly inflating Faida, with no correction path at all once
  "Hariri Gharama" was removed from a raw-material-tracked batch tile earlier the same
  day. Fixed: correcting a raw item's cost now also recomputes `cost_total` for every
  currently OPEN `KitchenBatch` sourced from it (`source_qty_drawn × the newly corrected
  cost_price`), mirrors the result into that batch's own `item.cost_price` (same
  convention `edit_kitchen_batch_target` already follows), and reports which batches were
  updated in the response message. `revenue_collected` is never touched — only the cost
  side moves. A CLOSED batch is deliberately left alone, since its `cost_total` is a
  historical record of a decision already finalized. 6 new tests (extending
  `EditRawMaterialCostTest`) — including a direct regression lock that `revenue_collected`
  survives untouched and that a CLOSED batch is NOT retroactively updated. No migrations.
  1631 tests pass (core + accounts).
- Revert: `KitchenStockReceipt.total_revenue()` back to plain item-level matching
  (2026-08-09, same-day follow-up). After three same-day rounds chasing a "Stock Receipt
  Mapato/Faida doesn't reflect previous days' sales" report (per-preset attribution,
  `[SVQ]` exclusion, then a `preset=None` historical fallback), Roy reported it was still
  wrong AND that the Kuku tile — previously working — had stopped working, and gave an
  explicit instruction: "if you can't fix the receipt issue based on the recordings of
  previous days when it comes to stock count and sales, just leave it be, revert kitchen
  to the way it was more so the chicken part." Reverted `total_revenue()` to its original,
  simple form: any Issue-type sale of an item this receipt received counts toward Mapato,
  regardless of preset — no per-preset filtering, no `[SVQ]` exclusion, no historical
  fallback. Root cause of why the precision attempts never satisfied Roy was never fully
  confirmed — the most likely explanation, stated plainly rather than guessed at further:
  Rekebisha (stock-count correction) has NO concept of a selling price, only a physical
  count, so a discrepancy resolved via Rekebisha on a prior day can never retroactively
  become "sales" no matter how the revenue query is engineered — that's a genuine workflow
  gap between two different tools, not a query bug. Extensive diagnostics (fresh render of
  `/kitchen/` synthetic-data smoke test → 200 OK; `node --check` on all 8 extracted
  `<script>` blocks → all syntax-valid; direct inspection of `_kbRevenueLines`/
  `_portionItems` JSON → structurally correct) found no reproducible break in the Kuku tile
  itself — most likely a stale device cache (this app's own well-documented recurring
  failure mode for "it was working, now it's broken" reports), not a fresh regression;
  flagged to Roy rather than guessed at with further code changes. Deliberately KEPT,
  since Roy explicitly approved or never flagged them as broken: `edit_raw_material_cost()`
  (including its retroactive open-batch cost recompute — "the gunia stock edit is okay"),
  `KitchenStockReceipt.reopen()`/`.delete()` ("you have made the previous receipt which
  was a mistake show up, I do not need it" — confirming delete was wanted), the Leo tile's
  confirmed-vs-credit split + `[SVQ]` exclusion, and the `kitchen_revenue_lines` breakdown
  disclosure. 1630 tests pass (core + accounts).
- Fix: Kuku tile lost all its presets (2026-08-09, same-day follow-up). Roy, sharply: "I am
  clicking that chicken tile and it is no longer showing the presets as it was before, there
  is something that you have done that has caused this, so find it and fix it" — with a
  screenshot showing the Kuku tile reduced to a bare "✓ Imekwisha" button, no preset picker,
  price shown as "KES 0". Root-caused (not guessed): the "cut visibility" gate built earlier
  the same session (2026-08-05, refined 2026-08-09) hides a preset from the sell tile once an
  item has ANY preset-attributed receipt, unless that specific preset's own received-vs-sold
  anchor tally (`_received_by_preset`/`_sold_by_preset`, grouped by
  `stock_tracking_anchor_id()`) is still positive — a completely separate ledger from the
  item's own real `current_balance()`. If every preset's anchor tally nets to zero or
  negative (plausible after today's raw-material-cost/stock-take/Rekebisha activity — any
  plain Receipt or correction that doesn't attach a `preset_id` grows the real balance
  without ever touching the anchor tally) while real stock is still positive, ALL presets
  silently vanished from the tile at once — `tileClick()`'s branch for zero presets falls
  straight to a plain no-picker add. Fixed in `kitchen_board()` (`core/kitchen_views.py`)
  with a safety net: if the gate would leave an item with configured presets showing NONE of
  them, but the item's real `current_balance()` is still positive, fall back to showing every
  configured preset instead of hiding them all. Deliberately gated on real balance
  specifically (not just "presets list is empty") so the intentional "genuinely fully
  sold — hide everything" case, where both ledgers agree on zero, stays correctly hidden —
  already locked in by the pre-existing `test_selling_enough_half_legs_hides_both_presets`,
  confirmed still passing unmodified. 2 new tests
  (`test_ledger_drift_falls_back_to_showing_all_presets` — reproduces the drift scenario
  directly and asserts all 3 presets reappear; `test_genuinely_depleted_item_still_hides_all_
  presets` — the same fully-sold case as the existing test, confirming the fallback doesn't
  fire when it shouldn't). No migrations. 1632 tests pass (core + accounts).
- Fix: the same-day preset-fallback fix itself was wrong — remove it, add owner-only
  diagnostic numbers instead (2026-08-09, same-day follow-up, live screenshots). Roy caught
  it immediately: "it is bringing unnecessary portion presets unlike before where I saw full
  chicken legs and the tether." Tracing his exact real-world sequence (23 full legs received
  as a single Kitchen Stock Receipt, backdated sales recorded against Full/Half Chicken Leg
  for 7th/8th August, an earlier "mistake" duplicate receipt deleted the same session) showed
  the "cut visibility" gate's received-vs-sold anchor tally can legitimately drift negative
  for a preset that WAS genuinely received (e.g. a receipt line lost from a delete, a
  backdated sale landing before the gate's aggregate window) — and the earlier same-day
  fallback ("if presets end up empty but real balance is positive, show every configured
  preset") then surfaced Wing and Drumstick, which were never received, right alongside the
  correct Full/Half Chicken Leg. Fabricating a sellable tile for stock that was never
  received is a worse failure than hiding a real one — a business owner could not tell fact
  from fiction from the tile alone. Removed the fallback outright; a drift now correctly
  hides only the affected preset, never invents extras. Since the root numeric mismatch
  itself remains real and not yet fully diagnosed (no direct production DB access this
  session), added an owner/manager-only diagnostic line to every preset in the sell-tile
  modal — `_received`/`_sold` numbers computed exactly as the gate itself uses them — visible
  as "imepokewa: X · imeuzwa: Y · iliyobaki: Z" under each preset label, staff never see it.
  This turns the next report from a blind guess into a direct read of the real ledger state.
  Same investigation also traced (with code evidence, not guessed) why Stock Receipt Mapato
  never reflected Roy's backdated 7th/8th sales all day: `KitchenStockReceipt.created_at` is
  always stamped to the exact moment the receipt row is saved (never backdatable — confirmed
  by reading `kitchen_stock_receipt_create()`, which passes no `created_at`/`received_on`
  override), while a backdated sale explicitly sets `Transaction.created_at` to the past date
  the sale actually happened on (`_kitchen_checkout`'s `kb_backdated_at` override). Since
  `total_revenue()`'s window starts at `self.created_at`, a backdated sale's timestamp will
  always fall BEFORE a receipt created after the fact — structurally, regardless of any
  item/preset matching logic, which is why three separate matching-logic attempts earlier
  this same day never once fixed it. Reported to Roy with the concrete fix available
  (`KitchenStockReceipt` already has an editable `received_on` DateField unused by
  `total_revenue()`'s window; using it instead of `created_at` as the floor would close this)
  but deliberately not touched without his go-ahead, honoring his own "leave the receipt
  issue be" instruction from earlier the same day. Separately verified from Roy's own two
  screenshots (Faida KES 4,217 → KES 4,317, a clean +KES 100 matching one "Ya 100" fries
  sale) that the Chipo batch sell→profit mechanism is NOT currently broken — reported this
  plainly rather than changing code with no reproducible symptom, and asked for a specific
  failing sale if he still sees a gap. 3 tests rewritten to match the new hide-don't-fabricate
  contract (`test_ledger_drift_hides_rather_than_fabricates` replaces the old fallback test;
  `test_genuinely_depleted_item_still_hides_all_presets` unchanged), 1 new test
  (`test_owner_sees_received_sold_diagnostic_numbers` — owner sees `_received`/`_sold`, staff
  never does). No migrations. 1633 tests pass (core + accounts).
- Fix: "Hariri Gharama" hid itself on the wrong signal, silently blocking cost correction
  (2026-08-09, same-day follow-up). Roy: "i did tap the edit icon in the raw potatoes tiles
  and inputed the cost but nothing changed." Root-caused, not guessed: `_batch_to_dict()`
  already computes `from_draw` (`batch.source_item_id is not None`) — a per-BATCH signal for
  whether THIS specific open batch was actually opened via a linked raw-material draw — but
  `kitchen_board.html`'s "Hariri Gharama" button visibility checked `item.raw_source_id`
  instead (the ITEM's CURRENT configuration), the wrong granularity entirely. A batch opened
  BEFORE the raw-material link existed (or via the older plain manual-cost path) has
  `source_item_id=None` permanently — `edit_raw_material_cost()`'s retroactive recompute
  correctly has nothing to find and update for it — but the manual per-batch editor
  (`edit_kitchen_batch_target`, the ONLY other cost-correction lever) was hidden anyway,
  because the ITEM now looks raw-material-tracked even though THIS batch was never actually
  linked. Net effect: zero working correction path existed for Roy's currently-open Chipo
  batch. Fixed by switching the button's condition from `!item.raw_source_id` to
  `!batch.from_draw` — a genuinely-linked batch still correctly hides the manual editor
  (correcting the raw item is the right lever there), while a batch that predates the link
  gets its manual editor back regardless of the item's current setting. 2 new tests
  (`test_pre_link_batch_stays_editable_regardless_of_later_item_link` — reproduces Roy's
  exact timeline: batch opened before the link, link added after, manual editor still
  reachable, automatic path correctly can't touch it; `test_linked_batch_from_draw_true` —
  the positive case, unchanged). No migrations. 1635 tests pass (core + accounts).
- Live production outage — gunicorn thread starvation, not a code crash (2026-08-09/10,
  urgent). Roy reported repeated 502s "while a customer was scanning." Traced via Render's
  own Events log: "HTTP health check failed (timed out after 5 seconds)" followed minutes
  later by "Service recovered," repeating — the signature of thread starvation, not a crash
  (a real crash stays down). Root cause: this app sends SMS (Africa's Talking) SYNCHRONOUSLY
  in many request paths including customer-facing ones (payment/cash-request
  notifications); the AT SDK's own bounded timeout allows up to ~9-12s per call. Worse: the
  LIVE Render Start Command (dashboard-configured, independent of the repo) had drifted to
  bare `gunicorn stockapp.wsgi:application --bind 0.0.0.0:$PORT` — zero worker/thread
  flags, meaning gunicorn's default `sync` worker class with ONE thread, literally one
  request at a time for the whole app. Any single slow SMS call blocked EVERYTHING else
  behind it, including Render's own health check. `Procfile`/`render.yaml` in the repo had
  already specified `--workers 1 --threads 3 --worker-class gthread`, bumped to `--threads
  8` — but this had NO EFFECT until Roy manually corrected the actual dashboard Start
  Command to match (config-only fix, zero app code touched, confirmed live via Render's own
  request logs — `GET /health/` returning clean 200s every few seconds afterward).
  **Lesson for next time a live incident shows this exact fail→recover→fail pattern**: check
  the ACTUAL dashboard Start Command first — the repo's Procfile/render.yaml are not
  guaranteed to be what's actually running.
- Kitchen board root-cause deep dive during the same live incident, chicken shift (2026-08-09/10):
  answered Roy's overlapping-shift question (Recheal never closed her shift, Susan opened
  over her mid-outage) by tracing `_shift_active_segments()`/`_segments_q()` directly rather
  than guessing — confirmed this exact scenario (bartender handover mid-shift, non-waitress
  vs waitress roles) is ALREADY correctly handled: a later same-station shift automatically
  caps the earlier one's own attribution window the moment it opens, regardless of when the
  earlier shift is actually closed in the UI, and a waitress role is explicitly excluded
  from capping anyone (2026-08-06/08 Monsoon Inn fixes, unchanged, still correct). Walked
  Roy through the practical steps this DOES still require by hand: enter the physical
  handover cash count (what the new staffer found) as the outgoing shift's closing count;
  use the close-shift modal's existing "📵 Mauzo Yasiyorekodiwa (Optional)" field
  (`Shift.offline_sales_amount`/`offline_sales_note`, built Sprint 4 2026-06-13, "Option A")
  to reconcile the CASH figure immediately using the paper-recorded total, with the
  template's own built-in warning to delete that figure later if/when the real line items
  are entered via Add Transaction, so the cash never gets counted twice; correct the new
  shift's own opening float via the existing `edit_shift_opening_float` tool if it doesn't
  already match the same handover count. No code changes — confirmed the existing mechanism
  already does the hard part correctly, this was a "trace and explain," not a fix.
- Fix: Stock List's per-item query cascade (2026-08-10, same live-incident follow-up). Roy
  flagged general slowness ("transitioning through various sections... accessing stock
  list") once the crash itself was resolved. Traced concretely: `stock_list.html` calls
  `item.current_balance` 5 times per row and `item.needs_reorder` 3 times, neither cached —
  and `needs_reorder()`/`recommended_order_qty()` each internally cascade into
  `current_balance()` + `on_order()` + `reorder_point()`, with `reorder_point()` itself
  calling `avg_daily_issues()` TWICE (once via `lead_time_demand()`, once via
  `safety_stock()`). `physical_balance()`/`deficit()`/`surplus()` add yet more uncached
  re-querying. For a 100-item stock list this was issuing 1000+ separate database
  round-trips on ONE page load — the dominant, concrete cause of "stock list is slow."
  New `core/views.py::_batch_stock_metrics(items)` computes the SAME values via 3 batch
  queries (balance/physical-balance movement sum, 30-day issues sum, PO on-order totals)
  and mutates each item with new `stock_*`-prefixed attributes before the template renders.
  Deliberately did NOT memoize the shared `Item` model methods themselves (`current_
  balance()` etc.) — that's used everywhere across this codebase, and a blanket instance-
  level cache would risk returning a stale balance to any OTHER caller that reads a
  balance, writes a Transaction, then re-reads the SAME Python instance expecting a fresh
  value within one request; auditing every such call site wasn't safe to do quickly on a
  live, money-critical system. This fix is scoped entirely to `stock_list()`/
  `stock_list.html` — every other page/view in the app is completely unaffected, and the
  original methods still behave exactly as before for every other caller.
  `StockListBatchMetricsTest.test_batch_metrics_match_original_methods_exactly` proves
  numerical equivalence directly (not assumed) by running the batch helper and then calling
  the REAL per-item methods on the same items, across mixed balances, a PO on-order
  quantity, custom reorder settings, and real 30-day issue history (including an
  out-of-window Issue that must NOT count, and a Draw transaction that MUST). A query-count
  test confirms the page no longer scales per item. No migrations. 1640 tests pass (core +
  accounts). Separately traced a batch of "session expired, try again" messages Roy saw
  stacked 5-deep on login to the most likely explanation: leftover `csrf_failure_view`
  warnings queued during the earlier crash window that never got displayed (since the page
  kept failing to load before the message could render), surfacing all at once on the first
  successful load afterward — not a new, ongoing bug; asked Roy to confirm with a fresh
  login attempt rather than guessing further. `home()`'s own query load (till breakdown +
  station revenue disclosure, each computed twice — bar and kitchen — every dashboard load)
  flagged as the likely next target but deliberately NOT touched this pass — a different
  shape of problem (many distinct single queries stacked up, not per-item multiplication)
  needing its own careful, separately-tested pass rather than folding into this one.
- Fix: login page never cached — closes the CSRF failure recurring on EVERY login
  (2026-08-10, same live-incident follow-up). Roy reported the 2026-07-27 safety net
  (`csrf_failure_view`) was firing on every single login, not just as leftover debris from
  the earlier crash window as first suspected — a genuinely reproducible, ongoing symptom,
  confirmed by him retesting after the crash was long resolved. Root cause: `home()` got
  `@never_cache` (2026-07-28) specifically because a volatile dashboard must never be served
  stale by ANY caching layer between browser and view — the phone's own HTTP disk cache, or
  (per that fix's own documented root-cause list) a Kenyan mobile carrier's transparent
  compression proxy. The LOGIN PAGE itself never got the same treatment — Django's built-in
  `LoginView` sets no cache-prevention headers on its own. A stale cached copy of
  `/accounts/login/` carries a CSRF token baked into its hidden form field at cache time,
  which fails validation the moment it's submitted — regardless of what the 2026-07-27
  `CSRF_FAILURE_VIEW` safety net or the service worker's `/accounts/` network-only guard do,
  since NEITHER of those addresses an upstream cache (carrier proxy, browser HTTP cache)
  serving a stale page BEFORE the service worker or Django's own CSRF check is ever
  reached — this is a distinct mechanism from the SW-cache theory the 2026-07-27 fix
  addressed, and explains why that fix alone didn't fully close the gap. Fixed by wrapping
  both `login` and `logout` URL routes with the same `never_cache` already proven on
  `home()` (`stockapp/urls.py`). 3 new tests (`LoginPageNeverCachedTest`) — login/logout
  both carry `no-store`/`no-cache` headers, and the ordinary login flow still works
  end-to-end unaffected. No migrations. 1643 tests pass (core + accounts).
- Fix: home() dashboard's own N+1 + duplicate revenue query (2026-08-10, same live-
  incident follow-up, Roy: "go ahead" on tackling home() next). Two real, evidenced fixes.
  (1) home() had the EXACT SAME per-item `needs_reorder()`/`current_balance()` cascade
  already fixed on Stock List the same night (lines 305-329, unchanged since) — except it
  ran on the WHOLE business's item list on EVERY dashboard load, which is the FIRST page
  hit after every single login, making it arguably the higher-impact copy of the same bug.
  Reused the already-tested `core.views._batch_stock_metrics()` helper directly instead of
  duplicating the batch-query logic a second time — `reorder_items`/`low_stock_count`/
  `reorder_count`/`total_items` now come from `item.stock_needs_reorder`/`item.stock_balance`
  (precomputed once via 3 batch queries) instead of calling the cascading methods per item.
  (2) For an owner/manager, `bar_today_revenue`/`kitchen_today_revenue` were being computed
  TWICE per load — once via an inline `Transaction` query in `home()` itself, and again
  inside `station_revenue_window_info()`'s own `total_revenue` (which calls the SAME shared
  `_window_revenue()` helper `home()`'s inline version duplicated — confirmed identical
  filter shape by reading both side by side, not assumed). `station_revenue_window_info()`
  was already being called anyway (for the owner-only "vipi hesabu hii ilipatikana?"
  disclosure) — home() now reads `total_revenue` straight from its return dict for
  owner/manager instead of re-running the same query a second time; the non-owner/manager
  path (which never calls `station_revenue_window_info()` at all) is completely unchanged.
  Both changes are pure query-count reductions with provably zero behavior change — the
  pre-existing `HomeDashboardRevenueSurvivesMidnightTest` and
  `StockTakeVarianceDashboardExclusionTest` (both log in as owner, both assert
  `bar_today_revenue`'s exact value including the `[SVQ]` exclusion) passed completely
  unmodified against the new code path. New `HomeDashboardBatchMetricsTest` proves the
  reorder/low-stock counts match calling the real per-item methods directly, plus a
  query-count ceiling test. No migrations. 1646 tests pass (core + accounts).
- Waitress debt-tracker view/payment access + fix cross-access debt scope bug (2026-08-10).
  Roy: give the waitress the ability to view debts and record payments on them, but never
  let her give out (issue) new debt — only counter staff should do that. Investigation
  found "never issue debt" was already fully solved — every checkout surface (Quick Sell,
  Bar Board, Kitchen Board, `convert_tab_to_debt`, `bulk_convert_tabs_to_debt`) already has
  an explicit `role=='waitress'` block, built 2026-08-06. The actual gap was narrower:
  `debt_dashboard`/`customer_debt_profile`/`record_debt_payment` are gated by
  `@login_required` only, no role restriction — she simply had no navbar link to reach
  them. Added "💳 Debt Tracker" to her navbar (mobile + desktop), matching the existing
  icon/label convention. While verifying this, found a real pre-existing bug in
  `_debt_scope()`: it re-derived show_bar/show_kitchen from `can_access_bar`/
  `can_access_kitchen` directly instead of going through the app's single source of truth,
  `_station_scope()`. `can_access_bar` is only ever meaningfully set for kitchen-role staff
  ("Kitchen staff may access the Bar Board") — never for an ordinary bar/general/waitress
  staffer, whose own bar access is implicit via role. Any such staffer granted
  `can_access_kitchen=True` (cross-station access) therefore fell into the 'kitchen'-only
  branch, incorrectly hiding their own bar debts — not waitress-specific, affects any
  cross-access non-kitchen-role staffer. Rebuilt `_debt_scope()` on top of `_station_scope()`
  directly, fixing this for every role at once (including the mirror case: kitchen staff
  correctly granted `can_access_bar` now correctly get 'all' instead of staying kitchen-
  only). 14 new tests (`DebtScopeHelperTest` +5, `WaitressDebtTrackerAccessTest` — full
  end-to-end: view dashboard, view profile, record a payment, plus regression locks that
  she's still blocked from Quick Sell credit and `convert_tab_to_debt`). All 129 pre-
  existing debt-tracker tests confirmed passing unmodified. No migrations. 1657 tests pass
  (core + accounts).
- Fix: paying a debt-converted tab from the tabs drawer silently discarded the payment
  (2026-08-10), live report from Roy — garbled at first, then confirmed directly: "a tab
  still in the tabs drawer for over a day... the auto conversion is happening... at the
  same time there is an addition to debt in the debt tracker, when the tab is paid and
  cleared from the tabs drawer, the debt tracker still shows it as debt and unpaid."
  Traced the full lifecycle rather than guessing (`convert_tab_to_debt`/
  `_convert_tab_to_debt_core`, `_convert_open_tabs_to_debt_for_shift` — the shift-close
  auto-convert sweep — `_debt_converted_tabs_qs`, `revert_tab_from_debt`, `settle_tab`,
  `tick_entry`). **Root cause, found in `settle_tab()`** (`core/keg_views.py`, shared by
  all three counters — Bar Board, Kitchen Board, Quick Sell all POST to the same
  `/bar/tabs/<id>/settle/`): once a tab auto-converts to debt, `tab.status` flips from
  `OPEN` to `SETTLED` (correct — that's what puts it in the debt tracker). But the
  endpoint's own guard, `if tab.status != 'OPEN': return {ok:True, already_settled:True}`,
  fired unconditionally the moment ANY non-OPEN tab was settled again — including a
  debt-converted tab that still has a full unpaid balance. A staffer tapping "Lipa Yote"
  on such a tab (a stale client render, or simply not realizing conversion already
  happened — very plausible after a whole day) got a success-shaped response with ZERO
  record of the real cash/mpesa the customer just handed over: no `CustomerDebtPayment`,
  no receipt, nothing. The frontend's own `loadTabs()` refresh then removed the tab from
  the drawer (since it's not OPEN either way) — looking exactly like a normal successful
  settle from the staffer's side, while the debt tracker's figure never moved. **Fix**:
  `settle_tab()` now detects this specific state (SETTLED + has a linked customer + still
  has unpaid entries — the same "effective DEBT status" fingerprint `_debt_converted_
  tabs_qs`/`_findable_tabs_qs` already use elsewhere) and redirects the payment into a
  REAL debt payment via the same canonical `_do_settle_debt_payment()` the Debt Tracker
  page and the M-Pesa debt-payment callback already use — honoring an optional partial
  `amount` POST param the same way the normal OPEN-tab path does, station-scoped via the
  existing `_allowed_tab_sources(up)` check. A genuinely fully-paid or VOID tab (nothing
  left unpaid) keeps the exact original idempotent no-op behavior — never fabricates a
  debt payment for a tab that never carried real debt. All three tabs-drawer JS handlers
  (`bar_board.html`'s `settleTab`, `quick_sell.html`'s `qsSettleTab`,
  `kitchen_board.html`'s `settleKitchenTab`) updated to show the redirect's own message
  instead of the misleading "✓ Tab imelipwa!" toast. **Found and fixed the identical bug,
  one level worse, in the STK Push path**: `_settle_tab_from_payment()` (`core/
  mpesa_views.py`, the staff-initiated "📲 STK Push" callback handler) had the same
  `if not tab or tab.status != 'OPEN': return` guard — but here `payment.status` is
  already `'completed'` by the time this runs, meaning a REAL, Safaricom-confirmed M-Pesa
  charge (a customer's STK approval landing just after their tab auto-converted to debt)
  was being silently dropped with zero record anywhere beyond the raw `Payment` row —
  no `CustomerDebtPayment`, no receipt, no SMS, the debt tracker never reflecting money
  that had genuinely already moved. Fixed with the same redirect mechanism. Confirmed
  `tick_entry()` (the per-entry "tick" settle, distinct from "Lipa Yote") does NOT have
  this bug — it operates directly on the `BarTabEntry`/`Transaction` with no `tab.status`
  gate at all, and `_get_customer_debt_data()`'s `credit_qs` filters live on
  `Transaction.payment_method == 'credit'`, so flipping that field (which `tick_entry`
  already does) correctly self-heals the debt aggregate without needing this fix — no
  change needed there. 10 new tests (`SettleTabRedirectsToDebtPaymentTest` ×7,
  `SettleTabFromPaymentDebtRedirectTest` ×3) — real debt payment recorded (full and
  partial), the underlying `BarTabEntry` correctly flips to paid via `_do_settle_debt_
  payment`'s own FIFO reconciliation, genuinely-fully-paid and VOID tabs stay pure
  no-ops (regression lock — never fabricate a debt payment), station-scoping blocks a
  kitchen-only staffer from redirecting a bar customer's payment, invalid amounts
  rejected, and the STK-confirmed-money-never-dropped regression lock mirroring the
  existing `SettleTabFromPaymentPartialAmountTest` pattern. No migrations. 1664 tests
  pass (core + accounts).
- Retroactive-correction audit: does a fix ever need to repair data it left behind,
  not just prevent the bug going forward? (2026-08-10, same-day follow-up). Roy's own
  question, with screenshots proving the point: several `Keg Gold` debt entries from
  1–9 Aug were STILL sitting unpaid in the debt tracker despite Roy believing they'd
  already been settled in the tabs drawer around those dates — direct, concrete
  evidence that the settle_tab-redirect bug above had been silently discarding real
  payments for over a week before today's fix, and a fair challenge: for THIS and
  every other recent fix, does correcting the code also correct the data it already
  produced? Audited every fix logged between 2026-07-27 and today (roughly 40 entries)
  and sorted each into one of three buckets. **(1) Pure computation/display/access
  fixes — self-heal automatically for ALL historical data the moment the fix deploys,
  zero action needed.** This is the large majority: `confirmed_sales`/`credit_rev`
  revenue-conflation separation, every `till_expected_cash()`/`station_revenue_window_*`
  anchor-and-window fix, `_debt_scope()`'s cross-access bug, the Recent-Payments/
  notification cross-counter-leak fixes, the Kitchen Board "Leo" `[SVQ]`/credit
  exclusions, the Stock List/`home()` N+1 query fixes, the Kuku-tile preset-visibility
  gate, and the (ultimately reverted, per Roy's own "leave it be") Kitchen Stock
  Receipt Mapato/Faida precision attempts — none of these ever wrote a wrong stored
  value; they only ever computed a number differently at DISPLAY time from data that
  was always intact, so every past day's figures are already showing correctly right
  now, with nothing to backfill. **(2) A real stored value was wrong or missing, but a
  correct value CAN be deterministically re-derived from other data that's still
  present** — the one case found this pass: `Shift.station` (added migration 0132,
  2026-07-27/28) is blank on every shift closed before that migration, and
  `_shift_station()`'s fallback for a blank row is the SAME buggy role-based guess the
  fix replaced — wrong for exactly the population (managers, cross-access staff)
  the bug report was about, meaning every till/reconciliation figure that still
  anchors on one of these old shifts can still be silently misattributed today. New
  `backfill_shift_station` management command infers the real station per blank-station
  shift from that staffer's OWN Transactions actually recorded during the shift's own
  time window (`item.store.is_kitchen` — a real activity signal, not a role guess) —
  only writes when the signal is unambiguous (one station has zero transactions, or one
  has ≥80% of them); genuinely mixed activity (e.g. a concurrent bar+kitchen shift pair
  for the same cross-access staffer, where transactions from both legitimately overlap
  in time) is deliberately left blank and reported for manual review rather than
  guessed. `--dry-run` first, matching this app's own established backfill convention;
  run once per deployed environment via Render's Shell tab. **(3) A real stored value
  is MISSING with no reliable way to reconstruct it — the honest answer is "not
  backfillable," and the fix only prevents recurrence.** This is where today's
  settle_tab bug and Roy's screenshots land: the bug's failure mode was "record
  nothing" — no `Payment`, no `CustomerDebtPayment`, no log row tying a specific staff
  tap to a specific tab at a specific time — so there is no artifact anywhere to
  recover from, and no way to distinguish "this old unpaid entry was actually paid and
  silently dropped" from "this old unpaid entry is genuinely still owed" without asking
  a human who remembers. Two more real-but-unrecoverable gaps found in the same audit,
  both flagged rather than guessed at: the pre-2026-07-31 client-trusted preset stock
  quantity bug (Roy's own KC Ginger half-bottle discrepancy) may have left some
  historical `Transaction.qty` values off from the true physical amount, but the
  correct historical value was never independently recorded anywhere to check against
  — the existing ⚖️ Rekebisha physical-recount tool is and remains the only correct
  fix for any resulting stock discrepancy, exactly as it already is for every other
  cause of a wrong balance; and `order_views.py`'s pre-2026-07-31
  `_create_transactions_for_order()` NameError (a served waitress table-order line
  could get zero stock effect when `order.waitress` was falsy) has no FK from
  `Transaction` back to `TableOrder` to detect which historical served orders were
  actually affected, and guessing via fuzzy item/time matching risks creating
  DUPLICATE transactions if wrong — worse than leaving it alone — so this is flagged
  as a narrow, likely-rare historical gap with no safe automatic detection, not solved
  here. **Two correct-but-blocked queues Roy should physically go check now**, found
  by this same audit, distinct from both buckets above — nothing was ever computed
  wrong, an approve/reject BUTTON was simply invisible until its fix shipped, so real
  pending entries have been silently accumulating: the owner's own petty cash entries
  (blocked until the 2026-07-31 `petty_cash_list.html` template-gap fix — check
  `/petty-cash/` for anything still `status='pending'` that Roy himself recorded), and
  — as of today — any debt-converted tab whose "Lipa Yote" tap silently no-op'd before
  this session's settle_tab fix (check the debt tracker's per-customer "Unpaid Credit
  Transactions" list against what staff actually remember collecting, and use
  `record_debt_payment`'s existing `paid_date` backdate field — built 2026-08-09 for
  precisely this "paid a while ago, never recorded at the time" scenario — to record
  each one against its real payment date rather than today's). Also fixed, found only
  because the full suite happened to run right at local midnight during this audit: a
  pre-existing test-authoring bug in `AdHocExpenseDayReconciliationTest`
  (`_make_closed_shift` anchored `started_at`/`ended_at` to `timezone.now() -
  timedelta(hours=N)`, which slips into the previous LOCAL calendar day exactly like
  every other instance of this bug class already documented in this file —
  `PettyCashReviewUndoTest`, `BarZReportOverlappingShiftsTest`) — re-anchored to a
  fixed mid-morning-today timestamp, same fix pattern as those two. 7 new tests
  (`BackfillShiftStationTest`). No migrations (backfill command reads/writes existing
  fields only). 1674 tests pass (core + accounts).
- Offline page told users the wrong thing, with no auto-retry (2026-08-11), live
  report: Roy hit the PWA's "You're Offline / lost your internet connection" screen
  while YouTube streamed smoothly on the same phone at the same moment — proof the
  message was actively wrong. Investigated live via Render dashboard screenshots
  first, not guessed: the Total Requests graph grouped by status code showed clean
  200s with no visible 502 anywhere in the 12-hour window, and the Events log's most
  recent "Instance failed"/"Service recovered" pair was from 2026-08-09 (the
  already-fixed gunicorn thread-starvation incident) — nothing recent. So the
  container itself never crashed. Root cause of the confusing "offline" message
  itself: `sw.js`'s navigate handler (`fetch(request).catch(() => ... caches.match
  (OFFLINE_URL))`) shows the offline fallback on ANY rejected `fetch()` promise —
  which fires not just when the phone has no internet at all, but also for a DNS/TLS
  hiccup or a slow/failed connection reaching THIS specific server, completely
  independent of whether other sites/apps work. Render's own health-check/Events log
  only measures Render's fast internal path to the container — it says nothing about
  whether an external phone over real mobile data could complete the full round trip
  to Render's public edge at that exact moment, so "Render says healthy" and "this
  one phone briefly couldn't reach us" are not a contradiction. Fixed
  `templates/offline.html`: reworded from a confident, often-wrong "you've lost your
  internet connection" to an honest "could be your connection, or a brief hiccup
  reaching our server"; added a background poll of the existing lightweight,
  unauthenticated `/health/` endpoint (already built for Render's own preboot check)
  every 5s with a 4s per-attempt timeout via `AbortController`, auto-reloading the
  page the instant a real connection succeeds — most users should now never need to
  tap "Try Again" themselves. Bumped `sw.js`'s `CACHE_NAME` 'duka-v11' → 'duka-v12' —
  `/offline/` is in `PRECACHE_URLS`, so without a cache-name bump a device that
  already installed the old service worker would keep serving the STALE cached copy
  of this exact page indefinitely, silently defeating the fix for exactly the users
  who need it. 3 new tests (`OfflinePageTest`). No migrations (template + static
  asset only).
- Analytics section audit — wastage/void/owner-drawing losses used a naive formula,
  not `cost()` (2026-08-11). Roy: "the revenues do not make sense... losses is just
  nonsense... I think the imbalance is being resulted as an effect of the spirits
  that are being sold in split form (quarter/half/three-quarter)." Screenshots showed
  Net Profit at −9,994,154 and Hasara/Losses' own breakdown naming the culprit
  directly: "Voids: 10,020,600" — on a business doing ~KES 150k of revenue for the
  period. **Root cause, confirmed by direct trace, not guessed**: `analytics_views.py`'s
  `wastage_loss`/`void_loss`/`owner_drawings_cost` each reimplemented a naive
  `abs(qty) * item.cost_price` formula instead of reusing `Transaction.cost()`'s own
  keg/bunch/batch/preset-aware proportional logic — the exact "don't reimplement,
  always call the canonical method" anti-pattern this file's own Known Issues section
  already warns about (raw `Sum('sale_amount')`, `Transaction.cost()`'s own
  `kitchen_batch_id` gap, both fixed the same way before). The naive formula is only
  correct for a plain, non-keg/non-bunch/non-batch/non-preset item. For a keg pour
  specifically it's catastrophic: `qty` is stored in ML (`KegBarrel.record_sale()`)
  while `item.cost_price` is priced per WHOLE KEG (thousands of KES) — a single
  voided ~500ml pour naively priced as `500 × cost_per_keg` inflates the loss by
  roughly 1000×, more than enough to produce a multi-million-shilling phantom figure
  from a handful of voided pours, matching the reported scale exactly (confirmed with
  the fixture's own real numbers: `item.cost_price=12000`, a 500ml void naively priced
  at 6,000,000 vs the correct proportional ~33). Roy's own hypothesis (spirits split
  sales) turned out NOT to be the dominant cause — a preset-linked quarter/half/
  three-quarter sale's `qty` is already a small, item-comparable fraction (e.g. -0.25),
  so the naive formula happened to be right-shaped for that case specifically;
  reasoned this out and told him directly rather than confirming a guess that didn't
  hold up under trace. **Fix**: extracted `Transaction.cost()`'s branch logic into
  `_stock_movement_cost()`, reused by `cost()` (Issue only, unchanged — every existing
  caller/test keeps working exactly as before) and new `loss_value()` (Wastage/
  OwnerConsumption, transaction types `cost()` deliberately zeroes by design so a
  non-sale movement never double-counts as if it were also a sale). `void_loss`
  switched to calling `t.cost()` directly (void transactions are still `type='Issue'`,
  so `cost()` already had the right logic — the bug was only that this call site never
  used it). **Real edge case caught while writing the fix, not by the test suite
  first**: `KitchenBatch.discard()`'s own Wastage row deliberately sets
  `sale_amount=Decimal('0')` (nothing was sold) with `qty` as the unrecovered fraction
  of `cost_total` — the original `kitchen_batch_id` branch checked `self.sale_amount
  is not None`, which is TRUE for `0` too, wrongly taking the proportional-sale branch
  (`0 × cost_total / revenue_collected = 0`) instead of falling through to the correct
  `item.cost_price`-based computation (`item.cost_price == cost_total` by this app's
  own established KitchenBatch convention) whenever the batch had ANY prior revenue
  collected before being discarded. Fixed by checking `self.sale_amount` (truthy, i.e.
  `> 0`) instead of `is not None` — a real sale can never legitimately be for KES 0
  through this mechanism, so truthiness cleanly separates a genuine sale from a
  discard row. Same naive-formula bug found and fixed in two more spots during the
  audit: `analytics_dashboard`'s PORTION-produce revenue/cost breakdown (switched to
  `t.revenue()`/`t.cost()`) and `daily_sales()`'s own `wastage_value` figure (switched
  to `t.loss_value()`) — Kitchen Performance was independently confirmed already
  correct (already used `t.revenue()`/`t.cost()` from an earlier sprint). **Also
  fixed, same report**: `top_products`/`store_list` showed raw accumulated floats
  ("Blue Ice 32.69999999999997 units", "Liquor Store 700.2000000000004 units") —
  ordinary binary-float summation noise from adding many small fractional preset
  amounts, never rounded before display unlike every other accumulated-units figure
  already rounded elsewhere on the same page (`units_sold`, `portion_produce`'s
  `units`) — now rounded to 2 decimals (not 1, to keep a genuine quarter-bottle sale
  showing as 0.25, not rounded away to 0.3). 17 new tests
  (`TransactionLossValueFixTest`, `AnalyticsVoidLossIntegrationTest`,
  `AnalyticsUnitsFloatRoundingTest`) — plain-item Wastage unchanged (regression lock),
  the voided-keg-pour scenario reproducing the exact reported bug end to end through
  `/analytics/`, `ProduceBunch.discard()` correctly using `bunch.cost_price` not
  `item.cost_price`, the `KitchenBatch.discard()`-after-prior-sales edge case, a real
  `KitchenBatch` sale still costing correctly (regression lock), `OwnerConsumption`
  using the new helper, `loss_value()` returning 0 for `type='Issue'` (must never
  double-count a sale), and the float-noise-never-displayed regression lock for both
  `top_products` and `store_list`. All pre-existing analytics/KitchenBatch/
  ProduceBunch test classes (73 tests across `KitchenBatchModelTest`,
  `KitchenBatchDiscardRecordsWastageTest`, `TransactionCostKitchenBatchProportionalTest`,
  `EditProduceBunchCostTest`, `EditKitchenBatchTargetTest`, and others) confirmed
  passing unmodified. No migrations. 1687 tests pass (core + accounts).
- Analytics period-filter CSS bug + tap-to-expand tile breakdowns (2026-08-11), same-day
  follow-up. Roy: (1) "the period filter up there is misbehaving when selecting a
  period", (2) "is it possible to break down what is being shown as the revenue... gross
  profit, net profit, owner drawings... and losses tiles when user selects them", (3)
  relaying a doubt from Bosco that revenue "must be extremely exaggerated" — "how do you
  think we could figure out the truth." **(1) Period filter**: root-caused from the CSS,
  not guessed — `.period-btn:hover` and `.period-btn.active` shared one style rule, so a
  touch/tap on a DIFFERENT period button looked visually identical to that button being
  the real selection, while the genuinely active period (from the currently loaded page)
  kept its own highlight too — two pills reading "selected" at once. This is a purely
  visual collision, not a real data bug — the underlying `period` value used for every
  computation is always exactly what the URL says, single source of truth. Fixed by
  giving `:hover` a lighter accent (border+text colour, no solid fill) so only the true
  `.active` state ever shows the solid raspberry fill. **(2)+(3) Breakdowns**: the
  concrete tool for answering "is revenue really exaggerated" empirically rather than by
  argument — tap-to-expand `<details>/<summary>` panels (same native, no-JS pattern
  established by the debt-erase/petty-cash disclosures) added to all 6 requested tiles.
  Revenue/Gross Profit show a day-by-day breakdown for the CURRENT period (reusing
  `daily_data`, already computed for the trend chart — no new query) plus the
  PREVIOUS period's own total alongside it, so the "X vs prev" comparison is directly
  checkable against two real numbers instead of trusted as a single percentage. Owner
  Drawings and Hasara/Losses list every underlying `OwnerConsumption`/`Wastage`/voided-
  `Issue` transaction with its own computed value (via `loss_value()`/`cost()` — the SAME
  correct methods from the loss-formula fix above, so the breakdown numbers can never
  drift from the headline total). Total Expenses lists the real `BusinessExpense` rows.
  Net Profit's tile expands its already-shown one-line formula summary into the full
  Gross − Expenses − Drawings − Hasara = Net breakdown. New context keys
  (`revenue_daily_breakdown`, `prev_revenue`, `prev_profit`, `prev_start`, `prev_end`,
  `owner_drawing_items`, `loss_items`, `expense_items`) built from data the view was
  already computing — `owner_drawing_txns`/`wastage_txns`/`void_txns` materialized to
  lists once (`list(...)`) so the same queryset serves both the cost SUM and the
  breakdown LIST with no duplicate querying. Traced the actual "X vs prev" comparison
  window while investigating Bosco's doubt and confirmed it's structurally fair (both
  periods are exactly `days` long, back-to-back, no overlap or length mismatch) — the
  likely honest explanation for a large multiplier, given the page's own "Active Selling
  Days: 12/30" figure from the same screenshot, is a genuinely quieter previous period
  rather than a computation bug; told to Roy directly as a hypothesis to verify with the
  new breakdown, not asserted as fact. 6 new tests (`AnalyticsTileBreakdownTest`). No
  migrations. 1693 tests pass (core + accounts).
- Live 502 investigation, second occurrence (2026-08-11) — Roy reported a 502 "right now
  as we are speaking," shortly after two consecutive deploys, with a much fuller set of
  Render screenshots than the first 2026-08-09 incident: Events log (both deploys marked
  successfully live), application logs (normal traffic, healthy `/health/` checks), web
  service Memory/CPU/Total-Instances/Total-Requests, and — new this time — the SEPARATE
  Postgres database service's own Memory/CPU/Disk-Usage/Disk-Activity/Network/Active-
  Connections/Transaction-Volume graphs. Investigated properly this time rather than
  repeating the earlier session's mistake of an under-evidenced "weak signal" guess
  (which Roy correctly and sharply rejected: "why am i able to watch YouTube videos
  smoothly but the app says no internet connection"). Diffed both of my two most recent
  deploys line-by-line first, specifically looking for anything that could spike CPU or
  hang a request: the `Transaction.cost()`/`loss_value()` refactor is a pure per-instance
  computation with no new queries; the analytics tile-breakdown feature only iterates
  data already being pulled for the existing period-bounded chart. Neither is a plausible
  crash/slowdown source — ruled out with evidence, not assumed. **Real finding**:
  `render.yaml` has the Postgres database on Render's **Free plan**, not Starter — this
  matches the "0.25GB / 0.1 CPU" ceiling visible in Roy's own screenshots exactly, and
  free-tier Postgres has essentially no headroom to absorb a burst. Two things make that
  already-tight ceiling worse: (1) `core/management/commands/fix_staff_profiles.py` — run
  on EVERY deploy's release phase (`Procfile`: `migrate && fix_staff_profiles &&
  reset_superuser`) — looped over every `User` on the WHOLE PLATFORM issuing one
  extra query per user (`user.userprofile` on a cache miss) just to find the handful
  missing a profile; a genuine N+1 burst of database load stacked directly at deploy
  time, on top of whatever else is happening. Fixed to a single
  `User.objects.filter(userprofile__isnull=True)` query. (2) `SESSION_SAVE_EVERY_REQUEST
  = True` (deliberate, documented in this file's own Settings section — "Prevents CSRF
  token mismatch after cold starts") means the app writes to the database-backed session
  table on every single authenticated request, platform-wide, all day — very likely why
  the disk-write graph in Roy's screenshot looked continuous across the whole 12-hour
  window rather than only spiking at deploy time. Deliberately NOT touched — changing it
  risks reintroducing the specific CSRF-mismatch bug it was added to fix, and that's not
  a change to make on a live, money-critical app without its own careful, separately-
  tested pass. Also confirmed unrelated to this incident, and left alone: `conn_max_age
  =600` (connection pooling is already correctly configured, not a contributing factor);
  the health check (`health_check()` in `core/views.py`) genuinely does touch the
  database via `connection.ensure_connection()`, which is the plausible mechanism tying a
  momentarily-saturated free-tier database to an actual Render-perceived instance
  failure (a slow/timed-out health check reads as "unhealthy" to Render, which can then
  restart the instance — the same failure signature already documented for the first
  2026-08-09 incident, "HTTP health check failed (timed out after 5 seconds)"). Told to
  Roy plainly: the query-count fix is real and shipped, but the most likely durable fix
  is upgrading the database off the Free plan — a Render-dashboard/billing action only
  Roy can take, not something fixable in code. 4 new tests (`FixStaffProfilesCommandTest`
  — orphaned user gets a profile, existing profile left untouched, superuser skipped, and
  a no-orphans no-op). No migrations. 1697 tests pass (core + accounts).
  **Correction (same day, live dashboard screenshots from Roy)**: the "Free plan" claim
  above was wrong — trusted `render.yaml`'s `plan: free` line instead of checking the
  actual live Render dashboard, the exact config-drift trap this file's own Known Issues
  section already warns about for the web service Start Command (2026-08-09/10 entry).
  The database is really on **Basic-256mb**, a paid tier at $6.30/month — but Roy's own
  screenshot confirms it genuinely is capped at 256MB RAM / 0.1 CPU, the same number
  originally cited, so the resource-ceiling analysis and the "upgrade for headroom"
  recommendation both still hold; only the free-vs-paid label was wrong. Render's next
  tier up, Basic-1gb ($19/mo), gives 0.5 CPU — 5× the current allowance. `render.yaml`
  annotated with a prominent warning not to trust its `plan:` lines for a live incident;
  left the value as `free` rather than guess at Render's current plan-slug naming for the
  newer Basic-256mb-style tiers, since this file isn't being used to sync the live
  services anyway. **Resolved (same day)**: Roy upgraded the database from Basic-256mb
  (256MB RAM / 0.1 CPU) to Basic-1gb (1GB RAM / 0.5 CPU — 5× the CPU allowance), storage
  bumped 1GB → 5GB with autoscaling enabled. `render.yaml`'s `plan:`/storage fields were
  left untouched per the file's own new warning comment — the dashboard is the source of
  truth, not this file. No app-side change needed (same `DATABASE_URL`). Watch point for
  a future session: if 502s recur after this upgrade, the CPU-ceiling hypothesis was
  incomplete and the investigation needs to resume from the continuous disk-write-activity
  angle (session-table writes, per `SESSION_SAVE_EVERY_REQUEST=True`) or something not yet
  identified — if they stop, this closes the incident.
- Hidden-presets diagnostic (2026-08-11), live report: "why are the presets not showing up
  when i press the Kuku tile" — tapping it added a bare "Kuku — KES 0" line straight to
  cart with no picker at all. Traced (not guessed): the tile's own displayed price was a
  flat "KES 0" rather than a range, which only happens when `item.presets.length === 0` —
  `tileClick()`'s three-way branch (0/1/many presets) falls to the "plain item at
  `selling_price`" case, and Kuku's own base `selling_price` has always been KES 0 since
  all real pricing lives on its presets. Root cause: the 2026-08-05/09 cut-visibility gate
  (`kitchen_board()`) had hidden EVERY configured preset on Kuku at once — each one's own
  received-minus-sold anchor tally (tracked separately from the item's overall balance,
  see the 2026-08-09 CLAUDE.md entries) had gone to zero or below. Roy's own added context
  — he'd recorded BACKDATED sales against this same receipt for a previous date — is a
  legitimate, non-buggy mechanism for this: a backdated sale counts toward `_sold_by_
  preset` exactly like a same-day one (real depletion is real depletion regardless of when
  it's entered), so a good volume of catch-up postings can genuinely exhaust the tracked
  anchor. Roy explicitly asked to see the real numbers first, one step at a time, before
  any fix — the 2026-08-09 diagnostic (`_received`/`_sold` on each preset) only ever
  rendered for presets that were STILL VISIBLE, so the one moment it mattered most (every
  preset hidden at once) left nothing on screen to look at. New `hidden_presets` list
  (owner/manager only, `core/kitchen_views.py::kitchen_board()`) — every preset the gate
  filtered OUT, with its own `_received`/`_sold`/`_remaining` numbers and `tethered_to`
  (which anchor a tethered preset like Half Chicken Leg tracks). Deliberately additive —
  the staff-facing `presets` list (what's actually sellable) is completely unchanged, only
  computed via an extracted `_is_visible(p)` helper so the two lists can never drift apart.
  Kitchen Board: a small "🔍 N zimefichwa" badge (owner/manager only) appears on any tile
  with hidden presets, opening a plain `alert()` (matching this file's existing convention
  for simple owner-only read-only displays) listing each hidden preset's label, tether
  target, and the three numbers, plus a one-line explanation of why it's hidden. 6 new
  tests added to `PresetStockTrackingTetherTest` — empty when nothing's hidden, correct
  numbers surfaced for the exact both-presets-vanish scenario Roy hit, and a regression
  lock that backdated sales correctly count toward `_sold` (so a future session doesn't
  "fix" this to exclude them, which would be wrong). No migrations. 1700 tests pass (core +
  accounts). Next: once Roy taps the diagnostic and reports back the real numbers, decide
  together whether this is expected depletion (nothing to fix) or a genuine data issue
  needing a correction — and separately, the tile still lets a sale through as a bare
  zero-price, preset-less line when nothing is visible, which is its own real bug flagged
  but deliberately not fixed yet, per Roy's "one step at a time."
- `KitchenStockReceipt.total_revenue()` window floor: created_at → received_on
  (2026-08-11, same-day follow-up). The hidden-presets diagnostic above surfaced real
  evidence: Kamau's Kuku receipt showed KES 0 Mapato / -100% Faida despite 32.5 units of
  genuinely-recorded, backdated sales existing for its own items — confirming (not just
  hypothesizing) the mechanism flagged earlier the same day. Root cause: the window's start
  was `self.created_at` — the moment the RECEIPT ROW was typed into the system, always
  "now" and never backdatable — while a catch-up posting's whole point is a sale with a
  BACKDATED `created_at` pointing to before that. Fixed by anchoring the window's start to
  `self.received_on` (the date the delivery physically arrived, already user-editable) via
  `datetime.combine(received_on, time.min)` localized to the project timezone, instead of
  `created_at`. A sale now correctly counts as long as it's dated on or after the day the
  stock it's selling actually arrived — matching how every other backdated-sale-aware
  figure in this app already reasons about it. Also surfaced a second, SEPARATE root cause
  for Kuku's -9.5 "Iliyobaki" preset-ledger drift while investigating (distinct from the
  receipt-window bug — fixing one does not fix the other): the 2026-08-09
  `kitchen_stock_receipt_delete()` view (built to remove a "mistake duplicate" Kamau
  receipt) is deliberately, correctly designed to delete ONLY the KitchenStockReceipt/
  KitchenStockReceiptLine bookkeeping rows, never the real stock-adding Transaction those
  lines created — so if that deleted duplicate really did add real chicken stock at some
  point, that stock is still sitting in the item's overall `current_balance()` right now,
  completely invisible to `_received_by_preset` (which only sums from surviving
  `KitchenStockReceiptLine` rows) — a very plausible explanation for why the item's real
  balance (13.5) and the preset anchor tally (-9.5) can both be true at once (roughly 23
  units' worth of gap between them, matching the deleted receipt's own line size).
  Unresolved and flagged to Roy rather than guessed at further: whether that deleted
  receipt was a genuine duplicate (meaning phantom stock is ALSO inflating the item's real
  balance, not just the preset tracker) or a real, separate delivery mislabeled a mistake.
  Also explained, not fixed (working as designed): `edit_raw_material_cost()`'s retroactive
  cost_total recompute only reaches a `KitchenBatch` that's still OPEN at correction time —
  Roy's "nothing changed" report for Chipo's raw-potato cost fix is consistent with every
  affected "bucket preparation" from 7th-8th August having already closed (sold through/
  discarded) before he entered the correction days later; a closed batch's cost_total is
  deliberately treated as a finalized historical record, per that function's own 2026-08-09
  docstring. Given both explanations, Roy confirmed he wants a clean wipe-and-re-enter for
  BOTH Kuku and Chipo since their most recent receipts, re-posting from the staff's paper
  sales book via backdating — a dedicated, preview-first safe-deletion tool for this
  (mirroring the existing "Reset Sales & Analytics" pattern, scoped to just these two
  items/mechanisms) is the next piece of work, not yet built. 2 new tests
  (`test_backdated_sale_before_created_at_but_after_received_on_now_counts`,
  `test_sale_genuinely_before_received_on_does_not_count`) on
  `KitchenStockReceiptRevenuePrecisionTest`. No migrations. 1702 tests pass (core +
  accounts).
- Kitchen Item Reset — erase-and-re-enter tool + receipt received_on now settable
  (2026-08-11, same-day follow-up). Roy confirmed the deleted "Kamau duplicate" WAS a
  genuine duplicate and Chipo's cutoff (since the sack arrived Wednesday) was fine, then:
  "just do this help me erase receipts and sales for both chipo and chicken for the most
  recent receipt for both and then i start again," plus "is it possible in the receipt i
  tell the app that this receipt is for a certain day then i just backdate from there till
  today." New `core/kitchen_reset_views.py` — mirrors `reset_views.py`'s proven "Reset
  Sales & Analytics" pattern (backup workbook first, typed item-name confirmation, one
  atomic transaction) but scoped to ONE item instead of the whole business, reusing
  `SalesResetLog` directly for the audit trail (no new migration — `reason`/
  `counts_snapshot` already generic enough) rather than inventing a parallel log model.
  `_scope_for_item(business, item, cutoff_date)` branches on `item.is_kitchen_batch`: for a
  portion item (Kuku), scope is every `Transaction` for the item since cutoff PLUS any
  `KitchenStockReceipt`/`Line` for it since cutoff; for a batch item (Chipo), scope is
  every `KitchenBatch` for the item with `received_on >= cutoff` plus every `Transaction`
  tied to those specific batches via `kitchen_batch_id` — a batch from before the cutoff is
  correctly left untouched even if it's still open. Deliberately does NOT attempt to
  algorithmically identify/delete the specific phantom duplicate Transaction or the exact
  raw-material Draw transaction that funded a since-deleted Chipo batch — no reliable FK
  ties either back to "the one at fault" (`KitchenBatch` has no FK to its own opening Draw
  transaction, only a `source_qty_drawn` snapshot), and fuzzy qty/date matching risks
  deleting the wrong row; the tool's own "next steps" page instead points the owner at one
  real physical recount via the existing ⚖️ Rekebisha tool afterward, which absorbs
  whatever drift is left regardless of its exact historical cause. Safety net found while
  building this: `BarTabEntry.transaction` is `on_delete=CASCADE` — a sale linked to a live
  Food Tab is therefore excluded from deletion entirely (never silently cascade-killing a
  tab entry), surfaced as its own `tab_linked_count` in the preview rather than deleted or
  hidden. Reachable via a new "🔄 Anza Upya" link on both the Kuku (portion) and Chipo
  (batch) tiles, owner/manager only. Separately, `kitchen_stock_receipt_create()` gained an
  optional `received_on` POST field (defaults to today exactly as before when blank/
  invalid) — the Stock Receipt modal now has an "Ilipokewa Tarehe" date input, so a
  delivery can be dated for the day it actually arrived, which is exactly the field the
  same-day `total_revenue()` window-floor fix now anchors on — answering Roy's question
  directly: yes, he can now date a receipt for a specific day and backdate sales against it
  from there forward. 15 new tests (`KitchenItemResetTest` — portion-item and batch-item
  scope preview, backup-required gate, exact-name-match gate, cutoff correctly preserves
  older history, audit log content, tab-linked exclusion, a batch predating the cutoff
  stays untouched, staff/cross-business access control; 3 more on `KitchenStockReceiptTest`
  for `received_on`). No migrations. 1717 tests pass (core + accounts).
- Four live fixes in one session (2026-08-11, same-day follow-up): Counter Cash ↔
  Matumizi double-entry, single-owner reorder alerts, item-scoped reset falsely
  triggering a business-wide recount, and staff hard-blocked past keg target regardless
  of the toggle. **(1) Counter Cash / Matumizi**: Roy assumed every ingredient/utility
  purchase needed typing into BOTH Counter Cash (`PettyCash`, for till reconciliation)
  AND Matumizi ya Leo (ad-hoc `BusinessExpense`) — turns out `review_petty_cash()`
  (2026-07-26, item 1) already auto-mirrors any APPROVED entry into Expense Intelligence
  via `linked_expense`, so this was never actually necessary; Roy simply didn't know.
  The real gap ran the other direction: EVERY approved reason (including cash physically
  handed to a person — police, chama, a personal loan) got mirrored as a business
  expense, with no way to say "this left the till but isn't an operating cost." New
  `PettyCash.REASON_CHOICES` entry `'cash_disbursement'`; `review_petty_cash()`'s
  auto-link block now skips it specifically — `till_expected_cash()`/`_reconcile()` are
  unaffected (pure `status='approved'` reads, no reason filter, so the till impact is
  unchanged either way). Modal gets a matching option + an inline hint explaining the
  distinction at record time. Migration 0159 (choices-only, no schema change). **(2)
  Reorder alerts**: Roy — "Bosco is telling me the beer reorder did not come to him...
  I've confirmed the reorder levels are set correctly." Traced `notify_reorder_alert()`
  (called from `notify_transaction()` whenever `item.needs_reorder()` goes true):
  resolved a SINGLE recipient via `business.users.filter(role="owner").first()` — Roy
  confirmed Bosco IS an owner-role account, meaning this business has TWO owner-role
  users and `.first()` was silently dropping whichever one didn't happen to sort first
  (Roy, apparently, always won). Rewritten to fan out to EVERY owner-role profile.
  Deliberately did NOT widen to `role__in=['owner','manager']` — the established
  convention elsewhere in this app — per Roy's own explicit correction: "only the owner
  should get stock alerts no one else." LOW_STOCK stays email + in-app only per
  `ROUTING_RULES`, unchanged — never SMS, so no interaction with the SMS bundling rate
  limiter from calling `route_notification()` once per recipient. **(3) Fresh-count
  false trigger**: same-day live report — Roy ran the new item-scoped Kitchen Item Reset
  on only Kuku and Chipo, and the dashboard immediately demanded a fresh physical count
  of 49 items business-wide, bar stock included. Root cause: Kitchen Item Reset
  deliberately reuses `SalesResetLog` for its own audit trail rather than a parallel
  model — but every "fresh count pending" banner (`home()`, `stock_list()`,
  `fresh_stock_count_checklist()`) picked whichever `SalesResetLog` was simply the MOST
  RECENT, with no way to tell a full business wipe apart from a 2-item reset. New
  `core.reset_views.latest_business_wide_reset(business)` distinguishes them by checking
  for an `'item'` key in `counts_snapshot` (only ever present on a Kitchen Item Reset's
  snapshot — a full reset's snapshot keys are model names, never literally `'item'`),
  scanning the last 20 rows in **plain Python**, not a queryset `__has_key` filter — this
  app has already hit real cross-database trouble with JSONField lookups on SQLite (see
  the documented `_safe_linked_query()` `__contains` `NotSupportedError` fix) and resets
  are rare enough that a Python scan is cheap and correct everywhere. All three call
  sites now route through this helper. **(4) Keg "sell past target" toggle a no-op for
  staff**: Roy — "whether i disable or enable the sales past target toggle, the system
  is still denying staff access" — confirmed real, not a settings mistake. Traced
  `openSellModal()`'s envelope gate (`bar_board.html`): when `block_sales_past_target`
  is OFF ("soft" mode), the OWNER correctly got a choice (close the barrel, or override
  and keep selling) — but STAFF hit an unconditional `showToast(...); return;` with zero
  path forward, IDENTICAL to the hard-block branch, regardless of the toggle. Root
  cause: `/stock/bar/deplete/<id>/` (closing a barrel) is owner/manager-only server-side,
  so the original author never gave staff a "close it" choice — but instead of falling
  through to let them keep selling (the toggle's own literal purpose when off), it just
  blocked them outright, making the toggle meaningless for anyone who isn't the owner.
  Fixed: staff in soft mode now get a brief "still on tap" warning toast and fall straight
  through to the sell modal, same as the owner's "Cancel = continue" path; hard mode is
  completely unchanged (staff still must involve the owner there, matching the settings
  page's own documented intent). Pure frontend fix, no backend/API change, no migration.
  9 new tests (`PettyCashAccountabilityTest`, `LowStockReorderNotificationTest`,
  `FreshStockCountChecklistTest`). One migration (0159, additive). 1724 tests pass (core
  + accounts). **Confirmed live (same day)**: Roy verified item (4) directly on Monsoon
  Inn (the app's pilot business) — genuine staff accounts can now sell past a keg's
  target once the toggle is off, no owner intervention needed. Worth noting for future
  sessions: Roy operates this pilot business himself using multiple real accounts
  (including Bosco's, an owner-role account) rather than a single dedicated test login —
  a screenshot from "Bosco's account" during testing is still an owner-tier view for
  gating purposes (`QS_IS_OWNER` is role-based), not necessarily what a true staff
  account sees.
- Kitchen Item Reset: fix the recurring-drift ROOT CAUSE, not another symptom-level patch
  (2026-08-11, same-day follow-up). Roy ran a Kitchen Item Reset for Kuku, received 23
  fresh chicken legs, and the "🔍 zimefichwa" diagnostic immediately showed the SAME class
  of drift again — `Imepokewa: 23, Imeuzwa: 27.5` — despite believing he'd started
  completely fresh. His own words: "why is this data still showing up, i have just reset
  everything aargh!" Traced (not guessed) by re-reading `_default_cutoff()`
  (`core/kitchen_reset_views.py`) against Roy's own stated real workflow: the tool's
  original default cutoff was the item's MOST RECENT receiving event — meant as "wipe
  since the last delivery" — but Roy's actual re-entry pattern is to deliberately BACKDATE
  catch-up sales (and sometimes the receipt itself) to a few days before the day he's
  actually sitting at the till typing them in. A backdated sale's `created_at` is
  therefore almost always EARLIER than "the most recent receipt," since the receipt is
  entered into the system strictly AFTER the paper sale it represents — meaning those
  exact backdated sales were structurally, permanently immune to ever being wiped by any
  reset run using the tool's own default: they can never be "since the most recent
  receipt" by construction. Compounding this: `_received_by_preset`/`_sold_by_preset`
  (`kitchen_board()`) are LIFETIME aggregates with no time boundary at all (by design, per
  the 2026-08-09 entry below) — so any backdated sale that survived a reset keeps counting
  forever, indistinguishable from a brand-new sale in the next "fresh" cycle. Fixed
  `_default_cutoff()` to return the item's EARLIEST-ever activity (first Transaction,
  first KitchenStockReceiptLine, or — for a KitchenBatch item — first batch's
  `received_on`) instead of the most recent one, so a reset run with the untouched default
  now genuinely means "delete every date this item has ever had a transaction, receipt, or
  batch" — matching what every real use of this tool has turned out to need. The date
  field on `kitchen_item_reset_intro.html` stays editable (a future case may genuinely
  want a partial wipe), but the page now states plainly, in Swahili, that the untouched
  default wipes everything from day one. All 12 pre-existing `KitchenItemResetTest` tests
  pass unmodified (each already passed an explicit `?cutoff=` param, never relying on the
  old default). 3 new tests — `test_default_cutoff_is_earliest_activity_not_most_recent_
  receipt` and `test_default_cutoff_for_batch_item_is_earliest_batch` lock in the new
  default computation directly; `test_default_cutoff_full_flow_wipes_pre_receipt_
  backdated_sale` is the literal end-to-end regression lock — a sale backdated to BEFORE
  the receipt (exactly Roy's real pattern) is now correctly wiped by a default-cutoff
  reset instead of surviving as invisible "earlier history." No migrations. Told to Roy
  directly: re-running the Kitchen Item Reset for Kuku right now (no need to type a date)
  will use this corrected default and should finally clear the lingering 27.5-sold figure
  for good — the receiving side (`+Pata Stok`'s preset dropdown, already wired to feed the
  same `KitchenStockReceiptLine` ledger since 2026-08-09) is unaffected by this fix and
  should work correctly for the next fresh receipt.
- Fix: no way to delete a genuine duplicate Stock Receipt while it's still OPEN
  (2026-08-11, same-day follow-up, live screenshots). Roy's own real sequence, tired and
  under time pressure at the end of the earlier reset work: used "+Pata Stok" first for 23
  chicken legs with a preset selected (this DID correctly create a `KitchenStockReceiptLine`
  — the 2026-08-09 fix — but the confirmation wasn't obvious to him in the moment), didn't
  see it reflected where he expected, assumed the tool was broken, and separately re-entered
  the SAME 23 legs via the dedicated "🧾 Stock Receipt" tool. Both entries are real,
  legitimately-created `KitchenStockReceipt` rows — not a bug in either receiving path
  itself — but together they double-counted the delivery: `_received_by_preset` (the
  per-cut visibility tracker) summed 23+23=46, and the item's real `current_balance()` was
  inflated by the same phantom 23 units, from the "Kamau" receipt's own real stock-adding
  Transaction. Roy needed to delete the wrong one immediately, but `kitchen_stock_receipt_
  delete()` (2026-08-09) — which has NEVER required CLOSED status server-side — only ever
  had its "🗑 Futa" button rendered on a CLOSED receipt card in `kitchen_board.html`; a
  freshly-discovered duplicate that's still OPEN had no delete affordance at all, forcing
  an unrelated "close first" detour that would have frozen `total_revenue()`'s window on a
  receipt about to be deleted anyway. Fixed by adding the same owner/manager-only 🗑 Futa
  button to the OPEN receipt card too, next to the existing "✓ Fungwa" (close) button — no
  backend change needed, since the view already allowed this; only the UI gap is closed.
  1 new test (`test_owner_can_delete_a_still_open_receipt`) locks in the backend contract
  the new button now relies on. No migrations. Guidance given to Roy for the CURRENT stuck
  Kuku state (not code — a real-data correction only he can make, since only he knows the
  true total cost paid): (1) delete whichever of the two receipt cards has the wrong total
  cost using the new button (both `KES 160.87/leg` and `KES 10.87/leg` look like mistaken
  entries, not the real price), (2) then use ⚖️ Rekebisha on Kuku from Stock List to set
  the balance to the TRUE physical count right now — this corrects the lingering phantom
  stock from the deleted receipt's still-standing Transaction (delete is bookkeeping-only,
  by design — it never touches the real stock-adding Transaction, so a real physical
  recount is the correct closing step here, same as every other "balance is wrong for
  reasons that can't be cleanly reconstructed" scenario in this app).
- Delegated keg management (tap/close) + accountability trail + action-row overflow fix
  (2026-08-11, live report with screenshots). Roy: a barrel physically ran out mid-shift
  with no owner present — staff had no way to tap the next sealed barrel or mark the
  finished one Imekwisha, both `tap_barrel()`/`deplete_barrel()` being strictly owner/
  manager-only; asked whether this could become a permission. Separately flagged that the
  action-button row was cramming 5 buttons ("Pima Har... + B... Im... Tupa") into an
  unreadably squeezed single row on his phone, and asked for the barrel's "opened by/closed
  by" to be shown directly on the tile. **Permission delegation**: new `UserProfile.
  can_manage_kegs` (accounts migration 0061, default False — a brand-new gate, opt-in like
  every other staff-permission toggle except `can_receive_stock`'s backward-compat
  exception) lets a trusted staffer tap a sealed barrel and deplete a finished one; both
  endpoints now check `is_owner_or_manager OR can_manage_kegs`, still requiring an open
  shift for the delegated staffer (same pattern as `record_breakage`/`add_cups`) — matches
  Roy's own framing that this needs to work mid-shift without the owner physically present.
  Deliberately does NOT extend to `discard_barrel` (Tupa, a real write-off/loss decision) or
  `receive_barrel` (Pokea, a supplier-delivery decision) — both stay owner/manager-only,
  same tier as every other financial-figure correction in this app. New toggle in
  `staff_permissions.html`, gated on `biz_profile.modules.keg`. **Accountability trail**:
  `KegBarrel.tapped_by`/`closed_by` (core migration 0160, both nullable FK to `auth.User`)
  — `tap()` already accepted a `user` param but silently discarded it; now stamps
  `tapped_by`. `close()` gained a `closed_by` kwarg, threaded through both callers
  (`discard_barrel`, `deplete_barrel`). `bar_board_api()` now `select_related`s these via a
  `Prefetch` on `keg_barrels` (avoiding N+1) and surfaces `tapped_by_name` (current tapped
  barrel) plus `last_closed_by_name` (the item's most-recently-closed barrel, found by
  `max(closed_at)` across `DEPLETED`/`RETURNED` barrels — so the trail survives the gap
  between one barrel closing and the next being tapped) — shown on the tile, visible only to
  whoever can also act on kegs (owner/manager/`can_manage_kegs` staff), matching "who's
  accountable" to "who can be held accountable." **Action-row overflow fix**: root-caused
  from the CSS, not guessed — `.keg-owner-btn` had `flex:1; min-width:0`, which lets a flex
  item shrink to literally nothing before `flex-wrap` ever triggers (wrapping only fires
  once total CONTENT width exceeds the row, and an item allowed to shrink to 0 never forces
  that), so 5 buttons all fighting for one row squeezed into unreadable truncated slivers
  instead of ever wrapping onto a second line. Two-part fix: (1) gave `.keg-owner-btn` a
  real `min-width:46px` floor (`flex:1 1 46px`) so a row that can't fit every button at a
  readable size now genuinely wraps; (2) more fundamentally, collapsed the two RARE,
  owner-only actions (✏️ Hariri cost-edit, 🗑 Tupa write-off) behind a single "⋯" button
  (`window.openKegMoreMenu`, a small self-positioning popover, same "click-outside-closes"
  pattern already used elsewhere in this file) — the two EVERYDAY actions (+ Barrel,
  Imekwisha — now also reachable by `can_manage_kegs` staff) stay directly visible on the
  main row, cutting the worst case from 5 buttons to 4 (owner: Pima/+Barrel/Imekwisha/⋯) or
  3 (delegated staff: Pima/+Barrel/Imekwisha — they never see Hariri/Tupa at all, so no ⋯
  needed for them). 12 new tests (`CanManageKegsPermissionTest`) — tap/deplete permission
  matrix (owner always, delegated staff with/without shift, plain staff blocked),
  cross-business isolation, discard/receive regression locks (still owner/manager-only
  regardless of the new toggle), and the board API's `tapped_by_name`/`last_closed_by_name`
  fields. Two migrations (accounts 0061, core 0160), both additive.
- Fix: "Kuna barrel inayouza tayari" tap error made actionable (2026-08-11, same-day
  follow-up, live screenshot). Not a bug — `tap_barrel()`'s existing "one TAPPED barrel per
  item" rule correctly refused a second barrel while the current one was still marked
  selling — but the error only said "close it first," with no pointer to HOW, and Roy's
  staff got stuck. Reworded to explicitly name the "✓ Imekwisha" button to press first.
  1 new test (`test_tap_blocked_while_another_barrel_already_selling_names_the_button`,
  added to `CanManageKegsPermissionTest`). No migrations.
- Manager delegated-oversight toggles + opening-shift stock take + Recent Sales access
  (2026-08-11): three live requests handled together. **(1) Petty cash self-service
  widened**: staff could already edit (`edit_petty_cash`) their own entry while it was
  `pending`, but not a `rejected` one, and had no delete path at all — Roy: "the only time
  they cannot make changes is if it had gone to the business owner and the business owner
  accepted it." `edit_petty_cash`'s gate loosened from `status != 'pending'` to `status ==
  'approved'` (blocks only the true point of no return); editing a REJECTED entry now
  resets it to `pending` (clearing `reviewed_by`/`reviewed_at`/`review_note`) — "sending it
  straight to the business owner side" again — and notifies owner/manager (excluding the
  actor) that it was corrected and resubmitted. New `delete_petty_cash` view, same boundary
  (blocked once `approved`, since that's also when `linked_expense`/`till_expected_cash()`
  start depending on it), also notifying owner/manager. `petty_cash_list.html` — already the
  SAME page owner and staff both use (`can_review` differentiates what's actionable), so no
  new page was needed — gained a "🗑 Futa" button alongside "✏️ Hariri" for any non-approved
  entry, "💬 Eleza" demoted to a secondary "Eleza pia" affordance rather than the only option.
  **(2) Rekebisha delegation**: new `UserProfile.can_adjust_stock` (accounts migration
  0062). Real finding while wiring the gate in: `adjust_stock_balance()` was NOT ungated as
  first assumed — it already carried `@owner_or_manager_required` — but that's a full-page
  decorator that HTML-redirects on failure, wrong for an endpoint that only ever returns
  JSON to `stock_list.html`'s `fetch()` (a non-owner/manager caller was silently getting a
  redirected HTML blob back instead of a real JSON error, undetected until this session's
  own tests caught it as `Content-Type: text/html` where JSON was expected). Removed the
  decorator; the permission check now lives inline (JSON-friendly, matching every other AJAX
  endpoint in this app) and additionally accepts `can_adjust_stock`, gated on an open shift
  for the delegated staffer. The "sio hasara halisi" (not a real loss) judgment stays
  owner/manager-only even when delegated — the backend silently ignores that flag from
  anyone else, and the checkbox itself is hidden from a delegated staffer in the modal
  (`adj-noloss-row`, now `{% if is_owner_or_manager %}`-gated; the JS that toggles/reads it
  null-guarded to match, since it no longer unconditionally exists in the DOM). Stock List's
  Rekebisha button/column widened from strict `is_owner` to `is_owner_or_manager or
  can_adjust_stock` — incidentally also fixes a pre-existing gap where even a MANAGER
  couldn't see this button at all. **(3) Waitress convert-to-debt delegation**: new
  `UserProfile.can_convert_tabs_to_debt` — `convert_tab_to_debt()`/`bulk_convert_tabs_to_
  debt()`'s existing unconditional `role == 'waitress'` block (2026-08-06) now reads `role
  == 'waitress' and not can_convert_tabs_to_debt`. Deliberately narrow: does NOT touch the
  separate, unconditional block on a waitress placing NEW credit directly at checkout
  (`bar_board()`'s `is_partial_debt_checkout` gate) — converting an EXISTING tab's already-
  served goods to debt is a different decision from originating new credit, and Roy's own
  framing ("this only affects who gets to record that it happened") drew that exact line.
  "Geuza Deni"/"→ Deni" buttons in all three tabs drawers (bar_board/kitchen_board/
  quick_sell) widened from `IS_WAITRESS ? hide : show` to `(IS_WAITRESS && !CAN_CONVERT_
  DEBT) ? hide : show`, per the tabs-drawer-parity rule. New toggle in `staff_permissions.
  html`, shown only for `role == 'waitress'` on a keg/kitchen business. 19 new tests across
  three classes (`PettyCashAccountabilityTest` +7, new `AdjustStockPermissionTest` — 6,
  new `WaitressConvertToDebtPermissionTest` — 6) — including a direct regression lock that
  the new waitress toggle does NOT bleed into the separate new-credit-at-checkout block.
  One migration (accounts 0062), additive.
- Debt-tracker item / whole-tab transfer (2026-08-11), live request: "a way for staff to
  transfer both single items and whole tabs for one customer to the other, even if that
  customer is in the debt tracker side." The existing split-item/whole-tab transfer
  mechanism (2026-07-23–25) only ever accepted `status='OPEN'` tabs on either side — once
  a tab converts to debt (`status='SETTLED'` with an unpaid balance), it had no transfer
  path at all, on either the giving or receiving end. **Model layer**
  (`core/models.py`): `BarTabEntry.split_and_transfer_locked()` and `TabTransferRequest.
  propose_whole_tab_locked()` both widened from `status != 'OPEN'` to `status not in
  ('OPEN', 'SETTLED')` for source AND destination — `entry.is_paid=False` (already checked
  first) is what guarantees a genuine, still-owed item regardless of which of the two
  live states the tab is in; VOID stays rejected either way. **The one real gap that
  needed new logic, not just a relaxed check**: `TabTransferRequest.accept()` moves an
  entry via a plain `entry.tab = dest_tab` reassignment — for an OPEN destination that's
  the whole story (nothing to sync; if that tab is ever later converted, THAT conversion's
  own entry loop sets `recipient` correctly on its own). But for a destination ALREADY
  converted to debt, no future conversion event is coming to attribute the newly-arrived
  item — `accept()` now explicitly syncs `Transaction.recipient = dest_tab.customer_name`
  and `payment_method = 'credit'` on the moved entry's transaction whenever `dest_tab.
  status != 'OPEN'`, mirroring exactly what `_convert_tab_to_debt_core()` itself does at
  conversion time, so the debt tracker immediately attributes the item to its new owner.
  **Destination picker** (`core/keg_views.py`): `_resolve_transfer_dest_tab()`'s by-name
  lookup and `transferable_tabs_api()` both widened to include debt-converted tabs (an
  `is_debt` flag added to the API response so a destination like this doesn't get
  silently duplicated by name — typing a debt customer's exact name now correctly finds
  their existing debt tab instead of opening a second one, same auto-detect-by-name
  guarantee the cross-counter merge feature already established). All three tabs
  drawers' destination dropdowns (`bar_board.html`, `kitchen_board.html`,
  `quick_sell.html`) now show a `[DENI]` tag next to a debt-converted customer's name.
  **New entry point directly on the debt page** (`core/debt_views.py` +
  `templates/core/customer_debt_profile.html`): new `_txn_tab_entry(txn)` helper (same
  try/except-on-a-reverse-OneToOne pattern as the pre-existing `_txn_transfer_note()`) —
  a transaction with no `BarTabEntry` behind it (a direct Quick Sell credit sale, never
  on a tab) has nothing transferable this way, a documented, narrower scope than "every
  possible debt origin," matching the feature's own existing BarTabEntry foundation
  rather than inventing a second, parallel mechanism. `customer_debt_profile()` now
  annotates each unpaid transaction with `tab_entry_id`/`source_tab_id` and computes a
  `transferable_tabs` list grouped by originating tab (a debt customer can have more than
  one, if several of their tabs were converted over time) — a new per-row "🔀 Hamisha"
  button next to the write-off button, plus one "🔀 Hamisha Tab Yote" button per distinct
  source tab above the Unpaid Transactions table, both opening a new shared
  `#debtTransferModal` that reuses the exact same `split_and_transfer_entry`/
  `transfer_whole_tab` endpoints and `transferable_tabs_api` picker the live tabs drawers
  already use — no new backend mechanism invented for the debt page, only a new UI
  surface calling the same one. Always a FULL move from this page (item mode sends
  `paid_amount=0`) — no partial-split UI here, unlike the richer modal in the live tabs
  drawers, since "the source customer keeps part of it" doesn't really apply once the
  goods are already debt. 24 new tests (`DebtTrackerTransferTest`) — debt-converted
  source transferable, the `accept()` recipient-sync onto an already-debt destination
  (and the regression lock that an OPEN destination is correctly left untouched), full
  debt-to-debt item and whole-tab transfers verified end-to-end against
  `_get_customer_debt_data()`'s own `outstanding` figure on both sides, a still-OPEN
  destination correctly staying non-debt until it's later converted in its own right
  (proving the "no sync needed, conversion handles it" design claim rather than just
  asserting it), VOID-source rejection, the debt page's own context annotations and
  template rendering (including a direct regression lock that a tab-less direct credit
  sale renders no transfer button at all), two full HTTP round-trips through the real
  `split_and_transfer_entry`/`transfer_whole_tab` endpoints, the by-name duplicate-tab
  guard, `transferable_tabs_api`'s `is_debt` tagging, and a station-scoping regression
  lock (a bar-only staffer still cannot target a kitchen-only tab as a transfer
  destination, debt or not). No migrations — every change reuses existing model fields.
- Kitchen preset visibility — restock anchor (2026-08-12). Live root-cause diagnosis with
  Roy (Monsoon Inn), traced from a real diagnostic-tap screenshot, not guessed. After
  properly using the Kitchen Item Reset tool (built the day before) with a partial cutoff
  — deliberately preserving genuinely old sales/revenue history he did NOT want deleted —
  Roy received a fresh 23-unit Full Chicken Leg delivery and the Kuku tile still hid every
  preset. Root cause: `_received_by_preset`/`_sold_by_preset` (`kitchen_board()`,
  `core/kitchen_views.py`) are a true LIFETIME sum with no cutoff at all — by design, these
  power the "is there stock to sell" tile-visibility gate, completely separate from any
  reset's own cutoff. The `tracks_stock_of` tether (Half Chicken Leg → Full Chicken Leg,
  added AFTER some of those old sales already happened) retroactively pulls old, deliberately-
  preserved Half Chicken Leg sales into the Full Chicken Leg anchor's running total the
  moment the tether exists, regardless of how long ago they happened — permanently
  suppressing "remaining" for a fresh restock unless the new stock alone outweighs ALL
  sold-ever history for that anchor. This conflated two genuinely separate concerns: (1)
  permanent revenue/COGS history, which must never be silently erased, and (2) a simple
  "is there physical stock right now" flag, which should be resettable without touching (1).
  New `ItemPortionPreset.restock_anchor_at` (migration 0161, nullable DateTimeField, pure
  visibility cursor — never read by any revenue/cost/analytics code, only by the tile-
  visibility gate) — when set on an anchor preset (never a tethered one; `stock_tracking_
  anchor_id()` never resolves to a tethered preset's own id), `_received_by_preset`/
  `_sold_by_preset` re-derive that ONE anchor's totals from only receiving/sales dated
  on/after the anchor timestamp, overriding the lifetime sum computed for every other
  anchor (which stays byte-for-byte unchanged when `restock_anchor_at` is null — the
  default, so this is a fully backward-compatible addition). Two ways to set it, both
  owner/manager only: (1) `kitchen_item_reset_confirm()` (`core/kitchen_reset_views.py`)
  now ALSO stamps `restock_anchor_at = cutoff_dt` on the item's own anchor presets as a
  side effect of its existing destructive wipe — the natural integration point when the
  wipe and the visibility reset are happening together anyway; (2) new, deliberately
  lighter, NON-destructive endpoint `reset_preset_restock_anchor()` (`/kitchen/item/<id>/
  reset-restock-anchor/`, `core/kitchen_views.py`) stamps `restock_anchor_at = now()` with
  zero deletion — no backup, no typed confirmation, since nothing is destroyed — for
  exactly Roy's actual situation: the fresh stock was ALREADY correctly received, nothing
  needed erasing, only the stale visibility confusion needed clearing. New "📍 Weka Alama
  Mpya" button on the Kuku tile (`kitchen_board.html`), owner-only, shown right alongside
  the existing "🔍 N zimefichwa" diagnostic whenever there's something hidden to fix — a
  plain `confirm()` (not the destructive Anza Upya's typed-name flow) explaining that
  nothing gets deleted. Idempotency-guarded (`claim_checkout_token`, matching every other
  checkout-shaped endpoint in this app). 13 new tests (`ResetPresetRestockAnchorTest` — 7;
  3 more added to `KitchenItemResetTest` for the reset-confirm integration, including a
  direct end-to-end reproduction of Roy's exact scenario: preserved older sold history
  co-existing with a fresh receipt, correctly excluded from visibility once the anchor is
  stamped). One migration (0161, additive).
- Kitchen batch/bunch backdating gap — Chipo (2026-08-12, same-day follow-up). Roy caught
  both halves of a real gap while re-entering his batch's history: "no matter how much i
  select and backdate in [checkout] has taken the sale into account as if it is today's,"
  and separately "+Pata Stok then chipo from the raw potatoes backdate entry of 7th August
  because there is actually no way to do that." Traced and confirmed both, exactly as
  reported — no guessing. **(1) Sale-side**: the "⏰ Haya ni Mauzo ya Nyuma" backdate
  toggle (built 2026-08-09) was only ever wired into `_kitchen_checkout()`'s plain
  portion-item branch — `KitchenBatch.record_sale()` and `ProduceBunch.record_sale()`/
  `record_sale_locked()` had NO `created_at` parameter at all, so a Chipo or grill-batch
  sale always stamped "now" regardless of what the checkout form's date was set to (the
  portion branch's own comment already said as much: "batch/bunch record_sale() have no
  created_at param" — this fixes exactly that gap). Both methods gained an optional
  `created_at=None` kwarg (default behavior byte-for-byte unchanged); `_kitchen_checkout()`
  now threads the SAME `kb_backdated_at` already computed for the portion branch into both
  new call sites, gated identically (`kb_backdated_at and not active_tab` — never for a
  Tab/Deni sale, matching the existing cash/mpesa-only rule). No frontend change needed for
  selling — `kbBatchSell()`/grill-batch tiles already route through the shared cart →
  checkout flow that already carries `backdated_at`; only the backend was silently ignoring
  it. **(2) Receiving-side**: `KitchenBatch.open_batch()` had no way to backdate the batch
  itself — `received_on` (a real field on the model) was never settable, always defaulting
  to today; the raw-material Draw transaction it creates (for a `raw_material_source`-linked
  item like Chipo) had the identical gap. New optional `received_on=None` kwarg — when
  given, stamps `batch.received_on` AND the Draw transaction's `created_at` to the same
  date, so both halves of one physical event agree (matters for the raw item's own
  `avg_daily_issues()`/reorder-alert history, not just the batch's own P&L). `kitchen_
  receive()`'s `kitchen_batch` mode (both the raw-material-draw and plain-manual-cost sub-
  paths) now reads an optional `received_on` POST field, parses it defensively (blank/
  invalid silently falls back to today, matching Kitchen Stock Receipt's own established
  convention), and passes it through. New "Tarehe (kwa default: leo)" date input added to
  the "+Pata Stok" modal's kitchen-batch fields section (auto-filled to today on open,
  editable), threaded into `submitReceive`'s POST body — the exact same UX pattern Kitchen
  Stock Receipt's own "Ilipokewa Tarehe" field already established for portion items,
  applied here for parity. 17 new tests (`KitchenBackdatedCheckoutTest` +5 — batch and bunch
  backdated-sale regression locks mirroring the pre-existing portion-item ones exactly, plus
  the tab-exclusion and no-backdate-param baselines; new `KitchenBatchOpenBatchReceivedOnTest`
  — 6, covering both `open_batch()` directly and the full `kitchen_receive()` HTTP round-trip
  for draw/manual modes, blank and invalid date fallback). No migrations — `received_on`/
  `created_at` were both already real, existing fields; only the write path was missing.
- Raw-material receipt now shows the finished product's own sales + backdate label fix
  (2026-08-12, same-day follow-up). Two more Chipo asks from Roy after the backdating fix
  above. **(1)** "ensure the chipo receipt tracks sales as well, as you can see I have
  sold on it and it has reflected in the tile but not in the receipt." Traced and
  confirmed CORRECT-BUT-CONFUSING behavior, not a bug: `KitchenStockReceipt.total_revenue()`
  sums Issue-transaction revenue for the RECEIPT'S OWN item(s) — for a raw-material receipt
  (Raw Potatoes), that will structurally always be KES 0, since the raw item itself is
  never sold directly, only drawn into a batch (`type='Draw'`, never revenue). Chipo's own
  sales genuinely were already reflected — on Chipo's own tile (Gharama/Mapato/Faida),
  which reads directly from `KitchenBatch.revenue_collected`/`cost_total` and was already
  correct — but nowhere near the Raw Potatoes receipt card Roy was actually looking at.
  Deliberately did NOT attempt to make the receipt's own `total_revenue()` include Chipo's
  sales directly (would conflate two genuinely different revenue streams into one number,
  the same precision trap this file's own 2026-08-11 entry already hit and was explicitly
  told to abandon for a *same-item* case — this is a *cross-item* case and doesn't need
  that same fragile matching at all). Instead, `_kitchen_stock_receipt_to_dict()` now adds
  a `raw_material_for` list — for each receipt line whose item is a `raw_material_source`
  for one or more `is_kitchen_batch` items (`item.derived_batch_items`), sums cost/revenue
  across that finished item's currently-OPEN `KitchenBatch` row(s) (already-correct,
  already-live numbers, no new computation) and returns them as a clearly separate,
  clearly-labelled block. Kitchen Board's Stock Receipt cards (both open and closed) now
  render a "→ Chipo (batch N iliyo wazi): Gharama KES X · Mapato KES Y · Faida KES Z" line
  under the raw material's own Gharama/Mapato/Faida row — visually distinct, never summed
  into the receipt's own total. A closed/depleted batch is excluded (only "current state"
  is shown, matching what the Chipo tile itself displays). **(2)** "if i backdate and
  choose a date there at idadi iliyopokelewa in chipo, it should ask me idadi iliyopokelewa
  hiyo siku not idadi iliyopokelewa leo" — the raw-material draw field's label ("Kiasi
  Ulichotumia Leo") was static text, always saying "Leo" (today) regardless of the date
  just picked in the new `received_on` field from the same-day backdating fix, confusing
  when entering several past days' bucket counts in order. New `_kbUpdateDrawQtyLabel()`
  (`kitchen_board.html`) reads the date field's current value and rewrites the label to
  name that actual date (`Kiasi Ulichotumia 07/08` etc.), falling back to "Leo" only when
  the date is genuinely today; wired to the date field's `input`/`change` events and to
  the modal's own item-selection handler so it's correct immediately on open, not just
  after the user touches the date field once. 4 new tests
  (`KitchenStockReceiptRawMaterialForTest`) — empty when no open batch exists, correct
  cost/revenue/profit surfaced for a linked open batch, a depleted batch correctly
  excluded, and an unrelated item's receipt never shows the block. No migrations.
- Kitchen Board tile silently hid a second open batch (2026-08-12, same-day follow-up).
  Roy caught a real mismatch: the new "→ Chipo" line on the Raw Potatoes receipt showed
  Mapato KES 0 for a batch, while the Chipo tile right below it showed a Faida implying
  KES 100 had already been sold. Traced to a genuine, previously-invisible bug:
  `buildKitchenBatchGrid()` (`kitchen_board.html`) has always read only `open_batches[0]`
  (the newest) — any OTHER simultaneously-open batch for the same item was silently
  dropped from the tile entirely, its own cost/revenue never shown anywhere. Since Roy
  had tapped "+Pata Stok" for Chipo more than once while troubleshooting earlier in this
  session, two batches ended up genuinely OPEN at once; the tile showed one, my new
  `raw_material_for` aggregate (which correctly sums every OPEN batch matching a raw
  item, not just the newest) surfaced the OTHER one — both numbers were individually
  correct, just for two different rows, with no way to see that from the tile alone.
  **First attempt, reverted same session**: added a KegBarrel-style "only one open batch
  per item" guard to `KitchenBatch.open_batch()` — wrong call, caught immediately by the
  pre-existing `KitchenBatchOpenBatchDrawTest.test_sequential_draws_deduct_balance_
  correctly`, whose own docstring says the multi-pot case (more than one pot of chips
  genuinely cooking at once on a busy day) is a **deliberate, already-tested, allowed**
  scenario — unlike KegBarrel, where only one barrel is ever physically tapped at a time.
  Reverted the guard; the real fix belongs entirely in the tile, not in blocking a
  legitimate business scenario. **Real fix**: `buildKitchenBatchGrid()` now shows an
  owner-only warning block for any batch beyond the first — "⚠️ N batch nyingine iko/ziko
  wazi, haionekani/hazionekani hapo juu" — listing each one's own Gharama/Mapato/Faida with
  direct "✓ Imekwisha"/"🗑 Tupa" buttons (reusing the existing `kbDepleteBatch`/
  `kbDiscardBatch` functions unchanged, just given the extra batch's id instead of
  `ob[0]`'s). The backend already sent the full `open_batches` array all along — this was a
  frontend-only fix, no new endpoint or field needed. 1 new test
  (`test_multiple_open_batches_summed_correctly`, on `KitchenStockReceiptRawMaterialForTest`)
  locking in that `raw_material_for` correctly sums cost/revenue across multiple open
  batches rather than only the first — plus the full pre-existing `KitchenBatchOpenBatchDrawTest`
  suite re-run and confirmed passing unmodified, proving the multi-pot scenario is still
  fully supported. No migrations.
- Split a direct sale straight into a customer's debt, dated to the original sale
  (2026-08-12, same-day follow-up). Roy: "there is an order for chipo that was sold on
  7th, the customer paid 50 mpesa and 50 went to debt, so i am not sure how to backdate
  that so what i have done is put it as mpesa then went to recent sales in the food tab
  split it into two but then, there is no way to transfer the remainder into debt for
  that customer for that specific day." The existing "✂️ Gawanya" split-payment
  correction (`Transaction.split_payment_method_locked()`, 2026-07-26) only ever allowed
  splitting between `cash` and `mpesa` — no way to route part of an already-recorded
  direct sale to credit at all. Widened `new_method` to also accept `'credit'`, requiring
  a new `recipient` param (the customer's name) in that case; the split-off sibling
  transaction already copies `created_at` from the original (pre-existing behavior) — so
  a debt split off from a BACKDATED sale is automatically, correctly backdated too,
  answering Roy's exact question. `split_transaction_payment_method`
  (`core/keg_views.py`) resolves/creates the `Customer` record the same safe way this
  codebase always does (`filter(name__iexact=...).first()`, never `get_or_create` — see
  this file's own documented `MultipleObjectsReturned` history), sets
  `credit_approved=True` matching every other auto-created-customer call site, and sends
  a best-effort debt-confirmation SMS worded with the ACTUAL historical sale date
  (`sale_when`, derived from the original transaction's own `created_at`), never "today"
  — deliberately no `evaluate_credit()` gate here, same reasoning already established for
  tab-to-debt conversion: this is recording a historical fact (the goods already sold),
  not originating new credit. New "🤝 Deni" button added to the Recent Payments panel's
  direct-sales section in all three counters (`bar_board.html`, `kitchen_board.html`,
  `quick_sell.html`, per the tabs-drawer-parity rule) next to the existing "✂️ Gawanya" —
  two `prompt()`s (amount owed, then customer name), reusing each file's own established
  POST helper (`post`/`qsPost`, form-encoded) and toast function. 15 new tests
  (`DirectSalePaymentSplitToDebtTest`) — recipient required for a credit split, the debt
  sibling's `created_at` matches the original (backdated) sale exactly, Customer
  creation + case-insensitive reuse, rejection without a recipient leaves the original
  transaction untouched, and the resulting debt correctly appears in
  `_get_customer_debt_data()`'s `outstanding` figure — plus the full pre-existing
  `DirectSalePaymentSplitTest` suite re-run and confirmed passing unmodified (the
  cash/mpesa split path is completely untouched). No migrations.
- Backdate a direct Deni sale at checkout, no split-correction step needed (2026-08-12,
  same-day follow-up). Roy's very next ask, right after the split-to-debt feature above:
  "ensure that for backdating i can put customer in debt for that day without it having
  to go to sales just right there on the selling part." Quick Sell's whole-cart catch-up
  backdate toggle (2026-08-07) and Kitchen Board's own (2026-08-09) were both deliberately
  gated to cash/mpesa only — their own comments explicitly said "never Tab/Deni... Credit/
  Deni [is] its own separate, more sensitive flow" — meaning a direct Deni (credit)
  checkout had NO way to post under a historical date; the only path was to sell as cash
  first, then use the "🤝 Deni" Recent Payments correction from the fix above. Widened
  both counters' backdate gate to also accept `'credit'` — but carefully NOT `'tab'`/
  `'food_tab'`/`'bar_tab'`, which remain excluded since an open running bill is an
  ongoing thing, not something that already "happened" on a fixed past date. **Quick
  Sell's own subtlety**: its `payment_method_qs` remaps BOTH the `'tab'` and `'credit'`
  radio values to the stored value `'credit'` (a Tab's underlying Transaction is
  `payment_method='credit'` until settled) — so the gate had to check the true
  pre-remap `payment_method_raw` (`core/views.py`), not `payment_method_qs`, to tell a
  direct Deni sale apart from an open Tab. Kitchen Board's `payment_method` is already
  the raw, unambiguous value (`'credit'` vs `'food_tab'`/`'bar_tab'` are distinct strings
  throughout `core/kitchen_views.py`), so no equivalent remapping issue there — its
  `kb_backdated_at` already threads unconditionally into all three sale-creation branches
  (plain portion-item, `KitchenBatch.record_sale`, `ProduceBunch.record_sale_locked`, all
  three gained a `created_at` param earlier this same day) via the existing `kb_backdated_at
  and not active_tab` check, which was already correctly `None` for a direct credit
  checkout — so widening the initial parse gate was the only backend change needed there.
  **Frontend had a SECOND, separate gate in both files that would have silently defeated
  the fix if missed**: both `quick_sell.html`'s submit handler and `kitchen_board.html`'s
  `doCheckout()` only ever READ the backdate input's value into the POST body when
  `pm === 'cash' || pm === 'mpesa'` — the row-visibility toggle is a completely different
  code path from what actually gets sent, so widening only the visibility rule (showing
  the toggle button for Deni) without also widening this submit-time read would have shown
  staff a working-looking backdate field that silently did nothing. Both fixed alongside
  the visibility toggles. Bar Board was NOT touched — its own keg-cart checkout never had
  ANY backdate support to begin with (only Quick Sell and Kitchen Board got that feature),
  so this is a pre-existing, separate gap, not part of this fix. 4 tests rewritten from
  "backdate ignored for credit" to "backdate now applies to a direct credit sale" (one
  each for Quick Sell's plain checkout, Kitchen's plain portion-item, and a new one for
  Kitchen's `KitchenBatch` branch — matching Roy's own literal Chipo-on-Deni scenario);
  2 new "still ignored for an open Tab/food_tab" regression locks added alongside them so
  the excluded case stays excluded. No migrations.
- Live 502 investigation, third occurrence — root cause finally identified with hard
  evidence (2026-08-12). Roy hit the same "HTTP health check failed (timed out after 5
  seconds)" signature as the two prior incidents (2026-08-09, 2026-08-11), this time
  clearly unrelated to a deploy (the last deploy had been live and stable for 5 hours).
  Investigated the SMS-blocking-request-threads theory this session's own history had
  already flagged as a candidate but never confirmed with numbers: inspected the installed
  `africastalking` SDK directly (`africastalking.Service.DEFAULT_TIMEOUT_S`) and confirmed
  its HTTP calls carry a real, built-in timeout of `(3.05, 9.05)` seconds
  (connect, read) — so a single `send_sms_notification()` call can legitimately block its
  calling thread for up to ~12 seconds before giving up. Grepped for call sites: ~81 across
  the codebase, the large majority synchronous in the request path (only
  `notify_transaction_async()`'s own background-thread dispatch is truly non-blocking).
  Cross-checked the actual deployed gunicorn config (`Procfile`/`render.yaml`, confirmed
  matching the real dashboard Start Command Roy fixed on 2026-08-09/10) — still
  `--workers 1 --threads 8`, meaning ONE process, 8 total threads, serving every page load,
  every checkout, every SMS send, AND Render's own `/health/` check simultaneously. A burst
  of just a handful of SMS-triggering actions landing close together (debt confirmations,
  cash-payment-request notices, shift alerts — all real, common events on a busy shift) can
  occupy every one of those 8 threads for up to 12 seconds each, leaving none free to answer
  the health check inside its 5-second window — Render marks the instance unhealthy,
  restarts it, and the customer sees a 502 for the ~30-60s it takes to recover (matching the
  "Instance failed" → "Service recovered" pair in Roy's own Events log screenshot exactly).
  **Fix applied (stopgap, Roy approved)**: bumped `--workers` 1 → 2 in both `Procfile` and
  `render.yaml` (16 total threads across 2 independent processes instead of 8 in one) —
  doubles the buffer against this exact failure shape with zero application-code risk.
  Explicitly flagged as NOT the real fix — that's making every SMS send genuinely
  non-blocking (same background-thread pattern `notify_transaction_async()` already proves
  works), which touches ~80 call sites and needs its own dedicated, carefully-tested pass,
  not a same-session rush. **Reminder for next time this recurs**: per the SAME config-drift
  warning already documented in `render.yaml`'s header comment (2026-08-09/10) — this
  Procfile/render.yaml change does NOT by itself change what's running in production; Roy
  must also update the Render dashboard's own Start Command field to match (worker count
  1 → 2), same as he had to do for the original thread-count fix.
- Same-incident follow-up (2026-08-12) — shortened SMS/email network timeouts. Roy
  approved going further than the worker bump alone. Inspected both SDKs directly rather
  than guessing: Africa's Talking (`africastalking.Service.DEFAULT_TIMEOUT_S`) defaults to
  `(3.05, 9.05)`s — confirmed the ~12s worst-case figure already cited in this file's own
  incident writeup. Resend's default HTTP client (`resend.http_client_requests.
  RequestsClient.__init__(timeout=30)`) turned out to be a WORSE, previously-undiscovered
  risk — a single slow email send could block a thread for up to 30 seconds, six times
  Render's 5-second health-check window, on its own. Both `send_sms_notification()` and
  `send_email_notification()` (`core/notifications.py`) now pass an explicit, much
  shorter timeout to the underlying SDK call — SMS to `(3, 5)` (≈8s worst case, down from
  ~12s) via `sms.send(..., timeout=(3, 5))`, email to `8` seconds (down from 30s) via
  `resend.default_http_client = RequestsClient(timeout=8)`. Deliberately safe: audited all
  81 `send_sms_notification()` call sites first and found only 2 (`debt_views.py`'s
  `send_debt_reminder`, `receipt_views.py`'s receipt-share SMS) actually read the
  `(success, detail)` return value to shape an immediate user-facing response — both are
  deliberate, low-frequency, user-initiated "send now" taps (not automatic side-effects
  fired during every checkout), left completely untouched; every other call site already
  treats a failure as best-effort (logged, never blocks the main flow), so failing a bit
  faster on a genuinely slow/unreachable endpoint is a pure improvement, not a behavior
  change anywhere. Deliberately did NOT touch M-Pesa/Daraja's own `requests` timeouts
  (`core/mpesa.py`, 15-30s, already explicit) — those are synchronous, user-initiated
  payment flows where the whole feature legitimately depends on waiting for Safaricom's
  response; shortening them is a different, higher-stakes tradeoff outside this fix's
  scope. Also deliberately did NOT attempt the fuller "make every SMS send truly
  non-blocking via a background thread" version of the fix — traced why that's riskier
  than it looks: `notify_transaction_async()`'s existing background-thread pattern is
  already the suspected source of the recurring, mostly-harmless "database table is
  locked" tracebacks visible in this project's own test-suite output (a background thread
  querying the DB while the test's own wrapping transaction is still open) — doing the
  same for the other ~79 call sites would meaningfully amplify that test-suite flakiness
  risk for a live, money-critical app, and deserves its own dedicated, carefully-tested
  pass rather than a blanket same-incident sweep. 2 new tests
  (`NotificationTimeoutTest`) — mock-verify the shortened timeout is actually threaded
  through to each SDK call, not just documented in a comment. No migrations.
- Same-incident, same-day escalation — the deeper fix (2026-08-12). The shortened-timeout
  fix wasn't enough on its own: a health-check-timeout 502 recurred 7 minutes after a
  stable deploy with the worker bump confirmed live (Roy screenshotted the dashboard
  Start Command to prove it), meaning real customers were still hitting this during
  active use. Roy: "just commence with the deeper fix." Built `send_sms_notification_
  async()`/`send_email_notification_async()` in `core/notifications.py` — true
  fire-and-forget wrappers (`threading.Thread(daemon=True).start()`) around the existing
  sync functions. Re-examined the DB-threading-flakiness concern that made this feel
  risky earlier the same day and found it doesn't actually apply here:
  `notify_transaction_async()`'s own background worker is risky specifically because it
  does `Transaction.objects.get(id=...)`/`Business.objects.get(id=...)` — real ORM
  queries racing a test's own wrapping transaction — but `send_sms_notification()`/
  `send_email_notification()` themselves make ZERO database calls, only a pure external
  HTTP request; a background thread calling either is therefore no more DB-risky than
  the SMS/email calls the test suite already runs synchronously and fails against on
  every run (the recurring, already-benign "ProxyError" log noise). Audited every call
  site with the return value in mind (81 SMS, 16 email): exactly 3 read it to shape an
  immediate response (`debt_views.py`'s `send_debt_reminder`, `receipt_views.py`'s
  SMS-share and email-share buttons on the public receipt page) — all three deliberate,
  low-frequency, user-initiated "send now" taps, left calling the synchronous originals
  unchanged. Every other call site (~94 total) converted to the `_async` sibling via a
  scripted sweep, verified in two independent passes: a line-based check (correctly
  skipped the 3 exceptions and 2 internal worker-thread calls that must stay sync) and,
  after that missed two files, a proper AST-based scope checker (walks every `Call` node,
  tracks `Import`/`ImportFrom`/`FunctionDef`/`Assign` bindings per scope) that caught
  every remaining unbound reference precisely. **Real bug the AST checker caught, that a
  regex/grep pass had missed**: `core/performer_views.py` and `core/stock_take_views.py`
  both import from `core.notifications` using a **multi-line** parenthesized `from ...
  import (` statement — the first line-based import-fixing pass only matched a line
  containing the substring `"notifications import"`, which is on a DIFFERENT line than
  `send_sms_notification` itself in a multi-line import, so those two files' call sites
  were renamed to the `_async` variant but the import never brought the new name in,
  causing `NameError: name 'send_sms_notification_async' is not defined` — first
  surfaced by a genuine full-suite test failure (`StockTakeVarianceAttributionTest.
  test_both_staff_notified_appropriately`), traced to the real root cause rather than
  dismissed as one more instance of this file's own well-documented flaky-test class
  (confirmed by re-running the test in isolation with a stack trace, not by assumption).
  Both import statements fixed; `core/procurement_views.py`'s own multi-line import
  turned out to be a false alarm — the file has TWO import statements in the same
  function (an outer one lacking the async name, and a closer, already-correct one
  actually in scope for the real call site) — verified by direct code reading, not
  patched needlessly. 5 new tests (`NotificationAsyncDispatchTest`) — both wrappers
  return well under a second even while the underlying send is artificially blocked
  (proving the caller genuinely never waits), both actually dispatch the real function
  from the background thread (polled with a short timeout, not a fixed sleep), and an
  exception inside the background send is caught and logged, never raised into the
  caller. 2 pre-existing tests (`DailySummaryIdempotencyTest`) updated to patch the
  `_async` wrapper directly instead of the sync function it calls from inside its own
  worker thread — patching the sync version raced the assertion against a background
  thread that might not have run yet; patching the wrapper itself keeps the test
  deterministic. Full 1810-test suite (plus these additions) run clean before push. No
  migrations.
- Fix: backdated sales silently counted as "today's" revenue — Transaction.date vs
  created_at drift (2026-08-12, live report). Roy: "I backdated everything from 7th to
  11th... kitchen staff has not yet made sales but the system is showing as if it had."
  Root cause: `Transaction.date` (`models.DateField(default=timezone.now)`) and
  `Transaction.created_at` (`models.DateTimeField(default=timezone.now)`) are two
  INDEPENDENTLY-defaulted fields meant to represent the same moment — in the ordinary
  case both evaluate `timezone.now()` at the same instant and naturally agree, but every
  backdating feature built this session (Quick Sell's whole-cart catch-up toggle
  2026-08-07, Kitchen Board's own portion/batch/bunch backdate 2026-08-09, and today's
  direct-Deni backdate widening) only ever overrode `created_at=` — `date` kept its own
  independent default of "today, the actual day it was entered," silently diverging from
  the historical date `created_at` correctly represented. Kitchen Board's own "Leo"
  (today) revenue tile — `kitchen_revenue_today`/`kitchen_revenue_lines` in
  `kitchen_board()`, plus its live-poll sibling `kitchen_stats_api()` — filtered on
  `date=timezone.localdate()`, not `created_at`, so a whole week of backdated catch-up
  sales entered today all silently counted as if they'd just happened. **Two-layer fix**:
  (1) READ side — both queries switched from `date=` to the same `created_at__gte=
  station_revenue_window_start(business, is_kitchen=True)` window `home()`'s own
  dashboard tile already uses (`core/views.py`) — the two now can never show different
  numbers either, closing a smaller pre-existing inconsistency for free; also added the
  `.exclude(payment_method='void')` `home()`'s version already had but this hand-rolled
  query was missing. (2) WRITE side, the actual root-cause fix — every backdating call
  site now sets `date=timezone.localtime(created_at).date()` alongside `created_at=`, so
  the two fields can never drift apart again for any NEW transaction: `ProduceBunch.
  record_sale()`, `KitchenBatch.record_sale()`, `KitchenBatch.open_batch()`'s raw-
  material Draw transaction (all `core/models.py`), Quick Sell's checkout
  (`core/views.py`), and Kitchen Board's own plain portion-item checkout
  (`core/kitchen_views.py`). Confirmed by direct trace that `split_payment_method_
  locked()` (the "✂️ Gawanya"/"🤝 Deni" split-payment corrections) was ALREADY correct —
  it copies `date=orig_txn.date` alongside `created_at=orig_txn.created_at`, so a split
  off an already-correctly-dated transaction stays correct automatically. **Backfill for
  historical rows** (every backdated transaction entered before this fix, across every
  business on the platform, not just Kitchen — the same field is read by `daily_sales()`
  (`/daily/`, an explicit date-picker report where `date` genuinely is the right field to
  filter on, unlike Kitchen Board's live tile) and the emailed daily summary
  (`send_daily_summary()`), both of which would show a historical backdated entry under
  the WRONG day until corrected): new `backfill_transaction_date_from_created_at`
  management command, same `--dry-run`-first convention as every other backfill in this
  app, corrects `Transaction.date` to match `timezone.localtime(created_at).date()` for
  every row where the two currently disagree (found via direct code trace that no
  application code anywhere intentionally sets them to different values — the drift is
  purely this bug, not a deliberate design choice) — safe to re-run, platform-wide, run
  once via Render's Shell tab. 8 new tests (`TransactionDateCreatedAtSyncTest` — the
  literal reported bug reproduced end to end plus a regression lock that a genuinely-
  today sale still counts; `TransactionDateBackfillCommandTest` — dry-run, correction,
  idempotent re-run). One new migration-free management command file; no model changes.
- Revert: "today's revenue" dashboard tiles back to a plain daily reset, matching a real
  Till statement (2026-08-12, live design-reversal request). Immediately after confirming
  the `Transaction.date`/`created_at` backfill had worked, Roy reported Kitchen's own tile
  STILL showed a stale KES 600 with zero real same-day sales — traced this time to a
  SEPARATE, correctly-working mechanism: `station_revenue_window_start()`'s confirm-
  anchored design (built 2026-08-01/02, specifically at Roy's own earlier request, to stop
  a station's revenue from resetting before anyone had actually signed off on the shift
  behind it) was showing a shift confirmed hours earlier that day — working exactly as
  designed, evidenced by his own Shift History screenshot ("Shavel Atis... CONFIRMED...
  Imethibitishwa na Bosco — 12 Aug, 14:52"), just no longer matching what he actually
  wanted from the number. His own words, verbatim, explaining the reversal: "regardless of
  monsoon being 24hrs, the revenue specifically should go by day regardless of shift
  presence, like shifts and counter cash modals are very fine as they are, but let us make
  revenue realistic since it says today's revenue... it goes hand in hand with the way
  Safaricom's till mpesa portal usually is whereby it usually displays revenue per day."
  `station_revenue_window_start(business, is_kitchen, now=None)` simplified to a single
  line — always `timezone.localtime(now).replace(hour=0, minute=0, second=0,
  microsecond=0)` — dropping the confirm-anchor lookup (and, before that, the 2026-07-31
  open-shift-spans-midnight extension) entirely; both prior designs' reasoning kept in the
  docstring for context, not deleted, matching this file's own convention for a deliberate
  reversal. `station_revenue_window_info()` follows suit: `anchor_label` is now always
  "Tangu usiku wa manane wa leo," and the pending-shifts breakdown lists every shift
  overlapping TODAY regardless of confirm status (dropped the `.exclude(status=
  'CONFIRMED')` filter, since confirm state is no longer a signal for this figure at all).
  **Deliberately, explicitly NOT touched** — Roy's own instruction, "shifts and counter
  cash modals are very fine as they are": `till_expected_cash()` (the SEPARATE "Kiasi
  Kinachotarajiwa Kwenye Counter Sasa" continuous-till mechanism, anchored on
  `Shift.closing_cash_counted`/`TillCount`), and every shift open/close/confirm mechanic
  itself — confirmed via grep that nothing in the shift-close/counter-cash code paths calls
  either rewritten function. `home.html`'s owner-only revenue disclosure panel dropped its
  now-meaningless "⏳ shift not yet confirmed... Nenda uthibitishe →" prompt (confirming a
  shift no longer changes what the tile shows, so telling the owner to go confirm one was
  actively misleading) — kept the per-shift + "other revenue" breakdown list and the
  running total, unchanged in structure. `_station_reset_anchor()` (the now-unused confirm-
  anchor helper) left in place as dead code rather than deleted, per this file's own
  low-risk-cleanup discipline; two stale docstring references to it in
  `_auto_close_expired_shifts()` were left as-is (cosmetic only). 13 pre-existing tests
  across `StationRevenueWindowStartTest`, `StationRevenueWindowInfoTest`, and
  `AutoCloseRevenueContinuityTest` rewritten (not just deleted) to assert the new plain-
  daily-reset contract instead of the old confirm-anchored one, including a new explicit
  regression lock for the literal reported bug
  (`test_yesterdays_shift_never_appears_in_todays_breakdown`) and one confirming a
  CONFIRMED shift no longer moves the window at all
  (`test_confirmed_shift_does_not_change_the_window`). `HomeDashboardRevenueSurvivesMidnightTest`
  needed no changes — it already only asserted that a sale genuinely dated today counts,
  which stayed true under both designs. No migrations (pure function-body + template
  change, no schema touched).
- Four live gaps + a standing permission-parity principle (2026-08-12/13). Roy: "the staff
  never got a way of editing... counter cash entries before they get approved... even the
  waitress"; "the reconciliation... no-loss checkbox does not include [delegated staff]...
  whenever we set a permission toggle the function should be exactly as it is to the one
  who has it"; "the waitress time shift count is not counting in real time... stays at 0";
  "in the shifts section integrate a search module." **(1) Waitress navbar gap**: `petty_
  cash_list()`/`shift_history()` already scoped correctly to a non-reviewer's own entries/
  shifts — the actual gap was purely that `templates/base.html`'s `is_waitress` navbar
  block (both mobile and desktop) was the ONLY role block missing the `petty_cash_list`/
  `shift_history` links `is_kitchen_staff`/`is_staff_member` already had; she could already
  open the Petty Cash modal from Bar Board, just had nowhere to go back and edit/respond.
  Two links added, zero backend change. **(2) Standing principle, applied + audited**:
  `adjust_stock_balance()`'s "not a real loss" flag was hard-restricted to
  `is_owner_or_manager` even for a delegated `can_adjust_stock` staffer (2026-08-11's own
  deliberate design, now reversed at Roy's explicit request) — widened both the backend
  gate and the `#adj-noloss-row` template visibility to also honor `can_adjust_stock`.
  Audited every other delegated toggle in the app
  (`can_receive_stock`/`can_receive_kitchen_stock`/`can_confirm_shifts`/`can_review_petty_
  cash`/`can_convert_tabs_to_debt`/`can_approve_debt_erase`/`can_input_cost_price`/
  `can_override_restrictions`) for the same "toggle grants the action but silently
  withholds part of it" shape — found exactly one more genuine match:
  `can_manage_kegs` already let a delegated staffer `tap_barrel()`/`deplete_barrel()` but
  `discard_barrel()` (Tupa)/`receive_barrel()` (Pokea) were hardcoded owner/manager-only
  with no `can_manage_kegs` branch at all; widened both to the same two-step (owner/
  manager always, else `can_manage_kegs` + open shift) gate already used by tap/deplete,
  plus the "⋯" more-menu / "+ Pokea Barrel" button visibility in `bar_board.html`
  (Hariri cost-edit stays strictly owner-only within the same menu — a genuine financial-
  figure correction, not a withheld sub-part of "manage kegs"). Everything else audited
  turned out to be a deliberate SEPARATION between two different actions (e.g.
  `can_approve_debt_erase` only ever covers "erase a mistake," never a real write-off) —
  left unchanged, reasoning recorded in the test class docstrings. **(3) Waitress live
  shift timer**: `active_shift_api()`'s `elapsed`/"Muda" field (used for cash-variance
  accountability) is deliberately reduced by any overlapping bar/kitchen custodian shift
  for a waitress (2026-08-08 design) — since she typically works the whole time a
  custodian shift is open, this sits near zero almost her entire shift, which is what Roy
  was seeing as "stuck at 0." Rather than change what that number means (it protects her
  from being blamed for a till she isn't holding), built a SEPARATE, plain wall-clock
  "time since I opened" live-ticking timer: `waitress_screen()` (`core/order_views.py`)
  now resolves her own open `Shift.started_at` and passes it as an ISO timestamp;
  `waitress_screen.html` renders a client-side `setInterval` ticker from it (no polling
  needed); `active_shift_api()` gained `started_at_iso` on the "my shift" JSON block so
  `bar_board.html` can render the same live timer next to her existing accountability
  "Muda" text when `IS_WAITRESS` — both numbers visible side by side, neither redefined.
  Scoped to the waitress only per her own confirmation, not rolled out to every role.
  **(4) Shift search module**: `shift_history()` had zero filter params — always the last
  60 shifts, business-wide for owner or self-scoped for staff, with no date/staff/status
  filter and no grouping. Added `preset` (today/week/month) or custom `date_from`/
  `date_to`, a `status` filter, and an owner/manager-only `staff_id` filter (a non-owner/
  manager passing a foreign staff_id is silently ignored — still hard-scoped to
  `staff=request.user`, unchanged); raised the row cap from 60 to 500 only when a filter
  is actually active. Owner/manager view also gets a `grouped_rows` (staff name → that
  staff's own rows) built alongside — but deliberately kept SEPARATE from `rows` itself:
  a first draft inlined `{'is_group_header': True, ...}` marker dicts directly into the
  same flat `rows` list the template already loops (to avoid duplicating the ~200-line
  per-shift card markup), and that broke 4 pre-existing tests across three other test
  classes that iterate `resp.context['rows']` assuming every entry has a `'shift'` key —
  caught by the full suite, not by the narrower tests written for this feature alone.
  Fixed by leaving `rows` completely untouched (still the plain flat list every existing
  caller expects) and rendering `grouped_rows` as its own separate "Kwa Mfanyakazi" jump-
  link summary above the unchanged detailed list, each link anchoring to `#shift-<id>`
  on the corresponding card. New filter form (quick presets + custom range + status +
  staff dropdown) at the top of the page. 35 new/updated tests across
  `WaitressNavbarPettyCashAndShiftsLinksTest`, `AdjustStockNoRealLossPermissionParityTest`
  (rewritten from "ignored" to "honored" — a deliberate reversal, matching this file's own
  convention for updating pre-existing tests on a design change), `CanManageKegsPermission
  Test` (2 tests rewritten from "still blocked" to "now allowed" for discard/receive, 6 new),
  `WaitressLiveShiftTimerTest`, `ShiftHistorySearchTest`. Also fixed, caught by the full
  suite: two pre-existing `StationRevenueWindowInfoTest` tests hardcoded shift start times
  to "today at 8am/9am," which flaked whenever the suite happened to run before that real
  wall-clock hour (`started_at__lt=now` then filters out a shift that hasn't "started" yet
  relative to the real test-execution moment) — same bug class already documented
  repeatedly in this file (`PettyCashReviewUndoTest`, `BarZReportOverlappingShiftsTest`,
  `AdHocExpenseDayReconciliationTest`). Fixed by anchoring both tests to an explicit `now=`
  passed into `station_revenue_window_info()` instead of relying on real wall-clock time.
  No migrations. 1852 tests pass.
- Bar-side backdate-at-checkout, matching Kitchen/Quick Sell (2026-08-12/13). Roy's earlier
  request, confirmed still wanted: "the same backfill mechanism that we did for kitchen,
  create the exact same for the bar side but it should not be in the tabs drawers, put it
  independently on both boards." Confirmed via direct code read that `KegBarrel.
  record_sale()`/`record_sale_locked()` had no `created_at` param at all, unlike
  `ProduceBunch`/`KitchenBatch` (both got one 2026-08-12) — Bar Board's own keg-cart
  checkout never had ANY backdate support. Added the same `created_at=None` param,
  mirroring the exact `{'created_at':..., 'date': timezone.localtime(created_at).date()}`
  pattern from the date/created_at-drift fix. `bar_board()`'s checkout parses a new
  `bb_backdated_at` field — but ADAPTED to this board's own payment options: the keg-cart
  payment selector only ever offers Cash/M-Pesa/Tab (no standalone Deni/credit radio the
  way Kitchen/Quick Sell have), so the gate is `payment_method in ('cash', 'mpesa')`, not
  `('cash','mpesa','credit')`. Also confirmed Bar Board's checkout has NO separate "plain
  non-keg item" branch at all (unlike the plan's original assumption, based on Kitchen's
  shape) — the whole checkout is keg-cart only; non-keg items on a bar business sell via
  Quick Sell instead, already fully backdate-capable. UI: same "⏰ Haya ni Mauzo ya Nyuma"
  toggle + datetime-local input added to the checkout/cart panel itself (never the tabs
  drawer), gated to cash/mpesa exactly like the existing ✂️ Gawanya split-payment toggle
  it sits next to. 4 new tests (`BarBoardBackdatedCheckoutTest`). No migrations.
- "Mmiliki Alichukua" — owner ledger, price capture, and tab/debt transfers
  (2026-08-12/13). Roy's own framing: "the system can be able to know that a certain name
  in either orders/tabs/debt tracker is the owner... that part of mmiliki alichukua could
  have a section of its own... he gets to see every item the price the quantities...
  transference... with an ability to accept or reject... this will help him to not be
  affected by the credit window in place." Confirmed via direct code read that most of the
  requested infrastructure already existed from the 2026-08-07 sprint (`/stock/owner-
  consumption/list/`, void, cash/mpesa settle) — the genuine gaps were price capture,
  tab/debt transfers, and (per Roy, deferred — see below) STK settle. **Design decision,
  confirmed via AskUserQuestion**: built on top of the EXISTING `OwnerConsumption`
  transaction type rather than giving the owner a real `Customer`/`BarTab` record — his
  draws already live outside every `type='Issue'`-filtered revenue/debt/analytics
  aggregate by construction (`Transaction.revenue()`/`.cost()` both gate on `type !=
  'Issue'` first, same "excluded by construction" pattern as `type='Draw'`/`'Transfer'`);
  routing him through a real tab would have risked the exact revenue-leakage class this
  app's own 2026-08-02 stock-transfer redesign was built specifically to avoid. "The
  system knowing a name is the owner" is therefore NOT a Customer-name-matching heuristic
  (fragile — a real customer could share his first name) — every transfer entry point
  simply offers "🏠 Mmiliki" as its own distinct, always-available destination, backed
  directly by the OwnerConsumption mechanism. **Price capture**: `record_owner_
  consumption()` now accepts an optional `price` field, auto-filled from `item.
  selling_price × qty` when blank (editable — same pattern every other sale in this app
  uses to capture its amount), stored on `Transaction.sale_amount`; `owner_consumption_
  list.html` shows it per row. **The transfer mechanism — the key design insight that
  keeps this low-risk**: reclassifying an item between "a customer's tab/debt" and "the
  owner's draw" needs no new parallel Transaction at all — `Transaction.type` is a plain
  CharField already read by every revenue/debt/analytics query via a `type=` filter, so
  flipping an existing row's `type` between `'Issue'` and `'OwnerConsumption'` IN PLACE
  (inside a locked, atomic block) is the same "safe by construction" mechanism this app
  already trusts for Draw/Transfer types — the row instantly and correctly disappears
  from (or appears in) every existing aggregate with zero new exclusion logic to write or
  audit, and never touches `qty` (the physical item already left the shelf once, at
  original sale time — nothing deducted twice, locked in by a dedicated regression test).
  New `OwnerConsumptionTransferRequest` model (migration 0162) — deliberately NOT a reuse
  of `TabTransferRequest` (tightly coupled to `BarTabEntry`/`BarTab` on both ends), mirrors
  its lifecycle shape only (PENDING/ACCEPTED/REJECTED/CANCELLED, `batch_id` for whole-bill
  transfers, accept()/reject() cascading to every PENDING sibling sharing it — a whole-
  bill transfer resolves as ONE decision). Deliberately whole-item/whole-bill only, no
  partial split, matching Roy's own wording ("an item or bill whether one or all") — a
  materially simpler scope than the customer-to-customer split-transfer feature.
  **Direction "to_owner"** (staff or the owner reclassifies a customer's unpaid tab item
  or debt balance as the owner's own — needs the owner's own accept): `propose_to_owner_
  locked()`/`_accept_to_owner()` flip `type` from `'Issue'` to `'OwnerConsumption'`,
  clear `payment_method`, and close out the now-orphaned `BarTabEntry` (if tab-sourced)
  the same way `remove_tab_entry` already closes an entry it's finished with — reused
  `debt_views.py`'s own `txn.tab_entry` reverse-OneToOne try/except pattern (already
  established for the 2026-08-11 debt-transfer feature) rather than inventing a second
  way to detect a tab-linked transaction. **Direction "from_owner"** (owner hands an item/
  bill to a customer willing to cover it): `propose_from_owner_locked()`/`_accept_from_
  owner()` flip `type` back to `'Issue'`, set `payment_method='credit'`, and — per the
  confirmed answer — resolve an existing open tab by name or open a brand-new one via the
  existing `BarTab.create_with_credentials()` (same auto-detect-by-name guarantee the
  cross-counter merge feature already established, so this can never silently duplicate a
  customer's tab), attaching a new `BarTabEntry`. Confirmed and locked in by a dedicated
  test: this never calls `evaluate_credit()` directly (consistent with the existing,
  established rule that opening/adding to a tab is never a credit-issuance point — only
  actual new-credit checkout points are) — the already-existing, non-blocking `notify_
  owners_of_conversion_risk()` heads-up fires automatically the moment such a tab is
  LATER genuinely converted to debt, with zero new wiring needed, since that conversion
  path was never touched. **Views**: `propose_transfer_to_owner`/`propose_transfer_from_
  owner` (any staff with an open shift, or owner/manager exempt — matches Roy's "initiated
  by the staffs or him of course"), `respond_owner_transfer` (owner/manager only, either
  direction — accept/reject). **A real, pre-existing latent bug found and fixed while
  testing this** (not introduced by this feature): `ShiftEnforcementMiddleware`'s
  `_SHIFT_EXEMPT_PREFIXES` had no entry for `/stock/owner-consumption/` at all — a manager
  with no open shift hit a silent 302 redirect to `/bar/` on ANY owner-consumption action,
  including the already-existing `void_owner_consumption`/`settle_owner_consumption`
  endpoints from 2026-08-07, never just the new transfer endpoints. Found only because a
  test exercised a manager with no shift open — this is exactly the kind of oversight/
  correction action (`/debt/`, `/petty-cash/`, `/receipts/`) this middleware's own exempt
  list already carves out for the identical reason; added `/stock/owner-consumption/` to
  the same list. **UI**: `owner_consumption_list.html` gained a "⏳ Vinavyosubiri Uamuzi
  Wako" pending-to-owner section (owner/manager-only accept/reject) and a "🔀 Hamishia kwa
  Mteja" action per unsettled draw; `customer_debt_profile.html` gained a "🏠 Mmiliki"
  button next to every unpaid debt line (owner/manager-only) to propose it as the owner's
  draw — directly answers "a customer can transfer a bill to him even if that customer is
  in the debt tracker." **Deliberately deferred, flagged explicitly rather than silently
  dropped, given the scope already covered this session**: the destination customer's own
  public accept/reject page for a from_owner transfer (staff/owner confirm on their behalf
  for now, same tier the general split-transfer feature already allows when confirmed
  verbally); wiring "🏠 Mmiliki" into the three tabs drawers' own existing split/whole-tab
  transfer picker (only the debt profile and the owner's own ledger got the entry point
  this pass); and the STK Push settle option for `settle_owner_consumption()` (still
  cash/mpesa only) — explicitly the piece flagged in advance as needing its own careful
  pass, since it's the one part touching the M-Pesa callback surface. 18 new tests
  (`OwnerConsumptionPriceCaptureTest`, `OwnerConsumptionTransferTest`). One migration
  (0162, additive).
- Fix: owner-dashboard "Active Shifts" meter frozen (2026-08-13), live report. The
  shift-meter row's elapsed-time figure looked stuck instead of counting up. Root cause:
  the whole `home.html` block only ever fetched `/bar/shift/active/` once, on page load —
  unlike the revenue tiles right below it, which already poll every 30s. Wrapped the
  existing fetch+render logic in a named function (`_homeRefreshShiftMeter()`) and polled
  it on the same 30s cadence. No backend/Python change — pure template/JS fix.
- Fix: waitress's Muda genuinely, permanently stays 0h00m — explain it, don't just
  refresh it (2026-08-13), same-day follow-up. After the polling fix above shipped, Roy
  caught the real gap: a waitress (Sarah, cross-access — "BAR & KITCHEN") stayed at
  "0h 00m" on every fresh poll while a genuine custodian's number correctly climbed.
  Traced to this app's own deliberate `_shift_active_segments()` rule (2026-08-06/08
  design): a waitress's own attribution is subtracted out entirely for as long as a real
  till custodian (bartender/kitchen staff) is open on her station — correct accounting,
  since she's a concurrent helper, never the till custodian — but shown with zero
  explanation it just looks like a broken clock. `active_shift_api()`'s `all_shifts`
  payload now carries `started_at_iso` per shift; `home.html`'s Active Shifts meter
  computes a genuine client-side wall-clock duration from it and shows "Muda: 0h 00m ·
  Tangu Kufungua: Xh Ym" for a waitress row specifically (`s.staff_role === 'waitress'`)
  — the exact same real-time-vs-accountability split already built for her own screens
  this session (`waitress_screen.html`/`bar_board.html`'s live timer), now extended to
  the owner's own dashboard, where he was actually looking. 1 new test
  (`test_all_shifts_payload_carries_started_at_iso_for_owner_dashboard`, added to
  `WaitressLiveShiftTimerTest`). No migrations.
- "Mmiliki Alichukua" — recognize a debt customer as the owner, bulk transfer, and a
  cross-customer search page (2026-08-13), live request with screenshots: a debt customer
  named "Bosco" IS the business owner Bosco (KES 4,750 outstanding, blocked/high-risk) —
  "I want the system to identify that this is the owner so that I can transfer all those
  items to his personalised section... and transfer some of the other customers' tabs and
  debts that those customers claimed Bosco was to pay for them... let us give it more
  detailing and practicality." Three design decisions confirmed via `AskUserQuestion`
  before building: (1) **permanent redirect**, not one-time — "what if there is a similar
  name to that of the owner at the same time, in such instances the system should ask for
  clarification" (2) build a **dedicated cross-customer search/bulk-transfer page**, not
  just the existing per-customer button (3) **still go through the pending/accept step**
  even when the owner himself initiates — never auto-post, matching this app's own
  established discipline everywhere else. New `Customer.is_owner_alias` (core migration
  0163) — deliberately NOT automatic name-matching (same "the system knowing a name is the
  owner is not a heuristic" principle the original Mmiliki Alichukua design already
  established) — only ever set via an explicit "🔗 Weka kama Mmiliki" tap on that specific
  Customer's own debt profile (owner/manager only). New `link_customer_as_owner`/
  `unlink_customer_as_owner` views (`core/debt_views.py`) — linking sets the flag AND
  bulk-proposes every currently-unpaid transaction under that customer (via
  `_get_customer_debt_data(scope='all')`, the same authoritative FIFO source the debt
  profile itself reads) to Mmiliki Alichukua in one action; pressing the SAME button again
  once already linked becomes a "🔄 Sawazisha kwa Mmiliki" resync — safely idempotent for
  anything already proposed or already transferred, only picking up genuinely NEW unpaid
  debt each time, so debt accumulating under that name later doesn't silently pile up
  unnoticed. `OwnerConsumptionTransferRequest.propose_to_owner_locked()` widened from a
  single-`txn_id` signature to accept a list, batched under one `batch_id` (mirroring the
  `from_owner` direction's existing whole-bill behaviour) — but deliberately SKIPS (never
  errors on) any id that already has a pending request, unlike its `from_owner` sibling,
  since a resync call must be a safe no-op for already-in-flight items and only error when
  NOTHING in the set is actually eligible; `propose_transfer_to_owner` (the view) widened
  to match, accepting `txn_ids` (comma-separated) alongside the original single `txn_id` —
  existing pre-2026-08-13 tests updated from `request_id` to `request_ids[0]` to match the
  new always-a-list response shape, confirmed passing unmodified otherwise. New dedicated
  `owner_alias_debt_search` page (`/debt/owner-alias/search/`, owner/manager only) —
  searches unpaid debt items across EVERY customer at once (by customer name OR item
  description — deliberately does NOT pre-filter the `Customer` queryset by name at the DB
  level, so a search for an item like "Tusker" correctly finds it under whichever
  customer(s) actually owe it, not just customers whose own name matches), reusing
  `_get_customer_debt_data` per candidate customer (same technique `debtors_list_api`
  already established) rather than a raw aggregate query, since a bare `Sum()` would
  double-count or miss a partially-paid transaction — multi-select checkboxes submit the
  chosen items to the same widened `propose_transfer_to_owner` endpoint in one bulk call.
  Linked from the Debt Tracker dashboard toolbar next to the existing "🔀 Sahihisha Jina la
  Mteja" button. **Similar-name hint at checkout** (the Q1 caveat): `tab_check_api`
  (`core/kitchen_views.py`, the shared blur-check endpoint bar_board.html's tab-customer
  field already calls) now also checks the typed name against every `is_owner_alias=True`
  Customer — an EXACT match surfaces a purely informational note ("this name is linked to
  the owner, it'll show up on the resync list"), a SIMILAR-but-not-exact match surfaces
  the SAME click-to-confirm pattern the existing duplicate-name check already uses ("is
  this the same person as the owner? Ndiyo"). Deliberately never auto-redirects the sale
  itself at checkout time — reasoned explicitly against the invasive alternative (touching
  every checkout code path — Quick Sell, Bar Board, Kitchen Board, tab-to-debt conversion —
  each with different mechanics and no single safe choke point) in favor of the
  lower-risk, already-proven "confirm first, staff/owner-initiated action" pattern this
  whole feature already uses everywhere else. Disclosed scope note: this hint is wired
  into `bar_board.html` only, matching where the underlying `tab_check_api` blur-check is
  actually already used today — `kitchen_board.html`/`quick_sell.html` never had this
  blur-check wired in at all (confirmed by grep before assuming otherwise), so extending
  it there would be new client wiring beyond what's asked, not a parity fix. **UI polish**
  ("give it more detailing and practicality"): `owner_consumption_list.html` gained
  summary stat tiles (total unpaid, total paid this month, pending-decision count), a
  "🏠 Wateja Walioungwa na Mmiliki" section listing every currently-linked customer with
  their live outstanding figure, and split the previously-flat mixed list into distinct
  "⏳ Bado Haijalipwa" / "✓ Imelipwa" sections (shared per-row markup factored into a new
  `_owner_consumption_row.html` partial to avoid duplicating it). `customer_debt_profile.
  html` gained the link/resync button plus a distinct "🏠 Huyu ni Mmiliki wa Biashara"
  banner (with its own unlink action) whenever `customer.is_owner_alias` is true. 39 new
  tests (`CustomerLinkAsOwnerTest`, `OwnerAliasDebtSearchTest`,
  `OwnerAliasSimilarNameHintTest`, 3 more on `OwnerConsumptionTransferTest` for the widened
  bulk `propose_to_owner_locked`) — including cross-business isolation, staff-blocked
  regression locks on every new endpoint, the resync-never-reproposes-already-accepted
  regression lock, the never-auto-posts-even-when-owner-initiated regression lock, the
  open-tab-items-excluded regression lock (matching `_get_customer_debt_data`'s own
  exclusion), and the item-vs-customer-name search-scope regression lock. One migration
  (0163, additive). 1878 tests pass.
- Fix: Petty Cash review showed a dead-end "Hitilafu ya mtandao" with zero real signal
  (2026-08-13), live screenshot. **This is the THIRD distinct time this app has hit
  "generic Hitilafu ya mtandao masking the real cause," each with a DIFFERENT underlying
  mechanism** — worth knowing before assuming a future instance shares a root cause:
  (1) 2026-07-12 stock take — the VIEW returned `redirect()` instead of `JsonResponse`,
  so JS fetch followed it and got HTML (server-side bug); (2) 2026-07-23 tabs drawer —
  `quick_sell.html`'s handlers discarded the response body on a non-2xx status before
  parsing, masking a real `{ok:false, error:'...'}` JSON payload (client-side bug), on
  top of a genuine 404 from `_allowed_tab_sources` never returning `'qs'`; (3) this one.
  **Confirmed mechanism here**: every fetch() in `petty_cash_list.html`
  (review/edit/delete/respond — 5 call sites) blindly called `r.json()` with no check the
  response actually IS JSON, so anything returning HTML makes `.json()` throw while
  parsing and land in `.catch()` under a message naming the wrong problem. Also confirmed
  the service worker is NOT involved (`sw.js` skips every non-GET request at its first
  line — POST always goes straight to network, ruling out the stale-cache theory this app
  has hit before for similar-looking symptoms). **NOT confirmed — deliberately not
  guessed**: which candidate actually produced Roy's screenshot; no server log was
  available and it wasn't reproduced. Three are live on this app and produce an identical
  symptom: a 403 from `csrf_failure_view` (stale/rotated token — `SingleSessionMiddleware`
  refreshing the session elsewhere, or a long-open page), a 5xx HTML error page from
  Render/gunicorn (very live — three 502 incidents on 2026-08-09/11/12 from thread
  starvation), or a view returning `redirect()` (mechanism #1 above, already fixed once).
  Rather than hardcode one explanation, new shared `_pcParseJson`/`_pcNetworkErrorMessage`
  helpers capture `r.status` and derive the message from it — 403 → "session expired,
  reload"; 5xx → "server busy, wait, reload to see real state, retry"; other → generic
  with the status code shown; a genuine network failure (fetch itself rejecting) stays
  distinct from all of them. Deliberately avoids asserting "nothing changed" on a 5xx —
  `review_petty_cash` isn't wrapped in a transaction, so a partial write can't be ruled
  out; the message tells the user to reload and check instead of overclaiming. Pure
  template/JS fix — no backend change, no migration.
- Bar-ops transactional audit (2026-08-14): a requested comprehensive debug pass over the
  bar module's transactional flow and item balances, not a live-bug report. Traced
  `KegBarrel.record_sale`/`record_sale_locked`, `Transaction.cost()`/`revenue()`/
  `loss_value()`/`_stock_movement_cost()`, the full `BarTab`/`BarTabEntry` lifecycle
  (settle, split-transfer, revoke, void, convert-to-debt, revert-from-debt), `bar_board()`'s
  checkout (idempotency, backdating, split-payment), the newest `OwnerConsumptionTransfer
  Request` reclassification mechanism, and `_reconcile()`/`till_expected_cash()` — most of
  it checked out correct, including some non-obvious invariants (`void_tab` and the owner-
  transfer reclassification deliberately leave `KegBarrel.revenue_collected`/
  `volume_dispensed_ml` untouched, since those fields track "value poured," not "cash
  collected" — confirmed by reading `record_sale()`'s own semantics before assuming either
  was a bug). **Found and fixed one real, reproducible bug**: `remove_tab_entry()` ("✕ Futa",
  the shared tab-entry-removal endpoint all three counters use) correctly restored the
  `Item`'s own stock balance when voiding a mistaken keg-pour tab entry, but never reversed
  the source `KegBarrel`'s own envelope counters (`revenue_collected`, `volume_dispensed_ml`,
  cup/pint/jug serving counts) — unlike its sibling `void_direct_transaction()` (built later,
  2026-08-02, for the identical "item was never actually served" scenario on a direct sale),
  which already does this reversal correctly. Reproduced directly against a real `KegBarrel.
  record_sale()` output before touching any code: `revenue_collected`/`volume_dispensed_ml`
  were left completely unchanged after Futa, even though the item's own stock balance
  correctly restored. Confirmed via grep that `keg_metrics.py` (book-vs-scale variance,
  staff shrinkage), `keg_reconciliation`/`keg_barrel_detail`, and Bar Performance analytics
  all read these as STORED fields, never a live Transaction sum — so this silently,
  permanently overstated the barrel's envelope (and therefore the sell-modal's "target
  reached" gate, keg reconciliation's wastage %, and barrel P&L/markup in analytics) for
  every voided keg tab entry. Existing tests for `remove_tab_entry` (`RevokePaymentAndRemove
  EntryTest`) only ever used a hand-built plain `Transaction` with no `keg_barrel_id` set, so
  this gap was never exercised. Same `BarTabEntry` code path is shared by produce/kitchen
  tab entries too, so the fix covers `ProduceBunch`/`KitchenBatch` envelopes identically.
  Fixed by extracting the existing envelope-reversal logic out of `void_direct_transaction`
  into a new shared `_reverse_stock_movement_envelope(txn)` helper, called from both — zero
  behavior change to `void_direct_transaction` itself (regression-locked by a dedicated
  test), `remove_tab_entry` wrapped in `select_for_update()`/`atomic()` for the first time to
  safely lock the barrel/bunch/batch row during the decrement, matching the locking
  convention already used everywhere else in this file. 5 new tests
  (`RemoveTabEntryEnvelopeReversalTest`) — keg revenue/volume/serving-count reversal, the
  item-stock restoration still works, a `max(0, ...)` floor when the barrel was already
  independently corrected, the produce-bunch case, and the `void_direct_transaction`
  regression lock. No migrations. 1879 tests pass (core + accounts).
- Personalized shift-open welcome message (2026-08-14), live request: "welcome back
  (staff name) motivational message when staff logs in and opens shift." Hooked into
  `open_shift()` — not login itself — since that's the one moment every staffer's daily
  flow already funnels through on both counters, and it already has a dedicated success
  screen (`_showOpenShiftDoneScreen`, shown after every open-shift exit path: no tapped
  barrels, barrel-confirm skipped, or barrel weights confirmed). New
  `_build_shift_welcome_message(user, business, shift)` (`core/shift_views.py`) —
  deliberately NOT built from `haki_views._staff_contribution()`, which is a heavy,
  multi-query report meant for an on-demand page load and mixes in negative
  accountability figures (wastage, rejected petty cash, keg loss) that have no place in
  a welcome message; this is a couple of cheap, always-positive queries instead; every
  branch is failure-safe (a query error here must never block opening the shift, only
  cost the staffer a nicer greeting — falls back to a bare "Karibu, {name}!"). Priority:
  a round-number shift-count milestone (5/10/25/50/100/250/500/1000) > a first-ever
  shift > this month's own `recorded_by`-attributed revenue (cash+mpesa+credit, `.exclude
  (payment_method='void')`) if any > a plain generic greeting — the last two tiers each
  have two phrasings, picked off the new shift's own id (cheap, deterministic per shift)
  for variety. Added `welcome_message` to `open_shift()`'s JSON response (shared by both
  `/bar/shift/open/` and `/kitchen/shift/open/`, one view). `bar_board.html` and
  `kitchen_board.html` — counter-parity — both gained `_justOpenedWelcomeMessage`
  (module-level, set in `submitOpenShift`'s success handler; read by
  `_showOpenShiftDoneScreen` rather than threaded through inline `onclick` handlers,
  since the text itself may contain quotes/apostrophes) and render it as a gold banner
  above the existing "✓ Umefungua shift" line. 7 new tests
  (`ShiftWelcomeMessageTest`) — all four tiers, void-sale exclusion from the revenue
  figure, the kitchen endpoint parity check, and a no-blank-name-blocks-shift regression
  lock. No migrations. 1886 tests pass (core + accounts).
- Debt-tracker "does a cleared tab leak into the debt tracker" audit + new read-only
  diagnostic (2026-08-14), live report with screenshots: Roy's own "Roy" customer
  profile showed KES 320 outstanding (4× "Keg Gold" @ 80, dated 09 Aug) while he
  believed he had no outstanding bar debt, right after looking at a live receipt for a
  DIFFERENT, still-open 12–13 Aug tab (KC Ginger/Pineapple). Traced every settle/
  convert/transfer code path that could plausibly cause a genuinely-cleared tab entry
  to still read as debt: `settle_tab()`'s debt-redirect (resolves `tab.customer` via
  the stored FK, not a name lookup — correct), `tick_entry()`/`settle_entries_amount_
  locked()` (both correctly sync `Transaction.payment_method` away from `'credit'` the
  moment an entry is genuinely settled), `TabTransferRequest.accept()` (correctly syncs
  `recipient`/`payment_method` when a destination tab is already debt-converted),
  `OwnerConsumptionTransferRequest._accept_to_owner`/`_accept_from_owner` (round-trips
  cleanly), and `_convert_open_tabs_to_debt_for_shift()` (the shift-close auto-convert
  path — correctly sets `tab.customer` via the same FK pattern `_convert_tab_to_debt_
  core()` uses, so `settle_tab()`'s redirect can recognise an auto-converted tab too).
  All checked out structurally correct — no reproducible bug found in the code itself.
  The numbers in Roy's own screenshots are also internally consistent (Bar KES 1680
  credit / 1360 paid + Kitchen KES 1040 credit / 1040 paid = the profile's own KES 2720
  / 2400 totals), and FIFO always surfaces the OLDEST unpaid transactions first — a real,
  separate, older debt from 09 Aug predating an unrelated newer tab is the simplest
  explanation, not a leak. Given the stakes ("I cannot allow the system to put irregular
  amounts to be paid for customers who had nothing left to pay") a code read alone isn't
  enough reassurance — built `audit_debt_ledger_integrity` (read-only, mutates nothing,
  confirmed by a dedicated test), a diagnostic Roy can run himself via Render's Shell
  against his REAL production data to check the three CONCRETE, mechanical ways this
  exact symptom could actually happen: (1) a `Transaction` still `payment_method='credit'`
  whose own `BarTabEntry.is_paid=True` — the direct signature of an entry settled without
  syncing its transaction; (2) a `BarTab` left `status='SETTLED'` with no `customer_id`,
  still carrying unpaid entries — stuck in limbo, invisible to `settle_tab()`'s debt-
  redirect; (3) two-or-more `Customer` rows sharing the same name (case/whitespace-
  insensitive) within one business — a real payment recorded against one duplicate never
  reduces the other's outstanding figure, since `CustomerDebtPayment` is tied to a
  specific `Customer` row, not a name string (this app's own well-documented recurring
  bug class — see the Jenerali/Genro/McKenzie entries above; the `🔀 Sahihisha Jina la
  Mteja` merge tool already exists for exactly this). `--customer=<name>` additionally
  prints a full itemized unpaid-transaction breakdown (item/date/amount/originating tab)
  for direct cross-checking against what an owner remembers paying. 8 new tests
  (`AuditDebtLedgerIntegrityTest`) — each of the three finding types reproduced and
  detected, a clean/correctly-settled ledger produces zero false positives, a case-only
  name difference (`'roy'` vs `'Roy'`) is correctly NOT flagged as a duplicate (matches
  every real `name__iexact` resolution path elsewhere in the app), business-scoping, and
  a direct regression lock that the command never writes to the database. No migrations.
  1894 tests pass (core + accounts).
- `audit_debt_ledger_integrity` — `--all-customers` flag (2026-08-14, same-day follow-up):
  Roy asked to run the diagnostic for every customer at once rather than one name at a
  time, for "something conclusive." The 3 structural findings (unsynced payment_method,
  stuck SETTLED tabs, duplicate names) already scanned the whole business by default —
  only the itemized per-transaction breakdown was single-customer. New `--all-customers`
  iterates every `Customer` in the matched business(es), skips anyone with `outstanding
  <= 0` (keeps the output focused on real balances, not a wall of zeros), and prints a
  grand-total line (customer count + combined KES) at the end. `--customer` is ignored
  when `--all-customers` is also passed (documented in both flags' help text). 2 new
  tests (`AuditDebtLedgerIntegrityTest`) — a mixed owing/clear-balance business correctly
  lists only the owing customer with the right total, and the flag-precedence case. No
  migrations. 1896 tests pass (core + accounts).
- **CRITICAL FIX** — `_do_settle_debt_payment()` never synced `Transaction.payment_method`
  off `'credit'` when its FIFO reconciliation marked a `BarTabEntry` paid (2026-08-14,
  found LIVE via `audit_debt_ledger_integrity --all-customers` against Monsoon Inn's real
  production data — dozens of genuinely-settled transactions across 25 different
  customers, still permanently tagged as outstanding credit). Root cause, in
  `core/debt_views.py`: the "FIFO BarTabEntry reconciliation" block — which fires whenever
  a debt payment (`record_debt_payment`, the M-Pesa debt-payment callback, or `settle_tab`'s
  /`_settle_tab_from_payment`'s own debt-redirect for an already-converted-to-debt tab)
  fully covers an entry — used a bulk `BarTabEntry.objects.filter(...).update(is_paid=True,
  payment_method=payment_method)` that only ever touched the `BarTabEntry` row, never the
  `Transaction` it points at. Every OTHER settle path in this app (`tick_entry`,
  `settle_tab`'s own main loop, `BarTab.settle_entries_amount_locked`, `_settle_tab_from_
  payment`, `_settle_receipt_entries_from_payment`) already syncs both together in the
  same step — this was the ONE place that didn't, and it happens to be the single most
  heavily-used closing path for a business that relies on shift-close auto-convert +
  later debt-tracker payments (exactly Monsoon Inn's real usage pattern) to close out
  tabs. Consequence: `_get_customer_debt_data()`'s `total_credit`/`outstanding` AGGREGATE
  math was never actually wrong (`total_credit`/`total_paid` never read `BarTabEntry.
  is_paid` at all — both are computed independent of it), but the PER-LINE "which specific
  transaction is still owed" breakdown, `settle_tab()`'s own "already fully covered, nothing
  to redirect" guard (`tab.entries.filter(is_paid=False).exists()`), and anything else
  reading `Transaction.payment_method` directly (e.g. `promo_views.py`'s "customers with
  debt" segment) all silently, permanently treated an already-paid transaction as still-
  owed credit forever — exactly the "leak" Roy suspected, now confirmed and fixed. Also
  separately audited `Customer.merge_locked()`/`rename_locked()`/`_propagate_name_change()`
  (Roy's own follow-up question — "could merging names cause a similar bug?") and confirmed
  clean: they correctly reassign `Transaction.recipient`, `BarTab` FK+name,
  `CustomerDebtPayment.customer`, `Payment.debt_customer`, and `Receipt.customer_name`+
  `linked_tab_ids` — none of them touch `payment_method` at all, so no equivalent sync gap
  exists there. Fixed by syncing `txn.payment_method` in the same loop, right after the
  `BarTabEntry` bulk update, using the already-loaded `txn` object from `_get_customer_
  debt_data()`'s own FIFO walk (`unpaid_before`) — no new query needed. Retroactive
  repair: `backfill_split_paid_txn_payment_method` (originally built 2026-07-31 for a
  DIFFERENT bug — `BarTabEntry.split_paid_unpaid_locked()`'s own, separate sync gap) turned
  out to already have the EXACT right repair query for this identical broken-row signature
  (`BarTabEntry.is_paid=True, payment_method in (cash, mpesa), transaction__payment_method
  ='credit'`) — docstring updated to document both root causes it now covers rather than
  writing a confusingly-duplicate command; no logic change needed, safe to re-run. 5 tests
  added/extended (`DoSettleDebtPaymentTransactionSyncTest` — full coverage syncs, partial
  coverage never marks paid/never syncs, multi-entry FIFO walk syncs only the covered ones
  and leaves the genuinely-newer-and-still-unpaid one untouched — matching Roy's own real
  KES 320 outstanding figure exactly, and an OPEN tab's entries are never touched by
  someone else's debt payment; plus the pre-existing `SettleTabRedirectsToDebtPaymentTest.
  test_settle_on_debt_converted_tab_records_real_debt_payment` gained the missing
  Transaction-sync assertion it should have had from the start — confirmed by temporarily
  reverting the fix and re-running: 3 tests fail without it, all pass with it). No
  migrations. 1900 tests pass (core + accounts). **Action for Roy**: once this deploys,
  run `python manage.py backfill_split_paid_txn_payment_method` (add `--dry-run` first to
  preview) on Render's Shell to repair every historical stuck-credit transaction, then
  re-run `audit_debt_ledger_integrity --business="Monsoon Inn" --all-customers` to confirm
  zero findings.
- App icon redesign — glossy 3D gold "D" monogram (2026-08-14). Live request, inspired by
  Apple Music's 3D icon (Roy's own screenshot reference): replaced the flat gold-ring
  favicon/PWA icon with a layered, glossy design (radial gold gradient ring, onyx
  glass-effect center, gradient "D" letterform with drop shadow, raspberry accent
  underline), rendered via Playwright + headless Chromium (no rasterization tools
  available in this environment — SVG/HTML rendered directly at each target pixel size to
  avoid resampling artifacts) at every declared manifest size (72/96/128/144/152/192/
  384/512). Since every icon reference in the app (favicon, apple-touch-icon, manifest,
  and the Android/Chrome auto-generated PWA install splash screen) funnels through the
  same `static/icons/icon-*.png` files, replacing all 8 covers everything with zero other
  code changes. Explained to Roy: an already-installed PWA icon does NOT update
  automatically on iOS (no update mechanism at all — must remove and re-add "Add to Home
  Screen" to see it) but DOES update itself within a few days on Android/Chrome (WebAPK
  periodically re-checks the manifest in the background) — this is a real OS-level
  limitation on both platforms, not something fixable from the app side. Confirmed for
  Roy that reinstalling the PWA icon is unrelated to login/session behavior — it only
  changes the home-screen shortcut, never touches cookies or server-side session state.
- Single-session AJAX-aware kick (2026-08-14), live report: Roy and the Monsoon Inn
  owner share one login (Roy logs into the owner's account directly to check progress),
  so `SingleSessionMiddleware`'s one-session-per-user enforcement — correct, intended
  behavior — mutually boots whichever side isn't the most recent login. Roy's ask: the
  boot itself should be smooth on both sides, not "a hard time," and not slow. Root
  cause of the asymmetry: `SingleSessionMiddleware` always did a raw `redirect('login')`
  on a stale session, which is clean for a real page navigation but WRONG for this app's
  many background `fetch()` polls (notifications count, tab lists, dashboard revenue,
  shift status) — fetch() follows redirects by default, so the booted party's next poll
  silently received the login page's HTML instead of JSON, which every JSON-parsing
  handler in the app then fails on with a confusing generic error, not a clear "you were
  logged out" message. Whoever logs in LAST is never the one hitting this path — Roy,
  who initiates the check-in, always got a clean fresh login; the owner, sitting on an
  already-open screen, is the one who'd hit a silently-broken poll until his next full
  page navigation. Fixed with `_is_ajax_request()` (Sec-Fetch-Mode header — sent by every
  modern browser including iOS Safari since ~2021 — as the primary signal, falling back
  to X-Requested-With/Accept for the rare browser without it, defaulting to the original
  safe "treat as navigation" behavior otherwise) — a stale-session AJAX request now gets
  a plain JSON 401 (`{"error":"logged_out","redirect":"/accounts/login/"}`) instead of an
  HTML redirect. Paired with a small global `window.fetch` interceptor added once in
  `base.html` (loaded on every page, no per-template sweep needed) that watches every
  fetch() response for this exact shape and immediately forces a full-page redirect —
  the booted side now gets an instant, clear kick the moment ANY background call
  notices, not a silently broken UI. **Real, separate pre-existing bug found and fixed
  in the same file while writing this fix's own tests**: the neighboring "deactivated
  mid-session" branch (`if not request.user.is_active`) has been DEAD CODE since the day
  it was added (2026-07-25) — Django's own `AuthenticationMiddleware` already resolves a
  deactivated user's session to `AnonymousUser` via `ModelBackend.user_can_authenticate()`
  *before* this middleware ever runs, so `request.user.is_authenticated` was always
  already False by the time the check executed, meaning it could never fire; the
  existing regression test only asserted `is_authenticated == False` afterward, which
  Django's own middleware already guaranteed independent of this code, so the dead
  branch was never caught. A deactivated staffer was landing on the public page with
  zero explanation and no explicit server-side session flush (self-healing only once
  the cookie's own `SESSION_COOKIE_AGE` eventually expired). Fixed by checking
  `request.session.get(SESSION_KEY)` (the raw, unresolved session data Django's own auth
  stack leaves behind) whenever `request.user` comes back anonymous — narrowly scoped:
  a genuinely-new anonymous visitor never has this key at all, and a password-change
  elsewhere already gets its own session flush inside Django's `auth.get_user()` before
  this middleware would ever see it, so this can't misfire for either of those cases.
  7 new tests (`SingleSessionAjaxKickTest`) — plain-navigation regression lock, AJAX
  gets JSON 401 (both Sec-Fetch-Mode and bare Accept-header detection), the session is
  genuinely ended either way (not just told it's ended), the now-actually-reachable
  deactivation-AJAX case, and both bypasses (`allow_concurrent_sessions`, superuser)
  confirmed unaffected regardless of request shape — plus the pre-existing
  `DeactivatedStaffMiddlewareTest` re-run and confirmed passing (now for the right
  reason). No migrations.
- **CRITICAL FIX — "Paid exceeds Credit" / genuinely-owed items silently vanishing from
  the debt tracker (2026-08-15), live report with screenshots from Monsoon Inn.** Roy:
  staff reported tabs/debts "disappearing" — a customer (Eugene) with a real 320 KES
  debt (160 paid partially, 160 still owed) showed 0 unpaid transactions and Total Paid
  KES 800 against Total Credit KES 320; Roy's own "Roy" customer profile showed the Bar
  sub-ledger as "All paid" with Total Paid KES 1360 against Total Credit KES 920 —
  impossible under a correct FIFO ledger, and specifically NOT reproducible on the
  Kitchen side. **Root cause, traced end to end, not guessed**: `_get_customer_debt_
  data()`'s "Total Credit" figure was computed by summing every Transaction CURRENTLY
  `payment_method='credit'` — but at least 15 separate settle paths across this app
  (`tick_entry`, `settle_tab`, `settle_entries_amount_locked`, the STK-push tab-
  settlement callbacks, and `_do_settle_debt_payment`'s own FIFO reconciliation — the
  very mechanism from THIS SAME DAY's earlier "CRITICAL FIX" entry above — among them)
  all flip `payment_method` AWAY from 'credit' the instant a transaction is resolved,
  correct for `shift_views._reconcile()`'s live cash/mpesa/credit split but fatal for
  the debt tracker: the moment ANY of them resolved a transaction, "Total Credit" simply
  stopped counting it, while `CustomerDebtPayment` ("Total Paid," append-only, never
  shrinks) kept the full record — a payment recorded through the debt tracker's own
  `_do_settle_debt_payment()` DOUBLE-SUBTRACTED itself (once via excluding the
  transaction from Total Credit, once via adding to Total Paid), silently understating
  `outstanding` for every account with enough resolved credit history, worst wherever
  debt-tracker payment activity was heaviest (bar, for both Eugene and Roy's own test
  account that day). **Second, distinct gap found in the same investigation**:
  `tick_entry()` (the per-item "tick" checkmark, separate from "Lipa Yote") never got
  the debt-redirect fix `settle_tab()` received on 2026-08-10 — ticking a single entry
  on an ALREADY debt-converted tab flipped its `Transaction.payment_method` off
  'credit' directly, with ZERO matching `CustomerDebtPayment` ever created, meaning
  money genuinely collected at the counter this way vanished from Total Credit with
  nothing added to Total Paid to compensate — a real, silent leak. **Fix, in two parts,
  both required together**: (1) `tick_entry()` now shares `_is_debt_converted_tab()`
  (factored out of `settle_tab`'s own check) and redirects through the same canonical
  `_do_settle_debt_payment()` for a debt-converted tab's entry — every resolution
  mechanism now always creates a matching payment record, never a silent flip. (2) New
  `Transaction.was_credit` — a permanent, one-way marker stamped automatically via a
  `__init__`/`save()` override (Django's own from-db value snapshotting, `_loaded_
  payment_method`) the instant `payment_method` transitions FROM 'credit' TO a real
  channel (cash/mpesa specifically — never 'void', so a written-off debt doesn't look
  permanently-unpaid-forever) — deliberately implemented at the MODEL layer specifically
  so it automatically covers all ~15 existing (and any future) settle call sites with
  ZERO changes needed to any of them, avoiding the exact "sweep every call site and risk
  missing one" failure mode that caused this bug in the first place. **A first draft of
  this was too broad and caught by its own test before shipping**: stamping was_credit
  on ANY transition off 'credit' would have ALSO flagged completely ordinary tab items
  (which are briefly `payment_method='credit'` while merely OPEN, by design — `KegBarrel.
  record_sale`'s `pay = 'credit' if tab else ...` — and never real debt) the moment they
  settled normally, reintroducing phantom debt for every ordinary customer; fixed by only
  stamping when the transaction's own tab (if any) is NOT still OPEN at the transition
  moment — i.e. it was ALREADY being counted as genuine debt (SETTLED via conversion)
  when resolved, or there's no tab at all (a direct credit sale, debt from creation).
  `_get_customer_debt_data()`'s `credit_qs` (both scope branches), `_calc_avg_payment_
  days()`, `credit_policy._count_late_repayments()` (an independent, parallel FIFO
  simulation with the identical bug), and `debtors_list_api()` (the staff-facing "💳
  Wateja wenye Deni" panel, its own raw reimplementation of the same buggy formula) all
  widened from `payment_method='credit'` to `Q(payment_method='credit') | Q(was_credit=
  True)` — the FIFO walk logic itself needed ZERO changes, since it naturally and
  correctly treats an already-resolved transaction as "already spoken for" the moment
  `total_paid`'s cumulative consumption reaches it in date order, exactly mirroring
  `_do_settle_debt_payment`'s own oldest-first resolution order — the two can never
  drift apart. Deliberately NOT widened (correctly stay live/current-state only, verified
  by reading each): `shift_views._reconcile()`'s `credit_sales` (must reflect the REAL
  current cash/mpesa/credit split for till reconciliation), `haki_views._staff_
  contribution()`'s revenue-by-payment-method split (same reasoning), `request_write_
  off()`'s eligibility check (a write-off request only makes sense for something still
  genuinely owed). Deliberately deferred as lower-stakes (a promo/marketing segment-
  builder completeness gap, not a money-correctness one): `promo_views.py`'s
  `SEGMENT_DEBTORS`/`_count_debtors` still use the live-only filter — flagged, not fixed,
  to keep this urgent fix's scope to money-correctness surfaces only. **New tools for
  the live incident**: `diagnose_customer_debt` (read-only, dumps a customer's raw
  Transaction + CustomerDebtPayment history bypassing the buggy aggregate entirely — built
  and shipped FIRST, before the fix, specifically so Roy had an immediate way to see the
  true underlying data and reassure staff that nothing was actually lost, only miscounted)
  and `backfill_was_credit` (best-effort repair for transactions ALREADY resolved before
  this fix existed — recovers `was_credit=True` for any tab-linked transaction whose tab
  was EVER debt-converted, via `BarTab.customer` being set — a permanent signal, unlike
  the since-overwritten `payment_method`; explicitly documented as NOT able to recover a
  tab-LESS direct credit sale resolved before this fix, since no equivalent permanent
  signal survives for that case — `diagnose_customer_debt` is the fallback for inspecting
  any such customer directly). 27 new tests (`TransactionWasCreditFieldTest`,
  `PaidExceedsCreditBugFixTest` — including a direct reproduction of the exact reported
  numeric shape and a regression lock that the ordinary single-transaction partial-
  payment case, Eugene's own literal remembered scenario, is completely unaffected,
  `TickEntryDebtRedirectTest`, `BackfillWasCreditCommandTest`, `DiagnoseCustomerDebt
  CommandTest`) plus the full pre-existing `CreditGate*`/`UniversalCreditLimitTest`/
  `SettleTabRedirectsToDebtPaymentTest`/`DoSettleDebtPaymentTransactionSyncTest` suites
  confirmed passing unmodified. One migration (0164, additive — `was_credit` defaults
  False). **Action for Roy, once deployed**: run `python manage.py backfill_was_credit`
  (with `--dry-run` first to preview) on Render's Shell to repair historical data for
  every business on the platform — safe to re-run. For any customer whose numbers still
  look wrong afterward (the documented tab-less-direct-credit-sale gap), run
  `python manage.py diagnose_customer_debt --business="X" --customer="Y"` to inspect
  their raw history directly and manually reconcile with `record_debt_payment`'s existing
  backdate field if needed.
- Kitchen Stock Receipt auto-close + Gawa Kuku (2026-08-21), live request: Monsoon Inn
  switching from buying pre-cut chicken pieces to whole birds portioned in-house. Two
  ordered features, built in the sequence Roy asked for. **(1) Auto-close** (built first,
  per Roy's own ordering): `KitchenStockReceipt.maybe_auto_close()` — checked lazily on
  read (`kitchen_stock_receipts_list()`), closes an OPEN receipt the moment every one of
  its lines' own item hits `current_balance() <= 0`, reliable now specifically because of
  `Item.capped_deduction()` (2026-08-07, "stock cannot be negative") — reverses the
  original 2026-07-25 "closing is always deliberate, never automatic" decision, which
  predates that guarantee. Roy's own backdating/no-internet concern ("a later receipt for
  the SAME item shouldn't stop an earlier day's paper-recorded sale from counting once it's
  finally entered") is satisfied for free — `total_revenue()`'s window only ever WIDENS
  (closed_at is stamped at the real moment auto-close fires; a backdated sale entered later
  still lands inside `[received_on, closed_at]` as long as it's dated before the close),
  locked in by a dedicated regression test. **Confirmed scope, mid-build, from a live
  clarifying message**: this ONLY ever touches `KitchenStockReceipt` (the portion-item "🧾
  Stock Receipt" flow — Kuku pieces, Whole Chicken, Raw Potatoes) — `KitchenBatch` (Chipo)
  is a completely separate model/lifecycle and still closes ONLY via the explicit "✓
  Imekwisha"/"🗑 Tupa" buttons, untouched by this fix. **(2) Gawa Kuku**: new
  `PortioningEvent`/`PortioningEventLine` models (migration 0169) — staff cuts one whole
  raw unit (Whole Chicken) into whatever mix of named cuts it actually yields (never
  assumed fixed — a bigger bird gives more/different pieces), funding one or more of the
  finished item's own existing cut-presets in a single motion. Deliberately its own
  raw-to-multi-cut flow rather than reusing `KitchenBatch.open_batch()` (that mechanism
  draws one raw unit into exactly ONE derived item's `cost_total`; this needs one raw unit
  to fund SEVERAL independently-priced presets at once). Reuses `Item.raw_material_source`
  (generalized the same day beyond KitchenBatch-only — see its updated docstring) and
  `ItemPortionPreset.cost_price` (2026-07-28) so a portioned cut sells exactly like any
  other preset-costed piece, through the ALREADY-correct Kitchen Performance analytics —
  deliberately does NOT attribute a later sale back to which specific bird/event it came
  from (no FK from Transaction to a line here), per Roy's own explicit worry about
  repeating the Kuku/Chipo Mapato time-window precision trap already abandoned earlier this
  session; Kitchen Performance's per-preset cost/revenue was never built on a receipt
  time-window in the first place, so it's already immune. `PortioningEvent.create_locked()`
  locks the raw item, validates enough balance, creates one Draw transaction for the bird
  plus one Receipt transaction per cut produced, and writes each preset's `cost_price` — a
  blank per-line cost is filled as an even split of the bird's own cost across ALL pieces
  declared in the same submission (a pre-filled SUGGESTION only, per Roy's own answer —
  "if the staff does not change the cost manually the system should assume that" — a
  staff-typed cost always wins, never re-derived). One PortioningEvent = one bird
  (`qty_drawn`, default 1, a field not a constant purely for future flexibility) — "receipt
  of 10 whole chickens does not mean they all get portioned at the same time," matching
  Roy's own sack-of-potatoes analogy. Supports backdating (`created_at`) so a bird
  portioned to catch up a past paper record lands on the real date. New
  `portion_event_create` view (`core/kitchen_views.py`) mirrors `kitchen_stock_receipt_
  create()`'s exact permission/idempotency shape — `_kb_gate` (shift + station scoping) +
  `can_receive_kitchen_stock` tier, `claim_checkout_token` double-submit guard, `ValueError`
  → 400 JSON — at `/kitchen/portion-event/create/`. Kitchen Board: `kitchen_board()` gained
  `gawa_kuku_targets` per raw item (its linked finished item(s)' own non-tethered presets,
  `derived_batch_items.filter(is_kitchen_batch=False)` — deliberately excludes a
  KitchenBatch target like Chipo, which already has its own raw-material-draw flow) driving
  a new "🍗 Gawa" button on the raw item's own tile (owner/manager or `can_receive_stock`
  staff, hidden once out of stock), opening a new modal listing the finished item's cuts
  with a qty input per cut and an optional cost override — same "server resolution is
  authoritative" convention as every other checkout surface in this app. 21 new tests
  (`PortioningEventTest` — model-layer happy path, explicit-cost override, insufficient
  balance, no-real-lines, backdating; endpoint happy path, unlinked-pair rejection,
  duplicate-token block, cross-business rejection, and the full staff/shift/station
  permission matrix mirroring `KitchenStockReceipt`'s own coverage) plus 9 for auto-close
  (`KitchenStockReceiptAutoCloseTest`). One migration (0169, additive). 2117 tests pass
  (core + accounts).
- Kitchen Stock Receipt live bug arc, same-day follow-up (2026-08-21). Five live reports
  from Roy, each fixed and pushed same-day, all continuing directly off the auto-close
  sprint above. **(1) Duplicate receipt card** — the just-added auto-close (checked
  lazily on read) mutated a receipt to CLOSED in-memory (`newly_closed`) but
  `kitchen_stock_receipts_list()` then separately re-queried `recent_closed` from the DB,
  which now ALSO included that same now-committed row — concatenated with no dedup, so
  the exact receipt Roy was watching showed as two identical cards for one poll cycle.
  Fixed by deduping on `id`, preferring the `newly_closed` copy. **(2) Kamau's closed
  receipt showing a later, unrelated delivery's stock** — `_kitchen_stock_receipt_to_dict()`
  always showed `item.current_balance()` (live, whole-item balance) regardless of the
  receipt's own status, so a CLOSED receipt displayed whatever the item's balance happened
  to be NOW, including stock from a completely different, LATER Stock Receipt for the same
  item. Fixed: `current_balance` is `None` unless `receipt.status == 'OPEN'`. **(3) Backdate
  rectification tool ("prioritise this first — needed right now by the staff")** — new
  `Transaction.split_from`-adjacent standalone fix: `correct_transaction_date()`
  (`core/keg_views.py`, `/bar/transactions/<id>/correct-date/`) moves BOTH `created_at` AND
  `date` together (never just one — see the 2026-08-12 `Transaction.date`/`created_at` drift
  entry above for why leaving one behind silently breaks every day-bucketed revenue query),
  preserves the original time-of-day, rejects a future date, and is reachable via a new
  "📅 Tarehe" button on the direct-sale rows of all three tabs drawers' "🕐 Malipo ya Hivi
  Karibuni" panel (tabs-drawer-parity rule) — same shift+station permission tier as every
  other direct-sale correction (`correct_transaction_payment_method`). **(4) A deleted/
  voided item still showed on the customer's own receipt** — `receipt.lines` is a static
  JSON snapshot taken at checkout time, never re-read once a correction tool (Futa/void)
  later changes the underlying `Transaction`. New `core.receipt_views._live_direct_lines()`
  — the DIRECT-sale sibling of the tab-linked `_get_live_tab_state()` — recomputes lines
  live at render/poll time by cross-referencing each line's new `txn_id` field (added to
  every `receipt_lines.append(...)` call across Quick Sell, Bar Board's keg-cart checkout,
  and all three Kitchen checkout branches) against the real current `Transaction` rows,
  dropping any that are now voided or zero-qty. Wired into both `public_receipt()` (initial
  render) and `receipt_live_status()` (the 20s live poll), so a Futa'd item disappears from
  the customer's receipt automatically, no reload needed. **Roy's own completeness
  challenge, same session**: "will backdating, gawanya and all other relevant functions...
  change and adjust the receipt situation accordingly?" — traced `split_payment_method_
  locked()` directly (not assumed) and found a real, previously-unnoticed gap: the
  split-off sibling transaction (`qty=Decimal('0')`, the unpaid/other-method remainder) had
  NO receipt line at all — a Gawanya split silently vanished from the customer's own bill.
  Fixed with new `Transaction.split_from` self-FK (migration 0170, `on_delete=SET_NULL`,
  one-level-deep by design — a split-of-a-split is out of scope, matching this feature's own
  real-world usage) stamped by `split_payment_method_locked()`; `_live_direct_lines()`
  extended to SYNTHESIZE a receipt line for any such child transaction alongside its
  (amount-adjusted) parent line, so a checkout-time split (new cart-level split-payment
  feature) AND a later correction-time split both self-heal the receipt for free from the
  same mechanism. **(5) Raw-material receipt's structurally-guaranteed -100% Faida** — Roy:
  "how when 5 buckets have been sold... each bucket shows the chipo profit [but the raw
  potatoes receipt still shows -100%]?" `KitchenStockReceipt.total_revenue()` sums sales of
  the receipt's OWN item (Raw Potatoes) — which is NEVER sold directly, only drawn into a
  batch (Chipo) via `type='Draw'` — so revenue=0/profit=-100% for a raw-material receipt is
  a structural fact about the model, not a real loss signal; the real profit lives on
  `KitchenBatch.revenue_collected`/`cost_total` for whichever batches drew from it (already
  correct, per Roy's own screenshot of a real per-bucket Chipo profit). Fixed the DISPLAY,
  not the (correct) underlying math: `_kitchen_stock_receipt_to_dict()`'s `raw_material_for`
  computation rebuilt to sum ALL batches (not just currently-open ones) drawn from that
  specific raw-material receipt, windowed by "which raw-material delivery was active when
  this batch opened" (`[this_receipt.received_on, next_receipt.received_on)` for that same
  item, open-ended if no next receipt exists — same reasoning `total_revenue()`'s own
  window already uses) — so a CLOSED raw-material receipt still correctly attributes every
  batch it ever fed, not just ones still open. New `is_all_raw_material` flag (every line's
  item is a `raw_material_source` for something) on the receipt dict and `is_raw_material_
  source` per line; `kitchen_board.html`'s `_ksrCostLineHtml()` hides the misleading
  Gharama/Mapato/Faida line entirely for such a receipt (an italic note points to the batch
  history instead), and `kitchen_viability.html`'s receipt-history table does the same
  (`core/kitchen_viability.py`'s `kitchen_receipt_history()` gained the identical
  `is_raw_material` flag). No migrations beyond `split_from` (0170, additive).
- Fix: Recent Payments panel showed nothing for a date Receipts clearly had entries for
  (2026-08-21, same-day urgent follow-up). Roy: kitchen staff backdated yesterday's catch-up
  sales but mistakenly left some un-backdated (landing on today); wanted to correct them via
  the just-shipped "📅 Tarehe" button — but the ONLY place that button lives, the "🕐 Malipo
  ya Hivi Karibuni" panel (`recent_settled_tabs_api`), showed "Hakuna malipo tarehe hii" for
  today even though the Receipts list clearly showed real entries dated today. Investigated
  the query line-by-line (day-boundary computation, station scoping, direct-vs-tab
  discrimination) and reproduced the EXACT reported scenario twice against a clean test
  database — a plain portion-item cash sale AND a KitchenBatch (Chipo) cash sale, made by
  both an owner and a kitchen staffer, with explicit `?date=<today>&station=kitchen` — and
  in every case the sale correctly appeared. Could not reproduce a backend bug; rather than
  guess further at an unreproduced production-only symptom (this app's own standing
  discipline), shipped two things instead of a blind fix: (1) **`diagnose_recent_sales_
  visibility`** (new, read-only management command, `core/management/commands/`) — runs the
  SAME query logic `recent_settled_tabs_api` uses against REAL production data for one day,
  then cross-references every `Receipt` issued that day against it, printing for each
  underlying transaction exactly which bucket it landed in (direct list / tab list /
  EXCLUDED by station filter) or, if found in neither, the SPECIFIC reason why (wrong type,
  wrong payment_method, `created_at` falling outside the day window despite the receipt
  being inside it, a tab entry that's unpaid or paid-but-outside-the-window, void) — turning
  the next report into a direct read of the real data instead of another blind guess.
  (2) **A second, independent correction surface** — `transaction_history()`
  (`/history/`, `core/views.py`) now annotates every business-scoped `Transaction` with
  `has_tab_entry` (via `Exists(BarTabEntry...)`) and a computed `is_direct_correctable` flag
  (Issue type, no tab entry, cash/mpesa/credit), and `transaction_history.html` gains a
  "📅 tarehe" link per matching row, reusing the same `correct_transaction_date` endpoint —
  deliberately chosen because this page has NO day-bucket computation at all (the server
  returns the WHOLE business's history; the existing date/text filter is pure client-side
  matching over an already-rendered `data-date` attribute), so a staffer unsure why the
  other panel came up empty can still find and fix the exact row here by searching the
  item/time directly, completely independent of whatever turns out to be wrong with
  `recent_settled_tabs_api`. Told to Roy plainly: this is a working fallback shipped
  alongside an honest "couldn't reproduce it yet" rather than a claimed fix for the mystery
  itself — the diagnostic command is the next step once he can run it against the real data.
  8 new tests (`TransactionHistoryDateCorrectionFallbackTest`). No migrations. 2121 tests
  pass (core + accounts).
- Item Journey — per-item balance-at-every-point + who, urgent live request (2026-08-21).
  Roy: staff physically counted 8 Dallas bottles, system showed 16 — "I need to know if the
  business owner might have received double the amount in system based on the latest
  receipt, or if there were unrecorded sales... widening the scope of the transaction
  history of each and every stock item via stock list or history should tell a story or
  show the journey of the item." New `Item.balance_journey()` (`core/models.py`, right next
  to `current_balance()`) — the single canonical computation: every `Transaction` for the
  item ordered `(date, created_at, id)`, each carrying the running BIN balance immediately
  after it, starting from `opening_bin_balance`. Deliberately mirrors `current_balance()`'s
  own exact math so the final entry's `running_balance` always equals `current_balance()`
  exactly — locked in by a dedicated test, never re-derived separately. `item_detail()`
  (`/item/<id>/`, already the click-through destination from every `stock_list.html` row —
  no new navigation needed) now renders this as "🧭 Safari ya Bidhaa," each row showing
  time-of-day, running balance, and who recorded it (`recorded_by`, already an existing
  field on every `Transaction` — simply never surfaced anywhere before), plus payment
  method for context. Defaults to newest-first (matching every other history table in this
  app); a `?order=asc` toggle switches to oldest-first — the literal "read it like a story"
  framing — since a forensic investigation like Roy's often wants to start from a known-good
  count and read forward. **Avoided building a duplicate diagnostic tool**: `diagnose_stock_
  shortfalls --item=` (2026-08-19, built for this SAME Dallas item two days earlier) already
  prints a full chronological ledger with running balance + who — rather than ship a second,
  90%-overlapping command, extended that one instead with the two genuinely new pieces of
  investigative value Roy's new question needs: a duplicate-receipt heuristic (flags two
  Receipt transactions within 48h with matching/near-matching quantities — the concrete
  signature of one delivery entered twice) and a new `--physical=N` flag that prints the
  exact system-vs-physical gap with a plain-language explanation of what each direction
  implies (system HIGHER → check the duplicate-receipt flag above, or ask staff whether
  every sale went through Quick Sell/Bar Board, since an unrecorded sale leaves NO trace in
  the ledger at all — its ABSENCE, not a wrong entry, is the signature to look for; system
  LOWER → an unrecorded receipt/return, or an earlier Rekebisha overcorrected). 19 new tests
  (`ItemBalanceJourneyTest`, plus 4 more on `DiagnoseStockShortfallsCommandTest`). No
  migrations (no schema change — `balance_journey()` is a pure computation over existing
  fields).
- Same-day follow-up: `--item` accepts a comma-separated list + duplicate-receipt heuristic
  tightened after real Dallas data (2026-08-21). Roy: "can I do the same for all other
  spirits, not Dallas only?" Investigated whether `Item.category` could drive an automatic
  "scan every spirit" mode and found it unreliably populated across this app's history —
  only items added via the enriched liquor catalogue or a supplier upload get a real
  `category.level1='spirit'`; items added via the original static catalogue or the plain
  item form very often have `category=None` — an automatic filter would have silently
  skipped real spirit items rather than named them honestly. Widened `--item` to accept a
  comma-separated list instead (`--item="Dallas,KC Ginger,Blue Ice"`), running the full
  diagnostic for each in one call; `--physical` now explicitly rejected when more than one
  name is given, since one physical count can't describe several different items at once.
  **Roy then ran the tool for real against Dallas and shared the output** — surfaced a real
  false-positive bug in the SAME-DAY duplicate-receipt heuristic: a wall of "duplicate"
  warnings for 20-unit receipts 30-46h apart, spanning over a week — not a mistake, just
  Dallas being restocked in the same standard crate size every day or two, completely
  normal for a fast-moving spirit. The 48h window couldn't tell routine restocking apart
  from a genuine same-sitting double-entry (which WAS present in the same output — two
  receipts of the same qty just 2 minutes apart). Tightened the window to 3h and sorted
  hits closest-gap-first so the most suspicious pair leads. Separately, the real output's
  `--physical=8` comparison against a system balance of 0 pointed the OPPOSITE direction
  from the original "16 vs 8" report — flagged to Roy as most likely an unrecorded recent
  delivery (physically on the shelf, never logged as a Receipt) given no receipt had been
  recorded since Aug 13 while sales continued through the 21st, not a duplicate-entry
  question at all. 2 new tests (`test_routine_restocking_30h_apart_not_flagged_as_
  duplicate` — a direct regression lock on Roy's own real-world shape; `test_same_sitting_
  double_entry_still_caught`), 3 more for the comma-separated `--item` support. No
  migrations.
- Responsiveness audit + mid-shift stock take (2026-08-21, live request before Roy travels
  to Monsoon Inn). Three asks in one: "audit the speed of responsiveness... navigating
  through sections"; "when staff click on hesabu stock during opening and closing shift,
  everything works seamlessly"; "add a stock take function for staff... mid service if it
  is not there already." **Performance**: found and fixed the same N+1 shape already closed
  for `stock_list()`/`home()` on 2026-08-09/10 (`item.current_balance()` called per item,
  each its own DB round-trip) at four more real, frequently-hit surfaces, all reusing the
  same proven `_batch_stock_metrics()` helper rather than four separate optimizations:
  (1) `stock_take_api()`'s GET (the exact endpoint the Hesabu Stock modal calls) — was
  issuing one query per item just to open the count form, on the single most time-pressured
  moment of a shift; (2) `kitchen_board()` — `current_balance()` per portion item AND per
  kitchen-batch item's raw-material source, plus a missing `select_related('raw_material_
  source')` (one query per batch item just to follow that FK) — Kitchen Board is one of the
  most-visited pages in the app, hit on nearly every checkout/shift/receive action;
  (3) `analytics_dashboard()`'s stock-health section — up to 3 separate `current_balance()`
  calls per item (out-of-stock check, low-stock check, velocity-ranking loop); (4)
  `bar_board_api()`'s active-waitresses block — a genuinely different shape (not item
  balances): walked every one of today's TableOrders one at a time, firing 2 extra COUNT
  queries per distinct waitress found, on the single most-polled endpoint on the busiest
  page — replaced with one aggregate query (`Count` with a conditional filter, grouped by
  waitress) regardless of order volume. None of the underlying Item/model methods were
  touched — every other caller in the app is completely unaffected, per this helper's own
  established scoping discipline. **Hesabu Stock open/close audit**: traced both boards'
  `openStockTake()`/`submitStockTake()` flow end to end — structurally sound, including the
  2026-08-16 modal-stacking race fix already in place; the N+1 fix above is the concrete
  "seamless" improvement, since a slow GET on a busy/flaky connection is what would have
  made it feel broken rather than merely slow. **Mid-shift stock take**: `ShiftStockCount.
  PHASE_CHOICES` gains `'midshift'` (migration 0171, additive) — a voluntary, informational
  spot-check any time during an open shift, distinct from the existing opening/closing
  phases. Safe by construction: every consumer that sums these into a real loss/variance
  figure (`keg_metrics.staff_shrinkage`'s bottle loss, `bar_z_report`'s day variance,
  `_missed_tasks_for_shift`'s "did you do your stock take" reminder) already filters to an
  EXPLICIT `phase='closing'`, never a bare "not opening" — so a midshift row is
  automatically excluded from all of them with zero other code change, same "excluded by
  construction" pattern already used elsewhere in this app (`Transaction` type `'Draw'`/
  `'Transfer'`). `stock_take_api()`'s phase validation widened to accept it. New persistent
  "📦 Hesabu Stock" button in both boards' live shift-status header (visible whenever `s.
  is_mine && s.status === 'OPEN'`, same `!IS_WAITRESS` scoping as the existing open/close
  offers — a waitress is a concurrent helper, not the stock custodian), calling the exact
  same modal/endpoint with `phase='midshift'`; the modal's own title/intro text branches to
  a distinct "Wakati wa Zamu" (during the shift) framing making clear it's informational
  only and doesn't affect the real shift-close reconciliation. Mirrored identically across
  `bar_board.html`/`kitchen_board.html` per this file's own counter-parity rule. 15 new
  tests across 4 test classes (`ShiftStockCountPhaseTest` +4 for midshift coexistence/
  exclusion, `StockTakeApiAndKitchenBoardBatchMetricsTest`, `BarBoardApiActiveWaitressBatch
  ingTest`, `AnalyticsStockHealthBatchMetricsTest`). One migration (0171, additive).
- Kitchen preset visibility — Gawa Kuku receiving gap (2026-08-21, live on-site at Monsoon
  Inn). Roy: Kuku's tile showed "KES 0" with "4 zimefichwa" (4 hidden) right after
  portioning a bird via Gawa Kuku, asked whether the earlier auto-close/stock-receipt work
  caused it. Traced directly: `kitchen_board()`'s cut-visibility gate (`_received_by_preset`/
  `_sold_by_preset`, the received-minus-sold anchor tally deciding whether a preset is
  sellable) only ever summed `KitchenStockReceiptLine.qty_received` — `PortioningEvent`
  (Gawa Kuku, built earlier the same session) creates real stock-adding Transactions per cut
  but writes to a completely separate model, `PortioningEventLine`, never counted here. Real
  stock portioned via Gawa Kuku correctly grew the item's own balance (`current_balance()`,
  confirmed correct in Roy's own screenshot — "21 Pcs") but was invisible to the PER-PRESET
  gate; the very next sale against any cut drained `_sold_by_preset` with nothing on the
  "received" side to offset it, pushing every preset's anchor tally to zero/negative and
  hiding all of them at once — falling back to a bare, preset-less tile at `item.
  selling_price` (KES 0, since Kuku's own base price is always 0 by design, per the
  2026-07-29 per-cut-costing entry). Confirmed NOT related to the KitchenStockReceipt auto-
  close fix — that only ever touches `receipt.status`, and this query was never filtered by
  status in the first place. Fixed by summing `PortioningEventLine.qty_produced` (same
  `tracks_stock_of` anchor-coalescing logic) into `_received_by_preset` — both the lifetime
  sum and the `restock_anchor_at`-windowed override (keyed off `PortioningEvent.created_at`
  instead of `KitchenStockReceiptLine.receipt.received_on`, the closest equivalent "when did
  this stock arrive" signal Gawa Kuku has). 1 new regression test reproducing the exact
  scenario end-to-end (portion via Gawa Kuku, sell one cut, assert both presets stay visible
  and `hidden_presets` is empty) — before the fix, this test failed exactly as Roy described.
  No migrations.
- Kitchen preset visibility — decisive fix + delegated ad-hoc expense recording, same-day
  urgent live follow-up (2026-08-21, Roy on-site at Monsoon Inn, mid-service, blocked). Live
  screenshot showed Kuku's tile at "KES 0" with "4 zimefichwa" AGAIN — every preset hidden,
  cart falling back to a bare preset-less "Kuku KES 0" line, minutes after the Gawa Kuku
  receiving-gap fix above shipped. Roy: "this going in circles everytime we change something
  really sucks." Root cause was structural, not a missed receiving source this time: `_is_
  visible(p)`'s gate — "show this preset only while its own received-minus-SOLD net anchor
  tally is positive" — has too many independent ways to legitimately drift negative (a
  deleted receipt, a tether added after old sales, a receiving mechanism the gate didn't
  know about yet, backdated catch-up sales) while the item's own real, authoritative
  `current_balance()` stays positive; every fix so far (2026-08-09, 2026-08-11 ×2,
  2026-08-12, 2026-08-21 earlier today) only taught the gate about one MORE receiving
  source, which just delays the next drift-triggered outage instead of closing the class of
  bug. Decisive fix: `_is_visible()` no longer nets received-minus-sold at all — a preset
  that has EVER been genuinely received under this item's per-cut regime (`ever_received`,
  `anchor in _received_by_preset` — still respects the original 2026-08-09 "never fabricate
  a preset that was never actually stocked" concern, locked in by the pre-existing
  `test_ledger_drift_hides_rather_than_fabricates`) now stays SELLABLE for as long as
  `item.stock_balance > 0` — real physical stock is the only gate a busy kitchen genuinely
  needs; the net tally is demoted to purely an owner-only diagnostic (`hidden_presets`,
  unchanged) rather than a hard block on the sell button. A preset truly never received
  under this item's own regime, or an item that's genuinely fully depleted
  (`current_balance() <= 0`), both still correctly hide — regression-locked by the two
  pre-existing tests of that exact shape, both re-run and confirmed still passing unmodified.
  New `test_ever_received_preset_stays_sellable_when_net_anchor_drifts_negative` reproduces
  the drift directly (extra stock via a plain no-preset Receipt, then enough sold via the
  tethered preset to push the net tally negative while real balance stays positive) and
  asserts both presets stay visible. Second live ask, same message: "the staff have no way
  of back dating expenses... I have been left with lots of recordings of both yesterday and
  today." `record_ad_hoc_expense()`/`ad_hoc_expenses_list()` (Matumizi ya Leo, 2026-08-09)
  have always supported a `date` field — the gap was pure ACCESS, both were `@owner_or_
  manager_required` with zero delegation option. New `UserProfile.can_record_expenses`
  (accounts migration 0064, default False, opt-in — matching `can_adjust_stock`/
  `can_manage_kegs`/every other delegated-oversight toggle in this app) lets the owner grant
  a trusted staffer this specific action. Removed `@owner_or_manager_required` from `record_
  ad_hoc_expense` (a decorator built for full-page views — HTML-redirects on failure, wrong
  for this AJAX/JSON-only endpoint, the same latent bug class already fixed once for
  `adjust_stock_balance`, 2026-08-11) in favor of an inline, JSON-friendly check requiring an
  open shift for the delegated staffer, same pattern as `can_adjust_stock`. `ad_hoc_expenses_
  list()` (read) widened the same way so a delegated staffer can see what they've already
  logged for the day without needing the owner. `edit_ad_hoc_expense` (correcting an
  already-recorded entry) deliberately stays owner/manager-only, matching the established
  "delegation covers the everyday action, correction stays a higher tier" pattern
  (`can_manage_kegs` not extending to Hariri/Tupa). Both `bar_board.html`/`kitchen_board.html`
  widened their Matumizi button/readout/modal-include gate from `is_owner` to the new
  `can_record_expenses` context var (`is_owner_or_manager or up.can_record_expenses`), passed
  from `bar_board()`/`kitchen_board()`'s own render context. 9 new tests
  (`test_ever_received_preset_stays_sellable_when_net_anchor_drifts_negative` on
  `PresetStockTrackingTetherTest`; `AdHocExpenseTest` gained 4 new tests — delegated-staff
  record, delegated-without-shift blocked, backdate still works for delegated staff, list
  access widened — plus `test_plain_staff_blocked` updated from asserting a 302 redirect to
  a JSON 403, matching the decorator removal). One migration (accounts 0064, additive). Full
  core+accounts suite re-run and confirmed passing before push.
- Tab checkouts (Quick Sell/Bar Board/Kitchen Board) now honor the backdate toggle
  (2026-08-21), live follow-up: Roy, re-entering a two-day paper sales log, "quick sell/bar
  orders has no back date when the item is in tabs drawer i am not sure about the bar board
  side." Confirmed both — all three counters' backdate mechanism (built 2026-08-07 through
  2026-08-12) deliberately excluded Tab/food_tab/bar_tab from day one, reasoning "an open
  running bill isn't something that already happened." That reasoning is right for the
  TAB'S OWN lifecycle (`BarTab.created_at` — the true moment it's entered into the system —
  is correctly left untouched) but wrong for what actually needed the correct date: the
  SALE's own revenue/stock/debt-aging impact, which is exactly what `created_at`/`date`
  drive everywhere else in this app. Widened the eligibility check in `views.py::
  quick_sell()`, `keg_views.py::bar_board()`, and `kitchen_views.py::_kitchen_checkout()`
  to include the tab payment-method values, and removed the `and not active_tab` guard at
  every sale-creation call site so the timestamp actually reaches the underlying
  Transaction (`KegBarrel.record_sale_locked`/`KitchenBatch.record_sale`/`ProduceBunch.
  record_sale_locked`/plain `Transaction.objects.create` — all already handled `created_at`+
  `date` together correctly from the 2026-08-12 date/created_at-drift fix, so no model-layer
  change was needed, only the call sites feeding them). Frontend fix needed TWO changes per
  template, not one — same recurring gap this app has hit before (2026-07-23 tabs-drawer,
  2026-08-12 direct-Deni-at-checkout): the backdate row's VISIBILITY toggle and the SEPARATE
  submit-time gate that actually puts the value in the POST body are two different code
  paths that can silently disagree — both updated in `quick_sell.html`, `bar_board.html`
  (kept split-payment's own `isCashOrMpesa` gate untouched, added a distinct
  `isCashMpesaOrTab` for backdate only, since a Tab sale still has no cash+mpesa split to
  make), and `kitchen_board.html`. Confirmed unrelated, no change needed: ad-hoc expense
  (Matumizi ya Leo) backdating already works for ANY past date, for both owner/manager and
  a `can_record_expenses`-delegated staffer — the `date` field has never had a lower bound,
  only ever falls back to today when blank/invalid/future. 6 tests rewritten from
  "backdate ignored for tab" to "backdate honored for tab" across
  `QuickSellCatchUpBackdateTest`, `KitchenBackdatedCheckoutTest` (plain portion item +
  KitchenBatch branch), and `BarBoardBackdatedCheckoutTest` — each now also asserts the
  resulting `BarTabEntry` is correctly linked to the backdated `Transaction`. No migrations.
- PWA install diagnosis (2026-08-21), live report: Roy uninstalled the app from a staff
  Android phone and reinstalled via Chrome — "it says installing but it does not show up...
  insistent on the add to home screen shortcut, could it be the internet was an issue."
  Re-verified the app's own PWA config is unaffected by anything shipped since the
  2026-08-21 real-browser audit earlier this session (manifest.json/sw.js/base.html
  untouched) — `beforeinstallprompt` firing and zero `Page.getInstallabilityErrors` under a
  real (non-incognito) Chromium profile still stand as the last confirmed state. Told Roy
  his own instinct is the most likely explanation: Android's real WebAPK install (what
  makes a true standalone app icon, as opposed to a browser bookmark) requires the phone to
  briefly reach Google's own WebAPK-minting service at the moment "Install" is tapped —
  distinct from just loading this app's own site — and Chrome silently falls back to
  offering "Add to Home Screen" instead of ever explaining why when that round-trip fails,
  which matches his exact description. No code to fix on this app's side for that failure
  mode. Gave a concrete on-device checklist: retry on strong/stable WiFi rather than mobile
  data; check `chrome://apps` in case it silently DID install without a launcher refresh;
  clear Chrome's stored site data for the domain first, in case a broken install record
  survived the earlier uninstall; confirm Chrome itself is reasonably up to date. No code
  changes.
- Counter Cash (Petty Cash) backdate + Kuku preset-correction guidance (2026-08-21, live
  follow-up mid-catch-up-entry). Roy: "the counter cash backdate plus matumizi is not there
  on the staff's side" — while separately mistakenly selecting "Drumstick" instead of "Half
  Chicken Leg" on Kuku (both KES 150, easy to confuse) and asking for a revert-and-redo.
  **Kuku correction — no code needed, existing tool**: `correct_transaction_preset()`
  ("🔄 Kipande" button, already visible on every direct-sale row in the "🕐 Malipo ya Hivi
  Karibuni" panel) reassigns BOTH `Transaction.preset` AND `Transaction.qty` together in
  place — fixes the wrong attribution AND auto-corrects the balance in one tap, without
  touching the already-correct backdated timestamp at all. Recommended over a revert-and-
  redo (safer, faster, no risk of a second mistake on re-entry). Separately, "Drumstick" is
  a leftover preset from the old Meatco per-cut era, no longer part of this business's
  current stocking pattern — `Transaction.preset` is `on_delete=SET_NULL` (migration 0133),
  so Roy can safely delete it from Kuku's Edit Item preset table without touching any
  historical revenue/cost data. **Counter Cash backdate — genuinely missing, built now**:
  `PettyCash.created_at` is `auto_now_add=True`, which Django enforces at the DB layer and
  can NEVER be overridden by application code — every till-affecting figure (`_reconcile()`/
  `till_expected_cash()`) correctly, deliberately keeps reading it unchanged, so a backdated
  Counter Cash entry must never move TODAY's live till. `PettyCash.date` already existed as
  a field but was never settable from the request — `record_petty_cash()` now accepts an
  optional `date` (same "never blocks, never future, falls back to today" contract as
  `record_ad_hoc_expense()`'s own). New `shift_views._backdated_petty_cash_total_for_shift()`
  mirrors `_ad_hoc_expense_total_for_shift()`'s exact pattern (additive fold-in at Shift
  History/Z-report display time only, never the live in-progress panel or till), with one
  real difference PettyCash forces: unlike `BusinessExpense` (never read by `_reconcile()`
  at all), `_reconcile()`'s own `_petty_qs` ALREADY sums approved `PettyCash` by
  `created_at` within the shift's segments — so the new fold-in explicitly excludes any
  entry whose `created_at` already falls inside the shift's own live window (already
  counted once), leaving only genuinely backdated entries (recorded at some OTHER real
  moment, dated for a day this shift covers). Wired into `shift_history()` (per-row
  `backdated_petty_cash`, folded into the existing `expected_cash_after_expenses`/
  `variance_after_expenses` alongside ad-hoc expenses) and `bar_z_report()` (per-shift +
  a deduped DAY-level query, same overlap-safe pattern as `day_cash`/`day_ad_hoc_expenses`).
  New "🕐 Hii ilitokea siku nyingine" toggle on the shared `petty_cash_modal.html`/
  `petty_cash_js.html` (included by all three boards — Bar/Kitchen/Quick Sell — so this
  fixes staff access everywhere at once, no per-board change needed; petty cash recording
  has never had any permission gate, confirmed already staff-usable, only backdating was
  the real gap). Separately confirmed unrelated: `record_ad_hoc_expense`'s own permission
  gate (`can_record_expenses`, shipped earlier the same day) — its button/modal include is
  gated per-staffer in Staff Permissions; Shavel Atis's board correctly hid it because the
  toggle hadn't been switched on for her yet, not a bug — told Roy to enable it there if he
  wants a specific staffer to have Matumizi. **Bonus test-suite fix, unrelated, found by
  the full-suite run**: `KitchenStockReceiptAutoCloseTest.test_backdated_sale_still_counts_
  after_auto_close` hardcoded a fixed "10:00" wall-clock anchor for its backdated
  transaction — the same day-boundary/time-of-day flakiness class already documented
  repeatedly in this file (`PettyCashReviewUndoTest`, `BarZReportOverlappingShiftsTest`,
  `AdHocExpenseDayReconciliationTest`) — genuinely fails when the suite happens to run
  before 10am Nairobi time, since the transaction's timestamp then lands AFTER
  `maybe_auto_close()`'s own `closed_at` (stamped at real "now"), pushing it outside the
  receipt's revenue window; fixed by anchoring to `timezone.now() - timedelta(minutes=1)`
  instead, same fix pattern as the other three. 10 new tests (`PettyCashBackdateTest`) —
  staff can record a backdated entry, no-date/future-date fallback, the already-counted-
  same-day-entry exclusion (the core double-count-prevention logic), a genuinely backdated
  entry counted correctly, pending/rejected entries never counted, a different-day entry
  excluded, Shift History's adjusted expected-cash/variance, the Z-report's day-level
  figure (built around a realistic past-day shift + today's catch-up entry, not a same-day
  fixture — the day-level window is the WHOLE day, wider than any one shift's own narrow
  segment, a real design nuance the first draft of this test got wrong), and the live-
  panel/close-shift regression lock mirroring `AdHocExpenseDayReconciliationTest`'s own.
  No migrations (`PettyCash.date` already existed).
- Owner-facilitated sales attribution guide, shift modal + dashboard revenue
  (2026-08-22). Live Q&A, then a build request: Roy asked three precise
  questions about what happens to a staffer's shift-modal Cash Sales/M-Pesa
  figures when the owner (who never needs to open a shift to sell) is also
  selling — concurrently with an open staff shift, before the staff arrives,
  and both at once. Traced `_reconcile()` directly and confirmed all three
  scenarios: owner sales during the window already correctly blend into
  cash_sales/mpesa_sales/expected_cash (a real till doesn't care who rang it
  up — `txns` has no `recorded_by` filter), a pre-arrival owner sale is
  structurally excluded (`_shift_active_segments()` always starts at
  `shift.started_at`), and the combined case is just the union of both.
  Roy then confirmed he wants a distinct guide line, with an explicit,
  precise constraint given as a worked example: "if the shift modal is
  showing cash sales 500 and the owner's sales are 200 it should just mean
  that in that 500... 200 is part of it not separate from it" — i.e. a pure
  attribution ANNOTATION, never a second number added on top. **Backend**:
  `_reconcile()` computes `owner_cash`/`owner_mpesa`/`owner_credit` from the
  SAME `txns` queryset (same segments, same station scope) filtered to
  `recorded_by_id` in the set of every owner-role `UserProfile.user_id` for
  the business (a business can have more than one — Bosco is also
  owner-role at Monsoon Inn, confirmed from earlier session history) —
  guaranteeing it can never drift from cash_sales/mpesa_sales/credit_sales
  themselves; left at 0 for the owner's own shift (self-attribution is
  meaningless). Returned as `owner_facilitated_cash/mpesa/credit/total` —
  additive display fields, the underlying totals are completely unchanged.
  Wired into all three JSON response builders (`active_shift_api()`'s
  `is_mine: True` and owner-proxy `is_mine: False` branches, `close_shift()`)
  and into `all_shifts_data` (the owner dashboard's "Active Shifts" meter).
  **Same-day widening** ("oh and the same for mpesa" / "and debt placement
  and recovery too" / "im short every transactional aspect of the shift
  modal+revenue count on the dashboard"): (1) M-Pesa's own guide note was
  already computed but two display surfaces only ever showed a bare Cash
  Sales stat with no M-Pesa box at all — fixed both. (2) "Debt placement" is
  already `owner_facilitated_credit` (a credit sale IS placing debt); added
  the missing "recovery" half — `owner_facilitated_debt_recovered_cash/mpesa`,
  computed the identical way from `debt_qs` (CustomerDebtPayment) filtered
  to the same owner-id set — plus `owner_facilitated_expected_cash` (=
  owner_cash + owner_debt_recovered_cash), the owner's own share of the
  Inayotarajiwa/Expected Drawer figure itself. (3) Extended to the
  DASHBOARD's own revenue surfaces, not just the per-shift modal: new
  `_window_revenue_owner_facilitated()` (mirrors `_window_revenue()`,
  filtered the same way) feeds a new `owner_facilitated_revenue` field on
  `station_revenue_window_info()` (the home dashboard's "🍺/🍗 Revenue — vipi
  hesabu hii ilipatikana?" disclosure); `till_expected_cash()` (the
  CONTINUOUS "what's in the till right now" figure — Roy's own 2026-08-12
  instruction that "shifts and counter cash modals are very fine as they
  are" was about not touching its anchor/window MATH, not about withholding
  a pure display addition) gained `owner_facilitated_cash_sales`/
  `owner_facilitated_debt_recovered` on its `breakdown` dict, surfaced in
  the same disclosure. **Frontend**: `bar_board.html`'s `renderShiftPanel()`
  (the live shift-status panel) gained small "(ikiwemo KES X kutoka kwa
  mmiliki)" sub-notes under Cash Sales, M-Pesa, Mikopo Mapya, Deni
  Zilizolipwa, and Inayotarajiwa — each only rendered when its own
  owner-facilitated value is > 0, with a tooltip making the "already
  included, not additive" contract explicit. The close-shift-open modal's
  small pre-close summary box (previously Cash Sales/Float/Expected only)
  and the close-shift RESULT panel (previously had NO M-Pesa stat box at
  all, only Cash Sales) both rebuilt to show the full Cash/M-Pesa/Mikopo
  Mapya/Deni Zilizolipwa/Float/Expected set with the same guide notes.
  `home.html`'s Active Shifts meter row gained a compact "👤 mmiliki: KES X"
  combined note (cash+mpesa+credit+debt-recovered summed into one figure,
  to keep the already-dense per-shift row from needing 5 separate
  tooltips) plus per-figure `title=` tooltips on the Cash/M-Pesa spans
  themselves; both revenue-info and till-breakdown disclosure panels gained
  their own non-additive note lines. `kitchen_board.html` mirrored
  `bar_board.html`'s three changes verbatim, per this file's own
  counter-parity rule. 15 new tests
  (`OwnerFacilitatedSalesAttributionTest`) — blended-not-doubled invariants
  for cash/mpesa/credit, pre-shift-start exclusion, owner's-own-shift
  self-attribution exclusion, multi-owner-profile summing, station
  isolation, debt-recovery attribution and its role in
  owner_facilitated_expected_cash, direct end-to-end checks that
  `active_shift_api`/`close_shift`/`all_shifts_data`/
  `station_revenue_window_info`/`till_expected_cash` all carry and agree on
  the new fields, and that `shift_history()`/`bar_z_report()` (per-row AND
  the deduped day-level total) both surface it too. Also extended, same
  pass: `shift_history.html`'s per-shift cards and `bar_z_report.html`'s
  per-row table cells + day-summary tiles now show the identical
  non-additive guide notes. No migrations (pure computation over existing
  `Transaction.recorded_by`/`CustomerDebtPayment.recorded_by`, both
  already-existing fields).
- Shift-change stock-imbalance accountability — gap-aware attribution,
  symmetric accountability engine, and a non-binding payroll suggestion
  (2026-08-22). Live Q&A: Roy described the exact scenario — staff A closes
  a 12h shift and counts stock, the business sits empty for a few hours,
  staff B opens and counts again, and a variance appears. Who explains it?
  Traced `attribute_variance_shift()` (already correctly answers "A or B" by
  walking back to whichever shift last touched the item) and found it can't
  tell "A's count was sloppy" apart from "the loss happened during the
  unattended gap itself" — both land on the same "ask A" outcome — and, worse,
  a single legitimate OWNER sale (the owner never needs a shift to sell)
  landing after B's shift technically started could fool the coarse "has
  this shift touched the item" check into blaming B for a loss that predates
  her entirely. Roy's own framing on the fix, and the two things to build:
  "so long as there is a trail of the sales, the system should make the gap
  make sense... all parties concerned should be aware of the same," and "the
  real accountability tool needs to be symmetric... offered at both open and
  close, not just close." Then, once explanation/affirmation is genuinely
  absent: "attribute the gap to the staff's own track record for the period
  ... this would just be a suggestion from the system... not a permanent
  declaration."

  **Gap-aware reconciliation** (`core/stock_take_views.py`): new
  `_immediately_preceding_shift()` (the literal "who had custody right
  before this gap" shift on the SAME station — deliberately distinct from
  `attribute_variance_shift()`'s own walk-back, which can skip past this
  shift to an even earlier one) and `_gap_reconciled_variance()` — for an
  OPENING-phase count specifically, compares the prior shift's own physical
  CLOSING count (`ShiftStockCount`, anchored on its real `recorded_at`, not
  the shift's `ended_at`) to every real Transaction recorded since (sales by
  the owner included, regardless of whether they landed before or after the
  new shift's own `started_at`) to compute an `expected_now` figure — a
  sharper, trail-aware anchor than the coarse book-balance check, since it's
  anchored to A's own MOST RECENT VERIFIED physical truth rather than the
  item's full lifetime transactional history (which can carry its own,
  unrelated, older drift). A residual that survives netting the real trail
  is the genuine, still-unexplained gap; one that nets to exactly zero is
  auto-resolved right there — `StockVarianceQuery.kind='gap'`,
  `status=RESOLVED`, `owner_accepted=True`, a system-generated `gap_note`
  (new field, permanent — never overwritten by a later human note) — nobody
  asked to respond, but the row still exists and is notified to owner + both
  staff so "all parties concerned" see the reconciliation, per Roy's own
  words, instead of the gap being silently dropped. A live regression test
  (`test_owner_sale_after_shift_b_started_does_not_wrongly_blame_b`) proves
  the exact failure mode described above: a transaction timed after B's own
  `started_at` DOES satisfy `attribute_variance_shift()`'s coarse check in
  isolation (confirmed directly), but gap-reconciliation still correctly
  attributes the residual to A. Falls back to ordinary `kind='shift'`
  attribution whenever the prior shift never physically counted the item at
  all — nothing to reconcile against. New `StockVarianceQuery.kind`/
  `gap_note` fields (migration 0172, additive). Also fixed in the same pass:
  `attribute_variance_shift()`'s walk-back fallback had NO station filter at
  all — on a combo bar+kitchen business it could attribute a bar item's
  variance to whichever counter's shift happened to be chronologically most
  recent, regardless of station; now scoped via the item's own store (a
  `keg_barrel` is always bar) through the existing `_station_q()` helper.

  **Symmetric accountability engine**: the quick "📦 Hesabu Stock" modal on
  Bar/Kitchen Board's shift open/close flow (`stock_take_api()`,
  `core/shift_views.py`) used to be purely informational for a plain item —
  a variance shown once on screen and then forgotten, nobody notified,
  nobody asked to explain; only the SEPARATE dedicated guided page
  (`start_stock_take()`, owner/manager only) fed the real `StockVarianceQuery`
  accountability engine, and that page was only ever linked from the
  CLOSE-shift modal, never the open one — so Roy's exact described sequence
  (A closes+counts via the quick modal, gap, B opens+counts via the quick
  modal) produced ZERO accountability record under the pre-existing code.
  Extracted the guided page's per-item attribution + StockVarianceQuery-
  creation + notification-batching logic (previously ~180 lines inline in
  `start_stock_take()`'s POST body) into one shared, reusable engine —
  `run_accountability_stock_take()` plus its helpers `_process_variance_row()`
  and `_send_variance_notifications()` — now called by BOTH surfaces:
  `start_stock_take()` (unchanged `phase=None`, preserving its exact
  pre-existing behaviour byte-for-byte) and `stock_take_api()` (new,
  `phase='opening'`/`'closing'` — gap-aware reconciliation only ever applies
  for `'opening'`; `'midshift'` stays purely voluntary/informational,
  matching its own already-documented design intent, per this file's own
  "excluded by construction" convention). `stock_take_api()`'s POST gained
  the same `claim_checkout_token` double-submit guard every other real
  accountability-creating endpoint in this app has (a duplicate submission
  would otherwise double-notify both staff and owner for one physical
  count); `submitStockTake()`'s JS in both boards gained a matching
  idempotency token per this app's own established convention. The modal's
  own result panel now says plainly when a variance was sent for
  explanation vs auto-reconciled, instead of showing a number and moving on.

  **Non-binding payroll suggestion + staff transparency**: found and fixed a
  real pre-existing bug while building this — `_staff_contribution()`'s
  `variance_loss_kes` (Haki's staff-accountability tally, built 2026-07-26)
  summed EVERY decrease-direction variance attributed to a staffer's shift
  regardless of resolution, including a row the owner had already ACCEPTED
  (`owner_accepted=True` — a believed, legitimate explanation with a real
  corrective transaction created, e.g. an unrecorded cash sale — not a real
  loss at all, just a paperwork catch-up) — overcounting every affirmed row
  as "loss" against a staffer since the figure was first built. Roy's own
  explicit rule for this session's feature applies retroactively to the
  existing figure too: only "no explanation nor affirmation from the
  required parties" should count — fixed to `.exclude(owner_accepted=True)`,
  so a still-pending, staff-responded-but-not-yet-reviewed, or dismissed row
  all correctly still count, and only a genuine owner affirmation clears one.
  `_staff_contribution()` now also returns `unaffirmed_variances` (the
  actual underlying rows, not just the total) so every consumer can show
  exactly which item/date each contribution came from. New per-payroll-
  period computation in `staff_contribution_report()` (`core/haki_views.py`,
  new `_period_date_range()` helper converting a `'2026-08'` period string
  to real dates) — `suggested_salary` = configured salary amount minus that
  SPECIFIC period's own unaffirmed variance total (never the report's own
  adjustable date-range filter, which the owner may have widened for an
  unrelated reason) — surfaced in the salary-payment modal
  (`haki_contribution.html`) as a clearly-labeled, non-binding note with a
  "Use suggested amount" button that only pre-fills the amount field; the
  owner can still type any amount at all, confirmed by a dedicated test that
  posts the FULL, non-discounted amount successfully even with an active
  suggestion on record. Staff-side transparency ("so that they do not claim
  that they were paid unfairly"): `haki_kazi_yangu.html` (Kazi Yangu, self-
  service) previously showed NONE of `wastage_kes`/`variance_loss_kes` at
  all despite both already being computed into that page's own context —
  new "📊 Tofauti za Stock Ambazo Hazijaidhinishwa" card lists every one of
  the staffer's own still-unaffirmed rows (item, date, status, and — for a
  `kind='gap'` row — a "gap between shifts" tag) with the same total the
  owner's payroll suggestion is built from, so a staffer can always trace
  exactly why. `staff_journey.html` (owner-facing tenure report, which
  already showed the — now corrected — total) gained the same per-row
  itemised list. `stock_variance_respond.html`/`stock_variances_pending.html`
  both show a "🔀 pengo baina ya zamu" badge and the system-generated
  `gap_note` for a `kind='gap'` row, at every lifecycle stage (pending,
  responded, resolved). 28 new tests across 6 test classes
  (`AttributeVarianceShiftStationScopingTest`, `GapReconciledVarianceTest`,
  `RunAccountabilityStockTakeGapTest`, `StockTakeApiAccountabilityTest`,
  `VarianceLossKesAffirmationTest`, `PayrollVarianceSuggestionTest`) —
  including the exact "owner sale technically inside the new shift's own
  window must not fool attribution" regression lock, the fully-explained-
  auto-resolve vs genuine-residual split, idempotent double-submit
  rejection, `midshift` never creating an accountability record, the
  accepted/pending/dismissed affirmation matrix, and the suggestion's own
  non-binding guarantee. Keg barrel weighing at shift changes explicitly
  named by Roy as needing its own separate study before building the same
  treatment there — deliberately NOT touched this pass. One migration
  (0172, additive).
- Composite staff recognition tiers (2026-08-22, same-day follow-up). Roy,
  after the gap-accountability sprint above shipped: "what are the merits
  set that determine an outstanding employee?" Traced the existing
  recognition mechanism and answered plainly — 4 independent milestone
  badges (`_check_and_fire_recognition()`: 30+ shifts, KES 50k+ revenue/
  month, KES 10k+ debts recovered, clean keg handling) with no combined
  score and, until this same week's own fix, no negative side at all.
  Recommended combining the positive milestones with the newly-corrected
  negative ledger (dismissed variances, unaffirmed variance loss, wastage,
  rejected petty cash) into one composite tier rather than separate
  disconnected nudges, and asked Roy for the tradeoff rule. Roy: "no need
  to add more raw metrics, things are already good as is... one or two
  dismissed variances should not knock the staff out, lowering the tier is
  just enough... I am not sure what the disqualifying/weighting rule should
  be, maybe you guide me on that so long as it is sensible, logical and
  fair" — explicitly authorizing the design, with the one hard constraint
  that 1-2 minor incidents must only ever lower the tier, never disqualify.

  New `compute_staff_recognition(contrib)` (`core/haki_views.py`) — a pure
  function over an already-computed `_staff_contribution()` dict, so it
  needed no new queries or migration. Points (positive side, capped at 100
  before deductions): consistency (shift count, full marks at the existing
  30-shift milestone threshold, up to 30 pts), revenue (full marks at the
  existing KES 50k threshold, up to 40 pts — the single biggest factor,
  since it's the most direct measure of contribution), debt recovery (full
  marks at the existing KES 10k threshold, up to 20 pts), clean keg
  handling (flat 10-pt bonus, 0 for a non-keg business). Deductions are
  GRADUATED, not linear, directly implementing Roy's own rule: for both
  dismissed stock variances and rejected petty cash entries, the first two
  incidents cost only 3 points each (a real but small ding — matching "one
  or two should not knock the staff out"), while the third and beyond cost
  8 points each — a genuine repeated pattern is treated as materially
  worse than an isolated mistake, not just added up. Unaffirmed variance
  loss and wastage are deliberately scored as a PERCENTAGE OF THE STAFFER'S
  OWN REVENUE, never a flat KES figure — the same KES 500 gap is a much
  bigger red flag against a slow KES 5,000 month than a busy KES 100,000
  one, and a flat-KES rule would unfairly penalize a business's highest
  performers simply for handling the most stock. A separate "pattern cap"
  — 3+ dismissed variances, OR 3+ rejected petty cash, OR unaffirmed
  variance loss exceeding 5% of revenue — bars ONLY the top (gold) tier
  regardless of point score, and is drawn precisely at PATTERN, never at a
  single incident, so it can never fire from 1-2 alone; it does not
  disqualify from a tier entirely, satisfying Roy's constraint exactly.
  Returns `{'tier', 'tier_label', 'score', 'capped', 'breakdown'}` — five
  tiers (unrated below a 5-shift minimum — "simply not enough data to rate
  fairly" — then gold/silver/bronze/developing by score) — `breakdown` is a
  list of `(label, points, is_deduction)` tuples so the score is always
  explainable to both the owner and the staffer, never just asserted,
  matching this app's own accountability-and-transparency standard
  established throughout the gap-accountability sprint just above.

  Wired into all four `_staff_contribution()` call sites: owner's staff
  ledger (`staff_contribution_report()`), Kazi Yangu self-service
  (`my_work_and_pay()`), the H4 shareable/printable recognition statement
  (`haki_recognition_statement()`), and the owner's full-tenure report
  (`staff_journey()`) — each attaches `contrib['recognition'] =
  compute_staff_recognition(contrib)` right after the underlying
  `_staff_contribution()` call. Template display added to all four:
  `haki_contribution.html` shows a colour-coded tier badge (gold/silver/
  bronze/red for developing) next to the existing milestone badges on each
  staffer's card, plus a `<details>` disclosure of the full points
  breakdown (and a "imezuiliwa kufikia dhahabu" note when the pattern cap
  is active); `haki_kazi_yangu.html` shows the same badge + breakdown to
  the staffer themselves, right where the existing unaffirmed-variance
  transparency card already lives; `haki_statement.html` shows the tier
  prominently at the top of the printable/shareable statement itself, since
  this is literally the artifact meant to recognize good performance;
  `staff_journey.html` shows the badge in the owner's tenure summary. 17
  new tests (`StaffRecognitionTierTest` — unrated below minimum, gold for a
  clean strong record, the direct regression lock for Roy's own 1-2-never-
  disqualifies rule, the pattern cap firing at 3+ dismissed/3+ rejected/
  >5% variance and NOT firing at 2, the same-KES-hits-low-revenue-harder
  proportional-scoring proof, score bounded to [0,100] under extreme
  inputs, and breakdown explainability; `StaffRecognitionWiringTest` —
  confirms `recognition` genuinely reaches all four real pages via a live
  HTTP round-trip each, not just the scoring function in isolation). No
  migrations (pure computation over existing `contrib` dict fields — no new
  model fields). Full core+accounts suite (2270 tests) re-run and confirmed
  passing.
- Fix: manager shift-open capping a bartender's own sales attribution
  (2026-08-23), live report with a Monsoon Inn screenshot: manager Dush
  Master opened his own bar shift (required to sell, per the existing
  manager-must-have-a-shift-to-sell gate) purely to be present/supervise
  while bartender Susan was already actively selling on the same till —
  his shift modal immediately showed real Cash/M-Pesa/Deni figures
  (KES 310/450/480) that were actually Susan's ongoing sales, mis-
  attributed the moment his later shift-open capped hers. Root cause:
  `_shift_active_segments()`'s "the most-recently-opened shift on a
  station owns every moment since it opened" rule is correct for a
  genuine handover (one bartender relieving another) but was only ever
  exempted for `role == 'waitress'` (2026-08-08) — a manager opening a
  shift on an already-staffed counter hit the exact same failure shape
  the waitress fix was built for, just never extended to cover him.
  Fixed by widening BOTH existing waitress exemptions to a shared
  `NON_CUSTODIAN_ROLES = ('waitress', 'manager')`: (1) a manager's
  shift-open never caps another shift's already-open attribution on the
  same station (the literal reported bug), and (2) his OWN attribution
  correctly nets to zero for any stretch a real custodian (any role
  outside this set) is concurrently open on that station — same
  "Muda: 0h 00m" pattern already shown for a waitress. Unlike a
  waitress (always exempted), a manager who is genuinely the SOLE open
  shift on a station IS the real custodian and accrues normally, same
  as ordinary staff — the exemption only fires while he's actually
  joining someone already there. Extended the identical reasoning to
  `open_shift()`'s opening-float variance alert: a manager JOINING an
  already-active real custodian is counting cash that's already
  mid-session (not an independent till), so the >KES 500 variance
  comparison is disregarded exactly like it already is for a waitress —
  but only when actually joining one (tracked via a new
  `joining_real_custodian` flag computed from the same overlap-scan the
  existing "another staffer already has this station open" warning
  already runs); opening alone still gets the real comparison. 8 new
  tests (`ManagerShiftDoesNotCapRevenueTest` — the literal reported bug
  reproduced end-to-end, sole-custodian accrues normally, a genuine
  staff handover still caps a manager exactly like anyone else;
  `ManagerOpeningFloatVarianceDisregardedTest` — joining-disregarded vs
  opening-alone-compared-normally) plus the full pre-existing
  `WaitressShiftDoesNotCapRevenueTest`/`WaitressOpeningFloatVariance
  DisregardedTest`/`SegmentedShiftReconcileTest` suites re-run and
  confirmed passing unmodified. No migrations. 2275 tests pass (core +
  accounts).
- Eight-item System Updates batch (2026-08-23/24), from a detailed Roy-approved
  spec covering customer profiling, debt reminders, wall-QR search, owner
  consumption limits, an app-wide customer search engine, and a full bar-system
  audit ("Proceed as you see fit, I approve"). **(1) Customer Profiling**: new
  `core/customer_profile.py` — `customer_transaction_history()` (date/time/item/
  served-by/recorded-by, reusing the `tab_entry__tab__served_by` vs
  `recorded_by` distinction), `customer_payment_history()`, `customer_summary()`,
  `Customer.ledger_token` (migration 0173) + `ensure_ledger_token()` for a
  public, token-authenticated per-customer ledger page. New
  `core/customer_profile_views.py`: `customer_journey` (staff-facing, any
  station — Roy's explicit privacy/scope call: "customers have been asking
  for one simple thing, 'can you search for me in your system?'"),
  `customer_lookup_api` (search-as-you-type), `customer_ledger_public` (the
  customer's own token page). **(2) Debt reminder enforcement**: new
  `DebtReminderLog` (migration 0173) + `fire_debt_reminder()`/`require_
  reminder_before_flagging()` in `debt_views.py` — a customer must be sent a
  reminder before being flagged a defaulter or written off; wired into
  `void_tab()` and `_execute_write_off_approval()` BEFORE the erasing logic
  runs (a bug in my own first draft — placing the check AFTER meant the
  debt was already gone by the time `_get_customer_debt_data()` computed
  what to remind about, so it silently never fired; caught by my own
  end-to-end regression tests, not by Roy). **(3) Wall-QR search
  enrichment**: `find_tab_search()` now returns date/time/amount (via the
  real debt-tracker aggregate, not the stale `unpaid_total()`), a `kind`
  (active tab vs debt) and cross-links between the two; `find_tab.html`
  splits results into "💳 Deni" and "🍺 Bili zinazoendelea" sections.
  **(4) Owner consumption accountability**: new `core/owner_limits.py` —
  per-owner amount + time-window limit (`Business`/`UserProfile` fields via
  accounts migration 0065), hard-blocks `record_owner_consumption()` once
  exceeded, emails the owner on limit-reached and on any transfer proposed
  into their own name (name-recognition against `Customer.is_owner_alias`
  reused from the earlier Mmiliki Alichukua work). **(5)/(6) App-wide
  customer search** — the same `customer_journey`/`customer_lookup_api`
  from item 1, deliberately open to ALL staff roles, wired into the bar
  interface and dashboard alike, per Roy's explicit privacy decision.
  **(7) Tab transfer audit**: fixed a live bug — a transfer stuck PENDING
  forever when its destination tab had ALREADY been separately settled
  before anyone responded. `tabs_list()` widened to surface a settled tab
  with a still-pending INCOMING transfer (`already_settled` flag);
  `_cancel_pending_transfers_for_tab()` gained an `include_incoming` param,
  wired into `void_tab()`/`remove_tab_entry()` so a transfer can no longer
  orphan itself against a tab that's gone. **(8) Full bar-system audit**
  (money-path idempotency / state-transition completeness / access-control
  scoping, the same three-theme structure this app's systemic audits always
  use): `place_table_order()` gained `claim_checkout_token` (the highest-
  severity finding — a genuine duplicate-order/revenue risk with none
  before); `confirm_till_count()` likewise; `OwnerConsumptionTransferRequest.
  _siblings()`'s non-batch branch gained the same `status='PENDING'` guard
  the batch branch already had (a resolved request could otherwise be
  re-accepted or rejected-after-acceptance with nothing reversed);
  `_accept_to_owner()` now refuses a voided transaction; `void_owner_
  consumption()` now cancels any pending `OwnerConsumptionTransferRequest`
  referencing the voided draw, mirroring `void_tab()`'s own inverse-action
  discipline. `waitress_screen.html`'s `placeOrder()` rewritten to hold ONE
  idempotency token across retries (was minting a fresh one per attempt,
  defeating the whole point of the guard) with duplicate-response handling
  treated as success, not failure. 55+ new tests across
  `CustomerProfilingTest`/`DebtReminderEnforcementTest`/
  `WallQrSearchEnrichmentTest`/`OwnerConsumptionLimitTest`/
  `BarAuditIdempotencyAndStateTest`. Migrations: core 0173 (`Customer.
  ledger_token`, `DebtReminderLog`), core 0174 (`Transaction.consumed_by`),
  accounts 0065 (owner consumption limit fields).
- Live fixes, same session (2026-08-24): two bugs Roy caught via a live
  screenshot, both fixed same-day. **Template comment leak — critical,
  customer-facing**: Django's `{# ... #}` comment syntax is single-line
  only; a multi-line one is NOT parsed as a comment and renders as literal
  text — exactly what Roy saw on a public receipt page. A project-wide scan
  found 7 instances (6 written this session, 1 pre-existing since
  2026-08-21 in `kitchen_viability.html`, leaking unnoticed the whole
  time), all converted to `{% comment %}...{% endcomment %}`. New
  `TemplateCommentLeakTest` — a permanent regression scan of every `.html`
  under `templates/` for this exact pattern, so this class of bug can never
  silently recur. **Partial-paid transfer quoting the wrong amount**: "debt
  tab transfer is not transferring partial debt payment of an item, it is
  transferring the whole item price when the customer whom the debt is
  being transferred from had paid partially." Root cause:
  `BarTabEntry.split_and_transfer_locked()`'s full-item (`paid_amount==0`)
  branch and `TabTransferRequest.propose_whole_tab_locked()` both used
  `entry.amount` (the ORIGINAL price) with zero regard for `entry.
  amount_paid` (what had since been collected via the debt tracker) when
  stamping the proposed transfer's own `amount` field. New `BarTabEntry.
  remaining_amount()` — `max(0, amount - amount_paid)` — is now the one
  definition used by both paths, so a partial payment can never be quoted
  away in the transfer flow. 6 new tests
  (`PartialPaidTransferAmountTest`).
- Waitress History enhancement — recorded-by, then all-staff + served-by
  (2026-08-24). Roy: "I need you to enhance waitress capabilities... start
  with improving the history section, by showing per transaction who
  recorded it so that the waitress is not left hanging between counter
  staffs." `transaction_history()` gained `select_related('recorded_by',
  'tab_entry__tab__served_by')` and a `served_by_name` per row (`served_by`
  = whose tab this was — often a bartender — `recorded_by` = whoever
  actually keyed the sale in; the two routinely differ on a busy counter,
  same distinction `customer_profile.py`'s journey already makes).
  `transaction_history.html` gained "Served by"/"Recorded by" columns. I
  then offered 4 further recommendations (table status chips, a
  ready-to-serve push notification, her own daily tally, split/merge-order
  flagging) — Roy's own next reply clarified his actual ask was narrower:
  "all of them if they are not there already, I just need all staff to
  access History and the transactional history should show who served and
  who recorded" — i.e. widen ACCESS to every staff role, not build the 4
  suggestions (deferred, unscoped, not started). Added `transaction_
  history` navbar links for `is_waitress` and `is_kitchen_staff` (both
  mobile/desktop blocks in `base.html` — every other role already had it).
  9 new tests (`WaitressTransactionHistoryRecordedByTest`) including a
  `CaptureQueriesContext` regression lock proving the new columns cost
  nothing extra per row (select_related, not N+1).
- Backfill for the partial-paid-transfer fix — historical data, not just
  code (2026-08-24, same-day follow-up). Roy, with a live receipt
  screenshot (#1888, Monsoon Inn): "I need a backfiil command for an
  already transfered debt to debt that was partially paid before, that was
  done before the change we made... 30bob was already paid partially in
  the debt tracker profile for that customer I just transferred the
  remaining 50 to Marley's Debt but it showed 80 not 50." Traced
  end-to-end before writing anything: `_do_settle_debt_payment()`'s FIFO
  reconciliation (`_reconcile_tab_entries_for_debt_payment()`) already
  correctly persists a partial cover into `entry.amount_paid` via `F()`,
  and `_get_customer_debt_data()`'s tab-linked walk already recomputes
  `remaining = txn.revenue() - entry.amount_paid` LIVE at read time — so
  the actual money owed was never wrong, and `TabTransferRequest.accept()`
  is a one-field reassignment (`entry.tab = dest_tab`) that never touches
  `amount`/`amount_paid` either. The bug is entirely in the STORED
  `TabTransferRequest.amount` snapshot field itself: on the pre-2026-08-23
  code, both `split_and_transfer_locked()`'s full-item branch and
  `propose_whole_tab_locked()` stamped it from `entry.amount` (unaware of
  the fix from the same day's earlier entry above) — read directly by the
  SMS sent when a transfer is proposed (already sent, unfixable) and by
  the live pending-transfer banner on the destination customer's own
  receipt/tab-live page and every tabs drawer (still wrong for as long as
  a row stays PENDING). New `backfill_tab_transfer_request_amounts`
  management command (`--dry-run` first, matching this app's established
  convention) — every `TabTransferRequest` with `paid_amount==0` (the
  full-item-transfer fingerprint; a REAL partial split always had a
  genuine non-zero `paid_amount` and was never affected, since its own
  remainder was already the true remainder at creation time) whose stored
  `amount` no longer matches `entry.remaining_amount()` computed now gets
  corrected to that live figure — safe by construction, since `entry.
  amount` never changes on this path, so a mismatch can only mean
  `amount_paid` grew after the field was stamped. Deliberately corrects
  PENDING and REJECTED/CANCELLED rows too, not just ACCEPTED ones — a
  still-pending stale row is exactly where the live banner still matters
  today. `diagnose_customer_debt` (read-only, built 2026-08-15) extended
  with a new "Tab transfer requests involving this customer" section
  (both as source and destination) flagging any stale row, plus
  `entry.amount_paid`/`remaining_amount()` on every tab-linked transaction
  line — so a future report like this one can be traced to a specific
  transfer row directly instead of reasoned about blind. 7 new tests
  (`BackfillTabTransferRequestAmountsTest`, `DiagnoseCustomerDebt
  TransferHistoryTest`) — including a direct end-to-end reproduction of
  Roy's exact scenario (built via constructing the historical bad row
  shape directly, since the current, fixed code can no longer produce one
  on its own) proving the real debt figure was correct throughout and only
  the display field moves. No new migrations. **Action for Roy**: run
  `python manage.py backfill_tab_transfer_request_amounts --dry-run` first
  on Render's Shell to preview, then without the flag to apply.
- Fix: partially-paid transfer entries — full display gap + a real till
  double-count (2026-08-24, same-day follow-up, live screenshots). Roy ran
  the new backfill and reported "it went through but why is the result
  still the same" — the tabs drawer and live receipt STILL showed Kikombe
  at KES 80 and Marley's tab total at KES 740, not the true 50/710.
  **Root cause, traced far beyond the backfill's own scope**: the backfill
  only ever corrected the STORED `TabTransferRequest.amount` snapshot field
  (used by the proposal SMS/pending banner) — `BarTabEntry.remaining_
  amount()` (added earlier the same day) was never wired into any of the
  places that actually DISPLAY or ACT ON an entry's still-owed balance.
  `BarTab.unpaid_total()`, the tabs-drawer entry serialisers (`keg_views.
  _entry_dict()` and kitchen_views' three equivalents), and `_get_live_
  tab_state()`'s outstanding total (the live receipt's own "Jumla" figure
  — this app's own established "what's still unpaid right now" convention,
  see the 2026-08-01 "Umeshalipa Hadi Sasa" entry) all summed the entry's
  full original `amount`, ignoring `amount_paid` entirely. Fixed all of
  them to use `remaining_amount()` for an unpaid entry (a receipt LINE's
  own subtotal stays the true full item price, unchanged — only the
  OUTSTANDING/total figures move). **A second, far more serious money-
  correctness bug found in the same trace**: `settle_entries_amount_
  locked()`'s own partial-settle boundary math compared a customer's
  payment against the entry's FULL amount, not what was truly still owed
  — a customer paying EXACTLY the correct remaining balance would have
  been wrongly SPLIT via `split_paid_unpaid_locked()`, silently reopening
  the already-collected portion as a brand-new "still owed" balance.
  Worse: once an entry's `payment_method` finally flips off 'credit' at
  full settlement (a full-item transfer landing on an OPEN tab, then
  settled ordinarily), `shift_views._reconcile()`'s cash_sales/mpesa_sales
  would count the transaction's WHOLE `sale_amount` as freshly collected —
  DOUBLE-COUNTING the portion already recognised via `debt_recovered_cash/
  mpesa` when the earlier debt-tracker payment happened, corrupting till
  reconciliation for real. **Fix, comprehensive**: new shared
  `BarTabEntry.mark_fully_paid()` (used by `settle_tab`'s full-settle
  loop, `settle_entries_amount_locked`'s fully-covered branch, and
  `tick_entry`'s non-debt branch) captures and returns exactly what was
  NEWLY collected in that action (`remaining_amount()`, read before
  mutating) — used for the settled_amount/SMS text so a customer is never
  told they paid more than they actually just paid. `split_paid_unpaid_
  locked()`/`split_kept_unpaid_locked()` both made amount_paid-aware
  (`total_kept = prior amount_paid + this action's own paid_amount`,
  never touching `Transaction.sale_amount`'s revenue-recognition
  correctness — the two split transactions still sum to the original,
  no revenue gained or lost, just correctly redistributed). For the till
  double-count specifically: a NEW, NARROWER field —
  `BarTabEntry.debt_collected_amount` (migration 0175) — isolates
  specifically the portion ever collected via `_reconcile_tab_entries_
  for_debt_payment()` (both its partial and fully-covered branches),
  deliberately distinct from the broader `amount_paid` (which also grows
  from an ORDINARY counter split-settle, e.g. a customer paying 40 of 50
  via M-Pesa with zero debt-tracker involvement — subtracting THAT would
  have wrongly zeroed out genuinely fresh cash, confirmed by a real test
  failure — `SplitPaidTransactionPaymentMethodSyncTest`'s own pre-existing
  Hezzy fixture — caught before push, not after). `_reconcile()` and
  `till_expected_cash()` both now subtract `debt_collected_amount` (never
  `amount_paid`) from a transaction's `sale_amount` before counting it as
  cash/mpesa, via a `Subquery`/`OuterRef` annotation — a no-op for the
  overwhelming common case (no tab entry, or nothing ever collected via
  the debt tracker) and only differs for exactly this scenario.
  `_rebuild_tab_entry_state_for_customer()` (the debt-payment-revert
  replay mechanism) resets `debt_collected_amount` alongside `amount_paid`
  — proven safe by tracing that every entry reaching its own filter
  (`payment_method` still 'credit', or `was_credit=True`) can ONLY ever
  have had its `amount_paid` history contributed by the debt tracker
  (ordinary counter-settle paths never touch a tab that isn't OPEN, so
  they can never produce a `was_credit=True` stamp), so the two fields are
  always equal there — nothing else to preserve. 11 new tests
  (`PartiallyPaidEntryDisplayAndReconcileTest`) reproducing Roy's exact
  scenario end-to-end across the tabs drawer, the live receipt, the
  dangerous pay-exactly-what's-owed split case, `_reconcile()`, `till_
  expected_cash()`, and a direct regression lock that a pure counter
  split with zero debt-tracker involvement is completely unaffected —
  plus the full pre-existing settle/transfer/reconcile/till test suites
  (172+117 tests) re-run and confirmed passing, including catching and
  fixing the real `debt_collected_amount` vs `amount_paid` distinction
  via `SplitPaidTransactionPaymentMethodSyncTest`'s own failure before
  it could reach production. One migration (0175, additive).
- Money-path audit — three real defects found and fixed (2026-08-24, Roy's
  own framing: "cash & mpesa entries accuracy, counter cash accuracy per
  shift... basically anywhere money & inventory touches transactionally...
  no duplications, accurate arithmetic"). A fresh systematic pass over
  every place a BarTabEntry's paid state is written or read, every STK
  settlement callback, and `_reconcile()`/`till_expected_cash()`'s own
  arithmetic — scrutinising the SAME session's own earlier changes hardest,
  which is where two of the three turned out to live. **(1) `revoke_
  payment_locked()` never rolled back `BarTabEntry.amount_paid`.** Several
  settle paths stamp `amount_paid` to the full amount once an entry is
  fully covered (`mark_fully_paid()` and `split_paid_unpaid_locked()`, both
  added earlier the same day, plus `debt_views._reconcile_tab_entries_for_
  debt_payment()`'s own fully-covered branch, which has done this since
  2026-08-15). Leaving that stale on revoke made `remaining_amount()`
  return 0, so a revoked entry read as owing **KES 0** — invisible to
  `unpaid_total()`, showing zero in all three tabs drawers, dropped from
  `_get_customer_debt_data()`'s per-line remainder, and rejected by
  `settle_entries_amount_locked()` as "more than owed" on any attempt to
  re-settle it correctly. Fixed to reset `amount_paid` to
  `debt_collected_amount` — NOT to zero: revoking a COUNTER settle must
  never un-collect money the debt tracker genuinely took (that has its own
  `CustomerDebtPayment` row and its own separate revert path,
  `revert_debt_payment` → `_rebuild_tab_entry_state_for_customer`, which
  resets both fields and replays). Was already a live bug for
  debt-tracker-settled entries before `mark_fully_paid()` widened it to
  every counter settle. **(2) `mpesa_views._create_debt_payment_from_
  receipt()` carried its own hand-rolled copy of the debt FIFO walk** —
  the last one left (its staff-side sibling `_settle_debt_customer_from_
  payment()` already delegates to `_do_settle_debt_payment`) — and it had
  drifted badly, losing money three ways: it had **NO partial-coverage
  branch at all**, so a customer's partial STK debt payment from the public
  receipt created the `CustomerDebtPayment` but never persisted
  `amount_paid` on the entry — their outstanding balance did not move at
  all (verified: 80 owed, 30 paid, still showed 80); it never synced the
  underlying `Transaction` off `'credit'` on full coverage (the 2026-08-14
  fix, applied to `debt_views`' copy but never to this one), meaning this
  path kept permanently REGENERATING exactly the broken rows
  `backfill_split_paid_txn_payment_method` exists to repair; and it never
  stamped `debt_collected_amount`, so once that backfill DID flip such a
  Transaction to `'mpesa'`, `_reconcile()`/`till_expected_cash()` would
  count its whole `sale_amount` as freshly collected cash/mpesa ON TOP OF
  the `CustomerDebtPayment` already counted in `debt_recovered_mpesa` — a
  real, reachable double-count. Replaced with a direct call to the
  canonical `_reconcile_tab_entries_for_debt_payment()`. **(3)
  `receipt_views.receipt_pay()` billed the customer an entry's FULL
  original price**, not its remaining balance — `sum(float(e.amount))`
  rather than `remaining_amount()` — so a customer whose item already had
  a partial payment against it (e.g. an 80 KES cup with 30 collected
  before it was transferred to them) was STK-charged the full 80 instead
  of the 50 they owed. Fixed, and entries whose balance is already fully
  covered are now dropped from the bill rather than billed at zero.
  **Audited and confirmed CLEAN, no changes made**: `_reconcile()`'s
  `expected_cash` arithmetic and every component's filter (void/`[SVQ]`/
  non-Issue types all correctly excluded; splits sum to the original with
  no double-count); `till_expected_cash()`'s anchor and window logic;
  `split_payment_method_locked()`/`apply_split_payment_locked()`/
  `split_to_credit_locked()` sum invariants (the two resulting rows always
  sum to the original `sale_amount`, `qty=0` on the sibling so stock is
  untouched, envelope FKs copied so `cost()`'s proportional share stays
  correct); `_settle_tab_from_payment()` (correctly routes through
  `settle_entries_amount_locked`, with a safe ValueError backstop);
  `_settle_receipt_entries_from_payment()` (flips entries+transactions to
  mpesa and creates NO `CustomerDebtPayment`, so no double-count);
  `remove_tab_entry()`/`void_tab()` (both `is_paid=True` +
  `payment_method='void'`, correctly excluded from every unpaid/cash
  aggregate, and revoke explicitly refuses a void entry); and
  `OwnerConsumptionTransferRequest._accept_to_owner()` (`type` flip to
  `'OwnerConsumption'` is excluded by construction from every
  `type='Issue'` aggregate). 8 new tests (`MoneyPathAuditFixesTest`),
  including a direct regression lock that a fully-STK-paid debt is never
  double-counted as both `debt_recovered_mpesa` and `mpesa_sales`. No
  migrations.
- Money-path audit, live-data companions (2026-08-24, same day). Roy's
  follow-up question — "will this update reconcile and correct live
  figures?" — answered honestly per fix rather than assumed. Two of the
  three fixes are forward-looking only for data that already exists;
  one leaves a stored value that IS deterministically reconstructible.
  **The gap that had to be closed first**: `debt_collected_amount`
  (migration 0175) defaults to 0, so EVERY pre-existing row understates
  it — and `revoke_payment_locked()` now rolls `amount_paid` back to
  that field. On a legacy entry the debt tracker genuinely paid off,
  revoking would therefore have reset `amount_paid` to 0 and re-inflated
  a debt the customer had already cleared: the fix shipped hours earlier
  was itself incomplete against existing data. Verified directly against
  the pre-2026-08-24 source (`git show 94aaba4:core/models.py` /
  `core/debt_views.py`) that the ONLY writer of `BarTabEntry.amount_paid`
  before that date was `_reconcile_tab_entries_for_debt_payment` (both
  branches) — `mark_fully_paid()` and `split_paid_unpaid_locked()`'s own
  `amount_paid` write both landed the same day as the new field — so for
  every legacy row `debt_collected_amount` should simply equal
  `amount_paid`. New `backfill_debt_collected_amount` (`--dry-run` first,
  idempotent) does exactly that, scoped by the SAME discriminator
  `_rebuild_tab_entry_state_for_customer` already uses in production
  (`transaction.payment_method='credit' OR was_credit=True`), which
  cleanly separates debt-tracker-collected entries from an ordinary
  counter settle (a counter settle happens while the tab is still OPEN,
  so `was_credit` is never stamped) — making it correct whenever it runs,
  not only immediately after deploy. New read-only
  `audit_money_path_integrity` answers "did these bugs actually touch my
  live data?" without changing anything: (A) revoked entries left with a
  stale `amount_paid` (detected via the permanent `TabPaymentRevocation`
  audit row + current state), (B) customers whose receipt-STK debt
  payment was never applied to their entries (payment ledger total vs
  what the entries actually record — that path's `CustomerDebtPayment`
  rows carry a distinctive `risiti ` notes marker), (C) rows still
  missing `debt_collected_amount`. Prints "clean, nothing to reconcile"
  when a business is unaffected. Honest scope note recorded for the
  future: the receipt overcharge fix is forward-only — a past overcharge
  is real money that moved and can only be refunded by hand, never
  corrected in code; the audit surfaces the other two so a decision can
  be made on real numbers instead of guesses. 7 new tests
  (`MoneyPathBackfillAndAuditTest`) including a direct lock that the
  backfill never touches an ordinary counter settle and that the audit
  is strictly read-only. No migrations.
- Fix: the audit's own check (A) contradicted the backfill on live data
  (2026-08-24, from Roy's real run across all 16 businesses). 15 of 16
  came back completely clean; Monsoon Inn reported 1 finding under (A)
  ("tab #390 Bosire — Dallas, amount_paid=50, should be 0 → owes KES 50")
  while the backfill's own dry-run simultaneously listed that SAME entry
  as "amount_paid=50 → debt_collected_amount 0 -> 50.00", i.e. genuinely
  collected by the debt tracker. The two directly contradicted each
  other, and acting on the audit's version — zeroing amount_paid — would
  have RE-CREATED a KES 50 debt the customer had already cleared.
  **The backfill was right, the audit check was wrong.** Root cause:
  check (A) asks "is amount_paid higher than what the debt tracker
  collected?" by comparing against `debt_collected_amount`, which is 0 on
  every row predating migration 0175 — so run against un-backfilled data
  it flags EVERY legitimately debt-paid revoked entry. Re-verified
  directly from the pre-2026-08-24 source (`git show 94aaba4` on both
  `keg_views.settle_tab`'s own loop and
  `models.settle_entries_amount_locked`'s fully-covered branch) that no
  counter-settle path ever wrote `amount_paid` — only the debt tracker's
  FIFO did — so on legacy data the honest answer is "cannot tell yet",
  never a finding. Fixed by evaluating (C) FIRST and gating (A) behind
  it: while a business still has un-backfilled rows, (A) prints "not
  assessable yet — run backfill_debt_collected_amount first, then re-run"
  instead of a misleading finding, and keeps its full teeth once (C) is
  clean. Also confirmed while tracing this that the backfill is not
  merely defensive: without `debt_collected_amount` populated,
  `_reconcile()` counts a debt-tracker-settled transaction's whole
  `sale_amount` as cash/mpesa in the shift where the ORIGINAL SALE
  happened (its `created_at` never moves) on top of the
  `CustomerDebtPayment` counted in `debt_recovered_*` — so running it
  retroactively corrects historical shift/till figures that a later debt
  payment had silently inflated, 44 entries / KES 6,240 on Monsoon Inn.
  3 new tests (`test_audit_defers_check_A_while_legacy_rows_are_
  unbackfilled`, `test_check_A_self_resolves_once_the_backfill_has_run`
  reproducing the live Monsoon Inn case end to end, and
  `test_a_genuinely_stale_revoke_is_still_caught_after_backfill` locking
  in that the check keeps working once (C) is clean). No migrations.
- Revert a mistakenly-fully-paid direct sale to a tab (2026-08-24), live
  request: "the staff who was on shift yesterday sold whitecap but the
  customer paid only half, the rest she told the next staff on shift that
  the customer will come and pay the rest, but now that staff put it in
  the system as if the item was paid whole... I need the staff to be able
  to revert (tengua) the sale, it comes back to tabs or goes to tab with
  the name of the staff who sold it initially for the partial amount to
  be set and the receipt should adjust itself too in the receipts
  section." Distinct from the pre-existing "🤝 Deni" button
  (`split_transaction_payment_method`, 2026-08-12) — that only ever splits
  a direct sale into a paid portion + a bare debt-tracker credit line;
  this instead wraps the still-owed remainder in a real `BarTab`/
  `BarTabEntry` visible in the tabs drawer, attributed to whoever
  ACTUALLY made the original sale, not whoever is fixing the record now.
  New `Transaction.revert_direct_sale_to_tab_locked()` (`core/models.py`)
  is deliberately built ON TOP of `split_payment_method_locked()` rather
  than duplicating its money-correctness logic — the genuinely-collected
  portion stays on the sale's original payment method untouched (a real
  till figure must never move), the owed remainder splits off as an
  ordinary 'credit' transaction dated to the ORIGINAL sale's own
  `created_at` (same backdate-preserving behaviour every other correction
  in this app already has) — then wraps that split-off transaction in a
  `BarTabEntry` on a `BarTab` resolved via this app's established
  auto-detect-by-name convention (an already-OPEN tab under the exact
  customer name on the same station is reused, never duplicated), with
  `served_by` set to `orig_txn.recorded_by` (the original seller) rather
  than the correcting staffer. New `revert_direct_sale_to_tab` view
  (`core/keg_views.py`) mirrors `split_transaction_payment_method`'s exact
  permission shape (shift-gated for non-owner/manager, station-scoped via
  `_allowed_tab_sources`, `claim_checkout_token` idempotency guard) —
  staff types how much was GENUINELY collected, the server derives the
  owed remainder from the transaction's own live `revenue()`, never
  trusting a client-computed figure. "The receipt should adjust itself"
  needed zero extra code — since the new transaction is `split_from` the
  original (2026-08-21's own mechanism), the existing `_live_direct_lines()`
  live-recompute already synthesizes both the reduced paid line and the
  new owed line on the customer's receipt automatically, exactly as it
  already does for the "🤝 Deni" button. New "↩️ Tengua→Tab" button added
  to the "🕐 Malipo ya Hivi Karibuni" panel's direct-sales section in all
  three tabs drawers (`bar_board.html`, `kitchen_board.html`,
  `quick_sell.html`), next to the existing "🤝 Deni" button, per the
  tabs-drawer-parity rule. 15 new tests (`RevertDirectSaleToTabTest`) —
  model-layer paid/owed split and served_by attribution, auto-detect-by-
  name tab reuse, credit-transaction rejection, the full view permission
  matrix (shift gate, owner bypass, station scoping, idempotency,
  cross-business isolation, tab-linked-transaction rejection), and the
  receipt self-correction regression lock via `_live_direct_lines()`. No
  migrations (reuses existing fields end to end). 2399 tests pass (core +
  accounts).
- Fix: "↩️ Tengua→Tab" silently rejected `paid_amount=0` (2026-08-25, live
  report same day, minutes after deploy): Roy tapped the new button, was
  asked how much the customer paid, but "the staff who claimed it was
  paid for never confirmed the mode of payment used for the half/partial
  payment" — so he entered 0. Nothing happened. Root cause: the original
  design only supported a KNOWN partial split — `revert_direct_sale_to_
  tab_locked()`'s validation required `0 < owed_amount < original_total`,
  which a `paid_amount=0` submission structurally can never satisfy
  (`owed_amount` would equal the full total, tripping the `>= original_
  total` guard) — the server correctly rejected it with a 400, but this
  wasn't the right behavior: Roy's real need, confirmed in his follow-up,
  was a THIRD case beyond "known partial paid/owed split" — "the other
  staff who raised this concern simply wanted me to revert it to go to
  tabs so that the staff who put it gets to sort it out on their own...
  did not want any problem to fall onto her but to the right person."
  When NOTHING about the payment can be confirmed, the whole sale should
  revert to one open, unresolved tab entry — not be forced into a
  fabricated 50/50-style split. Reworked `Transaction.revert_direct_
  sale_to_tab_locked()`: renamed its parameter from `split_amount` (owed)
  to `paid_amount` (what was genuinely collected, matching what the UI
  already asks) and gave `paid_amount<=0` its own branch — no sibling
  transaction is created at all; the original transaction's own `payment_
  method` flips straight to 'credit' and IT becomes the tab entry,
  carrying the full original amount, still attributed to `orig recorded_
  by` for `served_by`. A known partial split (`paid_amount>0`) still
  routes through `split_payment_method_locked()` exactly as before — this
  is additive, not a behavior change for the already-working case (a
  50/50 split test's numbers are unaffected, since paid+owed still sum to
  the total either way). `revert_direct_sale_to_tab()` (`core/keg_views.
  py`) relaxed its own validation to accept 0 as genuinely valid (was
  incorrectly folded into the "invalid amount" rejection), and its
  message/response building now branches on whether anything was
  confirmed collected (`paid_txn is None` → "hakuna kilichothibitishwa
  kulipwa — jumla yote inarejeshwa" instead of naming a paid amount).
  All three tabs drawers' prompts (`bar_board.html`, `kitchen_board.html`,
  `quick_sell.html`) now default to `'0'` and explicitly explain the
  sentinel ("Weka 0 kama hakuna kilichothibitishwa — jumla yote itarudi
  kwenye tab bila kujulikana") rather than leaving 0 looking like a
  mistake to avoid. Diagnosed via a full render+`node --check` pass of
  all three boards' rendered JS against a real bar-type `BusinessType`
  fixture (ruled out a template-rendering/syntax bug entirely — none
  existed; a bar-type business is required for this JS block to render
  at all, a red herring from an earlier synthetic test business lacking
  one) before concluding the actual bug was the 0-rejection itself, from
  Roy's own account of exactly what he entered. 5 new/updated tests
  (`test_model_zero_paid_reverts_the_whole_sale_unresolved`,
  `test_model_blank_paid_amount_treated_as_zero`,
  `test_view_zero_paid_amount_reverts_whole_sale_to_tab` — the literal
  reported scenario reproduced end to end — plus the pre-existing partial
  -split tests updated for the renamed parameter). No migrations. 2402
  tests pass (core + accounts).
- Fix: "Tengua→Tab" still silently failing — root-caused from a screen
  recording (2026-08-25, same-day follow-up). Roy sent a 24s screen
  recording of Quick Sell's "Bar Orders" Tabs drawer: he tapped
  "↩️ Tengua→Tab" on a "White Cap" direct sale, the `0`-default prompt
  (confirming the previous fix HAD deployed), entered a customer name,
  tapped OK — and the panel just silently reverted to its exact original
  state, both "White Cap" rows unchanged, no toast, no error. He also
  tried "🤝 Deni" as a fallback — "still nothing." Extracted frames with
  ffmpeg (installed fresh in this session — not present by default) and
  traced two REAL, distinct bugs, found by reading the actual code rather
  than guessing from the video alone. **(1) The actual data bug**:
  `revert_direct_sale_to_tab_locked()`'s `paid_amount<=0` branch built
  `BarTabEntry.amount=owed_txn.sale_amount` directly — but
  `Transaction.sale_amount` is nullable, and `core/views.py`'s
  `quick_sell()` checkout ONLY sets it for a preset/stock_qty cart line
  (`sale_amt = None` is the literal default for a plain item tap,
  relying on `revenue()`'s `selling_price × qty` fallback instead) — "White
  Cap" is exactly a plain, non-preset direct sale. `BarTabEntry.amount`
  is NOT NULL, so this raised an IntegrityError, which rolled back the
  WHOLE atomic block (including the `payment_method='credit'` flip) —
  net visible effect: literally nothing changed, matching the recording
  exactly. My own test fixtures never caught this because every one of
  them explicitly set `sale_amount=Decimal(...)` on the fixture
  transaction, unlike a real Quick Sell checkout. Fixed by pinning
  `txn.sale_amount = Decimal(str(round(original_total, 2)))` explicitly
  in the zero-paid branch (also closes a subtler correctness gap: without
  this, `revenue()` would keep recomputing from `item.selling_price` at
  READ time, so a later price edit on the item would silently change what
  this now-historical sale reads as — unlike every other snapshot this
  app takes at sale time) and switched `BarTabEntry.amount` to always
  derive from `revenue()` rather than `sale_amount` directly, as a
  defense-in-depth safety net. **(2) A separate, pre-existing UX bug that
  made both the real failure AND any future error/success message
  invisible**: `quick_sell.html`'s `qsShowToast(msg)` — unlike
  `bar_board.html`'s `#keg-toast` and `kitchen_board.html`'s `#kb-toast`,
  both real fixed-position toast elements — wrote into `#qsSuccessTitle`,
  the CHECKOUT-SUCCESS SCREEN'S OWN HEADING, which is never visible while
  the Tabs offcanvas/Recent Payments panel sits open on top of it. All 73
  call sites of `qsShowToast` in this file — every correction button in
  the Tabs drawer: Gawanya, Deni, Tengua→Tab, Futa, Tarehe, revoke — have
  been silently firing into a hidden element this whole time whenever
  triggered from inside the drawer, success or failure alike; 35 of those
  call sites already passed a second `isError` argument the old
  implementation never even read. Replaced with a real fixed-position
  toast (`#qs-real-toast`, `z-index:2000` — above Bootstrap's offcanvas at
  1045 — matching `#keg-toast`'s exact visual convention), now honoring
  `isError` for red/gold styling. This means Roy's "Deni" fallback attempt
  was very likely ALSO either succeeding or correctly failing the whole
  time — just with zero visible feedback either way, which is why it
  "looked like nothing happened" there too. 2 new tests reproducing the
  exact `sale_amount=None` production shape — at the model layer
  (`test_model_zero_paid_works_when_original_sale_amount_is_none`) and as
  a full HTTP round-trip
  (`test_view_zero_paid_via_http_succeeds_when_sale_amount_is_none`) —
  plus the full pre-existing `RevertDirectSaleToTabTest` suite re-run and
  confirmed passing against the pinned-`sale_amount`/entry-amount change.
  No migrations. 2404 tests pass (core + accounts).
- Fix: "Tengua→Tab" still failing — root-caused from a live screenshot
  (2026-08-25, third same-day follow-up). Roy: reverting a bar-item sale
  from Quick Sell's "Bar Orders" panel now correctly disappeared from
  "Malipo ya Hivi Karibuni" (confirming the previous two fixes worked),
  but the resulting tab was "nowhere to be seen on the tabs drawer" —
  he wanted it discoverable so the original staffer could clear it via
  "Geuza Deni". Root cause: `revert_direct_sale_to_tab_locked()` derived
  the new `BarTab.source` from the ITEM's own station (bar vs kitchen,
  via `item.store.is_kitchen`) — for "White Cap" (not a kitchen item)
  that's `'bar'`. But `tabs_list()` (the endpoint powering all three
  boards' tabs drawers) filters STRICTLY: Quick Sell's own drawer
  (`?ctx=qs`) only ever shows `BarTab.source='qs'`; Bar/Kitchen Board's
  drawer only shows `source__in=['bar','kitchen']` — completely
  EXCLUDING 'qs'. Quick Sell's own tab-CREATION code has always used
  `source='qs'` unconditionally regardless of item type (confirmed by
  reading its own Recent Payments panel, which requests `station=bar`
  items specifically, yet its OWN tabs are always `source='qs'`) — 'qs'
  is a genuinely separate axis from bar/kitchen, not something
  derivable from the item at all. A tab created with the item's station
  was therefore invisible in the exact drawer Roy was looking at, and
  would only ever have shown up in Bar Board's own drawer instead.
  Fixed by threading an explicit `station` parameter through the whole
  chain — the view (`revert_direct_sale_to_tab`) now reads a `station`
  POST field (validated against `{'bar','kitchen','qs'}`, falling back
  to `None` on anything else) and passes it to `Transaction.revert_
  direct_sale_to_tab_locked()`'s new `station=` kwarg, which uses it
  directly for `BarTab.source` when valid, falling back to the old
  item-derived value only when no valid station is given (defensive
  default for a stale/uncached client). Each of the three templates now
  sends its OWN station explicitly in the POST body — `bar_board.html`→
  `'bar'`, `kitchen_board.html`→`'kitchen'`, `quick_sell.html`→`'qs'` —
  matching exactly which drawer that template's own tab-creation code
  already uses, so the reverted tab always lands in the SAME drawer the
  correcting staffer is actually looking at, regardless of which board
  they're using or what station the underlying item belongs to. The
  existing item-station permission check (`_allowed_tab_sources`) is
  UNCHANGED — deliberately kept separate from this purely-cosmetic
  routing decision, since who's allowed to act on a sale is a different
  question from which drawer displays the result. 6 new tests — 3 at
  the model layer (`test_model_station_param_routes_tab_to_the_right_
  drawer`, `test_model_invalid_station_falls_back_to_item_derived_
  source`, `test_model_no_station_falls_back_to_item_derived_source`)
  and 2 at the view layer including a full round-trip through the real
  `tabs_list()` endpoint proving the tab is genuinely visible via
  `/bar/tabs/?ctx=qs` after the fix (`test_view_station_param_routes_
  tab_to_the_right_drawer`), plus a backward-compat regression lock for
  a client that never sends `station=` at all. No migrations. 2409 tests
  pass (core + accounts).
- Stock-take accuracy + Rekebisha owner-visibility, "catch this theft"
  audit (2026-08-25). Same-message follow-up to the tab-routing fix above:
  "once this is fixed properly I need you to ensure that stock variance
  during staff stock take is accurate so that we catch this theft that
  has been happening and in regards to the cause and effect mapping, the
  staff's track record should be impacted." Traced the full pipeline
  first (`attribute_variance_shift`, `_gap_reconciled_variance`,
  `_process_variance_row`, `run_accountability_stock_take`,
  `_staff_contribution`'s `variance_loss_kes`, `compute_staff_
  recognition`) and confirmed it intact and correctly wired from the
  2026-08-22 sprint — no computational bug found there. Two genuine,
  concrete gaps found by auditing the ACTUAL staff-facing surfaces
  instead, both fixed. **(1) Silently-skippable items in the quick
  "Hesabu Stock" modal**: `submitStockTake()` (both boards) only ever
  included an input the staffer actually typed a value into — an item
  left BLANK got literally zero scrutiny: no book-vs-actual comparison,
  no `StockVarianceQuery`, no notification, nothing. This is the exact
  blind spot a dishonest staffer could exploit — simply never count the
  one item they're worried about. Forcing every item to be entered was
  considered and rejected: it doesn't actually stop theft (a determined
  thief can just type the book balance and lie) and would add real
  friction to every ordinary shift-close on every business on the
  platform — a decision this app's own established philosophy says is
  Roy's to make, not mine to impose unilaterally. Fixed with pure
  VISIBILITY instead: `stock_take_api()`'s POST handler (opening/closing
  phases only — midshift stays voluntary/informational by design) now
  computes which station-scoped, non-keg/non-produce items were shown in
  the modal but never got a count, and returns `uncounted_count`/
  `uncounted_names` (capped at 10) — surfaced on the result panel in both
  `bar_board.html`/`kitchen_board.html` ("⚠️ Haikuhesabiwa (N): ...")
  right alongside the existing variance/auto-reconciled lines. Never
  blocks submission — a pattern (the same item, the same staffer, every
  time) is now something Roy can actually see instead of something
  invisible. **(2) Rekebisha (`adjust_stock_balance`) never notified the
  owner at all, for either direction, whether triggered by the owner or
  a `can_adjust_stock`-delegated staffer.** This is the single most
  theft-relevant lever in the app — it PERMANENTLY reconciles the book
  balance to whatever the person doing it claims the physical count is —
  yet unlike every sibling loss-recording action (`record_breakage`,
  `kitchen_wastage`, petty cash) it fired zero notification, ever. A
  dishonest delegated staffer (the toggle was added 2026-08-11, and its
  "not a real loss" judgment was widened to delegated staff on 2026-08-23
  per Roy's own permission-parity principle) could recount their own
  shortfall, correct the book down to match, optionally tick "sio hasara
  halisi" to suppress it from every loss/P&L figure, and the owner would
  never be told — completely erasing the evidence trail an independent
  stock take would otherwise have caught. Fixed: `adjust_stock_balance()`
  now sends an in-app + SMS notification to every owner/manager (title
  "⚖️ Marekebisho ya Stock") whenever the correction was made by a
  DELEGATED staffer specifically (never when the owner/manager corrects
  their own item — that would just be noise about their own action) —
  item, direction, amount, who, when, and whether "not a real loss" was
  claimed, mirroring `record_breakage()`'s exact wording/notification
  convention. **Deliberately NOT wired into `variance_loss_kes`/
  `compute_staff_recognition`**: Rekebisha is the staffer's own
  VOLUNTARY, HONEST self-correction — the opposite behaviour from theft —
  and scoring it the same way an independent, unbiased stock-take-
  discovered variance is scored would perversely teach staff to leave
  the books wrong and hope nobody ever stock-takes it, worse for
  detection, not better; Roy also explicitly said (2026-08-22, this same
  file) not to add more raw metrics to that scoring rubric, and I
  respected that standing instruction rather than walking it back
  unilaterally for a related-sounding but distinct request. Also
  deliberately NOT added: a minimum-variance noise-tolerance threshold —
  considered (fractional preset/keg sales could in theory produce small
  rounding differences) but ruled out after confirming `current_
  balance()` stays pure Decimal arithmetic end to end with no float
  conversion, so there's no real noise source to filter; suppressing
  small variances would also work directly against the stated goal, the
  wrong lever to pull unilaterally. 15 new tests (`StockTakeApi
  AccountabilityTest` +5 for the uncounted-items visibility;
  `AdjustStockPermissionTest` +5 for the Rekebisha notification,
  including regression locks that a no-op recount never notifies and
  that an owner correcting their own item never self-notifies). No
  migrations. 2418 tests pass (core + accounts).
- Backfill for pre-fix reverted tabs + retroactive stock-take/Rekebisha
  visibility (2026-08-25), same-day follow-up. Roy: "now that the sale is
  no longer in recent sales and it disappeared before the fix is there a
  way i can backfill so that the update works well and also could the
  same happen for stock take done before your update?" **Part 1 — the
  actual backfill**: the station-routing fix (`4f4638c`) only prevents
  FUTURE mis-routed tabs; a `BarTab` already created via `revert_direct_
  sale_to_tab_locked()` before that fix shipped still carries whatever
  `source` it was given at the time (item-derived — 'bar'/'kitchen' —
  regardless of which board's drawer the correction was actually made
  from), permanently, until corrected by hand. There is no explicit
  marker column distinguishing "this tab was created via a revert" from
  an ordinary one — the only available breadcrumb is the correction's own
  `_notify_direct_correction()` message, which always starts "↩️ {who}
  amerejesha ... kwenye tab ya {customer}". New `diagnose_reverted_tab_
  station` (read-only, `--business=NAME`) regex-parses that trail,
  resolves each match against the business's current `BarTab`/
  `BarTabEntry` state, and prints everything a human needs to decide —
  tab id, CURRENT source, customer, item, amounts, dates — pointing at
  the fix command rather than guessing which station is "correct" (only
  a human who remembers which board the correction was made from can
  know that for sure). New `fix_tab_station` (`--business=NAME --tab-id=
  <id[,id...]> --station=<bar|kitchen|qs> [--dry-run]`) is the explicit,
  per-tab correction — touches ONLY `BarTab.source`, a pure display-
  routing field with zero effect on money/stock/debt, so always safe to
  run once the right station is known. **Part 2 — "could the same happen
  for stock take done before your update"**: answered directly rather
  than assumed — NO, this is a structurally different kind of change from
  the tab-routing bug. Both stock-take fixes shipped minutes earlier
  (uncounted-items visibility, Rekebisha owner-notification) only changed
  what's COMPUTED/NOTIFIED at the moment of a NEW action — neither one
  ever wrote a wrong value or dropped/hid any data; every historical
  `ShiftStockCount` row and every historical `[ADJ]`/`[ADJ-NOLOSS]`
  Rekebisha `Transaction` has always been fully and correctly recorded,
  visible in Transaction History exactly as it always was. Nothing
  "disappeared" the way the tab did — there's nothing to REPAIR, only
  something worth being able to look BACK at. Built two read-only
  lookback reports to make that concrete rather than just asserted: (1)
  `diagnose_stock_take_history` (`--business=NAME [--shift=N] [--limit=
  N]`) reconstructs the EXACT SAME "which items were shown in the modal
  but never got a count" answer for any PAST opening/closing stock take,
  by comparing that shift's real, already-stored `ShiftStockCount` rows
  against the same station-scoped item list `stock_take_api()`'s own GET
  handler would have shown at the time (reuses the real `_shift_
  station()` helper, not a hand-rolled guess, so a legacy pre-migration-
  0132 shift with a blank `station` field resolves identically to
  production); (2) `diagnose_rekebisha_history` (`--business=NAME`) lists
  every historical `[ADJ]`/`[ADJ-NOLOSS]` correction made by a DELEGATED
  (non-owner/manager) staffer — exactly the population the new
  notification now covers going forward — so Roy can review what already
  happened before the fix existed, with the "sio hasara halisi" flag
  called out explicitly per row. 24 new tests
  (`DiagnoseRevertedTabStationTest`, `FixTabStationTest`,
  `DiagnoseStockTakeHistoryTest`, `DiagnoseRekebishaHistoryTest`) —
  including business-scoping regression locks on every command (caught a
  real test-authoring bug while writing them: a `name__icontains`
  substring collision between two fixture business names made
  `fix_tab_station` LOOK like it leaked cross-business when the bug was
  actually in the test's own naming, not the command — fixed the fixture,
  confirmed the command's substring-match convention, shared with every
  other diagnostic command in this app, is intentional). All four
  commands are read-only except `fix_tab_station`, which only ever
  touches the one explicitly-named field on the one explicitly-named
  tab(s). No migrations.
- Two more live audit findings, same-day (2026-08-25): "what have you
  broken with petty cash that staff cannot see today's entries, only
  previous ones" + "when an item sold mode of payment is reverted or
  adjusted... does it effectively adjust everywhere the money touches and
  displays... in real time?" **(1) Petty cash — confirmed via `git log
  --follow`, PRE-EXISTING, not caused by anything shipped today.**
  `petty_cash_list()`'s `entries[:100]` has had NO explicit `.order_by()`
  since the line was first written, and `PettyCash` has no `Meta.
  ordering` either — Django hands back whatever order the database
  happens to return for an unordered query. SQLite (local dev) usually
  returns roughly insertion order, masking the bug there; Postgres (this
  app's real production database) makes NO such guarantee for an
  unordered query at all. Once a business crosses 100 total petty cash
  entries over its lifetime (Monsoon Inn's long history easily does),
  `[:100]` can permanently slice an arbitrary/old subset while today's
  newest entries never render — exactly the reported symptom. Fixed with
  an explicit `.order_by('-created_at')`, matching every other list view
  in this app. Swept every other `PettyCash.objects` queryset in the
  codebase for the same missing-ordering-plus-slice shape — all others
  are either `.count()`/`.aggregate()` (order-independent) or already
  carry their own explicit `.order_by()` — this was the only instance.
  **(2) Payment-method corrections — the MONEY side was already fully
  live-correct everywhere (`_reconcile()`, `till_expected_cash()`, daily
  sales, analytics, the debt tracker all read `Transaction.payment_
  method`/`sale_amount` fresh on every call, no caching layer anywhere in
  this app), confirmed by re-tracing rather than assumed. The DISPLAY
  side had a real, concrete gap specific to `revert_direct_sale_to_tab`
  (↩️ Tengua→Tab) — `correct_transaction_payment_method` (🔄 whole-amount
  cash/mpesa swap) and `split_transaction_payment_method` (✂️ Gawanya)
  were both already correctly reflected on the customer's own receipt via
  `_live_direct_lines()` (2026-08-21), but that function's own docstring
  named only VOID and split as things it handles — revert-to-tab was
  never taught to it at all.** The whole-sale (`paid_amount<=0`) case
  mutates the ORIGINAL transaction in place (`payment_method` -> 'credit',
  never 'void'), so it hit neither the void-drop check nor the split-
  child path — the line rendered on the ORIGINAL receipt exactly like a
  normal, fully-paid, complete sale forever, with zero indication the
  item now sits UNPAID on a (possibly different-named) customer's open
  tab, and its full amount stayed counted in that receipt's own total.
  The partial (`paid_amount>0`) case DOES route through the same
  `split_payment_method_locked()` mechanism as Gawanya, so its split-
  child WAS already picked up structurally — but the synthesized child
  line carried no status marker at all, rendering identically to an
  ordinary already-paid line, with nothing telling the reader that
  portion is now owed elsewhere. Fixed: `_live_direct_lines()` now checks
  every transaction it touches (both a line's own txn and any split
  child) for a live `BarTabEntry` — while genuinely still unpaid, the
  line gets `moved_to_tab` (the tab's customer name) instead of a plain
  subtotal; if that tab entry has SINCE been settled through the ordinary
  tab mechanisms, it flips to `is_paid=True` and displays/counts exactly
  like any other paid line — money genuinely collected, just via a
  different final path, self-healing the same way every other live-
  recomputed state in this file already does. New shared
  `_direct_lines_total()` (used by both `public_receipt()` and
  `receipt_live_status()`, so the initial page load and the 20s live poll
  can never disagree) excludes a still-unpaid `moved_to_tab` line from
  the receipt's own total — it's not part of THIS completed sale anymore
  until it's actually settled. `receipt_public.html`'s static render AND
  its `renderLines()` JS (used by the live poll) both got the matching
  visual branch and the same total-exclusion logic, so a page already
  open in a browser self-heals within 20 seconds of the correction,
  exactly matching Roy's own "in real time" framing. Confirmed already
  correct and unaffected by this fix: `recent_settled_tabs_api`'s direct-
  sales query already `tab_entry__isnull=True`-excludes a reverted sale,
  so it correctly disappears from "🕐 Malipo ya Hivi Karibuni" the moment
  it's reverted (already visible to Roy, matching his own report). 12 new
  tests (`PettyCashAccountabilityTest` +1,
  `RevertDirectSaleToTabReceiptDisplayTest` — whole-sale and partial
  revert marking, the settle-later self-heal, two full HTTP round-trips
  through `public_receipt()`/`receipt_live_status()`, and two regression
  locks proving ordinary Gawanya and void behavior are byte-for-byte
  unchanged) plus the full pre-existing `RevertDirectSaleToTabTest`/
  `LiveDirectReceiptLinesTest`/`DirectSalePaymentSplitTest`/`ReceiptSplit
  PaymentDisplayTest`/`VoidDirectTransactionTest` suites re-run and
  confirmed passing unmodified. No migrations.
- Stock-take variance reject redesigned into a theft verdict (2026-08-26),
  live design conversation: Roy pushed back hard on the pre-existing
  accept/reject shape — "if rejected, the variance closes as resolved but
  nothing gets corrected... the business owner cannot fail to replenish
  stock... but the business owner knows that he has been stolen from."
  Root problem: 'dismiss' ("Kataa") used to mean nothing more than "I don't
  believe this explanation" — no corrective transaction, no distinction
  between an innocent mistake and something deliberate, and the stock
  balance stayed wrong forever. Redesigned per Roy's own framing, with his
  explicit invitation to interpret the ambiguous parts ("I am not sure if
  I have made sense so you can also improve on what you know I mean").
  **The physical correction is now unconditional and immediate on EITHER
  decision** — 'dismiss' now creates the SAME kind of corrective
  `Transaction` 'accept' already did (Wastage for a decrease, Receipt for
  an increase), the instant it's clicked, tagged `[THEFT]` (a real loss —
  deliberately NOT excluded from P&L/analytics the way `[ADJ-NOLOSS]` is).
  **What's NOT immediate is the accusation**: the row goes to a new
  `DISPUTED` status (not straight to `RESOLVED`) with a
  `StockVarianceQuery.dispute_deadline` — `Business.
  variance_dispute_window_hours` (accounts migration 0066, owner-
  configurable, default 48h, new "Sera ya Tofauti za Stock" card on Payment
  Settings, `_section='variance_policy'`) — during which the accused
  staffer can respond (`respond_to_variance()` stays `DISPUTED`, doesn't
  flip to `RESPONDED`, so the verdict+deadline already in place aren't
  lost — notifies the owner distinctly: "Jibu Limepokewa Kabla ya Muda
  Kuisha"). `item_has_pending_variance()` treats `DISPUTED` exactly like
  `RESOLVED` for the ITEM's own sale-ability — Roy's explicit "the item
  should be sellable again... another staff's mess should not affect her
  normal operations" — the appeal window is purely about the STAFFER's own
  record, never the item. New `finalize_now` action lets the owner skip
  waiting entirely ("the business owner should be the one to get to decide
  whether he wants an explanation or not"). New lazy sweep
  `finalize_expired_variance_disputes()` (same "checked on read, no real
  cron" pattern as `KitchenStockReceipt.maybe_auto_close()`) auto-finalizes
  a `DISPUTED` row past its deadline on the next page load of either the
  owner's variances list or the staffer's own respond page, notifying the
  staffer it's now permanent. **Reconsideration** (accept↔dismiss any
  number of times, once a row has ever been decided) is a PURE
  accountability-record flip — `owner_accepted`/`compliance_noted`/`status`
  only — the `corrective_txn` created at the moment of the FIRST decision
  is NEVER re-created, re-touched, or reversed, matching Roy's own explicit
  rule verbatim: "the only thing that should change is the staff's
  performance record and remuneration... but not the stock balance."
  `select_for_update()` + `transaction.atomic()` added around the whole
  read-modify-write (a new necessity now that dismiss also writes a real
  financial transaction) closes the one race this creates: two near-
  simultaneous first-time decisions on the same row could otherwise both
  read `owner_accepted=None` and both create a corrective transaction for
  the same physical shortfall. A reversal (either direction) gets the
  established MAREKESHO wording (who, what changed, when, why) via
  `owner_note`, and the staffer is separately notified either way. Haki/
  recognition impact: `core/haki_views.py`'s `dismissed_variances`/
  `variance_loss_kes` now `.exclude(status=StockVarianceQuery.DISPUTED)` —
  a still-DISPUTED theft verdict does NOT yet count against a staffer's own
  track record; only once it's genuinely `RESOLVED` (timeout, "Thibitisha
  Sasa," or staying firm after a response) does it, per Roy's own "the
  verdict now becomes permanent" framing — this was my own interpretive
  call on the ambiguous part of the request, flagged here for Roy to
  correct if he meant something else. Separately, per Roy's "regarding
  email notification... it is quite important": `_notify_owner()`
  (the shared fan-out already used for every stock-take owner notification
  — a new variance created, an auto-reconciled gap, a staff response) now
  ALSO sends email via `send_email_notification_async`, alongside the
  pre-existing in-app + SMS — confirmed via direct trace this channel was
  completely missing before (in-app + SMS only). New `StockVarianceQuery.
  DISPUTED` status value + `dispute_deadline` field (migration
  `0176_stockvariancequery_dispute_deadline_and_more`). `stock_variances_
  pending.html` gained a new "🚨 Disputed" section (item/variance/staff/
  deadline/corrective_txn/owner_note/staff_response, three owner actions:
  🔒 Thibitisha Sasa / ✅ Badilisha kuwa Sahihi / Bado Ninakataa) and a
  "↺ Badilisha uamuzi" reconsider toggle on already-Resolved rows, matching
  the petty-cash-review-undo precedent (2026-07-25). `stock_variance_
  respond.html` gained a disputed-state banner explaining the preliminary
  verdict + deadline to the accused staffer. Two pre-existing tests updated
  for the new first-time-dismiss contract (`StockVarianceReviewWordingTest.
  test_dismiss_skip_leaves_owner_note_blank_and_still_resolves` and
  `StockTakeVarianceItemLockTest.test_owner_resolving_variance_unlocks_the_
  item`, both now assert `DISPUTED` not `RESOLVED` for a first-time
  dismiss; the latter gained a companion `test_owner_accepting_variance_
  unlocks_the_item` locking in that 'accept' is completely unchanged — no
  appeal window, straight to `RESOLVED`). 38 new tests
  (`StockVarianceTheftVerdictTest`, `VarianceDisputeWindowSettingsTest`,
  `VarianceLossKesDisputedExclusionTest`) covering the full lifecycle:
  immediate correction on first dismiss (both directions), the `[THEFT]`
  tag's real-loss treatment via `loss_value()`, deadline computation
  (default and business-configured), immediate item unblocking, staffer
  notification + SMS + owner email, both reconsideration directions never
  touching `corrective_txn`/stock, re-dismissing while still disputed never
  double-corrects, MAREKEBISHO wording, `finalize_now` (including the
  reject-when-not-disputed guard), the lazy-sweep auto-finalize (both entry
  points, plus a still-within-window negative), staff-response-while-
  disputed staying `DISPUTED` and notifying the owner distinctly, the
  `select_for_update()` race-safety re-run, manager parity, a staff-blocked
  regression lock (redirect, not JSON 403 — `@owner_or_manager_required` is
  a full-page decorator), the settings form (default/custom/invalid/
  clamped-to-1), and the Haki-exclusion matrix (disputed excluded, same row
  counts once finalized, mixed rows sum only the resolved one). All
  pre-existing stock-take/variance/Haki test suites (98 tests across
  `StockVarianceReviewWordingTest`, `StockTakeVarianceItemLockTest`,
  `VarianceLossKesAffirmationTest`, `PayrollVarianceSuggestionTest`,
  `RunAccountabilityStockTakeGapTest`, `StockTakeApiAccountabilityTest`,
  `AttributeVarianceShiftTest`, `StockTakeVarianceAttributionTest`,
  `StartStockTakeIdempotencyTest`, `StockTakeVarianceDashboardExclusionTest`,
  `BackfillSvqInvoiceTagsCommandTest`) confirmed passing unmodified or with
  only the two documented updates above. Two migrations (core 0176,
  accounts 0066), both additive.
- Stock-take variance: an INCREASE is never theft (2026-08-26, same-day
  follow-up). Roy, with a live screenshot showing "Blue Ice ↑ +0.06" being
  offered "⚡ Mauzo ya Kawaida (Cash)" alongside a genuine decrease: "the
  only way stock would be extra during stock take is if it was received
  but not received in the system... that is a plus not a theft, so the
  system should not ask the owner mauzo ya kawaida cash... if the owner
  accepts such a variance, the system should append it as a receipt but
  just unrecorded and it should assume the cost price is like the previous
  previous receipt for that specific item unless the owner says
  differently." Root cause: the PENDING/RESPONDED sections' quick-action
  UI, and the theft-verdict `dismiss` machinery shipped hours earlier the
  same day, both applied uniformly to `direction='increase'` rows with no
  logical distinction from `direction='decrease'` — an increase was
  offered the exact same "was this an unrecorded cash/mpesa/credit sale?"
  reasoning that only ever makes sense when stock is MISSING, and dismiss
  for increase silently created the identical Receipt `accept` would
  (no `[THEFT]` tag, no distinction at all — likely inherited unexamined
  from the decrease pattern when the theft-verdict redesign was built).
  **Redesigned `review_variance()`'s direction handling from the ground
  up.** `accept` for increase now reads an optional `owner_cost_price`
  POST field — parsed defensively (blank/invalid/negative all fall back),
  defaulting to `item.cost_price` ("like the previous receipt," since that
  field is already exactly the last real receipt's cost by this app's own
  "Item.cost_price has exactly ONE designed writer" convention) — creates
  the Receipt exactly as before, and additionally writes the resolved cost
  to `item.cost_price` when it differs: a new, narrow, explicitly
  documented exception to that convention, same category as the
  pre-existing `KitchenBatch.open_batch()` exception. `dismiss` for
  increase means "I don't believe this recount" — resolves immediately
  with **no correction created at all** (nothing to append — the owner is
  saying the extra stock isn't real), **no `[THEFT]` tag, no `DISPUTED`
  appeal window, no accountability consequence** (`compliance_noted` stays
  `False` — confirmed `haki_views.py` needed zero changes, since its
  `dismissed_variances` count already only ever counts `compliance_noted=
  True` rows and `unaffirmed_variances_qs` was already `direction=
  'decrease'`-scoped from the start). **The real structural subtlety**:
  since an increase-dismiss may create no correction, `owner_accepted is
  not None` (the prior signal for "has this already been decided") stopped
  being the right test for "has the stock already been corrected" — a
  new `has_correction = (svq.corrective_txn_id is not None)` replaces it
  everywhere. For decrease the two were always equivalent (both accept and
  the old-and-new dismiss always create a correction on first decision),
  so decrease behavior is byte-for-byte unchanged; for increase they
  diverge exactly where it matters — reconsidering an earlier "not
  accepted" dismiss back to `accept` now correctly performs the
  DEFERRED Receipt creation right then (with the MAREKEBISHO wording),
  rather than the generic reconsideration code's "just flip the fields,
  never touch the transaction" behavior, which would have silently left
  no correction ever created. Conversely, reconsidering an increase's
  earlier `accept` (a Receipt already exists) back to `dismiss` never
  reverses that Receipt — same "never touch the stock a second time" rule
  as the decrease theft-verdict reversal, applied here even though there's
  no theft framing to walk back. `finalize_now` needed no code change —
  its own `status != DISPUTED` guard already correctly rejects any
  increase row, which can never reach DISPUTED under the new design.
  **UI**: `stock_variances_pending.html`'s PENDING/RESPONDED/RESOLVED
  sections all split on `v.direction` — a decrease row keeps the exact
  original cash/mpesa/credit/wastage flow untouched; an increase row gets
  a new "📦 Kubali kama Mapokezi Yasiyorekodiwa" button (a `prompt()` for
  the cost price, pre-filled from `item.cost_price` — deliberately a plain
  prompt rather than a new inline form, matching this app's own
  established "single number entered rarely" convention, e.g.
  `edit_raw_material_cost`/`edit_kitchen_batch_target`) and a neutral
  "Hesabu Sio Sahihi" dismiss button (reason chips reworded away from
  wizi/theft language: "Hesabu ya awali ilikuwa sahihi", "Sikuamini kuwa
  kilipokewa", "Nitaangalia tena baadaye"). The RESOLVED section's badge
  and reconsider-toggle both branch the same way (increase shows "➖
  Haikukubaliwa" instead of "❌ Imekataliwa," never "· Rekodi utendaji").
  22 new tests added to `StockVarianceTheftVerdictTest` (replacing the one
  now-wrong pre-existing increase test that assumed dismiss created a
  Receipt) — default/explicit cost price, cash/mpesa/credit language
  confirmed never read for increase, no-correction/no-consequence/no-SMS
  dismiss, the accept-after-dismiss deferred-Receipt creation (the core
  new mechanism), the dismiss-after-accept never-reverses-the-Receipt
  regression lock, and the `finalize_now` rejection. No migrations (no
  model changes — `Item.cost_price` and `StockVarianceQuery.corrective_
  txn` both already existed).
- Stock-take variance: preset-aware cost division for fractional increases
  + "Blue Ice never picked up presets" root-caused to an ALREADY-DIAGNOSED
  bug (2026-08-26, same-day follow-up). Roy: "if the variance of said item
  let's say like blue ice is +0.25 or +0.5 or +0.75 the cost price
  division, should be according to preset... blue ice never picked up the
  presets accordingly no matter what we did, as you can even see from the
  variance, +0.06 is too strange, even in the quick sell tiles and the
  analytics, anywhere blue ice is mentioned the balances keep on
  misfiring." **Part 1 — preset-aware cost routing.** New
  `_matching_preset_for_increase(item, variance)` (`core/stock_take_views.
  py`) finds the item's own preset whose `quantity_consumed` matches the
  variance within a small tolerance (0.01, closest-match-wins when more
  than one qualifies) — an increase landing on a clean fraction (0.25/0.5/
  0.75 of a bottle) is far more likely a PORTIONED amount than a whole new
  delivery, since nobody "receives" a quarter bottle from a supplier.
  `review_variance()`'s increase-accept branch now routes the owner's
  entered cost into the MATCHED PRESET's own `cost_price` (same per-whole-
  unit basis as `item.cost_price` — confirmed by tracing `Transaction.
  cost()`'s own `preset.cost_price` branch: `qty * preset.cost_price`,
  where `qty` is already the fraction, so `preset.cost_price` must be
  denominated per WHOLE unit, not per-fraction) — leaving `item.cost_price`
  completely untouched, same as the pre-existing `KitchenStockReceiptLine`
  writer of this exact field. `corrective_txn.preset` is set to the match
  too (harmless bookkeeping, mirrors the sale-attribution convention —
  `Transaction.cost()` only ever reads `preset` for `type='Issue'`, so
  attaching it to a `Receipt` has no functional side effect elsewhere,
  confirmed by grepping every `preset_id` filter in the codebase for a
  hidden Issue-only assumption). A whole-number variance (a genuine new
  bottle) or an odd, non-preset fraction (the reported `+0.06` — matches
  nothing) both fall through UNCHANGED to the original flat `item.
  cost_price` update from the same-day theft-verdict-redesign sprint.
  `ItemPortionPreset.cost_price`'s own docstring updated to document this
  as a second, narrow, deliberate writer alongside the Kitchen one.
  **Part 2 — "no matter what we did," root-caused, not guessed.** Traced
  `diagnose_stock_shortfalls.py` (2026-08-21) and found this EXACT symptom
  was already diagnosed for Blue Ice by name, in the codebase's own
  standing record: `Transaction.qty` for a preset-attributed sale is a
  PERMANENT SNAPSHOT of `preset.quantity_consumed × cart_qty` taken at
  sale time, but the preset's own `quantity_consumed` is read LIVE, never
  versioned — if a preset's fraction is ever edited after some sales
  already happened under the old value, every OLDER transaction's `qty`
  stays permanently based on the fraction that existed then while newer
  sales use the new one, so the running balance becomes an honest sum of
  two different schemes and will almost never land on a clean fraction —
  exactly explaining both "+0.06 is too strange" (a genuinely odd,
  non-clean number) and "anywhere Blue Ice is mentioned the balances keep
  on misfiring" (every surface — Quick Sell tile, analytics, the stock-
  take variance itself — reads the SAME already-drifted `current_
  balance()`, so it's one root cause showing up everywhere consistently,
  not a separate display bug at each site). "No matter what we did" is
  consistent with this mechanism too: RE-EDITING a preset's fraction to
  "fix" it only adds MORE historical drift, since it can never retroactively
  correct transactions already recorded under the old value — the
  diagnostic's own existing `preset_drift` section (2026-08-21) already
  flags exactly which historical transactions don't divide evenly by the
  CURRENT `quantity_consumed`, which is the concrete way to confirm this
  against Blue Ice's real production data. Extended the SAME diagnostic
  (rather than build a second, overlapping one) with a new "Portion
  presets configured for THIS item id" dump at the very top of `--item`
  mode — since "presets never picked up" could also mean literally ZERO
  `ItemPortionPreset` rows exist for the specific `Item` id Quick Sell is
  rendering (a config problem, not a balance-math one), or that a
  DIFFERENT "same name" duplicate `Item` is the one actually carrying
  them — the existing duplicate-item section now also prints each
  duplicate's own preset count, so that specific confusion is visible in
  one glance instead of two separate lookups. **UI**: `pending_variances()`
  now attaches `matched_preset`/`looks_like_drift` to every open INCREASE
  row (`stock_variances_pending.html`'s PENDING/RESPONDED/RESOLVED
  sections) — a matched preset shows a blue "≈ {label}" hint chip and the
  accept prompt names it explicitly and defaults to its own cost; an
  unmatched NON-whole-number variance shows an amber "⚠️ si kipimo — angalia
  Rekebisha" warning steering the owner toward a physical recount instead
  of accepting a nonsensical "0.06 of a bottle" delivery. 20 new tests
  (`IncreaseVariancePresetMatchingTest`, `DiagnoseStockShortfallsPresetDump
  Test`) — exact/tolerance/no-match/closest-match-wins matching, the
  preset-cost-price routing (both with and without an explicit override,
  and reusing the preset's own already-set cost as the default), the
  whole-number and odd-fraction fallback-to-item-cost_price regression
  locks, the view-context attachment (including the decrease-direction
  regression lock), the template hint/warning rendering, and the
  diagnostic's new preset-dump + duplicate-item-preset-count output. One
  migration (0177, additive — `ItemPortionPreset.cost_price`'s help_text
  change only, no schema change).
- Revert a stock-take variance as a counting error (2026-08-28, urgent live
  request with a screenshot). Roy: Chrome Vodka 250 ML's stock-take
  variance had already been rejected as a theft verdict (`-4`, Transaction
  #4713, still within its appeal window) — but the PHYSICAL count was
  actually 4.75, the system showed 0.75, and "there is not like there is a
  new receipt for it" (i.e., no unrecorded delivery explains it either) —
  the ORIGINAL stock-take count itself was simply wrong. "I want to return
  this chrome vodka back to stock... I do not want to use rekebisha stock
  so that this variance query to the staff shows that the owner reverted
  and accepted that it was a stock count miscalculation and that the staff
  does not have to account for it." **Deliberately distinct from the
  existing accept-reconsideration ("✅ Badilisha kuwa Sahihi")**: that
  action's whole point, per Roy's own original theft-verdict rule, is that
  reversing a verdict changes ONLY the staffer's record, "never the stock
  balance" — correct when the deficit itself is real, just not malicious.
  This is a genuinely different case: the deficit was never real at all,
  so the correction itself must be undone too, not merely the accusation.
  New `action='revert_miscount'` on `review_variance()` — requires a
  `corrective_txn` to exist; reverses it via a COMPENSATING transaction
  (this app never deletes/mutates a transaction's qty — same discipline as
  `StockTransfer.cancel_locked()`'s `[TRF-CANCEL]` rows), tagged
  `'[SVQ-REVERT]'`, opposite type and sign of the original (a `-4` Wastage
  correction gets a `+4` Receipt reversal; a Receipt-type correction from
  an increase-accept would get a Wastage reversal, handled symmetrically
  though Roy's own case is the decrease/theft-tagged one). The ORIGINAL
  corrective transaction is retagged `'[ADJ-NOLOSS]'` — reusing the
  EXACT established "not a real loss" convention already excluded from
  every P&L/analytics/Haki wastage aggregate in the app (confirmed by
  reading each: `analytics_views.py`, `daily_financials.py`,
  `haki_views.py`, `customer_profile.py` all already `.exclude(invoice_no=
  '[ADJ-NOLOSS]')`) — rather than inventing a parallel exclusion mechanism.
  `svq.owner_accepted=True`/`compliance_noted=False`/`status=RESOLVED` —
  "accepted that it was a miscount," never counting against the staffer —
  and `variance_loss_kes`'s own pre-existing `.exclude(owner_accepted=
  True)` picks this up automatically, zero extra code needed there.
  Deliberately works from EITHER `DISPUTED` (Roy's actual screenshot state)
  OR an already-finalized `RESOLVED` theft verdict — a miscount can
  legitimately be discovered after the appeal window closes too. New
  "🔄 Ilikuwa Kosa la Kuhesabu" button added to both the DISPUTED section
  and the RESOLVED section's reconsider-toggle (decrease-direction only —
  an increase-direction row's own "not accepted" dismiss already never
  touches stock, nothing to revert there) in `stock_variances_pending.
  html`, with a plain `prompt()` for an optional note (matching this app's
  established "single rare input" convention) and a distinct staffer
  notification explicitly saying "HAITAHESABIKA kwenye rekodi yako ya
  utendaji au malipo." 11 new tests (`RevertVarianceMiscountTest`) —
  balance restoration (the literal reported figures, 0.75→4.75), the
  compensating-transaction mechanism with a regression lock that the
  original's own `qty` is never mutated, the `[ADJ-NOLOSS]` retag actually
  excluding it from a real P&L wastage query, the accountability fields,
  the Haki `variance_loss_kes` exclusion, the staff notification wording,
  immediate item unblocking, working from both DISPUTED and finalized-
  RESOLVED starting states, the "nothing to revert" guard, and the
  staff-blocked regression lock. No migrations.
- Futa (Recent Sales) split-group stock/cash inconsistency + backfill +
  float→cash-sales→counter-cash audit (2026-08-26), urgent live request:
  "audit if futa from recent sales adjusts stock balance and then after
  the fix assist in the backfill at the same audit the integrity from
  float to cash sales to counter cash sensibility and if you flag
  anything perform the fix." **Root cause, traced through the real
  code, not guessed**: `void_direct_transaction()` ("🗑 Futa" on a direct
  sale in the "🕐 Malipo ya Hivi Karibuni" panel) zeroes `qty` and flips
  `payment_method='void'` on ONE clicked `Transaction` with no awareness
  at all of `Transaction.split_from`/`split_children` — the ✂️ Gawanya/
  🤝 Deni split mechanism (2026-07-26 onward). A split keeps the sale's
  REAL physical `qty` on the ORIGINAL row only; the split-off sibling
  always carries `qty=0` (see `split_payment_method_locked()`'s own
  docstring). Voiding the qty-carrying original ALONE therefore restored
  the WHOLE physical unit to stock — correct in isolation — while any
  still-live sibling (say, the mpesa portion of a cash+mpesa split) kept
  its own revenue recognized for that exact same now-"never sold" item: a
  self-contradictory ledger (stock says it never left the shelf, a
  sibling transaction says it was paid for) that fed straight into
  `_reconcile()`/`till_expected_cash()` too, since both simply filter on
  `payment_method` — the still-live sibling kept counting toward
  cash/mpesa reconciliation for an item the stock ledger no longer
  believed was sold. **Fix**: voiding now cascades DOWNWARD ONLY — the
  clicked transaction plus every live descendant reachable via
  `split_from`, walking recursively via a small closure — never upward to
  an unrelated root the staffer didn't click. This keeps the common, safe
  case (voiding a split-off sibling alone, which never carries real `qty`
  and needs no cascade at all — verified this is genuinely inert on its
  own) byte-identical to before, while closing the dangerous case
  (voiding a row that still has live children hanging off it, almost
  always the original). A live CREDIT descendant blocks the WHOLE cascade
  outright with a clear error pointing to the debt tracker's own
  "Ilikuwa Kosa" tool first — silently voiding just the cash/mpesa
  portion would leave the identical inconsistency one layer deeper (a
  customer's debt for an item the stock ledger now says never left the
  shelf). Each member of a cascaded group reverses its own share of the
  source revenue-envelope (`_reverse_stock_movement_envelope`, unchanged,
  called per-member) and gets the same `[FUTWA: reason]` tag, so the
  whole group is traceable identically in Transaction History; the
  physical `qty` is still only ever restored ONCE (from wherever it
  lives — the original), regardless of how many members are in the
  group. **Two more real bugs found in the SAME investigation, on the
  customer-facing receipt side**: `core.receipt_views._live_direct_lines()`
  (built 2026-08-21 to self-heal a direct sale's receipt after a
  correction) had its parent-liveness and child-liveness checks WRONGLY
  bundled together in one loop — voiding the PARENT `continue`d straight
  past the child-render code entirely, silently hiding a still-live
  sibling's genuine revenue from the customer's own receipt; and voiding
  a CHILD alone rendered it ANYWAY, since the child-append code never
  checked `payment_method=='void'` at all (only ever checked the
  UNRELATED parent's own status) — a voided split fragment kept showing
  its stale, pre-void amount on the receipt as if still charged. Fixed by
  deciding each row's own liveness independently — critically, a child's
  `qty` must NEVER be checked (always 0 by construction, unrelated to
  whether it's void) — only `payment_method`. **Backfill**: new
  `backfill_void_split_siblings` (`--dry-run` first, matching this app's
  own established convention) walks every historical VOID direct-sale
  Transaction's downward `split_from` closure and voids any still-live
  cash/mpesa descendant it finds (same envelope reversal, same
  `[FUTWA: backfill — ...]` tag); a live CREDIT descendant is NEVER
  auto-voided, only reported for manual follow-up via the debt tracker's
  own tool — idempotent, safe to re-run, optional `--business=NAME`
  scoping. **Float→cash-sales→counter-cash audit**: re-read `_reconcile()`
  and `till_expected_cash()` end to end — confirmed both already
  correctly exclude `payment_method='void'` by construction (their
  cash/mpesa/credit filters simply never match it), confirmed `_reconcile()`
  is the single formula every consumer (`active_shift_api`, `close_shift`,
  `shift_history`, `bar_z_report`) reads from with no independent
  reimplementation anywhere to drift, and confirmed the theft-verdict
  work earlier this same day (`[SVQ-REVERT]`/`[THEFT]` corrective
  transactions) never touches `type='Issue'` at all, so it has zero
  interaction with cash reconciliation. The one real, substantive gap in
  this whole chain WAS the split/void cascade above — now fixed, closing
  both the stock-balance question and the cash-reconciliation question
  together (a cascaded void drops the group's ENTIRE revenue from
  `_reconcile()`/`till_expected_cash()` at once, matching the group's
  entire `qty` being restored at once). No other drift found. 21 new
  tests (`VoidDirectTransactionTest` split-cascade coverage — the
  cascade itself, the safe-single-sibling-no-cascade regression lock, the
  credit-blocks-everything refusal, already-voided-group rejection, keg-
  envelope-reversed-once, and a direct `_window_revenue` regression lock
  proving both fragments drop out of live shift revenue together;
  `LiveDirectReceiptLinesTest` — voided-child-drops-its-line, voided-
  parent-still-shows-live-sibling, both-voided-shows-nothing, and a full
  end-to-end HTTP round-trip through the real, fixed endpoint;
  `BackfillVoidSplitSiblingsTest` — dry-run, real repair, credit-never-
  auto-voided, business-scoping, idempotent re-run, unaffected-business
  no-op). No migrations.
- Stock take "Zote Zinalingana" (Everything Matches) affirmation + a new
  delegated staff permission (2026-08-27). Roy's own framing, adopted
  as-is: "if stock take is accurate according to the system tally without
  the staff inputing any value... the system misbehaves, so I was
  thinking of making this an affirmation for staff and it goes through,
  but the owner should have this as a staff permission for trusted
  staff." **Root cause, traced to the frontend, not the backend**: the
  quick "Hesabu Stock" modal's own `submitStockTake()` JS (`bar_board.
  html`/`kitchen_board.html`) has always blocked submission outright —
  `if (counts.length === 0) { errEl.textContent = 'Ingiza angalau hesabu
  moja.'; return; }` — whenever every physical count genuinely matched
  the system and staff had nothing to type; the backend (`stock_take_
  api()`) never had an equivalent block and would have happily accepted
  an empty submission. The guided owner/manager-only page (`start_stock_
  take()`/`stock_take_form.html`) had the identical JS-level block, this
  time backed by a REAL server-side `if not counts: return 400` too.
  **Fix — server-derived affirmation, never a client-trusted shortcut**:
  new `affirm_all` POST flag on both endpoints. Rather than trust a
  client-synthesized "every blank item = book balance" array (which the
  client already fully controls anyway), the SERVER itself resolves,
  for every station-scoped item NOT present in the client's own `counts`,
  that item's own CURRENT live balance as the confirmed count — so
  nobody can lie about a specific item's number even with affirm_all on;
  only whether SKIPPING individual entry is allowed at all is the trust
  decision. Whatever the staffer DID type (e.g. the one item they know is
  genuinely short) is left completely untouched and still creates a real
  `StockVarianceQuery` exactly as before — affirm_all only ever fills in
  the REST. New `UserProfile.can_affirm_stock_take` (accounts migration
  0067, default False, matching this app's own established "trusted
  staff, owner opts them in" convention for every recent delegated
  toggle) gates `stock_take_api()`'s staff-facing use of the flag —
  owner/manager always exempt; `start_stock_take()` needs no extra check
  at all, since that whole page is already `@owner_or_manager_required`.
  New "✅ Zote Zinalingana" button added next to the existing submit
  button on all three surfaces (`bar_board.html`, `kitchen_board.html`,
  `stock_take_form.html`) — shown to owner/manager unconditionally, to a
  plain staffer only when the new toggle is on; tapping it never blocks
  on an empty form (unlike the ordinary submit button) and correctly
  produces `variance_count=0`/`uncounted_count=0` for a genuinely clean
  count, since every item now gets a real `ShiftStockCount` row instead
  of being silently skipped. **A second, real, pre-existing gap found and
  fixed in the same pass, directly adjacent to this work**:
  `UserProfile.can_record_expenses` (built 2026-08-21, "staff have no way
  of backdating expenses") has existed on the model and been read by
  `record_ad_hoc_expense()`/board templates this whole time, but was
  NEVER actually wired into `staff_permissions.html`/`accounts.views.
  staff_permissions()` at all — an owner had no UI to ever grant it,
  meaning that toggle has been permanently stuck off for every business
  on the platform since the day it shipped. Wired in alongside the new
  `can_affirm_stock_take` toggle (same form, same view, same `update_
  fields` list) rather than leaving it broken while touching the exact
  same lines for an unrelated new field. 11 new tests
  (`StockTakeAffirmAllTest` — unpermitted-staff-blocked, permitted-staff-
  affirms-everything, owner-always-allowed, the mixed typed+affirmed
  case with the typed value surviving untouched, the server-never-
  trusts-a-stale-client-value regression lock, station-scoping, midshift
  still gated, an ordinary non-affirm submission completely unaffected,
  and the guided page's own end-to-end round-trip;
  `StaffPermissionsAffirmAndExpenseWiringTest` — both toggles persist
  on/off through the real form). One migration (accounts 0067, additive).
- Money-path/stock-behaviour audit (2026-08-27), Roy: "audit all money
  paths, transactionals flow and stock behaviours and enforce both
  integrity and logical arithmetic operations." A targeted, evidence-
  based pass — not a re-derivation of the huge amount already audited
  across this app's history — focused on (1) re-verifying the same-day
  Futa split-cascade fix has no interaction bugs with `was_credit`/
  `debt_collected_amount`/`OwnerConsumptionTransferRequest` (traced
  `Transaction.save()`'s `was_credit` stamp — only fires on a transition
  FROM 'credit', never involves the cash/mpesa rows the cascade touches;
  confirmed a tab-less credit transaction can NEVER be flipped to cash/
  mpesa outside the tab-linked debt-tracker path, so it can never reach
  the "direct sales" Futa list at all — ruled out clean); (2) a fresh
  `Sum('sale_amount')` sweep for any NEW un-guarded raw aggregate since
  the 2026-07-31 sweep (none found — `KitchenBatch.split_by_date_locked`'s
  own `_recompute_revenue()` and every keg/bunch/batch revenue query are
  correctly scoped to envelope-only rows, which `record_sale()` always
  stamps with a real `sale_amount`); (3) a full division-by-zero sweep
  across every `/` operation in `core/models.py` touching cost/price/
  revenue/qty (all ~15 sites independently checked — every one already
  correctly guarded, several with a falsy-Decimal check that also
  doubles as the zero-guard, one with a belt-and-suspenders `try/except
  ZeroDivisionError` on top of its own falsy check). **Found and fixed
  one real, confirmed bug**: `Return.process_locked()` (and, via the
  same call path, `Exchange.process_locked()`) computed `refund_amount`
  from `orig.revenue()` alone — correct in isolation, but a ✂️ Gawanya/
  🤝 Deni payment-method split (`Transaction.split_payment_method_
  locked()`, 2026-07-26 onward) reduces the ORIGINAL row's own `sale_
  amount` while leaving its `qty` completely untouched (only the
  original row ever carries the sale's real physical quantity — see
  that method's own docstring). `qty_returned` was already correctly
  validated against the FULL original `qty` (unaffected by a split),
  but the refund/revenue-reversal amount was silently computed from
  only PART of the money — concretely: a 500 KES sale split into 200
  cash + 300 mpesa, then returned in full, refunded/reversed only 200,
  permanently leaving 300 KES of revenue recognized for a physical unit
  that's back on the shelf. New `Return._true_sale_total(orig)`
  classmethod sums `orig.revenue()` plus every LIVE (non-void)
  `split_children` sibling's own `revenue()` — a void sibling is
  deliberately excluded, since that portion was already corrected away
  and is no longer real, recognized revenue — and `process_locked()`
  now prorates `qty_returned/original_qty_sold` against this TRUE total
  instead of `orig.revenue()` alone. Fully backward compatible: for the
  overwhelming common case (never split), `split_children` is empty and
  the total is byte-identical to `orig.revenue()` alone — confirmed by
  a dedicated regression-lock test, and by all 22 pre-existing `Return`/
  `Exchange` tests passing unmodified. Not reachable through any built
  UI today (`process_return`'s own module docstring documents the
  return-flow UI as a deliberate deferral, confirmed via a template
  grep — zero references anywhere), so no live-data backfill is needed;
  fixed now so a future session building that UI doesn't inherit a live
  landmine. Separately confirmed `revert_direct_sale_to_tab_locked()`
  does NOT share this bug shape — it corrects one specific, already-
  known transaction's own amount directly, never prorating against a
  presumed "whole sale" total the way Return's qty-based refund math
  does. 7 new tests (`ReturnPrimitiveTest` — the literal reported-shape
  scenario reproduced exactly, partial-qty proration, end-to-end revenue
  reversal, a stock-reversal regression lock proving only the money side
  changed, the voided-sibling-excluded case, the never-split regression
  lock, and one `Exchange`-specific test proving the fix propagates
  through that shared call path with zero extra code). No migrations.
- Four live requests in one message (2026-08-27): manager-on-duty banner
  staleness, debt-payment recorder attribution, Kazi Yangu full history,
  and a standing 2nd-vs-3rd-person wording rule. **(1) Manager on Duty
  strip staleness**: Roy — "the manager's 'aliingia' ... shift has been
  running for so long when he left 12 hours ago." Root cause:
  `home()`'s `active_managers` (the purple owner-dashboard banner —
  distinct from the real per-Shift rows below it, which already close
  correctly) was driven purely by `User.last_login__gte=start-of-today`,
  with zero concept of the manager actually having left. New
  `UserProfile.last_seen_at` (accounts migration 0068) — stamped by
  `SingleSessionMiddleware` on every authenticated request, throttled to
  once per `ACTIVITY_STALE_MINUTES` (5) via a plain `.update()` (never a
  full `.save()`) specifically to avoid adding write load on top of the
  already-documented `SESSION_SAVE_EVERY_REQUEST` disk-activity concern
  behind this app's own 502 incidents. `active_managers` now filters on
  `last_seen_at__gte=now - MANAGER_ACTIVITY_IDLE_MINUTES(20)` instead —
  a manager silently drops off the strip 20 minutes after their last real
  request, rather than showing a stale same-day login timestamp all day.
  Banner wording changed from "Aliingia HH:MM" (logged in at) to "Hai —
  alionekana HH:MM" (active — last seen at), matching what the figure now
  actually means. **(2) Debt-payment recorder + date/time**: the
  "Unpaid Credit Transactions" table on `customer_debt_profile.html`
  showed a bare date and no indication of which staffer actually rang the
  item up on credit — the sibling "Payment History" table right below it
  already had both. Added a "Recorded By" column (`Transaction.
  recorded_by`, already an existing field, simply never surfaced here) and
  switched the Date cell from `txn.date` (date only) to
  `txn.created_at` (date + time), matching the payment table's own
  `"d M Y, H:i"` format; `recorded_by` added to `_get_customer_debt_data`'s
  own `select_related()` to keep this free of N+1 (locked in by a
  dedicated query-count test). **(3) Kazi Yangu full history**: "ensure
  that staff can see all their data since they began." `my_work_and_pay()`
  was hardcoded to the current calendar month for its contribution
  summary, and capped Payment History / advance-request history at the
  last 12/10 rows. New shared `_staff_tenure_window(staff_profile,
  business)` (factored out of `staff_journey()`'s own pre-existing
  "earliest activity → now or departure" computation, now reused by both)
  feeds a new, additive `all_time` contribution block — same figures
  (revenue, shifts, hours, debts recovered, milestones, recognition tier)
  as the existing "this month" card, but spanning the staffer's whole
  tenure — rendered as a new "📜 Historia Yako Kamili" card, plus a
  collapsible full deduction history (`all_deductions`/
  `all_deductions_total`, every period, not just the current one).
  `pay_history`/`advance_requests` queries had their `[:12]`/`[:10]`
  slices removed entirely (small per-staff tables, no date filtering
  needed — same precedent `staff_journey()`'s own salary history query
  already established). **(4) 2nd-person wording for staff-addressed
  text**: Roy's own concrete example — Haki's recognition tier label
  "Anahitaji Kuboresha" (3rd person: "[they] need to improve") is correct
  on the OWNER-facing `haki_contribution.html` (reading ABOUT a staffer)
  but wrong on the STAFF's own `haki_kazi_yangu.html` (should be
  "Unahitaji Kuboresha" — "YOU need to improve," since it's addressed
  directly TO the staffer) — plus his general instruction to "enforce this
  anywhere there is such communication between workers across the app."
  `compute_staff_recognition(contrib, audience='owner')` gained the
  `audience` param — only the two verb-conjugated tier labels (bronze
  "Anaendelea"/"Unaendelea Vizuri", developing "Anahitaji"/"Unahitaji
  Kuboresha") actually differ by person; gold/silver/unrated carry no
  person-conjugated verb and render identically either way. Every call
  site audited and set explicitly: `my_work_and_pay()` (Kazi Yangu, always
  the staffer reading about themselves — `audience='staff'`);
  `haki_recognition_statement()` (a personal, shareable/printable
  statement — `audience='staff'` UNCONDITIONALLY, regardless of whether
  the owner or the staffer is the one currently viewing/printing it, since
  the document itself always reads as addressed to the staffer it's
  about); `staff_contribution_report()` and `staff_journey()` (both
  strictly owner/manager-only — left at the default `audience='owner'`,
  unchanged). 44 new tests across
  `UserProfileActivityTrackingTest`/`ManagerOnDutyStripTest` (accounts +
  core), `DebtProfileRecordedByDisplayTest`, `KaziYanguFullHistoryTest`,
  and additions to `StaffRecognitionTierTest`/`StaffRecognitionWiringTest`
  — including the exact reported-bug reproduction (a developing-tier
  staffer sees "Unahitaji Kuboresha" on Kazi Yangu, "Anahitaji Kuboresha"
  on the owner's report), a direct regression lock that gold/silver/
  unrated are audience-independent, full-tenure activity older than the
  current month showing on `all_time` while the this-month figure stays
  clean, and query-count regression locks for both new select_related
  additions. Two migrations (accounts 0068, additive).
- Debt-tracker duplicate-customer audit — staff vs owner showing different
  debts (2026-08-27, same-day follow-up). Roy: "I am looking at certain
  debts that show different debts from the staff's side compared to the
  owner's side in the debt tracker, could you audit this too and see if
  you might flag any excess entries or exaggerations of debt items and
  amounts out of the control of the user." Traced directly to the EXACT
  duplicate-Customer-row mechanism this file already documented and partly
  mitigated on 2026-08-09 ("two Eugenes with the same amount and same
  items") — `_get_customer_debt_data()`/`Transaction.recipient` match by a
  plain NAME STRING, not the `Customer` FK, so two `Customer` rows sharing
  a name each independently compute and display the SAME underlying debt.
  `debt_dashboard()` already WARNS about this via `duplicate_groups`
  (owner/manager only) — but its own headline `total_outstanding` figure
  was STILL silently inflated by it (each duplicate's identical outstanding
  summed in twice), and `debtors_list_api()` (the STAFF-facing "💳 Wateja
  wenye Deni" panel on all three counters) had NO awareness of this at
  all — exactly matching Roy's description: staff would see a duplicate
  name as two separate list entries, each quoting the FULL undivided
  amount, reading as "excess"/"exaggerated" debt through no fault of the
  customer. **Two distinct fixes, deliberately different matching
  strictness, reasoned through carefully**: `debt_dashboard()`'s row/total
  dedup uses EXACT string matching only (never the broader case/whitespace
  -insensitive key `_find_duplicate_customer_groups` uses for its
  warning) — because it reuses `_get_customer_debt_data()`'s own per
  -customer (exact-name-filtered) output; two customers whose names differ
  only by case/spacing reflect genuinely DIFFERENT, non-overlapping
  transaction sets, so deduping them the broader way would silently
  UNDER-count real debt instead of fixing an over-count (locked in by a
  dedicated regression test proving a case-variant customer's own separate
  80 KES debt survives alongside the exact-duplicate pair's correctly
  -deduped 480). `debtors_list_api()` was rebuilt to aggregate credit AND
  paid totals directly from `Transaction`/`CustomerDebtPayment` rows keyed
  by the case/whitespace-insensitive name (same key as `_find_duplicate_
  customer_groups`) rather than reusing any per-customer computation — this
  is safe there specifically because it's summing real rows fresh, not
  trusting two duplicate calls to agree; a payment recorded against EITHER
  duplicate now correctly reduces the whole group's combined outstanding.
  Both fixes verified to respect station scoping end-to-end (Roy's own
  explicit reminder mid-session: "each station has its own debt ledger and
  as such each staff according to permission and role should see their
  own") — `debtors_list_api()`'s scope filter is applied to `credit_qs`/
  `payment_qs` BEFORE any name-grouping happens, and `debt_dashboard()`'s
  scope is fixed per-request through `_get_customer_debt_data(customer,
  business, scope)` before the dedup ever runs — so neither fix can leak
  debt across a bar/kitchen boundary, confirmed by a direct test giving
  one duplicate-named customer debt on BOTH stations and checking a
  bar-scoped, a kitchen-scoped, and the owner's combined view each see
  only their own correct total. Existing `test_debt_dashboard_lists_both_
  duplicates_separately` (which had deliberately locked in the old,
  buggy double-listing as "known, not yet fixed") rewritten to assert the
  new deduped-to-one-row, correctly-summed-total behavior instead — the
  `duplicate_groups` warning itself is completely unchanged, still the
  real fix path via "🔀 Sahihisha Jina la Mteja." 8 new tests
  (`DebtorsListApiTest` +4, `DuplicateCustomerDebtDoubleDisplayTest`
  station-scoping +1, case-variant-not-undercounted +1, plus the rewritten
  double-listing test). No migrations.
- Test-infrastructure fix, same session: `WaitressTransactionHistoryRecordedByTest.
  test_history_query_count_does_not_grow_per_transaction` (2026-08-24) started
  failing after the `last_seen_at` activity-tracking middleware landed above —
  its own first-vs-second `/history/` request comparison was legitimately
  sensitive to the middleware's one-time-per-session write query (fires on the
  first authenticated request in a session, throttled away on the next).
  Fixed by priming the session with a throwaway request before the real
  comparison, matching this file's own established "the middleware write is a
  real, deliberate one-time cost — account for it, don't chase it away" pattern.
  Not a regression in the reported feature (query count still correctly stays
  flat as transaction count grows) — a test-authoring artifact of adding a new,
  legitimate one-time middleware query, same bug class already documented
  several times in this file for day-boundary/wall-clock-timing test fragility.
- Debt write-off: quantity-split for a consolidated multi-unit line (2026-08-28),
  live request with a screenshot: "if I users wanted to delete just one item that
  were recorded two like for instance that chrome vodka as you can see there is
  800, one is 400, meaning that if it is 800 it means two, so i need it to be
  that if i click on futa, the system in such instances asks whether you are
  deleting one item or more or all for such specific consolidations." Some
  direct-credit sales legitimately consolidate several identical units into ONE
  `Transaction` row (a cart line for "2× Chrome Vodka" at checkout creates a
  single `qty=-2` row, not two separate rows) — "Futa" (write-off/erase) on such
  a line always acted on the WHOLE consolidated amount, with no way to correct
  or erase just one of the units. New `Transaction.split_quantity_locked(cls,
  txn_id, business, qty_to_split, staff_user=None)` (`core/models.py`) —
  deliberately a NEW method, not a reuse of `split_payment_method_locked()`/
  `split_to_credit_locked()` (both existing splits are PAYMENT-CHANNEL
  corrections that always put `qty=0` on the sibling, since the physical item
  already left the shelf once — this is a genuine PHYSICAL split, where the
  sibling must carry its own real, non-zero share of `qty`). Locked
  (`select_for_update()`), refuses anything but a whole-number `qty` of 2 or
  more on the ORIGINAL transaction (a consolidated line is always an integer
  count of identical units — a fractional/keg/preset-priced line has nothing
  meaningful to "split into 1 unit"), refuses a tab-linked transaction (that
  has its own dedicated per-entry tools already) and anything not a plain,
  still-unpaid direct credit line. Splits `sale_amount` proportionally
  (`original_revenue × qty_to_split / full_qty`, quantized to cents, remainder
  = original minus split share — never independently recomputed from
  `item.selling_price`, so a manually-adjusted or discounted original amount
  divides correctly instead of being silently overwritten) and copies every
  envelope FK (`keg_barrel`/`produce_bunch`/`kitchen_batch`) onto the new
  sibling so `Transaction.cost()`'s existing proportional-share formula prices
  both halves correctly with no new logic. New sibling is `split_from`-linked
  (the same lineage field the payment-channel splits already use), so it's
  automatically visible everywhere that already reads `split_from`/
  `split_children` — the live-receipt self-healing (`_live_direct_lines()`)
  and the Futa split-cascade voiding logic both already generalise to this
  new use of the field with zero extra code. Wired into `request_write_off()`
  (`core/debt_views.py`) via an optional `qty_to_erase` POST field — when
  given and strictly between 0 and the full qty, calls `split_quantity_locked`
  first so the write-off request that follows operates on the freshly-created,
  correctly-sized sibling transaction instead of the original; `qty_to_erase`
  equal to the full qty is a no-op (proceeds on the whole line, unchanged
  behaviour), and an out-of-range value is rejected with a clear error before
  anything is touched. New quantity-picker UI in the write-off modal
  (`customer_debt_profile.html`) — "1 tu" / a custom number input / "Vyote" —
  shown only when the underlying `Transaction.qty` is a whole number ≥ 2 with
  no linked tab entry (mirroring the model method's own eligibility rule
  exactly, so the button never offers a choice the backend would reject), with
  a live KES preview of what one unit is worth before submitting. 12 new tests
  (`TransactionSplitQuantityLockedTest`, `DebtWriteOffQuantitySplitViewTest`) —
  model-layer split math and every rejection path (fractional/single-unit/
  tab-linked/non-credit/out-of-range), the full-qty-is-a-no-op case, and an
  end-to-end regression lock that erasing one of two units restores only that
  unit's own stock while leaving the other unit's transaction and debt
  untouched. No migrations (reuses the existing `split_from` field). 2605
  tests pass (core + accounts).
- home() dashboard slowness (2026-08-28), live report: "the system is slow
  when navigating from any other section of the app to home... it is too
  slow." Root-caused via a full read of the 653-line `home()` view to three
  separate, independently-fixable cost centers, all converted to real DB-side
  aggregates instead of Python-loop summation — the same discipline already
  proven correct by `till_expected_cash()` in the same file (used directly as
  the reference pattern, not reinvented). (1) `station_revenue_window_info()`
  (via `_window_revenue()`/`_window_revenue_owner_facilitated()`,
  `core/shift_views.py`) fetched every matching Issue transaction into Python
  just to sum `.revenue()` one-by-one — called once for the day's total, once
  for the owner-facilitated subset, AND once PER PENDING SHIFT in its own
  breakdown list, for BOTH bar and kitchen stations, on every single owner/
  manager home() load — the single largest cost, scaling directly with a
  business's daily transaction volume and open-shift count. Both rewritten to
  a single `.aggregate(Sum(...))` call using the app's own established
  Case/When revenue formula (`_window_revenue_expr()` — `sale_amount` when
  set, else `abs(qty) × item.selling_price` — the exact same formula
  `Transaction.revenue()` computes in Python, now computed once in SQL
  instead of once per row fetched into Python). (2) `home()`'s own
  `_period_rev()` closure (the daily/weekly/monthly revenue-targets widget)
  did the identical Python-loop summation, called 3× on every authenticated
  user's load with no role gate at all — same fix, same formula, via a
  local `_rev_expr` built the same way. (3) The UBA §M0-5 dashboard tile
  registry (`core/dashboard_tiles.py`) was eagerly computed on every load
  even though `home.html` doesn't read `uba_dashboard_tiles` yet (documented
  as a deliberate no-op at the time it was built) — for a keg business, its
  one registered example tile ran the real, non-trivial
  `keg_metrics.staff_shrinkage()` report and the result was thrown away
  unread every single time. Stopped computing it — the context key stays
  present as an empty list, satisfying the registry's own "complete no-op"
  contract, rather than paying for a report nothing displays. Confirmed the
  actual revenue FIGURES are byte-identical before and after — the aggregate
  formula is mathematically the same sum the Python loop was already
  computing, just pushed into the database — locked in by a dedicated test
  asserting exact KES values, not just that the page loads. 9 new tests
  (`HomeDashboardRevenueWindowQueryEfficiencyTest`,
  `WindowRevenueAggregateCorrectnessTest`) — a query-count-does-not-scale
  regression lock (adds 60 transactions + 6 open shifts between two `/`
  loads, asserts the query-count delta stays under 15 rather than growing
  with activity), the untouched-figures regression lock, the dead-tile-key
  no-op confirmation, and direct unit coverage of both new aggregate
  helpers (explicit `sale_amount`, the `qty×selling_price` fallback, void/
  `[SVQ]` exclusion, station scoping, the owner-facilitated subset, and the
  no-owner-on-business zero case). No migrations (pure query-shape change,
  no schema touched). 2605+9 tests pass (core + accounts).
- Keg theft valuation — revenue basis, not cost basis (2026-08-29), live
  request: Roy is running a controlled sting on a suspected counter-staff
  thief — receive 3 fresh barrels (gross 60 kg / net 50 kg, already this
  app's own `keg_default_gross_kg`/`keg_default_tare_kg` defaults, nothing
  to type), tap, let a few real pours happen, void ("Futa") a couple to
  simulate what a thief does (pours without ringing them up), then weigh —
  and needs "any variance to be attributed to the counter staff's shift
  according to expected sales according to the weight that is missing
  minus the recorded sales... displayed to the business owner in terms of
  revenue expected based on the weight sold and according to how the
  business sells their cups." Traced the existing mechanism first rather
  than assuming a gap: receiving/tapping/voiding a keg sale/weighing/per-
  shift attribution (`shift_views.attribute_variance_shift`) were all
  ALREADY correct and already confirmed working (voiding a sale already
  correctly reverses `KegBarrel.revenue_collected`/`volume_dispensed_ml`
  per the 2026-08-24 bar-ops audit fix). The real, confirmed gap: every
  existing variance figure (`keg_metrics.BarrelVariance.wastage_kes`,
  `ShiftBarrelVariance.wastage_kes`, `StaffShrinkage.loss_kes`, and
  `weigh_barrel()`'s own live SPOT-check `variance_kes`) is priced at
  either the item's COST (wastage_kes — what the business spent on the
  missing stock) or `KegBarrel.target_revenue` (a sales GOAL, usually just
  `cost × keg_revenue_multiplier`, calibrated for the barrel's own
  progress tracking) — neither is "what a thief actually pocketed," which
  is the item's real SELLING price. New `Item.keg_expected_revenue_per_ml()`
  mirrors the exact convention `bottle_expected_revenue_per_unit()` already
  established for spirits — a plain average of `price ÷ quantity_consumed`
  across the item's configured cup/pint/jug portion presets, i.e. literally
  "how the business sells their cups," not a heuristic. New `theft_kes`
  field added ADDITIVELY (never replacing the existing cost-basis figures,
  which other reports still legitimately need) to `keg_metrics.
  BarrelVariance`/`ShiftBarrelVariance` — same `variance_ml`/`variance_l`
  each function already computes, now ALSO priced at the cup rate — and a
  matching `theft_kes`/`net_theft_kes` aggregate on `StaffShrinkage`
  (positive-only sum, mirroring `loss_kes`/`net_variance_kes`'s existing
  pattern exactly). `weigh_barrel()`'s live SPOT-check response gains
  `expected_rev_cup_based`/`theft_kes` (recorded_rev stays the ACTUAL
  recorded revenue — exact, never reconstructed from a rate; only the
  "what should this weight have earned" side changes) — always returned,
  regardless of the danger-flag alert threshold, so the Bar Board weigh
  modal shows the figure on every weigh, not just a flagged one.
  `_fire_keg_alert()` gained an optional `theft_kes` kwarg (both existing
  callers — `weigh_barrel()`'s SPOT check and `shift_views.py`'s
  SHIFT_CLOSE check — now pass it; the message only grows when provided,
  so a caller that doesn't pass it is byte-for-byte unchanged). Displayed
  on three owner-facing surfaces: the live Bar Board weigh-result panel
  (a raspberry callout box, shown only when `theft_kes > 0`), `keg_barrel_
  detail`'s Shift-by-Shift Breakdown table (new "Est. Theft (Revenue)"
  column next to the existing "Waste Cost" one, plus a barrel-level
  summary stat) and `bar_shrinkage_report`'s per-staff leaderboard (new
  "Est. Keg Theft (Revenue)" column) — each labeled and explained inline
  as valuing the SAME missing volume at selling price instead of cost, so
  it will always read higher than the existing figure for an identical
  shortfall. 15 new tests (`KegTheftRevenueValuationTest`) — the rate
  helper's averaging (not weighted) and zero-preset fallback, `theft_kes`
  exceeding `wastage_kes` for an identical shortfall at both the whole-
  barrel and per-shift level, `None` without a weight reading or without
  any presets configured, `StaffShrinkage`'s positive-only aggregation,
  the live weigh response carrying both new fields (present even below
  the alert threshold), the alert call/message including it when danger
  fires, both owner-facing template surfaces rendering the new column,
  and a full end-to-end reproduction of Roy's own described sting
  (receive → tap → 3 real pours → void one to simulate theft → weigh →
  confirm the resulting theft_kes is strictly positive and exceeds the
  cost-basis figure, both from the live weigh response and independently
  from `keg_metrics.shift_barrel_variance()`). No migrations (pure
  computed methods + additive dataclass fields, no schema change).
- Home dashboard Active Shifts meter — debt-recovered breakdown, green
  alongside red (2026-08-30). Live follow-up after explaining the existing
  red "Deni KES X" (new credit placed) figure on a shift row: Roy asked for
  "another deni alongside there but in green that shows... either x amount
  of cash or mpesa was recovered so that we know what amount of the cash
  sales is part of debt recovered and what amount of mpesa sales is debt
  recovered." Traced first rather than assuming a gap: `shift_views.
  _reconcile()` has computed `debt_recovered_cash`/`debt_recovered_mpesa`
  since 2026-07-26, and `active_shift_api()`'s `all_shifts_data` (the exact
  JSON payload driving this meter) has carried both fields in every row
  since 2026-08-22 — the gap was purely that the meter's own client-side
  JS never rendered either one; only the red `credit_sales` span existed.
  Added a green span next to Cash and a green span next to M-Pesa, each
  keyed to that channel's own `debt_recovered_*` figure, matching Roy's
  own ask for a per-channel split rather than one combined figure. **Real
  semantic bug caught by the tests themselves, not shipped**: the first
  draft copied the existing owner-facilitated tooltip's wording pattern
  ("already counted inside Cash above, non-additive") — WRONG for this
  figure specifically. `owner_facilitated_cash` genuinely is a subset of
  `cash_sales` (same `Transaction` query, just also separately attributed
  by recorded_by), but `debt_recovered_cash` is a COMPLETELY SEPARATE
  query against `CustomerDebtPayment`, not `Transaction` — confirmed
  directly against `_reconcile()`'s own `expected_cash = opening_float +
  cash_sales + debt_recovered_cash - petty_total` formula, which only
  makes sense if the two are ADDITIVE, never overlapping. A debt payment
  is money collected toward an old receivable, not a new sale — it was
  never going to be part of `cash_sales` to begin with. First draft of
  the test asserted `cash_sales` should read 300+120=420 for a fixture
  with a 300 sale + a 120 cash debt payment; it failed with the real,
  correct value of 300, which is what caught the wrong tooltip wording
  before it shipped. Fixed to a "+ Deni X" prefix and corrected tooltip
  text ("nyongeza juu ya Cash hapo juu, si sehemu yake" — additive on top
  of Cash above, not a portion of it). Separate, unrelated test-fixture
  bug also caught and fixed along the way: dating the fixture's
  `Transaction.created_at`/`CustomerDebtPayment.paid_at` as `shift.
  started_at + timedelta(minutes=N)` landed AFTER real `timezone.now()`
  (since the OPEN shift's own reconciliation window ends at real "now",
  captured only milliseconds after `started_at` at fixture-build time) —
  pushed the fixture's own transactions outside `_reconcile()`'s window
  entirely, reading 0 for everything; fixed by using real `timezone.now()`
  directly instead of an artificial offset from `started_at`. 3 new tests
  (`HomeShiftMeterDebtRecoveredBreakdownTest`) — the real JSON payload
  carries both fields with correct, non-overlapping values end to end via
  a live `CustomerDebtPayment`, the shipped JS actually contains the new
  rendering logic (not just described in a commit message), and a direct
  regression lock that debt recovered never bleeds into `credit_sales`
  (a completely different model — new credit placed, not old debt paid
  back). Pure template/JS change — no backend/model change, no migrations.
- Home dashboard Active Shifts meter — per-channel totals row (2026-08-30,
  same-day follow-up). Roy: "let us go a mile extra and put totalities
  inclusive of both (cash sales + cash debt recovered) and (mpesa sales +
  mpesa debt recovered) next to (19h 10m) timer or even better right below
  the staff in shift name and the timer just above the segregation of
  both, which do you prefer" — explicitly asked for a placement call.
  Recommended and built the latter: the header row already carries the
  station icon, staff name, and start-time/timer, and on a phone-width
  screen two more money figures crammed in there would wrap awkwardly;
  a dedicated row keeps each line single-purpose (who/when → channel
  totals → composition breakdown → the existing Deni/confirmed-sales
  footer) and reads cleanly top-down. Restructured the shift row's outer
  container from a single `flex; justify-content:space-between` pair
  (header + breakdown, wrapping unpredictably depending on screen width)
  to three explicit stacked block-level divs — header, new totals row,
  breakdown row — so the totals row always renders on its own line
  regardless of viewport width, not just when content happens to wrap.
  `cashTotal = cash_sales + debt_recovered_cash`, `mpesaTotal = mpesa_
  sales + debt_recovered_mpesa` (both fields already delivered by
  `all_shifts_data`, same as the immediately-prior sprint) — rendered as
  "Jumla Cash: KES X" / "Jumla M-Pesa: KES Y" with a tooltip breaking each
  down into its own sales + recovered components. Verified the edited
  script block's JS syntax directly (extracted and `node --check`'d,
  confirming the one pre-existing "error" is unrelated raw Django
  template tags in a DIFFERENT, un-touched script block on the same
  page — not something this edit introduced). 4 tests total in
  `HomeShiftMeterDebtRecoveredBreakdownTest` (1 new —
  `test_home_page_js_renders_channel_totals_row`, locking in that the
  shipped page actually contains the new row's JS, not just a described
  intention). Pure template/JS change, no backend/model change, no
  migrations.
- Pyramid-hierarchy attribution — manager gets the owner's own acknowledgement
  pattern (2026-08-30, same-day follow-up). Live design conversation: Roy
  framed the full staff/waitress/manager/owner hierarchy as a "pyramid
  scheme" and asked me to confirm it works as intended. Investigated two
  claims separately rather than assuming either. **(1) Waitress conversion
  credit** ("she is the only person making physical hectic rounds... her
  service is a conversion of sales regardless of whoever is in the counter
  collecting the actual revenue") — traced `haki_views._staff_contribution()`
  and confirmed this was ALREADY correctly true, with zero code change
  needed: it attributes revenue/debt-recovered/debt-placed purely via
  `Transaction.recorded_by`/`CustomerDebtPayment.recorded_by`, completely
  decoupled from the shift-modal's till-accountability logic (which is a
  separate, deliberately different concern — "whose till is this cash
  physically sitting in" vs "who did the work"). Reported this back
  directly instead of building anything redundant. **(2) Manager
  attribution** — a real, confirmed gap: the owner already has a full
  "facilitated" acknowledgement pattern (2026-08-22 sprint) across 5
  backend computation sites — `_reconcile()`, `station_revenue_window_
  info()`, `till_expected_cash()`, and `bar_z_report()`'s own separate
  per-row + day-level computation — feeding Bar Board/Kitchen Board's live
  shift panels, the close-shift result panel, the home dashboard's Active
  Shifts meter and till/revenue disclosures, and the Z-report; the manager
  had none of it. Roy's own precise framing: "so long as there is a staff
  running the counter all revenue collected in the shift modal for that
  staff should be aggregated to that staff... but with an acknowledgement
  that manager/business owner sold this and that" — and the counter-caveat
  for when the counter staff has closed: "since the manager has to open
  shift to sell the inflation of revenue on the shift modal should only be
  caused by the owner's sales at the same time the shift is on." Confirmed
  via `AskUserQuestion`: (a) the manager gets his own SEPARATE
  acknowledgement line, never merged into the owner's, plus one combined
  total; (b) scope is every surface the owner pattern already touches, not
  just the one dashboard meter being looked at.

  Built the exact parallel of every owner-facilitated computation, for
  manager, across all 5 backend sites in `core/shift_views.py` +
  `core/keg_views.py`'s `bar_z_report()`: `manager_facilitated_cash/mpesa/
  credit/total/debt_recovered_cash/debt_recovered_mpesa/expected_cash`, plus
  a new combined `leadership_facilitated_total` (owner + manager summed
  across every stream) satisfying Roy's "one totality somewhere there for
  both of them." `_reconcile()`'s manager block is gated
  `staff_role not in ('owner', 'manager')` — deliberately BROADER than the
  owner block's own `staff_role != 'owner'` gate — so a manager's own shift
  correctly never self-attributes (he IS the counter custodian for it, same
  reasoning already applied to the owner's own shift), while the OWNER's
  acknowledgement note keeps firing normally during the manager's own
  shift — exactly satisfying the caveat: on a manager-run shift, only HIS
  own self-note is suppressed, the owner's is untouched. `till_expected_
  cash()`'s and `station_revenue_window_info()`'s manager mirrors have no
  self-exclusion at all (no "current shift" concept exists at that
  continuous/day-level scope, same as their existing owner siblings) — a
  manager's contribution to the day's overall total is genuinely useful
  information regardless of whose shift is open, distinct from the
  shift-modal's self-attribution concern.

  All four surfaces updated to display it, mirroring each figure's existing
  owner-facilitated note exactly: `templates/core/home.html` (till/revenue
  disclosure panels' new manager note + combined leadership total; the
  Active Shifts meter's Cash/M-Pesa tooltips extended to include both
  roles, plus a new "🧑‍💼 meneja: KES Y" note line alongside the existing
  "👤 mmiliki: KES X" one and a new "🤝 uongozi jumla: KES Z" combined-total
  line, reading `s.leadership_facilitated_total` straight from the server
  rather than recomputing client-side); `templates/core/bar/bar_board.html`
  and `templates/core/kitchen/kitchen_board.html` (identical edits in both,
  per this app's own counter-parity rule — the live shift panel's Cash/
  M-Pesa/Mikopo Mapya/Deni Zilizolipwa/Jumla/Inayotarajiwa stat boxes, the
  pre-close summary's `ownerNotes`/new `managerNotes` array builders, and
  the close-shift RESULT panel); `templates/core/bar/bar_z_report.html`
  (day-summary metric boxes gain a "(meneja: X)" line alongside "(mmiliki:
  X)", the Total Sales tile gains a leadership-total note, and each
  per-row table cell gains a 🧑‍💼 icon alongside the existing 👤, with a
  new 🤝 icon on the Total Sales column reading `row.leadership_facilitated_
  total`). Verified every inserted JS fragment both syntactically (`node
  --check`) and functionally (executed against representative fixture data,
  confirming the leadership-total note actually renders) before touching
  the templates further — the one full-file `node --check` false-positive
  encountered (a pre-existing, unrelated `{% if %}...{% else %}...{% endif
  %}` JS-string alternative at a different line entirely, `kitchen_board.
  html`'s tab-detail rendering) was confirmed via `git diff` to be
  completely outside every edited region before being dismissed, not
  assumed.

  20 new tests (`ManagerFacilitatedSalesAttributionTest`) — direct mirrors
  of every `OwnerFacilitatedSalesAttributionTest` case (blending not
  doubling, mpesa/credit guided the same way, a pre-shift-start sale
  excluded, multiple manager profiles both counted, station isolation,
  debt-recovery attribution, `manager_facilitated_expected_cash`, and the
  full JSON/context/template pipeline across `active_shift_api`,
  `close_shift`, `all_shifts_data`, `shift_history`, `bar_z_report`, and
  `till_expected_cash`) plus three tests specific to the new hierarchy
  logic: the manager's own shift never self-attributing, the owner's own
  note staying intact during the manager's own shift (the literal
  "caveat" scenario), and `leadership_facilitated_total` correctly summing
  both roles across every stream while each keeps its own separate figure.
  All pre-existing `OwnerFacilitatedSalesAttributionTest`/
  `WaitressShiftDoesNotCapRevenueTest`/`ManagerShiftDoesNotCapRevenueTest`/
  `SegmentedShiftReconcileTest` suites re-run and confirmed passing
  unmodified. No migrations (pure computation over existing `Transaction.
  recorded_by`/`CustomerDebtPayment.recorded_by`/`UserProfile.role` fields
  — no new model fields). 2653 tests pass (core + accounts).
- `audit_daily_operations` — one-shot daily transactional/service-process
  audit (2026-08-30, same-day follow-up). Roy: "are you able to audit all
  transactional and service processes for monsoon for me from yesterday" —
  this session has no direct shell/DB access to Monsoon Inn's real
  production database, so answered honestly and built the concrete
  deliverable instead: a new, read-only management command consolidating
  every relevant existing diagnostic into one report for a single
  business+day, rather than asking Roy to run six separate commands
  himself. `core/management/commands/audit_daily_operations.py`
  (`--business=NAME [--date=YYYY-MM-DD, default yesterday]`) covers, in
  order: **[1] Sales** — cash/mpesa/credit tie-out business-wide, per-
  station (bar/kitchen), and per-staff (`recorded_by`), plus a check for
  any real sale with neither `sale_amount` nor a priceable `item.
  selling_price` (would silently read as KES 0 revenue); **[2] Shifts** —
  per-shift `_reconcile()` figures, closing-vs-expected variance with the
  existing >KES 500/unreviewed flag, and a same-station overlap visibility
  note (explicitly non-alarming — `_shift_active_segments()` already de-
  overlaps two real-custodian shifts correctly, this is shown for
  transparency only, not as a bug flag); **[3] Stock movement** — a
  per-type (Issue/Receipt/Wastage/Draw/etc.) count + net qty breakdown,
  and a hard integrity check that no item touched that day shows a
  negative `current_balance()` (should be structurally impossible per
  `Item.capped_deduction()`, 2026-08-07 — a genuine hit here means that
  guarantee was bypassed somewhere, e.g. a raw `Transaction.objects.
  create()` outside the normal checkout path, exactly what my own smoke-
  test fixture tripped by skipping a Receipt for one item — confirming
  the check actually fires); **[4] Stock-take variances** raised or
  resolved that day (item/direction/book/actual/status/kind/staff);
  **[5] Receiving** — Kitchen Stock Receipts, keg RECEIVE weigh-ins, Gawa
  Kuku PortioningEvents, and plain Receipt-type transactions (excluding
  the `[ADJ]`/`[SVQ]`-family correction tags, which aren't real deliveries);
  **[6] Expenses** — Counter Cash (Petty Cash) approved/pending/rejected
  breakdown with an explicit note on which reduces `till_expected_cash()`
  and which doesn't, plus Matumizi (ad-hoc `BusinessExpense`, explicitly
  noted as bookkeeping-only, never till-affecting — see the 2026-08-09
  entry establishing that distinction); **[7] Corrections** — a visibility
  count of voids, split fragments, `[SVQ-REVERT]` miscount reversals,
  `[ADJ]`/`[ADJ-NOLOSS]` Rekebisha adjustments, and `[THEFT]`-tagged
  corrections. Then orchestrates the existing, already-tested
  `diagnose_recent_sales_visibility`, `audit_debt_ledger_integrity
  --all-customers`, and `audit_money_path_integrity` commands via Django's
  own `call_command(..., stdout=self.stdout)` — deliberately NOT
  reimplementing their logic, since each is already independently built
  and tested; this command's own value-add is the day-scoped sections plus
  composing everything into one report instead of six. Read-only
  throughout — mutates nothing. 3 new tests
  (`AuditDailyOperationsCommandTest`) — an end-to-end smoke test against a
  realistic fixture (two stations, owner/staff/manager, an overlapping
  manager shift, a transaction with no `recorded_by`, a voided sale, a
  `[ADJ]`-tagged Wastage, pending petty cash, a resolved stock-take
  variance) confirming every section renders without raising, the
  default-to-yesterday date behavior, and the no-matching-business error
  path. No migrations (no schema change). 2656 tests pass (core + accounts).
- `audit_daily_operations` made screenshot-friendly (2026-08-30, same-day
  follow-up). Roy, after screenshotting the tail of a real run: "the output
  is too much I could not paste it all so I screenshoted the last output"
  then, sharper: "you will have to shorten that command to get precisely
  what you want to see, these screenshots are too much they will waste
  me." The command's own default output (every section fully itemized —
  per-transaction, per-staff, per-shift-overlap-note, per-petty-cash-entry,
  per-receiving-event — plus all three orchestrated deep checks appended
  unconditionally) was built for completeness, not for a mobile terminal
  where the only way to get output back into this session is a screenshot
  per screenful. Redesigned around three new flags rather than trimming
  content outright (nothing was removed, only made opt-in): **`--verbose`**
  (default off) gates every itemized listing — per-staff breakdown, full
  stock-take-variance list beyond the first 5, itemized receiving events,
  itemized petty-cash/expense entries, and the informational shift-overlap
  notes — behind a single flag; the compact default keeps only the
  numbers that actually answer "is anything wrong" (totals, counts, and
  explicit FLAG lines, which are NEVER hidden regardless of verbosity,
  since suppressing the one thing worth seeing would defeat the whole
  tool). **`--section=sales|shifts|stock|variances|receiving|expenses|
  corrections|deep|all`** (default `all`) scopes a run to exactly one
  section — the intended workflow once a full compact run flags something:
  re-run just that one section with `--verbose` instead of the whole
  report again. **`--deep`** (default off, `--section=deep` always runs
  it) makes the three orchestrated commands
  (`diagnose_recent_sales_visibility`/`audit_debt_ledger_integrity --all-
  customers`/`audit_money_path_integrity`) opt-in — these were consistently
  the single longest part of the old default report (as the Section 2026-
  08-30 screenshot showed — three lines of "clean" still cost real
  scroll-and-screenshot budget on a phone) and are whole-ledger checks, not
  date-scoped, so running them every single day is rarely what's actually
  needed. Verified the size reduction concretely, not just claimed:
  against the same synthetic fixture used by the command's own smoke test,
  compact-mode output is 1,102 characters (~26 lines, fits one mobile
  screenshot) versus the old always-everything default, which included a
  full per-staff table, the complete per-entry expense/receiving lists,
  and three appended sub-command reports. 3 new tests added to
  `AuditDailyOperationsCommandTest` (6 total in the class) — `--verbose`
  measurably lengthens output and surfaces the per-staff line the compact
  run correctly omits, `--section=` includes only the requested section's
  header and excludes every other one, and `--deep` is confirmed off by
  default / on when either `--deep` or `--section=deep` is passed. No
  migrations. 2659 tests pass (core + accounts).
- `audit_daily_operations --deep` still too long — a live screenshot found
  it (2026-08-30, same-day follow-up). Roy ran `--deep` (or a full run
  including it) and had to scroll/screenshot a middle chunk anyway — his
  screenshot showed two "Dallas" line items under a customer's own unpaid-
  transaction breakdown, then "=== TOTAL: 55 customer(s) with an
  outstanding balance, KES 25,765 combined ===". Traced directly: `--deep`
  was calling `audit_debt_ledger_integrity` with `all_customers=True`
  unconditionally — that flag (per the command's own `add_arguments` help
  text) dumps a full itemized unpaid-transaction list (item/date/amount/
  days-outstanding/originating-tab) for EVERY customer in the business
  with a nonzero balance, 55 of them on Monsoon Inn — reintroducing the
  exact "too much to screenshot" problem the whole `--verbose`/`--section`/
  `--deep` redesign (earlier the same day) was built to solve, just one
  layer deeper. The real signal `--deep` needs to answer "is anything
  wrong" is the SHORT anomaly-findings section that command already prints
  FIRST (unsynced payment_method / a SETTLED tab stuck with no customer_id
  / duplicate customer names) — a handful of lines unless something is
  genuinely flagged; the itemized whole-ledger dump is a separate,
  deliberately verbose tool for when that's actually needed. Fixed by
  dropping `all_customers=True` from the orchestration call — `--deep` now
  only ever prints the findings section (plus "No integrity issues found."
  when clean); the full itemized ledger is still one command away by
  running `audit_debt_ledger_integrity --business=NAME --all-customers` (or
  `--customer=NAME` for one person) directly, unchanged. 1 new test
  (`test_deep_still_short_when_debt_ledger_has_many_customers`) reproduces
  the exact reported shape at small scale — two Customer rows sharing a
  name (a real, genuine finding, independent of any balance) plus a THIRD
  customer with a real unpaid credit transaction (so the itemized dump, if
  it were still running, would have something concrete to print for it) —
  asserting the finding's own explanation still surfaces in full while the
  itemized dump's distinct header format, its grand-total line, and the
  debtor's name are all absent. 2660 tests pass (core + accounts).
- **Root cause + fix: receipt showed one figure, debt tracker showed a
  smaller one, for the SAME transaction (2026-08-30)**, live report with
  screenshots — Roy: "why did staff add two items on debt and it reflected
  on the receipt but not in the debt tracker" (KC Pineapple 250 ML ×2,
  receipt KES 800, debt tracker KES 400, Monsoon Inn, customer Trixie).
  Root-caused via direct code trace, then confirmed by reproducing the
  exact scenario end to end in a local repro before touching any code —
  never guessed at from the screenshots alone. `Transaction.revenue()`
  (`core/models.py`) prefers `sale_amount` when set, else falls back LIVE to
  `abs(qty) × item.selling_price` — a deliberate, correct design for a
  preset/produce line whose price can legitimately differ from the item's
  flat price. The bug: a PLAIN (non-preset, non-produce) item tap on Quick
  Sell's checkout (`core/views.py::quick_sell()`) left `sale_amount=None`
  entirely — `sale_amt` was only ever set `if (sale_preset is not None or
  entry.get("stock_qty") is not None) and display_price:`. `BarTabEntry.
  amount` (and the receipt's own `recorded['subtotal']`) were ALWAYS
  correctly frozen at `line_amount` — the price genuinely charged at
  checkout time — but `Transaction.revenue()` kept recomputing LIVE from
  `item.selling_price`, so raising the item's price at ANY point after the
  sale silently changed what that now-historical sale was worth, forever,
  on every surface that calls `.revenue()`: the debt tracker's own
  `_get_customer_debt_data()` (`core/debt_views.py`) reads `txn.revenue()`
  directly for BOTH its `total_credit` aggregate AND its per-row "Amount
  Owed" figure (`remaining = txn.revenue() - entry.amount_paid`) — exactly
  why the debt tracker page and the customer's own receipt (whichever
  render path reflects the frozen `entry.amount` vs the live-drifting
  `revenue()`) can show two different numbers for one sale. Confirmed the
  identical shape ALSO existed in Quick Sell's own STK-cart settlement
  callback (`core/mpesa_views.py::_settle_qs_from_payment`) — `sale_amount
  = amount if (preset or amount != qty * item.selling_price) else None`,
  the same "skip pinning when it currently matches the live formula
  anyway" trap, one narrower variant of the same bug. Confirmed Bar
  Board's checkout has no plain-item path of its own (keg-cart only,
  everything else routes through Quick Sell) and Kitchen Board's plain
  portion-item branch ALREADY unconditionally pins `sale_amount=amount` —
  so the gap was isolated to these two Quick Sell call sites, not
  systemic across every counter. Also confirmed, by re-reading `split_
  paid_unpaid_locked()`/`split_kept_unpaid_locked()` directly, that ANY
  split mechanism in this app already writes a real (non-null)
  `sale_amount` onto the original transaction the moment it touches
  `entry.amount` — meaning a transaction still showing `sale_amount=NULL`
  today was NEVER split, so `entry.amount` (when a tab entry exists) is
  guaranteed to still be the untouched original — the safe, sound
  foundation for a backfill. **Fix**: both call sites now pin `sale_amount`
  unconditionally, for every line, matching what `BarTabEntry.amount` and
  the receipt already captured all along — a plain item is no longer
  special-cased differently from a preset/produce one. **Backfill**: new
  `backfill_missing_sale_amount` management command (`--business=`,
  `--dry-run`) recovers the true historical amount for every existing
  `sale_amount IS NULL` Issue transaction from whichever of two frozen
  snapshots survives — a linked `BarTabEntry.amount` (tab/credit sales,
  safe per the split-mechanism guarantee above), or a matching `Receipt.
  lines` entry found by `txn_id` (a direct, tab-less sale) — and reports
  two distinct things per business, since they need different follow-up:
  transactions **ALREADY DRIFTED** (the recovered value disagrees with
  what `revenue()` currently returns — a real, live-right-now over/under-
  statement someone may already be looking at, with the exact KES delta)
  vs merely now-pinned-for-the-future (recoverable, backfilled, but
  nothing currently disagrees — pinning only prevents a FUTURE price edit
  from causing this). A transaction recoverable by neither source is left
  untouched and listed separately, same "no automatic repair, reconcile
  manually" honesty this app already applies everywhere a historical value
  genuinely can't be reconstructed. Answers Roy's own direct follow-up
  ("what about such an effect in all customers") precisely: `--dry-run`
  with no `--business` filter scans every business on the platform at
  once and reports the true scope, not just Monsoon Inn/Trixie. 13 new
  tests (`SaleAmountPinnedAgainstPriceDriftTest` — the literal reported bug
  reproduced end to end through the real `/quick-sell/` checkout, a tab
  converted to debt via `_convert_tab_to_debt_core`, and the debt tracker's
  own `_get_customer_debt_data()` proven to agree with the receipt/entry
  after a later price change; the STK-settlement sibling fix locked in the
  same way; `BackfillMissingSaleAmountTest` — recovery from a tab entry
  with drift correctly flagged, recovery from a receipt line, the
  recovered-but-not-yet-drifted case correctly NOT flagged as drift, the
  not-recoverable case left untouched, dry-run, business scoping, and
  idempotent re-run). No migrations (no schema change — pure checkout-code
  fix plus a read-only-safe backfill of an already-existing nullable
  field). Action for Roy: run `python manage.py backfill_missing_sale_
  amount --dry-run` (omit `--business=` to scan every business) on
  Render's Shell to see the true scope platform-wide, then re-run without
  `--dry-run` to apply.
- Fix: Kitchen Board's own "+Pata Stok" invisible for a cross-access bar
  staffer (2026-08-31), live request: Roy gave a bar staffer (Recheal
  Katanu) Kitchen Board access (`can_access_kitchen`) plus the general
  "Stock Receiving Access" toggle (`can_receive_stock`) to cover for
  kitchen staff on leave, without changing her role — but the "+Pata Stok"
  button never appeared on the Kitchen Board itself. Root cause, confirmed
  by direct code trace: Kitchen Board's own receive flow is gated by a
  SEPARATE, already-role-agnostic field, `can_receive_kitchen_stock`
  (`core/kitchen_views.py`: `is_owner or getattr(up, 'can_receive_kitchen_
  stock', False)` — already exercised for a non-owner staffer by the
  pre-existing `KitchenStockReceiptPermissionTest.test_staff_with_can_
  receive_kitchen_stock_allowed`), completely independent of `can_receive_
  stock` (which only governs Add Transaction's Receipt flow and Quick
  Sell's own "+Pata Stok" shortcut, per its own 2026-08-11 migration help
  text). The toggle for the CORRECT field exists in `staff_permissions.
  html` — but was wrapped in `{% if biz_profile.modules.kitchen and staff_
  profile.role == 'kitchen' %}`, so it was completely invisible for any
  staffer whose role isn't literally `'kitchen'`, even with full cross-
  access granted. Widened the condition to `staff_profile.role ==
  'kitchen' or staff_profile.can_access_kitchen` — matching the backend's
  own role-agnostic check exactly — via two separate nested `{% if %}`
  tags rather than one combined `and`/`or` expression, since Django
  templates evaluate `and` before `or` with no way to parenthesize a
  sub-clause, and the naive single-line version I first wrote (`A and B or
  C`) would have parsed as `(A and B) or C`, wrongly showing the toggle
  even for a `can_access_kitchen` staffer on a business with NO kitchen
  module at all — caught before shipping, not after. This block used to be
  nested inside the outer `{% if staff_profile.is_kitchen_staff %}` that
  also wraps the separate "Require Shift Before Kitchen Work" toggle right
  above it — pulled the Pata Stok block out to be a fully independent
  sibling `{% if %}` (closing `is_kitchen_staff` immediately after the
  shift-requirement div instead), so the shift-requirement toggle's own
  visibility (still role=='kitchen' only, untouched — out of scope for
  this report) can never be accidentally affected by this change.
  `accounts/views.py`'s `staff_permissions()` POST handler already reads
  and saves `can_receive_kitchen_stock` unconditionally regardless of
  role (matching its own "harmless no-op to save 'off' for a [role] who
  could never reach [this] control anyway" convention already used for
  the manager-only toggles) — no backend change needed, this was a purely
  template-visibility gap. 5 new tests
  (`KitchenPataStokVisibilityForCrossAccessStaffTest`) — toggle now visible
  for a cross-access bar staffer, still hidden for a staffer with neither
  role='kitchen' nor can_access_kitchen, still visible for native kitchen
  role (regression lock), hidden entirely on a business with no kitchen
  module (the exact case the and/or precedence bug would have broken),
  and a full save/unsave round-trip through the real POST endpoint. No
  migrations (no schema change — template-only fix).
- Test-suite fix, same session (2026-09-02): the full-suite run crashed
  outright (a pickle error from Django's parallel test runner, masking the
  real failure) on `OwnerConsumptionLimitTest.test_daily_window_ignores_
  an_earlier_day` — same day/month-boundary wall-clock test-authoring bug
  class already documented repeatedly in this file
  (`PettyCashReviewUndoTest`, `BarZReportOverlappingShiftsTest`,
  `AdHocExpenseDayReconciliationTest`), a month-boundary variant this
  time: the test hardcoded `timezone.now() - timedelta(days=2)` for a
  transaction meant to land on "an earlier day, still this month" — which
  breaks whenever the suite happens to run on the 1st or 2nd of a
  calendar month, since 2 days ago then falls in the PREVIOUS month
  (`owner_consumption_usage`'s `'monthly'` window correctly, deliberately
  resets at `window_start('monthly')` = the 1st of the current month —
  production behavior is right, only the test's date construction was
  wrong). Fixed by anchoring to `window_start('monthly') + timedelta(hours=1)`
  instead — deterministic and always inside the current calendar month
  regardless of what day the suite runs on. No migrations.
- Four live fixes in one message (2026-09-02): owner self-approving his own
  debt write-off, a write-off's removal not reflecting on the customer's
  live receipt, the public ledger missing staff names, and no partial
  payment for a keg tab in Bar Board. **(1) Owner write-off self-
  approval confusion** — Roy: "owner should not see (futa/omba) who is he
  requesting to approve deletion when he is the owner surely." Root cause,
  confirmed by direct code trace: `request_write_off()`
  (`core/debt_views.py`) ALWAYS created a PENDING `WriteOffRequest` for a
  REAL write-off (`is_mistake=False` — the checkbox's own default state),
  regardless of who submitted it, per the function's own docstring:
  "Owner/manager: same — approval is always a separate action for audit
  trail." For the owner specifically this meant submitting a request and
  then having to separately click ✅ approve on his OWN just-submitted
  request — meaningless friction, since the final decision on a real
  write-off has always been owner-only anyway (`_can_approve_debt_
  action`). Fixed: `if (is_mistake and not up.business.debt_erase_
  requires_approval) or up.is_owner:` — the owner now ALWAYS self-executes
  immediately via the same `_execute_write_off_approval(..., self_
  service=True)` path already used for the pre-existing erase_mistake
  self-service case, for BOTH is_mistake and a real write-off. A MANAGER
  submitting a real write-off is deliberately UNCHANGED — still goes
  through the pending state, genuine two-person control, since a manager
  can never give the final decision on a real write-off (only recommend,
  via `manager_review_write_off`). Found and fixed a real bug WHILE making
  this change: `_execute_write_off_approval`'s `self_service=True` message
  branch unconditionally said "Stock imerejeshwa" (stock restored) —
  correct for erase_mistake, but now also reachable for a REAL write-off
  (which never restores stock, only forgives the receivable) — would have
  been a factually wrong confirmation shown to the owner. Now branches on
  `is_mistake` for the message text. Button/modal wording updated to match
  — row button: "Futa" (was "Futa / Omba"); modal title: "Futa Kiingilio"
  (was "Futa / Omba Write-off"); submit button: "Futa" for owner, "Tuma
  Ombi" unchanged for staff (who genuinely IS requesting); the modal's
  info banner now tells the owner plainly "itafutwa mara moja — hakuna
  idhini inayohitajika kwako" instead of the staff-only "will be sent for
  approval" text, which was never conditionally hidden from the owner's
  submit-button reset path before either (fixed the JS to capture and
  restore the button's own original text on error, instead of two
  hardcoded 'Tuma Ombi' resets that would have been wrong for the owner's
  now-different label). **(2) Write-off not reflecting on the customer's
  live receipt** — same root cause as (1), not a separate bug: before this
  fix, an owner's real write-off sat PENDING (the `Transaction` was never
  actually voided until a separate approve click), so of course the
  customer's own ledger still showed it as owed — it genuinely still WAS
  owed. Confirmed `customer_transaction_history()` (`core/customer_
  profile.py`) already correctly `.exclude(payment_method='void')` — the
  moment the write-off actually executes (now immediate for the owner),
  the customer's ledger self-corrects on its very next load with zero
  additional code needed. **(3) Public ledger missing staff names** — Roy:
  "it needs dates and staff who served and recorded" — traced to
  `templates/core/customer_ledger_public.html` specifically: dates were
  ALREADY shown there; `customer_transaction_history()`'s row dict already
  carries `served_by`/`recorded_by` (confirmed already correctly rendered
  on the STAFF-facing sibling page, `customer_journey.html`) — only the
  CUSTOMER-facing public ledger template never rendered them. Added a
  "Aliyehudumia: X · Aliyeandika: Y" line per row, matching `customer_
  journey.html`'s own wording convention. **(4) No partial payment for a
  keg tab in Bar Board's tabs drawer** — Roy: "there is no partial payment
  for keg in the bar board in tabs." Root cause, confirmed by direct code
  trace: `bar_board.html`'s `renderTabs()` has TWO separate tab-card
  render paths — the REGULAR one (an ordinary keg/bar tab — what every
  plain keg tab actually renders through) and a MIXED one (a food tab that
  also carries bar items, cross-counter-merged). Only the MIXED path ever
  built the "Kiasi" amount `<input id="tab-partial-amount-<id>">` — the
  REGULAR path's own partial-selection row jumped straight from the
  selected-total display to the Cash/M-Pesa/STK buttons, with no way to
  type a smaller amount. `settleTabPartial()`/`settleTabPartialStk()` both
  already look up that input by this exact id regardless of which path
  built the card — with it missing, `amountEl` was always `null`,
  `userTouched` always `false`, so checking items and tapping a payment
  button ALWAYS settled the full selected total; a customer paying "mpesa
  70 of an 80 keg tab" had no way to be recorded correctly. The backend
  (`settle_entries_amount_locked`, shipped 2026-07-25) already fully
  supports a partial amount — this was purely a template gap in the
  REGULAR render path, mirrored in from the MIXED path's own already-
  correct markup. `quick_sell.html` was confirmed to have only one render
  path (already correct, no gap); `kitchen_board.html` uses a modal-based
  settle flow entirely, a different mechanism not affected by this bug.
  60 new tests (`WriteOffOwnerSelfExecuteWordingTest`,
  `BarBoardTabPartialAmountInputTest`, plus additions to
  `CustomerProfilingTest`) — including the exact reported end-to-end
  scenario (owner submits a real write-off with no separate approve step,
  it disappears from the customer's own public ledger on the very next
  load) and 2 pre-existing tests updated for the new owner-self-executes
  contract (`DebtEraseMistakeTest.test_regular_writeoff_never_touches_
  keg_barrel_envelope`, `DebtWriteOffQuantitySplitViewTest`'s quantity-
  split real-write-off test) with a new manager-still-needs-approval
  sibling test added alongside each so the two-person-control case stays
  regression-locked. No migrations (no schema change — pure view-logic +
  template fix).
- Fix: direct credit sales silently split across a duplicate Customer row
  (2026-09-03), live report — Roy relayed a staff claim: "for direct debt
  sales when they put in 2 items it shows the two items in recent sales
  but in the debt tracker it only shows one." Investigated rather than
  reproduced verbatim (a SINGLE Quick Sell checkout with 2 cart items
  turned out NOT to be able to split — one POST captures `recipient` once
  and reuses it identically for every cart-loop iteration) — but found the
  REAL, confirmed, reproducible mechanism one layer up: the same customer
  typed with different capitalization on two SEPARATE credit checkouts
  (very plausible on a busy counter — `qsRecipientData`'s own JS is a bare
  `customerName.trim()`, no autocomplete forcing a canonical spelling).
  `core/customer_profile.py`'s own docstring already documents the
  established, correct convention for this exact risk: "case-insensitive
  throughout... never a bare =, since the same person is routinely typed
  with different capitalization across a busy evening" — and every OTHER
  counter already follows it (`kitchen_views.py`'s credit-checkout
  Customer lookup uses `name__iexact`; so does every relevant `keg_views.
  py` lookup). Quick Sell (`core/views.py`) was the one exception: found
  SEVEN separate `Customer.objects.filter(..., name=credit_recipient)`
  calls — a bare, case-SENSITIVE match — auto-creating/reusing the
  customer for a credit sale. Typing "roy" against an existing
  Customer("Roy") silently created a SECOND Customer row; the Receipt's
  own same-day dedup (`_existing_rcpt` lookup) already merges by
  `customer_name__iexact`, so both sales still appeared together on one
  receipt — but each Customer's own debt profile
  (`_get_customer_debt_data`'s `credit_qs`) filters
  `Transaction.recipient=customer.name`, a deliberate EXACT string match
  (see that function's own docstring — two Customer rows sharing a name is
  the KNOWN failure mode it defends against via the "🔀 Sahihisha Jina la
  Mteja" merge tool, not via a broader match there), so the second item
  never showed on either individual debt page — matching the reported
  symptom precisely. **Two-part fix, both needed together** (confirmed by
  a failing intermediate test before the second part): (1) every credit/
  debt-sale Customer LOOKUP fixed to `name__iexact` — `core/views.py`
  (`add_transaction`'s `new_customer_name` + CREDIT DISCIPLINE GATE, and
  all 6 remaining Quick Sell sites: full-credit gate, partial-credit gate,
  the "Lipa kidogo" split lookup, the TAB SALE branch, the DENI/direct
  branch — the site most directly behind the report — and the receipt's
  `_build_credit_receipt_meta` lookup), `core/keg_views.py` (`void_tab`'s
  two defaulter-flagging lookups), `core/debt_views.py`
  (`_execute_write_off_approval`'s defaulter-flagging lookup), and the
  same bug class in three UBA module views (`payment_plans_views.py`,
  `rentals_views.py`, `salon_views.py`) — prevents a NEW duplicate
  Customer row from ever being created this way again. (2) NOT sufficient
  on its own, confirmed by writing the test first: reusing the existing
  Customer still leaves `Transaction.recipient` written as whatever raw
  string was typed THIS checkout, which still fails the debt tracker's
  own deliberately-exact match against `customer.name`. Fixed by
  normalizing `credit_recipient` itself ONCE, right where it's first read
  from POST in `quick_sell()` — before any Transaction, BarTab, or receipt
  branch runs — to the EXISTING Customer's canonical `.name` when one is
  found (`name__iexact`); a genuinely new customer name is left as typed,
  becoming the canonical spelling for every future sale under it. This
  single normalization point means every downstream use (the per-item
  loop's `recipient=`, the BarTab `customer_name=` lookup, the DENI
  branch's own Customer/receipt handling) all consistently reference one
  spelling, closing the loop end to end. Existing, ALREADY-split duplicate
  Customer rows from before this fix are not retroactively merged —
  `🔀 Sahihisha Jina la Mteja` (per-customer) and
  `audit_debt_ledger_integrity --all-customers` (whole-business scan,
  2026-08-14) are the existing, already-built tools for that; not rebuilt
  here. 6 new tests (`CustomerCaseInsensitiveDebtLookupTest`) — a single
  checkout reusing an existing differently-cased Customer (no duplicate
  created), the literal two-separate-checkouts reported scenario end to
  end (asserts both items land on ONE customer's `unpaid_transactions`,
  matching the merged receipt — this test failed after part (1) alone,
  confirming part (2) was genuinely necessary, not defensive
  over-engineering), `add_transaction`'s two sites, and both defaulter-
  flagging sites (`void_tab`, real write-off) landing on the existing
  Customer rather than silently missing it. No migrations (pure query/
  string-normalization fix, no schema change).
