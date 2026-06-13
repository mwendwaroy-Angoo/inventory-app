"""
Sprint 4 — Shift Handover Module.

Flow: staff opens shift (opening float) → sells → closes shift (counts cash)
      → owner / incoming staff confirms → CONFIRMED.

Reconciliation:
  expected_closing_cash = opening_float + cash_sales_during_shift
  variance = closing_cash_counted − expected_closing_cash
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Shift, Transaction


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
    cash_sales  = float(txns.filter(payment_method='cash' ).aggregate(t=Sum('sale_amount'))['t'] or 0)
    mpesa_sales = float(txns.filter(payment_method='mpesa').aggregate(t=Sum('sale_amount'))['t'] or 0)
    credit_sales= float(txns.filter(payment_method='credit').aggregate(t=Sum('sale_amount'))['t'] or 0)
    total_sales  = cash_sales + mpesa_sales + credit_sales
    expected_cash = float(shift.opening_float) + cash_sales
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
        'variance':      variance,
        'elapsed':       f"{hours}h {mins:02d}m",
        'elapsed_mins':  elapsed_secs // 60,
    }


# ── Active shift API (for bar board polling) ──────────────────────────────────

@login_required
def active_shift_api(request):
    """JSON: current open/closed shift for this business, or null."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'shift': None})

    shift = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
    ).order_by('-started_at').first()

    if not shift:
        # No active shift — find the last CONFIRMED shift's closing count as float suggestion
        last = Shift.objects.filter(
            business=up.business,
            status='CONFIRMED',
            closing_cash_counted__isnull=False,
        ).order_by('-ended_at').first()
        last_closing = float(last.closing_cash_counted) if last else None
        return JsonResponse({'shift': None, 'can_open': True, 'last_closing': last_closing})

    rec = _reconcile(shift)
    return JsonResponse({
        'shift': {
            'id':             shift.id,
            'status':         shift.status,
            'staff_name':     shift.staff.get_full_name() or shift.staff.username,
            'started_at':     timezone.localtime(shift.started_at).strftime('%H:%M'),
            'opening_float':  float(shift.opening_float),
            'cash_sales':     rec['cash_sales'],
            'mpesa_sales':    rec['mpesa_sales'],
            'credit_sales':   rec['credit_sales'],
            'total_sales':    rec['total_sales'],
            'expected_cash':  rec['expected_cash'],
            'variance':       rec['variance'],
            'elapsed':        rec['elapsed'],
            'is_mine':        shift.staff_id == request.user.id,
        },
        'can_open': False,
    })


# ── Open shift ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def open_shift(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)

    # Enforce one open shift per business at a time
    existing = Shift.objects.filter(
        business=up.business,
        status__in=('OPEN', 'CLOSED'),
    ).first()
    if existing:
        return JsonResponse({
            'ok': False,
            'error': f"Kuna shift iliyofunguliwa tayari na "
                     f"{existing.staff.get_full_name() or existing.staff.username}. "
                     f"Imalize kwanza.",
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

    return JsonResponse({
        'ok': True,
        'shift_id': shift.id,
        'staff_name': request.user.get_full_name() or request.user.username,
        'opening_float': float(opening_float),
        'started_at': timezone.localtime(shift.started_at).strftime('%H:%M'),
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

    shift.closing_cash_counted = closing_cash
    shift.ended_at = timezone.now()
    shift.status = 'CLOSED'
    if notes_add:
        shift.notes = (shift.notes + '\n' + notes_add).strip()
    shift.save(update_fields=['closing_cash_counted', 'ended_at', 'status', 'notes'])

    rec = _reconcile(shift)
    return JsonResponse({
        'ok': True,
        'expected_cash': rec['expected_cash'],
        'variance':      rec['variance'],
        'total_sales':   rec['total_sales'],
        'cash_sales':    rec['cash_sales'],
        'mpesa_sales':   rec['mpesa_sales'],
    })


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
            'shift':      shift,
            'rec':        rec,
            'var_class':  var_class,
        })

    return render(request, 'core/bar/shift_history.html', {
        'rows':     rows,
        'is_owner': getattr(up, 'is_owner', False),
    })
