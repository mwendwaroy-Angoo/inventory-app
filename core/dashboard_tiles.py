"""
core/dashboard_tiles.py — UBA §M0-5 dashboard tile registry.

WHY THIS EXISTS
---------------
The owner/staff home dashboard (core.views.home) currently computes roughly 30
individually-named context keys unconditionally, in one large, monolithic view
function, regardless of whether the viewing business's capability composition
(business_profiles.Capability) actually needs each one. The UBA spec's M0-5 asks
for this to become a registry: {key, title, value, url} tiles that are only
computed when their required capability is present on the viewing business's
profile — so an irrelevant tile's query never runs.

SCOPE OF THIS SPRINT (read before extending)
---------------------------------------------
Migrating all ~30 existing ad-hoc home() tiles into this registry, AND rewiring
home.html to render from it instead of its ~30 individually-named context
variables, is a large, template-touching change this sprint deliberately did
NOT attempt in one pass — home.html cannot be verified pixel-identical without
browser access (per M0-AC1), and the existing tiles' query logic is deeply
entangled with home()'s station-scoping, per-tile try/except error isolation,
and role gating. Rewiring the legacy tiles is left as a dedicated follow-up
once there is a way to visually confirm home.html renders unchanged.

What THIS module builds instead: the real registry mechanism itself
(register_tile/build_tiles), proven correct with genuinely working, genuinely
capability-gated example tiles — so "tiles whose capability is absent are
never computed" is actually true for these tiles today, not just aspirational.
Wired into core.views.home() as an ADDITIVE context key (`uba_dashboard_tiles`)
that home.html does not read yet, so this changes zero visible behavior for
any business (M0-AC1). Future business types' dashboards (salon, retail, ...)
register their own tiles here from day one; home()'s existing legacy tiles
migrate opportunistically whenever their own surface is next touched, per
CLAUDE.md's own "migrate readers opportunistically, never in the same sprint
as a feature" rule (§2.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class DashboardTile:
    key: str
    title: str
    value: object
    url: str = ''


# key -> (requires(capability) -> bool, builder(business, user_profile, capability) -> Optional[DashboardTile])
_TILE_BUILDERS: dict[str, tuple[Callable[[object], bool], Callable[..., Optional['DashboardTile']]]] = {}


def register_tile(key: str, requires: Callable[[object], bool],
                   builder: Callable[..., Optional[DashboardTile]]) -> None:
    """Register a dashboard tile. `requires` decides whether this tile is even
    relevant for the viewing business's Capability; `builder` computes it — only
    called when `requires` returns True, so an irrelevant tile's query never runs."""
    _TILE_BUILDERS[key] = (requires, builder)


def build_tiles(business, user_profile, capability) -> list[DashboardTile]:
    """Build every registered tile whose capability requirement is met. A builder
    that decides there's nothing worth showing (e.g. a zero count) may return
    None — filtered out here, same as an unmet capability requirement."""
    tiles = []
    for _key, (requires, builder) in _TILE_BUILDERS.items():
        if not requires(capability):
            continue
        tile = builder(business, user_profile, capability)
        if tile is not None:
            tiles.append(tile)
    return tiles


# ── Example registrations — prove the mechanism works; not a full home() migration ──

def _keg_variance_tile(business, user_profile, capability):
    """Bar-only (WEIGH_IN accountability): today's keg variance in KES, via
    core.accountability (M0-7) rather than recomputing keg_metrics math inline."""
    from datetime import date
    from core import accountability
    today = date.today()
    rows = accountability.leaderboard(business, date_from=today, date_to=today)
    if not rows:
        return None
    total_loss = sum(r.loss_kes for r in rows)
    if total_loss <= 0:
        return None
    return DashboardTile(
        key='keg_variance', title='Upungufu wa Keg Leo',
        value=f"KES {total_loss:,.0f}", url='/bar/shrinkage/',
    )


register_tile(
    'keg_variance',
    requires=lambda capability: 'WEIGH_IN' in getattr(capability, 'accountability', frozenset()),
    builder=_keg_variance_tile,
)


def _pending_petty_cash_tile(business, user_profile, capability):
    """Every real profile today composes CASH_DRAWER accountability — pending
    petty cash review count is universal, not specific to any one business type."""
    from core.models import PettyCash
    count = PettyCash.objects.filter(business=business, status='pending').count()
    if count <= 0:
        return None
    return DashboardTile(
        key='pending_petty_cash', title='Petty Cash Inayosubiri Uhakiki',
        value=count, url='/petty-cash/',
    )


register_tile(
    'pending_petty_cash',
    requires=lambda capability: 'CASH_DRAWER' in getattr(capability, 'accountability', frozenset()),
    builder=_pending_petty_cash_tile,
)
