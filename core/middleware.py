"""
Core middleware and context processors for ShiftSync.

TimezoneMiddleware:
  Activates the timezone for each request based on the viewing context.
  For schedule views, the timezone is the location's timezone.
  For all other views, falls back to UTC.

  This means Django's template filters (|date, |time) automatically render
  in the correct timezone without manual conversion in every template.
"""

import logging
from zoneinfo import ZoneInfo

from django.utils import timezone

logger = logging.getLogger(__name__)


class TimezoneMiddleware:
    """
    Middleware that activates the appropriate timezone for each request.

    For requests with a `location_id` URL kwarg (schedule views),
    the location's timezone is activated. Otherwise UTC is used.

    This integrates with Django's USE_TZ=True setting so all template
    date/time rendering is automatically localized.
    """

    def __init__(self, get_response):
        """
        Initialize the middleware.

        Args:
            get_response: The next middleware or view in the chain.
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Activate the appropriate timezone before processing the request.

        Args:
            request: The incoming HTTP request.

        Returns:
            The HTTP response from the next layer.
        """
        tz = self._get_timezone_for_request(request)
        with timezone.override(tz):
            response = self.get_response(request)
        return response

    def _get_timezone_for_request(self, request) -> ZoneInfo:
        """
        Determine the timezone to activate for this request.

        Priority:
          1. Location timezone (if location_id is in the URL resolver match)
          2. User's preferred timezone (future: from profile)
          3. UTC (default)

        Args:
            request: The incoming HTTP request.

        Returns:
            ZoneInfo object for the appropriate timezone.
        """
        # Try to get location_id from the URL resolver
        try:
            resolver_match = request.resolver_match
            if resolver_match and "location_id" in resolver_match.kwargs:
                location_id = resolver_match.kwargs["location_id"]
                return self._get_location_timezone(location_id)
        except Exception:
            pass  # Fall through to default

        return ZoneInfo("UTC")

    @staticmethod
    def _get_location_timezone(location_id: int) -> ZoneInfo:
        """
        Look up and return the timezone for a given location ID.

        Args:
            location_id: The PK of the location.

        Returns:
            ZoneInfo for the location's timezone, or UTC if not found.
        """
        try:
            from apps.locations.models import Location
            location = Location.objects.only("timezone").get(pk=location_id)
            return ZoneInfo(location.timezone)
        except Exception:
            return ZoneInfo("UTC")


def unread_notification_count(request) -> dict:
    """
    Context processor that injects the unread notification count for the nav badge.

    Available as `{{ unread_notification_count }}` in all templates.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with 'unread_notification_count' key.
    """
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}

    try:
        from apps.notifications.models import Notification
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    except Exception:
        count = 0

    return {"unread_notification_count": count}