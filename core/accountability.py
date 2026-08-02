"""
core/accountability.py — UBA §2.3 Accountability Engine.

The bar module already built the hardest, most valuable part of this platform:
book-vs-scale variance with staff attribution, a learned normal-loss baseline, and an
honest coverage_pct so nobody is accused off a partially-measured figure. That logic
lives in core/keg_metrics.py. This module is the generalisation the UBA spec calls for
in §2.3 — a single, business-type-agnostic contract every future variance type (Kibanda
envelope depletion, Kitchen recipe yield, Retail cycle counts, Salon supply variance,
Rental asset returns...) can register into, instead of each one growing its own bespoke
report page the way keg/produce/kitchen did.

WHAT THIS IS NOT: a rewrite of keg_metrics.py. Per the execution order's explicit
instruction ("wrapping and re-exporting the existing keg_metrics.py functions so no bar
code changes"), core/keg_metrics.py and every view that calls it (keg_views.py,
shift_views.py, etc.) are completely untouched. This module only ADDS a thin,
business-type-agnostic facade on top — nothing currently calls it, so it changes no
behaviour for any business today.

The five-step model every variance type follows:
    1. ESTABLISH — a measurable opening position   (weight / count / meter / asset roll)
    2. EXPECT    — what SHOULD have happened        (sales x recipe/preset/portion)
    3. OBSERVE   — a measurable closing position     (weight / count / meter / asset roll)
    4. ATTRIBUTE — variance -> shift -> staff         (StaffShrinkage)
    5. SURFACE   — leaderboard / Z-report / alert / learned baseline

New variance types register via `register_engine(scope, fn)` and are then reachable
through the one shared `variance_for(scope, ...)` entry point. Only 'keg_shift' is
registered today — the exact bar-module math, wrapped, not reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal
from typing import Callable, Optional

from core import keg_metrics
# Re-exported so `from core.accountability import BarrelVariance, staff_shrinkage, ...`
# works identically to importing from core.keg_metrics — existing bar code keeps
# importing from keg_metrics directly and is completely unaffected either way.
from core.keg_metrics import (  # noqa: F401
    BarrelVariance,
    ShiftBarrelVariance,
    StaffShrinkage,
    barrel_variance,
    business_cup_pool,
    business_keg_loss_baseline,
    kitchen_consumable_pool,
    shift_barrel_variance,
    staff_shrinkage,
    variance_flag,
)


def _dec(value) -> Decimal:
    """Cast to Decimal via str() — never float(Decimal(...)) or Decimal(float), which
    either raises TypeError (CLAUDE.md Known Issues) or introduces float noise."""
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


@dataclass
class VarianceResult:
    """UBA §2.3's stable, business-type-agnostic variance contract.

    coverage_pct and is_partial are the "honesty fields" carried over from the bar
    module's own hardened rules: a variance type must never rank a staff member worst
    just because they happened to be more measured than someone else, and a KES loss
    figure must always be aggregated in KES, never as a mean of percentages.
    """
    expected: Decimal
    actual: Decimal
    variance: Decimal              # actual - expected (negative = loss)
    variance_kes: Decimal
    variance_pct: Optional[Decimal]
    baseline_pct: Optional[Decimal]  # learned normal loss (F3 pattern) — None until learned
    flag: str                        # 'ok' | 'warning' | 'danger'
    coverage_pct: Decimal            # % of value actually measured
    is_partial: bool                 # True when coverage < 100


# ──────────────────────────────────────────────────────────────────────────────
# Engine registry — new variance types register here as they're built
# ──────────────────────────────────────────────────────────────────────────────

_ENGINES: dict[str, Callable[..., Optional[VarianceResult]]] = {}


def register_engine(scope: str, fn: Callable[..., Optional[VarianceResult]]) -> None:
    """Register a variance engine under `scope`. `fn` must accept the same keyword
    arguments as `variance_for` (business, store, shift, date_from, date_to) and return
    a VarianceResult or None (None means "not enough data to compute a result yet" —
    e.g. no weight reading brackets the window — never an error)."""
    _ENGINES[scope] = fn


def variance_for(scope: str, *, business, store=None, shift=None,
                  date_from=None, date_to=None) -> Optional[VarianceResult]:
    """Dispatch to the engine registered for `scope`. Returns None for an
    unregistered scope rather than raising — callers should treat that as "no
    accountability engine built for this business type yet", not an error."""
    engine = _ENGINES.get(scope)
    if engine is None:
        return None
    return engine(business=business, store=store, shift=shift, date_from=date_from, date_to=date_to)


def leaderboard(business, *, store=None, date_from: date_type, date_to: date_type) -> list:
    """UBA §2.3 leaderboard() — today delegates entirely to
    keg_metrics.staff_shrinkage(), the only variance type built so far. `store` is
    accepted (per UBA §5's eventual store scoping) but not used yet: staff_shrinkage()
    itself isn't store-scoped — that's a Sprint M1 concern, not this one."""
    return keg_metrics.staff_shrinkage(business, date_from, date_to)


# ──────────────────────────────────────────────────────────────────────────────
# 'keg_shift' engine — the bar module's own per-(shift x barrel) variance, wrapped
# ──────────────────────────────────────────────────────────────────────────────

def _keg_shift_engine(*, business, store, shift, date_from=None, date_to=None) -> Optional[VarianceResult]:
    """`store` here is the KegBarrel instance for this (shift x barrel) window — kept
    under the generic `store` kwarg name so future scopes (a ProduceBunch, a
    KitchenBatch, a StockCountSession...) share the same call shape. Wraps
    keg_metrics.shift_barrel_variance() verbatim; no math is reimplemented here."""
    if shift is None or store is None:
        return None
    sbv = keg_metrics.shift_barrel_variance(shift, store)
    if sbv is None:
        return None

    expected = _dec(sbv.book_ml)
    actual = _dec(sbv.scale_ml) if sbv.scale_ml is not None else expected
    variance = _dec(sbv.variance_ml) if sbv.variance_ml is not None else Decimal('0')
    variance_kes = _dec(sbv.wastage_kes) if sbv.wastage_kes is not None else Decimal('0')

    variance_pct = None
    flag = 'ok'
    tolerance_pct = float(getattr(business, 'keg_variance_tolerance_pct', 10) or 10)
    if sbv.has_weight and sbv.book_ml:
        variance_pct = _dec(abs(sbv.variance_ml) / sbv.book_ml * 100.0) if sbv.book_ml else Decimal('0')
        flag = keg_metrics.variance_flag(float(variance_pct), tolerance_pct)

    baseline = keg_metrics.business_keg_loss_baseline(business)
    baseline_pct = _dec(baseline['baseline_pct']) if baseline.get('is_learned') else None

    return VarianceResult(
        expected=expected,
        actual=actual,
        variance=variance,
        variance_kes=variance_kes,
        variance_pct=variance_pct,
        baseline_pct=baseline_pct,
        flag=flag,
        coverage_pct=Decimal('100') if sbv.has_weight else Decimal('0'),
        is_partial=not sbv.has_weight,
    )


register_engine('keg_shift', _keg_shift_engine)


# ──────────────────────────────────────────────────────────────────────────────
# 'fitting_room' engine — UBA §8.4 (Sprint A3), the second real caller for this
# module's `attribute()`-shaped contract that M0-7's docstring deferred until a
# second engine actually needed it (produce envelope / kitchen recipe / retail
# cycle count were all named candidates — a boutique fitting-room count turned
# out to be the one that landed first).
# ──────────────────────────────────────────────────────────────────────────────

def _fitting_room_engine(*, business, store, shift, date_from=None, date_to=None) -> Optional[VarianceResult]:
    """`store` here is the FittingRoomLog instance for one staff/shift count —
    kept under the generic `store` kwarg name for the same reason
    `_keg_shift_engine` does: every future scope shares one call shape.
    pieces_out is "expected back", pieces_back is "actual" — a shortfall
    (pieces taken into the fitting room that never came back) is the
    variance this exists to surface, no learned baseline yet (no prior
    sprint built one for this scope, unlike keg's F3 baseline)."""
    if store is None:
        return None
    log = store
    expected = _dec(log.pieces_out)
    actual = _dec(log.pieces_back)
    variance = actual - expected
    flag = 'ok' if variance == 0 else ('warning' if abs(variance) <= 1 else 'danger')
    return VarianceResult(
        expected=expected, actual=actual, variance=variance,
        variance_kes=Decimal('0'), variance_pct=None, baseline_pct=None,
        flag=flag, coverage_pct=Decimal('100'), is_partial=False,
    )


register_engine('fitting_room', _fitting_room_engine)
