"""
Production settings for ShiftSync.
Deployed on Fly.io — TLS terminated at the Fly proxy layer.
"""

from .base import *  # noqa: F401, F403

DEBUG = False

# Cookie security — enforce secure cookies (Fly guarantees HTTPS externally)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS — instruct browsers to only use HTTPS for this domain
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Fly apps get a *.fly.dev domain by default.
# Add your custom domain here too if you attach one.
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["shiftsync.fly.dev", "localhost", "*"],
)

# Email: simulated via console for the demo (no SMTP needed)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Structured logging to stdout — Fly captures and indexes these automatically
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            # Use a simple format; swap for python-json-logger in real prod
            "format": "%(levelname)s %(asctime)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
