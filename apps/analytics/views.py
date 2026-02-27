"""
Analytics views for ShiftSync.

View inventory:
  AnalyticsOverviewView → admin/manager fairness + overtime summary
"""

import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views import View

from apps.accounts.models import User
from apps.locations.models import Location, LocationCertification
from apps.scheduling.models import Shift, ShiftAssignment
from core.permissions import ManagerRequiredMixin

logger = logging.getLogger(__name__)


class AnalyticsOverviewView(ManagerRequiredMixin, View):
    """
    Fairness and overtime analytics dashboard.

    Admins see all locations. Managers see their own.
    Shows premium shift distribution and weekly hour totals per staff member.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render analytics overview with fairness scores and overtime data.

        Args:
            request: Authenticated GET request.
        """
        user = request.user
        now = timezone.now()

        # Date range: default last 4 weeks
        range_weeks = int(request.GET.get("weeks", 4))
        period_start = now - timedelta(weeks=range_weeks)

        if user.role == User.Role.ADMIN:
            locations = Location.objects.filter(is_active=True)
        else:
            locations = user.managed_locations.filter(is_active=True)

        staff_ids = LocationCertification.objects.filter(
            location__in=locations, is_active=True
        ).values_list("user_id", flat=True).distinct()
        staff = User.objects.filter(pk__in=staff_ids).prefetch_related("skills")

        # Total and premium shift counts per staff member
        analytics_data = []
        total_premium = Shift.objects.filter(
            location__in=locations,
            is_published=True,
            start_utc__gte=period_start,
        ).count()

        for member in staff:
            assignments = ShiftAssignment.objects.filter(
                user=member,
                shift__location__in=locations,
                shift__start_utc__gte=period_start,
                status__in=[ShiftAssignment.Status.ASSIGNED, ShiftAssignment.Status.COVERED],
            ).select_related("shift")

            total_hours = sum(a.shift.duration_hours for a in assignments)
            premium_count = sum(1 for a in assignments if a.shift.is_premium)

            analytics_data.append({
                "user": member,
                "total_hours": round(total_hours, 1),
                "total_shifts": assignments.count(),
                "premium_shifts": premium_count,
                "desired_hours_period": member.desired_hours_per_week * range_weeks,
            })

        # Sort by hours descending
        analytics_data.sort(key=lambda x: x["total_hours"], reverse=True)

        # Fair share of premium shifts = total_premium / len(staff)
        fair_share = round(total_premium / len(analytics_data), 1) if analytics_data else 0

        return render(request, "analytics/overview.html", {
            "analytics_data": analytics_data,
            "period_start": period_start.date(),
            "range_weeks": range_weeks,
            "total_premium": total_premium,
            "fair_share": fair_share,
            "locations": locations,
        })