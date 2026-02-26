"""
Base Django settings for ShiftSync.

All environment-specific settings (local.py, production.py) extend this module.
Values that MUST be overridden per environment are marked with # REQUIRED OVERRIDE.
"""

import os
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# django-environ reads from .env file or OS environment
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY")  # REQUIRED OVERRIDE
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "channels",
    "crispy_forms",
    "crispy_tailwind",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.locations",
    "apps.scheduling",
    "apps.notifications",
    "apps.analytics",
    "apps.audit",
    "core",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.TimezoneMiddleware",  # Activates location timezone in views
]

ROOT_URLCONF = "shiftsync.urls"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.unread_notification_count",
                "core.context_processors.global_context"
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# ASGI / Channels
# ---------------------------------------------------------------------------
ASGI_APPLICATION = "shiftsync.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("REDIS_URL", default="redis://localhost:6379/0")],
        },
    },
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST'),
        'PORT': env('DB_PORT'),
    }
}

DATABASES["default"]["ATOMIC_REQUESTS"] = True  # Wrap every request in a transaction

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization & Timezone
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # Server always runs in UTC; display timezone set per-request
USE_I18N = True
USE_TZ = True  # CRITICAL: all datetimes are timezone-aware

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Queues: default for general work, notifications for user-facing async events
CELERY_TASK_QUEUES = {
    "default": {},
    "notifications": {},
}
CELERY_TASK_DEFAULT_QUEUE = "default"

# ---------------------------------------------------------------------------
# Crispy Forms
# ---------------------------------------------------------------------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = "tailwind"

# ---------------------------------------------------------------------------
# ShiftSync Business Rules (override in settings if needed)
# ---------------------------------------------------------------------------
SHIFTSYNC = {
    # Minimum rest hours between consecutive shifts for the same employee
    "MIN_REST_HOURS": 10,
    # Weekly hours warning threshold (soft)
    "WEEKLY_HOURS_WARNING": 35,
    # Weekly hours hard block
    "WEEKLY_HOURS_HARD_LIMIT": 40,
    # Daily hours warning threshold
    "DAILY_HOURS_WARNING": 8,
    # Daily hours hard block
    "DAILY_HOURS_HARD_LIMIT": 12,
    # Consecutive days before warning
    "CONSECUTIVE_DAYS_WARNING": 6,
    # Consecutive days requiring documented manager override
    "CONSECUTIVE_DAYS_OVERRIDE": 7,
    # Default schedule edit cutoff (hours before shift start)
    "DEFAULT_EDIT_CUTOFF_HOURS": 48,
    # Max pending swap/drop requests per staff member
    "MAX_PENDING_SWAP_REQUESTS": 3,
    # Hours before shift start when unclaimed drop requests expire
    "DROP_REQUEST_EXPIRY_HOURS": 24,
    # Tags for "premium" shifts (day of week: 4=Friday, 5=Saturday)
    "PREMIUM_SHIFT_DAYS": [4, 5],
    # Hours considered "evening" for premium shift detection (24h format)
    "PREMIUM_SHIFT_START_HOUR": 17,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
