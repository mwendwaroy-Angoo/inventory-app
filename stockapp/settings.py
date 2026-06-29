"""
Django settings for stockapp project.
"""

import os
import dj_database_url
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECRET_KEY = os.getenv(
    "SECRET_KEY", "django-insecure-#e-h-#sy&d7=%m2v$2g%&x_odbmwfi(%5zoeewt1(@y!$607ju"
)

DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1")

ALLOWED_HOSTS = [
    "dukamwecheche.co.ke",
    "www.dukamwecheche.co.ke",
    "stock-made-simpler-sms.onrender.com",
    "localhost",
    "127.0.0.1",
]

# Allow the Django test client to use the default 'testserver' host during
# local test runs without modifying production settings.
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

# Django 4.0+ requires CSRF_TRUSTED_ORIGINS for HTTPS form submissions.
# Without this, any HTTPS POST can fail CSRF validation — especially after
# Render free-tier cold starts where the process/cookie context resets.
CSRF_TRUSTED_ORIGINS = [
    'https://dukamwecheche.co.ke',
    'https://www.dukamwecheche.co.ke',
    'https://stock-made-simpler-sms.onrender.com',
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "core",
    "django_bootstrap5",
    "accounts",
]

# Optional: only add django_celery_beat if installed in the current environment.
# This prevents ModuleNotFoundError when running with a Python interpreter that
# doesn't have it (e.g. system Python instead of the project venv).
try:
    import importlib.util as _ilu

    if _ilu.find_spec("django_celery_beat") is not None:
        INSTALLED_APPS.insert(INSTALLED_APPS.index("core"), "django_celery_beat")
except Exception:
    pass


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.SingleSessionMiddleware",
    "core.middleware.ShiftEnforcementMiddleware",
]

ROOT_URLCONF = "stockapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.onboarding_context",
                "core.context_processors.business_profile",
            ],
        },
    },
]

WSGI_APPLICATION = "stockapp.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL", f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# ── INTERNATIONALIZATION (Kenyan Languages) ───────────────────────────────────
LOCALE_PATHS = [BASE_DIR / "locale"]

LANGUAGES = [
    ("en", "English"),
    ("sw", "Kiswahili"),
    ("ki", "Gĩkũyũ"),
    ("luo", "Dholuo"),
    ("kln", "Kalenjin"),
    ("kam", "Kĩkamba"),
    ("luy", "Luhya"),
    ("guz", "Ekegusii"),
    ("mer", "Kĩmĩrũ"),
    ("mas", "Maa (Maasai)"),
    ("tuv", "Ng'aturkana"),
    ("so", "Soomaali"),
    ("dav", "Kitaita"),
    ("pko", "Pokot"),
    ("teo", "Ateso"),
    ("saq", "Samburu"),
    ("ebu", "Kĩembu"),
]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

LOGIN_REDIRECT_URL = "/business/role-redirect/"
LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"
DEVICE_LANGUAGE_COOKIE_NAME = "duka_device_language"

# ── EMAIL ─────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("GMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("GMAIL_USER", "")
EMAIL_TIMEOUT = 5  # seconds — fail fast instead of hanging
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Duka Mwecheche <onboarding@resend.dev>')

# ── AFRICA'S TALKING ──────────────────────────────────────────────────────────
AT_USERNAME = os.getenv("AT_USERNAME", "dukamwecheche")
AT_API_KEY = os.getenv("AT_API_KEY", "")

# ── TWILIO ────────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

# ── CRON ──────────────────────────────────────────────────────────────────────
CRON_SECRET = os.getenv("CRON_SECRET", "duka-mwecheche-cron-2026")

# ── M-PESA DARAJA ─────────────────────────────────────────────────────────────
MPESA_ENV = os.getenv("MPESA_ENV", "sandbox")  # 'sandbox' or 'production'
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY", "")

# ── RENDER / REVERSE PROXY ────────────────────────────────────────────────────
# Render terminates TLS at the edge and forwards to Django over plain HTTP
# internally, adding X-Forwarded-Proto: https. Without this, request.is_secure()
# returns False, breaking CSRF validation and HTTPS-only cookie behaviour.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Force-upgrade any stray HTTP hit to HTTPS (Render also does this, but belt+braces).
SECURE_SSL_REDIRECT = not DEBUG

# CSRF and session cookies must only travel over HTTPS in production.
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG

# ── SESSION ───────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 86400        # 24 hours — keeps retail owners logged in all day
SESSION_SAVE_EVERY_REQUEST = True  # Refresh session expiry on every request

# ── REST FRAMEWORK ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}


# Celery configuration
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ── LOGGING ────────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "core": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.notifications": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}


# Optional Beat schedule: default nightly precompute at 02:15 if celery is installed
try:
    from celery.schedules import crontab
except Exception:
    crontab = None

if crontab:
    CELERY_BEAT_SCHEDULE = {
        "precompute-forecasts-nightly": {
            "task": "core.tasks.precompute_forecasts_task",
            "schedule": crontab(hour=2, minute=15),
            "args": (),
        },
    }
