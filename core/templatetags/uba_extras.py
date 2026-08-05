from django import template

register = template.Library()


@register.filter
def vocab(word, biz_profile):
    """UBA §2.4/M0-4 — per-business-type word substitution.

    Usage: {{ 'item'|vocab:biz_profile }}

    The master spec's illustrative usage is `{{ 'item'|vocab }}` with no
    argument, but a plain Django template filter cannot read the surrounding
    template context on its own (only simple_tag/inclusion_tag can) — so the
    profile has to be passed explicitly. `biz_profile` is already injected
    into every template's context by core.context_processors.business_profile,
    so passing it costs nothing at the call site: {{ 'item'|vocab:biz_profile }}.

    Falls back to `word` itself when there's no capability, or the capability
    has no substitution for this word. Every one of the 8 real profiles today
    has an empty `vocabulary` dict (see business_profiles.py's CAPABILITIES
    registry) — no profile has ever populated one yet, so calling this
    anywhere today is a no-op by construction, not just by accident.
    """
    try:
        if isinstance(biz_profile, dict):
            capability = biz_profile.get('capability')
        else:
            capability = getattr(biz_profile, 'capability', None)
        vocabulary = getattr(capability, 'vocabulary', None) or {}
        return vocabulary.get(word, word)
    except Exception:
        return word
