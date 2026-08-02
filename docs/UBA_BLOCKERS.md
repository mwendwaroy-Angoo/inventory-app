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
