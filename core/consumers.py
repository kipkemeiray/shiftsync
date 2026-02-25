"""
WebSocket consumers for ShiftSync's real-time features.

Three consumers handle different real-time concerns:
  1. ScheduleConsumer — broadcasts schedule updates to all staff at a location
  2. UserConsumer — delivers personal notifications (swaps, assignments)
  3. AdminDashboardConsumer — live "on-duty now" dashboard across all locations

Channel group naming convention:
  - schedule_{location_id}: all viewers of a location's schedule
  - user_{user_id}: personal notification stream
  - admin_dashboard: corporate admin live view
  - shift_editing_{shift_id}: conflict detection for concurrent edits

Security: All consumers require authentication. Anonymous connections are
immediately closed. Managers can only join groups for their assigned locations.
"""

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class ScheduleConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for live schedule updates at a specific location.

    Clients join by connecting to /ws/schedule/{location_id}/.
    When a manager publishes or modifies a schedule, this consumer
    broadcasts the change to all connected staff at that location.

    Events broadcast:
      - schedule.published: A week's schedule was published
      - shift.updated: An individual shift was modified
      - shift.assignment.changed: An assignment was added, removed, or changed
      - concurrent_edit.warning: Another manager is editing the same shift
    """

    async def connect(self) -> None:
        """
        Accept the WebSocket connection after verifying authentication and authorization.

        Joins the location's schedule group if the user has access.
        Rejects the connection if:
          - User is not authenticated
          - User doesn't have access to this location (for managers)
        """
        self.location_id = self.scope["url_route"]["kwargs"]["location_id"]
        self.group_name = f"schedule_{self.location_id}"
        self.user = self.scope["user"]

        # Reject unauthenticated connections immediately
        if not self.user.is_authenticated:
            logger.warning("Unauthenticated WebSocket connection attempt rejected.")
            await self.close(code=4001)
            return

        # Verify access to this location
        if not await self._user_can_access_location():
            logger.warning(
                "User %d attempted WebSocket access to location %s without permission.",
                self.user.pk,
                self.location_id,
            )
            await self.close(code=4003)
            return

        # Join the location's broadcast group
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        logger.info(
            "User %d connected to schedule WebSocket for location %s.",
            self.user.pk,
            self.location_id,
        )

    async def disconnect(self, close_code: int) -> None:
        """
        Remove this consumer from all channel groups on disconnect.

        Args:
            close_code: The WebSocket close code.
        """
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

        # Also leave any shift editing groups this user may have joined
        if hasattr(self, "editing_shift_group"):
            await self.channel_layer.group_discard(self.editing_shift_group, self.channel_name)

        logger.info(
            "User %d disconnected from schedule WebSocket for location %s (code=%d).",
            self.user.pk,
            self.location_id,
            close_code,
        )

    async def receive(self, text_data: str) -> None:
        """
        Handle messages sent from the client to the server.

        Currently handles:
          - edit_start: Manager opens a shift assignment modal (conflict detection)
          - edit_stop: Manager closes the modal without saving

        Args:
            text_data: JSON-encoded message from the browser.
        """
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON received from user %d", self.user.pk)
            return

        event_type = data.get("type")

        if event_type == "edit_start" and self.user.is_manager:
            shift_id = data.get("shift_id")
            if shift_id:
                await self._join_shift_editing_group(shift_id)

        elif event_type == "edit_stop":
            if hasattr(self, "editing_shift_group"):
                await self.channel_layer.group_discard(
                    self.editing_shift_group, self.channel_name
                )
                del self.editing_shift_group

    # ------------------------------------------------------------------
    # Group event handlers (called by channel_layer.group_send)
    # ------------------------------------------------------------------

    async def schedule_published(self, event: dict) -> None:
        """
        Handle a schedule.published event and forward to the WebSocket client.

        Args:
            event: The event dict sent via group_send.
        """
        await self.send(text_data=json.dumps({
            "type": "schedule.published",
            "location_id": event["location_id"],
            "week": event["week"],
            "published_by": event["published_by"],
        }))

    async def shift_updated(self, event: dict) -> None:
        """
        Handle a shift.updated event and forward to the WebSocket client.

        Args:
            event: The event dict with shift details.
        """
        await self.send(text_data=json.dumps({
            "type": "shift.updated",
            "shift_id": event["shift_id"],
            "changes": event.get("changes", {}),
        }))

    async def shift_assignment_changed(self, event: dict) -> None:
        """
        Handle an assignment change event and forward to the WebSocket client.

        Args:
            event: The event dict with assignment details.
        """
        await self.send(text_data=json.dumps({
            "type": "shift.assignment.changed",
            "shift_id": event["shift_id"],
            "user_id": event["user_id"],
            "action": event["action"],  # 'assigned' | 'removed' | 'swap_pending'
        }))

    async def concurrent_edit_warning(self, event: dict) -> None:
        """
        Warn this manager that another manager is editing the same shift.

        Args:
            event: The event dict with competing manager's info.
        """
        await self.send(text_data=json.dumps({
            "type": "concurrent_edit.warning",
            "shift_id": event["shift_id"],
            "other_manager": event["other_manager"],
            "message": event["message"],
        }))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _user_can_access_location(self) -> bool:
        """
        Check if the current user can view the requested location's schedule.

        Returns:
            True if admin (all locations), or if manager/staff assigned to this location.
        """
        from apps.accounts.models import User
        from apps.locations.models import Location, LocationCertification

        user = self.user

        if user.role == User.Role.ADMIN:
            return True

        location_id = int(self.location_id)

        if user.role == User.Role.MANAGER:
            return user.managed_locations.filter(pk=location_id).exists()

        # Staff: must have a certification (active or inactive — can still view published schedules)
        return LocationCertification.objects.filter(
            user=user, location_id=location_id
        ).exists()

    async def _join_shift_editing_group(self, shift_id: int) -> None:
        """
        Join a shift-specific editing group for concurrent edit detection.

        Notifies any other managers already in the group of the conflict.

        Args:
            shift_id: The ID of the shift being edited.
        """
        group_name = f"shift_editing_{shift_id}"
        self.editing_shift_group = group_name

        # Notify others in the group that this manager started editing
        await self.channel_layer.group_send(
            group_name,
            {
                "type": "concurrent_edit_warning",
                "shift_id": shift_id,
                "other_manager": self.user.get_full_name(),
                "message": f"{self.user.get_full_name()} is also editing this shift.",
            },
        )

        await self.channel_layer.group_add(group_name, self.channel_name)


class UserConsumer(AsyncWebsocketConsumer):
    """
    Personal WebSocket channel for a specific authenticated user.

    Delivers real-time events to the user's private notification stream:
      - New shift assignments
      - Swap request updates
      - Manager approval decisions

    URL: /ws/user/
    Group: user_{user_id}
    """

    async def connect(self) -> None:
        """Accept connection after verifying authentication."""
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.group_name = f"user_{self.user.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        """Leave the personal notification group on disconnect."""
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data: str) -> None:
        """
        Handle client-to-server messages.

        Currently supports:
          - mark_read: mark a notification as read
        """
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        if data.get("type") == "mark_read":
            notification_id = data.get("notification_id")
            if notification_id:
                await self._mark_notification_read(notification_id)

    async def notification(self, event: dict) -> None:
        """
        Forward a notification event to the connected client.

        Args:
            event: The notification event dict.
        """
        await self.send(text_data=json.dumps({
            "type": "notification",
            "notification_id": event["notification_id"],
            "notification_type": event["notification_type"],
            "title": event["title"],
            "body": event["body"],
        }))

    @database_sync_to_async
    def _mark_notification_read(self, notification_id: int) -> None:
        """
        Mark a notification as read if it belongs to this user.

        Args:
            notification_id: The PK of the notification to mark as read.
        """
        from apps.notifications.models import Notification

        Notification.objects.filter(
            pk=notification_id, recipient=self.user
        ).update(is_read=True)


class AdminDashboardConsumer(AsyncWebsocketConsumer):
    """
    Live "on-duty now" dashboard for admins.

    Broadcasts real-time updates showing which staff are currently on shift
    at each location. Updates are triggered by shift start/end times via Celery.

    URL: /ws/admin/dashboard/
    Group: admin_dashboard
    """

    async def connect(self) -> None:
        """Accept connection only for admin users."""
        self.user = self.scope["user"]

        if not self.user.is_authenticated or not self.user.is_admin:
            await self.close(code=4003)
            return

        await self.channel_layer.group_add("admin_dashboard", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        """Leave the admin dashboard group on disconnect."""
        await self.channel_layer.group_discard("admin_dashboard", self.channel_name)

    async def on_duty_update(self, event: dict) -> None:
        """
        Forward an on-duty status update to the admin dashboard.

        Args:
            event: Dict with location_id and current on-duty staff list.
        """
        await self.send(text_data=json.dumps({
            "type": "on_duty.update",
            "location_id": event["location_id"],
            "location_name": event["location_name"],
            "on_duty": event["on_duty"],  # List of {user_id, name, skill, shift_id}
        }))