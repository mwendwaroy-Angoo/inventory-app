from django.conf import settings
from django.utils import translation


class UserLanguageMiddleware:
    """Activate the user's preferred language from their profile."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        preferred_language = None
        if request.user.is_authenticated:
            profile = getattr(request.user, 'userprofile', None)
            if profile and profile.preferred_language:
                preferred_language = profile.preferred_language
                translation.activate(preferred_language)
                request.LANGUAGE_CODE = preferred_language

        response = self.get_response(request)

        if request.user.is_authenticated:
            profile = getattr(request.user, 'userprofile', None)
            if profile and profile.preferred_language:
                preferred_language = profile.preferred_language

        if preferred_language and request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) != preferred_language:
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                preferred_language,
                max_age=365 * 24 * 60 * 60,
            )

        return response
