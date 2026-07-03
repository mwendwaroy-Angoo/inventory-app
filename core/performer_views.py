"""
DJ / MC Performer Session Management.

Who can do what:
  - Owner: full access — create performers, start/end/pay sessions, view history
  - Counter staff with open shift: start/end sessions, approve performer check-in
  - Public (no login): performer check-in URL, customer feedback URL, live display
"""
import datetime
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum as SumF
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    BusinessExpense,
    Customer,
    Performer,
    PerformerFeedback,
    PerformerSession,
    RecurringExpense,
    Shift,
)
from .notifications import (
    create_in_app_notification,
    normalize_ke_phone,
    send_sms_notification,
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


def _maybe_activate(session):
    """Auto-flip PENDING_CONFIRMATION → ACTIVE once all required parties have confirmed."""
    if session.status == PerformerSession.STATUS_PENDING_CONFIRMATION and session.all_confirmed:
        session.status     = PerformerSession.STATUS_ACTIVE
        session.started_at = timezone.now()
        session.save(update_fields=['status', 'started_at'])
        return True
    return False


def _fire_session_started_notification(session, started_by):
    """In-app + optional SMS to owner when a session goes ACTIVE."""
    business = session.business
    names = [session.performer.name] if session.performer else ['Unknown']
    if session.second_performer:
        names.append(session.second_performer.name)
    performers_label = ' & '.join(names)
    staff_label = started_by.get_full_name() or started_by.username
    msg = (
        f"🎤 DJ/MC session started — {performers_label}. Staff: {staff_label}."
    )
    for up in business.users.filter(role='owner').select_related('user'):
        create_in_app_notification(up.user, '🎤 DJ/MC Sesheni Imeanza', msg)

    if business.event_sms_enabled and business.phone:
        try:
            send_sms_notification(msg, business.phone)
        except Exception:
            logger.exception("DJ session start SMS failed (business=%s)", business.id)


def _fire_unverified_alert(session, unconfirmed_names):
    """Alert owner when a session ends without all performers confirming."""
    business = session.business
    names_label = ' na '.join(unconfirmed_names)
    msg = (
        f"⚠️ DJ/MC session iliisha lakini {names_label} hajakuthibitisha ufika. "
        f"Tarehe: {session.date}."
    )
    for up in business.users.filter(role='owner').select_related('user'):
        create_in_app_notification(up.user, '⚠️ DJ/MC Hajakuthibitishwa', msg)

    if business.event_sms_enabled and business.phone:
        try:
            send_sms_notification(msg, business.phone)
        except Exception:
            logger.exception("DJ unverified alert SMS failed (business=%s)", business.id)


def _send_payment_sms(session):
    """SMS to each performer confirming payment was made. No amount — privacy."""
    date_label = session.date.strftime('%d %b %Y')
    for performer in [session.performer, session.second_performer]:
        if performer and performer.phone:
            try:
                msg = (
                    f"Habari {performer.name}, malipo yako ya onyesho "
                    f"la {date_label} @ {session.business.name} "
                    f"yamethibitishwa. Asante kwa kazi nzuri! \U0001f3a4"
                )
                send_sms_notification(msg, normalize_ke_phone(performer.phone))
            except Exception:
                logger.exception(
                    "Payment SMS to performer %s failed", performer.id
                )


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
        # Total fees paid (primary role + secondary role)
        total_primary = PerformerSession.objects.filter(
            performer=p, business=business, payment_status='PAID'
        ).aggregate(t=SumF('agreed_fee'))['t'] or Decimal('0')
        total_second = PerformerSession.objects.filter(
            second_performer=p, business=business, payment_status='PAID'
        ).aggregate(t=SumF('second_performer_fee'))['t'] or Decimal('0')
        p.stat_total_paid = float(total_primary + total_second)
        # Booking insight badge derived from ratings
        if p.stat_count < 2:
            p.insight_label = 'Mpya'
            p.insight_color = '#b0b0b0'
        elif p.stat_customer and p.stat_customer >= 4.0:
            p.insight_label = '📈 Book Again'
            p.insight_color = '#4caf50'
        elif (p.stat_staff and p.stat_staff < 3.0) or (p.stat_customer and p.stat_customer < 3.0):
            p.insight_label = '⚠️ Angalia'
            p.insight_color = '#e87090'
        else:
            p.insight_label = '📊 Angalia Takwimu'
            p.insight_color = '#c9a84c'

    # Best performer for the insight callout (min 2 sessions + min 1 customer rating)
    top_performer = None
    eligible = [p for p in performers_qs if p.stat_count >= 2 and p.stat_customer]
    if eligible:
        top_performer = max(eligible, key=lambda x: (x.stat_customer or 0))

    return render(request, 'core/performer_list.html', {
        'performers':    performers_qs,
        'top_performer': top_performer,
        'business':      business,
        'is_owner':      True,
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
        notes         = request.POST.get('notes', '').strip()
        photo_url     = request.POST.get('photo_url', '').strip()
        is_active     = request.POST.get('is_active') == '1'
        try:
            standard_rate = Decimal(str(request.POST.get('standard_rate') or '0'))
        except Exception:
            standard_rate = Decimal('0')

        if not name:
            return render(request, 'core/performer_form.html', {
                'performer': performer, 'error': 'Name is required.',
                'business': business, 'is_owner': True,
            })

        if performer:
            performer.name           = name
            performer.performer_type = ptype
            performer.phone          = phone
            performer.genre          = genre
            performer.contract_type  = contract_type
            performer.standard_rate  = standard_rate
            performer.notes          = notes
            performer.photo_url      = photo_url
            performer.is_active      = is_active
            performer.save()
        else:
            performer = Performer.objects.create(
                business=business, name=name, performer_type=ptype,
                phone=phone, genre=genre, contract_type=contract_type,
                standard_rate=standard_rate, notes=notes, photo_url=photo_url,
                is_active=True,
            )

        if contract_type == 'RETAINER' and request.POST.get('create_recurring') == '1':
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
        'business':  business,
        'is_owner':  True,
    })


# ── Session API (AJAX — bar board) ────────────────────────────────────────────

@login_required
def session_today_api(request):
    """GET — return today's sessions + performer roster for the bar board modal."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    business = up.business
    today     = timezone.localdate()
    next_week = today + timedelta(days=7)

    is_owner = getattr(up, 'is_owner', False)

    sessions = (
        PerformerSession.objects
        .filter(business=business, date=today)
        .exclude(status=PerformerSession.STATUS_CANCELLED)
        .select_related('performer', 'second_performer', 'staff_confirmed_by')
        .order_by('started_at')
    )
    upcoming_qs = (
        PerformerSession.objects
        .filter(business=business, date__gt=today, date__lte=next_week,
                status=PerformerSession.STATUS_SCHEDULED)
        .select_related('performer')
        .order_by('date', 'scheduled_start_time')
    )
    performers = list(
        Performer.objects.filter(business=business, is_active=True)
        .order_by('name')
        .values('id', 'name', 'performer_type', 'standard_rate', 'photo_url')
    )

    upcoming_result = []
    for s in upcoming_qs:
        upcoming_result.append({
            'id':                   s.id,
            'performer_name':       s.performer.name if s.performer else 'Unknown',
            'performer_type':       s.performer.get_performer_type_display() if s.performer else '',
            'date':                 s.date.strftime('%Y-%m-%d'),
            'scheduled_start_time': s.scheduled_start_time.strftime('%H:%M') if s.scheduled_start_time else None,
            'feedback_token':       str(s.feedback_token),
        })

    result = []
    for s in sessions:
        checkin_at    = timezone.localtime(s.performer_checkin_at).strftime('%H:%M') if s.performer_checkin_at else None
        p2_checkin_at = timezone.localtime(s.second_performer_checkin_at).strftime('%H:%M') if s.second_performer_checkin_at else None
        staff_conf_at = timezone.localtime(s.staff_confirmed_at).strftime('%H:%M') if s.staff_confirmed_at else None
        staff_conf_by = (
            s.staff_confirmed_by.get_full_name() or s.staff_confirmed_by.username
        ) if s.staff_confirmed_by else None
        row = {
            'id':               s.id,
            'performer_name':   s.performer.name if s.performer else 'Unknown',
            'performer_type':   s.performer.get_performer_type_display() if s.performer else '',
            # Duo fields
            'is_duo':                    s.second_performer_id is not None,
            'second_performer_name':     s.second_performer.name if s.second_performer else None,
            'second_performer_type':     s.second_performer.get_performer_type_display() if s.second_performer else None,
            'second_performer_checked_in':      s.second_performer_checked_in,
            'second_performer_checkin_at':      p2_checkin_at,
            'second_performer_checkin_token':   str(s.second_performer_checkin_token),
            'second_performer_checkin_short_code': s.second_performer_checkin_short_code,
            # Status
            'status':           s.status,
            'all_confirmed':    s.all_confirmed,
            'started_at':       timezone.localtime(s.started_at).strftime('%H:%M') if s.started_at else None,
            'ended_at':         timezone.localtime(s.ended_at).strftime('%H:%M')   if s.ended_at   else None,
            # Staff rating (visible to staff + owner — assessing quality, not cost)
            'staff_rating':     s.staff_rating,
            # Primary performer confirmation
            'performer_checked_in':  s.performer_checked_in,
            'performer_checkin_at':  checkin_at,
            'checkin_short_code':    s.checkin_short_code,
            'checkin_token':         str(s.checkin_token),
            # Staff on-ground confirmation
            'staff_confirmed':       s.staff_confirmed,
            'staff_confirmed_at':    staff_conf_at,
            'staff_confirmed_by':    staff_conf_by,
            # Public feedback
            'feedback_token':        str(s.feedback_token),
            'avg_customer_rating':   s.avg_customer_rating,
            'total_customer_ratings': s.total_customer_ratings,
            'duration_hours':        s.duration_hours,
            'expected_hours':        float(s.expected_hours) if s.expected_hours else None,
        }
        # Fee and payment status — owner only; performers see via their checkin URL
        if is_owner:
            row['agreed_fee']           = float(s.agreed_fee)
            row['second_performer_fee'] = float(s.second_performer_fee or 0)
            row['payment_status']       = s.payment_status
        result.append(row)

    customer_sms_count = (
        Customer.objects
        .filter(business=business)
        .exclude(phone='').exclude(phone__isnull=True)
        .count()
    )

    return JsonResponse({
        'ok':                True,
        'sessions':          result,
        'upcoming':          upcoming_result,
        'performers':        performers,
        'is_owner':          is_owner,
        'customer_sms_count': customer_sms_count,
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
            return JsonResponse(
                {'ok': False, 'error': 'Fungua shift kwanza.'}, status=403
            )

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    performer_id        = data.get('performer_id')
    second_performer_id = data.get('second_performer_id')
    try:
        agreed_fee = Decimal(str(data.get('agreed_fee') or '0'))
    except Exception:
        agreed_fee = Decimal('0')
    try:
        second_performer_fee = Decimal(str(data.get('second_performer_fee') or '0'))
    except Exception:
        second_performer_fee = Decimal('0')
    try:
        _eh_str = data.get('expected_hours')
        expected_hours = Decimal(str(_eh_str)) if _eh_str else None
    except Exception:
        expected_hours = None
    notes = (data.get('notes') or '').strip()

    performer = None
    if performer_id:
        performer = Performer.objects.filter(
            id=performer_id, business=business, is_active=True
        ).first()

    second_performer = None
    if second_performer_id and str(second_performer_id) != str(performer_id):
        second_performer = Performer.objects.filter(
            id=second_performer_id, business=business, is_active=True
        ).first()

    today = timezone.localdate()

    threshold = business.performer_approval_threshold or 0
    if 0 < threshold <= agreed_fee and not is_owner:
        status = PerformerSession.STATUS_PENDING_APPROVAL
    else:
        # Always starts awaiting confirmation — ACTIVE only once all parties confirm
        status = PerformerSession.STATUS_PENDING_CONFIRMATION

    open_shift = Shift.objects.filter(
        business=business, status='OPEN', staff=request.user
    ).first()
    if not open_shift:
        open_shift = Shift.objects.filter(business=business, status='OPEN').first()

    session = PerformerSession.objects.create(
        business=business,
        performer=performer,
        second_performer=second_performer,
        shift=open_shift,
        date=today,
        status=status,
        agreed_fee=agreed_fee,
        second_performer_fee=second_performer_fee if second_performer else Decimal('0'),
        expected_hours=expected_hours,
        notes=notes,
        created_by=request.user,
    )

    return JsonResponse({
        'ok':               True,
        'session_id':       session.id,
        'status':           status,
        'checkin_short_code':        session.checkin_short_code,
        'checkin_token':             str(session.checkin_token),
        'second_performer_checkin_short_code': session.second_performer_checkin_short_code,
        'second_performer_checkin_token':      str(session.second_performer_checkin_token),
        'feedback_token':            str(session.feedback_token),
        'pending_approval':          status == PerformerSession.STATUS_PENDING_APPROVAL,
        'is_duo':                    second_performer is not None,
    })


@login_required
@require_POST
def session_update(request, session_id):
    """End session + record staff rating, or approve/cancel."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    business = up.business
    session  = get_object_or_404(PerformerSession, id=session_id, business=business)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    action = data.get('action', 'end')

    if action == 'approve':
        if not getattr(up, 'is_owner', False):
            return JsonResponse({'ok': False, 'error': 'Owner only'}, status=403)
        # High-fee approval clears that gate; confirmation still required
        session.status = PerformerSession.STATUS_PENDING_CONFIRMATION
        session.save(update_fields=['status'])
        if _maybe_activate(session):
            _fire_session_started_notification(session, request.user)
        return JsonResponse({'ok': True, 'status': session.status})

    if action == 'activate':
        if session.status != PerformerSession.STATUS_SCHEDULED:
            return JsonResponse({'ok': False, 'error': 'Sesheni hii si ya ratiba.'})
        session.status = PerformerSession.STATUS_PENDING_CONFIRMATION
        session.date   = timezone.localdate()
        session.save(update_fields=['status', 'date'])
        if _maybe_activate(session):
            _fire_session_started_notification(session, request.user)
        return JsonResponse({'ok': True, 'status': session.status})

    if action == 'staff_confirm':
        # Staff on duty corroborates that the performer(s) have physically arrived
        if session.status not in (
            PerformerSession.STATUS_PENDING_CONFIRMATION,
            PerformerSession.STATUS_PENDING_APPROVAL,
        ):
            return JsonResponse({'ok': False, 'error': 'Sesheni haipo katika hali inayohitaji uthibitisho.'})
        session.staff_confirmed    = True
        session.staff_confirmed_by = request.user
        session.staff_confirmed_at = timezone.now()
        session.save(update_fields=['staff_confirmed', 'staff_confirmed_by', 'staff_confirmed_at'])
        activated = _maybe_activate(session)
        if activated:
            _fire_session_started_notification(session, request.user)
        return JsonResponse({
            'ok':          True,
            'activated':   activated,
            'all_confirmed': session.all_confirmed,
            'status':      session.status,
        })

    if action == 'cancel':
        session.status = PerformerSession.STATUS_CANCELLED
        session.save(update_fields=['status'])
        return JsonResponse({'ok': True})

    # Default: end session
    staff_notes = (data.get('staff_notes') or '').strip()
    staff_rating = None
    try:
        raw = data.get('staff_rating')
        if raw:
            val = int(raw)
            staff_rating = val if 1 <= val <= 5 else None
    except (ValueError, TypeError):
        pass

    session.status       = PerformerSession.STATUS_COMPLETED
    session.ended_at     = timezone.now()
    session.staff_rating = staff_rating
    session.staff_notes  = staff_notes
    session.save(update_fields=['status', 'ended_at', 'staff_rating', 'staff_notes'])

    unconfirmed = []
    if not session.performer_checked_in and session.performer:
        unconfirmed.append(session.performer.name)
    if session.second_performer and not session.second_performer_checked_in:
        unconfirmed.append(session.second_performer.name)
    if unconfirmed:
        _fire_unverified_alert(session, unconfirmed)

    return JsonResponse({
        'ok':                   True,
        'duration_hours':       session.duration_hours,
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
    session  = get_object_or_404(PerformerSession, id=session_id, business=business)

    if session.payment_status == PerformerSession.PAYMENT_PAID:
        return JsonResponse({'ok': False, 'error': 'Already paid'}, status=400)

    if not session.all_confirmed:
        return JsonResponse(
            {'ok': False, 'error': 'Sesheni bado haijathibitishwa na pande zote. Malipo hayawezi kufanywa.'},
            status=400,
        )

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    payment_method = data.get('payment_method', 'cash')
    if payment_method not in ('cash', 'mpesa'):
        payment_method = 'cash'

    now              = timezone.now()
    performer_name   = session.performer.name if session.performer else 'DJ/MC'
    second_fee       = session.second_performer_fee or Decimal('0')
    total_fee        = session.agreed_fee + second_fee
    dur_label        = f", {session.duration_hours}h" if session.duration_hours else ''
    start_lbl        = timezone.localtime(session.started_at).strftime('%H:%M') if session.started_at else ''
    end_lbl          = timezone.localtime(session.ended_at).strftime('%H:%M')   if session.ended_at   else ''
    time_label       = f" ({start_lbl}–{end_lbl}{dur_label})" if start_lbl else ''

    if session.second_performer and second_fee > 0:
        p2_name      = session.second_performer.name
        p1_fee_fmt   = f"{int(session.agreed_fee):,}"
        p2_fee_fmt   = f"{int(second_fee):,}"
        expense_desc = f"DJ/MC — {performer_name} & {p2_name} (DJ: {p1_fee_fmt} + MC: {p2_fee_fmt}){time_label}"
    else:
        expense_desc = f"DJ/MC — {performer_name}{time_label}"

    expense = BusinessExpense.objects.create(
        business=business,
        description=expense_desc,
        amount=total_fee,
        category='entertainment',
        date=session.date,
        notes=f"Session {session.date}, {payment_method}",
    )

    session.payment_status = PerformerSession.PAYMENT_PAID
    session.payment_method = payment_method
    session.paid_at        = now
    session.expense        = expense
    session.save(update_fields=['payment_status', 'payment_method', 'paid_at', 'expense'])

    _send_payment_sms(session)

    return JsonResponse({'ok': True, 'expense_id': expense.id})


@login_required
def session_checkin_poll(request, session_id):
    """Bar board polls this every 30s to see if performer has checked in."""
    up, err = _staff_or_owner(request)
    if err:
        return err
    session = get_object_or_404(PerformerSession, id=session_id, business=up.business)
    return JsonResponse({
        'ok':                          True,
        'status':                      session.status,
        'all_confirmed':               session.all_confirmed,
        'performer_checked_in':        session.performer_checked_in,
        'performer_checkin_at':        timezone.localtime(session.performer_checkin_at).strftime('%H:%M') if session.performer_checkin_at else None,
        'second_performer_checked_in': session.second_performer_checked_in,
        'second_performer_checkin_at': timezone.localtime(session.second_performer_checkin_at).strftime('%H:%M') if session.second_performer_checkin_at else None,
        'staff_confirmed':             session.staff_confirmed,
        'staff_confirmed_at':          timezone.localtime(session.staff_confirmed_at).strftime('%H:%M') if session.staff_confirmed_at else None,
    })


# ── Session History (owner) ───────────────────────────────────────────────────

@login_required
def session_list(request):
    up, err = _owner_required(request)
    if err:
        return redirect('home')
    business = up.business

    filter_performer = request.GET.get('performer', '')
    filter_from      = request.GET.get('from', '')
    filter_to        = request.GET.get('to', '')

    qs = (
        PerformerSession.objects
        .filter(business=business)
        .select_related('performer', 'second_performer')
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
        'sessions':         qs[:200],
        'performers':       performers,
        'filter_performer': filter_performer,
        'filter_from':      filter_from,
        'filter_to':        filter_to,
        'business':         business,
        'is_owner':         True,
    })


# ── Public: Performer Check-In ────────────────────────────────────────────────

def session_checkin_public(request, token):
    """
    Public — no login. DJ/MC taps confirm on their phone.
    Handles both primary and second-performer tokens via the same URL.
    After confirming, shows payment status (not amount) so performer
    can bookmark the page to check if they've been paid.
    """
    # Determine which performer this token belongs to
    session     = None
    is_second   = False
    try:
        session = PerformerSession.objects.select_related(
            'performer', 'second_performer', 'business'
        ).get(checkin_token=token)
    except PerformerSession.DoesNotExist:
        try:
            session = PerformerSession.objects.select_related(
                'performer', 'second_performer', 'business'
            ).get(second_performer_checkin_token=token)
            is_second = True
        except PerformerSession.DoesNotExist:
            from django.http import Http404
            raise Http404

    if request.method == 'POST':
        if is_second:
            if not session.second_performer_checked_in:
                session.second_performer_checked_in = True
                session.second_performer_checkin_at = timezone.now()
                session.save(update_fields=['second_performer_checked_in', 'second_performer_checkin_at'])
        else:
            if not session.performer_checked_in:
                session.performer_checked_in = True
                session.performer_checkin_at = timezone.now()
                session.save(update_fields=['performer_checked_in', 'performer_checkin_at'])

        # Auto-activate if all parties have now confirmed
        if _maybe_activate(session):
            # Notify owner — use system as the trigger since it's performer-initiated
            _fire_session_started_notification(session, session.created_by or session.business.users.filter(role='owner').first().user)

        return JsonResponse({'ok': True, 'all_confirmed': session.all_confirmed})

    performer = session.second_performer if is_second else session.performer
    already_checked_in = session.second_performer_checked_in if is_second else session.performer_checked_in

    return render(request, 'core/performer_checkin_public.html', {
        'session':            session,
        'performer':          performer,
        'is_second':          is_second,
        'already_checked_in': already_checked_in,
    })


# ── Public: Customer Feedback ─────────────────────────────────────────────────

def session_feedback_public(request, token):
    """
    Public — no login. Customers scan QR and submit 1–5 star rating.
    Dedup is handled client-side via localStorage (per device, per session).
    """
    session = get_object_or_404(PerformerSession, feedback_token=token)

    if request.method == 'POST':
        try:
            rating = int(request.POST.get('rating', 0))
        except (ValueError, TypeError):
            rating = 0
        if 1 <= rating <= 5:
            comment = (request.POST.get('comment') or '').strip()[:500]
            PerformerFeedback.objects.create(
                session=session, rating=rating, comment=comment,
            )
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'Chagua nyota kwanza (1–5).'})

    feedback_url = request.build_absolute_uri(f'/p/{token}/')
    return render(request, 'core/performer_feedback_public.html', {
        'session':      session,
        'feedback_url': feedback_url,
    })


# ── Public: Live Display (TV / second monitor) ────────────────────────────────

def session_live_display(request, token):
    """
    Public full-screen display page for a TV or secondary screen.
    Shows performer name + large feedback QR so customers at tables can scan.
    No login required — keyed off feedback_token (unguessable UUID).
    Auto-refreshes every 30 s. ?print=1 triggers browser print on load.
    """
    session      = get_object_or_404(PerformerSession, feedback_token=token)
    feedback_url = request.build_absolute_uri(f'/p/{token}/')
    auto_print   = request.GET.get('print') == '1'
    return render(request, 'core/session_live.html', {
        'session':      session,
        'feedback_url': feedback_url,
        'auto_print':   auto_print,
    })


# ── Owner: Schedule upcoming session ─────────────────────────────────────────

@login_required
@require_POST
def session_schedule(request):
    """Owner-only: create a SCHEDULED (future) session — generates a promo URL."""
    up, err = _owner_required(request)
    if err:
        return err
    business = up.business

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    performer_id  = data.get('performer_id')
    agreed_fee    = data.get('agreed_fee', 0)
    schedule_date = data.get('schedule_date', '')
    start_time    = data.get('start_time', '')

    if not performer_id:
        return JsonResponse({'ok': False, 'error': 'Chagua mwanamuziki.'})
    if not schedule_date:
        return JsonResponse({'ok': False, 'error': 'Weka tarehe ya onyesho.'})

    try:
        performer = Performer.objects.get(id=performer_id, business=business)
    except Performer.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Mwanamuziki hakupatikana.'})

    try:
        fee = Decimal(str(agreed_fee or 0))
    except Exception:
        fee = Decimal('0')

    try:
        parsed_date = datetime.date.fromisoformat(schedule_date)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Tarehe si sahihi.'})

    if parsed_date <= timezone.localdate():
        return JsonResponse({'ok': False, 'error': 'Chagua siku ya kesho au baadaye.'})

    parsed_time = None
    if start_time:
        try:
            parsed_time = datetime.time.fromisoformat(start_time)
        except ValueError:
            pass

    session = PerformerSession.objects.create(
        business=business,
        performer=performer,
        date=parsed_date,
        scheduled_start_time=parsed_time,
        agreed_fee=fee,
        status=PerformerSession.STATUS_SCHEDULED,
        created_by=request.user,
    )

    promo_url = request.build_absolute_uri(f'/p/{session.feedback_token}/promo/')
    return JsonResponse({'ok': True, 'session_id': session.id, 'promo_url': promo_url})


# ── Public: Upcoming session promo page ──────────────────────────────────────

def session_promo_page(request, token):
    """
    Public promo page for a scheduled/upcoming session.
    Shareable on WhatsApp before the event to attract customers.
    No login required — keyed off feedback_token (unguessable UUID).
    ?print=1 triggers browser print on load.
    """
    session   = get_object_or_404(PerformerSession, feedback_token=token)
    promo_url = request.build_absolute_uri(f'/p/{token}/promo/')
    auto_print = request.GET.get('print') == '1'
    return render(request, 'core/session_promo_page.html', {
        'session':    session,
        'promo_url':  promo_url,
        'auto_print': auto_print,
    })


# ── Owner: SMS blast to registered customers ──────────────────────────────────

@login_required
@require_POST
def session_announce(request, session_id):
    """
    One-shot SMS to all customers with a known phone, announcing the live session
    and linking to the feedback page. Capped at 200 to control AT costs. Owner-only.
    """
    up, err = _owner_required(request)
    if err:
        return err
    business = up.business
    session  = get_object_or_404(PerformerSession, id=session_id, business=business)

    if session.status != PerformerSession.STATUS_ACTIVE:
        return JsonResponse({'ok': False, 'error': 'Sesheni haipo hai sasa hivi.'})

    feedback_url   = request.build_absolute_uri(f'/p/{session.feedback_token}/')
    performer_name = session.performer.name if session.performer else 'DJ/MC'
    message = (
        f"\U0001f3a4 Usiku wa leo: {performer_name} LIVE @ {business.name}! "
        f"Piga kura yako hapa: {feedback_url}"
    )

    phones = list(
        Customer.objects
        .filter(business=business)
        .exclude(phone='').exclude(phone__isnull=True)
        .values_list('phone', flat=True)[:200]
    )

    sent = 0
    for raw_phone in phones:
        try:
            phone = normalize_ke_phone(raw_phone)
            if phone:
                send_sms_notification(message, phone)
                sent += 1
        except Exception:
            logger.exception("Announce SMS failed for phone %s", raw_phone)

    return JsonResponse({'ok': True, 'sent': sent, 'total': len(phones)})
