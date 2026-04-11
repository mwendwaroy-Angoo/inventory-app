from django.conf import settings
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
