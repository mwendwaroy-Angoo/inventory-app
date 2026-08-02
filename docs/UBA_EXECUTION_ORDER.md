# DUKA MWECHECHE — UBA EXECUTION ORDER
### Standing work order for Claude Code. Read this fully before writing any code.
Issued by Roy · 2026-08-02 · Companion to `DUKA_UNIVERSAL_ARCHITECTURE.md` (the UBA spec)

---

## 0. WHY THIS FILE EXISTS

Roy is working from a phone with limited chat quota and cannot supervise sprint by sprint. You
have **standing authority** to work through the entire queue in §4 autonomously, in order,
without asking for approval between sprints.

Two rules make that safe:
- You **never** batch sprints together. One sprint = one focused unit of work = one or more
  small commits = tests green = pushed = progress log updated. Then the next sprint.
- You **never** silently skip or reinterpret a sprint. If you cannot complete one, you log it
  in `docs/UBA_BLOCKERS.md` with the reason and move to the next *unblocked* sprint.

---

## 1. STANDING ORDERS (apply to every sprint below, without exception)

1. **Push directly to `main`. Do not open pull requests.** Ever. `git add` → descriptive
   commit (`feat:` / `fix:` / `refactor:` prefix) → `git push origin main`.
2. **Before every push:** `python manage.py check` · `python manage.py makemigrations --check`
   · `python manage.py test` (full suite). All must be clean/green. Never push red.
3. **Commits stay small.** If a sprint has natural sub-steps, commit each one separately with
   tests green in between. A sprint may be many commits; a commit may never span two sprints.
4. **Obey `CLAUDE.md` in full.** Especially: never name a variable `_`; never `text-muted`
   (use `style="color: #b0b0b0"`); `btn-gold` not `btn-primary`; no Bootstrap `bg-*` on cards;
   no `{% trans %}` in single-quoted JS strings; no `@login_required` on JSON/AJAX endpoints;
   never `get_or_create` on Customer; cast `float`/`Decimal`; output complete files.
5. **Cause-&-Effect Map first.** Before coding any sprint, write its map (the spec has one for
   every sprint) into the commit message body or the sprint's section in
   `docs/UBA_PROGRESS.md`. Implement every "yes" row. The inverse action is not optional.
6. **Regression sweep before "done".** Grep every reader and writer of any field, helper or
   settings value you changed. Paste the grep evidence into the progress log.
7. **Multi-tenancy + store scoping.** Every queryset scoped to
   `request.user.userprofile.business`, and from Sprint M1 onward also to store where the store
   dimension exists.
8. **Money paths get tests.** Any sprint touching payments, stock balance, debt, revenue or
   deposits must add tests. Never reduce the test count.
9. **After each sprint**, append one line to `docs/UBA_PROGRESS.md` **and** one line to the
   Sprint Status Log at the bottom of `CLAUDE.md`, in the existing house style.
10. **If genuinely blocked** (missing credential, external service needed, an ambiguity the
    spec does not resolve): write the blocker to `docs/UBA_BLOCKERS.md`, commit it, and move to
    the next unblocked sprint. Do not stall waiting for Roy.

---

## 2. SPRINT 0 — Persist the spec (do this first, it costs nothing and saves everything)

1. Create `docs/` if absent.
2. Commit `DUKA_UNIVERSAL_ARCHITECTURE.md` into the repo as **`docs/UBA_MASTER_SPEC.md`**
   (Roy will supply the file; if it is already in the working tree, move it there).
3. Commit this file as **`docs/UBA_EXECUTION_ORDER.md`**.
4. Create `docs/UBA_PROGRESS.md` with a table: `Sprint | Status | Commits | Date | Notes`.
5. Create `docs/UBA_BLOCKERS.md` (empty, with a header).
6. Append to `CLAUDE.md`, in the Coding Preferences section:
   > **UBA architecture:** All post-2026-08 feature work follows `docs/UBA_MASTER_SPEC.md`
   > (capability composition model) and the queue in `docs/UBA_EXECUTION_ORDER.md`. Read both
   > at the start of any session that adds a feature or a business type. Current progress:
   > `docs/UBA_PROGRESS.md`.
7. Commit + push.

**Why:** every future session reads the spec from the repo instead of Roy re-pasting it. This
is the single biggest token saving available.

---

## 3. PRE-ANSWERED DECISIONS

These resolve §17 of the spec so nothing in the queue is blocked on Roy. Treat them as final
unless Roy says otherwise in a later session.

| # | Question | **Decision** |
|---|---|---|
| 1 | The 8 existing profiles | Confirmed: `bar, liquor_store, club, kibanda, butchery, cereals, fish, water`. New profiles to add: `minimart`, `apparel`, `salon`, `rental_property`, `rental_equipment`, `pharmacy`, `hardware`, `electronics`, `gas` |
| 2 | Rentals first: property or equipment | **Property first.** Equipment mode in the same sprint only if it costs no extra models |
| 3 | Market price benchmark (§7.2) | **Split.** Product *names/barcodes/pack sizes* → shared by default, opt-out available. Buying/selling *prices* → **opt-IN only** (`Business.contribute_price_data = False` default), county median, `sample_size >= 5`, never below |
| 4 | Salon variance framing | **"Worth asking about", never an accusation.** No copy may imply theft. Show expected vs actual vs learned baseline and stop there. `Still learning N/3` for the first 3 periods |
| 5 | Layaway forfeiture default | **Refund minus 10% admin fee.** Business-configurable (`Business.layaway_forfeit_policy`: `full_refund` / `minus_percent` / `full_forfeit`), default `minus_percent` at 10. Policy text printed on the deposit receipt at the moment money is taken |
| 6 | Directory / marketplace (W4) | **Do not build.** Skip entirely for now |
| 7 | Pricing tiers | Add `Business.plan = CharField(default='standard', choices=['standard','multi','pro'])` in Sprint M1 as a **dormant hook only**. Gate nothing on it yet |

**Hard boundary, restated:** money never flows through any account Duka Mwecheche controls.
Settlement is always direct to the business's own Till/Paybill/Pochi. Refuse any design that
would make the platform an intermediary — this is a CBK PSP/National Payment System Act
constraint, not a preference.

---

## 4. THE WORK QUEUE

Work top to bottom. Each entry names its spec section — read that section before starting.
Acceptance criteria live in the spec; a sprint is not done until its ACs pass.

### PHASE 0 — FOUNDATION (mandatory, in order)

**M0-3 → M0-7 · Finish the capability refactor** — spec §4
Already done: `Item.stock_model` (migration 0140) and the `Capability` registry. Remaining:
- **M0-3** Item form field gating driven by `capability.hides` / stock models. Vanilla JS only
  on `item_form.html` (no jQuery/Select2 there).
- **M0-4** `vocab` template filter reading `capability.vocabulary`.
- **M0-5** Dashboard tile registry — tiles built in the view from capabilities; tiles whose
  capability is absent are never computed.
- **M0-6** Analytics section registry — same pattern; kills the bleed risk.
- **M0-7** `core/accountability.py` implementing the §2.3 contract, wrapping and re-exporting
  `keg_metrics.py` so no bar code changes.
> **AC gate:** bar, kibanda and kitchen users must see *pixel-identical* screens to today. Any
> visible change is a bug. Grep must show no `business_type ==` comparisons left in templates.

**P0-A · Split tender at checkout** — spec §6.1
Closes the Kibanda partial-payment gap. `SalePayment` tender lines; credit remainder flows
through the **existing** debt path unchanged; `evaluate_credit()` must run on the remainder.
Apply to all four POS surfaces: Quick Sell, produce board, kitchen board, bar board.

**M1 · Store as first-class outlet** — spec §5.1
`Store.store_type/code/is_outlet/manager/targets/geo`; keep `is_kitchen` synced (load-bearing).
`UserProfile.home_store` + `stores` M2M + `accessible_stores()`; `core/access.py`
`require_store_access()` applied on **view AND URL**; session store switcher + context
processor. Also add the dormant `Business.plan` field from §3 above.

**M2 · Stock transfers** — spec §5.2
`StockTransfer` + `StockTransferLine`, gap-free references, dispatch/receive/dispute/cancel,
`Transaction.transfer` FK, **transfers excluded from revenue everywhere** (sweep + tests).

**M3 · Maduka Yangu owner console + BusinessException** — spec §5.3
`/maduka/`, per-store cards sorted problem-first, exception feed, compare view, one daily
digest SMS per owner (suppressed if no sales). All existing ad-hoc alerts migrate to
`BusinessException`.

### PHASE 1 — RETAIL / MINIMART

**R1 · Barcode + GlobalProduct + fast onboarding** — spec §7.2
Honour decision #3: names shared by default, **prices opt-in only**. `balance_confirmed_at`;
unconfirmed items excluded from shrinkage attribution and surfaced honestly via `coverage_pct`.

**R2 · Retail board + margin guard + RETURNS** — spec §7.3
`retail_board.html` (search-first, favourites strip, scanner). Margin guard on cost rise with
one-tap price update. Below-cost block/warn. **Returns primitive (`Transaction.type='Return'`)
— reverses stock, revenue, targets and debt. Tests required.**

**R3 · Cycle counting + retail shrinkage** — spec §7.4
`StockCountSession`/`StockCountLine`, ABC classification, `Item.is_high_risk` watchlist,
attribution weighted across shifts since last count, learned baseline before any accusation.

**X1 · Payables** — spec §12.1
`SupplierInvoice`/`SupplierPayment`, aging mirroring the debt tracker, **cash position tile
(receivables − payables)** on the owner dashboard.

**R4 · Retail intelligence** — spec §7.5
Dead stock by capital tied up (with transfer-to-branch action), "Order ya leo" reorder list
with WhatsApp/SMS draft, basket affinity top-10, hour-of-day heatmap.

### PHASE 2 — APPAREL

**P0-B · Payment plans** — spec §6.2
`PaymentPlan`/`PaymentPlanEntry`. Reserved stock is not available stock — run the
`grep -rn "current_balance\|\.balance" templates/` sweep in full. Deposits are a **liability**,
excluded from revenue, profit and targets until completion. Forfeit policy per decision #5.

**A1 · Variants** — spec §8.2
Parent/child `Item` (NOT a new variant FK on Transaction). Variant matrix creator on
`item_form.html`, vanilla JS. Stock list collapses children under the parent.

**A2 · Bale envelope** — spec §8.3
`ProduceBunch.kind/grade/label`. Keep the model name and `produce_bunch_id` discriminator.
Vocabulary and analytics section title switch by capability — a kibanda must never see bale
wording and an apparel shop must never see mboga wording.

**A3 · Layaway, fitting room, markdown** — spec §8.4
⚠️ **Photos are BLOCKED** until external image storage exists (§5 below). Build A3 without
`ItemPhoto`; log the photo sub-task to `docs/UBA_BLOCKERS.md` and continue.

### PHASE 3 — SALON

**S1 · Services, recipes, supply variance** — spec §9.2
`Service` + `ServiceSupplyLine`. **Use the shadow-Item approach** so receipts, analytics, debt
and targets work unchanged. Add `Transaction.service` and `Transaction.performed_by`.
Variance framing per decision #4 — informational, never accusatory. Free redo consumes
supplies, zero revenue, excluded from the stylist's variance denominator.

**S2 · Bookings and chair queue** — spec §9.3
`Appointment`/`AppointmentService`, `salon_board.html` column-per-stylist, walk-in first-class,
no double-booking, T-24h reminder SMS, no-show tracking, rebooking nudge (`rebook_after_days`).

**S3 · Commission + Haki integration** — spec §9.4
Commission ledger from `performed_by`; wire into the existing Haki contribution ledger,
`SalaryPayment` and Kazi Yangu. Chair rent reuses `RentalAgreement` (build after L1).

### PHASE 4 — RENTALS (property first, per decision #2)

**L1 · Units, agreements, rent roll, C2B matching** — spec §10.3
`RentalUnit`/`RentalAgreement`/`RentalInvoice`/`MeterReading`. **Arrears reuse the debt
tracker entirely — no parallel aging engine.** Rent roll generation idempotent, following the
`RecurringExpense` period-review pattern (no Celery available). C2B `BillRefNumber` →
`RentalUnit.code` → oldest open invoice → receipt → SMS both parties; unmatched refs raise a
`BusinessException` rather than vanishing.

**L2 · Deposits, condition, maintenance, caretaker role** — spec §10.4
Deposit ledger as a liability with itemised deductions. New `role='caretaker'`: readings and
maintenance only, **no payment recording, no portfolio P&L** — enforced on view and URL.
Condition photos blocked until image storage; log and proceed.

**L3 · Rental board + occupancy analytics** — spec §10.5

### PHASE 5 — SUPPLY CHAIN

**X2 · Goods Received Note (three-way match)** — spec §12.2
Ordered vs delivered vs invoiced. Cost variance vs PO feeds the R2 margin guard. GRN posts the
Receipt transactions — one audited path, replacing free-typed receiving.

**X3 · Rider POD + COD reconciliation** — spec §12.3
`DeliveryRun`/`DeliveryStop`, delivery OTP by SMS, cash expected vs remitted → variance →
`BusinessException` + `StaffShrinkage`, undelivered stock returns as an inbound transfer.

### PHASE 6 — STOREFRONT (see blockers in §5 first)

**W1 · Public catalog** — spec §13.2 · **W2 · Orders, reservation, fulfilment** — spec §13.3
**W3 · Customer self-service** — spec §13.4 · **W4 · Directory — SKIPPED** per decision #6

### OPTIONAL, ONLY WHEN A REAL CUSTOMER ASKS
Composed profiles — spec §11: pharmacy (FIFO lot depletion), hardware, phone/electronics
(`ItemSerial`), gas (cylinder deposit), bakery. Half a sprint each once Phase 1 exists.
**Do not build the M-Pesa agent profile** — it sits too close to the CBK PSP boundary.

---

## 5. KNOWN BLOCKERS — do not burn time fighting these

Write these into `docs/UBA_BLOCKERS.md` in Sprint 0 and route around them.

| Blocker | Effect | Action |
|---|---|---|
| **Render free tier filesystem is ephemeral** | Uploaded images vanish on redeploy | Build A3, L2, X2 **without photo upload**. Leave `ItemPhoto` unbuilt. Roy must set up Cloudinary or R2 before photo sub-tasks and Phase 6 |
| **No Celery / no scheduler** | Rent roll, ABC reclass, digests, price aggregation | Use idempotent management commands designed to be safe under Render Cron **or** a lazy generate-on-first-view guard, copying the existing `RecurringExpense` period-review pattern |
| **Free tier cold starts (~40s)** | A public storefront will lose customers | Phase 6 assumes a paid instance. Build W1–W3 but flag in the progress log that they must not be announced to customers until hosting is upgraded |
| **SMS cost** | New alert classes multiply spend | Every new alert class gets an on/off switch on `Business`. One owner digest per day maximum. Respect the existing 10-minute bundling window. Highest-volume senders are S2 booking reminders and L1 rent reminders — make both configurable |
| **Security follow-up** | Historic committed SQLite with real credential hashes | Verify it is purged from git *history*, not just HEAD; confirm `SECRET_KEY` is set in Render env vars and not using the hardcoded fallback. Report findings in the progress log; do not rotate anything yourself |

---

## 6. SESSION HANDOFF PROTOCOL

You will run out of context long before this queue is finished. That is expected and fine.

**At the start of every session:** read `CLAUDE.md`, `docs/UBA_MASTER_SPEC.md`,
`docs/UBA_EXECUTION_ORDER.md` (this file) and `docs/UBA_PROGRESS.md`. Resume at the first
sprint not marked DONE.

**At the end of every sprint**, append to `docs/UBA_PROGRESS.md`:
```
| M1 | DONE | a1b2c3d, e4f5g6h | 2026-08-02 | Store scoping live. Sweep: 34 hits on is_kitchen, all verified. 78 tests pass. Note: shift store FK was already implied — no migration needed. |
```
Include anything the next session would otherwise have to rediscover: root causes found, dead
ends walked, surprises in the existing code. This is the same discipline as the Known Issues
section in `CLAUDE.md` and it is what makes the handoff cheap.

**Never** re-verify already-DONE sprints. Trust the log.

---

## 7. THE ONE THING TO GET RIGHT

Every sprint in this queue exists to answer a single question for a Kenyan business owner:
**"what actually happened in my business while I was not standing in it?"**

If a feature does not stop a leak, replace a paper book, let the owner be absent, or bring in a
shilling — it does not belong in this app, no matter how standard it looks in retail software
elsewhere. When a sprint tempts you to add process, add less. That restraint is the whole point
of the capability model.
