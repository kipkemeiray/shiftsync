"""
Scheduling views for ShiftSync.

The dashboard view acts as a router — it collects role-specific context
and renders the appropriate template, keeping the URL (/dashboard/) the
same for all roles.

View inventory:
  DashboardView      → role-aware homepage (GET)
  on_duty_now        → HTMX partial: who is on shift right now (GET)
  ScheduleView       → week grid, manager/admin (GET)
  MyShiftsView       → staff full shift list (GET)
  SwapListView       → staff swap/drop management (GET)
  SwapReviewView     → manager approve/reject swap (GET, POST)
  claim_shift        → HTMX POST: staff claims open shift
  LocationListView   → admin location overview (GET)
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.locations.models import Location, LocationCertification
from apps.scheduling.models import Shift, ShiftAssignment, SwapRequest
from core.permissions import AdminRequiredMixin, ManagerRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard — role-aware router
# ---------------------------------------------------------------------------

@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class DashboardView(View):
    """
    Central dashboard that renders a different template per user role.

    Admin   → dashboard_admin.html   (platform-wide stats + all locations)
    Manager → dashboard_manager.html  (location-scoped stats + coverage gaps)
    Staff   → dashboard_staff.html    (personal shifts + swap requests)
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """Route to the correct dashboard based on the user's role."""
        user = request.user
        if user.role == User.Role.ADMIN:
            return render(request, "scheduling/dashboard_admin.html", self._admin_context())
        if user.role == User.Role.MANAGER:
            return render(request, "scheduling/dashboard_manager.html", self._manager_context(user))
        return render(request, "scheduling/dashboard_staff.html", self._staff_context(user))

    # ------------------------------------------------------------------
    # Private context builders
    # ------------------------------------------------------------------

    def _admin_context(self) -> dict:
        """
        Build context for the admin dashboard.

        Returns platform-wide stats: locations, staff, shifts, overtime warnings,
        per-location coverage summaries, pending swap approvals, recent audit log.
        """
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)

        locations = Location.objects.filter(is_active=True).prefetch_related("managers")

        location_summaries = []
        for loc in locations:
            shifts = Shift.objects.filter(
                location=loc, start_utc__gte=week_start, start_utc__lt=week_end
            )
            total_headcount = sum(s.headcount_needed for s in shifts)
            filled = sum(s.assigned_count for s in shifts)
            coverage_pct = int((filled / total_headcount) * 100) if total_headcount else 100
            location_summaries.append({
                "location": loc,
                "shift_count": shifts.count(),
                "staff_count": LocationCertification.objects.filter(
                    location=loc, is_active=True
                ).count(),
                "coverage_pct": coverage_pct,
            })

        return {
            "today": now.date(),
            "total_locations": locations.count(),
            "total_staff": User.objects.filter(role=User.Role.STAFF, is_active=True).count(),
            "shifts_this_week": Shift.objects.filter(
                start_utc__gte=week_start, start_utc__lt=week_end
            ).count(),
            "overtime_warnings": self._overtime_warning_count(week_start, week_end),
            "location_summaries": location_summaries,
            "pending_swaps": SwapRequest.objects.filter(
                status=SwapRequest.Status.PENDING_MANAGER
            ).select_related(
                "requester", "assignment__shift__location", "assignment__shift__required_skill"
            )[:10],
            "recent_audit": AuditLog.objects.select_related("actor").order_by("-created_at")[:10],
        }

    def _manager_context(self, user: User) -> dict:
        """
        Build context for the manager dashboard scoped to their locations.

        Args:
            user: The logged-in manager.
        """
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)

        managed_locations = user.managed_locations.filter(is_active=True)

        week_shifts = Shift.objects.filter(
            location__in=managed_locations,
            start_utc__gte=week_start,
            start_utc__lt=week_end,
        ).select_related("location", "required_skill")

        understaffed = [s for s in week_shifts if s.is_published and not s.is_fully_staffed]

        staff_ids = LocationCertification.objects.filter(
            location__in=managed_locations, is_active=True
        ).values_list("user_id", flat=True).distinct()
        staff = User.objects.filter(pk__in=staff_ids)

        staff_hours = []
        for member in staff:
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=member,
                    status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                    shift__start_utc__gte=week_start,
                    shift__start_utc__lt=week_end,
                ).select_related("shift")
            )
            staff_hours.append({"user": member, "hours": hours})
        staff_hours.sort(key=lambda x: x["hours"], reverse=True)

        return {
            "today": now.date(),
            "managed_locations": managed_locations,
            "total_staff": staff_ids.count(),
            "shifts_this_week": week_shifts.count(),
            "understaffed_shifts": understaffed[:8],
            "understaffed_count": len(understaffed),
            "pending_swaps": SwapRequest.objects.filter(
                status=SwapRequest.Status.PENDING_MANAGER,
                assignment__shift__location__in=managed_locations,
            ).select_related(
                "requester", "assignment__shift__location", "assignment__shift__required_skill"
            )[:10],
            "staff_hours": staff_hours,
        }

    def _staff_context(self, user: User) -> dict:
        """
        Build context for the staff dashboard.

        Args:
            user: The logged-in staff member.
        """
        now = timezone.now()
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)

        upcoming_assignments = (
            ShiftAssignment.objects.filter(
                user=user,
                status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                shift__start_utc__gte=now,
                shift__start_utc__lte=now + timedelta(days=14),
            )
            .select_related("shift__location", "shift__required_skill")
            .order_by("shift__start_utc")
        )

        week_hours = sum(
            a.shift.duration_hours
            for a in ShiftAssignment.objects.filter(
                user=user,
                status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                shift__start_utc__gte=week_start,
                shift__start_utc__lt=week_end,
            ).select_related("shift")
        )

        my_swaps = SwapRequest.objects.filter(
            requester=user,
            status__in=[
                SwapRequest.Status.PENDING_ACCEPTANCE,
                SwapRequest.Status.PENDING_MANAGER,
                SwapRequest.Status.PENDING_PICKUP,
                SwapRequest.Status.APPROVED,
                SwapRequest.Status.REJECTED,
            ],
        ).select_related(
            "target", "assignment__shift__location", "assignment__shift__required_skill"
        ).order_by("-created_at")

        certified_location_ids = LocationCertification.objects.filter(
            user=user, is_active=True
        ).values_list("location_id", flat=True)

        claimable = (
            Shift.objects.filter(
                is_published=True,
                location_id__in=certified_location_ids,
                required_skill__in=user.skills.all(),
                start_utc__gte=now,
            )
            .exclude(assignments__user=user)
            .select_related("location", "required_skill")
            .order_by("start_utc")
        )
        claimable_shifts = [s for s in claimable if not s.is_fully_staffed]

        pending_count = my_swaps.filter(
            status__in=[SwapRequest.Status.PENDING_ACCEPTANCE, SwapRequest.Status.PENDING_MANAGER]
        ).count()

        return {
            "today": now.date(),
            "upcoming_assignments": upcoming_assignments[:5],
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
        """Count staff members with 35+ projected hours this week."""
        from django.conf import settings
        threshold = settings.SHIFTSYNC["WEEKLY_HOURS_WARNING"]
        count = 0
        for member in User.objects.filter(role=User.Role.STAFF, is_active=True):
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=member,
                    status=ShiftAssignment.Status.ASSIGNED,
                    shift__start_utc__gte=week_start,
                    shift__start_utc__lt=week_end,
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
    """
    Return an HTML partial showing who is on shift right now at each location.

    Admins see all locations. Managers see their locations. Staff see theirs.

    Args:
        request: Authenticated GET request.

    Returns:
        Rendered partial (no extends/base template).
    """
    now = timezone.now()
    user = request.user

    if user.role == User.Role.ADMIN:
        locations = Location.objects.filter(is_active=True)
    elif user.role == User.Role.MANAGER:
        locations = user.managed_locations.filter(is_active=True)
    else:
        cert_ids = LocationCertification.objects.filter(
            user=user, is_active=True
        ).values_list("location_id", flat=True)
        locations = Location.objects.filter(pk__in=cert_ids)

    on_duty_by_location = []
    for loc in locations:
        assignments = (
            ShiftAssignment.objects.filter(
                shift__location=loc,
                shift__start_utc__lte=now,
                shift__end_utc__gt=now,
                status=ShiftAssignment.Status.ASSIGNED,
            )
            .select_related("user", "shift__required_skill")
            .order_by("user__last_name")
        )
        on_duty_by_location.append({"location": loc, "assignments": list(assignments)})

    return render(
        request,
        "scheduling/partials/on_duty_now.html",
        {"on_duty_by_location": on_duty_by_location},
    )


# ---------------------------------------------------------------------------
# Schedule (manager/admin)
# ---------------------------------------------------------------------------

class ScheduleView(ManagerRequiredMixin, View):
    """
    Week-grid schedule view for managers and admins.

    Supports ?week=YYYY-Www navigation. Defaults to current week.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the weekly schedule grid for managed locations."""
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
                start_utc__date__gte=week_start,
                start_utc__date__lt=week_end_date,
            )
            .select_related("location", "required_skill")
            .prefetch_related("assignments__user")
            .order_by("start_utc")
        )

        # Annotate each shift with pre-computed CSS classes so the template
        # never needs {% if %} / {% with %} blocks for card colours.
        # Django templates cannot use {% elif %} inside {% with %}, and
        # {% if %} inside HTML attribute strings is also invalid, so we
        # resolve the classes here in Python instead.
        for shift in shifts:
            if not shift.is_published:
                shift.css_border = "border-secondary"
                shift.css_badge = "bg-secondary"
            elif shift.is_fully_staffed:
                shift.css_border = "border-success"
                shift.css_badge = "bg-success"
            else:
                shift.css_border = "border-warning"
                shift.css_badge = "bg-warning text-dark"

        grid = defaultdict(list)
        for shift in shifts:
            grid[(shift.start_utc.date(), shift.location_id)].append(shift)

        return render(request, "scheduling/schedule.html", {
            "managed_locations": managed_locations,
            "week_dates": week_dates,
            "week_start": week_start,
            "grid": dict(grid),
            "prev_week": (week_start - timedelta(days=7)).strftime("%G-W%V"),
            "next_week": (week_start + timedelta(days=7)).strftime("%G-W%V"),
            "current_week": week_start.strftime("%G-W%V"),
        })


# ---------------------------------------------------------------------------
# My Shifts (staff)
# ---------------------------------------------------------------------------

class MyShiftsView(StaffRequiredMixin, View):
    """Full upcoming + past shift list for a staff member."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the staff member's shift history and upcoming schedule."""
        now = timezone.now()
        user = request.user

        upcoming = (
            ShiftAssignment.objects.filter(user=user, shift__start_utc__gte=now)
            .select_related("shift__location", "shift__required_skill")
            .order_by("shift__start_utc")
        )
        past = (
            ShiftAssignment.objects.filter(user=user, shift__end_utc__lt=now)
            .select_related("shift__location", "shift__required_skill")
            .order_by("-shift__start_utc")[:20]
        )

        return render(request, "scheduling/my_shifts.html", {
            "upcoming": upcoming,
            "past": past,
        })


# ---------------------------------------------------------------------------
# Swaps (staff)
# ---------------------------------------------------------------------------

class SwapListView(StaffRequiredMixin, View):
    """Staff-facing swap and drop request management."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render swap requests initiated by or addressed to this staff member."""
        user = request.user
        my_requests = SwapRequest.objects.filter(requester=user).select_related(
            "target", "assignment__shift__location", "assignment__shift__required_skill"
        ).order_by("-created_at")

        received = SwapRequest.objects.filter(
            target=user, status=SwapRequest.Status.PENDING_ACCEPTANCE
        ).select_related(
            "requester", "assignment__shift__location", "assignment__shift__required_skill"
        )

        return render(request, "scheduling/swaps.html", {
            "my_requests": my_requests,
            "received_requests": received,
        })


# ---------------------------------------------------------------------------
# Swap review (manager/admin)
# ---------------------------------------------------------------------------

class SwapReviewView(ManagerRequiredMixin, View):
    """Manager approves or rejects a pending swap/drop request."""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Render the swap review form."""
        swap = get_object_or_404(SwapRequest, pk=pk)
        return render(request, "scheduling/swap_review.html", {"swap": swap})

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """
        Process approve or reject action on a swap request.

        Args:
            request: POST with 'action' = 'approve' | 'reject'.
            pk:      SwapRequest primary key.
        """
        swap = get_object_or_404(SwapRequest, pk=pk)
        action = request.POST.get("action")

        if action == "approve":
            swap.status = SwapRequest.Status.APPROVED
            swap.reviewed_by = request.user
            swap.manager_reviewed_at = timezone.now()
            swap.save()
        elif action == "reject":
            swap.status = SwapRequest.Status.REJECTED
            swap.reviewed_by = request.user
            swap.manager_reviewed_at = timezone.now()
            swap.manager_note = request.POST.get("note", "")
            swap.save()
            swap.assignment.status = ShiftAssignment.Status.ASSIGNED
            swap.assignment.save()

        return redirect("scheduling:dashboard")


# ---------------------------------------------------------------------------
# Claim shift — HTMX POST (staff)
# ---------------------------------------------------------------------------

@login_required(login_url="/accounts/login/")
def claim_shift(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Staff member claims an open/understaffed shift via HTMX POST.

    Returns an HTML fragment replacing the claimed shift's list item.

    Args:
        request: Authenticated POST request.
        pk:      Shift primary key.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    shift = get_object_or_404(Shift, pk=pk)

    if shift.is_fully_staffed:
        return HttpResponse(
            '<li class="list-group-item text-warning py-3">'
            '<i class="bi bi-exclamation-circle me-1"></i>'
            'This shift was just claimed by someone else.</li>'
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
# Locations (admin) - redirects to the locations app
# ---------------------------------------------------------------------------

class LocationListView(AdminRequiredMixin, View):
    """Redirect to locations:list (kept for backward-compat URL name)."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return redirect("/locations/")


# ---------------------------------------------------------------------------
# Shift management (manager/admin) — create, assign, publish, delete
# ---------------------------------------------------------------------------

import json
from datetime import datetime


class ShiftManageView(ManagerRequiredMixin, View):
    """
    Manager shift management page.

    Lists all shifts for managed locations with create/assign/publish controls.
    Supports filtering by location, date, and publish status.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render the shift management table with filters applied.

        Query params:
          location  → filter to a single location pk
          from_date → week start date (YYYY-MM-DD); defaults to Monday of this week
          status    → 'draft' | 'published' | 'understaffed'
        """
        from apps.accounts.models import Skill

        managed_locations = self.get_manager_locations()
        now = timezone.now()

        # Parse from_date
        from_date_raw = request.GET.get("from_date")
        try:
            from_date = datetime.strptime(from_date_raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            from_date = now.date() - timedelta(days=now.weekday())

        to_date = from_date + timedelta(days=7)

        # Location filter
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
                start_utc__date__gte=from_date,
                start_utc__date__lt=to_date,
            )
            .select_related("location", "required_skill")
            .prefetch_related("assignments__user")
            .order_by("start_utc")
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

        # Build staff_json: {locationId-skillId: [{id, name, hours}]}
        # Used by the assign modal JS to populate the dropdown
        from apps.locations.models import LocationCertification
        staff_map = {}
        week_start_dt = timezone.make_aware(datetime.combine(from_date, __import__("datetime").time.min))
        week_end_dt = week_start_dt + timedelta(days=7)

        for loc in managed_locations:
            certs = LocationCertification.objects.filter(
                location=loc, is_active=True
            ).select_related("user__skills" if False else "user").prefetch_related("user__skills")

            for cert in certs:
                member = cert.user
                hours = sum(
                    a.shift.duration_hours
                    for a in ShiftAssignment.objects.filter(
                        user=member,
                        status__in=[ShiftAssignment.Status.ASSIGNED],
                        shift__start_utc__gte=week_start_dt,
                        shift__start_utc__lt=week_end_dt,
                    ).select_related("shift")
                )
                for skill in member.skills.all():
                    key = f"{loc.pk}-{skill.pk}"
                    if key not in staff_map:
                        staff_map[key] = []
                    staff_map[key].append({
                        "id": member.pk,
                        "name": member.get_full_name(),
                        "hours": round(hours, 1),
                    })

        return render(request, "scheduling/shift_manage.html", {
            "managed_locations": managed_locations,
            "shifts": shifts,
            "from_date": from_date,
            "selected_location_id": selected_location_id,
            "status_filter": status_filter,
            "unpublished_count": unpublished_count,
            "all_skills": Skill.objects.all(),
            "staff_json": json.dumps(staff_map),
        })


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class CreateShiftView(ManagerRequiredMixin, View):
    """
    POST-only: create a new shift for a managed location.

    Redirects back to shift_manage on success.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Create a shift from POST data.

        POST fields:
          location_id, skill_id, start_utc (datetime-local),
          end_utc (datetime-local), headcount_needed, publish_immediately (checkbox)
        """
        from apps.accounts.models import Skill
        from apps.locations.models import Location
        from django.contrib import messages as msg

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
            location=location,
            required_skill=skill,
            start_utc=start,
            end_utc=end,
            headcount_needed=headcount,
            is_published=publish,
        )
        msg.success(request, f"Shift created{' and published' if publish else ' as draft'}.")
        logger.info("Manager %d created shift at location %d", request.user.pk, location.pk)
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class AssignStaffView(ManagerRequiredMixin, View):
    """
    POST-only: assign a staff member to a shift.

    Verifies the shift is at a managed location and the staff member
    is certified there before creating the assignment.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Assign a user to a shift.

        POST fields: shift_id, user_id
        """
        from django.contrib import messages as msg
        from apps.accounts.models import User
        from apps.locations.models import LocationCertification

        shift = get_object_or_404(Shift, pk=request.POST.get("shift_id"))
        self.get_location_or_403(shift.location_id)  # ensure manager owns this location

        staff_member = get_object_or_404(User, pk=request.POST.get("user_id"))

        # Verify certification
        if not LocationCertification.objects.filter(
            user=staff_member, location=shift.location, is_active=True
        ).exists():
            msg.error(request, f"{staff_member.get_full_name()} is not certified at this location.")
            return redirect("scheduling:shift_manage")

        # Verify skill
        if not staff_member.skills.filter(pk=shift.required_skill_id).exists():
            msg.error(request, f"{staff_member.get_full_name()} does not have the required skill.")
            return redirect("scheduling:shift_manage")

        _, created = ShiftAssignment.objects.get_or_create(
            shift=shift, user=staff_member,
            defaults={"assigned_by": request.user, "status": ShiftAssignment.Status.ASSIGNED},
        )
        if created:
            msg.success(request, f"{staff_member.get_full_name()} assigned to shift.")
        else:
            msg.warning(request, f"{staff_member.get_full_name()} is already assigned to this shift.")

        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class TogglePublishView(ManagerRequiredMixin, View):
    """
    POST-only: toggle a shift's is_published flag.

    Publishing makes it visible to assigned staff. Unpublishing hides it.
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Toggle published state of a single shift."""
        from django.contrib import messages as msg

        shift = get_object_or_404(Shift, pk=pk)
        self.get_location_or_403(shift.location_id)

        shift.is_published = not shift.is_published
        shift.save(update_fields=["is_published"])
        state = "published" if shift.is_published else "unpublished (draft)"
        msg.success(request, f"Shift {state}.")
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class PublishWeekView(ManagerRequiredMixin, View):
    """
    POST-only: publish all draft shifts for the selected week and location.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        """Bulk-publish all draft shifts matching the current filter."""
        from django.contrib import messages as msg
        from datetime import datetime as dt

        from_date_raw = request.POST.get("from_date")
        try:
            from_date = dt.strptime(from_date_raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            from_date = timezone.now().date() - timedelta(days=timezone.now().weekday())

        to_date = from_date + timedelta(days=7)
        managed_locations = self.get_manager_locations()

        qs = Shift.objects.filter(
            location__in=managed_locations,
            is_published=False,
            start_utc__date__gte=from_date,
            start_utc__date__lt=to_date,
        )

        loc_param = request.POST.get("location")
        if loc_param:
            qs = qs.filter(location_id=loc_param)

        count = qs.update(is_published=True)
        msg.success(request, f"{count} shift{'s' if count != 1 else ''} published.")
        return redirect("scheduling:shift_manage")


@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class DeleteShiftView(ManagerRequiredMixin, View):
    """
    POST-only: delete a draft (unpublished) shift.

    Published shifts cannot be deleted; they must be unpublished first.
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Delete a draft shift."""
        from django.contrib import messages as msg

        shift = get_object_or_404(Shift, pk=pk)
        self.get_location_or_403(shift.location_id)

        if shift.is_published:
            msg.error(request, "Cannot delete a published shift. Unpublish it first.")
        else:
            shift.delete()
            msg.success(request, "Draft shift deleted.")

        return redirect("scheduling:shift_manage")