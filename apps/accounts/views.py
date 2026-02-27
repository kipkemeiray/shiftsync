"""
Accounts views for ShiftSync.

View inventory:
  LoginView        → email/password login with HTMX error fragment
  LogoutView       → POST-only logout
  ProfileView      → view/edit own profile
  AvailabilityView → staff set weekly + one-off availability windows
  StaffListView    → manager/admin: browse all staff with skills + hours
"""

import logging

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.accounts.models import StaffAvailability, User
from apps.locations.models import LocationCertification
from apps.scheduling.models import ShiftAssignment
from core.permissions import ManagerRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

class LoginView(View):
    """
    Email/password login view.

    GET  → renders the login page.
    POST → authenticates; on failure returns a plain HTML error fragment
           for HTMX to swap into #login-errors. On success sends HX-Redirect.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the login page. Redirect authenticated users to dashboard."""
        if request.user.is_authenticated:
            return redirect("scheduling:dashboard")
        return render(request, "accounts/login.html")

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Authenticate the submitted credentials.

        Returns:
            204 + HX-Redirect on success.
            200 HTML error fragment on failure (HTMX swaps into #login-errors).
        """
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)
            response = HttpResponse(status=204)
            response["HX-Redirect"] = "/"
            return response

        # Return 200 so HTMX performs the swap (non-2xx is silently discarded)
        return HttpResponse(
            '<div class="alert alert-danger mb-0">'
            '<i class="bi bi-exclamation-circle-fill me-2"></i>'
            'Invalid email or password. Please try again.'
            '</div>',
            status=200,
        )


class LogoutView(View):
    """POST-only logout to prevent CSRF-based logout via GET links."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Log the user out and redirect to login."""
        logout(request)
        return redirect("accounts:login")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class ProfileView(View):
    """
    View and update the logged-in user's profile.

    GET  → renders profile form pre-filled with current data.
    POST → saves valid changes; re-renders form with errors on failure.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the profile page."""
        return render(request, "accounts/profile.html", {"user_obj": request.user})

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Update first name, last name, phone number, and notification preferences.

        Args:
            request: POST with profile fields.
        """
        user = request.user
        user.first_name = request.POST.get("first_name", user.first_name).strip()
        user.last_name = request.POST.get("last_name", user.last_name).strip()
        user.phone_number = request.POST.get("phone_number", user.phone_number).strip()
        user.notify_in_app = "notify_in_app" in request.POST
        user.notify_email = "notify_email" in request.POST
        user.save(update_fields=["first_name", "last_name", "phone_number", "notify_in_app", "notify_email"])

        # HTMX: return a success fragment; fall back to redirect for non-HTMX
        if request.headers.get("HX-Request"):
            return HttpResponse(
                '<div class="alert alert-success alert-dismissible fade show">'
                '<i class="bi bi-check-circle-fill me-2"></i>Profile updated successfully.'
                '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>'
                '</div>'
            )
        return redirect("accounts:profile")


# ---------------------------------------------------------------------------
# Availability (staff)
# ---------------------------------------------------------------------------

@method_decorator(login_required(login_url="/accounts/login/"), name="dispatch")
class AvailabilityView(View):
    """
    Staff members manage their weekly recurring and one-off availability windows.

    GET  → renders the availability calendar grid.
    POST → saves a new or updated window; returns HTMX fragment or redirects.
    """

    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the availability management page."""
        user = request.user
        weekly = StaffAvailability.objects.filter(
            user=user, recurrence=StaffAvailability.Recurrence.WEEKLY
        ).order_by("day_of_week")

        one_offs = StaffAvailability.objects.filter(
            user=user, recurrence=StaffAvailability.Recurrence.ONE_OFF
        ).order_by("specific_date")

        # Build a dict keyed by weekday index for the template grid
        weekly_by_day = {w.day_of_week: w for w in weekly}

        return render(request, "accounts/availability.html", {
            "weekly_by_day": weekly_by_day,
            "one_offs": one_offs,
            "days": list(enumerate(self.DAYS)),
            "timezones": self._common_timezones(),
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        """
        Save a weekly or one-off availability window.

        Expects POST fields:
          recurrence: 'weekly' | 'one_off'
          day_of_week: 0-6 (weekly only)
          specific_date: YYYY-MM-DD (one_off only)
          start_time, end_time: HH:MM  (blank both = mark unavailable)
          timezone: IANA timezone string
          delete: 'true' to remove the window
        """
        user = request.user
        recurrence = request.POST.get("recurrence")
        tz = request.POST.get("timezone", "UTC")
        delete = request.POST.get("delete") == "true"

        if recurrence == StaffAvailability.Recurrence.WEEKLY:
            day = int(request.POST.get("day_of_week", 0))
            qs = StaffAvailability.objects.filter(
                user=user, recurrence=recurrence, day_of_week=day
            )
        else:
            specific_date = request.POST.get("specific_date")
            qs = StaffAvailability.objects.filter(
                user=user, recurrence=recurrence, specific_date=specific_date
            )

        if delete:
            qs.delete()
        else:
            start_raw = request.POST.get("start_time")
            end_raw = request.POST.get("end_time")
            start_time = self._parse_time(start_raw)
            end_time = self._parse_time(end_raw)

            defaults = {"start_time": start_time, "end_time": end_time, "timezone": tz}
            if recurrence == StaffAvailability.Recurrence.WEEKLY:
                StaffAvailability.objects.update_or_create(
                    user=user, recurrence=recurrence, day_of_week=day, defaults=defaults
                )
            else:
                StaffAvailability.objects.update_or_create(
                    user=user, recurrence=recurrence, specific_date=specific_date, defaults=defaults
                )

        if request.headers.get("HX-Request"):
            return HttpResponse(
                '<div class="alert alert-success alert-dismissible fade show mt-2 mb-0">'
                '<i class="bi bi-check-circle-fill me-2"></i>Availability saved.'
                '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>'
                '</div>'
            )
        return redirect("accounts:availability")

    @staticmethod
    def _parse_time(raw: str):
        """
        Parse a time string from a form input.

        Args:
            raw: String in HH:MM format or empty.

        Returns:
            datetime.time instance or None if blank.
        """
        from datetime import time
        try:
            h, m = map(int, raw.split(":"))
            return time(h, m)
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _common_timezones() -> list[str]:
        """Return a curated list of common US timezones for the select widget."""
        return [
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Los_Angeles",
            "America/Phoenix",
            "America/Anchorage",
            "Pacific/Honolulu",
            "UTC",
        ]


# ---------------------------------------------------------------------------
# Staff list (manager/admin)
# ---------------------------------------------------------------------------

class StaffListView(ManagerRequiredMixin, View):
    """
    Browse all staff members with their skills, certifications, and weekly hours.

    Managers see only staff certified at their locations.
    Admins see all staff.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render the staff list with current-week hours and location certifications.

        Args:
            request: Authenticated GET request.
        """
        now = timezone.now()
        week_start = now - __import__("datetime").timedelta(days=now.weekday())
        week_end = week_start + __import__("datetime").timedelta(days=7)

        user = request.user
        if user.role == User.Role.ADMIN:
            staff_qs = User.objects.filter(role=User.Role.STAFF, is_active=True)
        else:
            certified_ids = LocationCertification.objects.filter(
                location__in=user.managed_locations.all(), is_active=True
            ).values_list("user_id", flat=True).distinct()
            staff_qs = User.objects.filter(pk__in=certified_ids, is_active=True)

        staff_qs = staff_qs.prefetch_related("skills", "location_certifications__location")

        staff_data = []
        for member in staff_qs:
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=member,
                    status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.SWAP_PENDING],
                    shift__start_utc__gte=week_start,
                    shift__start_utc__lt=week_end,
                ).select_related("shift")
            )
            staff_data.append({"user": member, "hours_this_week": hours})

        staff_data.sort(key=lambda x: x["user"].last_name)

        return render(request, "accounts/staff_list.html", {"staff_data": staff_data})