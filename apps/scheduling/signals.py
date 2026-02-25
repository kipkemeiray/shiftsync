from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.scheduling.models import ShiftAssignment, SwapRequest
from apps.audit.models import AuditLog
from apps.notifications.models import Notification


@receiver(post_save, sender=ShiftAssignment)
def log_assignment(sender, instance, created, **kwargs):
    """Create audit log and notification when a shift assignment is made."""
    if created:
        # Audit log entry
        AuditLog.objects.create(
            actor=instance.assigned_by,
            action="shift_assignment.created",
            content_object=instance,
            after={"shift": instance.shift.id, "user": instance.user.id},
        )

        # Notification to staff
        Notification.objects.create(
            recipient=instance.user,
            notification_type=Notification.Type.SHIFT_ASSIGNED,
            title="New Shift Assigned",
            body=f"You have been assigned to {instance.shift}",
            data={"shift_id": instance.shift.id},
        )


@receiver(post_save, sender=SwapRequest)
def log_swap_request(sender, instance, created, **kwargs):
    """Audit and notify when a swap/drop request is created."""
    if created:
        AuditLog.objects.create(
            actor=instance.requester,
            action=f"swap_request.{instance.request_type}.created",
            content_object=instance,
            after={"assignment": instance.assignment.id, "status": instance.status},
        )

        Notification.objects.create(
            recipient=instance.assignment.user,
            notification_type=Notification.Type.SWAP_REQUEST_RECEIVED,
            title="Swap Request Received",
            body=f"{instance.requester.get_full_name()} requested a swap for {instance.assignment.shift}",
            data={"swap_request_id": instance.id},
        )