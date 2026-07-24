from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import translation


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


class SingleSessionMiddleware:
    """
    Enforces one active session per user.

    When a user logs in from a new device/browser, anyone still using the old
    session is logged out on their next request and shown a message.

    Bypass: set UserProfile.allow_concurrent_sessions = True via Django admin
    (intended for the developer who tests across multiple devices simultaneously).
    Django superusers are also always exempt.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_superuser:
            # 2026-07-25: Django's AuthenticationForm only checks User.is_active at
            # LOGIN — a staffer deactivated (accounts.views.deactivate_staff) mid-session
            # would otherwise keep working normally for up to SESSION_COOKIE_AGE (24h).
            # This middleware already runs on every request for exactly this class of
            # "kick them out now" enforcement (stale-session logout below), so it's the
            # natural place to also enforce deactivation taking effect immediately.
            if not request.user.is_active:
                logout(request)
                messages.warning(
                    request,
                    'Akaunti yako haipatikani tena. Wasiliana na mmiliki wa biashara.',
                )
                return redirect('login')
            profile = getattr(request.user, 'userprofile', None)
            if profile and not profile.allow_concurrent_sessions:
                stored = profile.current_session_key
                current = request.session.session_key
                if stored and current and stored != current:
                    logout(request)
                    messages.warning(
                        request,
                        'Umefunguliwa nje — akaunti yako imefunguliwa kwenye kifaa kingine. '
                        'Logged out: your account was signed in on another device.',
                    )
                    return redirect('login')
        return self.get_response(request)
