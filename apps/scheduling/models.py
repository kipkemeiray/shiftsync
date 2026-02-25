"""
Scheduling models for ShiftSync.

The core of the platform. Defines:
  - Shift: a time block at a location requiring a skill and headcount
  - ShiftAssignment: links a staff member to a shift
  - SwapRequest: the state machine for shift swaps and drops
  - ManagerOverride: documents why a constraint was bypassed

All datetimes are stored as UTC. The display layer converts to the location's
timezone. This is enforced by using `datetime` with `timezone.now()` everywhere,
never `datetime.now()`.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Shift(models.Model):
    """
    A scheduled work block at a restaurant location.

    A shift specifies WHAT skill is needed, WHEN, WHERE, and HOW MANY staff.
    Individual staff are attached via ShiftAssignment.

    Overnight shifts (e.g., 11pm–3am) are stored as a single record where
    end_utc > start_utc across the date boundary — no special handling needed.

    Publishing workflow:
      1. Manager creates shifts (draft mode, invisible to staff)
      2. Manager reviews and publishes the week
      3. Published shifts are visible to assigned staff
      4. Edits are blocked after edit_cutoff_hours before the shift start
         (this is configurable per shift; default from settings)
    """

    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="shifts",
    )
    required_skill = models.ForeignKey(
        "accounts.Skill",
        on_delete=models.PROTECT,
        related_name="shifts",
    )
    headcount_needed = models.PositiveSmallIntegerField(
        default=1,
        help_text="Number of staff required for this shift.",
    )

    # Times stored as UTC — ALWAYS. Display layer converts to location timezone.
    start_utc = models.DateTimeField(help_text="Shift start time in UTC.")
    end_utc = models.DateTimeField(help_text="Shift end time in UTC (may cross midnight).")

    # Publishing state
    is_published = models.BooleanField(
        default=False,
        help_text="Published shifts are visible to assigned staff.",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_shifts",
    )

    # Edit cutoff: managers cannot edit after this many hours before shift start
    edit_cutoff_hours = models.PositiveSmallIntegerField(
        default=48,
        help_text="Hours before shift start after which the shift is locked from edits.",
    )

    # Notes visible to assigned staff
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_shifts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shift"
        verbose_name_plural = "Shifts"
        ordering = ["start_utc"]
        indexes = [
            models.Index(fields=["location", "start_utc"]),
            models.Index(fields=["start_utc", "end_utc"]),
        ]

    def __str__(self) -> str:
        """Return a human-readable shift description."""
        return (
            f"{self.location.name} | {self.required_skill.display_name} | "
            f"{self.start_utc.strftime('%Y-%m-%d %H:%M')} UTC"
        )

    @property
    def duration_hours(self) -> float:
        """Calculate the total duration of the shift in decimal hours."""
        delta = self.end_utc - self.start_utc
        return delta.total_seconds() / 3600

    @property
    def is_overnight(self) -> bool:
        """Return True if the shift crosses a local midnight boundary."""
        local_tz = self.location.get_zoneinfo()
        start_local = self.start_utc.astimezone(local_tz)
        end_local = self.end_utc.astimezone(local_tz)
        return start_local.date() != end_local.date()

    @property
    def is_premium(self) -> bool:
        """
        Return True if this shift qualifies as a "premium" shift.

        Premium shifts are Friday/Saturday evenings (configurable in settings).
        The day is evaluated in the location's local timezone.
        """
        config = settings.SHIFTSYNC
        local_tz = self.location.get_zoneinfo()
        start_local = self.start_utc.astimezone(local_tz)
        return (
            start_local.weekday() in config["PREMIUM_SHIFT_DAYS"]
            and start_local.hour >= config["PREMIUM_SHIFT_START_HOUR"]
        )

    @property
    def is_past_edit_cutoff(self) -> bool:
        """
        Return True if the shift can no longer be edited.

        Shifts are locked for editing edit_cutoff_hours before they start.
        """
        cutoff = self.start_utc - timezone.timedelta(hours=self.edit_cutoff_hours)
        return timezone.now() >= cutoff

    @property
    def assigned_count(self) -> int:
        """Return the number of currently active (non-cancelled) assignments."""
        return self.assignments.filter(
            status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING]
        ).count()

    @property
    def is_fully_staffed(self) -> bool:
        """Return True if the shift has enough assigned staff."""
        return self.assigned_count >= self.headcount_needed

    def publish(self, published_by: settings.AUTH_USER_MODEL) -> None:
        """
        Mark this shift as published, making it visible to assigned staff.

        Args:
            published_by: The manager/admin who is publishing the shift.
        """
        self.is_published = True
        self.published_at = timezone.now()
        self.published_by = published_by
        self.save(update_fields=["is_published", "published_at", "published_by"])


class ShiftAssignment(models.Model):
    """
    Links a staff member to a shift they are assigned to work.

    Status machine:
      ASSIGNED → SWAP_PENDING (when swap requested)
      SWAP_PENDING → ASSIGNED (swap cancelled/rejected)
      SWAP_PENDING → COVERED (swap approved, this person is off)
      ASSIGNED → DROPPED (drop approved, shift is open)

    When a swap is approved:
      - Original assignment: status → COVERED
      - New assignment created for the replacement: status → ASSIGNED
    """

    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        SWAP_PENDING = "swap_pending", _("Swap/Drop Pending")
        COVERED = "covered", _("Covered (swap out)")
        DROPPED = "dropped", _("Dropped")

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shift_assignments",
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ASSIGNED)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assignments_made",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shift Assignment"
        verbose_name_plural = "Shift Assignments"
        constraints = [
            # A staff member can only have one active assignment per shift
            models.UniqueConstraint(
                fields=["shift", "user"],
                condition=models.Q(status__in=["assigned", "swap_pending"]),
                name="unique_active_assignment_per_shift",
            )
        ]
        indexes = [
            models.Index(fields=["user", "shift"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        """Return a readable description of this assignment."""
        return f"{self.user.get_full_name()} → {self.shift} [{self.get_status_display()}]"


class SwapRequest(models.Model):
    """
    Represents a shift swap or drop request initiated by a staff member.

    State machine for SWAP type:
      PENDING_ACCEPTANCE (Staff B hasn't responded yet)
        → PENDING_MANAGER (Staff B accepted, awaiting manager approval)
        → APPROVED (Manager approved, assignments updated)
        → REJECTED (Manager or Staff B rejected)
        → CANCELLED (Staff A or manager cancelled)
        → EXPIRED (Not resolved before the deadline)

    State machine for DROP type:
      PENDING_PICKUP (waiting for any qualified staff to claim)
        → PENDING_MANAGER (someone claimed it, awaiting manager approval)
        → APPROVED / REJECTED / CANCELLED / EXPIRED

    Constraint: Staff may have at most MAX_PENDING_SWAP_REQUESTS open at once.
    The target field is null for DROP type (open to any qualified staff).
    """

    class Type(models.TextChoices):
        SWAP = "swap", _("Shift Swap")
        DROP = "drop", _("Drop / Open Pickup")

    class Status(models.TextChoices):
        PENDING_ACCEPTANCE = "pending_acceptance", _("Awaiting Staff B Acceptance")
        PENDING_PICKUP = "pending_pickup", _("Open for Pickup")
        PENDING_MANAGER = "pending_manager", _("Awaiting Manager Approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    # Staff A: the person initiating the swap/drop
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="swap_requests_made",
    )
    # Staff B: target for swaps (null for drops)
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="swap_requests_received",
    )
    # Who actually claimed an open drop (may differ from target for drops)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drops_claimed",
    )

    assignment = models.ForeignKey(
        ShiftAssignment,
        on_delete=models.CASCADE,
        related_name="swap_requests",
    )

    request_type = models.CharField(max_length=5, choices=Type.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING_ACCEPTANCE
    )

    # Manager who reviewed the final approval
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="swap_reviews",
    )

    requester_note = models.TextField(blank=True)
    manager_note = models.TextField(blank=True)

    # Timestamps for the state machine transitions
    target_accepted_at = models.DateTimeField(null=True, blank=True)
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)

    # For drops: expires 24h before shift start if unclaimed
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Auto-calculated for drops; null for manager-only expiry."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Swap Request"
        verbose_name_plural = "Swap Requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["requester", "status"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        """Return a readable description of this swap/drop request."""
        if self.request_type == self.Type.SWAP:
            parties = f"{self.requester.get_short_name()} ↔ {self.target.get_short_name() if self.target else '?'}"
        else:
            parties = f"{self.requester.get_short_name()} (drop)"
        return f"{parties} | {self.assignment.shift} | {self.get_status_display()}"

    @property
    def is_pending(self) -> bool:
        """Return True if this request is still in a pending (unresolved) state."""
        return self.status in [
            self.Status.PENDING_ACCEPTANCE,
            self.Status.PENDING_PICKUP,
            self.Status.PENDING_MANAGER,
        ]


class ManagerOverride(models.Model):
    """
    Documents when a manager bypasses a scheduling constraint.

    Required when:
      - Assigning staff on their 7th consecutive day
      - Any future hard-block overrides added to the constraint engine

    The documented reason is stored for audit purposes and is visible to admins.
    """

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="overrides_made",
    )
    assignment = models.ForeignKey(
        ShiftAssignment,
        on_delete=models.CASCADE,
        related_name="overrides",
    )
    constraint_violated = models.CharField(
        max_length=100,
        help_text="Identifier of the constraint that was overridden (e.g., 'consecutive_days_7').",
    )
    reason = models.TextField(help_text="Manager's documented reason for the override.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Manager Override"
        verbose_name_plural = "Manager Overrides"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return a description of the override."""
        return f"{self.manager.get_full_name()} overrode {self.constraint_violated} for {self.assignment}"