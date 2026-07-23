"""
Sprint 4 — Shift Handover Module.

Flow: staff opens shift (opening float) → sells → closes shift (counts cash + weighs barrels)
      → owner / incoming staff confirms → CONFIRMED.
      Incoming staff opens their shift → confirms barrel weights (SHIFT_OPEN) vs SHIFT_CLOSE.

Reconciliation:
  expected_closing_cash = opening_float + cash_sales_during_shift
  variance = closing_cash_counted − expected_closing_cash
"""
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, KegBarrel, KegWeightReading, Shift, ShiftStockCount, Transaction


def _get_up(request):
    from .views import get_user_profile
    return get_user_profile(request)


def get_active_staff_shift(user_profile, business):
    """Return the caller's open Shift, or None.

    Owner always returns None — meaning "no gate needed, proceed".
    Staff must have their own OPEN shift regardless of business type.
    Use the return value like:
        shift = get_active_staff_shift(up, business)
        if shift is False:      # staff with no open shift
            return error(...)   # block the action
        # proceed (shift is either the Shift object or None for owner)
    Returns:
        None  — caller is owner; no shift gate required
        Shift — caller has an open shift; proceed
        False — caller is staff with no open shift; block
    """
    if getattr(user_profile, 'is_owner_or_manager', False):
        return None
    from .models import Shift
    active = Shift.objects.filter(
        business=business, status='OPEN', staff=user_profile.user
    ).first()
    return active if active else False


# ── Reconciliation helper ─────────────────────────────────────────────────────

def _reconcile(shift):
    """Return a dict of sales totals and cash reconciliation for a shift."""
    end = shift.ended_at or timezone.now()
    txns = Transaction.objects.filter(
        business=shift.business,
        type='Issue',
        created_at__gte=shift.started_at,
        created_at__lte=end,
    )
    # Scope to the correct counter so concurrent bar + kitchen shifts don't bleed.
    # Kitchen staff  → kitchen store only (is_kitchen=True).
    # Bar/general staff → non-kitchen stores (is_kitchen=False).
    # Owner → no store filter: the owner may sell on either board and we must not
    #   exclude their transactions by store type. The is_kitchen filter exists only
    #   to separate concurrent staff shifts; the owner's shift doesn't need it.
    #   Also avoids the INNER JOIN that silently drops items with store=None.
    try:
        staff_role = shift.staff.userprofile.role
    except Exception:
        staff_role = 'staff'

    if staff_role == 'kitchen':
        txns = txns.filter(item__store__is_kitchen=True)
    elif staff_role != 'owner':
        txns = txns.filter(item__store__is_kitchen=False)
    # Revenue per transaction: use sale_amount when set (keg pours, preset Quick Sell),
    # otherwise abs(qty) * selling_price (regular Quick Sell without preset).
    _rev = Case(
        When(sale_amount__isnull=False, then=F('sale_amount')),
        default=Abs(F('qty')) * Coalesce(F('item__selling_price'), Value(0)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    cash_sales   = float(txns.filter(payment_method='cash'  ).aggregate(t=Sum(_rev))['t'] or 0)
    mpesa_sales  = float(txns.filter(payment_method='mpesa' ).aggregate(t=Sum(_rev))['t'] or 0)
    credit_sales = float(txns.filter(payment_method='credit').aggregate(t=Sum(_rev))['t'] or 0)
    total_sales  = cash_sales + mpesa_sales + credit_sales
    offline_adj  = float(shift.offline_sales_amount or 0)
    # expected_cash includes any offline cash that staff declared but didn't enter in the system
    expected_cash = float(shift.opening_float) + cash_sales + offline_adj
    variance = None
    if shift.closing_cash_counted is not None:
        variance = round(float(shift.closing_cash_counted) - expected_cash, 2)
    elapsed_secs = int((end - shift.started_at).total_seconds())
    hours, rem   = divmod(elapsed_secs, 3600)
    mins         = rem // 60
    return {
        'cash_sales':    round(cash_sales, 2),
        'mpesa_sales':   round(mpesa_sales, 2),
        'credit_sales':  round(credit_sales, 2),
        'total_sales':   round(total_sales, 2),
        'expected_cash': round(expected_cash, 2),
        'variance':           variance,
        'elapsed':            f"{hours}h {mins:02d}m",
        'elapsed_mins':       elapsed_secs // 60,
        'offline_adj':        round(offline_adj, 2),
        'offline_sales_note': shift.offline_sales_note or '',
    }


def _tapped_barrels_for_business(business):
    """Return list of dicts for each TAPPED barrel, with last SHIFT_CLOSE reading."""
    barrels = KegBarrel.objects.filter(
        business=business, status='TAPPED'
    ).select_related('item')
    result = []
    for barrel in barrels:
        last_close = KegWeightReading.objects.filter(
            barrel=barrel, reading_type='SHIFT_CLOSE'
        ).order_by('-recorded_at').first()
        result.append({
            'barrel_id':       barrel.id,
            'name':            barrel.item.description,
            'tare_kg':         float(barrel.tare_weight_kg),
            'last_close_kg':   float(last_close.weight_kg) if last_close else None,
            'last_close_net':  round(float(last_close.weight_kg) - float(barrel.tare_weight_kg), 2) if last_close else None,
            'last_close_by':   (last_close.recorded_by.get_full_name() or last_close.recorded_by.username) if last_close and last_close.recorded_by else None,
            'last_close_at':   timezone.localtime(last_close.recorded_at).strftime('%H:%M') if last_close else None,
        })
    return result


# ── Auto-close stale shifts ───────────────────────────────────────────────────

_SHIFT_AUTO_CLOSE_GRACE_HOURS = 2   # grace period after closing time before auto-close fires


def _convert_open_tabs_to_debt_for_shift(shift, business, should_convert):
    """Auto-convert this shift's still-OPEN tabs to debt (Geuza Deni).

    Shared by close_shift() (manual close) and _auto_close_expired_shifts()
    (system safety-net close) — bar-audit finding, 2026-07-19: the auto-close
    path used to skip this entirely, meaning a shift auto-closed because staff
    forgot (precisely the scenario most likely to also have forgotten open
    tabs) left any customer tab silently OPEN forever, with no conversion, no
    notification, and no visibility anywhere — not even in the missed-tasks
    reminder, which only checks stock-take and barrel-weight readings.

    Returns (auto_converted_count, auto_converted_names, open_tabs_list) —
    open_tabs_list covers tabs left OPEN (should_convert=False) so the caller
    can surface them for manual resolution, same shape close_shift() already
    returns to the bar board.
    """
    from .models import BarTab
    from core.models import Customer

    try:
        _is_kitchen_shift = shift.staff.userprofile.role == 'kitchen'
    except Exception:
        _is_kitchen_shift = False
    _tab_source = 'kitchen' if _is_kitchen_shift else 'bar'

    open_tabs = list(
        BarTab.objects.filter(business=business, status='OPEN', source=_tab_source)
        .prefetch_related('entries')
        .select_related('customer')
    )
    auto_converted = 0
    auto_converted_names = []
    for tab in open_tabs:
        if not should_convert:
            continue
        try:
            customer_name = (tab.customer_name or '').strip() or f'Tab #{tab.id}'
            phone = ''
            if tab.customer_id and tab.customer.phone:
                phone = tab.customer.phone

            cust = None
            if phone:
                cust = Customer.objects.filter(business=business, phone=phone).first()
            if cust is None:
                cust = Customer.objects.filter(
                    business=business, name__iexact=customer_name,
                ).first()
            if cust is None:
                cust = Customer.objects.create(
                    business=business, name=customer_name, phone=phone,
                    credit_approved=True,
                )

            unpaid_total = float(tab.unpaid_total())

            for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
                txn = entry.transaction
                txn.recipient = cust.name
                txn.payment_method = 'credit'
                txn.save(update_fields=['recipient', 'payment_method'])

            tab.customer = cust
            tab.status = 'SETTLED'
            tab.settled_at = timezone.now()
            tab.cash_requested_at = None
            tab.save(update_fields=['customer', 'status', 'settled_at', 'cash_requested_at'])
            from core.keg_views import _cancel_pending_transfers_for_tab
            _cancel_pending_transfers_for_tab(tab)
            auto_converted += 1
            auto_converted_names.append(customer_name)

            try:
                from core.credit_policy import notify_owners_of_conversion_risk
                notify_owners_of_conversion_risk(
                    business, cust, _tab_source, unpaid_total,
                )
            except Exception:
                logger.exception(
                    'shift close: credit-risk notify failed for tab %s in shift %s', tab.id, shift.id,
                )
        except Exception:
            logger.exception('shift close: auto-convert failed for tab %s in shift %s', tab.id, shift.id)

    open_tabs_list = []
    for tab in open_tabs:
        if tab.status == 'OPEN':
            opened_at_str = ''
            if tab.opened_at:
                local_dt = timezone.localtime(tab.opened_at)
                opened_at_str = local_dt.strftime('%H:%M')
            open_tabs_list.append({
                'id': tab.id,
                'customer_name': tab.customer_name or '—',
                'opened_at': opened_at_str,
            })

    return auto_converted, auto_converted_names, open_tabs_list


def _auto_close_expired_shifts(business):
    """Close any OPEN shifts that ran past the business's closing time + grace.

    Returns a list of dicts describing what was auto-closed (for toast/notification).
    Does nothing when:
      - business.closing_time is not set (treat as 24-hour / unrestricted)
      - closing_time == opening_time (ambiguous — also treated as 24-hour)
    Handles overnight businesses (opening_time > closing_time, e.g. bar opens
    22:00, closes 02:00) by projecting the close onto the following calendar day.
    """
    closing_time = getattr(business, 'closing_time', None)
    opening_time = getattr(business, 'opening_time', None)

    if not closing_time or closing_time == opening_time:
        return []   # 24-hour or no hours configured — never auto-close

    now_local = timezone.localtime(timezone.now())
    local_tz  = timezone.get_current_timezone()
    grace     = timedelta(hours=_SHIFT_AUTO_CLOSE_GRACE_HOURS)
    is_overnight = closing_time < opening_time  # e.g. opens 22:00, closes 02:00

    open_shifts = list(
        Shift.objects.filter(business=business, status='OPEN').select_related('staff')
    )
    auto_closed = []

    for shift in open_shifts:
        start_local = timezone.localtime(shift.started_at)
        shift_date  = start_local.date()

        # Scheduled close: closing_time on shift_date, or next day for overnight
        close_date   = (shift_date + timedelta(days=1)) if is_overnight else shift_date
        close_naive  = datetime.combine(close_date, closing_time)
        scheduled_close = timezone.make_aware(close_naive, local_tz)

        # If the computed close is at or before shift start, the shift opened in an
        # unusual window (e.g. opened after closing time). Skip — don't auto-close.
        if scheduled_close <= shift.started_at:
            continue

        if now_local >= timezone.localtime(scheduled_close + grace):
            staff_name = shift.staff.get_full_name() or shift.staff.username
            note = (
                f'[Auto-closed at {closing_time.strftime("%H:%M")} — '
                f'business hours ended. Staff may have forgotten.]'
            )
            shift.status     = 'CLOSED'
            shift.ended_at   = scheduled_close
            shift.auto_closed = True
            shift.notes      = (shift.notes + '\n' + note).strip() if shift.notes else note
            shift.save(update_fields=['status', 'ended_at', 'auto_closed', 'notes'])

            # Same tab-to-debt sweep a manual close performs — the system is force-closing
            # because we're already past closing time + grace, so always convert (no
            # "manager_taking_over" concept applies to an unattended auto-close).
            _converted_n, _converted_names, _ = _convert_open_tabs_to_debt_for_shift(
                shift, business, should_convert=True,
            )

            auto_closed.append({
                'shift_id':        shift.id,
                'staff_name':      staff_name,
                'scheduled_close': closing_time.strftime('%H:%M'),
                'tabs_converted':  _converted_n,
            })

    if auto_closed:
        try:
            from accounts.models import UserProfile as _UP
            from core.notifications import create_in_app_notification
            owner_profile = _UP.objects.filter(
                business=business, role='owner'
            ).select_related('user').first()
            if owner_profile:
                names   = ', '.join(r['staff_name'] for r in auto_closed)
                close_t = auto_closed[0]['scheduled_close']
                total_tabs_converted = sum(r['tabs_converted'] for r in auto_closed)
                tabs_note = (
                    f" {total_tabs_converted} tab(s) zilizoachwa wazi zimegeuzwa deni."
                    if total_tabs_converted else ''
                )
                create_in_app_notification(
                    user=owner_profile.user,
                    title='⏰ Shift Auto-Closed',
                    message=(
                        f"{names}: shift auto-closed at {close_t} "
                        f"(business hours ended). Staff may have forgotten to close shift."
                        f"{tabs_note}"
                    ),
                    notification_type='shift',
                )
        except Exception:
            pass

    return auto_closed


def _missed_tasks_for_shift(shift, business):
    """Return a list of human-readable task descriptions that were skipped on
    an auto-closed shift. Empty list means nothing was missed.

    Checks:
      1. Stock take  — ShiftStockCount rows linked to the shift
      2. Barrel readings (SHIFT_CLOSE) — keg businesses with TAPPED barrels only
    """
    from .models import ShiftStockCount, KegWeightReading, KegBarrel
    missed = []

    if not ShiftStockCount.objects.filter(shift=shift).exists():
        missed.append('Hesabu ya bidhaa (stock take) haikufanywa')

    has_keg = getattr(business, 'has_keg', False)
    if has_keg:
        tapped = KegBarrel.objects.filter(business=business, status='TAPPED').exists()
        if tapped:
            end = shift.ended_at or timezone.now()
            had_reading = KegWeightReading.objects.filter(
                barrel__business=business,
                reading_type='SHIFT_CLOSE',
                recorded_at__gte=shift.started_at,
                recorded_at__lte=end,
            ).exists()
            if not had_reading:
                missed.append('Uzito wa pipa (barrel scale reading) haukupimwa')

    return missed


# ── Active shift API (for bar board polling) ──────────────────────────────────

@login_required
def active_shift_api(request):
    """JSON: current user's own shift + all active shifts for the business.

    d.shift      — the calling user's shift (for bar board panel; null if not open).
    d.all_shifts — every OPEN/CLOSED shift across all staff (for owner dashboard).
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'shift': None, 'all_shifts': []})

    # Sweep before building the response so stale shifts are already CLOSED
    # when we query all_open and my_shift below.
    auto_closed = _auto_close_expired_shifts(up.business)

    def _section(s):
        try:
            return 'kitchen' if s.staff.userprofile.role == 'kitchen' else 'bar'
        except Exception:
            return 'bar'

    def _covers_both(s):
        try:
            sup = s.staff.userprofile
            if sup.role == 'kitchen':
                return getattr(sup, 'can_access_bar', False)
            return getattr(sup, 'can_access_kitchen', False)
        except Exception:
            return False

    # All open/closing shifts for the business (for dashboard)
    all_open = list(
        Shift.objects.filter(
            business=up.business,
            status__in=('OPEN', 'CLOSED'),
        ).order_by('started_at').select_related('staff__userprofile')
    )
    all_shifts_data = []
    for s in all_open:
        rec = _reconcile(s)
        all_shifts_data.append({
            'id':          s.id,
            'staff_name':  s.staff.get_full_name() or s.staff.username,
            'section':     _section(s),
            'covers_both': _covers_both(s),
            'status':      s.status,
            'started_at':  timezone.localtime(s.started_at).strftime('%H:%M'),
            'elapsed':     rec['elapsed'],
            'cash_sales':  rec['cash_sales'],
            'mpesa_sales': rec['mpesa_sales'],
            'total_sales': rec['total_sales'],
        })

    # MY shift — for the bar board's own shift panel
    my_shift = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
        staff=request.user,
    ).order_by('-started_at').first()

    if not my_shift:
        # Owner with no own shift: surface the most recently started OPEN staff shift
        # for the relevant counter so the bar/kitchen board panel shows live shift info
        # instead of "Hakuna shift iliyofunguliwa". is_mine=False keeps the Close button
        # hidden — the owner is not the one closing another staff's shift from here.
        if up.role == 'owner':
            _target_section = 'kitchen' if request.path.startswith('/kitchen/') else 'bar'
            # all_open is oldest-first; reversed() gives most-recently-started first.
            proxy = next(
                (s for s in reversed(all_open) if s.status == 'OPEN' and _section(s) == _target_section),
                None,
            )
            if proxy:
                proxy_rec = _reconcile(proxy)
                return JsonResponse({
                    'shift': {
                        'id':            proxy.id,
                        'status':        proxy.status,
                        'staff_name':    proxy.staff.get_full_name() or proxy.staff.username,
                        'started_at':    timezone.localtime(proxy.started_at).strftime('%H:%M'),
                        'opening_float': float(proxy.opening_float),
                        'cash_sales':    proxy_rec['cash_sales'],
                        'mpesa_sales':   proxy_rec['mpesa_sales'],
                        'credit_sales':  proxy_rec['credit_sales'],
                        'total_sales':   proxy_rec['total_sales'],
                        'expected_cash': proxy_rec['expected_cash'],
                        'variance':      proxy_rec['variance'],
                        'elapsed':       proxy_rec['elapsed'],
                        'is_mine':       False,
                    },
                    'can_open': True,
                    'all_shifts': all_shifts_data,
                    'auto_closed': len(auto_closed),
                })

        # Float suggestion: the previous CONFIRMED shift opened by this same staff member
        last = Shift.objects.filter(
            business=up.business,
            status='CONFIRMED',
            closing_cash_counted__isnull=False,
            staff=request.user,
        ).order_by('-ended_at').first()
        last_closing = float(last.closing_cash_counted) if last else None

        # Missed-tasks reminder: check the most recent CLOSED auto-closed shift
        # for this staff member. Remind until the shift is CONFIRMED.
        prev_auto = Shift.objects.filter(
            business=up.business,
            staff=request.user,
            status='CLOSED',
            auto_closed=True,
        ).order_by('-ended_at').first()
        missed_tasks = _missed_tasks_for_shift(prev_auto, up.business) if prev_auto else []

        return JsonResponse({
            'shift': None,
            'can_open': True,
            'last_closing': last_closing,
            'all_shifts': all_shifts_data,
            'auto_closed': len(auto_closed),
            'missed_tasks': missed_tasks,
        })

    rec = _reconcile(my_shift)
    return JsonResponse({
        'shift': {
            'id':             my_shift.id,
            'status':         my_shift.status,
            'staff_name':     my_shift.staff.get_full_name() or my_shift.staff.username,
            'started_at':     timezone.localtime(my_shift.started_at).strftime('%H:%M'),
            'opening_float':  float(my_shift.opening_float),
            'cash_sales':     rec['cash_sales'],
            'mpesa_sales':    rec['mpesa_sales'],
            'credit_sales':   rec['credit_sales'],
            'total_sales':    rec['total_sales'],
            'expected_cash':  rec['expected_cash'],
            'variance':       rec['variance'],
            'elapsed':        rec['elapsed'],
            'is_mine':        True,
        },
        'can_open': False,
        'all_shifts': all_shifts_data,
        'auto_closed': len(auto_closed),
    })


# ── Open shift ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def open_shift(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    # Each staff member may have one active shift at a time — concurrent shifts across
    # different staff (e.g. bar counter + kitchen counter) are explicitly allowed.
    existing = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
        staff=request.user,
    ).first()
    if existing:
        return JsonResponse({
            'ok': False,
            'error': "Una shift iliyofunguliwa tayari. Imalize kwanza.",
        }, status=400)

    try:
        opening_float = Decimal(str(request.POST.get('opening_float', '0')))
    except Exception:
        opening_float = Decimal('0')

    try:
        banked = Decimal(str(request.POST.get('banked_amount', '0') or '0'))
    except Exception:
        banked = Decimal('0')

    prev_closing_raw = request.POST.get('prev_closing', '').strip()
    notes = (request.POST.get('notes') or '').strip()

    # Build automatic audit note for the cash handover chain
    auto_note_parts = []
    if prev_closing_raw:
        try:
            prev = Decimal(str(prev_closing_raw))
            auto_note_parts.append(f"Shift iliyopita iliisha na KES {int(prev)}")
            if banked > 0:
                auto_note_parts.append(f"KES {int(banked)} iliondolewa/kubanked")
            auto_note_parts.append(f"Float ya kuanza: KES {int(opening_float)}")
        except Exception:
            pass

    full_notes = ' · '.join(auto_note_parts)
    if notes:
        full_notes = (full_notes + '\n' + notes).strip() if full_notes else notes

    shift = Shift.objects.create(
        business=up.business,
        store=up.business.stores.first() if up.business.stores.exists() else None,
        staff=request.user,
        opening_float=opening_float,
        notes=full_notes,
    )

    # Notify owner when a non-owner opens a shift
    if not up.is_owner:
        staff_name = request.user.get_full_name() or request.user.username
        float_str = f"KES {int(opening_float)}"
        try:
            from .models import Notification
            from django.contrib.auth import get_user_model
            User = get_user_model()
            owners = User.objects.filter(
                userprofile__business=up.business,
                userprofile__role='owner',
            )
            for owner in owners:
                Notification.objects.create(
                    user=owner,
                    title=f'🟢 Shift Imefunguliwa — {staff_name}',
                    message=f"{staff_name} amefungua shift na float ya {float_str}.",
                    notification_type='staff',
                )
        except Exception:
            pass

    # Kitchen staff have no kegs — skip barrel confirm step entirely
    if getattr(up, 'role', '') == 'kitchen':
        tapped = []
    else:
        tapped = _tapped_barrels_for_business(up.business)

    return JsonResponse({
        'ok': True,
        'shift_id': shift.id,
        'staff_name': request.user.get_full_name() or request.user.username,
        'opening_float': float(opening_float),
        'started_at': timezone.localtime(shift.started_at).strftime('%H:%M'),
        'tapped_barrels': tapped,
    })


# ── Close shift ───────────────────────────────────────────────────────────────

@login_required
@require_POST
def close_shift(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if shift.status != 'OPEN':
        return JsonResponse({'ok': False, 'error': 'Shift si OPEN'}, status=400)

    try:
        closing_cash = Decimal(str(request.POST.get('closing_cash_counted', '0')))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Nambari si sahihi'}, status=400)

    notes_add = (request.POST.get('notes') or '').strip()
    try:
        offline_amt = Decimal(str(request.POST.get('offline_sales_amount', '0') or '0'))
    except Exception:
        offline_amt = Decimal('0')
    offline_note = (request.POST.get('offline_sales_note') or '').strip()

    shift.closing_cash_counted  = closing_cash
    shift.ended_at              = timezone.now()
    shift.status                = 'CLOSED'
    shift.offline_sales_amount  = offline_amt
    shift.offline_sales_note    = offline_note
    if notes_add:
        shift.notes = (shift.notes + '\n' + notes_add).strip()
    shift.save(update_fields=[
        'closing_cash_counted', 'ended_at', 'status', 'notes',
        'offline_sales_amount', 'offline_sales_note',
    ])

    # Process barrel weights (SHIFT_CLOSE readings)
    barrel_weights_raw = request.POST.get('barrel_weights', '[]')
    try:
        barrel_weights_list = json.loads(barrel_weights_raw)
    except Exception:
        barrel_weights_list = []

    weight_readings = []
    for entry in barrel_weights_list:
        try:
            bid  = int(entry.get('barrel_id', 0))
            wkg  = Decimal(str(entry.get('weight_kg', '0') or '0'))
            if wkg <= 0:
                continue
            barrel = KegBarrel.objects.filter(
                id=bid, business=up.business, status='TAPPED'
            ).select_related('item').first()
            if not barrel:
                continue
            KegWeightReading.objects.create(
                barrel=barrel,
                shift=shift,
                weight_kg=wkg,
                reading_type='SHIFT_CLOSE',
                recorded_by=request.user,
                note='Mwisho wa shift',
            )
            # F2: check variance on SHIFT_CLOSE — no volume threshold, fire if danger
            if up.business.keg_alerts_enabled:
                try:
                    from . import keg_metrics
                    bv = keg_metrics.barrel_variance(barrel)
                    if bv.wastage_pct is not None:
                        tol = float(up.business.keg_variance_tolerance_pct)
                        if keg_metrics.variance_flag(bv.wastage_pct, tol) == 'danger':
                            from .keg_views import _fire_keg_alert
                            _fire_keg_alert(
                                up.business,
                                barrel.item.description,
                                request.user.get_full_name() or request.user.username,
                                bv.wastage_kes or 0.0,
                                bv.wastage_pct,
                            )
                except Exception:
                    pass
            net_kg = round(float(wkg) - float(barrel.tare_weight_kg), 2)
            weight_readings.append({
                'barrel_id': barrel.id,
                'name':      barrel.item.description,
                'weight_kg': float(wkg),
                'net_kg':    net_kg,
                'tare_kg':   float(barrel.tare_weight_kg),
            })
        except Exception:
            continue

    rec = _reconcile(shift)

    # Cash variance alert — fire to owner when discrepancy > KES 500
    _variance = rec.get('variance')
    if _variance is not None and abs(_variance) > 500:
        try:
            from .notifications import normalize_ke_phone as _nkp, send_sms_notification as _ssms
            from .models import Notification as _Notif
            from accounts.models import UserProfile as _UP
            _staff_name = shift.staff.get_full_name() or shift.staff.username
            _direction  = 'upungufu' if _variance < 0 else 'ziada'
            _alert_msg  = (
                f"⚠️ Tofauti ya fedha mwisho wa shift — {_staff_name}: "
                f"KES {abs(_variance):,.0f} ({_direction}). "
                f"Angalia Z-Report kwa maelezo zaidi."
            )
            for _op in _UP.objects.filter(
                business=up.business, role='owner'
            ).select_related('user'):
                _Notif.objects.create(
                    user=_op.user,
                    title='⚠️ Tofauti ya Fedha',
                    message=_alert_msg,
                    notification_type='warning',
                )
                if _op.phone:
                    try:
                        _ssms(_alert_msg, _nkp(_op.phone))
                    except Exception:
                        pass
        except Exception:
            logger.exception('close_shift: cash variance alert failed for shift %s', shift.id)

    # Auto-convert open tabs to debt at shift close — but ONLY when the business
    # is past its closing time (or operates 24/7 with no closing_time set).
    # A staff member who closes shift early (e.g. for a break) while the bar is
    # still within operating hours should NOT have their customers' tabs wiped.
    # 24/7 bars (no closing_time): always convert — shift changes ARE the end-of-service.
    #
    # manager_taking_over: staff can tick this when a manager or owner is staying
    # on to serve remaining customers. Suppresses auto-convert so open tabs stay
    # alive for the manager to settle directly from the bar board.
    _biz = up.business
    _has_closing_time = bool(getattr(_biz, 'closing_time', None))
    _manager_taking_over = request.POST.get('manager_taking_over') == '1'
    _should_convert = (not _has_closing_time or not _biz.is_open()) and not _manager_taking_over

    # Single source of truth shared with _auto_close_expired_shifts() — see that
    # function's call site for why this must never be a copy again.
    auto_converted, auto_converted_names, open_tabs_list = (
        _convert_open_tabs_to_debt_for_shift(shift, up.business, _should_convert)
    )

    return JsonResponse({
        'ok': True,
        'expected_cash':        rec['expected_cash'],
        'variance':             rec['variance'],
        'total_sales':          rec['total_sales'],
        'cash_sales':           rec['cash_sales'],
        'mpesa_sales':          rec['mpesa_sales'],
        'offline_sales_amount': float(offline_amt),
        'offline_sales_note':   offline_note,
        'weight_readings':      weight_readings,
        'open_tabs':            open_tabs_list,
        'open_tabs_count':      len(open_tabs_list),
        'auto_converted':       auto_converted,
        'auto_converted_names': auto_converted_names,
        'manager_taking_over':  _manager_taking_over,
    })


# ── Confirm barrel weights (incoming staff after opening their shift) ─────────

@login_required
@require_POST
def confirm_barrel_weights(request):
    """
    Incoming staff saves SHIFT_OPEN readings right after opening their shift.
    Compares with the last SHIFT_CLOSE per barrel and returns a verdict.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    # Must have their own OPEN shift
    shift = Shift.objects.filter(
        business=up.business, status='OPEN', staff=request.user
    ).first()
    if not shift:
        return JsonResponse({'ok': False, 'error': 'Hakuna shift iliyofunguliwa'}, status=400)

    barrel_weights_raw = request.POST.get('barrel_weights', '[]')
    try:
        barrel_weights_list = json.loads(barrel_weights_raw)
    except Exception:
        barrel_weights_list = []

    results = []
    for entry in barrel_weights_list:
        try:
            bid  = int(entry.get('barrel_id', 0))
            wkg  = Decimal(str(entry.get('weight_kg', '0') or '0'))
            if wkg <= 0:
                continue
            barrel = KegBarrel.objects.filter(
                id=bid, business=up.business, status='TAPPED'
            ).select_related('item').first()
            if not barrel:
                continue
            KegWeightReading.objects.create(
                barrel=barrel,
                shift=shift,
                weight_kg=wkg,
                reading_type='SHIFT_OPEN',
                recorded_by=request.user,
                note='Uthibitisho wa kufungua shift',
            )
            net_kg = round(float(wkg) - float(barrel.tare_weight_kg), 2)
            last_close = KegWeightReading.objects.filter(
                barrel=barrel, reading_type='SHIFT_CLOSE'
            ).order_by('-recorded_at').first()
            if last_close:
                diff = round(float(wkg) - float(last_close.weight_kg), 2)
                flag = 'ok' if abs(diff) <= 0.3 else ('warn' if abs(diff) <= 1.0 else 'danger')
                # F2: overnight barrel-loss alert when handover gap > 1.0 kg
                if abs(diff) > 1.0 and up.business.keg_alerts_enabled:
                    try:
                        from accounts.models import UserProfile
                        from .models import Notification
                        from .notifications import normalize_ke_phone, send_sms_notification
                        from django.utils import timezone as _tz
                        msg = (
                            f"⚠️ Barrel {barrel.item.description}: imepoteza"
                            f" {abs(diff):.2f} kg usiku"
                            f" (SHIFT_CLOSE→SHIFT_OPEN). Kagua."
                        )
                        now = _tz.now()
                        can_sms = (
                            not up.business.last_txn_sms_at or
                            (now - up.business.last_txn_sms_at).total_seconds() > 600
                        )
                        owners = UserProfile.objects.filter(
                            business=up.business, role='owner'
                        ).select_related('user')
                        for op in owners:
                            Notification.objects.create(
                                user=op.user, title='Keg Barrel Alert', message=msg,
                                notification_type='warning',
                            )
                            if can_sms and op.phone:
                                normalized = normalize_ke_phone(op.phone)
                                if normalized:
                                    send_sms_notification(msg, normalized)
                        if can_sms:
                            up.business.last_txn_sms_at = now
                            up.business.save(update_fields=['last_txn_sms_at'])
                    except Exception:
                        pass
            else:
                diff = None
                flag = 'ok'
            results.append({
                'barrel_id':     barrel.id,
                'name':          barrel.item.description,
                'weight_kg':     float(wkg),
                'net_kg':        net_kg,
                'tare_kg':       float(barrel.tare_weight_kg),
                'last_close_kg': float(last_close.weight_kg) if last_close else None,
                'diff_kg':       diff,
                'flag':          flag,
            })
        except Exception:
            continue

    return JsonResponse({'ok': True, 'readings': results})


# ── Confirm shift (incoming staff / owner) ────────────────────────────────────

@login_required
@require_POST
def confirm_shift(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if shift.status != 'CLOSED':
        return JsonResponse({'ok': False, 'error': 'Shift si CLOSED — haiwezi kuthibitishwa'}, status=400)

    notes_add = (request.POST.get('notes') or '').strip()
    shift.confirmed_by = request.user
    shift.status = 'CONFIRMED'
    if notes_add:
        shift.notes = (shift.notes + '\n✓ ' + notes_add).strip()
    shift.save(update_fields=['confirmed_by', 'status', 'notes'])
    return JsonResponse({'ok': True})


# ── Shift history page ────────────────────────────────────────────────────────

@login_required
def shift_history(request):
    up = _get_up(request)
    if not up:
        from django.shortcuts import redirect
        return redirect('login')

    # Station scoping: bar-only staff see bar shifts; kitchen-only staff see kitchen shifts
    from django.db.models import Q as _Q
    _is_owner = getattr(up, 'is_owner_or_manager', False)
    _is_kitchen = getattr(up, 'is_kitchen_staff', False)
    _can_bar = getattr(up, 'can_access_bar', False)
    _can_kitchen = getattr(up, 'can_access_kitchen', False)

    if _is_owner:
        _show_bar, _show_kitchen = True, True
    elif _is_kitchen:
        _show_bar, _show_kitchen = _can_bar, True
    else:
        _show_bar, _show_kitchen = True, _can_kitchen

    _base_qs = Shift.objects.filter(business=up.business)
    if _show_kitchen and not _show_bar:
        _base_qs = _base_qs.filter(store__is_kitchen=True)
    elif _show_bar and not _show_kitchen:
        _base_qs = _base_qs.filter(_Q(store__is_kitchen=False) | _Q(store__isnull=True))

    shifts_qs = _base_qs.select_related('staff', 'confirmed_by').order_by('-started_at')[:60]

    rows = []
    for shift in shifts_qs:
        rec = _reconcile(shift)
        var = rec['variance']
        if var is None:
            var_class = 'pending'
        elif abs(var) <= 50:
            var_class = 'ok'
        elif abs(var) <= 200:
            var_class = 'warn'
        else:
            var_class = 'danger'
        rows.append({
            'shift':     shift,
            'rec':       rec,
            'var_class': var_class,
        })

    return render(request, 'core/bar/shift_history.html', {
        'rows':     rows,
        'is_owner': getattr(up, 'is_owner', False),
    })


# ── Shift stock take ──────────────────────────────────────────────────────────

@login_required
def stock_take_api(request, shift_id):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    shift = get_object_or_404(Shift, id=shift_id, business=up.business)

    if request.method == 'GET':
        items = (
            Item.objects
            .filter(business=up.business)
            .exclude(is_keg=True)
            .exclude(is_produce=True)
            .order_by('description')
            .prefetch_related('portion_presets')
        )
        # Station scoping: only show items from the same counter as this shift
        _shift_store = shift.store
        if _shift_store is not None:
            items = items.filter(store__is_kitchen=_shift_store.is_kitchen)
        data = []
        for item in items:
            data.append({
                'item_id':      item.id,
                'name':         item.description,
                'unit':         item.unit or '',
                'book_balance': float(item.current_balance()),
                'bottle_envelope':  item.bottle_envelope,
                'tots_per_unit':    float(item.tots_per_unit or 0),
                'expected_rev_per_unit': item.bottle_expected_revenue_per_unit(),
            })
        return JsonResponse({'ok': True, 'items': data})

    if request.method == 'POST':
        try:
            counts = json.loads(request.POST.get('counts', '[]'))
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Invalid data'}, status=400)

        _shift_store = shift.store
        results = []
        for entry in counts:
            try:
                item_id      = int(entry.get('item_id', 0))
                actual       = Decimal(str(entry.get('actual_count', '0') or '0'))
                item = Item.objects.select_related('store').filter(
                    id=item_id, business=up.business
                ).first()
                if not item:
                    continue
                # Skip items that belong to the other counter
                if _shift_store is not None and item.store_id:
                    if item.store.is_kitchen != _shift_store.is_kitchen:
                        continue
                book = item.current_balance()
                ShiftStockCount.objects.update_or_create(
                    shift=shift, item=item,
                    defaults={
                        'book_balance': book,
                        'actual_count': actual,
                        'recorded_by':  request.user,
                    }
                )
                variance = float(actual) - float(book)
                result_row = {
                    'name':       item.description,
                    'unit':       item.unit or '',
                    'book':       float(book),
                    'actual':     float(actual),
                    'variance':   round(variance, 2),
                }
                if item.bottle_envelope:
                    loss_units = max(0.0, float(book) - float(actual))
                    result_row['bottle_envelope'] = True
                    result_row['variance_kes'] = round(
                        loss_units * item.bottle_expected_revenue_per_unit(), 2
                    )
                results.append(result_row)
            except Exception:
                continue

        return JsonResponse({'ok': True, 'results': results})
