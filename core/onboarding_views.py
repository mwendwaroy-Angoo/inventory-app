"""
Onboarding tour tracking views.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json


@login_required
@require_POST
def mark_section_seen(request):
    """
    Mark a tour section as seen for the current user.
    POST body: { "section": "stores" }
    """
    try:
        data = json.loads(request.body)
        section = data.get('section', '').strip()
    except (json.JSONDecodeError, AttributeError):
        section = request.POST.get('section', '').strip()

    if not section:
        return JsonResponse({'error': 'section required'}, status=400)

    try:
        profile = request.user.userprofile
        seen = profile.onboarding_sections_seen or []
        if section not in seen:
            seen.append(section)
            profile.onboarding_sections_seen = seen
            profile.save(update_fields=['onboarding_sections_seen'])
        return JsonResponse({'ok': True, 'seen': seen})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
