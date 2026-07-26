"""
Petty Cash / Counter Drawdown — views for recording and reviewing small operational
expenses taken directly from the till during service (tokens, tissues, transport, etc.).
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BusinessExpense, PettyCash


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

    # 2026-07-26 (item 1) — mismatch flag: if this staffer has an open shift, warn
    # (never block — this app never hard-blocks on a figure that could be a real,
    # legitimate withdrawal) when this entry would take total petty cash beyond
    # what the shift has actually taken in cash so far. Non-blocking by design:
    # the entry is still recorded either way, staff just gets an explicit heads-up
    # to double check the amount before the owner reviews it.
    warning = None
    try:
        from .shift_views import get_active_staff_shift, _reconcile
        shift = get_active_staff_shift(up, business)
        if shift and shift is not False:
            rec = _reconcile(shift)
            available = (
                rec['cash_sales'] + rec['debt_recovered_cash'] + rec['offline_adj']
                - rec['petty_cash'] - rec['petty_cash_pending']
            )
            if amount > available:
                warning = (
                    f"Umeingiza KES {amount:,.0f} lakini fedha taslimu zilizopo kwenye "
                    f"shift hii (baada ya petty cash nyingine) ni karibu KES "
                    f"{max(available, 0):,.0f} pekee. Hakikisha kiasi hiki ni sahihi."
                )
    except Exception:
        pass

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
        'warning': warning,
    })


# ── Staff self-edit (own entry, still pending only) ───────────────────────────

@login_required
@require_POST
def edit_petty_cash(request, entry_id):
    """Let the RECORDING staffer correct their own entry before the owner reviews
    it (2026-07-26 live request) — e.g. they mistyped the amount. Once reviewed
    (approved/rejected) this is no longer available; use respond_petty_cash for a
    rejected entry instead, which explains rather than silently changes it.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated'}, status=401)

    entry = get_object_or_404(PettyCash, id=entry_id, business=up.business)
    if entry.recorded_by_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'Unaweza kubadilisha entry yako mwenyewe pekee.'}, status=403)
    if entry.status != 'pending':
        return JsonResponse({'ok': False, 'error': 'Entry hii tayari imepitiwa na haiwezi kubadilishwa tena.'}, status=400)

    amount_str = request.POST.get('amount', '').strip()
    reason = request.POST.get('reason', entry.reason)
    description = (request.POST.get('description') or '').strip()

    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Ingiza kiasi sahihi.'}, status=400)

    valid_reasons = [r[0] for r in PettyCash.REASON_CHOICES]
    if reason not in valid_reasons:
        reason = entry.reason

    entry.amount = amount
    entry.reason = reason
    entry.description = description
    entry.save(update_fields=['amount', 'reason', 'description'])

    return JsonResponse({
        'ok': True,
        'id': entry.id,
        'amount': float(entry.amount),
        'reason_display': entry.get_reason_display(),
        'description': entry.description,
    })


# ── Staff explains after a rejection (does not change status) ────────────────

@login_required
@require_POST
def respond_petty_cash(request, entry_id):
    """The recording staffer explains themselves after a rejection (2026-07-26
    live request) — appends to staff_note and notifies the owner, who can then
    choose to re-review (review_petty_cash already supports flipping a decision
    back and forth). Does NOT change status on its own — only an explicit owner
    re-review does that.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated'}, status=401)

    entry = get_object_or_404(PettyCash, id=entry_id, business=up.business)
    if entry.recorded_by_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'Unaweza kujibu kuhusu entry yako mwenyewe pekee.'}, status=403)
    if entry.status != 'rejected':
        return JsonResponse({'ok': False, 'error': 'Unaweza kujibu tu kwa entry iliyokataliwa.'}, status=400)

    note = (request.POST.get('staff_note') or '').strip()
    if not note:
        return JsonResponse({'ok': False, 'error': 'Andika maelezo yako.'}, status=400)

    entry.staff_note = note
    entry.staff_note_at = timezone.now()
    entry.save(update_fields=['staff_note', 'staff_note_at'])

    who = request.user.get_full_name() or request.user.username
    from .models import Notification
    from accounts.models import UserProfile as _UP
    for op in _UP.objects.filter(business=up.business, role__in=['owner', 'manager']).select_related('user'):
        Notification.objects.create(
            user=op.user,
            title='💬 Petty Cash — Maelezo Kutoka kwa Staff',
            message=(
                f"{who} amejibu kuhusu KES {entry.amount:,.0f} ({entry.get_reason_display()}) "
                f"iliyokataliwa: {note}"
            ),
            notification_type='info',
            link_url='/petty-cash/',
        )

    return JsonResponse({'ok': True, 'staff_note': entry.staff_note})


# ── Owner review list ─────────────────────────────────────────────────────────

@login_required
def petty_cash_list(request):
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    entries = PettyCash.objects.filter(business=business).select_related('recorded_by', 'reviewed_by')

    # 2026-07-26 (item 1) — staff now have somewhere to see, edit (while pending),
    # and respond to (once rejected) their OWN entries — previously this whole
    # page was owner-only, so a staffer had no way to correct a mistaken amount
    # or explain themselves after a rejection at all. Staff only ever see their
    # own entries here; the owner still sees everyone's.
    if not up.is_owner:
        entries = entries.filter(recorded_by=request.user)

    # Filter by status
    status_filter = request.GET.get('status', 'all')
    if status_filter in ('pending', 'approved', 'rejected'):
        entries = entries.filter(status=status_filter)

    pending_count = PettyCash.objects.filter(business=business, status='pending').count()

    return render(request, 'core/petty_cash_list.html', {
        'entries': entries[:100],
        'status_filter': status_filter,
        'pending_count': pending_count,
        'is_owner': up.is_owner,
        'my_user_id': request.user.id,
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

    # 2026-07-26 (item 1) — Expense Intelligence linkage. Approved petty cash is
    # real business cost and must show up in the P&L/expense trend the same as
    # any other expense; a REJECTED entry is money that was never validated, so
    # any expense mirror created by an earlier approval must be removed again on
    # a reversal (this is a DERIVED row — PettyCash.status stays authoritative,
    # never the other way round).
    if entry.status == 'approved' and not entry.linked_expense_id:
        expense = BusinessExpense.objects.create(
            business=entry.business,
            description=f"Petty Cash — {entry.get_reason_display()}" + (f": {entry.description}" if entry.description else ""),
            amount=entry.amount,
            category='petty_cash',
            date=entry.date,
            notes=f"Auto-created from petty cash entry #{entry.id}, recorded by "
                  f"{entry.recorded_by.get_full_name() or entry.recorded_by.username if entry.recorded_by else 'mtu asiyejulikana'}.",
        )
        entry.linked_expense = expense
        entry.save(update_fields=['linked_expense'])
    elif entry.status != 'approved' and entry.linked_expense_id:
        entry.linked_expense.delete()
        entry.linked_expense = None
        entry.save(update_fields=['linked_expense'])

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
            link_url='/petty-cash/',
        )

    return JsonResponse({
        'ok': True, 'new_status': entry.status, 'message': message, 'is_reversal': is_reversal,
    })
