from django.utils import translation


class UserLanguageMiddleware:
    """Activate the user's preferred language from their profile."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'userprofile', None)
            if profile and profile.preferred_language:
                translation.activate(profile.preferred_language)
                request.LANGUAGE_CODE = profile.preferred_language
        response = self.get_response(request)
        return response
