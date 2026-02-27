"""
Locations views for ShiftSync.

View inventory:
  LocationListView   → admin: all locations with coverage stats (GET)
  LocationDetailView → admin/manager: single location + roster + certifications (GET)
  CertificationView  → admin/manager: grant or revoke staff certification (POST)
"""

import logging
from datetime import timedelta

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.accounts.models import User
from apps.locations.models import Location, LocationCertification
from apps.scheduling.models import Shift, ShiftAssignment
from core.permissions import AdminRequiredMixin, ManagerRequiredMixin

logger = logging.getLogger(__name__)


class LocationListView(AdminRequiredMixin, View):
    """
    Platform-wide location list (admin only).

    Shows all active locations with manager assignments, certified staff
    counts, and current-week shift coverage percentage.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render the location overview table.

        Args:
            request: Authenticated admin GET request.

        Returns:
            Rendered locations/list.html.
        """
        now = timezone.now()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start -= timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)

        locations = Location.objects.filter(is_active=True).prefetch_related("managers")

        location_data = []
        for loc in locations:
            shifts = Shift.objects.filter(
                location=loc, start_utc__gte=week_start, start_utc__lt=week_end
            )
            total_headcount = sum(s.headcount_needed for s in shifts)
            filled = sum(s.assigned_count for s in shifts)
            coverage_pct = int((filled / total_headcount) * 100) if total_headcount else 100

            location_data.append({
                "location": loc,
                "shift_count": shifts.count(),
                "staff_count": LocationCertification.objects.filter(
                    location=loc, is_active=True
                ).count(),
                "coverage_pct": coverage_pct,
            })

        return render(request, "locations/list.html", {"location_data": location_data})


class LocationDetailView(ManagerRequiredMixin, View):
    """
    Single location detail page.

    Managers can only view their assigned locations. Admins can view any.
    Shows the certified staff roster, upcoming shifts, and certification controls.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        """
        Render the location detail page.

        Args:
            request: Authenticated GET request.
            pk:      Location primary key.

        Returns:
            Rendered locations/detail.html, or 403 if manager lacks access.
        """
        location = self.get_location_or_403(pk)

        now = timezone.now()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start -= timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=7)

        # Active certified staff with current-week hours
        certifications = LocationCertification.objects.filter(
            location=location, is_active=True
        ).select_related("user").prefetch_related("user__skills")

        staff_data = []
        for cert in certifications:
            hours = sum(
                a.shift.duration_hours
                for a in ShiftAssignment.objects.filter(
                    user=cert.user,
                    shift__location=location,
                    shift__start_utc__gte=week_start,
                    shift__start_utc__lt=week_end,
                    status__in=[
                        ShiftAssignment.Status.ASSIGNED,
                        ShiftAssignment.Status.SWAP_PENDING,
                    ],
                ).select_related("shift")
            )
            staff_data.append({"cert": cert, "user": cert.user, "hours_this_week": hours})

        # Published shifts at this location for the current week
        upcoming_shifts = Shift.objects.filter(
            location=location,
            start_utc__gte=now,
            start_utc__lt=week_end,
            is_published=True,
        ).select_related("required_skill").prefetch_related(
            "assignments__user"
        ).order_by("start_utc")

        # Staff not yet certified here (for the grant-certification form)
        already_certified_ids = LocationCertification.objects.filter(
            location=location
        ).values_list("user_id", flat=True)

        certifiable_staff = User.objects.filter(
            role=User.Role.STAFF, is_active=True
        ).exclude(pk__in=already_certified_ids)

        return render(request, "locations/detail.html", {
            "location": location,
            "staff_data": staff_data,
            "upcoming_shifts": upcoming_shifts,
            "certifiable_staff": certifiable_staff,
        })


class CertificationView(ManagerRequiredMixin, View):
    """
    Grant or revoke a staff member's certification at a location.

    POST actions:
      grant  → create/reactivate a LocationCertification
      revoke → deactivate an existing certification (history preserved)
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """
        Process grant or revoke action.

        Args:
            request: POST with 'action' ('grant'|'revoke'), 'user_id', 'reason'.
            pk:      Location primary key.

        Returns:
            HTMX success fragment or redirect for non-HTMX POST.
        """
        location = self.get_location_or_403(pk)
        action = request.POST.get("action")
        staff_member = get_object_or_404(
            User, pk=request.POST.get("user_id"), role=User.Role.STAFF
        )
        reason = request.POST.get("reason", "")

        if action == "grant":
            cert, created = LocationCertification.objects.get_or_create(
                user=staff_member, location=location,
                defaults={"is_active": True, "certified_by": request.user},
            )
            if not created and not cert.is_active:
                cert.is_active = True
                cert.certified_by = request.user
                cert.deactivated_at = None
                cert.deactivated_reason = ""
                cert.save()
            msg = f"{staff_member.get_full_name()} certified at {location.name}."
            logger.info("Manager %d granted cert: user %d @ location %d",
                        request.user.pk, staff_member.pk, location.pk)

        elif action == "revoke":
            cert = get_object_or_404(LocationCertification, user=staff_member, location=location)
            cert.deactivate(reason=reason)
            msg = f"Certification revoked for {staff_member.get_full_name()}."
            logger.info("Manager %d revoked cert: user %d @ location %d",
                        request.user.pk, staff_member.pk, location.pk)
        else:
            msg = "Unknown action."

        if request.headers.get("HX-Request"):
            return HttpResponse(
                f'<div class="alert alert-success alert-dismissible fade show mb-0">'
                f'<i class="bi bi-check-circle-fill me-2"></i>{msg}'
                f'<button type="button" class="btn-close" data-bs-dismiss="alert"></button>'
                f'</div>'
            )
        return redirect("locations:detail", pk=pk)