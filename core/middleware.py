"""
Shift Enforcement Middleware.

For staff users in bar businesses (has keg items), blocks access to all
operational pages unless they have personally opened an active shift.
Owners are never blocked. Non-bar businesses are never affected.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve

# URL names that are always allowed regardless of shift status
_SHIFT_EXEMPT_NAMES = {
    'login', 'logout', 'home',
    # shift endpoints themselves
    'open_shift', 'close_shift', 'confirm_shift', 'confirm_barrel_weights',
    'active_shift_api', 'shift_history',
    # bar board (needed to open shift)
    'bar_board', 'bar_board_api',
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

        # Waitresses don't have shifts — never block them
        if getattr(up, 'role', '') == 'waitress':
            return False

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
        return True
