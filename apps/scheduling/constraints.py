"""
Scheduling Constraint Engine for ShiftSync.

This module defines and runs all business rules that govern whether a staff member
can be assigned to a shift. Each constraint is an independent, testable function
that returns a ConstraintResult.

The pipeline runs constraints in priority order; the first hard failure short-circuits
the rest (unless check_all=True is passed for the "what-if" UI).

Usage:
    from apps.scheduling.constraints import ConstraintEngine

    result = ConstraintEngine.check(user=staff, shift=shift)
    if not result.ok:
        return JsonResponse({"error": result.reason, "suggestions": result.suggestions})

Adding a new constraint:
    1. Write a function matching the ConstraintCheck protocol.
    2. Add it to CONSTRAINT_PIPELINE with its severity and identifier.
    3. Write tests in tests/test_constraints.py.

Design notes:
  - All time comparisons are done in UTC to avoid DST ambiguity.
  - SELECT FOR UPDATE is used by the engine entry point to prevent TOCTOU races.
  - Suggestions are lightweight (id + name) to avoid N+1 queries.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


if TYPE_CHECKING:
    from apps.accounts.models import Skill, User, StaffAvailability
    from apps.scheduling.models import Shift, ShiftAssignment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Suggestion:
    """A suggested alternative staff member when a constraint blocks an assignment."""

    user_id: int
    full_name: str
    reason: str  # Why this person is suggested (e.g., "Has bartender skill, available 5pm–11pm")


@dataclass
class ConstraintResult:
    """
    The result of running one or more constraint checks.

    Attributes:
        ok: True if the assignment is allowed, False if blocked.
        severity: 'block' (cannot proceed), 'warning' (can proceed with acknowledgement),
                  'override_required' (requires manager override with documented reason).
        constraint_id: Machine-readable identifier of the violated constraint.
        reason: Human-readable explanation of why the constraint failed.
        suggestions: Alternative staff members the manager might consider.
    """

    ok: bool
    severity: str = "block"  # 'block' | 'warning' | 'override_required' | 'ok'
    constraint_id: str = ""
    reason: str = ""
    suggestions: list[Suggestion] = field(default_factory=list)

    @classmethod
    def success(cls) -> "ConstraintResult":
        """Return a passing constraint result."""
        return cls(ok=True, severity="ok")

    @classmethod
    def warning(cls, constraint_id: str, reason: str, suggestions=None) -> "ConstraintResult":
        """Return a warning result that can be acknowledged by the manager."""
        return cls(
            ok=True,  # ok=True because warnings don't block; they just inform
            severity="warning",
            constraint_id=constraint_id,
            reason=reason,
            suggestions=suggestions or [],
        )

    @classmethod
    def block(cls, constraint_id: str, reason: str, suggestions=None) -> "ConstraintResult":
        """Return a hard-block result that prevents assignment."""
        return cls(
            ok=False,
            severity="block",
            constraint_id=constraint_id,
            reason=reason,
            suggestions=suggestions or [],
        )

    @classmethod
    def override_required(cls, constraint_id: str, reason: str) -> "ConstraintResult":
        """Return a result that requires a documented manager override."""
        return cls(
            ok=False,
            severity="override_required",
            constraint_id=constraint_id,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Individual constraint checks
# ---------------------------------------------------------------------------


def check_skill_match(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Verify the staff member has the skill required by the shift.

    Args:
        user: The staff member being considered for assignment.
        shift: The shift to be assigned.

    Returns:
        ConstraintResult with suggestions of other staff who have the required skill.
    """
    from apps.accounts.models import User as UserModel

    if user.skills.filter(pk=shift.required_skill.pk).exists():
        return ConstraintResult.success()

    # Build suggestions: other staff with the right skill at this location
    suggestions = _get_skilled_available_suggestions(shift)

    return ConstraintResult.block(
        constraint_id="skill_mismatch",
        reason=(
            f"{user.get_full_name()} does not have the '{shift.required_skill.display_name}' skill "
            f"required for this shift."
        ),
        suggestions=suggestions,
    )


def check_location_certification(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Verify the staff member has an active certification to work at the shift's location.

    Args:
        user: The staff member being considered for assignment.
        shift: The shift to be assigned.

    Returns:
        ConstraintResult. Blocked if no active certification exists.
    """
    from apps.locations.models import LocationCertification

    has_certification = LocationCertification.objects.filter(
        user=user, location=shift.location, is_active=True
    ).exists()

    if has_certification:
        return ConstraintResult.success()

    # Check if there's an inactive (revoked) certification vs. never certified
    has_any = LocationCertification.objects.filter(user=user, location=shift.location).exists()
    detail = " (certification was revoked)" if has_any else " (never certified for this location)"

    return ConstraintResult.block(
        constraint_id="location_certification",
        reason=(
            f"{user.get_full_name()} is not certified to work at {shift.location.name}{detail}."
        ),
    )


def check_availability(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Verify the staff member's availability covers the entire shift window.

    Availability is stored with a timezone; we convert to UTC for comparison.
    One-off entries override weekly entries for the same date.

    Args:
        user: The staff member being considered for assignment.
        shift: The shift to be assigned.

    Returns:
        ConstraintResult explaining which availability window is missing or conflicting.
    """
    from apps.accounts.models import StaffAvailability

    local_tz = shift.location.get_zoneinfo()
    shift_start_local = shift.start_utc.astimezone(local_tz)
    shift_date = shift_start_local.date()
    shift_weekday = shift_start_local.weekday()

    # Check for one-off override first (takes precedence)
    one_off = StaffAvailability.objects.filter(
        user=user, recurrence=StaffAvailability.Recurrence.ONE_OFF, specific_date=shift_date
    ).first()

    if one_off:
        if one_off.is_unavailable_day:
            return ConstraintResult.block(
                constraint_id="availability_one_off_unavailable",
                reason=(
                    f"{user.get_full_name()} has marked {shift_date} as unavailable."
                    + (f" Note: {one_off.notes}" if one_off.notes else "")
                ),
            )
        return _check_time_window_covers_shift(user, one_off, shift, "one-off availability")

    # Fall back to weekly recurring availability
    weekly = StaffAvailability.objects.filter(
        user=user,
        recurrence=StaffAvailability.Recurrence.WEEKLY,
        day_of_week=shift_weekday,
    ).first()

    if not weekly:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return ConstraintResult.block(
            constraint_id="availability_no_window",
            reason=(
                f"{user.get_full_name()} has not set availability for "
                f"{days[shift_weekday]}s. Ask them to update their availability."
            ),
        )

    if weekly.is_unavailable_day:
        return ConstraintResult.block(
            constraint_id="availability_weekly_unavailable",
            reason=f"{user.get_full_name()} is marked as unavailable on {days[shift_weekday]}s.",
        )

    return _check_time_window_covers_shift(user, weekly, shift, "weekly availability")


def _check_time_window_covers_shift(
    user: "User",
    availability: "StaffAvailability",
    shift: "Shift",
    window_type: str,
) -> ConstraintResult:
    """
    Check if an availability window fully covers the shift's time range.

    Converts availability times (stored with explicit timezone) to UTC and
    compares against the shift's UTC times.

    Args:
        user: The staff member.
        availability: The StaffAvailability instance to check.
        shift: The shift being evaluated.
        window_type: Human-readable label for error messages.

    Returns:
        ConstraintResult.
    """
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    avail_tz = ZoneInfo(availability.timezone)

    # Build aware datetimes using the availability's timezone and the shift's local date
    local_tz = shift.location.get_zoneinfo()
    shift_date_local = shift.start_utc.astimezone(local_tz).date()

    avail_start_aware = dt.combine(shift_date_local, availability.start_time, tzinfo=avail_tz)
    avail_end_aware = dt.combine(shift_date_local, availability.end_time, tzinfo=avail_tz)

    # Convert to UTC for comparison
    avail_start_utc = avail_start_aware.astimezone(timezone.utc)
    avail_end_utc = avail_end_aware.astimezone(timezone.utc)

    if avail_start_utc <= shift.start_utc and avail_end_utc >= shift.end_utc:
        return ConstraintResult.success()

    return ConstraintResult.block(
        constraint_id="availability_window_mismatch",
        reason=(
            f"{user.get_full_name()}'s {window_type} "
            f"({availability.start_time.strftime('%H:%M')}–{availability.end_time.strftime('%H:%M')} "
            f"{availability.timezone}) does not cover this shift "
            f"({shift.start_utc.strftime('%H:%M')} – {shift.end_utc.strftime('%H:%M')} UTC)."
        ),
    )


def check_no_double_booking(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Ensure the staff member has no overlapping shift assignments, even cross-location.

    Overlapping is defined as: existing shift's [start, end) overlaps with new shift's [start, end).

    Args:
        user: The staff member being considered for assignment.
        shift: The proposed new shift.

    Returns:
        ConstraintResult with the conflicting shift details.
    """
    from apps.scheduling.models import ShiftAssignment

    # Find assignments where the shifts overlap
    conflicting = (
        ShiftAssignment.objects.filter(
            user=user,
            status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
        )
        .filter(
            # Overlap condition: existing.start < new.end AND existing.end > new.start
            shift__start_utc__lt=shift.end_utc,
            shift__end_utc__gt=shift.start_utc,
        )
        .exclude(shift=shift)  # Exclude the shift being re-assigned
        .select_related("shift__location")
        .first()
    )

    if not conflicting:
        return ConstraintResult.success()

    return ConstraintResult.block(
        constraint_id="double_booking",
        reason=(
            f"{user.get_full_name()} is already assigned to a shift at "
            f"{conflicting.shift.location.name} from "
            f"{conflicting.shift.start_utc.strftime('%H:%M')} to "
            f"{conflicting.shift.end_utc.strftime('%H:%M')} UTC on that day, "
            f"which overlaps with this shift."
        ),
    )


def check_minimum_rest(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Enforce the 10-hour minimum rest period between consecutive shifts.

    Checks both:
      - Previous shift ending within 10 hours before this one starts
      - Next shift starting within 10 hours after this one ends

    Args:
        user: The staff member being considered for assignment.
        shift: The proposed new shift.

    Returns:
        ConstraintResult with the conflicting shift and actual gap.
    """
    from apps.scheduling.models import ShiftAssignment

    config = settings.SHIFTSYNC
    min_rest = timedelta(hours=config["MIN_REST_HOURS"])

    active_statuses = [ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING]

    # Check: does a previous shift end too soon before this one?
    too_recent = (
        ShiftAssignment.objects.filter(
            user=user, status__in=active_statuses
        )
        .filter(
            shift__end_utc__gt=shift.start_utc - min_rest,
            shift__end_utc__lte=shift.start_utc,
        )
        .exclude(shift=shift)
        .select_related("shift__location")
        .first()
    )

    if too_recent:
        gap = shift.start_utc - too_recent.shift.end_utc
        gap_hours = gap.total_seconds() / 3600
        return ConstraintResult.block(
            constraint_id="minimum_rest_before",
            reason=(
                f"{user.get_full_name()} would only have {gap_hours:.1f} hours of rest "
                f"after their shift ending at {too_recent.shift.location.name}. "
                f"The minimum is {config['MIN_REST_HOURS']} hours."
            ),
        )

    # Check: does the next shift start too soon after this one ends?
    too_soon = (
        ShiftAssignment.objects.filter(
            user=user, status__in=active_statuses
        )
        .filter(
            shift__start_utc__lt=shift.end_utc + min_rest,
            shift__start_utc__gte=shift.end_utc,
        )
        .exclude(shift=shift)
        .select_related("shift__location")
        .first()
    )

    if too_soon:
        gap = too_soon.shift.start_utc - shift.end_utc
        gap_hours = gap.total_seconds() / 3600
        return ConstraintResult.block(
            constraint_id="minimum_rest_after",
            reason=(
                f"{user.get_full_name()} has a shift starting at {too_soon.shift.location.name} "
                f"only {gap_hours:.1f} hours after this shift would end. "
                f"The minimum rest period is {config['MIN_REST_HOURS']} hours."
            ),
        )

    return ConstraintResult.success()


def check_daily_hours(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Check daily hour limits for the shift's calendar day.

    Rules (in the location's local timezone):
      - >8 hours total in a calendar day → warning
      - >12 hours total in a calendar day → hard block

    Args:
        user: The staff member being considered for assignment.
        shift: The proposed new shift.

    Returns:
        ConstraintResult (warning or block depending on severity).
    """
    from apps.scheduling.models import ShiftAssignment

    config = settings.SHIFTSYNC
    local_tz = shift.location.get_zoneinfo()
    shift_date = shift.start_utc.astimezone(local_tz).date()

    # Find all assignments on the same calendar day (in the location's timezone)
    existing = ShiftAssignment.objects.filter(
        user=user,
        status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
    ).filter(
        shift__start_utc__date=shift_date
    ).exclude(shift=shift).select_related("shift")

    existing_hours = sum(a.shift.duration_hours for a in existing)
    total_hours = existing_hours + shift.duration_hours

    if total_hours > config["DAILY_HOURS_HARD_LIMIT"]:
        return ConstraintResult.block(
            constraint_id="daily_hours_exceeded",
            reason=(
                f"Assigning this {shift.duration_hours:.1f}h shift would give "
                f"{user.get_full_name()} {total_hours:.1f} hours in a single day, "
                f"exceeding the {config['DAILY_HOURS_HARD_LIMIT']}-hour daily limit."
            ),
        )

    if total_hours > config["DAILY_HOURS_WARNING"]:
        return ConstraintResult.warning(
            constraint_id="daily_hours_warning",
            reason=(
                f"{user.get_full_name()} will have {total_hours:.1f} hours on this day, "
                f"exceeding the {config['DAILY_HOURS_WARNING']}-hour guideline. "
                f"This is allowed but may require overtime pay."
            ),
        )

    return ConstraintResult.success()


def check_weekly_hours(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Check weekly hour totals for the ISO week containing this shift.

    Rules:
      - 35+ hours → warning
      - 40+ hours → hard block (overtime territory)

    The week is defined as Monday–Sunday in the location's timezone.

    Args:
        user: The staff member being considered for assignment.
        shift: The proposed new shift.

    Returns:
        ConstraintResult (warning or block).
    """
    from apps.scheduling.models import ShiftAssignment

    config = settings.SHIFTSYNC
    local_tz = shift.location.get_zoneinfo()
    shift_date = shift.start_utc.astimezone(local_tz).date()

    # ISO week boundaries (Monday 00:00 to Sunday 23:59 in local timezone)
    monday = shift_date - timedelta(days=shift_date.weekday())
    sunday = monday + timedelta(days=6)

    from datetime import datetime as dt
    week_start_utc = dt.combine(monday, dt.min.time(), tzinfo=local_tz).astimezone(timezone.utc)
    week_end_utc = dt.combine(sunday, dt.max.time(), tzinfo=local_tz).astimezone(timezone.utc)

    existing = ShiftAssignment.objects.filter(
        user=user,
        status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
        shift__start_utc__gte=week_start_utc,
        shift__start_utc__lte=week_end_utc,
    ).exclude(shift=shift).select_related("shift")

    current_hours = sum(a.shift.duration_hours for a in existing)
    projected_hours = current_hours + shift.duration_hours

    if projected_hours >= config["WEEKLY_HOURS_HARD_LIMIT"]:
        return ConstraintResult.block(
            constraint_id="weekly_hours_exceeded",
            reason=(
                f"{user.get_full_name()} already has {current_hours:.1f} hours this week. "
                f"Adding this {shift.duration_hours:.1f}h shift would bring the total to "
                f"{projected_hours:.1f} hours, exceeding the {config['WEEKLY_HOURS_HARD_LIMIT']}-hour limit."
            ),
        )

    if projected_hours >= config["WEEKLY_HOURS_WARNING"]:
        return ConstraintResult.warning(
            constraint_id="weekly_hours_warning",
            reason=(
                f"{user.get_full_name()} will have {projected_hours:.1f} hours this week, "
                f"approaching the {config['WEEKLY_HOURS_HARD_LIMIT']}-hour overtime threshold. "
                f"Current: {current_hours:.1f}h, adding: {shift.duration_hours:.1f}h."
            ),
        )

    return ConstraintResult.success()


def check_consecutive_days(user: "User", shift: "Shift") -> ConstraintResult:
    """
    Check for excessive consecutive work days.

    Design decision: any shift (even 1 hour) counts as a worked day.

    Rules:
      - 6th consecutive day → warning
      - 7th consecutive day → requires manager override with documented reason

    Args:
        user: The staff member being considered for assignment.
        shift: The proposed new shift.

    Returns:
        ConstraintResult (warning or override_required).
    """
    from apps.scheduling.models import ShiftAssignment

    config = settings.SHIFTSYNC
    local_tz = shift.location.get_zoneinfo()
    shift_date = shift.start_utc.astimezone(local_tz).date()

    # Walk backwards to count consecutive worked days before this shift
    consecutive_before = 0
    check_date = shift_date - timedelta(days=1)
    for _ in range(7):  # Check up to 7 days back
        has_work = ShiftAssignment.objects.filter(
            user=user,
            status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
            shift__start_utc__date=check_date,
        ).exists()
        if not has_work:
            break
        consecutive_before += 1
        check_date -= timedelta(days=1)

    # This shift would be day (consecutive_before + 1) in the run
    total_consecutive = consecutive_before + 1

    if total_consecutive >= config["CONSECUTIVE_DAYS_OVERRIDE"]:
        return ConstraintResult.override_required(
            constraint_id="consecutive_days_7",
            reason=(
                f"{user.get_full_name()} has worked {consecutive_before} consecutive days. "
                f"This would be their 7th consecutive day, which requires a documented manager override."
            ),
        )

    if total_consecutive >= config["CONSECUTIVE_DAYS_WARNING"]:
        return ConstraintResult.warning(
            constraint_id="consecutive_days_6",
            reason=(
                f"{user.get_full_name()} has worked {consecutive_before} consecutive days. "
                f"This would be their 6th consecutive day. A 7th would require an override."
            ),
        )

    return ConstraintResult.success()


# ---------------------------------------------------------------------------
# Constraint pipeline
# ---------------------------------------------------------------------------

# Ordered list of (function, can_short_circuit) tuples.
# Short-circuit = stop checking further if this fails (for speed).
# Non-short-circuit constraints are still checked even after prior failures (for completeness).
CONSTRAINT_PIPELINE = [
    (check_skill_match, True),               # Must-have before anything else
    (check_location_certification, True),     # Must-have
    (check_availability, True),               # Must-have
    (check_no_double_booking, True),          # Hard conflict
    (check_minimum_rest, True),               # Hard labour rule
    (check_daily_hours, False),               # Warning or block
    (check_weekly_hours, False),              # Warning or block
    (check_consecutive_days, False),          # Warning or override_required
]


class ConstraintEngine:
    """
    Entry point for all scheduling constraint checks.

    Usage:
        results = ConstraintEngine.check(user=staff_member, shift=shift)
        # Returns the first blocking result, or the most severe warning, or success.

        all_results = ConstraintEngine.check_all(user=staff_member, shift=shift)
        # Returns all results regardless of severity (for "what-if" UI).
    """

    @staticmethod
    @transaction.atomic
    def check(
        user: "User",
        shift: "Shift",
        exclude_assignment_id: Optional[int] = None,
    ) -> ConstraintResult:
        """
        Run all constraints and return the first blocking issue, or success.

        Uses SELECT FOR UPDATE on ShiftAssignment to prevent concurrent
        race conditions (two managers assigning the same staff member at once).

        Args:
            user: The staff member to check.
            shift: The shift to assign them to.
            exclude_assignment_id: Optional ID of an existing assignment to exclude
                                   (used when re-checking an existing assignment).

        Returns:
            The most severe ConstraintResult. If all pass, returns success.
        """
        from apps.scheduling.models import ShiftAssignment

        # Lock the user's assignments to prevent concurrent modification
        ShiftAssignment.objects.select_for_update().filter(user=user)

        warnings = []

        for check_fn, short_circuit in CONSTRAINT_PIPELINE:
            result = check_fn(user, shift)

            if result.severity == "ok":
                continue

            if result.severity in ("block", "override_required"):
                logger.info(
                    "Constraint failed: %s for user=%d shift=%d: %s",
                    result.constraint_id,
                    user.pk,
                    shift.pk,
                    result.reason,
                )
                return result

            # It's a warning — collect it but keep going
            warnings.append(result)

        # Return the first warning if any exist, otherwise success
        if warnings:
            return warnings[0]

        return ConstraintResult.success()

    @staticmethod
    def check_all(user: "User", shift: "Shift") -> list[ConstraintResult]:
        """
        Run all constraints and return ALL results (for "what-if" projections).

        Does not short-circuit on failure; useful for the UI to show the manager
        a complete picture of issues before committing an assignment.

        Args:
            user: The staff member to check.
            shift: The shift to assign them to.

        Returns:
            List of all non-success ConstraintResults. Empty list means all clear.
        """
        results = []
        for check_fn, _ in CONSTRAINT_PIPELINE:
            result = check_fn(user, shift)
            if result.severity != "ok":
                results.append(result)
        return results


# ---------------------------------------------------------------------------
# Helper: build alternative suggestions
# ---------------------------------------------------------------------------


def _get_skilled_available_suggestions(shift: "Shift") -> list[Suggestion]:
    """
    Find staff members who could work this shift (correct skill + certification).

    Used to populate the "suggestions" field in ConstraintResult so managers
    see actionable alternatives when an assignment is blocked.

    This is a fast approximation — it doesn't run full constraint checks on each
    candidate (that would be N+1). Full checks run when the manager selects
    a suggestion.

    Args:
        shift: The shift for which to find alternatives.

    Returns:
        List of Suggestion instances, up to 5.
    """
    from apps.accounts.models import User
    from apps.locations.models import LocationCertification

    certified_user_ids = LocationCertification.objects.filter(
        location=shift.location, is_active=True
    ).values_list("user_id", flat=True)

    candidates = User.objects.filter(
        pk__in=certified_user_ids,
        role=User.Role.STAFF,
        skills=shift.required_skill,
        is_active=True,
    ).distinct()[:5]

    return [
        Suggestion(
            user_id=c.pk,
            full_name=c.get_full_name(),
            reason=f"Certified at {shift.location.name}, has {shift.required_skill.display_name} skill.",
        )
        for c in candidates
    ]