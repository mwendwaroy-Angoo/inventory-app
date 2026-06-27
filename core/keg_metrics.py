"""
core/keg_metrics.py — single source of truth for keg variance / shrinkage math.

WHY THIS EXISTS
---------------
The book-vs-scale variance math currently lives inline in three places:
  - keg_views.keg_barrel_detail()   (per-shift bracketed variance + whole-barrel wastage)
  - keg_views.keg_reconciliation()  (whole-barrel book/scale/wastage)
  - keg_views.weigh_barrel()        (SPOT variance + ok/warning/danger flag)

F2 (shrinkage leaderboard + alerts) and F3 (learned loss baseline) need the EXACT same
numbers. Per CLAUDE.md ("everything is connected — one logical number, one source of truth"),
this module is the canonical implementation. After landing this module, REFACTOR the three
views above to call it instead of recomputing — do not leave parallel copies of the math.

CONVENTIONS (match the existing code)
  - 1 kg of keg beer == 1 L == 1000 ml (Senator Keg density ≈ 1.0). Same assumption as
    KegBarrel.net_volume_l.
  - "book" volume = what sales recorded (Transaction.qty is stored NEGATIVE ml for keg pours,
    so book uses abs()). "scale" volume = ground truth from weight readings.
  - variance_ml > 0  ==>  scale says MORE was poured than sales recorded  ==>  unexplained loss.
  - All money math uses float() casts (the model helpers already return floats); never mix
    Decimal * float (raises TypeError — see CLAUDE.md Known Issues).
  - Voided transactions are excluded from all revenue/book queries (.exclude(payment_method='void')).

VERIFY-ME markers below point at the source lines this was lifted from. Claude Code: diff each
function against the cited block in core/keg_views.py and confirm identical behaviour before
deleting the inline copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Return contracts
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BarrelVariance:
    """Whole-barrel book/scale/wastage snapshot.
    Lifted from keg_reconciliation() per-barrel block + keg_barrel_detail() overall wastage.
    """
    barrel_id: int
    cost: float
    revenue: float
    target: float
    net_vol_l: float
    book_l: float
    scale_l: Optional[float]          # None when no weight reading exists
    variance_l: Optional[float]       # scale_l - book_l ; None without weight
    wastage_l: Optional[float]        # None for TAPPED barrels with no weight
    wastage_kes: Optional[float]
    wastage_pct: Optional[float]
    has_weight: bool
    cups: int = 0
    pints: int = 0
    jugs: int = 0


@dataclass
class ShiftBarrelVariance:
    """One (shift × barrel) window of loss attribution.
    Lifted from the per-shift loop in keg_barrel_detail() (the shift_rows builder).
    """
    shift_id: int
    barrel_id: int
    staff_id: Optional[int]
    staff_name: str
    window_start: object              # aware datetime
    window_end: object                # aware datetime
    book_ml: float
    scale_ml: Optional[float]         # None unless readings bracket the window
    variance_ml: Optional[float]      # scale_ml - book_ml
    wastage_l: Optional[float]
    wastage_kes: Optional[float]
    revenue: float
    cups: int = 0
    pints: int = 0
    jugs: int = 0
    has_weight: bool = False


@dataclass
class StaffShrinkage:
    """One row of the F2 shrinkage leaderboard — aggregated across barrels & shifts."""
    staff_id: Optional[int]
    staff_name: str
    shifts_worked: int = 0
    book_revenue_kes: float = 0.0     # throughput this staff recorded (denominator)
    loss_kes: float = 0.0             # sum of POSITIVE per-window keg losses
    net_variance_kes: float = 0.0     # signed sum (loss minus any overcount), for transparency
    windows_with_weight: int = 0      # how many (shift×barrel) windows had a usable weigh-in
    windows_total: int = 0
    bottle_loss_kes: float = 0.0      # F5: spirits/bottle revenue variance from ShiftStockCount
    void_count: int = 0               # K5.C: tabs voided by this staff in the period
    void_kes: float = 0.0             # K5.C: KES value of voided tabs

    @property
    def total_loss_kes(self) -> float:
        return self.loss_kes + self.bottle_loss_kes

    @property
    def loss_pct(self) -> float:
        # IMPORTANT: aggregate at the KES level, NOT a mean of per-barrel percentages.
        # Each barrel has its own cost rate; averaging percentages distorts. See module notes.
        return (self.loss_kes / self.book_revenue_kes * 100.0) if self.book_revenue_kes else 0.0

    @property
    def coverage_pct(self) -> float:
        """% of this staff's windows that actually had a bracketing weigh-in.
        Low coverage => the loss number is only partially measured; surface it in the UI so the
        owner doesn't over-trust an under-measured figure (see 'Attribution honesty' note)."""
        return (self.windows_with_weight / self.windows_total * 100.0) if self.windows_total else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Primitive: ok / warning / danger flag  (lift from weigh_barrel ~line 587-592)
# ──────────────────────────────────────────────────────────────────────────────

def variance_flag(variance_pct: float, tolerance_pct: float) -> str:
    """'ok' | 'warning' | 'danger'. Identical thresholds to weigh_barrel()."""
    vp = abs(variance_pct)
    if vp <= tolerance_pct:
        return 'ok'
    if vp <= tolerance_pct * 2:
        return 'warning'
    return 'danger'


# ──────────────────────────────────────────────────────────────────────────────
# Whole-barrel summary  (lift from keg_reconciliation ~line 1192-1246
#                        + keg_barrel_detail overall wastage ~line 1423-1432)
# ──────────────────────────────────────────────────────────────────────────────

def barrel_variance(barrel) -> BarrelVariance:
    """Compute the whole-barrel book/scale/wastage snapshot for one KegBarrel.

    `barrel` should be loaded with weight_readings prefetched to avoid an extra query
    (qs.prefetch_related('weight_readings')).
    """
    cost      = float(barrel.cost_price or 0)
    revenue   = float(barrel.revenue_collected or 0)
    target    = float(barrel.target_revenue or 0)
    net_vol_l = float(barrel.net_volume_l)

    book_ml    = float(barrel.volume_dispensed_ml or 0)
    scale_ml   = barrel.weight_implied_dispensed_ml()         # uses latest reading; falls back to gross
    has_weight = bool(barrel.weight_readings.all())

    variance_ml = (scale_ml - book_ml) if has_weight else None

    if barrel.status in ('DEPLETED', 'RETURNED'):
        # Barrel is physically empty: everything that left = net_vol_l. Beyond book = wastage.
        wastage_l = max(0.0, net_vol_l - book_ml / 1000.0)
    elif has_weight:
        wastage_l = max(0.0, scale_ml / 1000.0 - book_ml / 1000.0)
    else:
        wastage_l = None

    wastage_kes = (wastage_l / net_vol_l * cost) if (wastage_l is not None and net_vol_l > 0) else None
    wastage_pct = (wastage_l / net_vol_l * 100.0) if (wastage_l is not None and net_vol_l > 0) else None

    return BarrelVariance(
        barrel_id=barrel.id, cost=cost, revenue=revenue, target=target,
        net_vol_l=net_vol_l, book_l=book_ml / 1000.0,
        scale_l=(scale_ml / 1000.0 if has_weight else None),
        variance_l=(variance_ml / 1000.0 if variance_ml is not None else None),
        wastage_l=wastage_l, wastage_kes=wastage_kes, wastage_pct=wastage_pct,
        has_weight=has_weight,
        cups=barrel.cups_dispensed or 0, pints=barrel.pints_dispensed or 0,
        jugs=barrel.jugs_dispensed or 0,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Per-shift bracketed variance  (lift from keg_barrel_detail shift_rows ~line 1364-1421)
# ──────────────────────────────────────────────────────────────────────────────

def _barrel_lifespan(barrel):
    """(start, end) aware datetimes bounding the barrel's active life.
    Lift from keg_barrel_detail ~line 1341-1344."""
    from django.utils import timezone
    start = barrel.tapped_at or timezone.make_aware(
        timezone.datetime.combine(barrel.received_on, timezone.datetime.min.time())
    )
    end = barrel.closed_at or timezone.now()
    return start, end


def shift_barrel_variance(shift, barrel, readings=None, barrel_txns=None) -> Optional[ShiftBarrelVariance]:
    """Loss attribution for one (shift × barrel) window. Returns None if the shift did not
    overlap the barrel's active life.

    Args:
        shift:        a Shift instance (staff loaded ideally).
        barrel:       a KegBarrel instance.
        readings:     optional pre-sorted (recorded_at ASC) list of this barrel's KegWeightReading
                      rows. Pass it in when looping to avoid N+1.
        barrel_txns:  optional queryset/list of this barrel's keg Transactions. Pass in to avoid N+1.

    NOTE on bracketing: weight_before = last reading at/<= window_start; weight_after = first
    reading at/>= window_end. scale_ml = max(0, (before - after) * 1000). This is only meaningful
    when ONE person held the barrel between the two readings — see 'Attribution honesty' in the
    module-level discussion handed to Roy.
    """
    from django.db.models import Sum
    from django.utils import timezone

    barrel_start, barrel_end = _barrel_lifespan(barrel)

    s_start = shift.started_at
    s_end   = shift.ended_at or timezone.now()
    window_start = max(s_start, barrel_start)
    window_end   = min(s_end, barrel_end)
    if window_start >= window_end:
        return None

    if barrel_txns is None:
        from .models import Transaction
        barrel_txns = Transaction.objects.filter(
            business=barrel.business, keg_barrel=barrel,
        ).exclude(payment_method='void')
    win = barrel_txns.filter(created_at__gte=window_start, created_at__lte=window_end) \
        if hasattr(barrel_txns, 'filter') else \
        [t for t in barrel_txns if window_start <= t.created_at <= window_end]

    if hasattr(win, 'aggregate'):
        cups    = win.filter(keg_serving='cup').aggregate(n=Sum('keg_qty'))['n'] or 0
        pints   = win.filter(keg_serving='pint').aggregate(n=Sum('keg_qty'))['n'] or 0
        jugs    = win.filter(keg_serving='jug').aggregate(n=Sum('keg_qty'))['n'] or 0
        revenue = float(win.aggregate(r=Sum('sale_amount'))['r'] or 0)
        book_ml = abs(float(win.aggregate(v=Sum('qty'))['v'] or 0))
    else:  # in-memory fallback for voided-filtered lists
        cups    = sum(int(t.keg_qty or 0) for t in win if t.keg_serving == 'cup')
        pints   = sum(int(t.keg_qty or 0) for t in win if t.keg_serving == 'pint')
        jugs    = sum(int(t.keg_qty or 0) for t in win if t.keg_serving == 'jug')
        revenue = float(sum(float(t.sale_amount or 0) for t in win))
        book_ml = abs(float(sum(float(t.qty or 0) for t in win)))

    if readings is None:
        readings = list(barrel.weight_readings.order_by('recorded_at'))

    before = [r for r in readings if r.recorded_at <= window_start]
    after  = [r for r in readings if r.recorded_at >= window_end]
    weight_before = float(before[-1].weight_kg) if before else None
    weight_after  = float(after[0].weight_kg) if after else None

    if weight_before is not None and weight_after is not None:
        scale_ml    = max(0.0, (weight_before - weight_after) * 1000.0)
        variance_ml = scale_ml - book_ml
        has_weight  = True
    else:
        scale_ml = variance_ml = None
        has_weight = False

    net_vol_l = float(barrel.net_volume_l)
    cost      = float(barrel.cost_price or 0)
    wastage_l   = (variance_ml / 1000.0) if variance_ml is not None else None
    wastage_kes = (wastage_l / net_vol_l * cost) if (wastage_l is not None and net_vol_l > 0) else None

    return ShiftBarrelVariance(
        shift_id=shift.id, barrel_id=barrel.id,
        staff_id=getattr(shift.staff, 'id', None),
        staff_name=(shift.staff.get_full_name() or shift.staff.username) if shift.staff_id else '—',
        window_start=window_start, window_end=window_end,
        book_ml=book_ml, scale_ml=scale_ml, variance_ml=variance_ml,
        wastage_l=wastage_l, wastage_kes=wastage_kes, revenue=revenue,
        cups=cups, pints=pints, jugs=jugs, has_weight=has_weight,
    )


# ──────────────────────────────────────────────────────────────────────────────
# F2 — Staff shrinkage leaderboard
# ──────────────────────────────────────────────────────────────────────────────

def staff_shrinkage(business, date_from: date_type, date_to: date_type) -> list[StaffShrinkage]:
    """Aggregate weight-implied loss by the staff who held each shift, over [date_from, date_to].

    Strategy: for every Shift overlapping the range, find barrels active during the shift window,
    compute shift_barrel_variance() per (shift × barrel), then fold into per-staff totals.
    Loss is the sum of POSITIVE per-window losses (negative = recorded more than weight, treated
    as 0 loss but kept in net_variance for transparency).

    Returns rows sorted by loss_kes DESC. Prefetch aggressively to avoid N+1.
    """
    from django.utils import timezone
    from .models import Shift, KegBarrel, ShiftStockCount, Transaction

    start_dt = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
    end_dt   = timezone.make_aware(timezone.datetime.combine(date_to,   timezone.datetime.max.time()))

    shifts = (Shift.objects.filter(business=business, started_at__lt=end_dt)
              .filter(models_Q_ended_or_open(start_dt))
              .select_related('staff'))

    # Pre-load barrels + their readings + txns once.
    barrels = list(KegBarrel.objects.filter(business=business)
                   .select_related('item').prefetch_related('weight_readings'))
    readings_by_barrel = {b.id: list(b.weight_readings.order_by('recorded_at')) for b in barrels}
    txns = list(
        Transaction.objects.filter(business=business, keg_barrel__isnull=False)
        .exclude(payment_method='void')
        .only('id', 'keg_barrel_id', 'created_at', 'keg_serving', 'keg_qty', 'sale_amount', 'qty')
    )
    txns_by_barrel: dict[int, list] = {}
    for t in txns:
        txns_by_barrel.setdefault(t.keg_barrel_id, []).append(t)

    acc: dict[Optional[int], StaffShrinkage] = {}
    for shift in shifts:
        sid = getattr(shift.staff, 'id', None)
        row = acc.get(sid)
        if row is None:
            row = StaffShrinkage(
                staff_id=sid,
                staff_name=(shift.staff.get_full_name() or shift.staff.username) if shift.staff_id else '—',
            )
            acc[sid] = row
        row.shifts_worked += 1
        for b in barrels:
            sv = shift_barrel_variance(
                shift, b,
                readings=readings_by_barrel.get(b.id, []),
                barrel_txns=txns_by_barrel.get(b.id, []),
            )
            if sv is None:
                continue
            row.windows_total += 1
            row.book_revenue_kes += sv.revenue
            if sv.has_weight and sv.wastage_kes is not None:
                row.windows_with_weight += 1
                row.net_variance_kes += sv.wastage_kes
                if sv.wastage_kes > 0:
                    row.loss_kes += sv.wastage_kes

    # F5 — bottle/spirits loss from ShiftStockCount for bottle_envelope items
    bottle_counts = list(
        ShiftStockCount.objects
        .filter(shift__business=business,
                shift__started_at__gte=start_dt,
                shift__started_at__lt=end_dt,
                item__bottle_envelope=True)
        .select_related('shift__staff', 'item')
        .prefetch_related('item__portion_presets')
    )
    for sc in bottle_counts:
        sid = getattr(sc.shift.staff, 'id', None)
        row = acc.get(sid)
        if row is None:
            # Staff had only bottle counts, no keg shifts in range
            row = StaffShrinkage(
                staff_id=sid,
                staff_name=(sc.shift.staff.get_full_name() or sc.shift.staff.username)
                            if sc.shift.staff_id else '—',
            )
            acc[sid] = row
        variance_units = float(sc.book_balance) - float(sc.actual_count)
        if variance_units > 0:
            row.bottle_loss_kes += round(
                variance_units * sc.item.bottle_expected_revenue_per_unit(), 2
            )

    # K5.C — void tabs attributed to the staff who served them
    from .models import BarTab, BarTabEntry
    from django.db.models import Sum as _Sum
    void_tabs = list(
        BarTab.objects.filter(
            business=business,
            status='VOID',
            opened_at__gte=start_dt,
            opened_at__lte=end_dt,
            served_by__isnull=False,
        ).annotate(tab_total=_Sum('entries__amount'))
        .values('served_by', 'tab_total')
    )
    for vt in void_tabs:
        sb_id = vt['served_by']
        row = acc.get(sb_id)
        if row:
            row.void_count += 1
            row.void_kes += float(vt['tab_total'] or 0)

    return sorted(acc.values(), key=lambda r: r.total_loss_kes, reverse=True)


def models_Q_ended_or_open(start_dt):
    """Q(ended_at__isnull=True) | Q(ended_at__gt=start_dt) — barrel-detail's overlap filter."""
    from django.db.models import Q
    return Q(ended_at__isnull=True) | Q(ended_at__gt=start_dt)


# ──────────────────────────────────────────────────────────────────────────────
# F3 — Learned per-business loss baseline
# ──────────────────────────────────────────────────────────────────────────────

def business_keg_loss_baseline(business, min_sample: int = 3, default_pct: float = 10.0) -> dict:
    """Learn the bar's HONEST average loss% from fully-sold, depleted barrels.

    Only counts barrels that are trustworthy ground truth:
      - status == 'DEPLETED'
      - revenue_collected >= 0.95 * target_revenue   (it really sold out — count is honest)
      - has at least one weight reading (so scale loss is real, not assumed)

    baseline_pct = mean over those barrels of:  (net_vol_l - book_l) / net_vol_l * 100

    Returns {'baseline_pct': float, 'sample': int, 'is_learned': bool}. Below min_sample,
    baseline_pct = default_pct and is_learned=False (surface "still learning (n/min)" in the UI).

    Call this when a barrel is marked DEPLETED and cache the result on
    Business.keg_loss_baseline_pct (accounts migration in F3) so pages don't recompute.
    """
    from .models import KegBarrel

    qs = (KegBarrel.objects.filter(business=business, status='DEPLETED')
          .prefetch_related('weight_readings'))
    samples = []
    for b in qs:
        target = float(b.target_revenue or 0)
        if target <= 0 or float(b.revenue_collected or 0) < 0.95 * target:
            continue
        if not b.weight_readings.all():
            continue
        net_vol_l = float(b.net_volume_l)
        if net_vol_l <= 0:
            continue
        book_l = float(b.volume_dispensed_ml or 0) / 1000.0
        loss_pct = max(0.0, (net_vol_l - book_l) / net_vol_l * 100.0)
        samples.append(loss_pct)

    if len(samples) >= min_sample:
        return {'baseline_pct': round(sum(samples) / len(samples), 1),
                'sample': len(samples), 'is_learned': True}
    return {'baseline_pct': default_pct, 'sample': len(samples), 'is_learned': False}


# ──────────────────────────────────────────────────────────────────────────────
# K6.C — Business-wide disposable cup pool
# ──────────────────────────────────────────────────────────────────────────────

def business_cup_pool(business) -> dict:
    """Return the business-wide disposable cup stock balance.

    Bought:    SUM(BarCupLog.qty) scoped to business, split by cup_size.
    Consumed:  computed from Transaction cup counts (cups_dispensed stored on each tx)
               PLUS the per-pint/jug multiplier from business.cups_per_pint / cups_per_jug.

    Returns a dict:
        cups_300_bought   int
        cups_500_bought   int
        cups_300_cost     float   (total spend on 300 ml cups)
        cups_500_cost     float
        cups_used         int     (consumed via explicit cup transactions, size agnostic)
        pints_dispensed   int     (sum of keg pints across all open/depleted barrels)
        jugs_dispensed    int
        cups_per_pint     int     (from business config)
        cups_per_jug      int
        cups_from_pints   int     = pints_dispensed × cups_per_pint
        cups_from_jugs    int     = jugs_dispensed  × cups_per_jug
        total_cups_bought int     = cups_300_bought + cups_500_bought
        total_cups_used   int     = cups_used + cups_from_pints + cups_from_jugs
        remaining         int     = total_cups_bought - total_cups_used
        low_stock         bool    remaining < 30
    """
    from django.db.models import Sum
    from .models import BarCupLog, KegBarrel

    logs = BarCupLog.objects.filter(business=business)
    agg_300 = logs.filter(cup_size='300').aggregate(q=Sum('qty'), c=Sum('total_cost'))
    agg_500 = logs.filter(cup_size='500').aggregate(q=Sum('qty'), c=Sum('total_cost'))

    cups_300_bought = int(agg_300['q'] or 0)
    cups_500_bought = int(agg_500['q'] or 0)
    cups_300_cost   = float(agg_300['c'] or 0)
    cups_500_cost   = float(agg_500['c'] or 0)

    # Cups consumed as explicit cup transactions (BarCupLog tracks purchase; cups_dispensed
    # on KegBarrel tracks how many 300ml cups were poured directly as cup sales).
    barrel_agg = (KegBarrel.objects
                  .filter(business=business)
                  .exclude(status='DISCARDED')
                  .aggregate(
                      cups=Sum('cups_dispensed'),
                      pints=Sum('pints_dispensed'),
                      jugs=Sum('jugs_dispensed'),
                  ))
    cups_used     = int(barrel_agg['cups']  or 0)
    pints_total   = int(barrel_agg['pints'] or 0)
    jugs_total    = int(barrel_agg['jugs']  or 0)

    cpp = int(business.cups_per_pint)
    cpj = int(business.cups_per_jug)
    cups_from_pints = pints_total * cpp
    cups_from_jugs  = jugs_total  * cpj

    total_bought = cups_300_bought + cups_500_bought
    total_used   = cups_used + cups_from_pints + cups_from_jugs
    remaining    = total_bought - total_used

    return {
        'cups_300_bought':   cups_300_bought,
        'cups_500_bought':   cups_500_bought,
        'cups_300_cost':     cups_300_cost,
        'cups_500_cost':     cups_500_cost,
        'cups_used':         cups_used,
        'pints_dispensed':   pints_total,
        'jugs_dispensed':    jugs_total,
        'cups_per_pint':     cpp,
        'cups_per_jug':      cpj,
        'cups_from_pints':   cups_from_pints,
        'cups_from_jugs':    cups_from_jugs,
        'total_cups_bought': total_bought,
        'total_cups_used':   total_used,
        'remaining':         remaining,
        # low_stock only fires when cups have actually been logged (bought > 0);
        # when bought == 0 the owner hasn't set up cup tracking yet — not a shortage.
        'low_stock':         total_bought > 0 and remaining < 30,
    }


def kitchen_consumable_pool(business) -> dict:
    """Return the business-wide kitchen consumable stock balances.

    Tracks: 1/4 khaki bags, 1/2 khaki bags, tomato sauce.
    Oil and electricity are shared overheads — excluded from per-batch tracking.

    Bought:   SUM(KitchenConsumableLog.qty) by consumable_type
    Used:     SUM(KitchenBatch.khaki_small_used + khaki_large_used) across all batches

    Returns a dict:
        khaki_small_bought    int
        khaki_large_bought    int
        khaki_small_used      int  (deducted from all batches, any status)
        khaki_large_used      int
        khaki_small_remaining int
        khaki_large_remaining int
        sauce_jerricans_bought float
        khaki_small_low       bool  remaining < 20 and bought > 0
        khaki_large_low       bool  remaining < 20 and bought > 0
    """
    from django.db.models import Sum
    from .models import KitchenConsumableLog, KitchenBatch

    def _bought(ctype):
        return float(KitchenConsumableLog.objects.filter(
            business=business, consumable_type=ctype,
        ).aggregate(q=Sum('qty'))['q'] or 0)

    def _cost(ctype):
        return float(KitchenConsumableLog.objects.filter(
            business=business, consumable_type=ctype,
        ).aggregate(c=Sum('total_cost'))['c'] or 0)

    ks_bought = _bought('KHAKI_SMALL')
    kl_bought = _bought('KHAKI_LARGE')
    sauce_bought = _bought('SAUCE_TOMATO')

    batch_agg = KitchenBatch.objects.filter(business=business).aggregate(
        ks=Sum('khaki_small_used'),
        kl=Sum('khaki_large_used'),
    )
    ks_used = int(batch_agg['ks'] or 0)
    kl_used = int(batch_agg['kl'] or 0)

    ks_bought_i = int(ks_bought)
    kl_bought_i = int(kl_bought)
    ks_rem = ks_bought_i - ks_used
    kl_rem = kl_bought_i - kl_used

    return {
        'khaki_small_bought':    ks_bought_i,
        'khaki_large_bought':    kl_bought_i,
        'khaki_small_used':      ks_used,
        'khaki_large_used':      kl_used,
        'khaki_small_remaining': ks_rem,
        'khaki_large_remaining': kl_rem,
        'sauce_jerricans_bought': float(sauce_bought),
        'khaki_small_cost':      _cost('KHAKI_SMALL'),
        'khaki_large_cost':      _cost('KHAKI_LARGE'),
        'sauce_cost':            _cost('SAUCE_TOMATO'),
        'khaki_small_low':       ks_bought_i > 0 and ks_rem < 20,
        'khaki_large_low':       kl_bought_i > 0 and kl_rem < 20,
    }
