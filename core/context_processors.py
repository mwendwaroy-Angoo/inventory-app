import json

from .business_profiles import get_profile, DEFAULT_PROFILE


def business_profile(request):
    """Injects biz_profile AND business into every template.

    2026-08-01 live report: Roy asked whether the owner has to keep going
    into Business Settings to reach Haki — traced further than the navbar
    link's position (see HakiNavbarGroupedWithStaffTest) to a real,
    app-wide bug: base.html gates 8 navbar link instances on
    `{% if business.haki_enabled %}` (Kazi Yangu ×6, Haki — Staff/Payroll
    Run ×2), but NO context processor ever provided a top-level `business`
    variable — only ~18 individual views across the whole app happen to
    pass `'business': business` in their own context dict. Every OTHER
    view, including home() (the very first page after login), rendered
    with `business` undefined, so these links silently never appeared
    there regardless of haki_enabled's real value. Adding `business` here
    (Django context processors run first; any view's own explicit
    'business' key in its context dict still wins, so this only fills the
    gap, never overrides) makes the gate reliable on every page.
    """
    if not request.user.is_authenticated:
        return {'biz_profile': DEFAULT_PROFILE, 'business': None}
    try:
        biz = request.user.userprofile.business
        profile = get_profile(biz)
        return {'biz_profile': profile, 'business': biz}
    except Exception:
        return {'biz_profile': DEFAULT_PROFILE, 'business': None}


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
