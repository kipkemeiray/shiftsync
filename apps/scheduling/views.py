"""
Scheduling views for ShiftSync.

View inventory:
  DashboardView         → role-aware homepage (GET)
  on_duty_now           → HTMX partial: who is on shift right now (GET)
  ScheduleView          → week grid, manager/admin (GET)
  MyShiftsView          → staff full shift list (GET)          [Gap 2: swap/drop buttons]
  SwapListView          → staff swap/drop management (GET, POST) [Gap 5: accept/decline/cancel]
  InitiateSwapView      → staff initiates swap or drop (GET, POST) [Gap 2, Gap 4]
  SwapReviewView        → manager approve/reject swap (GET, POST) [Gap 3: re-validates constraints]
  claim_shift           → HTMX POST: staff claims open shift
  LocationListView      → redirect stub
  ShiftManageView       → manager shift table with filters (GET)
  CreateShiftView       → manager creates a shift (POST)
  AssignStaffView       → manager assigns staff; full constraint engine (POST) [Gap 1]
  TogglePublishView     → manager publishes/unpublishes single shift (POST) [Gap 2: cutoff]
  PublishWeekView       → manager bulk-publishes draft week (POST)
  DeleteShiftView       → manager deletes draft shift (POST) [Gap 2: cutoff]
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages as msg
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.locations.models import Location, LocationCertification
from apps.scheduling.constraints import ConstraintEngine
from apps.scheduling.models import ManagerOverride, Shift, ShiftAssignment, SwapRequest
from core.permissions import AdminRequiredMixin, ManagerRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket broadcast helpers 
# ---------------------------------------------------------------------------

def _ws_broadcast(group: str, payload: dict) -> None:
    """
    Fire-and-forget channel layer group_send from a synchronous Django view.

    Uses async_to_sync so it runs cleanly inside the request/response cycle.
    Failures are caught and logged — a WS glitch must never break the HTTP response.

    Args:
        group:   Channel group name (e.g. "schedule_3", "user_7").
        payload: Dict passed to group_send; must include a "type" key whose
                 value maps to a handler method on the consumer
                 (dots converted to underscores by Channels).
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(group, payload)
    except Exception as exc:  # pragma: no cover — WS infra may not be running in tests
        logger.warning("WebSocket broadcast to group '%s' failed: %s", group, exc)


def _notify_user(user_id: int, notification_type: str, title: str, body: str,
                 notification_id: int = 0) -> None:
    """
    Push a real-time notification to a specific user's WebSocket channel.

    Args:
        user_id:           The recipient's primary key.
        notification_type: One of the Notification.Type values.
        title:             Short notification title.
        body:              Full notification body text.
        notification_id:   PK of the persisted Notification record (0 if not persisted).
    """
    _ws_broadcast(f"user_{user_id}", {
        "type": "notification",
        "notification_id": notification_id,
        "notification_type": notification_type,
        "title": title,
        "body": body,
    })


# ---------------------------------------------------------------------------
# Dashboard — role-aware router
# ---------------------------------------------------------------------------

@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class DashboardView(View):
    """Central dashboard — renders a different template per user role."""

    def get(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if user.role == User.Role.ADMIN:
            return render(request, "scheduling/dashboard_admin.html", self._admin_context())
        if user.role == User.Role.MANAGER:
            return render(request, "scheduling/dashboard_manager.html", self._manager_context(user))
        return render(request, "scheduling/dashboard_staff.html", self._staff_context(user))

    def _admin_context(self) -> dict:
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)
        locations = Location.objects.filter(is_active=True).prefetch_related("managers")
        location_summaries = []
        for loc in locations:
            shifts = Shift.objects.filter(location=loc, start_utc__gte=week_start, start_utc__lt=week_end)
            total_headcount = sum(s.headcount_needed for s in shifts)
            filled = sum(s.assigned_count for s in shifts)
            coverage_pct = int((filled / total_headcount) * 100) if total_headcount else 100
            location_summaries.append({
                "location": loc,
                "shift_count": shifts.count(),
                "staff_count": LocationCertification.objects.filter(location=loc, is_active=True).count(),
                "coverage_pct": coverage_pct,
            })
        return {
            "today": now.date(),
            "total_locations": locations.count(),
            "total_staff": User.objects.filter(role=User.Role.STAFF, is_active=True).count(),
            "shifts_this_week": Shift.objects.filter(start_utc__gte=week_start, start_utc__lt=week_end).count(),
            "overtime_warnings": self._overtime_warning_count(week_start, week_end),
            "location_summaries": location_summaries,
            "pending_swaps": SwapRequest.objects.filter(
                status=SwapRequest.Status.PENDING_MANAGER
            ).select_related("requester", "assignment__shift__location", "assignment__shift__required_skill"),
            "recent_audit": AuditLog.objects.select_related("actor").order_by("-created_at"),
        }

    def _manager_context(self, user: User) -> dict:
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)
        managed_locations = user.managed_locations.filter(is_active=True)
        week_shifts = Shift.objects.filter(
            location__in=managed_locations, start_utc__gte=week_start, start_utc__lt=week_end,
        ).select_related("location", "required_skill")
        understaffed = [s for s in week_shifts if s.is_published and not s.is_fully_staffed]
        staff_ids = LocationCertification.objects.filter(
            location__in=managed_locations, is_active=True
        ).values_list("user_id", flat=True).distinct()
        threshold = settings.SHIFTSYNC["WEEKLY_HOURS_WARNING"]
        hard_limit = settings.SHIFTSYNC["WEEKLY_HOURS_HARD_LIMIT"]
        staff_hours = []
        for member in User.objects.filter(pk__in=staff_ids):
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=member,
                    status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                    shift__start_utc__gte=week_start, shift__start_utc__lt=week_end,
                ).select_related("shift")
            )
            # Gap 11: colour-code approaching/over limit
            if hours >= hard_limit:
                hours_class = "text-danger fw-bold"
            elif hours >= threshold:
                hours_class = "text-warning fw-semibold"
            else:
                hours_class = "text-success"
            staff_hours.append({"user": member, "hours": hours, "hours_class": hours_class})
        staff_hours.sort(key=lambda x: x["hours"], reverse=True)
        return {
            "today": now.date(),
            "managed_locations": managed_locations,
            "total_staff": staff_ids.count(),
            "shifts_this_week": week_shifts.count(),
            "understaffed_shifts": understaffed,
            "understaffed_count": len(understaffed),
            "pending_swaps": SwapRequest.objects.filter(
                status=SwapRequest.Status.PENDING_MANAGER,
                assignment__shift__location__in=managed_locations,
            ).select_related("requester", "assignment__shift__location", "assignment__shift__required_skill"),
            "staff_hours": staff_hours,
            "hours_warning_threshold": threshold,
            "hours_hard_limit": hard_limit,
        }

    def _staff_context(self, user: User) -> dict:
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)
        upcoming_assignments = (
            ShiftAssignment.objects.filter(
                user=user,
                status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                shift__start_utc__gte=now,
                shift__start_utc__lte=now + timedelta(days=14),
            ).select_related("shift__location", "shift__required_skill").order_by("shift__start_utc")
        )
        week_hours = sum(
            a.shift.duration_hours
            for a in ShiftAssignment.objects.filter(
                user=user,
                status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                shift__start_utc__gte=week_start, shift__start_utc__lt=week_end,
            ).select_related("shift")
        )
        my_swaps = SwapRequest.objects.filter(
            requester=user,
            status__in=[
                SwapRequest.Status.PENDING_ACCEPTANCE, SwapRequest.Status.PENDING_MANAGER,
                SwapRequest.Status.PENDING_PICKUP, SwapRequest.Status.APPROVED,
                SwapRequest.Status.REJECTED,
            ],
        ).select_related("target", "assignment__shift__location", "assignment__shift__required_skill").order_by("-created_at")
        certified_location_ids = LocationCertification.objects.filter(
            user=user, is_active=True
        ).values_list("location_id", flat=True)
        claimable = (
            Shift.objects.filter(
                is_published=True, location_id__in=certified_location_ids,
                required_skill__in=user.skills.all(), start_utc__gte=now,
            ).exclude(assignments__user=user).select_related("location", "required_skill").order_by("start_utc")
        )
        claimable_shifts = [s for s in claimable if not s.is_fully_staffed]
        pending_count = my_swaps.filter(
            status__in=[SwapRequest.Status.PENDING_ACCEPTANCE, SwapRequest.Status.PENDING_MANAGER]
        ).count()
        return {
            "today": now.date(),
            "upcoming_assignments": upcoming_assignments,
            "next_shift": upcoming_assignments.first(),
            "upcoming_count": upcoming_assignments.count(),
            "hours_this_week": week_hours,
            "my_swap_requests": my_swaps,
            "pending_swaps_count": pending_count,
            "claimable_shifts": claimable_shifts,
            "claimable_count": len(claimable_shifts),
        }

    @staticmethod
    def _overtime_warning_count(week_start, week_end) -> int:
        threshold = settings.SHIFTSYNC["WEEKLY_HOURS_WARNING"]
        count = 0
        for member in User.objects.filter(role=User.Role.STAFF, is_active=True):
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=member, status=ShiftAssignment.Status.ASSIGNED,
                    shift__start_utc__gte=week_start, shift__start_utc__lt=week_end,
                ).select_related("shift")
            )
            if hours >= threshold:
                count += 1
        return count


# ---------------------------------------------------------------------------
# On-duty now partial — HTMX polled every 60s
# ---------------------------------------------------------------------------

@login_required(login_url="/accounts/login/")
def on_duty_now(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    user = request.user
    if user.role == User.Role.ADMIN:
        locations = Location.objects.filter(is_active=True)
    elif user.role == User.Role.MANAGER:
        locations = user.managed_locations.filter(is_active=True)
    else:
        cert_ids = LocationCertification.objects.filter(user=user, is_active=True).values_list("location_id", flat=True)
        locations = Location.objects.filter(pk__in=cert_ids)
    on_duty_by_location = []
    for loc in locations:
        assignments = (
            ShiftAssignment.objects.filter(
                shift__location=loc, shift__start_utc__lte=now, shift__end_utc__gt=now,
                status=ShiftAssignment.Status.ASSIGNED,
            ).select_related("user", "shift__required_skill", "shift").order_by("user__last_name")
        )
        on_duty_by_location.append({"location": loc, "assignments": list(assignments), "now": now})
    return render(request, "scheduling/partials/on_duty_now.html", {"on_duty_by_location": on_duty_by_location})


# ---------------------------------------------------------------------------
# Schedule (manager/admin)
# ---------------------------------------------------------------------------

class ScheduleView(ManagerRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        now = timezone.now()
        managed_locations = self.get_manager_locations()
        week_param = request.GET.get("week")
        try:
            parts = week_param.replace("W", "").split("-") if week_param else []
            week_start = date.fromisocalendar(int(parts[0]), int(parts[1]), 1) if len(parts) == 2 else (
                now.date() - timedelta(days=now.weekday())
            )
        except (ValueError, AttributeError, IndexError):
            week_start = now.date() - timedelta(days=now.weekday())
        week_dates = [week_start + timedelta(days=i) for i in range(7)]
        week_end_date = week_start + timedelta(days=7)
        shifts = (
            Shift.objects.filter(
                location__in=managed_locations,
                start_utc__date__gte=week_start, start_utc__date__lt=week_end_date,
            ).select_related("location", "required_skill").prefetch_related("assignments__user").order_by("start_utc")
        )
        for shift in shifts:
            if not shift.is_published:
                shift.css_border, shift.css_badge = "border-secondary", "bg-secondary"
            elif shift.is_fully_staffed:
                shift.css_border, shift.css_badge = "border-success", "bg-success"
            else:
                shift.css_border, shift.css_badge = "border-warning", "bg-warning text-dark"
        grid = defaultdict(list)
        for shift in shifts:
            grid[(shift.start_utc.date(), shift.location_id)].append(shift)
        return render(request, "scheduling/schedule.html", {
            "managed_locations": managed_locations, "week_dates": week_dates,
            "week_start": week_start, "grid": dict(grid),
            "prev_week": (week_start - timedelta(days=7)).strftime("%G-W%V"),
            "next_week": (week_start + timedelta(days=7)).strftime("%G-W%V"),
            "current_week": week_start.strftime("%G-W%V"),
        })


# ---------------------------------------------------------------------------
# My Shifts (staff) —NB: exposes swap/drop buttons per assignment
# ---------------------------------------------------------------------------

class MyShiftsView(StaffRequiredMixin, View):
    """Full upcoming + past shift list. Includes Request Swap and Drop Shift actions."""

    def get(self, request: HttpRequest) -> HttpResponse:
        now = timezone.now()
        user = request.user
        upcoming = (
            ShiftAssignment.objects.filter(user=user, shift__start_utc__gte=now)
            .select_related("shift__location", "shift__required_skill").order_by("shift__start_utc")
        )
        past = (
            ShiftAssignment.objects.filter(user=user, shift__end_utc__lt=now)
            .select_related("shift__location", "shift__required_skill").order_by("-shift__start_utc")
        )
        # Track which assignments already have a pending swap/drop so buttons can be disabled
        pending_assignment_ids = set(
            SwapRequest.objects.filter(
                requester=user,
                status__in=[
                    SwapRequest.Status.PENDING_ACCEPTANCE,
                    SwapRequest.Status.PENDING_PICKUP,
                    SwapRequest.Status.PENDING_MANAGER,
                ],
            ).values_list("assignment_id", flat=True)
        )
        return render(request, "scheduling/my_shifts.html", {
            "upcoming": upcoming,
            "past": past,
            "pending_assignment_ids": pending_assignment_ids,
        })


# ---------------------------------------------------------------------------
# Initiate swap or drop 
# ---------------------------------------------------------------------------

class InitiateSwapView(StaffRequiredMixin, View):
    """
    Staff initiates a swap (targeting a named colleague) or drop (open pickup).

    GET  → renders form with list of qualified swap partners
    POST → creates the SwapRequest; enforces MAX_PENDING_SWAP_REQUESTS (Gap 4)
    """

    def get(self, request: HttpRequest, assignment_pk: int) -> HttpResponse:
        assignment = get_object_or_404(
            ShiftAssignment, pk=assignment_pk, user=request.user,
            status=ShiftAssignment.Status.ASSIGNED,
        )
        certified_ids = LocationCertification.objects.filter(
            location=assignment.shift.location, is_active=True
        ).values_list("user_id", flat=True)
        swap_partners = (
            User.objects.filter(
                pk__in=certified_ids, skills=assignment.shift.required_skill,
                is_active=True, role=User.Role.STAFF,
            ).exclude(pk=request.user.pk).order_by("last_name", "first_name")
        )
        return render(request, "scheduling/initiate_swap.html", {
            "assignment": assignment, "swap_partners": swap_partners,
        })

    def post(self, request: HttpRequest, assignment_pk: int) -> HttpResponse:
        user = request.user
        assignment = get_object_or_404(
            ShiftAssignment, pk=assignment_pk, user=user,
            status=ShiftAssignment.Status.ASSIGNED,
        )
        # Gap 4: enforce max pending requests
        max_pending = settings.SHIFTSYNC["MAX_PENDING_SWAP_REQUESTS"]
        active_count = SwapRequest.objects.filter(
            requester=user,
            status__in=[
                SwapRequest.Status.PENDING_ACCEPTANCE,
                SwapRequest.Status.PENDING_PICKUP,
                SwapRequest.Status.PENDING_MANAGER,
            ],
        ).count()
        if active_count >= max_pending:
            msg.error(
                request,
                f"You already have {active_count} pending request(s). "
                f"Maximum is {max_pending} — cancel an existing one first.",
            )
            return redirect("scheduling:my_shifts")

        request_type = request.POST.get("request_type", "drop")
        note = request.POST.get("note", "").strip()

        if request_type == "swap":
            target_id = request.POST.get("target_id")
            if not target_id:
                msg.error(request, "Please select a colleague to swap with.")
                return redirect("scheduling:initiate_swap", assignment_pk=assignment_pk)
            target = get_object_or_404(User, pk=target_id, role=User.Role.STAFF, is_active=True)
            SwapRequest.objects.create(
                requester=user, target=target, assignment=assignment,
                request_type=SwapRequest.Type.SWAP,
                status=SwapRequest.Status.PENDING_ACCEPTANCE,
                requester_note=note,
            )
            assignment.status = ShiftAssignment.Status.SWAP_PENDING
            assignment.save(update_fields=["status"])
            msg.success(request, f"Swap request sent to {target.get_full_name()}.")

            # Gap 6: ping Staff B in real-time
            shift = assignment.shift
            _notify_user(
                user_id=target.pk,
                notification_type="swap_request_received",
                title="Swap request from a colleague",
                body=(
                    f"{user.get_full_name()} wants to swap their "
                    f"{shift.required_skill.display_name} shift at {shift.location.name} "
                    f"on {shift.start_utc.strftime('%a %b %-d @ %-I:%M %p')} UTC with you."
                ),
            )
        else:
            expires_at = assignment.shift.start_utc - timedelta(
                hours=settings.SHIFTSYNC["DROP_REQUEST_EXPIRY_HOURS"]
            )
            SwapRequest.objects.create(
                requester=user, assignment=assignment,
                request_type=SwapRequest.Type.DROP,
                status=SwapRequest.Status.PENDING_PICKUP,
                requester_note=note, expires_at=expires_at,
            )
            assignment.status = ShiftAssignment.Status.SWAP_PENDING
            assignment.save(update_fields=["status"])
            msg.success(request, "Drop request posted — colleagues at this location will be notified.")

            # Gap 6: notify all certified staff at this location about the open drop
            shift = assignment.shift
            certified_staff_ids = LocationCertification.objects.filter(
                location=shift.location, is_active=True
            ).exclude(user=user).values_list("user_id", flat=True)
            for staff_id in certified_staff_ids:
                _notify_user(
                    user_id=staff_id,
                    notification_type="drop_available",
                    title="Open shift available for pickup",
                    body=(
                        f"A {shift.required_skill.display_name} shift at {shift.location.name} "
                        f"on {shift.start_utc.strftime('%a %b %-d @ %-I:%M %p')} UTC is open for pickup."
                    ),
                )

        logger.info("Staff %d initiated %s on assignment %d", user.pk, request_type, assignment_pk)
        return redirect("scheduling:swaps")


# ---------------------------------------------------------------------------
# Swaps (staff) — NB: accept / decline / cancel all handled here
# ---------------------------------------------------------------------------

class SwapListView(StaffRequiredMixin, View):
    """Staff swap and drop request management — GET renders, POST handles actions."""

    def get(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        my_requests = SwapRequest.objects.filter(requester=user).select_related(
            "target", "assignment__shift__location", "assignment__shift__required_skill"
        ).order_by("-created_at")
        received = SwapRequest.objects.filter(
            target=user, status=SwapRequest.Status.PENDING_ACCEPTANCE
        ).select_related("requester", "assignment__shift__location", "assignment__shift__required_skill")
        return render(request, "scheduling/swaps.html", {
            "my_requests": my_requests, "received_requests": received,
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Handle staff actions on swap requests.

        action=accept  → Staff B accepts incoming swap request → PENDING_MANAGER
        action=decline → Staff B declines swap request → REJECTED
        action=cancel  → Staff A cancels their own pending request → CANCELLED
        """
        user = request.user
        swap = get_object_or_404(SwapRequest, pk=request.POST.get("swap_id"))
        action = request.POST.get("action")

        if action == "accept":
            if swap.target != user or swap.status != SwapRequest.Status.PENDING_ACCEPTANCE:
                msg.error(request, "This request cannot be accepted.")
                return redirect("scheduling:swaps")
            swap.status = SwapRequest.Status.PENDING_MANAGER
            swap.target_accepted_at = timezone.now()
            swap.save(update_fields=["status", "target_accepted_at"])
            msg.success(request, "Swap accepted — a manager will review it shortly.")

        elif action == "decline":
            if swap.target != user or swap.status != SwapRequest.Status.PENDING_ACCEPTANCE:
                msg.error(request, "This request cannot be declined.")
                return redirect("scheduling:swaps")
            swap.status = SwapRequest.Status.REJECTED
            swap.save(update_fields=["status"])
            swap.assignment.status = ShiftAssignment.Status.ASSIGNED
            swap.assignment.save(update_fields=["status"])
            msg.info(request, "Swap request declined.")

        elif action == "cancel":
            # Gap 5: requester can cancel any pending request
            if swap.requester != user or not swap.is_pending:
                msg.error(request, "This request cannot be cancelled.")
                return redirect("scheduling:swaps")
            swap.status = SwapRequest.Status.CANCELLED
            swap.save(update_fields=["status"])
            swap.assignment.status = ShiftAssignment.Status.ASSIGNED
            swap.assignment.save(update_fields=["status"])
            msg.info(request, "Request cancelled.")

        else:
            msg.error(request, "Unknown action.")

        return redirect("scheduling:swaps")


# ---------------------------------------------------------------------------
# Swap review (manager) NB: re-runs constraints before approving
# ---------------------------------------------------------------------------

class SwapReviewView(ManagerRequiredMixin, View):
    """
    Manager approves or rejects a pending swap/drop request.

    NB: Before approving, re-runs the full constraint engine against
    the incoming staff member to catch any conflicts that arose since submission.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        swap = get_object_or_404(SwapRequest, pk=pk)
        return render(request, "scheduling/swap_review.html", {"swap": swap})

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        swap = get_object_or_404(SwapRequest, pk=pk)
        action = request.POST.get("action")

        if action == "approve":
            incoming = swap.target if swap.request_type == SwapRequest.Type.SWAP else swap.claimed_by
            if incoming is None:
                msg.error(request, "Cannot approve — no incoming staff member identified.")
                return redirect("scheduling:swap_review", pk=pk)

            # re-validate all 8 constraints for the incoming person
            result = ConstraintEngine.check(user=incoming, shift=swap.assignment.shift)
            if not result.ok and result.severity != "override_required":
                suggestion_names = ", ".join(s.full_name for s in result.suggestions[:3])
                detail = f" Consider: {suggestion_names}." if suggestion_names else ""
                msg.error(
                    request,
                    f"Cannot approve — {incoming.get_full_name()} no longer passes "
                    f"constraints: {result.reason}{detail}",
                )
                return redirect("scheduling:swap_review", pk=pk)

            # Transition assignments atomically
            swap.assignment.status = ShiftAssignment.Status.COVERED
            swap.assignment.save(update_fields=["status"])
            ShiftAssignment.objects.create(
                shift=swap.assignment.shift, user=incoming,
                assigned_by=request.user, status=ShiftAssignment.Status.ASSIGNED,
            )
            swap.status = SwapRequest.Status.APPROVED
            swap.reviewed_by = request.user
            swap.manager_reviewed_at = timezone.now()
            swap.save(update_fields=["status", "reviewed_by", "manager_reviewed_at"])

            if result.severity == "warning":
                msg.warning(request, f"Approved with warning: {result.reason}")
            else:
                msg.success(request, "Swap approved and assignments updated.")

            # Gap 6: notify both parties and broadcast schedule change
            shift = swap.assignment.shift
            _ws_broadcast(f"schedule_{shift.location_id}", {
                "type": "shift_assignment_changed",
                "shift_id": shift.pk,
                "user_id": incoming.pk,
                "action": "assigned",
            })
            _notify_user(
                user_id=swap.requester.pk,
                notification_type="swap_approved",
                title="Your swap request was approved",
                body=(
                    f"Your {shift.required_skill.display_name} shift on "
                    f"{shift.start_utc.strftime('%a %b %-d')} has been approved "
                    f"by a manager. {incoming.get_full_name()} will cover it."
                ),
            )
            _notify_user(
                user_id=incoming.pk,
                notification_type="shift_assigned",
                title="Swap approved — you’re now assigned",
                body=(
                    f"A manager approved your swap. You are now assigned to the "
                    f"{shift.required_skill.display_name} shift at {shift.location.name} "
                    f"on {shift.start_utc.strftime('%a %b %-d @ %-I:%M %p')} UTC."
                ),
            )

        elif action == "reject":
            swap.status = SwapRequest.Status.REJECTED
            swap.reviewed_by = request.user
            swap.manager_reviewed_at = timezone.now()
            swap.manager_note = request.POST.get("note", "")
            swap.save(update_fields=["status", "reviewed_by", "manager_reviewed_at", "manager_note"])
            swap.assignment.status = ShiftAssignment.Status.ASSIGNED
            swap.assignment.save(update_fields=["status"])
            msg.info(request, "Swap request rejected.")

            # Gap 6: notify requester of rejection
            shift = swap.assignment.shift
            _notify_user(
                user_id=swap.requester.pk,
                notification_type="swap_rejected",
                title="Swap request rejected",
                body=(
                    f"A manager rejected your swap request for the "
                    f"{shift.required_skill.display_name} shift on "
                    f"{shift.start_utc.strftime('%a %b %-d')}. "
                    f"You remain assigned to this shift."
                ),
            )

        return redirect("scheduling:dashboard")


# ---------------------------------------------------------------------------
# Claim shift — HTMX POST (staff)
# ---------------------------------------------------------------------------

@login_required(login_url="/accounts/login/")
def claim_shift(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    shift = get_object_or_404(Shift, pk=pk)
    if shift.is_fully_staffed:
        return HttpResponse(
            '<li class="list-group-item text-warning py-3">'
            '<i class="bi bi-exclamation-circle me-1"></i>This shift was just claimed by someone else.</li>'
        )
    ShiftAssignment.objects.create(
        shift=shift, user=request.user, assigned_by=request.user,
        status=ShiftAssignment.Status.ASSIGNED,
    )
    logger.info("Staff %d claimed shift %d", request.user.pk, pk)
    return HttpResponse(
        '<li class="list-group-item text-success py-3">'
        f'<i class="bi bi-check-circle-fill me-1"></i>'
        f'Claimed: <strong>{shift.required_skill.display_name}</strong> '
        f'on {shift.start_utc.strftime("%b %d @ %I:%M %p")} UTC.</li>'
    )


# ---------------------------------------------------------------------------
# Locations redirect stub
# ---------------------------------------------------------------------------

class LocationListView(AdminRequiredMixin, View):
    def get(self, request: HttpRequest):
        return redirect("/locations/")


# ---------------------------------------------------------------------------
# Shift management — create, assign, publish, delete
# ---------------------------------------------------------------------------

class ShiftManageView(ManagerRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        from apps.accounts.models import Skill
        managed_locations = self.get_manager_locations()
        now = timezone.now()
        from_date_raw = request.GET.get("from_date")
        try:
            from_date = datetime.strptime(from_date_raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            from_date = now.date() - timedelta(days=now.weekday())
        to_date = from_date + timedelta(days=7)
        selected_location_id = None
        loc_param = request.GET.get("location")
        if loc_param:
            try:
                selected_location_id = int(loc_param)
            except ValueError:
                pass
        shifts = (
            Shift.objects.filter(
                location__in=managed_locations,
                start_utc__date__gte=from_date, start_utc__date__lt=to_date,
            ).select_related("location", "required_skill").prefetch_related("assignments__user").order_by("start_utc")
        )
        if selected_location_id:
            shifts = shifts.filter(location_id=selected_location_id)
        status_filter = request.GET.get("status", "")
        if status_filter == "draft":
            shifts = [s for s in shifts if not s.is_published]
        elif status_filter == "published":
            shifts = [s for s in shifts if s.is_published]
        elif status_filter == "understaffed":
            shifts = [s for s in shifts if s.is_published and not s.is_fully_staffed]
        else:
            shifts = list(shifts)
        unpublished_count = sum(1 for s in shifts if not s.is_published)
        staff_map = {}
        week_start_dt = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
        week_end_dt = week_start_dt + timedelta(days=7)
        for loc in managed_locations:
            certs = LocationCertification.objects.filter(
                location=loc, is_active=True
            ).prefetch_related("user__skills").select_related("user")
            for cert in certs:
                member = cert.user
                hours = sum(
                    a.shift.duration_hours
                    for a in ShiftAssignment.objects.filter(
                        user=member, status__in=[ShiftAssignment.Status.ASSIGNED],
                        shift__start_utc__gte=week_start_dt, shift__start_utc__lt=week_end_dt,
                    ).select_related("shift")
                )
                for skill in member.skills.all():
                    key = f"{loc.pk}-{skill.pk}"
                    if key not in staff_map:
                        staff_map[key] = []
                    staff_map[key].append({"id": member.pk, "name": member.get_full_name(), "hours": round(hours, 1)})
        return render(request, "scheduling/shift_manage.html", {
            "managed_locations": managed_locations, "shifts": shifts,
            "from_date": from_date, "selected_location_id": selected_location_id,
            "status_filter": status_filter, "unpublished_count": unpublished_count,
            "all_skills": Skill.objects.all(), "staff_json": json.dumps(staff_map),
        })


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class CreateShiftView(ManagerRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from apps.accounts.models import Skill
        location = self.get_location_or_403(int(request.POST.get("location_id", 0)))
        skill = get_object_or_404(Skill, pk=request.POST.get("skill_id"))
        try:
            start = datetime.fromisoformat(request.POST.get("start_utc"))
            end = datetime.fromisoformat(request.POST.get("end_utc"))
            start = timezone.make_aware(start) if timezone.is_naive(start) else start
            end = timezone.make_aware(end) if timezone.is_naive(end) else end
        except (ValueError, TypeError):
            msg.error(request, "Invalid date/time format.")
            return redirect("scheduling:shift_manage")
        if end <= start:
            msg.error(request, "End time must be after start time.")
            return redirect("scheduling:shift_manage")
        headcount = max(1, int(request.POST.get("headcount_needed", 1)))
        publish = "publish_immediately" in request.POST
        Shift.objects.create(
            location=location, required_skill=skill, start_utc=start, end_utc=end,
            headcount_needed=headcount, is_published=publish, created_by=request.user,
        )
        msg.success(request, f"Shift created{' and published' if publish else ' as draft'}.")
        logger.info("Manager %d created shift at location %d", request.user.pk, location.pk)
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class AssignStaffView(ManagerRequiredMixin, View):
    """
    POST-only: assign a staff member to a shift.

    Runs the full 8-constraint engine (SELECT FOR UPDATE inside) before
    creating the assignment. Handles all four result severities:
      ok         → create assignment, success message
      warning    → create assignment, warning message (e.g. approaching 35h)
      block      → reject, show reason + suggested alternatives
      override_required → reject unless override_reason POSTed; if provided,
                          creates a ManagerOverride record and proceeds

    POST fields: shift_id, user_id, override_reason (optional — day-7 scenario only)
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        shift = get_object_or_404(Shift, pk=request.POST.get("shift_id"))
        self.get_location_or_403(shift.location_id)
        staff_member = get_object_or_404(User, pk=request.POST.get("user_id"))

        # Run all 8 constraints (SELECT FOR UPDATE inside prevents TOCTOU races)
        result = ConstraintEngine.check(user=staff_member, shift=shift)

        if not result.ok:
            if result.severity == "override_required":
                override_reason = request.POST.get("override_reason", "").strip()
                if not override_reason:
                    msg.warning(
                        request,
                        f"Override required to assign {staff_member.get_full_name()}: "
                        f"{result.reason} — provide an override reason in the form below.",
                    )
                    # Redirect back with params so the modal auto-opens with the
                    # override reason field visible (JS reads ?override_needed=1)
                    from django.urls import reverse
                    base = reverse("scheduling:shift_manage")
                    qs = (
                        f"?override_needed=1"
                        f"&shift_id={shift.pk}"
                        f"&user_id={staff_member.pk}"
                        f"&from_date={shift.start_utc.strftime('%Y-%m-%d')}"
                    )
                    return redirect(base + qs)
                # reason provided — fall through to create assignment
            else:
                suggestion_names = ", ".join(s.full_name for s in result.suggestions[:3])
                detail = f" Alternatives: {suggestion_names}." if suggestion_names else ""
                msg.error(request, f"Cannot assign: {result.reason}{detail}")
                return redirect("scheduling:shift_manage")

        assignment, created = ShiftAssignment.objects.get_or_create(
            shift=shift, user=staff_member,
            defaults={"assigned_by": request.user, "status": ShiftAssignment.Status.ASSIGNED},
        )
        if not created:
            msg.warning(request, f"{staff_member.get_full_name()} is already assigned to this shift.")
            return redirect("scheduling:shift_manage")

        if result.severity == "override_required":
            override_reason = request.POST.get("override_reason", "").strip()
            ManagerOverride.objects.create(
                manager=request.user, assignment=assignment,
                constraint_violated=result.constraint_id, reason=override_reason,
            )
            msg.warning(request, f"{staff_member.get_full_name()} assigned with manager override: {override_reason}")
        elif result.severity == "warning":
            msg.warning(request, f"Assigned — note: {result.reason}")
        else:
            msg.success(request, f"{staff_member.get_full_name()} assigned to shift.")

        logger.info(
            "Manager %d assigned user %d to shift %d (severity=%s)",
            request.user.pk, staff_member.pk, shift.pk, result.severity,
        )

        # notify location schedule group + assigned staff member via WebSocket
        _ws_broadcast(f"schedule_{shift.location_id}", {
            "type": "shift_assignment_changed",
            "shift_id": shift.pk,
            "user_id": staff_member.pk,
            "action": "assigned",
        })
        _notify_user(
            user_id=staff_member.pk,
            notification_type="shift_assigned",
            title="You\u2019ve been assigned to a shift",
            body=(
                f"You have been assigned to a {shift.required_skill.display_name} shift "
                f"at {shift.location.name} on {shift.start_utc.strftime('%a %b %-d @ %-I:%M %p')} UTC."
            ),
        )
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class TogglePublishView(ManagerRequiredMixin, View):
    """
    POST-only: toggle a shift's is_published flag.

    GAP 2 FIX: blocks unpublishing a shift that is within the 48-hour edit cutoff.
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        shift = get_object_or_404(Shift, pk=pk)
        self.get_location_or_403(shift.location_id)

        # enforce edit cutoff — schedule locked once window has passed
        if shift.is_published and shift.is_past_edit_cutoff:
            msg.error(
                request,
                f"This shift is locked — it starts within {shift.edit_cutoff_hours} hours "
                f"and cannot be unpublished. Use the swap/drop workflow for last-minute changes.",
            )
            return redirect("scheduling:shift_manage")

        shift.is_published = not shift.is_published
        if shift.is_published:
            shift.published_at = timezone.now()
            shift.published_by = request.user
            shift.save(update_fields=["is_published", "published_at", "published_by"])
        else:
            shift.save(update_fields=["is_published"])

        state = "published" if shift.is_published else "unpublished (draft)"
        msg.success(request, f"Shift {state}.")

        # Gap 6: if just published, broadcast to the location's schedule group
        if shift.is_published:
            _ws_broadcast(f"schedule_{shift.location_id}", {
                "type": "schedule_published",
                "location_id": shift.location_id,
                "week": shift.start_utc.strftime("%G-W%V"),
                "published_by": request.user.get_full_name(),
            })
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class PublishWeekView(ManagerRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        from_date_raw = request.POST.get("from_date")
        try:
            from_date = datetime.strptime(from_date_raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            from_date = timezone.now().date() - timedelta(days=timezone.now().weekday())
        to_date = from_date + timedelta(days=7)
        managed_locations = self.get_manager_locations()
        qs = Shift.objects.filter(
            location__in=managed_locations, is_published=False,
            start_utc__date__gte=from_date, start_utc__date__lt=to_date,
        )
        loc_param = request.POST.get("location")
        if loc_param:
            qs = qs.filter(location_id=loc_param)
        count = qs.update(is_published=True)
        msg.success(request, f"{count} shift{'s' if count != 1 else ''} published.")

        # Gap 6: broadcast schedule-published to every affected location group
        for loc in managed_locations:
            _ws_broadcast(f"schedule_{loc.pk}", {
                "type": "schedule_published",
                "location_id": loc.pk,
                "week": from_date.strftime("%G-W%V"),
                "published_by": request.user.get_full_name(),
            })
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class DeleteShiftView(ManagerRequiredMixin, View):
    """
    POST-only: delete an unpublished shift.
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        shift = get_object_or_404(Shift, pk=pk)
        self.get_location_or_403(shift.location_id)

        if shift.is_published:
            msg.error(request, "Cannot delete a published shift. Unpublish it first.")
        elif shift.is_past_edit_cutoff:
            msg.error(
                request,
                f"Cannot delete — this shift starts within {shift.edit_cutoff_hours} hours "
                f"and is within the edit lock window.",
            )
        else:
            shift.delete()
            msg.success(request, "Draft shift deleted.")

        return redirect("scheduling:shift_manage")