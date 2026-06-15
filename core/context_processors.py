import json

from .business_profiles import get_profile, DEFAULT_PROFILE


def business_profile(request):
    """Injects biz_profile into every template."""
    if not request.user.is_authenticated:
        return {'biz_profile': DEFAULT_PROFILE}
    try:
        profile = get_profile(request.user.userprofile.business)
        return {'biz_profile': profile}
    except Exception:
        return {'biz_profile': DEFAULT_PROFILE}


def onboarding_context(request):
    """Makes tour_sections_seen available in every template."""
    if request.user.is_authenticated:
        try:
            seen = request.user.userprofile.onboarding_sections_seen or []
            return {
                'tour_sections_seen': json.dumps(seen),
                'tour_sections_seen_list': seen,
            }
        except Exception:
            pass
    return {
        'tour_sections_seen': '[]',
        'tour_sections_seen_list': [],
    }
