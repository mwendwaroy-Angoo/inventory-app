"""Staff ↔ Owner structured request/approval channel (item 5, 2026-07-26).

A staff-initiated request for anything not already covered by a dedicated
flow (restock has StockRequest, debt write-off has WriteOffRequest, stock
variance has StockVarianceQuery). Staff submit a typed request → owner/manager
notified → decision → requester notified with the reason, matching this
app's wording/accountability standard on every reject.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification, StaffRequest
from .notifications import normalize_ke_phone, send_sms_notification

logger = logging.getLogger(__name__)


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


@login_required
@require_POST
def submit_staff_request(request):
    """Any staff member (or owner/manager) may raise a request — no shift gate,
    since asking a question or requesting permission doesn't require an open
    till, unlike a real money/stock movement.
    """
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    business = up.business
    category = request.POST.get('category', 'general')
    valid_categories = [c[0] for c in StaffRequest.CATEGORY_CHOICES]
    if category not in valid_categories:
        category = 'general'

    subject = (request.POST.get('subject') or '').strip()[:150]
    description = (request.POST.get('description') or '').strip()
    if not subject:
        return JsonResponse({'ok': False, 'error': 'Andika kichwa cha ombi.'}, status=400)

    sr = StaffRequest.objects.create(
        business=business, requested_by=request.user,
        category=category, subject=subject, description=description,
    )

    who = request.user.get_full_name() or request.user.username
    from accounts.models import UserProfile as _UP
    for op in _UP.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
        Notification.objects.create(
            user=op.user,
            title=f'📨 Ombi Jipya: {sr.get_category_display()}',
            message=f'{who} ameomba: "{subject}"' + (f' — {description}' if description else ''),
            notification_type='info',
            link_url='/staff-requests/',
        )
        if op.phone:
            try:
                send_sms_notification(
                    f'{business.name}: {who} ameomba ({sr.get_category_display()}): {subject}',
                    normalize_ke_phone(op.phone),
                )
            except Exception:
                logger.exception('submit_staff_request: SMS failed for owner %s', op.id)

    return JsonResponse({'ok': True, 'request_id': sr.id})


@login_required
def staff_request_list(request):
    """Owner/manager see everyone's requests; staff see only their own."""
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    requests_qs = StaffRequest.objects.filter(business=business).select_related('requested_by', 'reviewed_by')
    if not up.is_owner_or_manager:
        requests_qs = requests_qs.filter(requested_by=request.user)

    status_filter = request.GET.get('status', 'all')
    if status_filter in ('pending', 'approved', 'rejected'):
        requests_qs = requests_qs.filter(status=status_filter)

    pending_count = StaffRequest.objects.filter(business=business, status='pending').count()

    return render(request, 'core/staff_requests.html', {
        'requests': requests_qs[:100],
        'status_filter': status_filter,
        'pending_count': pending_count,
        'is_owner': up.is_owner_or_manager,
        'my_user_id': request.user.id,
    })


@login_required
@require_POST
def review_staff_request(request, request_id):
    """Owner/manager approve or reject — always with a reason back to the requester."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Owner or manager only.'}, status=403)

    sr = get_object_or_404(StaffRequest, id=request_id, business=up.business)
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        return JsonResponse({'ok': False, 'error': 'Invalid action.'}, status=400)

    review_note = (request.POST.get('review_note') or '').strip()
    sr.status = 'approved' if action == 'approve' else 'rejected'
    sr.reviewed_by = request.user
    sr.reviewed_at = timezone.now()
    sr.review_note = review_note
    sr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])

    reviewer = request.user.get_full_name() or request.user.username
    when = timezone.localtime(sr.reviewed_at).strftime('%d %b %Y, %H:%M')
    verb = 'imekubaliwa' if action == 'approve' else 'imekataliwa'
    message = f'Ombi lako "{sr.subject}" {verb} na {reviewer} tarehe {when}.'
    if review_note:
        message += f' Sababu: {review_note}'

    if sr.requested_by_id != request.user.id:
        Notification.objects.create(
            user=sr.requested_by,
            title='✅ Ombi Limekubaliwa' if action == 'approve' else '❌ Ombi Limekataliwa',
            message=message,
            notification_type=('info' if action == 'approve' else 'warning'),
            link_url='/staff-requests/',
        )

    return JsonResponse({'ok': True, 'message': message, 'new_status': sr.status})
