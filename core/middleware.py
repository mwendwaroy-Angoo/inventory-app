"""
Shift Enforcement Middleware.

For staff users in bar businesses (has keg items), blocks access to all
operational pages unless they have personally opened an active shift.
Owners are never blocked. Non-bar businesses are never affected.
"""
import logging
import time

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve

logger = logging.getLogger(__name__)


class SlowRequestLoggingMiddleware:
    """Logs any request taking >= SLOW_THRESHOLD_SECONDS, with method,
    path, duration, and status code.

    2026-08-18 live incident (Roy, repeated 502s — "why does this always
    have to happen, what else am I to do"): every prior fix for this
    failure signature (Render's health check timing out because all
    gunicorn threads were busy — 2026-08-09, 2026-08-11, 2026-08-12) was a
    good-faith inference from the FAILURE'S OWN timing, never a confirmed
    trace of which specific request was actually slow — this app's
    production logs had zero per-request duration visibility at all
    (Procfile's gunicorn has no --access-logfile, and nothing in Django
    itself timed a request). Each fix (more workers, shorter SMS
    timeouts, fully-async SMS) closed a real, evidenced gap, but "why does
    this keep happening" could never be answered with certainty because
    the actual slow request was never SEEN, only guessed at from the
    surrounding log lines.

    This closes that visibility gap going forward: the NEXT time gunicorn
    threads are saturated, Render's Logs tab will show a
    "SLOW REQUEST" line naming the exact path and duration BEFORE the
    health check ever times out, instead of another guess. Deliberately a
    thin, dependency-free middleware (no APM package) — wraps the full
    request/response cycle including every other middleware and the view
    itself, so it also catches CPU-bound and DB-bound slowness, not just
    the specific SMS-blocking-thread class already fixed.
    """
    SLOW_THRESHOLD_SECONDS = 3.0

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        duration = time.monotonic() - start
        if duration >= self.SLOW_THRESHOLD_SECONDS:
            logger.warning(
                'SLOW REQUEST %.2fs %s %s status=%s',
                duration, request.method, request.get_full_path(),
                getattr(response, 'status_code', '?'),
            )
        return response

# URL names that are always allowed regardless of shift status
_SHIFT_EXEMPT_NAMES = {
    'login', 'logout', 'home',
    # shift endpoints themselves
    'open_shift', 'close_shift', 'confirm_shift', 'confirm_barrel_weights',
    'active_shift_api', 'shift_history',
    # bar board (needed to open shift)
    'bar_board', 'bar_board_api',
    # kitchen board (needed for kitchen staff to open their shift)
    'kitchen_board',
    # receipts and debt — always accessible so staff can handle customers
    'receipts_list', 'public_receipt', 'send_receipt',
    'debt_dashboard', 'customer_debt_profile', 'record_debt_payment',
    # auth / misc
    'health_check', 'offline', 'manifest_json', 'service_worker',
    'password_change', 'password_change_done', 'password_reset',
    'signup', 'rider_signup', 'supplier_signup',
}

# URL path prefixes that are always allowed
_SHIFT_EXEMPT_PREFIXES = (
    '/admin/',
    '/static/',
    '/accounts/',
    '/business/',
    '/bar/shift/',    # shift endpoints including stock take
    '/bar/orders/',   # waitress order queue
    '/bar/',          # bar board
    '/stock/bar/',    # board API
    '/mpesa/',        # M-Pesa STK push, callbacks, prompts — never block payment flows
    '/r/',            # public receipt pages (unauthenticated)
    '/receipts/',     # receipt history — always accessible
    '/kitchen/',      # kitchen/grill board — independent of bar shift system
    '/debt/',         # debt tracker — always accessible so staff can handle credit customers
    '/petty-cash/',   # petty cash recording — staff must be able to log during any shift state
    '/stock/owner-consumption/',  # Mmiliki Alichukua — a manager oversight/correction
                                   # action, not a sale; must never require the manager's
                                   # own shift (2026-08-12 — found via a real test exercising
                                   # a manager with no open shift; a pre-existing gap for
                                   # the existing void/settle endpoints too, not new here).
    '/sw.js',
    '/manifest.json',
    '/offline/',
    '/health/',
    '/ussd/',
    '/customer-ussd/',
)


class ShiftEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_enforce(request):
            role = getattr(request, '_shift_block_role', '')
            if role == 'kitchen':
                return redirect('kitchen_board')
            return redirect('bar_board')
        return self.get_response(request)

    def _should_enforce(self, request):
        # Only authenticated users
        if not request.user.is_authenticated:
            return False

        # Check path prefix whitelist first (fast)
        path = request.path
        for prefix in _SHIFT_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return False

        # Resolve URL name and check name whitelist
        try:
            match = resolve(path)
            if match.url_name in _SHIFT_EXEMPT_NAMES:
                return False
        except Exception:
            return False

        # Get user profile
        try:
            up = request.user.userprofile
        except Exception:
            return False

        # Owners are never blocked
        if up.is_owner:
            return False

        # Waitresses are always exempt — they have no shift concept
        if getattr(up, 'role', '') == 'waitress':
            return False

        # Kitchen staff are exempt by default; owner can require a shift via the permission toggle
        if getattr(up, 'role', '') == 'kitchen':
            if not getattr(up, 'kitchen_requires_shift', False):
                return False
            # kitchen_requires_shift=True → fall through to the shift check below

        # Only enforce for bar businesses (has keg items)
        from .models import Item
        has_keg = Item.objects.filter(
            store__business=up.business, is_keg=True
        ).exists()
        if not has_keg:
            return False

        # Check if this staff member has their own open shift
        from .models import Shift
        my_shift = Shift.objects.filter(
            business=up.business,
            status='OPEN',
            staff=request.user,
        ).first()
        if my_shift:
            return False

        # Blocked — add a message and redirect
        any_shift = Shift.objects.filter(
            business=up.business, status='OPEN'
        ).first()
        if any_shift:
            name = any_shift.staff.get_full_name() or any_shift.staff.username
            messages.warning(
                request,
                f'Shift imefunguliwa na {name}. '
                f'Fungua shift yako mwenyewe kwanza ili uweze kuendelea.'
            )
        else:
            messages.warning(
                request,
                'Fungua shift yako kwanza ili uweze kufanya kazi.'
            )
        # Store the role on the request so __call__ can pick the right redirect
        request._shift_block_role = getattr(up, 'role', '')
        return True
