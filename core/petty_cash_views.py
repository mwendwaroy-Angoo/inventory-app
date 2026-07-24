"""
Petty Cash / Counter Drawdown — views for recording and reviewing small operational
expenses taken directly from the till during service (tokens, tissues, transport, etc.).
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import PettyCash


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


# ── Record a petty cash entry (staff or owner, AJAX POST) ────────────────────

@login_required
@require_POST
def record_petty_cash(request):
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated'}, status=401)

    business = up.business
    amount_str = request.POST.get('amount', '').strip()
    reason = request.POST.get('reason', 'other')
    description = (request.POST.get('description') or '').strip()

    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Ingiza kiasi sahihi.'}, status=400)

    valid_reasons = [r[0] for r in PettyCash.REASON_CHOICES]
    if reason not in valid_reasons:
        reason = 'other'

    entry = PettyCash.objects.create(
        business=business,
        amount=amount,
        reason=reason,
        description=description,
        recorded_by=request.user,
        date=timezone.localdate(),
    )
    return JsonResponse({
        'ok': True,
        'id': entry.id,
        'amount': float(entry.amount),
        'reason_display': entry.get_reason_display(),
        'description': entry.description,
        'status': entry.status,
    })


# ── Owner review list ─────────────────────────────────────────────────────────

@login_required
def petty_cash_list(request):
    up = _get_up(request)
    if not up or not up.is_owner:
        return redirect('home')

    business = up.business
    entries = PettyCash.objects.filter(business=business).select_related('recorded_by', 'reviewed_by')

    # Filter by status
    status_filter = request.GET.get('status', 'all')
    if status_filter in ('pending', 'approved', 'rejected'):
        entries = entries.filter(status=status_filter)

    pending_count = PettyCash.objects.filter(business=business, status='pending').count()

    return render(request, 'core/petty_cash_list.html', {
        'entries': entries[:100],
        'status_filter': status_filter,
        'pending_count': pending_count,
    })


# ── Approve / reject a petty cash entry (owner only, AJAX POST) ───────────────

@login_required
@require_POST
def review_petty_cash(request, entry_id):
    up = _get_up(request)
    if not up or not up.is_owner:
        return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)

    entry = get_object_or_404(PettyCash, id=entry_id, business=up.business)
    action = request.POST.get('action')  # 'approve' or 'reject'
    review_note = (request.POST.get('review_note') or '').strip()

    if action not in ('approve', 'reject'):
        return JsonResponse({'ok': False, 'error': 'Invalid action'}, status=400)

    # 2026-07-25 (live report — Roy rejected an entry by mistake and had no way
    # to undo it): re-reviewing an already-reviewed entry is allowed on purpose
    # — this is the fix. bar_z_report's shift reconciliation reads
    # PettyCash.status='approved' LIVE on every render (core/keg_views.py) and
    # nothing else in the app stores/caches an approved-petty-cash total
    # anywhere, so flipping the status back is the entire correction — no
    # separate reconciliation step is needed once this save commits.
    previous_status = entry.status
    previous_reviewer = entry.reviewed_by
    previous_when = entry.reviewed_at
    is_reversal = previous_status in ('approved', 'rejected') and previous_status != (
        'approved' if action == 'approve' else 'rejected'
    )

    entry.status = 'approved' if action == 'approve' else 'rejected'
    entry.reviewed_by = request.user
    entry.reviewed_at = timezone.now()
    entry.review_note = review_note
    entry.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])

    # 2026-07-24 wording/accountability audit finding: this used to return a
    # bare {'new_status': ...} with no message and never notify the staffer
    # who recorded the entry at all — they had no way to learn their KES X
    # was approved or rejected except by checking the list themselves.
    reviewer_label = request.user.get_full_name() or request.user.username
    when = timezone.localtime(entry.reviewed_at).strftime('%d %b %Y, %H:%M')
    verb = 'imekubaliwa' if action == 'approve' else 'imekataliwa'

    if is_reversal:
        prev_verb = 'ilikubaliwa' if previous_status == 'approved' else 'ilikataliwa'
        prev_reviewer_label = (
            previous_reviewer.get_full_name() or previous_reviewer.username
        ) if previous_reviewer else 'mtu asiyejulikana'
        prev_when = timezone.localtime(previous_when).strftime('%d %b %Y, %H:%M') if previous_when else '—'
        message = (
            f'MAREKEBISHO: KES {entry.amount:,.0f} ({entry.get_reason_display()}) '
            f'{prev_verb} na {prev_reviewer_label} tarehe {prev_when} — sasa {verb} na '
            f'{reviewer_label} tarehe {when}.'
        )
    else:
        message = (
            f'KES {entry.amount:,.0f} ({entry.get_reason_display()}) {verb} na '
            f'{reviewer_label} tarehe {when}.'
        )
    if review_note:
        message += f' Sababu: {review_note}'

    if entry.recorded_by_id and entry.recorded_by_id != request.user.id:
        from .models import Notification
        if is_reversal:
            title = '↺ Petty Cash — Uamuzi Umebadilishwa'
        else:
            title = '✅ Petty Cash Imekubaliwa' if action == 'approve' else '❌ Petty Cash Imekataliwa'
        Notification.objects.create(
            user=entry.recorded_by,
            title=title,
            message=message,
            notification_type=('info' if action == 'approve' else 'warning'),
        )

    return JsonResponse({
        'ok': True, 'new_status': entry.status, 'message': message, 'is_reversal': is_reversal,
    })
