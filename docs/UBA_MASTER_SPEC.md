# Duka Mwecheche — Universal Business Architecture (UBA)
### Master spec for extending the platform beyond Bar and Kibanda
Author brief: Roy (Collins Mwendwa) · Drafted 2026-08-02 · Target implementer: Claude Code (Sonnet 5)

---

## 0. How to use this document

This is a **consolidated master spec**, not a sprint document. Structure:

- **Part A (§1–§4)** — the architectural spine. Read this before ANY sprint. It replaces the current
  "one bespoke module per business type" approach with a **capability composition model**.
- **Part B (§5–§7)** — foundation sprints that benefit Bar and Kibanda immediately (multi-store,
  split-tender partial payments). Do these FIRST.
- **Part C (§8–§13)** — per-business-type designs: Retail/Minimart, Apparel, Salon, Rentals,
  plus composed profiles (pharmacy, butchery, hardware, gas, phone shop).
- **Part D (§14–§16)** — Supply chain (suppliers, payables, riders), Customer storefront, Sequencing.
- **Part E (§17–§18)** — Infrastructure warnings and open questions Roy must answer.

**For Claude Code sessions:** copy ONE sprint block at a time (each is self-contained and
labelled `SPRINT <ID>`). Every sprint block already contains its Cause-&-Effect Map per the
protocol in `CLAUDE.md`. Do not start a sprint until every "yes" row in its map is understood.

**Non-negotiable house rules for every sprint in this document** (from `CLAUDE.md`, restated
because they are violated most often when working at speed):
1. Never name a variable `_` anywhere in Python. Use `_unused`, `_created`, `_discard`.
2. Never `class="text-muted"` → `style="color: #b0b0b0"`. Never Bootstrap `bg-*` on cards.
   `btn-gold` not `btn-primary`.
3. Never `{% trans %}` inside single-quoted JS strings.
4. Never `@login_required` on JSON/AJAX endpoints — return `{"...": 0}` for anonymous.
5. Never `get_or_create(business=x, name=y)` on Customer — `filter(...).first()` then create.
6. `float * Decimal` → cast both.
7. Output COMPLETE files, one at a time.
8. Regression sweep before "done": grep every reader/writer of any field or helper you changed.
9. `python manage.py check`, `makemigrations --check`, `python manage.py test`, then commit
   and push to main. Every time, without being asked.
10. Multi-tenancy: every queryset scoped to `request.user.userprofile.business`. From this
    document onward, **also scoped to store** where the store dimension exists (§5).

**VERIFY-ME markers** appear where I could not confirm current code state from `CLAUDE.md`
alone. Claude Code must open the referenced file and confirm before implementing.

---

## 1. Diagnosis — why the system feels "too large and impractical"

The instinct is right, but the cause is not size. It is **shape**.

Today the app grows by *addition*: Bar got `keg_views.py`, `bar_board.html`, `KegBarrel`,
`ShiftStockCount`, `keg_metrics.py`. Kibanda got `produce_views.py`, `ProduceBunch`,
`produce_board`. Kitchen got `kitchen_views.py`, `kitchen_board.html`. Each new business type
has meant a new vertical stack, a new board template, new navbar entries, new analytics
sections, and new discriminators.

Three consequences, all of which you are already feeling:

**(a) Combinatorial cost.** Adding Retail, Apparel, Salon and Rentals the same way means four
more vertical stacks — roughly quadrupling the surface area that every future fix must sweep.
Your own `CLAUDE.md` rule ("everything in this app is connected — audit ALL surfaces") becomes
unenforceable by hand at that size.

**(b) Irrelevance leakage.** A salon owner logging in today sees Stock List, Expiry, Reorder
Level, Landed Cost, Yield Factor, Kibanda produce toggles. None of it is wrong; all of it is
noise. Perceived bloat is not "too many features" — it is **features that cannot switch
themselves off**. `business_profiles.py` gates the navbar; it does not gate forms, dashboards,
analytics sections, or vocabulary.

**(c) Reuse is invisible.** The three hardest things you have already built are far more
general than their names suggest, and you are currently one rename away from four new markets:

| What you built | What it actually is | Who else needs it |
|---|---|---|
| `ProduceBunch` (revenue envelope) | Buy a batch at cost, sell by price point until target revenue is reached, no unit counting | **Mitumba bale** (KES 12,000 bale → sell 500/300/100 bob) · **Butchery carcass** · **Charcoal/gunia goods** · **Fish crate** |
| Keg book-vs-scale + `StaffShrinkage` + Z-report | **Expected-vs-Actual variance engine with staff attribution** | **Every business type.** Only the measurement changes |
| Haki module + debt tracker + credit policy | Two-way accountability ledger between a business and the people it transacts with | **Salon stylists** · **Landlord/tenant** · **Consignment boutiques** · **Rider COD** |

So the correct move is not to trim the app. It is to **extract the primitives you already own,
express every business type as a composition of them, and let the profile switch off everything
it does not compose.** That is what the rest of this document specifies.

---

## 2. The Capability Model — the spine

A business type stops being a hardcoded branch and becomes a **declaration**:

> "A salon composes: SERVICE stock model + UNIT (retail products) + BOOKING sale mechanic +
> COMMISSION payout + RECIPE-variance accountability. It does NOT compose: expiry, yield
> factor, envelope batches, shift weigh-in."

Everything downstream — navbar, dashboard tiles, item form fields, analytics sections,
vocabulary, Z-report contents, onboarding tour — reads that declaration.

### 2.1 Stock models (how a sellable thing is counted)

There are exactly eight. Every business you will ever onboard uses a subset.

| Key | Name | Semantics | Status today |
|---|---|---|---|
| `UNIT` | Countable unit | Discrete integer/decimal balance. Bottle, bar of soap, shirt | **Built** (default `Item`) |
| `MEASURE` | Weight / volume | Decimal balance in kg/L/g; decanted from a bulk parent | **Partial** — `yield_item`/`yield_factor` covers some of it |
| `ENVELOPE` | Revenue batch | No count. Cost in, target revenue out, deplete by price points | **Built** (`ProduceBunch`) — needs generalising |
| `VARIANT` | Attribute matrix | One product, N sellable children by size/colour/spec | **Missing** — needed by Apparel, hardware, phone shop |
| `SERIAL` | Serialised unit | Each physical unit individually identified (IMEI, asset tag, chassis) | **Missing** — phone shop, electronics |
| `LOT` | Batch with expiry | FIFO depletion by batch, each batch has its own expiry and cost | **Partial** — `Transaction.expiry_date` exists, FIFO depletion does not |
| `SERVICE` | Time + skill | No stock of its own; consumes supplies per a recipe; occupies a staff slot | **Missing** — Salon, garage, clinic |
| `ASSET` | Returnable asset | Goes out and must come back. Tracks occupancy/possession, not depletion | **Missing** — Rentals, gas cylinders, crates, chairs/tents |

**Implementation:** `Item.stock_model` CharField, choices above, default `UNIT`.
Existing rows backfill: `is_produce and produce_mode='BUNCH'` → `ENVELOPE`; `is_produce and
produce_mode='PORTION'` → `UNIT`; keg items → `MEASURE`; everything else → `UNIT`.

**Discriminator consistency (CLAUDE.md rule):** `stock_model` becomes the canonical key, but
**do not remove `is_produce`, `produce_mode`, or `produce_bunch_id`** — they are load-bearing in
`_units()`, analytics, and the produce board. Keep them in sync inside `Item.save()`:
```python
# core/models.py — Item.save()
if self.is_produce and self.produce_mode == 'BUNCH':
    self.stock_model = 'ENVELOPE'
elif self.is_produce:
    self.stock_model = 'UNIT'
```
New code reads `stock_model`. Old code keeps working. Migrate readers opportunistically, never
in the same sprint as a feature.

### 2.2 Sale mechanics (how money is taken)

| Key | Name | Status |
|---|---|---|
| `POS_CART` | Cart checkout, cash/M-Pesa/credit | **Built** |
| `PRESET` | Price-point tiles / portions | **Built** |
| `TAB` | Open running tab, settle later | **Built** (bar + kitchen) |
| `CREDIT` | Deni — debt ledger, aging, credit policy gate | **Built** (K1/K3/K4) |
| `SPLIT_TENDER` | One sale paid partly cash + partly M-Pesa + partly credit | **Missing** → §7 (Kibanda's gap) |
| `PLAN` | Layaway / instalments / deposit held over time | **Missing** → §7 |
| `BOOKING` | Appointment reserving a staff slot, optional deposit | **Missing** → §11 (Salon) |
| `CYCLE` | Recurring billing period (rent, subscription) | **Missing** → §12 (Rentals) |
| `CONSIGNMENT` | Sell someone else's stock, owe them a share | **Missing** → §10 (Apparel) |
| `COMMISSION` | Staff earns a % or fixed cut per sale/service | **Missing** → §11, feeds Haki |
| `ONLINE` | Customer-initiated order from public storefront | **Partial** → §15 |

### 2.3 The Accountability Engine — generalise what you built for the bar

This is the heart of the platform and your real differentiator. Nobody else in this market
ships variance attribution. Right now it is spelled `keg_metrics.py`. It should be spelled
`core/accountability.py` and serve every business type.

The engine is always the same five steps:

```
1. ESTABLISH   — a measurable opening position    (weight / count / meter / asset roll)
2. EXPECT      — derive what SHOULD have happened  (sales × recipe/preset/portion)
3. OBSERVE     — a measurable closing position     (weight / count / meter / asset roll)
4. ATTRIBUTE   — variance → shift → staff          (StaffShrinkage)
5. SURFACE     — leaderboard · Z-report · alert · learned baseline
```

Only the *measurement* changes per business type. Everything else is shared code:

| Business type | ESTABLISH / OBSERVE | EXPECT is derived from | Primary loss vector it catches |
|---|---|---|---|
| Bar (built) | Barrel weight, bottle tots | Presets sold × ml | Over-pour, unrung sales, watering |
| Kibanda | Bunch envelope remaining | Target revenue vs collected | Under-ringing, "bei ya rafiki" |
| Kitchen | Portions vs raw input | Recipe yield per raw kg | Free plates, portion inflation |
| **Retail** | ABC cycle count | Book balance from transactions | Counter shrink, sweethearting, till skim |
| **Apparel** | Variant count + fitting-room log | Book balance per variant | Piece walk-off, price substitution |
| **Salon** | Supply stock count (dye, relaxer) | `ServiceSupplyRecipe` × services done | **Side-clients served on salon supplies** |
| **Rentals** | Asset return count + condition; rent roll | Agreements × rate × days | Uncollected rent, caretaker skim, asset loss |
| **All** | Cash drawer count | Cash sales − petty cash out | Till variance |

**Sprint AE1 (do it as part of §6/M0, not standalone):** rename/extend `keg_metrics.py` →
`core/accountability.py` exposing a stable contract:

```python
@dataclass
class VarianceResult:
    expected: Decimal
    actual: Decimal
    variance: Decimal          # actual - expected (negative = loss)
    variance_kes: Decimal
    variance_pct: Decimal | None
    baseline_pct: Decimal | None      # learned normal loss (F3 pattern)
    flag: str                          # 'ok' | 'watch' | 'danger'
    coverage_pct: Decimal              # % of value actually measured — HONESTY FIELD
    is_partial: bool                   # True when coverage < 100

def variance_for(scope, *, business, store=None, shift=None, date_from, date_to) -> VarianceResult
def attribute(result, *, shift) -> StaffShrinkage
def leaderboard(business, *, store=None, date_from, date_to) -> list[StaffRow]
```

Keep the two hardened rules you already won:
- **Aggregate losses in KES, never as a mean of percentages.**
- **Always surface `coverage_pct`, and visually mark partially-measured rows.** A staff member
  must never be ranked worst because they happened to be more measured than someone else.

Keep the F3 learned-baseline pattern too: every new variance type must learn its own normal
loss band before it starts accusing anyone (`Still learning N/3`).

### 2.4 Profile = composition

Replace type-specific `if business.business_type == 'bar'` branches with a declarative registry.

```python
# core/business_profiles.py  (extend the existing registry — VERIFY-ME: read the file first,
# CLAUDE.md says 8 profiles + catalogs exist; preserve their current keys and catalogs)

@dataclass(frozen=True)
class Capability:
    stock_models: frozenset       # {'UNIT','ENVELOPE',...}
    sale_mechanics: frozenset     # {'POS_CART','TAB',...}
    accountability: frozenset     # {'SHIFT','CYCLE_COUNT','RECIPE_VARIANCE','ASSET_RETURN','CASH_DRAWER'}
    modules: frozenset            # {'bar','kitchen','produce','salon','rental','storefront'}
    hides: frozenset              # item-form + dashboard fields to suppress
    vocabulary: dict              # {'item':'Bidhaa','sale':'Mauzo','customer':'Mteja'}
    board_template: str           # which POS surface is the home screen
```

**The composition matrix** (this table is the single source of truth for §8–§13):

| Profile | Stock models | Sale mechanics | Accountability | Home board |
|---|---|---|---|---|
| Bar / local joint | MEASURE, UNIT, VARIANT(tots) | POS_CART, PRESET, TAB, CREDIT, SPLIT_TENDER | SHIFT, WEIGH_IN, CASH_DRAWER | `bar_board` |
| Kibanda / mboga | ENVELOPE, UNIT | POS_CART, PRESET, CREDIT, **SPLIT_TENDER** | SHIFT, CASH_DRAWER | `quick_sell` (produce board) |
| Kitchen / grill | UNIT, ENVELOPE | POS_CART, PRESET, TAB, CREDIT | SHIFT, RECIPE_VARIANCE | `kitchen_board` |
| **Minimart / retail** | UNIT, LOT, VARIANT, MEASURE | POS_CART, CREDIT, SPLIT_TENDER, PLAN, ONLINE | SHIFT, CYCLE_COUNT, CASH_DRAWER | `retail_board` |
| **Apparel / boutique** | VARIANT, UNIT, ENVELOPE(bale) | POS_CART, PLAN(layaway), CREDIT, CONSIGNMENT, ONLINE | SHIFT, CYCLE_COUNT, FITTING_ROOM | `retail_board` (variant mode) |
| **Salon / barber** | SERVICE, UNIT | BOOKING, POS_CART, COMMISSION, CREDIT, PLAN | SHIFT, RECIPE_VARIANCE, CASH_DRAWER | `salon_board` |
| **Rentals (property)** | ASSET | CYCLE, PLAN(deposit), CREDIT | RENT_ROLL, METER | `rental_board` |
| **Rentals (equipment)** | ASSET, UNIT | POS_CART, PLAN(deposit), CYCLE | ASSET_RETURN, CONDITION | `rental_board` |
| Pharmacy / chemist | LOT, UNIT | POS_CART, CREDIT | CYCLE_COUNT, EXPIRY, RESTRICTED | `retail_board` |
| Butchery | ENVELOPE(carcass), MEASURE | POS_CART, PRESET, CREDIT | SHIFT, YIELD_VARIANCE | `retail_board` |
| Hardware | UNIT, VARIANT, MEASURE | POS_CART, CREDIT, PLAN | CYCLE_COUNT | `retail_board` |
| Phone / electronics | SERIAL, UNIT, VARIANT | POS_CART, PLAN, CREDIT | SERIAL_AUDIT, WARRANTY | `retail_board` |
| Gas / water | ASSET(cylinder), UNIT | POS_CART, EXCHANGE, CREDIT | ASSET_RETURN, DEPOSIT_LEDGER | `retail_board` |

Note how few genuinely new *screens* this needs: `retail_board`, `salon_board`, `rental_board`.
Everything else is composition.

---

## 3. What "practical for their industry" actually means

Before the per-type designs, the test each one must pass. A feature earns its place only if it
answers **yes** to at least one:

1. **Does it stop money leaking?** (shrinkage, unrung sales, uncollected debt, dead capital)
2. **Does it replace a physical book they already keep?** (deni book, rent book, bale book,
   appointment diary) — adoption in this market is won by replacing paper, not adding process.
3. **Does it let the owner be absent?** (remote visibility, exception alerts)
4. **Does it bring in a shilling they would not otherwise get?** (online orders, rebooking
   nudges, markdown of dead stock, credit recovery)

Anything that is merely "good practice from a Western retail textbook" — perpetual inventory
across 2,000 SKUs, purchase requisition workflows, cost-centre accounting — **fails** and must
not be built. This is the discipline that keeps the app from getting bigger again.


---
# PART B — FOUNDATION SPRINTS
*These pay off for Bar and Kibanda immediately. Do them before any new business type.*

---

## 4. SPRINT M0 — Capability refactor (no new user-facing features)

**Goal:** make the app switch itself off. Nothing new appears; a lot disappears for people who
never needed it.

### Why first
Every sprint after this one either doubles in size or halves in size depending on whether M0
exists. Building Retail before M0 means Retail gets its own vertical stack and you are back
where you started.

### Work

**M0-1. Extend `core/business_profiles.py`** — VERIFY-ME: open the file, list the existing 8
profile keys and their catalogs, and preserve them verbatim. Add the `Capability` dataclass from
§2.4 and attach one to each existing profile. Bar, kibanda and kitchen capabilities must be
derived from what those modules *actually do today* — this sprint must not change their
behaviour by a single pixel.

**M0-2. `Item.stock_model`** CharField + data migration + `Item.save()` sync (§2.1). No reader
changes yet.

**M0-3. Item form field gating.** `item_form.html` currently shows every field to everybody.
Wrap each field group in `{% if 'expiry' in caps.shows %}`-style guards driven by
`biz_profile.capability.hides`. Salon owners stop seeing yield factor; kibanda owners stop
seeing spirits accountability.
*Reminder: `item_form.html` has NO jQuery/Select2 — any new dynamic behaviour is vanilla JS.*

**M0-4. Vocabulary layer.** `{% trans %}` handles language; it does not handle *industry*.
A `vocab` template filter reading `biz_profile.capability.vocabulary` so the same template
renders "Bidhaa" for a duka, "Huduma" for a salon, "Nyumba" for a landlord.
```django
{{ 'item'|vocab }}          {# → Bidhaa / Huduma / Nyumba / Mzigo #}
```

**M0-5. Dashboard tile registry.** Home dashboard tiles become a list built in the view from
capabilities, not a hardcoded template block. Each tile: `{key, title, value, url, capability}`.
Tiles whose capability is absent are never computed (saves queries too).

**M0-6. Analytics section registry.** Same pattern for `analytics_views.py`. "Kibanda Produce
Performance" only computes for profiles with `ENVELOPE`; "Bar Performance" only for `MEASURE`+bar.
*This also fixes the existing bleed risk called out in CLAUDE.md.*

**M0-7. `core/accountability.py`** — the §2.3 contract, wrapping and re-exporting the existing
`keg_metrics.py` functions so no bar code changes. New variance types register into it.

### Cause-&-Effect Map — M0

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | No | Unchanged |
| Receipts | No | Unchanged |
| SMS/notifications | No | Unchanged |
| Analytics sections | **Yes** | Now registry-driven; must produce byte-identical output for bar/kibanda/kitchen |
| Home dashboard tiles | **Yes** | Registry-driven; same tiles for existing profiles |
| Item form | **Yes** | Fields gated; existing profiles must see exactly what they see today |
| Navbar | **Yes** | Driven by `capability.modules` instead of `business_type` checks |
| Revenue targets | No | Unchanged |
| Expiry alerts | **Yes** | Gated on `LOT` capability — bar/kibanda keep it if they have it today |
| Shift open/close | No | Unchanged |
| Access gate (view + URL) | No | Unchanged |
| M-Pesa routing | No | Unchanged |
| Haki ledger | No | Unchanged |
| **Inverse action** | n/a | Refactor only |

### Acceptance criteria
- **M0-AC1:** Log in as a bar owner, a kibanda owner and a kitchen staffer. Every screen is
  pixel-identical to pre-M0. This is a pure refactor and any visible change is a bug.
- **M0-AC2:** `python manage.py test` — all existing tests pass, count unchanged or higher.
- **M0-AC3:** A new profile can be added by editing only `business_profiles.py` — prove it by
  adding a stub `salon` profile that renders a working (empty) navbar and dashboard with no
  other file changed.
- **M0-AC4:** Grep confirms no remaining `business_type ==` string comparisons in templates.

---

## 5. SPRINT M1–M3 — Multi-store / multi-outlet

> *"Can it help me manage multiple stores that I have without me being physically there?"*

This is the most commercially valuable ask in your list. It applies to bar, kibanda and every
new type. It is also the ask that turns a KES 500/month single-duka customer into a KES
3,000/month chain customer.

Today `Store` exists and K2a already gave it M-Pesa overrides — so it is already becoming a
first-class outlet. Three things are missing: **staff scoping to stores, stock transfer between
stores, and an owner console that aggregates them.**

### 5.1 SPRINT M1 — Store as first-class outlet

```python
# core/models.py — Store additions (migration 00XX)
store_type   = CharField(max_length=20, default='retail', choices=[
                 ('bar','Bar'), ('kitchen','Jiko / Kitchen'), ('retail','Duka / Retail floor'),
                 ('produce','Kibanda'), ('salon','Salon'), ('rental','Rentals'),
                 ('warehouse','Godown / Store'), ('other','Nyingine')])
code         = CharField(max_length=12, blank=True)   # 'KHY01' — used on receipts, transfers, Paybill account
is_outlet    = BooleanField(default=True)             # False = godown: holds stock, cannot sell
manager      = FK('accounts.UserProfile', null=True, blank=True, related_name='managed_stores')
is_active    = BooleanField(default=True)
opening_time = TimeField(null=True); closing_time = TimeField(null=True)
target_daily_revenue = DecimalField(null=True)
phone        = CharField(blank=True)                  # store line for SMS + storefront
address_note = CharField(blank=True)
latitude / longitude = DecimalField(null=True)        # for owner map + rider dispatch
```

**Discriminator consistency — critical.** `Store.is_kitchen` is load-bearing across K1's
`_debt_scope()`, kitchen views, receipts, analytics. **Do not remove it.** Keep it synced:
```python
# Store.save()
if self.store_type == 'kitchen':
    self.is_kitchen = True
elif self.is_kitchen and self.store_type != 'kitchen':
    self.is_kitchen = False
```
Run the regression sweep: `grep -rn "is_kitchen" .` and confirm every hit still behaves.

**Staff scoping:**
```python
# accounts/models.py — UserProfile additions
home_store = FK('core.Store', null=True, blank=True, related_name='home_staff')
stores     = M2M('core.Store', blank=True, related_name='assigned_staff')
# empty stores M2M + role='owner'  → all stores
# empty stores M2M + role='staff'  → home_store only (back-compat for every existing staffer)

def accessible_stores(self):
    if self.role == 'owner': return Store.objects.filter(business=self.business, is_active=True)
    qs = self.stores.filter(is_active=True)
    if qs.exists(): return qs
    return Store.objects.filter(pk=self.home_store_id) if self.home_store_id else Store.objects.none()
```

**One gate, used on the view AND the URL** (Cause-&-Effect protocol dimension 2):
```python
# core/access.py
def require_store_access(profile, store):
    if store is None: return
    if not profile.accessible_stores().filter(pk=store.pk).exists():
        raise PermissionDenied("Huna ruhusa ya duka hili.")
```
Apply in: every board POST, `add_transaction`, stock list filters, receipts list, shift open,
debt views, analytics. Existing `_debt_scope()` and `can_access_kitchen` logic stays and
composes with this — store access is an *additional* gate, never a replacement.

**Active store switcher.** Staff with >1 accessible store get a navbar dropdown; selection
stored in session (`request.session['active_store_id']`) and read by a context processor into
`active_store`. All boards, stock lists and receipts scope to it. Owner gets an extra "Zote /
All stores" option that only aggregate/read-only screens honour — **never** POS screens
(a sale must always belong to exactly one store).

### 5.2 SPRINT M2 — Stock transfer between stores

The single biggest shrinkage hiding place in a multi-outlet business is **goods in transit**.
"Nilipeleka Kahawa branch" with no receipt on the other end is how stock evaporates.

```python
class StockTransfer(models.Model):
    business = FK(Business); reference = CharField(unique per business)   # TRF-0001, gap-free
    from_store = FK(Store, related_name='transfers_out')
    to_store   = FK(Store, related_name='transfers_in')
    status = CharField(choices=[('DRAFT','Draft'),('DISPATCHED','Imetumwa'),
                                ('RECEIVED','Imepokelewa'),('DISPUTED','Ina utata'),
                                ('CANCELLED','Imefutwa')], default='DRAFT')
    dispatched_by = FK(UserProfile, null=True); dispatched_at = DateTimeField(null=True)
    received_by   = FK(UserProfile, null=True); received_at   = DateTimeField(null=True)
    rider = FK(UserProfile, null=True, blank=True)
    note = TextField(blank=True)

class StockTransferLine(models.Model):
    transfer = FK(StockTransfer, related_name='lines')
    item = FK(Item); qty_sent = Decimal; qty_received = Decimal(null=True)
    variance_note = CharField(blank=True)
```

**Mechanics:**
- DISPATCH creates an `Issue` transaction at `from_store` **and** places the quantity into an
  in-transit state. In-transit stock must count as **neither** store's available balance.
  Simplest correct implementation given the existing balance logic: create the `Issue` at
  dispatch and the `Receipt` at receive; the gap between them is the in-transit window, and
  the transfer record itself is the audit trail. Add `Transaction.transfer` FK so both legs
  are linked and can be excluded from sales analytics.
- **`Transaction.transfer_id IS NOT NULL` must be excluded from revenue everywhere.** Regression
  sweep: every place that sums `Issue` transactions as sales. This is a money-path change —
  add tests.
- RECEIVE requires the receiving staffer to enter counted quantities. `qty_received <
  qty_sent` → status DISPUTED, auto-`Notification` + SMS to owner, and a `StaffShrinkage`
  attribution against the dispatching shift (not the receiver — the receiver is the whistle).
- Transfers to/from a `warehouse` store (is_outlet=False) are the godown replenishment flow.

**Inverse actions** (protocol dimension 1): DISPATCH ↔ CANCEL (only while DRAFT/DISPATCHED and
only by the dispatcher or owner) · DISPUTED ↔ RESOLVE (owner writes off to Wastage or corrects).

### 5.3 SPRINT M3 — Owner console: "Maduka Yangu"

Route `/maduka/` (owner only). This is the "manage without being there" screen.

**Layout (mobile-first — Roy works from a phone, and so will owners):**
1. **Day strip** — today's total revenue across all stores vs combined target, cash expected to
   be banked, open tabs KES, credit issued today.
2. **Per-store cards** — one card per outlet, sorted by *problem first, not alphabetically*:
   - store name + code, open/closed, who is on shift right now
   - today revenue vs store target (existing `_build_target_data` colours)
   - variance flag chip (from `core/accountability.py`) — green ✓ / amber / raspberry ▲
   - cash expected vs counted (Z-report) if the shift has closed
   - unread exceptions count
3. **Exception feed** — the actual product. Only things that are *wrong*, newest first:
   `shrinkage above baseline` · `shift closed with cash variance > X` · `transfer disputed` ·
   `sale below cost price` · `discount above N%` · `credit issued to blocked customer` ·
   `no sales for 90 minutes during opening hours` · `stock count variance` · `till not counted`.
   Each row: store · staff · KES impact · time · one-tap drill-through.
4. **Compare view** — stores side by side on one metric over a date range (revenue, margin %,
   shrinkage KES, credit outstanding, top item). Chart.js.

**Daily digest** — at each store's `closing_time + 30min`, one SMS to the owner:
`Duka Zangu 02/08: KHY01 12,400 (✓) | KHY02 8,900 (▲ upungufu 1,200) | Deni mpya 3,400. Angalia: dukamwecheche.co.ke/maduka/`
Reuse `NotificationRouter`. Respect the 10-min SMS bundling window. One digest per day, never
per-store spam — SMS costs are real money.

**Exception model:**
```python
class BusinessException(models.Model):
    business = FK(Business); store = FK(Store, null=True); shift = FK(Shift, null=True)
    staff = FK(UserProfile, null=True)
    kind = CharField(...)          # 'shrinkage'|'cash_variance'|'transfer_dispute'|'below_cost'|...
    severity = CharField(choices=[('info',...),('warn',...),('danger',...)])
    amount_kes = Decimal(null=True)
    title = CharField(); detail = TextField(blank=True)
    link_url = CharField(blank=True)
    created_at; acknowledged_by = FK(UserProfile, null=True); acknowledged_at = DateTimeField(null=True)
```
Every accountability check in the app writes here instead of inventing its own alert. This is
also the backbone of the storefront-era notification system later.

### Cause-&-Effect Map — M1/M2/M3

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | Debt must be attributable to a store. Add `store` FK to the credit transaction path; `_debt_scope()` gains a store dimension. Owner sees all; staff sees their store's ledger only |
| Receipts | **Yes** | `Receipt.store` FK; receipt shows store name + code; receipts list filters by store; staff see own store only |
| SMS/notifications | **Yes** | Daily digest; transfer dispute alert; store name must appear in every alert or the owner cannot act |
| Analytics | **Yes** | Every section gains a store filter; "All stores" aggregate; transfers excluded from revenue |
| Home dashboard | **Yes** | Staff dashboard scoped to active store; owner gets Maduka Yangu entry point |
| Revenue targets | **Yes** | Per-store targets + business rollup |
| Expiry alerts | **Yes** | Scoped per store — a manager must not chase another branch's milk |
| Tabs → debt | **Yes** | Tab belongs to a store; conversion keeps the store |
| Shift open/close | **Yes** | Shift gains `store` FK (VERIFY-ME: Sprint 21 made shifts per-staff; confirm whether store is already implied). Z-report becomes per-store |
| Navbar | **Yes** | Store switcher; Maduka Yangu (owner only) |
| Access gate view+URL | **Yes** | `require_store_access` everywhere |
| M-Pesa routing | **Yes** | Already store-aware via K2a `resolve_mpesa_config(business, store)` — extend to all new POS surfaces |
| Haki ledger | **Yes** | Staff contribution attributed per store; a staffer moved between branches keeps one continuous record |
| **Inverse action** | **Yes** | Transfer: dispatch↔cancel, dispute↔resolve. Store: activate↔deactivate (deactivating must block new sales but preserve history) |

### Acceptance criteria
- **M1-AC1:** A staffer assigned to Store A who manipulates the URL to hit Store B's board,
  stock list, receipts, debt profile or shift gets `PermissionDenied` — tested for each URL.
- **M1-AC2:** An existing single-store business sees zero change (no switcher, no Maduka link).
- **M2-AC1:** Transfer of 20 units dispatched, 18 received → both stores' balances correct,
  a DISPUTED record exists, owner got exactly one notification, and neither leg appears as
  revenue in analytics or in revenue targets.
- **M2-AC2:** Transfer reference numbers are gap-free per business (same guarantee as Receipt).
- **M3-AC1:** Exception feed shows a shrinkage event within one page load of a shift closing
  with variance beyond baseline.
- **M3-AC2:** Daily digest SMS fires once per day per owner, contains every active store, and
  is suppressed entirely if the business had no sales (do not pay for a "0" SMS).

---

## 6. SPRINT P0 — Partial payments (fixes Kibanda) + payment plans

Two different things are both called "partial payment". Build both; ship (a) first because it
is the Kibanda gap you named.

### 6.1 P0-A — Split tender at checkout *(the Kibanda fix)*

**The reality:** a customer buys mboga for KES 100, hands over KES 60 and says "ile 40 nitakuletea
jioni". Today the only options are full cash or full deni. So staff either ring 100 cash (and
carry the 40 personally — a shrinkage vector) or ring 100 credit (and lose the 60 from the
day's cash reconciliation). Both corrupt the numbers.

**Design:** one sale, multiple tenders.
```python
class SalePayment(models.Model):
    """A tender line against a single checkout. Sum(amount) == cart total."""
    business = FK(Business); store = FK(Store, null=True)
    receipt  = FK(Receipt, null=True, related_name='tenders')
    method   = CharField(choices=[('cash',...),('mpesa',...),('credit',...)])
    amount   = Decimal
    mpesa_ref = CharField(blank=True)
    recorded_by = FK(UserProfile); created_at
```
**Do not change `Transaction.payment_method`.** It is load-bearing in the debt tracker, the
credit policy gate, analytics, void logic and Z-report. Instead:
- The credit portion still creates the credit `Transaction`s the debt tracker already expects
  (`payment_method='credit'`, `recipient=name`) so **no debt code changes at all**.
- The cash/M-Pesa portion is recorded as tender lines and reflected in the drawer/Z-report.
- Where a single sale is split, split the transaction lines by value so each `Transaction` has
  exactly one payment_method — cheapest correct approach given how much reads that field.
  Rule: fill cash/M-Pesa first (FIFO by line), remainder to credit.

**UX (Quick Sell + produce board + kitchen board + bar board — all four):**
Checkout sheet gains a "Lipa kidogo" toggle. Numeric pad: `Amelipa: [ 60 ]`, live
`Deni: KES 40` in raspberry. Payment method chips for the paid part. Confirm.
Copy: *"Amelipa KES 60. Deni KES 40 itaenda kwa deni ya {{customer}}."*
Requires customer name/phone when a credit remainder exists — reuse the existing Quick Sell
credit flow and **the K3 `evaluate_credit()` gate must run on the remainder**, not be bypassed.

### 6.2 P0-B — Payment plans (layaway / deposit / instalments)

Needed by Apparel (lipa pole pole), Rentals (deposit), Hardware, Phone shops.

```python
class PaymentPlan(models.Model):
    business = FK; store = FK(null=True); customer = FK(Customer)
    kind = CharField(choices=[('LAYAWAY','Lipa pole pole'), ('DEPOSIT','Amana'),
                              ('INSTALMENT','Malipo ya awamu'), ('BOOKING','Booking')])
    total_amount = Decimal; paid_amount = Decimal(default=0)
    due_date = DateField(null=True)
    hold_expires_on = DateField(null=True)      # layaway: goods held until this date
    status = CharField(choices=[('OPEN',...),('COMPLETED',...),('FORFEITED',...),
                                ('REFUNDED',...),('CANCELLED',...)], default='OPEN')
    forfeit_policy = CharField(blank=True)      # human-readable, shown on receipt
    reserved_item = FK(Item, null=True); reserved_qty = Decimal(null=True)
    rental_unit = FK('RentalUnit', null=True)
    appointment = FK('Appointment', null=True)
    created_by = FK(UserProfile); created_at; closed_at

    @property
    def balance(self): return self.total_amount - self.paid_amount

class PaymentPlanEntry(models.Model):
    plan = FK(PaymentPlan, related_name='entries')
    amount = Decimal; method = CharField(...); mpesa_ref = CharField(blank=True)
    recorded_by = FK(UserProfile); created_at; receipt = FK(Receipt, null=True)
```

**Reserved stock is not available stock.** A layaway holds physical goods. Balance readers must
subtract open reservations, or two customers get sold the same dress. Add
`Item.reserved_qty()` helper and sweep every balance display surface (the CLAUDE.md
`grep -rn "current_balance\|\.balance" templates/` rule applies in full here).

**Inverse actions:** pay ↔ refund · hold ↔ release/forfeit (with the policy printed on the
receipt at the time the plan opened — never retro-applied) · plan ↔ convert to sale on
completion (creates the actual Issue transactions + final Receipt).

**Ethics note, consistent with Haki:** forfeiture copy must be honest and stated up front, in
Swahili, on the deposit receipt. *"Ukikosa kumaliza kufikia {{date}}, amana yako
haitarudishwa"* — printed at the moment money is taken, not discovered later. If Roy prefers a
softer default, make `forfeit_policy` a business setting with a "refund minus 10%" option;
do not hardcode a punitive default.

### Cause-&-Effect Map — P0

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | Credit remainder flows in via the existing path — verify aging, FIFO and score are unaffected. Plans are NOT debt and must not appear in the deni ledger (separate section) |
| Receipts | **Yes** | Split tender shows every tender line + "Bado unalipa KES X" (the K4 block already exists — reuse). Plan receipts show paid/balance/due date/forfeit policy |
| SMS | **Yes** | Credit remainder → existing debt SMS. Plan payment → confirmation SMS with running balance. Layaway hold expiring in 3 days → reminder |
| Analytics | **Yes** | Revenue recognised at sale for split tender; for plans, recognised on completion (deposits are a **liability**, not revenue — getting this wrong overstates the owner's profit) |
| Home dashboard | **Yes** | "Amana zilizoshikiliwa" tile (money held that is not yours yet) |
| Revenue targets | **Yes** | Split tender counts in full; plan deposits do not count until completion |
| Expiry alerts | No | — |
| Tabs → debt | **Yes** | Tab settlement gains split tender |
| Shift / Z-report | **Yes** | Drawer must reconcile tender lines, not `Transaction.payment_method` alone |
| Credit gate (K3) | **Yes** | `evaluate_credit()` runs on the credit remainder |
| M-Pesa routing | **Yes** | Partial M-Pesa tender uses `resolve_mpesa_config(business, store)` |
| **Inverse action** | **Yes** | Refund, release, forfeit, cancel — all four must exist before this ships |

### Acceptance criteria
- **P0-AC1:** Kibanda sale of 100 with 60 cash → Z-report cash expected +60, debt ledger +40,
  one receipt showing both, customer SMS states 40 outstanding.
- **P0-AC2:** Sum of tender lines always equals the receipt total. Add a test that fails loudly
  if it ever does not.
- **P0-AC3:** A layaway reservation reduces available stock on every surface listed by the
  `grep` sweep.
- **P0-AC4:** Deposits do not appear in revenue, revenue targets, or profit until completion.
- **P0-AC5:** Split tender works identically on Quick Sell, produce board, kitchen board and
  bar board.


---
# PART C — BUSINESS TYPE DESIGNS

---

## 7. Minimart / General retail / Duka  — SPRINTS R1–R4

> *"Can it work for my supermarket / minimart / general shop?"*
> This is your largest addressable market and the one where the current app is closest to
> usable already. It is also the one where a naive implementation fails hardest.

### 7.1 How it actually runs (and why textbook inventory fails)

- **200–2,000 SKUs**, most of them low-margin fast movers. Nobody is going to enter opening
  stock for 2,000 items on a phone. **Onboarding must not require a full stock take.**
- **Barcodes are partly present.** Branded FMCG (Blue Band, Omo, Coca-Cola) carry EAN-13.
  Loose goods (sugar, rice, unga scooped, eggs, bread from a local bakery) do not.
- **Pricing lives in the owner's head** and drifts. The distributor raises cost by KES 8 and the
  shop keeps selling at the old price for three weeks. That is the single biggest silent
  profit leak in Kenyan retail, and it is completely invisible without software.
- **Credit ("deni book") is enormous** — often 20–40% of monthly turnover, recorded in an
  exercise book, and 15% of it is never collected. You already have the best debt module in
  this market; retail is where it earns its keep.
- **Mzigo arrives on supplier credit.** The shop owes the distributor. You currently track
  receivables and not payables — half the cash picture is missing (§14).
- **Shrinkage at the counter**: sweethearting (friend gets a "discount"), unrung sales, till
  skim, and slow leakage of high-value small items (razors, batteries, airtime, phone cases).
- **Dead stock** ties up capital: the shop has KES 40,000 sitting in goods that last sold in
  March, and no idea.

### 7.2 SPRINT R1 — Fast onboarding + barcode + the shared product catalog

**This is the feature that wins the retail market, and only you can build it.**

Every duka that scans a barcode teaches the platform what that product is. The 500th duka to
onboard has almost nothing to type. No competitor with one customer can do this; you get it
free from multi-tenancy — the very thing you thought was making the app "too large."

```python
class GlobalProduct(models.Model):
    """Cross-tenant product dictionary. NAMES AND PACK SIZES ONLY — never prices per business."""
    barcode = CharField(max_length=32, unique=True, db_index=True)
    name = CharField(max_length=160)
    brand = CharField(max_length=80, blank=True)
    pack_size = CharField(max_length=40, blank=True)     # '250g', '500ml', '2kg'
    unit = CharField(max_length=20, blank=True)
    category = CharField(max_length=60, blank=True)
    image_url = URLField(blank=True)
    contributed_by = FK(Business, null=True, on_delete=SET_NULL)
    confirm_count = PositiveIntegerField(default=1)      # how many businesses agree on the name
    is_verified = BooleanField(default=False)            # confirm_count >= 3
    created_at; updated_at

class MarketPriceIndex(models.Model):
    """Anonymised, aggregated cost/price benchmark. NEVER exposes a single business."""
    global_product = FK(GlobalProduct); county = FK('core.County', null=True)
    median_cost = Decimal(null=True); median_price = Decimal(null=True)
    sample_size = PositiveIntegerField(default=0)        # suppressed entirely below 5
    updated_at
```

**Privacy and consent — treat this as seriously as the M-Pesa/CBK boundary.** A shop's buying
price is competitively sensitive. Rules, non-negotiable:
- `Business.contribute_market_data = BooleanField(default=True)` with a plain-Swahili
  explanation at onboarding and a one-tap opt-out in settings.
- Only **names, barcodes, pack sizes, categories** are shared as identifiable product data.
- Prices are shared **only** as a county-level median with `sample_size >= 5`. Below that
  threshold, return nothing at all — no "based on 2 shops".
- No business can ever query another named business's prices. There is no API surface for it.
- Opting out means the shop stops contributing **and** loses the benchmark — state that
  honestly rather than quietly degrading their experience.

**Scan flow (mobile-first):**
1. Owner taps ➕ Ongeza Bidhaa → camera opens (`html5-qrcode` or the BarcodeDetector API where
   available; VERIFY-ME whether any scanner lib is already vendored).
2. Barcode hit in `GlobalProduct` → name/brand/pack pre-filled. Owner enters **selling price
   and quantity only**. Two taps to a stocked item.
3. Miss → owner types the name once; a new `GlobalProduct` row is created and every future duka
   benefits.
4. No barcode (loose goods) → normal item form, `stock_model=UNIT` or `MEASURE`.

**Onboarding without a stock take — "Anza bila kuhesabu":**
Do not demand opening balances. Let the shop start selling immediately with unknown balances,
and build the inventory *as it sells*: the first time an item is sold, prompt for its
approximate current stock ("Bado unayo ngapi?"), or let it run negative-tolerant and settle at
the first cycle count. Set `Item.balance_confirmed_at`; items never confirmed are excluded
from shrinkage attribution (they would generate false accusations) and shown in a "Bidhaa
ambazo hazijahesabiwa" list. **`coverage_pct` in the accountability engine already exists to
express exactly this honestly.**

### 7.3 SPRINT R2 — Retail POS board + margin guard

**`retail_board.html`** — a new POS surface, reused later by Apparel, Pharmacy, Hardware,
Phone. Design principles from what already works in `bar_board`/`quick_sell`:
- Search-first, not grid-first (200+ SKUs makes tiles useless). Big input at the top; matches
  by name, barcode or material number as you type; scanner button beside it.
- **Favourites strip** — the 12 fastest movers as tiles, auto-computed from 30-day velocity.
  This covers ~60% of transactions in a typical duka.
- Cart panel identical in behaviour to Quick Sell (Add stays open, Done closes, ↩ Futa undo).
- Checkout: cash · M-Pesa (QR/STK via `resolve_mpesa_config`) · deni · **split tender (P0-A)**.
- Manual price override requires a reason and is logged as a `BusinessException` when the
  discount exceeds a business-set threshold — this is the sweethearting control.

**Margin guard** — the silent-profit-leak fix:
```python
# on every Receipt transaction where cost_price is entered
if previous_cost and new_cost > previous_cost * (1 + business.margin_alert_pct/100):
    suggested = new_cost * (old_price / previous_cost)     # preserve the old margin ratio
    → BusinessException(kind='cost_rise', severity='warn', amount_kes=...)
    → in-app + SMS: "Bei ya {{item}} imepanda kutoka {{old}} hadi {{new}}.
                     Ukiendelea kuuza kwa {{price}}, faida yako imepungua hadi {{pct}}%.
                     Pendekezo: uza kwa {{suggested}}."
```
One-tap "Sasisha bei" applies the suggestion. This single feature pays for the subscription and
is trivially demonstrable in a sales conversation.

Also surface at checkout: refuse-or-warn on **selling below cost** (`sale_below_cost`
exception), which catches both mistakes and deliberate sweethearting.

### 7.4 SPRINT R3 — Cycle counting (ABC) + retail shrinkage

A full stock take is impossible; a partial one is easy. Classify by revenue contribution and
count a small rotating subset every day.

```python
class StockCountSession(models.Model):
    business = FK; store = FK(Store)
    kind = CharField(choices=[('CYCLE','Hesabu ya kila siku'),('FULL','Hesabu kamili'),
                              ('SPOT','Hesabu ya ghafla')])
    scope_note = CharField(blank=True)     # 'Class A — 12 items'
    started_by = FK(UserProfile); started_at; closed_at = DateTimeField(null=True)
    status = CharField(choices=[('OPEN',...),('CLOSED',...)], default='OPEN')
    shift = FK('Shift', null=True)

class StockCountLine(models.Model):
    session = FK(StockCountSession, related_name='lines')
    item = FK(Item); variant = FK('ItemVariant', null=True)
    book_qty = Decimal            # snapshot at count time — never recomputed later
    counted_qty = Decimal(null=True)
    variance_qty = Decimal(null=True); variance_kes = Decimal(null=True)
    reason = CharField(blank=True)     # 'imeharibika' | 'imeibiwa' | 'kosa la kuandika' | ...
    counted_by = FK(UserProfile, null=True)
```

**ABC classification** — nightly or on-demand: rank items by 90-day revenue; top 80% of value
= A (count weekly), next 15% = B (monthly), rest = C (quarterly). Store as
`Item.abc_class`. The daily count list is then "today's 10 items" — a two-minute job, not a
Sunday-closing job.

**Attribution:** variance from a CYCLE session lands on the shift(s) covering the period since
that item's last count, weighted by hours worked — never on whoever happened to be counting.
Feed `StaffShrinkage` and the existing leaderboard, honouring `coverage_pct` and the learned
baseline (`Still learning N/3`) so nobody is accused on the first count.

**High-risk watchlist:** items with high value × small size (razors, batteries, phone
accessories, airtime scratch cards, condoms, spirits miniatures) get `Item.is_high_risk` and
are force-included in every cycle count regardless of ABC class.

### 7.5 SPRINT R4 — Retail intelligence (dead stock, reorder, basket)

- **Dead stock report** — items with zero movement in N days and non-zero balance, sorted by
  capital tied up. Copy: *"KES 41,200 imelala kwenye bidhaa 23 ambazo hazijauzwa tangu Mei."*
  Actions: markdown suggestion, transfer to another branch (M2!), bundle, return to supplier.
- **Reorder suggestions** — you already have ETS/Holt-Winters forecasting; wire it to
  `reorder_level`/`reorder_quantity` plus supplier lead time to produce a
  **"Order ya leo"** list, one tap to a draft PO (§14) or a pre-filled WhatsApp/SMS message to
  the distributor. Most dukas order by phone — meet them there rather than forcing a portal.
- **Basket affinity (light)** — "watu wanaonunua unga pia hununua sukari" → shelf placement and
  bundle suggestions. Keep it to a top-10 pairs table; do not build a recommender.
- **Hour-of-day heatmap** per store — staffing and closing-time decisions.

### Cause-&-Effect Map — R1–R4

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | Retail deni is the main use; ensure `retail_board` credit path sets `recipient` + Customer + `evaluate_credit()` gate, identical to Quick Sell |
| Receipts | **Yes** | Retail sale issues a Receipt; barcode/GlobalProduct name appears on lines; store code on header |
| SMS | **Yes** | Credit SMS (existing) · cost-rise alert · dead-stock weekly digest (opt-in, one per week max) |
| Analytics | **Yes** | New "Retail Performance" section (margin %, ABC mix, dead stock KES, shrinkage KES). Must NOT bleed into Kibanda or Bar sections |
| Home dashboard | **Yes** | Tiles: today revenue, deni outstanding, items below reorder, cost-rise alerts, uncounted items |
| Revenue targets | **Yes** | Retail sales count normally |
| Expiry alerts | **Yes** | Retail composes `LOT` — milk/bread/yoghurt. Existing expiry module applies directly |
| Tabs → debt | **Yes** | Retail has no tabs; ensure the tab UI is hidden by capability, not left dangling |
| Shift open/close | **Yes** | Retail shifts + cash drawer Z-report (reuse F4 `bar_z_report` generalised per store) |
| Navbar | **Yes** | Retail Board · Hesabu (counts) · Order ya Leo · Deni · Ripoti |
| Access gate | **Yes** | `require_store_access` on board, counts, and every report |
| M-Pesa routing | **Yes** | Per-store via K2a resolver |
| Haki ledger | **Yes** | Counter staff contribution = sales rung, counts completed, deni recovered — feeds Kazi Yangu |
| **Inverse action** | **Yes** | Sale ↔ return/refund (**retail needs returns — bar and kibanda did not**), count session open ↔ close, markdown ↔ revert, cost update ↔ history |

**Returns/refunds are a genuinely new primitive that retail forces.** Build
`Transaction.type='Return'` (positive stock back, negative revenue, linked to original
receipt token, reason required, owner-approval threshold). Sweep every revenue sum to exclude
or negate it correctly. Do not skip this — a shop that cannot process a return will not adopt.

### Acceptance criteria
- **R1-AC1:** Scanning a known barcode pre-fills name/brand/pack; entering price + qty creates
  a stocked item in ≤ 3 taps.
- **R1-AC2:** A business with `contribute_market_data=False` neither writes to `GlobalProduct`
  price aggregation nor sees benchmarks. `MarketPriceIndex` returns nothing below sample_size 5.
- **R2-AC1:** Cost rise beyond threshold generates exactly one exception + one SMS (bundled),
  and the one-tap price update writes a price-history row.
- **R2-AC2:** Selling below cost is blocked for staff and warned for owner, and logged.
- **R3-AC1:** A cycle count of 10 items produces per-item variance in KES, attributes to the
  correct shift(s), and marks items with `balance_confirmed_at IS NULL` as excluded with a
  visible reason.
- **R4-AC1:** Dead stock report sorts by capital tied up and offers transfer-to-branch when the
  business has >1 store.
- **R-AC-RET:** A return reverses stock, revenue, revenue-target contribution and, if the sale
  was credit, the debt ledger — verified by test.

---

## 8. Clothing / Apparel / Boutique / Mitumba — SPRINTS A1–A3

> *"Can it work for my clothline shop?"*
> Two sub-models under one profile, and one of them is already 80% built.

### 8.1 How it actually runs

**Sub-model 1 — Boutique / new stock.** One design in several sizes and colours. Stock is
shallow (2–3 pieces per size), turnover is slow, margin is high, and the killer problem is
**dead stock**: money sitting in a size 42 nobody wants. Layaway ("lipa pole pole") is
standard. Photos sell the goods — this is the type where the online storefront matters most.

**Sub-model 2 — Mitumba / bale.** Buys a bale for KES 8,000–25,000 containing an unknown mix of
unsorted pieces graded 1st/2nd/3rd. Sells by price point: 1,000 · 500 · 300 · 100 bob. **She
never counts the pieces.** She knows the bale cost and roughly what it must bring in.

**That second model is `ProduceBunch` exactly.** Cost in, target revenue, deplete by price
point, close when done, realized markup, wastage on the unsellable remainder. You already
built it, tested it, and shipped it. Apparel gets it for the cost of a rename and a re-skin.

### 8.2 SPRINT A1 — Variants (the boutique half)

**Recommended implementation: parent/child Items, not a new variant table.**

```python
# core/models.py — Item additions
parent = FK('self', null=True, blank=True, related_name='variants')
variant_label = CharField(max_length=60, blank=True)     # 'M / Navy'
variant_attrs = JSONField(default=dict, blank=True)      # {'size':'M','colour':'Navy'}
is_variant_parent = BooleanField(default=False)
```

Rationale, and it matters: a separate `ItemVariant` FK on `Transaction` would require auditing
**every** balance reader, analytics query, receipt line, stock list, reorder table, Quick Sell
tile, debt line and shrinkage calculation in the codebase — the exact class of change that has
burned this project repeatedly. Parent/child Items reuse 100% of existing machinery: each
variant *is* an Item with its own balance, cost, price, barcode and history. The parent is a
display grouping only and is never sold directly.

Cost: stock list gets noisy (one row per variant). Fix in the UI, not the schema — collapse
children under the parent with a summary row (total balance, price range, size chips) and
expand on tap.

**Variant matrix creator** — the screen that makes this bearable: enter product name, tick
sizes (S M L XL / 28–44), tick colours, set a base price, tap Create → N child Items generated
with auto SKUs (`SHRT-NVY-M`). Bulk quantity entry in a grid. Vanilla JS on `item_form.html`
(no Select2 there — CLAUDE.md).

### 8.3 SPRINT A2 — Bale envelope (the mitumba half)

Generalise the envelope model rather than duplicating it:
```python
# core/models.py — ProduceBunch additions (keep the model name to avoid breaking
# produce_bunch_id, which CLAUDE.md flags as THE discriminator)
kind = CharField(max_length=16, default='produce',
                 choices=[('produce','Mboga/Matunda'), ('bale','Bale ya mitumba'),
                          ('carcass','Nyama'), ('sack','Gunia')])
grade = CharField(max_length=12, blank=True)     # '1st'|'2nd'|'3rd' — mitumba grading
label = CharField(max_length=60, blank=True)     # 'Bale ya jeans — Gikomba 28/07'
```
- Analytics section title and board vocabulary switch on `kind` + profile vocabulary
  (`"Bale Performance"` for apparel, `"Kibanda Produce Performance"` for kibanda) — this is
  exactly the bleed the Cause-&-Effect protocol warns about, so gate it on capability.
- Price-point presets become 1000/500/300/100 via `ItemPortionPreset` (already built).
- `realized_markup()`, `is_wilting()` → rename the *label* per profile: for a bale, "wilting"
  becomes **"Bale ya zamani — imekaa siku N"** (slow-moving), same maths.
- `sell_mix()` spreads a sale across open bales — already built, works unchanged.

### 8.4 SPRINT A3 — Layaway, fitting room, markdown, photos

- **Layaway** — `PaymentPlan(kind='LAYAWAY')` from P0-B. Reserved variant, hold expiry, SMS
  reminders at T-7 and T-2 days, honest forfeit copy on the deposit receipt.
- **Fitting-room log** (optional, high-shrinkage shops): pieces out → pieces back. Lightweight:
  a counter per staff per shift. Variance feeds `StaffShrinkage`.
- **Aging & markdown engine** — the boutique's real profit lever:
  `days_since_last_sale` per variant → buckets (0-30 / 31-60 / 61-90 / 90+). Suggested markdown
  ladder (10% → 25% → 40%) with capital-recovery framing:
  *"Suruali 6 zimekaa siku 94. Umewekeza KES 9,600. Punguza hadi KES 1,200 urudishe pesa yako."*
  One tap applies the markdown, writes price history, and (if the storefront is live) flags the
  item as **Ofa** online.
- **Photos** — `ItemPhoto` (item FK, image, display_order, is_primary). ⚠️ **Render's free tier
  has an ephemeral filesystem — uploaded images will vanish on redeploy.** Photos require
  external storage (Cloudinary free tier is the pragmatic choice in Kenya; S3/R2 otherwise).
  This is a hard prerequisite for both apparel and the storefront (§17).

### Cause-&-Effect Map — A1–A3

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | Layaway is NOT debt (goods not yet released) — must appear in its own "Amana" ledger, never in deni aging. Credit sales of released goods are normal deni |
| Receipts | **Yes** | Variant label on lines; layaway receipt shows paid/balance/hold-expiry/forfeit policy |
| SMS | **Yes** | Layaway reminders, hold expiring, markdown-approved confirmation to owner |
| Analytics | **Yes** | "Bale Performance" (envelope) + "Boutique Performance" (variant sell-through %, markdown KES, dead stock). Must not appear for kibanda profiles |
| Home dashboard | **Yes** | Tiles: layaway held KES, bales open, dead stock KES, sell-through % |
| Revenue targets | **Yes** | Normal sales count; layaway counts only on completion |
| Expiry alerts | **No** | Apparel does not compose LOT — must be hidden |
| Shift / Z-report | **Yes** | Standard retail shift + drawer |
| Navbar | **Yes** | Duka · Bale · Amana (layaway) · Ofa/Markdown · Ripoti |
| Access gate | **Yes** | Store-scoped |
| M-Pesa | **Yes** | Per-store resolver |
| Haki ledger | **Yes** | Sales attributed per staff; commission optional (boutiques often pay commission) |
| **Inverse action** | **Yes** | Layaway ↔ refund/forfeit/release · markdown ↔ revert · variant create ↔ deactivate · bale open ↔ close/discard (exists) · sale ↔ return (R-sprint primitive, reused) |

### Acceptance criteria
- **A1-AC1:** Creating a 4-size × 3-colour product generates 12 sellable child items with
  unique SKUs in one action; stock list shows one collapsed parent row.
- **A1-AC2:** Every existing balance/analytics surface handles child items without showing the
  parent as sellable (grep sweep evidence required in the PR).
- **A2-AC1:** A bale of KES 12,000 with target 30,000 depletes by 1000/500/300/100 sales,
  closes at target, and reports realized markup — using the existing ProduceBunch code path.
- **A2-AC2:** A kibanda business sees no bale vocabulary anywhere, and an apparel business sees
  no "mboga" vocabulary anywhere.
- **A3-AC1:** A layaway reserves the specific variant; the variant's available balance drops on
  every surface; expiry of the hold releases it and notifies the customer.
- **A3-AC2:** Deposits held appear as a liability tile and are excluded from revenue and profit.

---

## 9. Salon / Barbershop / Spa — SPRINTS S1–S3

> *"Can it work for my salon Roy?"*
> Yes — and this is the type where your accountability engine has the most spectacular,
> most under-served application in the entire market.

### 9.1 How it actually runs

- Revenue is **services**, not goods: retouch, weave, braids, wash-and-blow, cut, shave, nails,
  pedicure. Prices are per service, sometimes per hair length/size.
- Staff are stylists/barbers, usually paid **commission** (30–50%) or renting a chair
  (daily/weekly). They frequently have personal client relationships stronger than the salon's.
- **The defining leak: supplies.** Relaxer, dye, peroxide, shampoo, treatment, gel, blades,
  gloves. Stock walks out of the door and, worse, stylists serve **side clients** — their own
  customers, on the salon's chair, with the salon's product, off the books, cash in pocket.
  Every salon owner in Nairobi knows this happens and none of them can prove it.
- Appointments are a WhatsApp mess; no-shows are constant; walk-ins dominate.
- Retail product sales (shampoo, oils) run alongside services.

### 9.2 SPRINT S1 — Services, recipes and the side-client detector

```python
class Service(models.Model):
    business = FK; store = FK(Store, null=True)
    name = CharField(max_length=120)                  # 'Retouch — nywele fupi'
    category = CharField(max_length=60, blank=True)   # 'Nywele'|'Kucha'|'Ngozi'
    price = Decimal; duration_minutes = PositiveIntegerField(default=30)
    buffer_minutes = PositiveIntegerField(default=0)
    commission_type = CharField(choices=[('NONE',...),('PERCENT',...),('FIXED',...)], default='NONE')
    commission_value = Decimal(default=0)
    requires_booking = BooleanField(default=False)
    is_active = BooleanField(default=True)
    photo = ImageField(null=True, blank=True)         # storefront
    display_order = IntegerField(default=0)

class ServiceSupplyLine(models.Model):
    """The recipe. THIS is what makes the accountability engine work for salons."""
    service = FK(Service, related_name='supplies')
    item = FK(Item)                  # a MEASURE or UNIT stock item
    qty_expected = Decimal           # e.g. 60 (ml of relaxer)
    tolerance_pct = Decimal(default=25)   # hair varies — be generous or you cry wolf
```

**Sale path — reuse `Transaction`, do not invent a parallel ledger:**
```python
# core/models.py — Transaction addition
service = FK('Service', null=True, blank=True)
performed_by = FK('accounts.UserProfile', null=True, blank=True)   # the stylist, not the cashier
```
`type='Issue'`, `item=None` is not viable (item is non-null) → VERIFY-ME on `Transaction.item`
nullability. If non-nullable, create one shadow `Item` per Service (`stock_model='SERVICE'`,
balance untracked) so every existing receipt/analytics/debt/target code path works unchanged.
**Prefer the shadow-item approach** — it is the lower-risk choice in this codebase and keeps
receipts, revenue targets and the debt tracker working with zero modification.

Completing a service auto-creates `Issue` transactions for each recipe line against supply
stock — exactly the yield-processing pattern already in `add_transaction`.

**The side-client detector (`RECIPE_VARIANCE` accountability):**
```
expected_consumption = Σ (services_performed × qty_expected)     over the period
actual_consumption   = opening_count + receipts − closing_count  (from StockCountSession)
variance             = actual − expected
```
Salon-specific copy, per stylist, per period:
*"Huduma 34 za retouch zilihitaji lita 2.0 za relaxer. Zilizotumika: lita 3.4.
Tofauti: lita 1.4 (KES 2,100). Kiwango cha kawaida: 12%."*

Apply the same hardening rules as the keg leaderboard: **learn the baseline before accusing**
(`Still learning N/3`), aggregate in **KES not mean-of-percentages**, surface `coverage_pct`,
and keep `tolerance_pct` generous — hair genuinely varies, and a false accusation costs the
owner a stylist. Frame the output as a **conversation prompt, not a verdict**: the UI copy
should be "worth asking about", never "amekuiba".

### 9.3 SPRINT S2 — Bookings, walk-ins, chair queue

```python
class Appointment(models.Model):
    business = FK; store = FK(Store); customer = FK(Customer, null=True)
    customer_name = CharField(blank=True); customer_phone = CharField(blank=True)
    staff = FK(UserProfile, null=True)          # requested stylist
    start_at = DateTimeField(); end_at = DateTimeField()
    status = CharField(choices=[('BOOKED','Imewekwa'),('CONFIRMED','Imethibitishwa'),
                                ('ARRIVED','Amefika'),('IN_SERVICE','Inaendelea'),
                                ('DONE','Imekamilika'),('NO_SHOW','Hakuja'),
                                ('CANCELLED','Imefutwa')], default='BOOKED')
    source = CharField(choices=[('walkin',...),('phone',...),('whatsapp',...),('online',...)])
    deposit_plan = FK(PaymentPlan, null=True)   # P0-B
    note = TextField(blank=True); created_by = FK(UserProfile, null=True)

class AppointmentService(models.Model):
    appointment = FK(Appointment, related_name='services')
    service = FK(Service); price_at_booking = Decimal
```

**`salon_board.html`** — the daily operating screen, mobile-first:
- **Today column view**: one column per stylist, chips for each appointment, colour-coded by
  status. Tap → arrive / start / complete / no-show.
- **Walk-in button** — dominant, because walk-ins dominate. Assign to the next free stylist.
- **Chair queue** — who is free now, who is busy until when.
- **Complete service sheet** — confirm services performed, adjust supplies used if unusual
  (with a reason), take payment (cash/M-Pesa/deni/split tender/plan), issue receipt.

**SMS** (reuse `NotificationRouter`, respect bundling and cost):
- T-24h reminder: *"Kesho saa {{time}} una appointment ya {{service}} na {{stylist}} — {{salon}}. Jibu 'SAWA' kuthibitisha."*
- No-show tracking on `Customer` → feeds the existing reliability/credit-score concept; a
  repeat no-show can be required to leave a deposit (`PaymentPlan(kind='BOOKING')`).
- **Rebooking nudge** — the highest-ROI SMS in the whole app: *"Umepita wiki 6 tangu retouch
  yako. Tukuwekee nafasi wiki hii?"* Configurable per service (`rebook_after_days`). This is
  found money for the owner and the clearest possible demonstration of the app's value.

### 9.4 SPRINT S3 — Commission, chair rent, and Haki for stylists

**Commission ledger** — compute per stylist per period from `Transaction.performed_by` +
`Service.commission_*`. This is *precisely* what the Haki module was built for: the Staff
Contribution Ledger already exists, `SalaryPayment` already exists with overdue tracking and
employee SMS confirmation. Salon is the best-fit business type for Haki in the entire platform.
- **Kazi Yangu** for a stylist: services done, revenue brought in, commission earned, paid vs
  outstanding, client retention rate. A stylist can see their own worth — and, per your
  two-way transparency principle, the owner is held to paying it on time.
- **Chair rent** alternative: stylist pays a fixed daily/weekly amount instead of commission.
  That is a **rental agreement** — reuse `RentalAgreement` from §12 (`CYCLE` mechanic) with the
  stylist as the tenant. One primitive, two markets.

**Recognition** — the H4 milestone statement applies beautifully here ("Stylist wa mwezi:
wateja 84, KES 96,000"), and doubles as marketing material the salon can post.

### Cause-&-Effect Map — S1–S3

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | Services on credit are common ("nitalipa mwisho wa mwezi") — must flow through the standard credit path + `evaluate_credit()` |
| Receipts | **Yes** | Service lines with stylist name ("Served by" already exists from Sprint 21). Retail product lines alongside |
| SMS | **Yes** | Booking reminder · rebooking nudge · commission/salary confirmation (Haki H2) · no-show follow-up |
| Analytics | **Yes** | New "Salon Performance": revenue per stylist, service mix, chair utilisation %, supply variance KES, client retention/rebooking rate, no-show rate. Gated on SERVICE capability |
| Home dashboard | **Yes** | Tiles: appointments today, revenue today, commission owed, supply variance flag, rebook-due clients |
| Revenue targets | **Yes** | Service revenue counts; per-stylist targets are a natural extension |
| Expiry alerts | **Yes** | Salon chemicals DO expire — compose `LOT` for supplies |
| Tabs → debt | **Yes** | No tabs; hide by capability |
| Shift open/close | **Yes** | Salon shift + cash drawer Z-report per store |
| Navbar | **Yes** | Ratiba (schedule) · Huduma (services) · Bidhaa (retail) · Wateja · Malipo ya Wafanyakazi · Ripoti |
| Access gate | **Yes** | Stylists see own schedule + own Kazi Yangu; only owner/manager sees all stylists and variance |
| M-Pesa | **Yes** | Per-store resolver; deposits via STK |
| Haki ledger | **Yes** | **Central to this type** — commission is the contribution ledger |
| **Inverse action** | **Yes** | Book ↔ cancel/no-show · start ↔ abandon · service done ↔ refund/redo (free redo must record supply consumption but zero revenue — otherwise variance blames the stylist for fixing their own mistake) · commission accrued ↔ paid |

### Acceptance criteria
- **S1-AC1:** Completing a retouch deducts the recipe quantities from supply stock and issues a
  receipt showing the service and the stylist.
- **S1-AC2:** Supply variance report shows expected vs actual per period per stylist, in KES,
  with a learned baseline and `Still learning` state for the first 3 periods, and never
  produces a variance for a stylist whose supplies were not counted (`coverage_pct`).
- **S1-AC3:** A free redo records supply consumption, zero revenue, and is excluded from the
  stylist's variance denominator.
- **S2-AC1:** Double-booking one stylist in the same slot is refused with a clear message.
- **S2-AC2:** Reminder SMS fires once at T-24h; no-show updates the customer record.
- **S3-AC1:** Commission for a period matches a hand calculation exactly; recording payment
  sends the employee SMS confirmation (existing H2 path) and clears from "owed".

---

## 10. Rentals — SPRINTS L1–L3

> Two distinct businesses, one primitive: **an asset that goes out and must come back, and
> money that recurs.** Property (houses, shops, plots) and equipment (tents, chairs, PA,
> cars, gas cylinders).

### 10.1 How it actually runs — property

- Landlord with 6–60 units. Rent due on the 5th. **Arrears are chronic and tracked in a
  notebook.** Tenants pay by M-Pesa Paybill with the **house number as the account number** —
  which means auto-reconciliation is not just possible, it is the natural fit for infrastructure
  you already built in K2a (`resolve_account_by_shortcode`) and C2B confirmation.
- Deposits held (usually 1–2 months) and disputed at exit. **Deposit disputes are the single
  biggest source of landlord–tenant conflict in Kenya**, and a neutral, timestamped ledger
  with move-in/move-out photos is genuinely valuable to *both* sides — the same two-way
  transparency ethic behind Haki.
- Water and electricity often sub-metered; readings taken monthly by a caretaker; billing
  errors and skimming are common.
- The caretaker collects, reports, and sometimes pockets. Owner is frequently absent — often
  in another county entirely.

### 10.2 How it actually runs — equipment

- 300 chairs, 5 tents, a PA system. Booked for events. Deposit taken. Items go out and come
  back **short or damaged**. Double-booking is the operational nightmare. Pricing is per day
  or per event, with delivery/collection charges.

### 10.3 SPRINT L1 — Units, agreements, rent roll

```python
class RentalUnit(models.Model):
    business = FK; store = FK(Store, null=True)      # store = property/portfolio
    kind = CharField(choices=[('property','Nyumba/Chumba'),('equipment','Kifaa')])
    code = CharField(max_length=30)                  # 'A12' — doubles as M-Pesa account number
    name = CharField(max_length=120, blank=True)     # 'Bedsitter A12'
    description = TextField(blank=True)
    default_rate = Decimal                            # per month (property) or per day (equipment)
    rate_period = CharField(choices=[('day',...),('week',...),('month',...)], default='month')
    deposit_amount = Decimal(default=0)
    quantity = PositiveIntegerField(default=1)        # equipment: 300 chairs = one unit, qty 300
    status = CharField(choices=[('AVAILABLE','Iko wazi'),('RESERVED','Imewekwa'),
                                ('OCCUPIED','Ina mtu'),('OUT','Imetoka'),
                                ('MAINTENANCE','Inatengenezwa'),('RETIRED','Imeondolewa')],
                       default='AVAILABLE')
    is_metered = BooleanField(default=False)
    photo_set = ...   # ItemPhoto-style, for condition evidence
    is_published = BooleanField(default=False)        # storefront listing

class RentalAgreement(models.Model):
    business = FK; unit = FK(RentalUnit, related_name='agreements')
    customer = FK(Customer)                           # tenant/hirer — reuses debt + credit score
    start_date = DateField(); end_date = DateField(null=True)
    rate = Decimal; rate_period = CharField(...)
    quantity = PositiveIntegerField(default=1)        # 200 of the 300 chairs
    deposit_held = Decimal(default=0); deposit_plan = FK(PaymentPlan, null=True)
    billing_day = PositiveSmallIntegerField(default=5)
    status = CharField(choices=[('DRAFT',...),('ACTIVE',...),('ENDED',...),('TERMINATED',...)])
    terms_note = TextField(blank=True)
    created_by = FK(UserProfile); created_at

class RentalInvoice(models.Model):
    agreement = FK(RentalAgreement, related_name='invoices')
    period_start = DateField(); period_end = DateField()
    rent_amount = Decimal; utilities_amount = Decimal(default=0)
    other_amount = Decimal(default=0); other_note = CharField(blank=True)
    total = Decimal; paid_amount = Decimal(default=0)
    due_date = DateField()
    status = CharField(choices=[('DUE',...),('PARTIAL',...),('PAID',...),('WAIVED',...)])
    issued_at; receipt = FK(Receipt, null=True)

class MeterReading(models.Model):
    unit = FK(RentalUnit); kind = CharField(choices=[('water',...),('electricity',...)])
    reading = Decimal; read_on = DateField(); read_by = FK(UserProfile, null=True)
    photo = ImageField(null=True)                 # anti-skim evidence
    rate_per_unit = Decimal(null=True)
```

**Rent roll generation** — a monthly job (VERIFY-ME: no Celery on Render free tier; use a
management command triggered by Render Cron, or a lazy "generate on first view of the month"
guard with idempotency, mirroring the existing `RecurringExpense` period-review pattern which
already solves exactly this problem — reuse that approach).

**Arrears = the debt tracker.** Do not build a second aging engine. A `RentalInvoice` that is
unpaid creates the same credit `Transaction` shape the debt tracker already consumes, so you
inherit FIFO, aged buckets (current/30/60/90+), credit score, reminders, statements (K4) and
the credit policy gate for free. Rent arrears aging is *literally* the module you already
shipped.

**M-Pesa auto-reconciliation — the marquee feature for landlords:**
C2B confirmation arrives with `BillRefNumber = 'A12'` → match `RentalUnit.code` → find the
active agreement → apply payment to the oldest open invoice (FIFO) → issue receipt → SMS both
tenant and landlord. Extend K2a's `resolve_account_by_shortcode()` with a unit lookup.
*"Umepokea KES 12,000 kutoka 0722xxx — A12 (Mary W.). Deni iliyobaki: 0."*
A landlord in Mombasa managing flats in Kitengela suddenly has a real-time rent book. That is
your answer to *"can it help me manage what I have without being there?"*

### 10.4 SPRINT L2 — Deposits, condition, move-in/move-out, maintenance

- **Deposit ledger**: held → deductions (itemised, with reason and photo) → refund. Both
  parties get a receipt. **Never silently absorb a deposit into revenue** — it is a liability
  on the books until released, and misclassifying it inflates the owner's apparent profit.
- **Condition checklist** with photos at handover and return. For equipment: quantity out vs
  quantity back (200 chairs → 194 back = 6 × replacement cost, auto-charged against deposit
  with a clear itemised note).
- **Maintenance tickets**: tenant reports (via SMS keyword or the storefront), caretaker
  updates, owner sees cost and history per unit. Feeds a per-unit P&L: rent collected minus
  maintenance minus vacancy = **the number no Kenyan landlord currently knows**.
- **Caretaker role** — new `UserProfile.role='caretaker'`: can record meter readings, report
  maintenance, mark units available; **cannot** record payments, cannot see the full portfolio
  P&L, cannot alter agreements. This is the access-scoping dimension of the protocol and the
  main anti-skim control.

### 10.5 SPRINT L3 — Rental board, calendar, occupancy

**`rental_board.html`:**
- **Property mode**: unit grid colour-coded by status; per unit — tenant, rent, paid/arrears
  chip, days overdue. Filters: arrears only · vacant only · lease expiring. One tap → record
  payment, send reminder, view statement.
- **Equipment mode**: availability calendar (which of the 300 chairs are committed on 14 Sept),
  hard block on overbooking, dispatch/return checklists with quantity reconciliation.
- **Occupancy & yield analytics**: occupancy %, collection rate %, arrears KES, average days
  to pay, per-unit yield, vacancy cost. For equipment: utilisation % per asset, revenue per
  asset, loss/damage KES.

### Cause-&-Effect Map — L1–L3

| Surface | Touched? | How |
|---|---|---|
| Debt tracker | **Yes** | **Arrears ARE debt** — reuse entirely; a rental invoice must not create a parallel ledger. Aged buckets, score, reminders, K4 statements all inherited |
| Receipts | **Yes** | Rent receipt (period, unit code, balance), deposit receipt, refund receipt, damage-deduction receipt |
| SMS | **Yes** | Rent due T-3 · overdue · payment confirmed (both parties) · lease expiring · meter reading recorded · maintenance status |
| Analytics | **Yes** | "Rental Performance": occupancy, collection rate, arrears aging, per-unit yield, maintenance cost. Gated on ASSET capability |
| Home dashboard | **Yes** | Tiles: collected this month vs expected, arrears KES, vacant units, deposits held (liability), leases expiring |
| Revenue targets | **Yes** | Monthly rent target = expected rent roll — a naturally strong fit |
| Expiry alerts | **Repurposed** | Lease expiry uses the same alert machinery |
| Shift | **No** | Rentals have no shifts — must be hidden by capability, not left visible-but-empty |
| Navbar | **Yes** | Nyumba/Vifaa · Wapangaji · Malipo · Amana · Matengenezo · Ripoti |
| Access gate | **Yes** | Caretaker scoping is the key control; per-store = per-property-portfolio |
| M-Pesa | **Yes** | **Account-number → unit matching is the headline feature**; extends K2a resolver |
| Haki ledger | **Yes** | Caretaker contribution + pay; chair-rent stylists reuse `RentalAgreement` |
| **Inverse action** | **Yes** | Agreement start ↔ terminate · deposit hold ↔ refund/deduct · unit out ↔ return · invoice ↔ waive/credit note · maintenance open ↔ closed |

### Acceptance criteria
- **L1-AC1:** Generating the month's rent roll for 20 units is idempotent — running it twice
  creates no duplicate invoices (same guarantee as the `RecurringExpense` period review).
- **L1-AC2:** A C2B payment with account number `A12` applies to unit A12's oldest open
  invoice, issues a receipt, and SMSes both parties. An unmatched account number raises a
  `BusinessException` rather than silently disappearing.
- **L2-AC1:** Deposits appear as a liability, never as revenue, until released. Deductions are
  itemised and produce a receipt the tenant can view at their own token URL.
- **L2-AC2:** A caretaker cannot reach any payment-recording URL, by view or by URL manipulation.
- **L3-AC1:** Equipment cannot be double-booked; a return of 194 of 200 chairs produces a
  damage/loss charge against the deposit with an itemised note.

---

## 11. Composed profiles — no new modules required

These need **configuration, catalogs and vocabulary**, not new vertical stacks. Each is a
half-sprint once §4–§10 exist. Ship them as `business_profiles.py` entries plus a seeded
item catalog.

| Profile | Composition | The one thing that makes it feel native |
|---|---|---|
| **Pharmacy / chemist** | LOT + UNIT + CYCLE_COUNT + RESTRICTED (built) + EXPIRY (built) | **True FIFO batch depletion** (already Next-Sprint-Candidate #3) + POM register: restricted items already require owner approval — reframe as prescription log with prescriber name |
| **Butchery** | ENVELOPE(carcass) + MEASURE + PRESET | A carcass is a revenue envelope: cost 18,000, target 26,000, sold by cuts at price points. Yield variance (bone/fat loss %) uses the learned-baseline engine |
| **Hardware / building** | UNIT + VARIANT + MEASURE + CREDIT + PLAN | Bulk-break (buy by tonne, sell by kg/piece); **credit to fundis** is the norm — the K3 credit policy gate is the selling point; quotations that convert to sales |
| **Phone / electronics** | SERIAL + VARIANT + PLAN + warranty | `Item.serials` — IMEI in, IMEI out, on the receipt. Warranty lookup by IMEI. Kills the "this isn't the phone I sold you" dispute and enables stolen-stock traceability |
| **Gas / water refill** | ASSET(cylinder) + UNIT + EXCHANGE + DEPOSIT | Cylinder deposit ledger + exchange flow (empty in, full out). Reuses `RentalUnit`/deposit primitives exactly |
| **Bakery / food prep** | MEASURE + yield (built) + LOT | Production batch: flour in → loaves out, yield variance vs recipe (the salon RECIPE_VARIANCE engine, applied to dough) |
| **M-Pesa agent** | Float management | ⚠️ **Do not build.** Handling agent float sits uncomfortably close to the CBK PSP boundary in `CLAUDE.md`, and the compliance surface is not worth it. Politely decline this segment |

**SERIAL primitive** (small, needed by phone/electronics):
```python
class ItemSerial(models.Model):
    item = FK(Item, related_name='serials'); business = FK; store = FK(Store, null=True)
    serial = CharField(max_length=64, db_index=True)     # IMEI / asset tag
    status = CharField(choices=[('IN_STOCK',...),('SOLD',...),('RETURNED',...),('FAULTY',...)])
    received_txn = FK(Transaction, null=True, related_name='+')
    sold_txn = FK(Transaction, null=True, related_name='+')
    warranty_until = DateField(null=True)
    class Meta: unique_together = [('business','serial')]
```


---
# PART D — SUPPLY CHAIN, STOREFRONT, SEQUENCING

---

## 12. Suppliers, payables and riders — SPRINTS X1–X3

You already have a supplier portal, rider portal and procurement (POs, bids, scoring). Assessed
against the new business types, three structural gaps stand out. All three are money-path gaps.

### 12.1 SPRINT X1 — Payables: the missing half of the cash picture

**The gap.** The app tracks what customers owe the business (receivables, beautifully). It does
not track **what the business owes suppliers**. Every duka, minimart, hardware and bar in Kenya
receives mzigo on supplier credit. An owner looking at the app today sees "KES 84,000 owed to
me" and feels rich, while owing the distributor KES 61,000. **The app is currently telling
owners a half-truth about their own solvency.** For retail this is not a nice-to-have.

```python
class SupplierInvoice(models.Model):
    business = FK; store = FK(Store, null=True)
    supplier = FK(Business, null=True, related_name='issued_invoices')  # platform supplier
    supplier_name = CharField(blank=True)          # off-platform distributor — most cases
    invoice_no = CharField(max_length=60, blank=True)
    purchase_order = FK('PurchaseOrder', null=True, blank=True)   # VERIFY-ME: existing model name
    invoice_date = DateField(); due_date = DateField(null=True)
    amount = Decimal; paid_amount = Decimal(default=0)
    status = CharField(choices=[('DUE',...),('PARTIAL',...),('PAID',...),('DISPUTED',...)])
    note = TextField(blank=True); recorded_by = FK(UserProfile)

class SupplierPayment(models.Model):
    invoice = FK(SupplierInvoice, related_name='payments')
    amount = Decimal; method = CharField(...); reference = CharField(blank=True)
    paid_on = DateField(); recorded_by = FK(UserProfile)
```

- **Aging mirrors the debt tracker** — same buckets, same UI patterns, opposite direction.
  Build it as a mirror module (`payables_views.py`) reusing the debt tracker's aging helpers.
- **Cash position tile**: `Receivables − Payables = Hali halisi ya pesa`. Put it on the owner
  dashboard. It is the most honest number in the app.
- **Payment due reminders** to the owner (not the supplier) — missing a distributor payment
  costs a shop its credit line, which is existential.

### 12.2 SPRINT X2 — Goods Received Note: reconcile what was ordered vs delivered vs invoiced

**The gap.** Receiving stock is currently a `Receipt` transaction typed in by hand. Nothing
checks it against the PO. The classic three-way leak is invisible: **ordered 50, delivered 46,
invoiced 50.** Staff receiving goods can also under-record deliveries and take the difference —
the exact class of shenanigan you have been fighting on the bar side, one step upstream.

```python
class GoodsReceivedNote(models.Model):
    business = FK; store = FK(Store)
    purchase_order = FK('PurchaseOrder', null=True)
    supplier_name = CharField(blank=True)
    delivery_note_no = CharField(blank=True)
    received_by = FK(UserProfile); received_at = DateTimeField()
    status = CharField(choices=[('OK',...),('SHORT',...),('OVER',...),('DAMAGED',...),('DISPUTED',...)])
    photo = ImageField(null=True)          # delivery note photo — evidence
    note = TextField(blank=True)

class GoodsReceivedLine(models.Model):
    grn = FK(GoodsReceivedNote, related_name='lines')
    item = FK(Item)
    qty_ordered = Decimal(null=True); qty_delivered = Decimal; qty_accepted = Decimal
    unit_cost = Decimal; po_unit_cost = Decimal(null=True)
    variance_reason = CharField(blank=True)
```
- Any qty or price variance vs the PO → `BusinessException` + owner alert + a pre-drafted
  claim message to the supplier.
- **Cost variance is where money quietly dies**: PO says 145/unit, invoice says 152/unit,
  nobody notices for four months. Flag it at receipt, feed the R2 margin guard.
- The GRN posts the `Receipt` transactions — one path, audited, instead of free-typed receipts.
- Second-signature option for high-value deliveries (`business.grn_second_check_above`).

### 12.3 SPRINT X3 — Rider accountability and COD reconciliation

**The gap.** A rider carrying goods and collecting cash is an unreconciled cash position moving
around Nairobi on a motorbike. Currently there is a rider portal but no proof of delivery and
no cash remittance ledger. This is the same problem as the bar's cash drawer, in motion.

```python
class DeliveryRun(models.Model):
    business = FK; store = FK(Store); rider = FK(UserProfile)
    status = CharField(choices=[('ASSIGNED',...),('OUT',...),('COMPLETED',...),('RECONCILED',...)])
    dispatched_at; completed_at = DateTimeField(null=True)
    cash_expected = Decimal(default=0); cash_remitted = Decimal(default=0)
    reconciled_by = FK(UserProfile, null=True); reconciled_at = DateTimeField(null=True)

class DeliveryStop(models.Model):
    run = FK(DeliveryRun, related_name='stops')
    order = FK('Order', null=True); transfer = FK(StockTransfer, null=True)
    customer_name; phone; address_note; latitude; longitude
    sequence = PositiveIntegerField()
    status = CharField(choices=[('PENDING',...),('DELIVERED',...),('FAILED',...),('RETURNED',...)])
    proof_kind = CharField(choices=[('otp',...),('photo',...),('signature',...),('none',...)])
    proof_ref = CharField(blank=True)     # OTP entered / photo path
    cash_collected = Decimal(default=0)
    delivered_at = DateTimeField(null=True); fail_reason = CharField(blank=True)
```
- **Delivery OTP**: customer receives a 4-digit code by SMS; rider enters it to close the stop.
  Cheap, offline-tolerant, and ends "the rider says he delivered it" disputes.
- **COD reconciliation**: at run close, `cash_expected` vs `cash_remitted`; variance →
  `BusinessException` + `StaffShrinkage` against the rider. Same engine, same leaderboard,
  same learned baseline. Riders appear in the Haki ledger too — their contribution counts.
- **Undelivered stock returns to the store as an inbound transfer** (M2), never quietly
  written off. Inverse action, per protocol.
- Delivery fee ledger: what the customer paid vs what the rider is owed — feeds Haki.

### 12.4 What is already good and should not be rebuilt
Supplier bids/scoring and the rider portal already exist. X1–X3 wrap them rather than replace
them. **VERIFY-ME:** read the existing procurement models and reuse `PurchaseOrder` /supplier
model names verbatim; do not create parallel entities.

---

## 13. Customer-facing storefront — SPRINTS W1–W4

> *"...sell online and grow, just as I envisioned"*

### 13.1 The boundary, restated
`CLAUDE.md` is unambiguous and it constrains this design: **money never flows through any
account Duka Mwecheche controls.** The storefront therefore takes orders and *directs payment
to the business's own Till/Paybill/Pochi* — Tier 0 EMVCo QR or Tier 1 per-owner STK Push.
Duka Mwecheche is a shopfront and a reconciliation layer, never a marketplace escrow. Any
future "buyer protection" or "pay on delivery held by us" feature crosses into CBK PSP
territory and must be refused at the design stage, not discovered late.

### 13.2 SPRINT W1 — Public catalog
```python
# Item additions
is_published = BooleanField(default=False)
online_price = Decimal(null=True, blank=True)   # blank = use selling_price
online_description = TextField(blank=True)

class ItemPhoto(models.Model):
    item = FK(Item, related_name='photos'); image = ImageField(upload_to='items/')
    display_order = IntegerField(default=0); is_primary = BooleanField(default=False)

# Business additions
slug = SlugField(unique=True)
storefront_enabled = BooleanField(default=False)
storefront_bio = TextField(blank=True)
storefront_delivery_note = CharField(blank=True)
```
Routes: `/duka/<slug>/` catalog · `/duka/<slug>/b/<item_id>/` detail · `/duka/<slug>/cart/`.
Public, no login, mobile-first, **fast on 3G** — this is the real constraint. Images must be
lazily loaded and served resized; the page must be usable before photos land.

**Per business type the catalog means something different**, driven by capability:
apparel → photo grid with size/colour chips (variants) · retail → searchable list with
categories · salon → **service menu with prices and a "Weka nafasi" booking button** ·
rentals → available units with photos and rates · kibanda/bar → typically not published.

### 13.3 SPRINT W2 — Orders, reservation and fulfilment
**VERIFY-ME:** `CLAUDE.md` Next-Sprint-Candidate #5 references a draft `Order` model. Confirm
whether it exists; if it does, extend it rather than creating a parallel model.

- Customer enters name + phone → **OTP by SMS** (no passwords; reuse Africa's Talking).
- Cart → order → business receives in-app notification + SMS. Owner accepts/rejects.
- **Stock reservation with a timeout** (e.g. 30 min) so online orders cannot oversell the
  physical counter — this is the most common failure mode of small-shop e-commerce and it
  reuses the P0-B reservation helper.
- Fulfilment: pickup · rider delivery (X3 `DeliveryRun`) · "tutakupigia" (call to confirm).
- Payment: EMVCo QR (Tier 0) or STK Push (Tier 1) to the owner's own shortcode via
  `resolve_mpesa_config(business, store)` — store-aware, so a branch's order pays that branch.
- **WhatsApp-first is not optional in this market**: every cart must produce a share link
  (`wa.me/?text=...`) with the order summary. Many customers will order by WhatsApp and the
  owner will paste the link back. Design for that rather than against it.

### 13.4 SPRINT W3 — Customer self-service (two-way transparency, applied to customers)
A logged-in (OTP) customer at `/akaunti/` sees:
- their order history and receipts (the K4 token receipts already exist),
- **their own deni statement** with aging and score — the same statement the business sees,
- rental tenants: their invoices, payments, deposit ledger and meter readings,
- salon clients: upcoming appointments, service history, rebooking.

This is your Haki principle extended to the customer side: the business already sees everything
about the customer; the customer should see the same record. It also quietly reduces disputes,
which is the practical argument that will sell it to owners.

### 13.5 SPRINT W4 — Discovery (optional, strategic — decide deliberately)
`/soko/` — browse businesses by county/sub-county and category. This converts the platform from
a tool into a network and is the single biggest strategic decision in this document. It brings
real obligations: business verification, review moderation, dispute handling, and the
temptation to become an intermediary (which the CBK boundary forbids).

**Recommendation: defer.** Ship W1–W3, get 20 businesses selling through their own storefronts,
then decide with evidence. A weak directory with 15 shops is worse than none.

---

## 14. Feeding the accountability work back into Bar and Kibanda

You asked me not to redesign bar and kibanda, and I have not. But this architecture pays them
back, and you said the loophole work there is unfinished — so, briefly, what falls out for free:

- **`BusinessException` feed (M3)** replaces ad-hoc bar alerts with one prioritised list the
  owner actually reads, across every branch.
- **Multi-store (M1–M3)** answers the bar-chain question directly: one owner, three joints,
  one console, per-branch shrinkage comparison. Comparing branches is itself an accountability
  tool — two bars with the same taps and different variance is a conversation.
- **Split tender (P0-A)** closes the kibanda gap and also the bar's "amelipa nusu" case.
- **Stock transfer (M2)** closes the "nilipeleka branch nyingine" hole, which in a multi-bar
  operation is where crates and spirits disappear.
- **Returns primitive (R sprints)** gives bar a proper breakage/return path distinct from
  wastage.
- **GRN (X2)** closes the upstream leak: staff receiving a delivery of 46 crates and recording
  50 — or recording 46 and selling 4 off-book.
- **Rider COD (X3)** applies the drawer-variance engine to cash in motion.

Two further bar-side loopholes worth logging now while they are in mind, for a later sprint of
your own design (not specced here): **(1)** the gap between a tab being opened and the customer
leaving — an unrecorded top-up poured against an open tab is invisible to both the keg variance
and the tab total unless the pour is rung; **(2)** shift-boundary laundering — sales rung under
a colleague's still-open shift. The `performed_by` field introduced for salons (§9.2) is the
generic fix for (2): separate *who rang it* from *whose shift it fell in*, and variance stops
being dodgeable by timing.

---

## 15. Recommended sequence

Ordered by (leverage × commercial pull) ÷ risk. Each numbered item is one Claude Code session
unless noted.

**Phase 0 — Foundation (do not skip, do not reorder)**
1. `SPRINT M0` — capability refactor *(2 sessions; pure refactor, highest leverage in the doc)*
2. `SPRINT P0-A` — split tender *(1 session; closes your named Kibanda gap immediately)*
3. `SPRINT M1` — store as first-class outlet + staff scoping *(2 sessions)*
4. `SPRINT M2` — stock transfers *(1 session)*
5. `SPRINT M3` — Maduka Yangu owner console + `BusinessException` *(2 sessions)*

*At this point you can answer the multi-store question for bars and vibandas, and the app has
stopped growing sideways.*

**Phase 1 — Retail (largest market, most reuse)**
6. `SPRINT R1` — barcode + GlobalProduct + fast onboarding *(2 sessions)*
7. `SPRINT R2` — retail board + margin guard + returns primitive *(2 sessions)*
8. `SPRINT R3` — cycle counting + retail shrinkage *(1–2 sessions)*
9. `SPRINT X1` — payables *(1 session; pairs naturally with retail)*
10. `SPRINT R4` — dead stock, reorder, intelligence *(1 session)*

**Phase 2 — Apparel (fastest new type — reuses the envelope you already own)**
11. `SPRINT P0-B` — payment plans *(1 session)*
12. `SPRINT A1` — variants *(1–2 sessions)*
13. `SPRINT A2` — bale envelope *(0.5 session — genuinely mostly a re-skin)*
14. `SPRINT A3` — layaway, markdown, photos *(1 session + image storage setup, §17)*

**Phase 3 — Salon (highest differentiation, best Haki fit)**
15. `SPRINT S1` — services, recipes, side-client detector *(2 sessions)*
16. `SPRINT S2` — bookings and chair queue *(2 sessions)*
17. `SPRINT S3` — commission + Haki integration *(1 session)*

**Phase 4 — Rentals (best "manage remotely" story; strong M-Pesa hook)**
18. `SPRINT L1` — units, agreements, rent roll, C2B account matching *(2 sessions)*
19. `SPRINT L2` — deposits, condition, maintenance, caretaker role *(1–2 sessions)*
20. `SPRINT L3` — rental board + occupancy analytics *(1 session)*

**Phase 5 — Supply chain completion**
21. `SPRINT X2` — GRN three-way match *(1 session)*
22. `SPRINT X3` — rider POD + COD reconciliation *(1–2 sessions)*

**Phase 6 — Storefront**
23. `SPRINT W1` — public catalog *(1–2 sessions)*
24. `SPRINT W2` — orders, reservation, fulfilment *(2 sessions)*
25. `SPRINT W3` — customer self-service *(1 session)*
26. `SPRINT W4` — directory *(defer; decide with evidence)*

**Composed profiles** (pharmacy, butchery, hardware, phone, gas, bakery) slot in as half-sessions
after Phase 1, whenever a real customer asks.

**Reordering rule:** if a paying customer is waiting, pull their phase forward — but **never
skip Phase 0**. Building any new type before M0 recreates the exact problem this document exists
to solve.

---
# PART E — INFRASTRUCTURE AND OPEN QUESTIONS

## 16. Infrastructure realities to fix before Phase 2

These will bite, and two of them are hard blockers:

1. **🔴 Image storage.** Render's free tier filesystem is ephemeral — uploaded photos are lost
   on every redeploy. Apparel, storefront, condition evidence, meter-reading photos and GRN
   delivery notes all require external storage. Set up Cloudinary (free tier, works well from
   Kenya) or Cloudflare R2 **before Sprint A3**. Non-negotiable blocker.
2. **🔴 Scheduled jobs.** Rent roll generation, ABC reclassification, dead-stock digests, daily
   owner digest, market-price aggregation all need a scheduler. There is no Celery here. Use
   Render Cron Jobs calling management commands, and make every job **idempotent** — the
   `RecurringExpense` period-review pattern already in the codebase is the model to copy.
3. **🟠 Free-tier cold starts.** A storefront that takes 40 seconds to wake will lose every
   customer. Before W1, either move to a paid instance or add an external pinger. Assume the
   storefront cannot ship on free tier.
4. **🟠 Query load.** Multi-store aggregation and variant grids will produce N+1 queries.
   Follow the `keg_metrics.py` precedent: dataclass return contracts, prefetch, in-memory
   fallbacks. Keep `iterator(chunk_size=10)` for heavy loops (SIGKILL risk is already
   documented).
5. **🟠 SMS cost.** Every new alert type multiplies spend. Enforce: one owner digest per day,
   respect the 10-min bundling window, and give every alert class an on/off switch. Model the
   monthly SMS cost per business type before shipping S2 (booking reminders) and L1 (rent
   reminders) — those two are the highest-volume senders in this document.
6. **🟡 Test suite.** 72 tests today. Every money-path sprint here must add tests: split tender
   sums, transfer balance conservation, deposit-liability exclusion from revenue, C2B account
   matching, commission calculation, return reversal. Target ~120 tests by end of Phase 2.
7. **🟡 Security.** The committed SQLite file with real credential hashes noted in your history
   — confirm it is fully purged from git history (not just deleted in HEAD) and that all
   affected credentials were rotated. Also confirm `SECRET_KEY` is set in Render env vars and
   not falling back to the hardcoded default.

## 17. Open questions — I need your answers before some of this is final

1. **The existing 8 profiles.** Which are they exactly? I designed against your named asks
   (minimart, apparel, salon, rentals) plus likely composed types. If the registry already
   contains, say, "school" or "garage", tell me and I will spec them properly.
2. **Rentals: property or equipment first?** They share primitives but the UX differs. The
   property/M-Pesa-account-matching story is stronger commercially; equipment is simpler.
3. **The market-price benchmark (§7.2).** This is powerful and also the most sensitive thing in
   this document. Are you comfortable with anonymised county medians at sample_size ≥ 5, opt-out
   default-on? Or do you want it opt-**in**, which is safer and slower?
4. **Salon variance framing.** How hard do you want the side-client detector to push? My spec
   frames it as "worth asking about" rather than an accusation. Your call — you know how these
   conversations actually go between a salon owner and a stylist.
5. **Layaway forfeiture default.** Full forfeit, refund-minus-percentage, or full refund? This
   is an ethics call in the Haki spirit and should be your decision, not mine.
6. **Directory (W4).** Network play or stay a tool? It changes what the company is.
7. **Pricing.** Multi-store, storefront and the accountability engine are chain-scale features.
   Is there a tier structure in mind? It affects what gets gated behind `Business` flags, and
   flags are cheapest to add at build time.

---

## 18. One-paragraph version, for when someone asks you at a matatu stage

> Duka Mwecheche stops being "an app for bars and vibandas" and becomes a platform where a
> business type is a *recipe*: how you count stock, how you take money, and how you prove
> nothing walked out of the door. A salon composes services, a recipe-variance check and
> commission. A boutique composes size-colour variants, bales and layaway. A landlord composes
> units, monthly cycles and M-Pesa account matching. All of them inherit the debt tracker, the
> receipts, the shift discipline, the shrinkage engine and the Haki ledger you already built for
> the bar — because those were never really about beer. They were about knowing, at the end of
> the day, exactly what happened in your business while you were not standing in it.

*— End of spec. §17 needs Roy's answers before Phases 3–6 are final.*
