"""
Notifications views for ShiftSync.

View inventory:
  NotificationCenterView → paginated list of all notifications for current user
  mark_read              → POST: mark one or all notifications read, then redirect back
"""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class NotificationCenterView(View):
    """Notification inbox for the current user, newest first."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render the notification center.

        Args:
            request: Authenticated GET request.
        """
        qs = Notification.objects.filter(recipient=request.user).order_by("-created_at")

        # Count must happen on the full queryset BEFORE slicing.
        # Calling .filter() on a sliced queryset raises OperationalError.
        unread_count = qs.filter(is_read=False).count()

        notifications = qs[:50]

        return render(request, "notifications/center.html", {
            "notifications": notifications,
            "unread_count": unread_count,
        })


@login_required(login_url="/accounts/login/")
def mark_read(request: HttpRequest) -> HttpResponse:
    """
    Mark one or all notifications as read, then redirect back to center.

    POST body:
      notification_id: int  → mark a single notification
                       'all' → mark every unread notification

    Returns:
        Redirect to notifications:center so the page reloads with updated state.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    notification_id = request.POST.get("notification_id", "")

    if notification_id == "all":
        Notification.objects.filter(
            recipient=request.user, is_read=False
        ).update(is_read=True, read_at=timezone.now())
        logger.info("User %d marked all notifications read", request.user.pk)
    elif notification_id:
        Notification.objects.filter(
            pk=notification_id, recipient=request.user
        ).update(is_read=True, read_at=timezone.now())

    return redirect("notifications:center")