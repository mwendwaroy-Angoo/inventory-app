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
from decimal import Decimal

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
    # Scope to the correct counter so concurrent bar + kitchen shifts don't bleed into each other.
    # Kitchen staff shifts only count kitchen store sales; all other shifts count bar/general sales.
    try:
        is_kitchen_shift = shift.staff.userprofile.role == 'kitchen'
    except Exception:
        is_kitchen_shift = False
    if is_kitchen_shift:
        txns = txns.filter(item__store__is_kitchen=True)
    else:
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
        # Float suggestion: the previous CONFIRMED shift opened by this same staff member
        last = Shift.objects.filter(
            business=up.business,
            status='CONFIRMED',
            closing_cash_counted__isnull=False,
            staff=request.user,
        ).order_by('-ended_at').first()
        last_closing = float(last.closing_cash_counted) if last else None
        return JsonResponse({
            'shift': None,
            'can_open': True,
            'last_closing': last_closing,
            'all_shifts': all_shifts_data,
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
                    business=up.business,
                    user=owner,
                    message=f"{staff_name} amefungua shift na float ya {float_str}.",
                )
        except Exception:
            pass

    # Return tapped barrels so the frontend can show the confirmation step
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

    shifts_qs = (
        Shift.objects
        .filter(business=up.business)
        .select_related('staff', 'confirmed_by')
        .order_by('-started_at')[:60]
    )

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
        )
        data = []
        for item in items:
            data.append({
                'item_id':    item.id,
                'name':       item.description,
                'unit':       item.unit or '',
                'book_balance': float(item.current_balance()),
            })
        return JsonResponse({'ok': True, 'items': data})

    if request.method == 'POST':
        try:
            counts = json.loads(request.POST.get('counts', '[]'))
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Invalid data'}, status=400)

        results = []
        for entry in counts:
            try:
                item_id      = int(entry.get('item_id', 0))
                actual       = Decimal(str(entry.get('actual_count', '0') or '0'))
                item = Item.objects.filter(id=item_id, business=up.business).first()
                if not item:
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
                results.append({
                    'name':       item.description,
                    'unit':       item.unit or '',
                    'book':       float(book),
                    'actual':     float(actual),
                    'variance':   round(variance, 2),
                })
            except Exception:
                continue

        return JsonResponse({'ok': True, 'results': results})
