import json


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
