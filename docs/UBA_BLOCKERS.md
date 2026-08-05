# UBA Blockers

Genuine blockers hit while working `docs/UBA_EXECUTION_ORDER.md`'s queue — missing credentials,
external services needed, or an ambiguity the spec does not resolve. Per the execution order's
standing rule: never silently skip or reinterpret a sprint. If blocked, log it here, commit, and
move to the next *unblocked* sprint.

Known blockers carried over from the execution order's §5 (route around these, do not fight them):

| Blocker | Effect | Action |
|---|---|---|
| Render free tier filesystem is ephemeral | Uploaded images vanish on redeploy | Build A3, L2, X2 **without photo upload**. Leave `ItemPhoto` unbuilt. Roy must set up Cloudinary or R2 before photo sub-tasks and Phase 6 |
| No Celery / no scheduler | Rent roll, ABC reclass, digests, price aggregation have nothing to run them | Use idempotent management commands safe under Render Cron, or a lazy generate-on-first-view guard, copying the existing `RecurringExpense` period-review pattern |
| Free tier cold starts (~40s) | A public storefront will lose customers | Build W1–W3 but flag that they must not be announced to customers until hosting is upgraded |
| SMS cost | New alert classes multiply spend | Every new alert class gets an on/off switch on `Business`. One owner digest per day maximum. Respect the existing 10-minute bundling window |
| Security follow-up | Historic committed SQLite with real credential hashes | Verify purged from git *history*, not just HEAD; confirm `SECRET_KEY` is set in Render env vars, not the hardcoded fallback. Report findings here; do not rotate anything without Roy |

---

## Sprint-specific blockers encountered

(none yet — append below as they're hit, in the format: `### <Sprint ID> — <one-line summary>`
followed by what's blocking it, what was tried, and which unblocked sprint work moved to instead)

### R1–R4/X1/P0-B — real `minimart`/`apparel` profile registration deliberately deferred

Not a blocker in the sense of "cannot proceed" — every mechanism built in these sprints (barcode/
GlobalProduct, margin guard, sale-below-cost, returns, cycle counting, payables, cash position
tile, payment plans) is business-type-agnostic and reachable by ANY business today regardless of
`business_profiles.py` registration, since none of them gate behind `capability.modules`/`hides`.
Logged here because it's a real gap worth a future session's deliberate attention rather than a
silent omission.

**What was found**: pre-answered decision #1 says "new profiles to add: minimart, apparel, salon,
rental_property, rental_equipment, pharmacy, hardware, electronics, gas" — but the REAL seeded
`BusinessType` rows (`core/migrations/0006_seed_business_types_counties.py`) already include
"Retail Shop", "Wholesale", "Supermarket", "Hardware Store", "Clothing & Apparel" as LIVE business
types real businesses may already be registered under. Registering a new `'minimart'`/`'apparel'`
`PROFILES`/`CAPABILITIES` entry with `match=` pointing at these real names — unlike M0-AC3's
deliberately-inert, non-colliding stub — WOULD be a real, live behavior change for any existing
business already using that type (different `modules` dict, potentially populated `hides`/
`vocabulary` where none existed before), exactly the class of regression the M0 AC gate
("existing businesses see zero visible change") exists to prevent.

**Decision made**: do not register these profiles against the real business type names as a side
effect of building any one mechanism. This should be its own dedicated rollup sprint — once a
phase's mechanisms are proven, register the real profile with its own AC gate ("every existing
business currently under this type sees zero unexpected visible change") — not bundled into R1,
A1, or any other single-mechanism sprint. Route around it: keep building every A1–A3 (and later
S1–S3, L1–L3, etc.) mechanism as a general, type-agnostic feature exactly like Phase 1's, so
nothing is blocked on the profile-registration decision. Flag for Roy: when ready, a future
session should register `minimart`/`apparel`/`salon`/`rental_property`/`rental_equipment`/
`pharmacy`/`hardware`/`electronics`/`gas` against their real matching `BusinessType` names, with
its own dedicated regression sweep.
