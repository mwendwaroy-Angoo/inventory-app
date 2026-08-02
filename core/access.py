"""
core/access.py — UBA §5.1 store access gate.

One gate, used on the view AND the URL (per the spec's own instruction — a
button hidden in a template is not the same as gating the endpoint; this
codebase's own Bar/Kitchen/Quick-Sell systemic audits found that exact class
of bug repeatedly, see CLAUDE.md's Known Issues). `require_store_access()`
composes WITH the existing `_debt_scope()`/`can_access_kitchen`/
`_station_scope()` logic already in the app — store access is an ADDITIONAL
gate, never a replacement for those.

Nothing calls this yet. Wiring it into existing views (stock list, add
transaction, receipts, shift open, debt views, analytics — per the spec's
own list) is deliberately left for a following, narrower pass: each call
site needs its own care to resolve the right `store` to check against, and
rushing all of them in one pass risks exactly the kind of access-control
regression this mechanism exists to prevent. See docs/UBA_PROGRESS.md's M1
entry.
"""

from django.core.exceptions import PermissionDenied


def require_store_access(profile, store):
    """Raise PermissionDenied if `profile` cannot access `store`.

    A None store is always allowed — there is nothing to scope against
    (e.g. a business-wide report, or a feature that doesn't yet compose
    the store dimension).
    """
    if store is None:
        return
    if not profile.accessible_stores().filter(pk=store.pk).exists():
        raise PermissionDenied("Huna ruhusa ya duka hili.")
