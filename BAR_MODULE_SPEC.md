# Duka Mwecheche — Bar & Club Module + Business-Type UI Overhaul
## Implementation Spec for Claude Code

> **How to use this document:** Work sprint by sprint, in order. Every sprint ends in a
> deployable state. Follow ALL rules in CLAUDE.md (dark luxury theme, complete files,
> multi-tenancy scoping, Resend-only email, no `{% trans %}` line-wrapping, btn-gold,
> `style="color: #b0b0b0"` for muted text). Before writing code in any sprint, re-read
> the models and views this spec says it touches.

---

## 0. The Core Insight

A keg barrel is a **revenue envelope with a weighing scale attached**.

The Kibanda module already proved the envelope pattern: buy at cost, set a target,
sell by price points, close the batch when the target is earned. `KegBarrel` is the
same pattern as `ProduceBunch` with one new axis — **physical weight is independent
ground truth**. The scale doesn't lie:

```
dispensed_litres = opening_weight_kg − closing_weight_kg        (1 kg ≈ 1 L of beer)
expected_revenue = dispensed_litres / net_volume_l × target_revenue
variance         = expected_revenue − recorded_sales
```

Variance beyond tolerance = spillage, foam, free cups, under-recording, or theft.
This is THE number bar owners want and currently cannot get. Real-world data: keg
barrel margins swing KES 250–400+ per barrel purely on pouring discipline.

**Deliberately a NEW model, not ProduceBunch reuse.** ProduceBunch is money-only;
bolting weight, tapping lifecycle, and shift readings onto it would pollute the
produce module. Same philosophy, separate table.

**Bottle splitting needs ZERO new models.** `ItemPortionPreset` with fractional
`quantity_consumed` already does it:

| Bottle (Item, unit=Btl) | Preset label | price | quantity_consumed |
|---|---|---|---|
| Kibao 250ml (Quarter) | Mzima / Whole | 350 | 1.0 |
| Kibao 250ml (Quarter) | Nusu ya robo | 150 | 0.5 |
| Kibao 250ml (Quarter) | Robo ya robo | 50 | 0.25 |
| Chrome 750ml (Mzinga) | Chupa nzima | 1200 | 1.0 |
| Chrome 750ml (Mzinga) | Double shot | 100 | 0.08 |
| Chrome 750ml (Mzinga) | Single shot | 60 | 0.04 |

`sale_amount` already captures the preset price (commit fbff5b4 behaviour), so split
sales sum correctly toward the bottle's retail value. Fractional bottle balances
(e.g. 3.75 Btl) are fine — `qty` is Decimal. Halves (350/375ml) sell whole only:
single "Mzima" preset or no presets at all.

Shot math note: a 750ml mzinga ≈ 25 standard 30ml singles → single = 1/25 = 0.04,
double = 0.08. The white line on bar shot glasses marks single/double — presets
mirror that exactly.

---

## 1. New & Changed Data Models (migration 0043_bar_module)

All models in `core/models.py` unless stated. Every FK to Business; every queryset
in views scoped to `request.user.userprofile.business`.

### 1.1 Item — new fields
```python
is_keg = models.BooleanField(default=False,
    help_text='Keg item sold from a barrel by weight/volume. Stock tracked in ml '
              'via KegBarrel envelopes, not the normal balance.')
volume_ml = models.PositiveIntegerField(null=True, blank=True,
    help_text='Bottle volume for single-piece liquor (750=mzinga, 350/375=half, '
              '250=quarter). Drives shot math and catalog labels.')
```
- A keg Item (e.g. "Senator Keg Dark") has `is_keg=True`, unit `Ml`.
  Its `ItemPortionPreset` rows ARE the price tiles, with `quantity_consumed` = volume
  in ml: `Kikombe 300ml @ 70 (qty 300)`, `Jug @ 210 (qty 1250)`.
  This reuses the entire preset editor UI from the produce module.
- Keg items are EXCLUDED from the normal stock grid and from produce boards
  (filter `is_keg=False` wherever produce/board/grid querysets are built).

### 1.2 Business — new fields
```python
keg_variance_tolerance_pct = models.DecimalField(max_digits=4, decimal_places=1,
    default=3.0, help_text='Allowed % gap between weight-implied revenue and '
                           'recorded keg sales before a shift is flagged.')
keg_default_gross_kg = models.DecimalField(max_digits=5, decimal_places=2, default=60)
keg_default_tare_kg  = models.DecimalField(max_digits=5, decimal_places=2, default=10)
keg_revenue_multiplier = models.DecimalField(max_digits=4, decimal_places=2,
    default=1.50, help_text='Suggested barrel target = cost × this. '
                            '5000 × 1.5 = 7500, matching common owner targets.')
```

### 1.3 KegBarrel (the weight-aware envelope)
```python
class KegBarrel(models.Model):
    STATUS_CHOICES = [
        ('SEALED',   _('Sealed — received, not tapped')),
        ('TAPPED',   _('Tapped — selling')),
        ('DEPLETED', _('Depleted — target reached / empty')),
        ('RETURNED', _('Returned / discarded')),
    ]
    business   = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='keg_barrels')
    store      = models.ForeignKey(Store, on_delete=models.CASCADE, null=True, blank=True)
    item       = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='keg_barrels')
    gross_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, default=60)
    tare_weight_kg  = models.DecimalField(max_digits=6, decimal_places=2, default=10)
    cost_price      = models.DecimalField(max_digits=10, decimal_places=2)
    target_revenue  = models.DecimalField(max_digits=10, decimal_places=2)
    revenue_collected   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    volume_dispensed_ml = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Sum of preset volumes sold — the BOOK figure. Compare with weight.')
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='SEALED')
    received_on = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='kegs_received')
    tapped_at   = models.DateTimeField(null=True, blank=True)
    closed_at   = models.DateTimeField(null=True, blank=True)
    note        = models.CharField(max_length=120, blank=True)

    @property
    def net_volume_l(self):
        return float(self.gross_weight_kg) - float(self.tare_weight_kg)  # 1 kg ≈ 1 L

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
        return max(0, float(self.target_revenue) - float(self.revenue_collected))

    def realized_markup(self):
        return (float(self.revenue_collected) / float(self.cost_price)) if self.cost_price else 0

    def record_sale(self, preset, qty, payment_method, recorded_by, tab=None,
                    server_name=''):
        """One pour. Creates Transaction(type=Issue, qty=ml, sale_amount=KES,
        keg_barrel=self, bar_tab=tab). Increments revenue_collected and
        volume_dispensed_ml. Auto-DEPLETED when envelope reached AND latest
        weight ≤ tare + 0.5kg (don't close on money alone — owner may overshoot
        target on a generous barrel). Mirror ProduceBunch.record_sale structure."""

    def tap(self, user):  # SEALED → TAPPED, set tapped_at
    def close(self, reason=''):  # → DEPLETED/RETURNED, closed_at=now
```
**Float×Decimal warning applies everywhere above — cast both sides to float.**

### 1.4 KegWeightReading (audit trail; handover signatures)
```python
class KegWeightReading(models.Model):
    READING_TYPES = [
        ('RECEIVE', _('Received — verify 60kg')),
        ('SHIFT_OPEN', _('Shift opening check')),
        ('SHIFT_CLOSE', _('Shift closing check')),
        ('SPOT', _('Spot check')),
        ('FINAL', _('Final / barrel empty')),
    ]
    barrel       = models.ForeignKey(KegBarrel, on_delete=models.CASCADE, related_name='weight_readings')
    shift        = models.ForeignKey('Shift', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='keg_readings')
    weight_kg    = models.DecimalField(max_digits=6, decimal_places=2)
    reading_type = models.CharField(max_length=12, choices=READING_TYPES)
    recorded_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                     related_name='keg_readings_recorded')
    confirmed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='keg_readings_confirmed',
        help_text='Incoming staff who verified this reading at handover.')
    recorded_at  = models.DateTimeField(auto_now_add=True)
    note         = models.CharField(max_length=120, blank=True)
```
The handover ritual in code: outgoing staff records SHIFT_CLOSE; incoming staff
taps "Confirm" (sets `confirmed_by`) which simultaneously creates her SHIFT_OPEN
reading at the same weight. Disagreement → she enters her own figure and both
readings persist with the discrepancy flagged to the owner via NotificationRouter.

### 1.5 Shift (generalized — this IS Next-Sprint candidate #2, killed here)
```python
class Shift(models.Model):
    STATUS = [('OPEN', _('Open')), ('CLOSED', _('Closed — awaiting confirmation')),
              ('CONFIRMED', _('Confirmed by incoming staff'))]
    business      = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='shifts')
    store         = models.ForeignKey(Store, on_delete=models.CASCADE, null=True, blank=True)
    staff         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shifts')
    status        = models.CharField(max_length=10, choices=STATUS, default='OPEN')
    started_at    = models.DateTimeField(default=timezone.now)
    ended_at      = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_cash_counted = models.DecimalField(max_digits=10, decimal_places=2,
                                               null=True, blank=True)
    confirmed_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='shifts_confirmed')
    notes         = models.TextField(blank=True)
```
Reconciliation is COMPUTED, never stored (single source of truth = transactions +
readings). Service function `build_shift_reconciliation(shift)` in new file
`core/shift_views.py` returns:
- Per open barrel: opening kg, closing kg, dispensed ml, expected KES,
  recorded KES, variance KES, variance %, flag colour
- Cash: opening float + cash sales + tab settlements(cash) − payouts vs counted
- M-Pesa sales total (verifiable against till statement)
- Outstanding (unpaid) tab total, grouped by server
- Overall flag: green within tolerance, gold borderline (≤1.5× tolerance),
  raspberry beyond. Use existing `_build_target_data` colour philosophy —
  compute in the view, never `{% widthratio %}`.

Shifts are OPTIONAL plumbing for non-bar businesses but the model is generic on
purpose — kibanda and shops get cash-only shift handover for free later.

### 1.6 BarTab + BarTabEntry (the "Roy = 1,1" ledger)
```python
class BarTab(models.Model):
    STATUS = [('OPEN', _('Open')), ('SETTLED', _('Settled')), ('VOID', _('Void'))]
    business      = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='bar_tabs')
    store         = models.ForeignKey(Store, on_delete=models.CASCADE, null=True, blank=True)
    shift         = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='tabs')
    customer_name = models.CharField(max_length=80)  # "Roy" — first-name world
    customer      = models.ForeignKey('Customer', null=True, blank=True,
                                      on_delete=models.SET_NULL)  # optional link → debt module
    served_by     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='tabs_served')
    server_name   = models.CharField(max_length=80, blank=True,
        help_text='Waitress name when she has no login. "Did Roy pay?" is asked '
                  'of THIS person.')
    status        = models.CharField(max_length=8, choices=STATUS, default='OPEN')
    opened_at     = models.DateTimeField(auto_now_add=True)
    settled_at    = models.DateTimeField(null=True, blank=True)

    def total(self): ...
    def unpaid_total(self): ...

class BarTabEntry(models.Model):
    tab         = models.ForeignKey(BarTab, on_delete=models.CASCADE, related_name='entries')
    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE,
                                       related_name='tab_entry')
    description = models.CharField(max_length=80)   # "Kikombe ×2", "Robo ya robo"
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    is_paid     = models.BooleanField(default=False)  # ← the TICK
    paid_at     = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=10, blank=True)  # cash/mpesa set on tick
```
Critical accounting decision: **stock and the envelope move at POUR time; cash
moves at TICK time.** The pour creates the Transaction immediately with
`payment_method='credit'`; ticking the entry updates that Transaction's
`payment_method` to cash/mpesa and stamps `paid_at`. Revenue is therefore never
double-counted and the existing analytics keep working untouched.

Tab end-of-shift outcomes (shift-close screen forces a decision per open tab):
1. Tick remaining entries (customer paid late)
2. Carry tab to next shift (status stays OPEN, relinked to new shift)
3. Convert to Debt — requires picking/creating a Customer; pushes unpaid total
   into the EXISTING Debt Tracker (FIFO, aged buckets, credit score — all free)
4. Void with reason → creates Wastage transaction reversing the pours

### 1.7 Transaction — new fields
```python
keg_barrel = models.ForeignKey('KegBarrel', null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='transactions')
```
`keg_barrel_id` is the discriminator for keg sales — EXACTLY parallel to
`produce_bunch_id` for greens. Update `_units()` in analytics_views.py:
keg sales count cups by dividing qty(ml) by the item's smallest preset volume,
or simply count 1 customer-serving per transaction (consistent with bunch logic —
pick the latter, it's what the bunch path already does). NEVER use `sale_amount`
as a discriminator (set for presets too — commit fbff5b4).

---

## 2. Keg Lifecycle — Flows & Screens

New file `core/keg_views.py` (mirror produce_views.py structure), templates under
`core/templates/core/bar/`. ALL views: `@login_required`, business-scoped,
owner-only actions guarded (receive, tap, discard, tolerance settings).
Remember: decorators DIRECTLY above the view function, no helpers between.

### 2.1 Receive barrel (owner / permitted staff)
"+Pokea Barrel" in the Bar Board (owner-only, same QS_IS_OWNER context pattern):
- Item (keg items only) · How many barrels · Cost per barrel (5000/6000/6500 era —
  free input) · Gross kg (default 60, editable) · Tare kg (default 10)
- Weigh-in confirmation step: "Scale inasoma? __ kg" — if entered ≠ gross default,
  store the ACTUAL reading as gross (short-filled barrels happen; the supplier
  dispute is the owner's, the maths must use reality)
- Target per barrel: prefilled = cost × keg_revenue_multiplier (5000→7500),
  editable. The "nataka 7500 kutoka barrel hii" instruction, digitized.
- Creates N KegBarrel rows (SEALED) + N KegWeightReading(RECEIVE) + ONE Receipt
  Transaction per barrel (qty = net ml, cost captured) so P&L and supply chain
  reports see the purchase.

### 2.2 Tap barrel
Tile action "Fungua barrel" — only one TAPPED barrel per keg item per store at a
time (enforce in view; the pump holds one barrel). Tapping auto-suggests when the
previous one hits DEPLETED.

### 2.3 Sell (staff + owner, the 90% screen)
Keg tile on Bar Board shows: item name, barrels open, envelope remaining (KES),
latest weight, wilting-equivalent "barrel imekaa siku X" if tapped >2 days
(stale keg is a real quality issue). Tap tile → price tiles from presets:
- [Kikombe 70/=] [Jug 210/=] with qty stepper
- Payment row: Cash · M-Pesa · **Tab**
- Tab path: customer first name (autocomplete from this shift's open tabs),
  server (dropdown of staff + free-text waitress name). "2 cups for Roy" =
  two taps, type "Roy", done. Creates/extends BarTab + entries.
- Cart UX identical to greens board: Add stays open, Done closes,
  ↩ Futa undo persists until next add or Done.

### 2.4 Tabs drawer ("Nani hajalipa?")
Slide-over listing OPEN tabs for the current shift: customer, server, entries
with tick checkboxes, unpaid total. Tick one/many → payment method picker →
updates Transactions + entries. Settle-all button per tab. Badge on drawer
button = count of open tabs. Staff sees only; owner sees all + historical.

### 2.5 Weigh & close (shift close / spot check)
"Pima barrel" on each open barrel: enter kg → SPOT or SHIFT_CLOSE reading →
instant mini-report on screen: "Scale: 23.4L imetoka (≈ 3,510/=). Vitabu:
3,290/=. Tofauti: 220/= (6.3%) ⚠". Colour per tolerance. This immediate
feedback is the behavioural lever — staff who know the scale talks pour straight.

### 2.6 Shift handover screen (`/shifts/`)
- Start shift: opening float + confirm latest weight of every open barrel
  (creates SHIFT_OPEN readings, sets confirmed_by on predecessor's SHIFT_CLOSE)
- Close shift: weigh every open barrel, count cash, resolve every open tab
  (the four outcomes from §1.6), then `build_shift_reconciliation` renders the
  handover report. Outgoing staff closes; incoming confirms; owner is notified
  (NotificationRouter event `shift_closed`, channel rules like txn SMS; respect
  the 10-min bundling window) with the one-line verdict:
  "Shift ya Morrine: Keg variance 2.1% ✅ · Cash short 50/= ⚠ · Tabs 480/= open"

### 2.7 Analytics — "🍺 Bar Performance" section (analytics_views.py)
Mirror the Kibanda Produce Performance section:
- Per-barrel table: cost, target, collected, realized markup ×, book-vs-scale
  shrinkage %, days open
- Keg totals: barrels finished, avg markup, total shrinkage KES (the headline)
- Bottles/spirits (regular PORTION items in liquor categories): units, revenue,
  margin % — already computed by existing PORTION analytics, just surfaced here
- Staff league: variance % per staff across shifts (owner-only, sensitively
  worded: "Usahihi wa kumwaga" / pouring accuracy, not "theft ranking")
- Tabs aging: open tab totals by age bucket (reuse debt bucket styling)

---

## 3. Business-Type Profiles (the UI overhaul)

### 3.1 New BusinessType rows (migration 0044, follow 0028's get_or_create style)
- `Bar / Pub (Local Joint)` — keg + bottles + tabs + shifts
- `Club / Lounge` — bottles + tabs + shifts (no keg by default; they have POS but
  small clubs will use ours)
- `Wines & Spirits (Liquor Store)` — bottles only, no tabs/keg/shifts
Keep existing `Liquor Store / Bar` row (businesses point at it) but map it to the
liquor-store profile; settings page nudges bar owners to switch type.

### 3.2 Profile registry — new file `core/business_profiles.py`
Pure-Python config, no DB (catalog is suggestions, not data):
```python
PROFILES = {
  'bar': {
    'match': ['Bar / Pub (Local Joint)'],
    'board': 'bar',            # Quick Sell renders Bar Board
    'modules': {'keg': True, 'tabs': True, 'shifts': True, 'produce': False},
    'vocab': {'sell_button': 'Uza', 'receive': 'Pokea Barrel'},
    'catalog': BAR_CATALOG,
  },
  'liquor_store': {
    'match': ['Wines & Spirits (Liquor Store)', 'Liquor Store / Bar'],
    'board': 'grid', 'modules': {'keg': False, 'tabs': False, 'shifts': False},
    'catalog': LIQUOR_CATALOG,
  },
  'club': {'match': ['Club / Lounge'], 'board': 'grid',
           'modules': {'keg': False, 'tabs': True, 'shifts': True},
           'catalog': LIQUOR_CATALOG},
  'kibanda': {'match': ['Kibanda / Food Stall', 'Mama Mboga / Kiosk',
              'Vegetable & Produce Stall'], 'board': 'produce',
              'modules': {'produce': True}, 'catalog': KIBANDA_CATALOG},
  'butchery': {'match': ['Butchery & Abattoir', 'Nyama Choma Joint'],
               'board': 'grid', 'catalog': BUTCHERY_CATALOG},
  'cereals': {'match': ['Cereal & Grain Shop', 'Posho Mill'],
              'board': 'produce', 'catalog': CEREALS_CATALOG},
  'fish':    {'match': ['Fish Monger', 'Fish Farm / Aquaculture'],
              'board': 'grid', 'catalog': FISH_CATALOG},
  'water':   {'match': ['Water Refilling / Dispensing Point'],
              'board': 'grid', 'catalog': WATER_CATALOG},
  # default profile: board='grid', empty catalog, all modules off
}
def get_profile(business): ...  # match by business_type.name, fallback DEFAULT
```
Helper context processor (or mixin) injects `biz_profile` into every template;
Quick Sell view branches on `profile['board']`; navbar shows Shifts/Tabs links
only when the module flag is on. NO behaviour removed from anyone — grid +
existing features remain the universal default.

### 3.3 Item catalogs (auto-sense unit + mode + presets)
Each catalog entry:
```python
{'name': 'Senator Keg Dark', 'unit': 'Ml', 'is_keg': True,
 'presets': [('Kikombe 300ml', 70, 300), ('Jug', 210, 1250)]},
{'name': 'Chrome Gin 250ml (Quarter)', 'unit': 'Btl', 'volume_ml': 250,
 'presets': [('Mzima', None, 1.0), ('Nusu ya robo', None, 0.5),
             ('Robo ya robo', None, 0.25)]},   # None price = owner fills in
```
**BAR/LIQUOR catalog must include (each spirit in 750/350-375/250 variants where
sold):** Senator Keg Dark, Senator Keg Lite, Guinness Smooth (keg) · Tusker,
Tusker Malt, Tusker Lite, White Cap, Balozi, Pilsner, Guinness, Tusker Cider,
Snapp, Smirnoff Ice, KO · Kibao, Chrome, Konyagi, Kenya Cane (KC), County, Best
Gin, Best Whisky, Hunter's Choice, Triple Ace, Blue Moon, Kane Extra, Captain
Morgan, Gilbey's, Smirnoff, Richot, Viceroy, V&A, Kingfisher, General Meakins,
4th Street, Caprice, Drostdy-Hof · sodas/mixers (300ml & 500ml soda, Delmonte,
water, Predator/RedBull) · cigarettes (SM, Embassy — per stick & per packet
presets, qty 0.05 = 1 of 20 sticks).

**KIBANDA catalog:** lift the entire existing UNIT_MAP (sukuma, nyanya, viazi,
maharagwe, ndengu, mahindi, mchele, unga, sukari, karoti, kabichi, vitunguu,
dhania, pilipili, mangoes, avocado, ndizi…) and add the new Kg entries (§4.1).

**BUTCHERY:** Beef (Kg), Goat/Mbuzi (Kg), Matumbo (Kg), Liver (Kg), Mutton (Kg),
Chicken kienyeji (Pc & Kg), Bones/Supu (Kg) — presets ¼ kg / ½ kg / 1 kg.
**CEREALS:** beans/ndengu/njahi/rice/maize per gorogoro (BATCH from gunia — the
module already built) and per Kg (PORTION).
**FISH:** Tilapia (Pc, size S/M/L presets), Omena (Kg + Gorogoro), Fillet (Kg).
**WATER:** Refill 20L, Refill 10L, Refill 5L, Full bottle+water 20L (Pc).

For the remaining ~60 business types: ship `catalog: []` (default profile).
The architecture makes adding a catalog a 10-line config change — author them
incrementally based on real signups. Do NOT attempt all 70 in one sprint.

### 3.4 Item form integration (item_form.html)
- Replace free-text description with Select2 "tags" mode fed by the profile
  catalog (typing free text still allowed — catalog assists, never restricts)
- Picking a catalog entry auto-fills: unit, is_keg / is_produce + produce_mode /
  volume_ml, and pre-populates the preset formset rows (prices blank where None)
- Keep the existing UNIT_MAP fallback for free-typed names; EXTEND it with bar
  keywords: keg→Ml+keg-mode-hint, mzinga/750→Btl+volume 750, quarter/robo/250→
  Btl+250, half/350→Btl+375
- The cost-price field behaviour changes per §4.2

---

## 4. Kibanda Module Fixes (folded into this overhaul)

### 4.1 Kilograms — bought per kg, sold per kg or per piece
No new mode needed. Decision tree update (document in item form helper text and
the +From Market modal):
- Sold by weight (nyanya kg, vitunguu kg, omena, sugar loose) → **PORTION,
  unit=Kg**, presets `1 Kg / Nusu kg (0.5) / Robo kg (0.25)`. The existing
  +From Market PORTION path already works: "kgs received + total cost" →
  Receipt + cost_price = total/kgs. Just add Kg to the modal's unit handling
  and UNIT_MAP entries (`nyanya kg`, `vitunguu kg`, `omena`, `sukari kg`).
- Bought per kg, sold per PIECE, count known after arranging (e.g. she weighs
  5kg of mangoes then arranges 22 pieces) → PORTION unit=Pcs; From Market modal
  gains an optional "I'll count pieces" toggle on the PORTION path: kgs+cost
  entered, then "vipande ngapi?" → Receipt qty = piece count, cost_price =
  total/pieces.
- Bought per kg, sold by price points, count NEVER known → BATCH (already built).
The key question stays the same: "Do you know the count before selling?"

### 4.2 Cost-price confusion in item form
When `is_produce` is checked (any mode) OR `is_keg` is checked:
- HIDE the cost_price input entirely
- Show in its place: `<small style="color: #b0b0b0">Bei ya kununua inarekodiwa
  kila unapopokea mzigo — tumia "+From Market" / "+Pokea Barrel".</small>`
- The form save must not blank an existing cost_price when hidden (exclude the
  field from cleaned_data writes when hidden, don't post an empty value)
Plain items keep the field exactly as today.

---

## 5. Sprint Plan (each independently deployable)

| # | Scope | Touches |
|---|---|---|
| 1 | Models + migration 0043 (Item flags, Business fields, KegBarrel, KegWeightReading, Shift, BarTab, BarTabEntry, Transaction.keg_barrel) + admin registrations + exclude is_keg from stock grid/produce queries | core/models.py, admin.py, stock list views |
| 2 | Keg lifecycle: receive/tap/weigh/discard views + Bar Board UI + sell flow (cash & M-Pesa only) + board API endpoint `GET /stock/bar/board/` | core/keg_views.py, urls, templates/core/bar/ |
| 3 | Tabs: BarTab CRUD, tab payment path in sell flow, tabs drawer, tick-to-pay, convert-to-debt integration | keg_views.py, quick sell templates, debt module touchpoint |
| 4 | Shifts: start/close/confirm, reconciliation service + handover report, owner notifications | core/shift_views.py, NotificationRouter event |
| 5 | Business profiles: 0044 business types, business_profiles.py, context processor, Quick Sell branching, navbar gating, item-form Select2 catalog + auto-fill | settings nav, item_form.html, quick sell view |
| 6 | Kibanda fixes (§4) + Analytics "🍺 Bar Performance" + staff league + tabs aging | item_form, produce From-Market modal, analytics_views.py |

Sprint-boundary tests to run every time: existing produce board unaffected;
`_units()` unchanged for produce; a non-bar business sees zero new UI; staff
account (Morrine) cannot see receive/tap/discard/tolerance.

---

## 6. Assumptions Made — Roy to Confirm or Correct

1. **Pour now, pay later**: tabs create credit Transactions at pour, flipped on
   tick. (Alternative — no transaction until payment — breaks weight reconciliation.)
2. **Waitresses without logins** are a free-text `server_name`; staff with logins
   use `served_by`. Both supported.
3. **One tapped barrel per keg item per store** (one pump per beer type).
4. Variance tolerance default **3%** of expected revenue; owner-editable in
   Business Settings.
5. **1 kg = 1 L** for keg (beer density ~1.01 — within scale error).
6. Manual weight entry (no smart scale integration) — keypad + big numerals.
7. Barrel auto-closes only when envelope met AND weight ≈ tare; otherwise owner
   closes manually (handles over-target generous barrels and 50/= upcountry
   pricing where target may be missed on an empty barrel — closing then records
   the shortfall honestly rather than hiding it).
8. Upcountry smaller cups: just another preset (e.g. "Kikombe ndogo 50/=", 250ml).
9. Club profile ships without keg; toggleable later if a club asks.

