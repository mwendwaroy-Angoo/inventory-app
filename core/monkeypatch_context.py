"""Compatibility monkeypatch for Django template Context copying.

Some Django versions may call `copy(super())` inside
`BaseContext.__copy__`, which can break under certain Python/Django
combinations (raises AttributeError while test client captures rendered
templates). Patch `BaseContext.__copy__` with a safer implementation at
app startup.
"""
from copy import copy as _copy

try:
    from django.template import context as template_context
except Exception:
    template_context = None


def _safe_basecontext_copy(self):
    """Create a shallow duplicate of the context preserving `dicts`.

    This avoids copying the `super()` proxy object which is not
    copyable in some environments.
    """
    # Create instance without calling __init__ to avoid side-effects
    duplicate = object.__new__(self.__class__)
    # shallow-copy the dict stack
    try:
        duplicate.dicts = self.dicts[:]
    except Exception:
        # Fallback: try to copy the whole object
        return _copy(self)

    # Copy commonly-used attributes if present
    for attr in ("render_context", "template", "template_name", "autoescape", "use_l10n", "use_tz"):
        if hasattr(self, attr):
            try:
                setattr(duplicate, attr, _copy(getattr(self, attr)))
            except Exception:
                setattr(duplicate, attr, getattr(self, attr))

    return duplicate


if template_context is not None:
    try:
        template_context.BaseContext.__copy__ = _safe_basecontext_copy
    except Exception:
        # Best-effort patch; do not raise during import
        pass
