"""
ShiftSync root URL configuration.

URL namespaces follow the pattern: app_name:view_name
  - accounts:    login, logout, profile, availability
  - scheduling:  dashboard, shifts, swaps
  - notifications: center, preferences
  - analytics:   fairness, overtime
  - audit:       log, export
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils import timezone


def health_check(request):
    """
    Lightweight health check endpoint for Fly.io TCP/HTTP checks.

    Returns 200 OK with a JSON body confirming the app and DB are reachable.
    Fly.io polls this every 10 seconds to decide if the machine is healthy.
    """
    # Ping the database to catch connection issues early
    try:
        from django.db import connection
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "timestamp": timezone.now().isoformat(),
        },
        status=status,
    )


urlpatterns = [
    # Fly.io health check (no auth required, must be fast)
    path("health/", health_check, name="health_check"),

    # Django admin
    path("admin/", admin.site.urls),

    # App modules
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", include("apps.scheduling.urls", namespace="scheduling")),
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    path("analytics/", include("apps.analytics.urls", namespace="analytics")),
    path("audit/", include("apps.audit.urls", namespace="audit")),
]