from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'
    
    def ready(self):
        # Import monkeypatch early to fix template Context copy issues in tests
        try:
            from . import monkeypatch_context  # noqa: F401
        except Exception:
            pass
