# UBA Progress Log

Tracks execution of `docs/UBA_EXECUTION_ORDER.md` against `docs/UBA_MASTER_SPEC.md`.
Append one row per sprint, in the house style established in `CLAUDE.md`'s Sprint Status Log.
Never re-verify an already-DONE sprint — trust this log.

| Sprint | Status | Commits | Date | Notes |
|---|---|---|---|---|
| 0 | DONE | 32ccc6e | 2026-08-02 | Persisted `docs/UBA_MASTER_SPEC.md` (1664 lines) and `docs/UBA_EXECUTION_ORDER.md` (272 lines) verbatim via `cp` + `diff` confirmation (no transcription risk from a partial Read). Created this progress log and `docs/UBA_BLOCKERS.md`. Appended a UBA pointer to `CLAUDE.md`'s Coding Preferences section. Also folded in prior session's work as the M0 baseline: `Item.stock_model` (migration 0140, commit `cad33e2`) and the `Capability` registry in `business_profiles.py` (commit `96eb454`) were built in the session immediately before this standing order was issued — both already fast-forwarded onto `main` before Sprint 0 began (see `git log`). Execution order §4 M0-3→M0-7 explicitly names these two as already done. |
| M0-4 | DONE | 6dfab6b | 2026-08-02 | Vocabulary layer: `core/templatetags/uba_extras.py`'s `vocab` filter reading `biz_profile.capability.vocabulary`. Deviates slightly from the spec's illustrative `{{ 'item'|vocab }}` syntax — plain Django filters cannot read template context, so the profile must be passed explicitly: `{{ 'item'|vocab:biz_profile }}` (documented in the filter's docstring). No-op today: all 8 real profiles have an empty `vocabulary` dict, and nothing calls the filter yet. Also backfilled dedicated tests for the prior session's M0-1 (`Item.stock_model` sync) and M0-2 (Capability registry wiring) work, which shipped without tests. 12 new tests, 1143 total, OK. |
