"""
DJ / MC Performer Session Management.

Who can do what:
  - Owner: full access — create performers, start/end/pay sessions, view history
  - Counter staff with open shift: start/end sessions, approve performer check-in
  - Public (no login): performer check-in URL, customer feedback URL
"""
import hashlib
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (
    BusinessExpense, Performer, PerformerFeedback, PerformerSession, Shift,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def _owner_required(request):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return None, JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)
    return up, None


def _staff_or_owner(request):
    up = _get_up(request)
    if not up:
        return None, JsonResponse({'ok': False, 'error': 'Auth required'}, status=403)
    return up, None


def _fire_session_started_notification(session, started_by):
    """In-app + optional SMS to owner when a session starts."""
    business = session.business
    performer_name = session.performer.name if session.performer else 'Unknown'
    fee = session.agreed_fee
    msg = (
        f"\U0001f3a4 DJ/MC session started — {performer_name}, "
        f"KES {fee:,.0f}. Staff: {started_by.get_full_name() or started_by.username}."
    )
    # In-app notification to all owners
    from .models import Notification
    for up in business.users.filter(role='owner').select_related('user'):
        Notification.objects.create(business=business, user=up.user, message=msg)

    # SMS if enabled
    if business.event_sms_enabled and business.phone:
        try:
            from .notifications import send_sms_notification
            send_sms_notification(msg, business.phone)
        except Exception:
            logger.exception("DJ session start SMS failed (business=%s)", business.id)


def _fire_unverified_alert(session):
    """Alert owner when a session ends without performer check-in."""
    business = session.business
    performer_name = session.performer.name if session.performer else 'Unknown'
    msg = (
        f"⚠️ DJ/MC session ended but {performer_name} never confirmed presence. "
        f"Session date: {session.date}, KES {session.agreed_fee:,.0f}."
    )
    from .models import Notification
    for up in business.users.filter(role='owner').select_related('user'):
        Notification.objects.create(business=business, user=up.user, message=msg)

    if business.event_sms_enabled and business.phone:
        try:
            from .notifications import send_sms_notification
            send_sms_notification(msg, business.phone)
        except Exception:
            logger.exception("DJ unverified alert SMS failed (business=%s)", business.id)


# ── Performer CRUD (owner only) ───────────────────────────────────────────────

@login_required
def performer_list(request):
    up, err = _owner_required(request)
    if err:
        return redirect('home')
    business = up.business
    performers_qs = list(Performer.objects.filter(business=business).order_by('name'))
    for p in performers_qs:
        p.stat_count    = p.session_count()
        p.stat_staff    = p.avg_staff_rating()
        p.stat_customer = p.avg_customer_rating()
    return render(request, 'core/performer_list.html', {
        'performers': performers_qs,
        'business': business,
        'is_owner': True,
    })


@login_required
def performer_form(request, performer_id=None):
    up, err = _owner_required(request)
    if err:
        return redirect('home')
    business = up.business

    performer = None
    if performer_id:
        performer = get_object_or_404(Performer, id=performer_id, business=business)

    if request.method == 'POST':
        name          = request.POST.get('name', '').strip()
        ptype         = request.POST.get('performer_type', 'DJ')
        phone         = request.POST.get('phone', '').strip()
        genre         = request.POST.get('genre', '').strip()
        contract_type = request.POST.get('contract_type', 'ONE_OFF')
        try:
            standard_rate = Decimal(str(request.POST.get('standard_rate', '0') or '0'))
        except Exception:
            standard_rate = Decimal('0')
        notes     = request.POST.get('notes', '').strip()
        is_active = request.POST.get('is_active') == '1'

        if not name:
            return render(request, 'core/performer_form.html', {
                'performer': performer, 'error': 'Name is required.',
                'business': business, 'is_owner': True,
            })

        if performer:
            performer.name          = name
            performer.performer_type = ptype
            performer.phone         = phone
            performer.genre         = genre
            performer.contract_type = contract_type
            performer.standard_rate = standard_rate
            performer.notes         = notes
            performer.is_active     = is_active
            performer.save()
        else:
            performer = Performer.objects.create(
                business=business,
                name=name,
                performer_type=ptype,
                phone=phone,
                genre=genre,
                contract_type=contract_type,
                standard_rate=standard_rate,
                notes=notes,
                is_active=True,
            )

        # Offer to create RecurringExpense for retainer
        if contract_type == 'RETAINER' and request.POST.get('create_recurring') == '1':
            from .models import RecurringExpense
            already = RecurringExpense.objects.filter(
                business=business,
                description__icontains=performer.name,
                category='entertainment',
                is_active=True,
            ).exists()
            if not already:
                RecurringExpense.objects.create(
                    business=business,
                    description=f"Retainer — {performer.name}",
                    category='entertainment',
                    amount=standard_rate,
                    period='MONTHLY',
                    staff_profile=None,
                    is_active=True,
                )

        return redirect('performer_list')

    return render(request, 'core/performer_form.html', {
        'performer': performer,
        'business': business,
        'is_owner': True,
    })


# ── Session API (AJAX — bar board) ────────────────────────────────────────────

@login_required
def session_today_api(request):
    """GET — return today's sessions for the bar board modal."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    business = up.business
    today = timezone.localdate()
    sessions = (
        PerformerSession.objects
        .filter(business=business, date=today)
        .exclude(status=PerformerSession.STATUS_CANCELLED)
        .select_related('performer')
        .order_by('started_at')
    )
    performers = list(
        Performer.objects.filter(business=business, is_active=True)
        .order_by('name')
        .values('id', 'name', 'performer_type', 'standard_rate')
    )
    result = []
    for s in sessions:
        result.append({
            'id': s.id,
            'performer_name': s.performer.name if s.performer else 'Unknown',
            'performer_type': s.performer.get_performer_type_display() if s.performer else '',
            'status': s.status,
            'started_at': s.started_at.strftime('%H:%M') if s.started_at else None,
            'ended_at':   s.ended_at.strftime('%H:%M')   if s.ended_at   else None,
            'agreed_fee': float(s.agreed_fee),
            'payment_status': s.payment_status,
            'staff_rating':   s.staff_rating,
            'performer_checked_in':  s.performer_checked_in,
            'performer_checkin_at':  s.performer_checkin_at.strftime('%H:%M') if s.performer_checkin_at else None,
            'checkin_short_code': s.checkin_short_code,
            'checkin_token': str(s.checkin_token),
            'feedback_token': str(s.feedback_token),
            'avg_customer_rating': s.avg_customer_rating,
            'total_customer_ratings': s.total_customer_ratings,
            'duration_hours': s.duration_hours,
        })
    return JsonResponse({
        'ok': True,
        'sessions': result,
        'performers': performers,
        'is_owner': getattr(up, 'is_owner', False),
    })


@login_required
@require_POST
def session_start(request):
    """Start a new performer session."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    business = up.business
    is_owner = getattr(up, 'is_owner', False)

    if not is_owner:
        from .shift_views import get_active_staff_shift
        if get_active_staff_shift(up, business) is False:
            return JsonResponse({'ok': False, 'error': 'Fungua shift kwanza.'}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    performer_id = data.get('performer_id')
    try:
        agreed_fee = Decimal(str(data.get('agreed_fee', '0') or '0'))
    except Exception:
        agreed_fee = Decimal('0')
    notes = (data.get('notes') or '').strip()

    performer = None
    if performer_id:
        performer = Performer.objects.filter(id=performer_id, business=business, is_active=True).first()

    now = timezone.now()
    today = timezone.localdate()

    # Determine status — approval gate
    threshold = business.performer_approval_threshold or 0
    if threshold > 0 and agreed_fee >= threshold and not is_owner:
        status = PerformerSession.STATUS_PENDING_APPROVAL
    else:
        status = PerformerSession.STATUS_ACTIVE

    # Link to current open shift if one exists
    open_shift = Shift.objects.filter(business=business, status='OPEN', staff=request.user).first()
    if not open_shift:
        open_shift = Shift.objects.filter(business=business, status='OPEN').first()

    session = PerformerSession.objects.create(
        business=business,
        performer=performer,
        shift=open_shift,
        date=today,
        status=status,
        started_at=now if status == PerformerSession.STATUS_ACTIVE else None,
        agreed_fee=agreed_fee,
        notes=notes,
        created_by=request.user,
    )

    if status == PerformerSession.STATUS_ACTIVE:
        _fire_session_started_notification(session, request.user)

    return JsonResponse({
        'ok': True,
        'session_id': session.id,
        'status': status,
        'checkin_short_code': session.checkin_short_code,
        'checkin_token': str(session.checkin_token),
        'pending_approval': status == PerformerSession.STATUS_PENDING_APPROVAL,
    })


@login_required
@require_POST
def session_update(request, session_id):
    """End session + record staff rating."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    business = up.business

    session = get_object_or_404(PerformerSession, id=session_id, business=business)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    action = data.get('action', 'end')

    if action == 'approve':
        if not getattr(up, 'is_owner', False):
            return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)
        session.status = PerformerSession.STATUS_ACTIVE
        session.started_at = timezone.now()
        session.save(update_fields=['status', 'started_at'])
        _fire_session_started_notification(session, request.user)
        return JsonResponse({'ok': True, 'status': session.status})

    if action == 'cancel':
        session.status = PerformerSession.STATUS_CANCELLED
        session.save(update_fields=['status'])
        return JsonResponse({'ok': True})

    # Default: end session
    staff_rating = data.get('staff_rating')
    staff_notes  = (data.get('staff_notes') or '').strip()
    try:
        staff_rating = int(staff_rating) if staff_rating else None
        if staff_rating and not (1 <= staff_rating <= 5):
            staff_rating = None
    except (ValueError, TypeError):
        staff_rating = None

    session.status      = PerformerSession.STATUS_COMPLETED
    session.ended_at    = timezone.now()
    session.staff_rating = staff_rating
    session.staff_notes  = staff_notes
    session.save(update_fields=['status', 'ended_at', 'staff_rating', 'staff_notes'])

    if not session.performer_checked_in:
        _fire_unverified_alert(session)

    return JsonResponse({
        'ok': True,
        'duration_hours': session.duration_hours,
        'performer_checked_in': session.performer_checked_in,
    })


@login_required
@require_POST
def session_pay(request, session_id):
    """Mark session paid + auto-create BusinessExpense."""
    up, err = _owner_required(request)
    if err:
        return err
    business = up.business

    session = get_object_or_404(PerformerSession, id=session_id, business=business)
    if session.payment_status == PerformerSession.PAYMENT_PAID:
        return JsonResponse({'ok': False, 'error': 'Already paid'}, status=400)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    payment_method = data.get('payment_method', 'cash')
    if payment_method not in ('cash', 'mpesa'):
        payment_method = 'cash'

    now = timezone.now()
    performer_name = session.performer.name if session.performer else 'DJ/MC'
    duration_label = f", {session.duration_hours}h" if session.duration_hours else ''
    start_label = session.started_at.strftime('%H:%M') if session.started_at else ''
    end_label   = session.ended_at.strftime('%H:%M')   if session.ended_at   else ''
    time_label  = f" ({start_label}–{end_label}{duration_label})" if start_label else ''

    expense = BusinessExpense.objects.create(
        business=business,
        description=f"DJ/MC — {performer_name}{time_label}",
        amount=session.agreed_fee,
        category='entertainment',
        date=session.date,
        notes=f"Session {session.date}, {payment_method}",
    )

    session.payment_status = PerformerSession.PAYMENT_PAID
    session.payment_method = payment_method
    session.paid_at        = now
    session.expense        = expense
    session.save(update_fields=['payment_status', 'payment_method', 'paid_at', 'expense'])

    return JsonResponse({'ok': True, 'expense_id': expense.id})


@login_required
def session_checkin_poll(request, session_id):
    """Bar board polls this every 30s to see if performer has checked in."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    session = get_object_or_404(PerformerSession, id=session_id, business=up.business)
    return JsonResponse({
        'ok': True,
        'performer_checked_in': session.performer_checked_in,
        'performer_checkin_at': session.performer_checkin_at.strftime('%H:%M') if session.performer_checkin_at else None,
    })


# ── Session History (owner) ───────────────────────────────────────────────────

@login_required
def session_list(request):
    up, err = _owner_required(request)
    if err:
        return redirect('home')
    business = up.business

    # Filters — template uses filter_from / filter_to / filter_performer
    filter_performer = request.GET.get('performer', '')
    filter_from      = request.GET.get('from', '')
    filter_to        = request.GET.get('to', '')

    qs = (
        PerformerSession.objects
        .filter(business=business)
        .select_related('performer')
        .order_by('-date', '-started_at')
    )
    if filter_performer:
        qs = qs.filter(performer_id=filter_performer)
    if filter_from:
        qs = qs.filter(date__gte=filter_from)
    if filter_to:
        qs = qs.filter(date__lte=filter_to)

    performers = Performer.objects.filter(business=business).order_by('name')
    return render(request, 'core/session_list.html', {
        'sessions':          qs[:200],
        'performers':        performers,
        'filter_performer':  filter_performer,
        'filter_from':       filter_from,
        'filter_to':         filter_to,
        'business':          business,
        'is_owner':          True,
    })


# ── Public: Performer Check-In ────────────────────────────────────────────────

def session_checkin_public(request, token):
    """
    Public page — no login. DJ/MC opens this URL from bar board QR and taps
    "Ndio, niko hapa" to confirm their presence. Server-timestamped.
    """
    session = get_object_or_404(PerformerSession, checkin_token=token)

    if request.method == 'POST':
        if not session.performer_checked_in:
            session.performer_checked_in = True
            session.performer_checkin_at = timezone.now()
            session.save(update_fields=['performer_checked_in', 'performer_checkin_at'])
        return JsonResponse({'ok': True})

    return render(request, 'core/performer_checkin_public.html', {
        'session': session,
        'already_checked_in': session.performer_checked_in,
    })


# ── Public: Customer Feedback ─────────────────────────────────────────────────

def session_feedback_public(request, token):
    """
    Public page — no login. Customers scan QR at table and submit 1–5 star rating.
    Soft dedup: one submission per session+IP hash.
    """
    session = get_object_or_404(PerformerSession, feedback_token=token)

    client_ip = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', '')
    )
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip else ''

    already_rated = (
        ip_hash and
        PerformerFeedback.objects.filter(session=session, ip_hash=ip_hash).exists()
    )

    if request.method == 'POST' and not already_rated:
        try:
            rating = int(request.POST.get('rating', 0))
        except (ValueError, TypeError):
            rating = 0
        if 1 <= rating <= 5:
            comment = (request.POST.get('comment') or '').strip()[:200]
            PerformerFeedback.objects.create(
                session=session,
                rating=rating,
                comment=comment,
                ip_hash=ip_hash,
            )
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'Tathmini lazima iwe 1–5.'})

    return render(request, 'core/performer_feedback_public.html', {
        'session': session,
        'already_submitted': already_rated,
    })
