from django.contrib.auth.signals import user_logged_in


def _on_user_logged_in(sender, request, user, **kwargs):
    if user.is_superuser:
        return
    profile = getattr(user, 'userprofile', None)
    if not profile or profile.allow_concurrent_sessions:
        return
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    if session_key:
        profile.current_session_key = session_key
        profile.save(update_fields=['current_session_key'])


user_logged_in.connect(_on_user_logged_in)
