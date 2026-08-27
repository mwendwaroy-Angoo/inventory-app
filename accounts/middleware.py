from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import SESSION_KEY, logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone, translation

# How stale `UserProfile.last_seen_at` may be before we bother writing a
# fresh value. Throttles the write to roughly once per this many minutes per
# user instead of once per request — see UserProfile.last_seen_at's own
# docstring for why (avoids adding to the already-documented DB write-load
# concern behind this app's 502 incidents).
ACTIVITY_STALE_MINUTES = 5


class UserLanguageMiddleware:
    """
    Language activation middleware:
    - Authenticated users: always use their saved preferred_language from UserProfile.
    - Unauthenticated users on a familiar device (duka_device_language cookie present):
      keep the language activated by LocaleMiddleware from the django_language cookie,
      so the homepage/login page shows in their language.
    - Unauthenticated users on an unfamiliar device: reset to site default (English) so
      public pages always start in English; after login the preferred language kicks in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'userprofile', None)
            if profile and profile.preferred_language:
                translation.activate(profile.preferred_language)
                request.LANGUAGE_CODE = profile.preferred_language
            else:
                request.LANGUAGE_CODE = translation.get_language() or settings.LANGUAGE_CODE
        else:
            # Only honour the stored language for devices the user has explicitly
            # set up (i.e. the 'remember this device' cookie is present).
            is_familiar_device = (
                request.COOKIES.get(settings.DEVICE_LANGUAGE_COOKIE_NAME) == '1'
            )
            if is_familiar_device:
                # LocaleMiddleware already activated the language from the
                # django_language cookie — just mirror it onto the request.
                request.LANGUAGE_CODE = translation.get_language() or settings.LANGUAGE_CODE
            else:
                # Unknown device: force English on all public pages so guests
                # never see a translated page they didn't ask for.
                translation.activate(settings.LANGUAGE_CODE)
                request.LANGUAGE_CODE = settings.LANGUAGE_CODE

        response = self.get_response(request)
        return response


def _is_ajax_request(request):
    """
    Best-effort "is this a fetch()/XHR call, not a real page navigation" check.

    Sec-Fetch-Mode (sent by every modern browser, including iOS Safari since
    ~2021) is 'navigate' for a real top-level page load/form submit and
    'cors'/'same-origin'/'no-cors' for a fetch()/XHR resource request — the
    most reliable signal available, since this app's own fetch() calls don't
    consistently set an Accept or X-Requested-With header (confirmed by grep
    before relying on either as the primary signal). Falls back to those two
    anyway for the rare browser that omits Sec-Fetch-Mode, and finally to
    "treat as a real navigation" (the original, safe default) if none apply.
    """
    sec_fetch_mode = request.headers.get('Sec-Fetch-Mode', '')
    if sec_fetch_mode:
        return sec_fetch_mode != 'navigate'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if 'application/json' in request.headers.get('Accept', ''):
        return True
    return False


class SingleSessionMiddleware:
    """
    Enforces one active session per user.

    When a user logs in from a new device/browser, anyone still using the old
    session is logged out on their next request and shown a message.

    Bypass: set UserProfile.allow_concurrent_sessions = True via Django admin
    (intended for the developer who tests across multiple devices simultaneously).
    Django superusers are also always exempt.

    2026-08-14: a real page navigation gets a clean redirect('login') (unchanged,
    the message shows immediately). But most of this app's screens are driven by
    background fetch() polls (notifications count, tab lists, dashboard revenue,
    shift status, etc.) with no central JS wrapper to catch a redirect — a kicked
    party sitting on one of those screens would silently receive an HTML login
    page back from their next poll (fetch() follows redirects by default), which
    every JSON-parsing handler in the app then fails on with a confusing generic
    error instead of a clean "you were logged out" message — reported live as one
    of two people sharing a login (an owner + the developer checking in on their
    account) "getting a hard time" being booted while the other never noticed a
    problem, since whoever logs in LAST is never the one hitting this path. Fixed
    by detecting an AJAX/fetch request (_is_ajax_request) and returning a plain
    JSON 401 instead of an HTML redirect — paired with a small global fetch()
    interceptor in base.html that watches for this exact shape and immediately
    forces a full-page redirect to the login page (which still shows the same
    bilingual message, since logout()+messages.warning() already ran on this
    same request/response cycle before either response is built) — so the
    booted side gets an instant, clear redirect the moment ANY background call
    notices, not a silently broken poll.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _kick(self, request, message):
        logout(request)
        messages.warning(request, message)
        if _is_ajax_request(request):
            return JsonResponse(
                {'ok': False, 'error': 'logged_out', 'message': message, 'redirect': '/accounts/login/'},
                status=401,
            )
        return redirect('login')

    def __call__(self, request):
        # 2026-07-25 / found 2026-08-14: Django's AuthenticationForm only checks
        # User.is_active at LOGIN — a staffer deactivated (accounts.views.
        # deactivate_staff) mid-session was meant to be caught here on their very
        # next request. But Django's own AuthenticationMiddleware ALREADY resolves
        # request.user to AnonymousUser for a deactivated account before this
        # middleware ever runs (ModelBackend.user_can_authenticate() rejects an
        # inactive user), so `if not request.user.is_active` below was silently
        # unreachable the whole time — the check for "is_authenticated" always
        # failed first, meaning the deactivated staffer just landed on the public
        # page with zero explanation and their session was never actually flushed
        # server-side (only self-healed the next time the cookie expired). Caught
        # while adding the 2026-08-14 AJAX-kick fix above, whose own test exposed
        # it. request.session still carries the raw _auth_user_id even though
        # AuthenticationMiddleware refused to resolve it to a real user — that's
        # the one reliable signal available at this point to tell "deactivated
        # mid-session" apart from "never logged in" or "password changed elsewhere"
        # (the latter already gets its own session flush inside Django's own
        # auth.get_user(), so _auth_user_id is already gone by the time we'd see it).
        if not request.user.is_authenticated and request.session.get(SESSION_KEY):
            return self._kick(
                request,
                'Akaunti yako haipatikani tena. Wasiliana na mmiliki wa biashara.',
            )
        if request.user.is_authenticated and not request.user.is_superuser:
            profile = getattr(request.user, 'userprofile', None)
            if profile and not profile.allow_concurrent_sessions:
                stored = profile.current_session_key
                current = request.session.session_key
                if stored and current and stored != current:
                    return self._kick(
                        request,
                        'Umefunguliwa nje — akaunti yako imefunguliwa kwenye kifaa kingine. '
                        'Logged out: your account was signed in on another device.',
                    )
            if profile:
                self._stamp_activity(profile)
        return self.get_response(request)

    @staticmethod
    def _stamp_activity(profile):
        """Throttled UserProfile.last_seen_at write — see its own docstring.
        A queryset .update() (not profile.save()) so this never triggers a
        full model save for an unrelated field on every request."""
        now = timezone.now()
        stale_cutoff = now - timedelta(minutes=ACTIVITY_STALE_MINUTES)
        if profile.last_seen_at and profile.last_seen_at >= stale_cutoff:
            return
        type(profile).objects.filter(pk=profile.pk).update(last_seen_at=now)
        profile.last_seen_at = now
