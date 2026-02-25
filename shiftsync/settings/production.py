"""
Production settings for ShiftSync.
Deployed on Fly.io — TLS terminated at the Fly proxy layer.
"""

from .base import *  # noqa: F401, F403

DEBUG = False

# Fly.io terminates TLS at the edge proxy, then forwards internally via HTTP.
# Do NOT redirect HTTP→HTTPS ourselves (Fly already handles that).
# Trust the X-Forwarded-Proto header that Fly injects on every request.
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

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
    default=["shiftsync.fly.dev", "localhost"],
)

# CSRF trusted origins must match the public URL(s) the app is served from
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["https://shiftsync.fly.dev"],
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
