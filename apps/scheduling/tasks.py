"""
Celery tasks for ShiftSync scheduling.

Tasks:
  expire_drop_requests  — runs every 15 minutes; marks unclaimed drop requests
                          as EXPIRED when they pass their expires_at deadline.
  expire_swap_requests  — runs every hour; marks PENDING_ACCEPTANCE swap
                          requests as EXPIRED after 24 hours without a response.

Both tasks are registered in CELERY_BEAT_SCHEDULE (see settings/base.py).

Design notes:
  - Tasks are idempotent: safe to run multiple times with the same result.
  - We avoid select_for_update here because Django Celery Beat workers are
    single-threaded by default; add it if horizontal scaling is needed.
  - A logger record is emitted per batch so ops can trace spikes in expiries.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="scheduling.expire_drop_requests")
def expire_drop_requests() -> dict:
    """
    Expire unclaimed drop requests that have passed their expires_at deadline.

    A drop request is eligible for expiry when:
      - status is PENDING_PICKUP (no one has claimed it yet)
      - expires_at is in the past

    Assignment status is restored to ASSIGNED so the original staff member
    remains on the shift (the drop effectively failed).

    Returns:
        Dict with count of records expired.
    """
    from apps.scheduling.models import ShiftAssignment, SwapRequest

    now = timezone.now()
    expired_qs = SwapRequest.objects.filter(
        request_type=SwapRequest.Type.DROP,
        status=SwapRequest.Status.PENDING_PICKUP,
        expires_at__lt=now,
    ).select_related("assignment")

    count = 0
    for swap in expired_qs:
        swap.status = SwapRequest.Status.EXPIRED
        swap.save(update_fields=["status"])
        # Restore the original assignment so the staff member stays on the shift
        swap.assignment.status = ShiftAssignment.Status.ASSIGNED
        swap.assignment.save(update_fields=["status"])
        count += 1

    if count:
        logger.info("Expired %d unclaimed drop request(s).", count)

    return {"expired_drops": count}


@shared_task(name="scheduling.expire_swap_requests")
def expire_swap_requests() -> dict:
    """
    Expire swap requests that have been PENDING_ACCEPTANCE for more than 24 hours.

    Staff B is considered unresponsive after 24 hours. The original assignment
    status is restored to ASSIGNED.

    Returns:
        Dict with count of records expired.
    """
    from apps.scheduling.models import ShiftAssignment, SwapRequest

    cutoff = timezone.now() - timedelta(hours=24)
    expired_qs = SwapRequest.objects.filter(
        request_type=SwapRequest.Type.SWAP,
        status=SwapRequest.Status.PENDING_ACCEPTANCE,
        created_at__lt=cutoff,
    ).select_related("assignment")

    count = 0
    for swap in expired_qs:
        swap.status = SwapRequest.Status.EXPIRED
        swap.save(update_fields=["status"])
        swap.assignment.status = ShiftAssignment.Status.ASSIGNED
        swap.assignment.save(update_fields=["status"])
        count += 1

    if count:
        logger.info("Expired %d unaccepted swap request(s).", count)

    return {"expired_swaps": count}