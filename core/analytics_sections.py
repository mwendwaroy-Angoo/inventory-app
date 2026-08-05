"""
core/analytics_sections.py — UBA §M0-6 analytics section registry.

WHY THIS EXISTS
---------------
`core.analytics_views.analytics_dashboard()` computes every analytics section
("Kibanda Produce Performance", "Bar Performance", "Kitchen Performance", ...)
inline, in one ~975-line view function, gated by ad-hoc presence checks
(`if greens_items:`, `if keg_item_rows:`, etc.) rather than by a business
type's actual capability composition. CLAUDE.md itself calls this an
"existing bleed risk" — nothing stops a kitchen batch item from accidentally
showing up in Kibanda Produce Performance if its discriminators are ever set
inconsistently, since section membership is inferred from query results, not
declared from the business's capability.

SCOPE OF THIS SPRINT (read before extending)
---------------------------------------------
Migrating all of `analytics_dashboard()`'s existing sections into this
registry, AND rewiring `analytics.html` to render from it instead of its
~15 individually-named context variables, is a large, template-touching
change this sprint deliberately did NOT attempt in one pass —
`analytics.html` cannot be verified pixel-identical without browser access
(per M0-AC1), and several existing sections share query results with other
parts of the view (period comparisons, forecasting) in ways that would need
careful untangling. Rewiring the legacy sections is left as a dedicated
follow-up, same discipline as M0-3 (item form) and M0-5 (dashboard tiles).

What THIS module builds instead: the real registry mechanism itself
(register_section/build_sections), proven correct with one genuinely
working, genuinely capability-gated example section — `keg_shrinkage`,
which reuses `core.accountability.leaderboard()` (M0-7) rather than
reimplementing keg_metrics math, so its correctness rides on an
already-tested wrapper instead of new business logic. Wired into
`analytics_dashboard()` as an ADDITIVE context key
(`uba_analytics_sections`) that `analytics.html` does not read yet, so
this changes zero visible behavior for any business (M0-AC1). Future
business types' analytics (Retail Performance, Salon Performance, ...)
register their own sections here from day one; the existing sections
migrate opportunistically whenever their own surface is next touched, per
CLAUDE.md's own "migrate readers opportunistically, never in the same
sprint as a feature" rule (§2.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Callable, Optional


@dataclass
class AnalyticsSection:
    key: str
    title: str
    rows: list = field(default_factory=list)


# key -> (requires(capability) -> bool,
#         builder(business, capability, date_from, date_to) -> Optional[AnalyticsSection])
_SECTION_BUILDERS: dict[str, tuple[Callable[[object], bool], Callable[..., Optional['AnalyticsSection']]]] = {}


def register_section(key: str, requires: Callable[[object], bool],
                      builder: Callable[..., Optional[AnalyticsSection]]) -> None:
    """Register an analytics section. `requires` decides whether this section is
    even relevant for the viewing business's Capability; `builder` computes it —
    only called when `requires` returns True, so an irrelevant section's queries
    never run (the exact bleed-risk fix this sprint targets: a section can only
    appear for a business whose capability actually composes it)."""
    _SECTION_BUILDERS[key] = (requires, builder)


def build_sections(business, capability, date_from: date_type, date_to: date_type) -> list[AnalyticsSection]:
    """Build every registered section whose capability requirement is met. A
    builder that decides there's nothing to show (e.g. no rows in the period)
    may return None — filtered out here, same as an unmet requirement."""
    sections = []
    for _key, (requires, builder) in _SECTION_BUILDERS.items():
        if not requires(capability):
            continue
        section = builder(business, capability, date_from, date_to)
        if section is not None:
            sections.append(section)
    return sections


# ── Example registration — proves the mechanism works; not a full analytics migration ──

def _keg_shrinkage_section(business, capability, date_from, date_to):
    """Bar-only (WEIGH_IN accountability): the staff shrinkage leaderboard, via
    core.accountability (M0-7) rather than a fresh query against keg_metrics."""
    from core import accountability
    rows = accountability.leaderboard(business, date_from=date_from, date_to=date_to)
    if not rows:
        return None
    return AnalyticsSection(key='keg_shrinkage', title='Upungufu wa Keg (Leaderboard)', rows=rows)


register_section(
    'keg_shrinkage',
    requires=lambda capability: 'WEIGH_IN' in getattr(capability, 'accountability', frozenset()),
    builder=_keg_shrinkage_section,
)
