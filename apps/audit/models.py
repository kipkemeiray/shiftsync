"""
Audit trail models for ShiftSync.

Every schedule change is logged immutably. Logs record who did what, when,
and what the before/after state was. Logs are never updated or deleted.

The log is written atomically with the operation (same DB transaction)
so there is no window where a change exists without an audit record.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditLog(models.Model):
    """
    Immutable record of every schedule change made in ShiftSync.

    Uses Django's ContentType framework for generic relations so any model
    can be audited. The before/after fields are JSONB for flexible payloads.

    Action strings follow the pattern: "model.event"
    Examples:
      - "shift.created"
      - "shift.published"
      - "shift_assignment.created"
      - "shift_assignment.deleted"
      - "swap_request.approved"
      - "location_certification.deactivated"
    """

    # Who performed the action
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_actions",
    )

    # Verb describing the action
    action = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Dot-separated action identifier, e.g., 'shift_assignment.created'",
    )

    # Generic FK to the object that was changed
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True)
    object_id = models.PositiveBigIntegerField(null=True)
    content_object = GenericForeignKey("content_type", "object_id")

    # Snapshot of state before and after the change
    before = models.JSONField(
        default=dict,
        blank=True,
        help_text="Serialized state of the object before the change. Empty for creations.",
    )
    after = models.JSONField(
        default=dict,
        blank=True,
        help_text="Serialized state of the object after the change. Empty for deletions.",
    )

    # Optional human-readable context (e.g., manager override reason)
    note = models.TextField(blank=True)

    # IP address for security auditing
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Audit Log Entry"
        verbose_name_plural = "Audit Log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]
        # Logs are immutable — prevent accidental updates via Django ORM
        # (enforced at the model level; database-level constraint via migration)

    def __str__(self) -> str:
        """Return a human-readable summary of the audit entry."""
        actor_name = self.actor.get_full_name() if self.actor else "System"
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {actor_name} → {self.action}"

    def save(self, *args, **kwargs):
        """
        Override save to enforce immutability — audit logs cannot be updated.

        Raises:
            RuntimeError: If attempting to update an existing audit log entry.
        """
        if self.pk:
            raise RuntimeError("AuditLog entries are immutable and cannot be updated.")
        super().save(*args, **kwargs)