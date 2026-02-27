"""
ShiftSync root URL configuration.

Namespaces:
  accounts:      login, logout, profile, availability, staff list
  scheduling:    dashboard, schedule, my-shifts, swaps, on-duty
  locations:     list, detail, certify
  notifications: center, mark-read
  analytics:     overview
  audit:         log (+ CSV export)
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils import timezone


def health_check(request):
    """
    Lightweight liveness probe for Fly.io health checks.

    Verifies the DB connection is reachable and returns 200/503.
    """
    try:
        from django.db import connection
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "db": db_ok,
         "timestamp": timezone.now().isoformat()},
        status=200 if db_ok else 503,
    )


urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("admin/", admin.site.urls),

    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", include("apps.scheduling.urls", namespace="scheduling")),
    path("locations/", include("apps.locations.urls", namespace="locations")),
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    path("analytics/", include("apps.analytics.urls", namespace="analytics")),
    path("audit/", include("apps.audit.urls", namespace="audit")),
]

handler403 = "django.views.defaults.permission_denied"
handler404 = "django.views.defaults.page_not_found"