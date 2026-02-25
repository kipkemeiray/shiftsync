"""
Notifications models for ShiftSync.

All user-facing notifications are persisted here. Real-time delivery happens
via WebSocket (Django Channels). Email delivery is simulated via Django's
console email backend.

Notification types map to specific business events; the type determines
which template is rendered in the notification center.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """
    A persisted notification for a specific user.

    Notifications are created by service functions (not directly by views)
    and delivered through two channels:
      1. In-app: stored here, rendered in the notification center
      2. Email (simulated): logged to console / email backend

    The `data` JSONB field stores context-specific data for rendering
    the notification detail (e.g., shift ID, swap request ID).
    """

    class Type(models.TextChoices):
        # Staff notifications
        SHIFT_ASSIGNED = "shift_assigned", _("Shift Assigned")
        SHIFT_CHANGED = "shift_changed", _("Shift Changed")
        SHIFT_PUBLISHED = "shift_published", _("Schedule Published")
        SWAP_REQUEST_RECEIVED = "swap_request_received", _("Swap Request Received")
        SWAP_ACCEPTED = "swap_accepted", _("Swap Accepted by Staff")
        SWAP_APPROVED = "swap_approved", _("Swap Approved by Manager")
        SWAP_REJECTED = "swap_rejected", _("Swap Rejected")
        SWAP_CANCELLED = "swap_cancelled", _("Swap Cancelled")
        DROP_AVAILABLE = "drop_available", _("Open Shift Available for Pickup")
        DROP_CLAIMED = "drop_claimed", _("Your Dropped Shift Was Claimed")
        # Manager notifications
        SWAP_APPROVAL_NEEDED = "swap_approval_needed", _("Swap Awaiting Your Approval")
        OVERTIME_WARNING = "overtime_warning", _("Overtime Warning")
        AVAILABILITY_CHANGED = "availability_changed", _("Staff Availability Updated")
        # System
        SCHEDULE_CONFLICT = "schedule_conflict", _("Concurrent Edit Conflict")

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=30, choices=Type.choices)

    # Human-readable content rendered in the notification center
    title = models.CharField(max_length=200)
    body = models.TextField()

    # Context data for linking to the relevant object
    data = models.JSONField(default=dict, blank=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["recipient", "-created_at"]),
        ]

    def __str__(self) -> str:
        """Return a brief description of the notification."""
        return f"[{self.get_notification_type_display()}] → {self.recipient.get_short_name()}"

    def mark_read(self) -> None:
        """Mark this notification as read and record the timestamp."""
        from django.utils import timezone

        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])