"""Maombi ↔ Maagizo — the staff ↔ owner request/instruction channel.

Two directions sharing one model and one review lifecycle (see
StaffRequest's docstring in models.py for the full design rationale):

  direction='request'     — staff asks the owner something. Owner/manager
                             approve or reject, always with a reason back
                             to the requester (this app's standard).
  direction='instruction' — the owner tasks a staff member (or all staff)
                             with something concrete. The ASSIGNEE
                             completes it themselves (self-declared, same
                             trust model as any other staff-recorded
                             action in this app); the owner/manager may
                             also complete it on their behalf, or cancel
                             it if it's no longer needed. A regular
                             staffer can never cancel an instruction —
                             only complete one.

2026-07-30 redesign folds the original 2026-07-26 staff-only version into
this two-direction model rather than building a parallel one.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Item, Notification, StaffRequest
from .notifications import normalize_ke_phone, send_sms_notification

logger = logging.getLogger(__name__)


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def _assigned_or_broadcast_q(up):
    """Q object: instructions assigned to this profile, or broadcast (blank
    assigned_to)."""
    from django.db.models import Q
    return Q(assigned_to_id=up.id) | Q(assigned_to__isnull=True)


def _can_act_on_instruction(up, sr):
    """Who may COMPLETE (mark done) an instruction: the specific assignee,
    any staff member if it's a broadcast (assigned_to is blank), or
    owner/manager acting on someone's behalf. Cancelling is owner/manager
    only — checked separately in review_staff_request."""
    if not up:
        return False
    if up.is_owner_or_manager:
        return True
    if sr.assigned_to_id is None:
        return True
    return sr.assigned_to_id == up.id


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
        direction=StaffRequest.DIRECTION_REQUEST,
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
            link_url='/staff-requests/?tab=received',
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
@require_POST
def create_instruction(request):
    """Owner/manager tasks a staff member (or all staff) with a concrete
    action. task_type drives action_url() — the deep link into the real
    screen that fulfils the instruction (see StaffRequest.action_url)."""
    up = _get_up(request)
    if not up or not up.is_owner_or_manager:
        return JsonResponse({'ok': False, 'error': 'Owner or manager only.'}, status=403)

    business = up.business
    task_type = request.POST.get('task_type', 'general')
    valid_tasks = [t[0] for t in StaffRequest.TASK_TYPE_CHOICES]
    if task_type not in valid_tasks:
        task_type = 'general'

    subject = (request.POST.get('subject') or '').strip()[:150]
    description = (request.POST.get('description') or '').strip()
    if not subject:
        return JsonResponse({'ok': False, 'error': 'Andika kichwa cha agizo.'}, status=400)

    from accounts.models import UserProfile as _UP
    assigned_to = None
    assigned_to_id = request.POST.get('assigned_to')
    if assigned_to_id:
        assigned_to = _UP.objects.filter(
            id=assigned_to_id, business=business, role__in=['staff', 'waitress', 'kitchen', 'manager'],
            user__is_active=True,
        ).first()
        if not assigned_to:
            return JsonResponse({'ok': False, 'error': 'Mfanyakazi huyo hapatikani.'}, status=400)

    related_item = None
    related_item_id = request.POST.get('related_item')
    if related_item_id:
        related_item = Item.objects.filter(id=related_item_id, business=business).first()
        if not related_item:
            return JsonResponse({'ok': False, 'error': 'Bidhaa hiyo haipatikani.'}, status=400)

    due_date = (request.POST.get('due_date') or '').strip() or None

    sr = StaffRequest.objects.create(
        business=business, requested_by=request.user,
        direction=StaffRequest.DIRECTION_INSTRUCTION,
        category=StaffRequest.CATEGORY_GENERAL,
        task_type=task_type, subject=subject, description=description,
        assigned_to=assigned_to, related_item=related_item, due_date=due_date,
    )

    who = request.user.get_full_name() or request.user.username
    action_url = sr.action_url()
    notify_msg = f'{who} amekuagiza: "{subject}"' + (f' — {description}' if description else '')
    if assigned_to:
        targets = [assigned_to]
    else:
        targets = list(_UP.objects.filter(
            business=business, role__in=['staff', 'waitress', 'kitchen'], user__is_active=True,
        ))
    for profile in targets:
        Notification.objects.create(
            user=profile.user,
            title=f'📋 Agizo Jipya: {sr.get_task_type_display()}',
            message=notify_msg,
            notification_type='info',
            link_url=action_url or '/staff-requests/?tab=instructions',
        )
        if profile.phone:
            try:
                send_sms_notification(
                    f'{business.name}: {who} amekuagiza — {subject}',
                    normalize_ke_phone(profile.phone),
                )
            except Exception:
                logger.exception('create_instruction: SMS failed for profile %s', profile.id)

    return JsonResponse({'ok': True, 'request_id': sr.id})


@login_required
def staff_request_list(request):
    """Owner/manager: 'Maagizo Niliyotoa' (instructions I've given) +
    'Maombi Niliyopokea' (requests I've received) — both business-wide.
    Staff: 'Maagizo Kwangu' (instructions for me — assigned to me or
    broadcast) + 'Maombi Yangu' (my own requests)."""
    up = _get_up(request)
    if not up:
        return redirect('home')

    business = up.business
    is_owner = up.is_owner_or_manager
    status_filter = request.GET.get('status', 'all')

    def _apply_status(qs):
        if status_filter in ('pending', 'approved', 'rejected'):
            return qs.filter(status=status_filter)
        return qs

    base_qs = StaffRequest.objects.filter(business=business).select_related(
        'requested_by', 'reviewed_by', 'assigned_to__user', 'related_item',
    )

    if is_owner:
        instructions = _apply_status(base_qs.filter(direction=StaffRequest.DIRECTION_INSTRUCTION))
        requests_qs = _apply_status(base_qs.filter(direction=StaffRequest.DIRECTION_REQUEST))
        pending_count = StaffRequest.objects.filter(
            business=business, status='pending', direction=StaffRequest.DIRECTION_REQUEST,
        ).count()
    else:
        instructions = _apply_status(base_qs.filter(direction=StaffRequest.DIRECTION_INSTRUCTION).filter(
            _assigned_or_broadcast_q(up)
        ))
        requests_qs = _apply_status(base_qs.filter(direction=StaffRequest.DIRECTION_REQUEST, requested_by=request.user))
        pending_count = instructions.filter(status='pending').count()

    from accounts.models import UserProfile as _UP
    staff_choices = list(_UP.objects.filter(
        business=business, role__in=['staff', 'waitress', 'kitchen', 'manager'], user__is_active=True,
    ).select_related('user').order_by('user__first_name', 'user__username')) if is_owner else []

    item_choices = list(
        Item.objects.filter(business=business).order_by('description')[:500]
    ) if is_owner else []

    return render(request, 'core/staff_requests.html', {
        'instructions': instructions[:100],
        'requests': requests_qs[:100],
        'status_filter': status_filter,
        'pending_count': pending_count,
        'is_owner': is_owner,
        'my_user_id': request.user.id,
        'my_profile_id': up.id,
        'staff_choices': staff_choices,
        'item_choices': item_choices,
        'task_type_choices': StaffRequest.TASK_TYPE_CHOICES,
        'active_tab': request.GET.get('tab', 'instructions' if not is_owner else 'given'),
    })


@login_required
@require_POST
def review_staff_request(request, request_id):
    """Requests: owner/manager approve or reject, always with a reason back
    to the requester. Instructions: the assignee (or any staff, if
    broadcast) completes; owner/manager may complete on their behalf or
    cancel outright. A regular staffer can never cancel — only complete."""
    up = _get_up(request)
    if not up:
        return JsonResponse({'ok': False, 'error': 'Not authenticated.'}, status=403)

    sr = get_object_or_404(StaffRequest, id=request_id, business=up.business)
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        return JsonResponse({'ok': False, 'error': 'Invalid action.'}, status=400)

    if sr.status != 'pending':
        return JsonResponse({'ok': False, 'error': 'Hii tayari imeamuliwa.'}, status=400)

    is_instruction = sr.direction == StaffRequest.DIRECTION_INSTRUCTION

    if is_instruction:
        if action == 'approve':
            if not _can_act_on_instruction(up, sr):
                return JsonResponse({'ok': False, 'error': 'Agizo hili halijakukabidhiwa wewe.'}, status=403)
        else:  # reject == cancel — owner/manager only
            if not up.is_owner_or_manager:
                return JsonResponse({'ok': False, 'error': 'Mmiliki au meneja pekee anaweza kufuta agizo.'}, status=403)
    else:
        if not up.is_owner_or_manager:
            return JsonResponse({'ok': False, 'error': 'Owner or manager only.'}, status=403)

    review_note = (request.POST.get('review_note') or '').strip()
    sr.status = 'approved' if action == 'approve' else 'rejected'
    sr.reviewed_by = request.user
    sr.reviewed_at = timezone.now()
    sr.review_note = review_note
    sr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])

    actor = request.user.get_full_name() or request.user.username
    when = timezone.localtime(sr.reviewed_at).strftime('%d %b %Y, %H:%M')

    if is_instruction:
        verb = 'imetimizwa' if action == 'approve' else 'imefutwa'
        message = f'Agizo "{sr.subject}" {verb} na {actor} tarehe {when}.'
        if review_note:
            message += f' Maelezo: {review_note}'
        # Notify the owner who issued it (unless they're the one acting).
        if sr.requested_by_id != request.user.id:
            Notification.objects.create(
                user=sr.requested_by,
                title='✅ Agizo Limetimizwa' if action == 'approve' else '✖ Agizo Limefutwa',
                message=message,
                notification_type=('info' if action == 'approve' else 'warning'),
                link_url='/staff-requests/?tab=given',
            )
    else:
        verb = 'imekubaliwa' if action == 'approve' else 'imekataliwa'
        message = f'Ombi lako "{sr.subject}" {verb} na {actor} tarehe {when}.'
        if review_note:
            message += f' Sababu: {review_note}'
        if sr.requested_by_id != request.user.id:
            Notification.objects.create(
                user=sr.requested_by,
                title='✅ Ombi Limekubaliwa' if action == 'approve' else '❌ Ombi Limekataliwa',
                message=message,
                notification_type=('info' if action == 'approve' else 'warning'),
                link_url='/staff-requests/?tab=mine',
            )

    return JsonResponse({'ok': True, 'message': message, 'new_status': sr.status})
