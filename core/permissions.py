"""
Role-based permission mixins for ShiftSync views.

Every view that handles sensitive data should use one of these mixins.
They build on Django's LoginRequiredMixin and add role checks.

Manager views additionally scope querysets to the manager's assigned locations
via the get_manager_locations() helper, preventing cross-location data leakage.

Usage:
    class MyView(ManagerRequiredMixin, ListView):
        def get_queryset(self):
            # Only shifts at this manager's locations
            return Shift.objects.filter(location__in=self.get_manager_locations())
"""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class RoleRequiredMixin(LoginRequiredMixin):
    """
    Base mixin that enforces a specific user role.

    Subclasses set `required_role` to the role string(s) to allow.
    """

    required_roles: list[str] = []

    def dispatch(self, request: HttpRequest, *args, **kwargs):
        """
        Check authentication and role before dispatching.

        Args:
            request: The incoming HTTP request.

        Raises:
            PermissionDenied: If the user doesn't have the required role.
        """
        # LoginRequiredMixin handles the unauthenticated case
        response = super().dispatch(request, *args, **kwargs)
        if not request.user.is_authenticated:
            return response

        if self.required_roles and request.user.role not in self.required_roles:
            logger.warning(
                "User %d (role=%s) attempted to access %s which requires role in %s.",
                request.user.pk,
                request.user.role,
                request.path,
                self.required_roles,
            )
            raise PermissionDenied("You don't have permission to access this page.")

        return response


class AdminRequiredMixin(RoleRequiredMixin):
    """Restrict access to Admin users only."""

    required_roles = ["admin"]


class ManagerRequiredMixin(RoleRequiredMixin):
    """Restrict access to Manager (and Admin) users."""

    required_roles = ["admin", "manager"]

    def get_manager_locations(self):
        """
        Return the queryset of locations this user can manage.

        Admins see all locations. Managers see only their assigned locations.

        Returns:
            QuerySet of Location instances.
        """
        from apps.locations.models import Location

        user = self.request.user
        if user.is_admin:
            return Location.objects.filter(is_active=True)
        return user.managed_locations.filter(is_active=True)

    def get_location_or_403(self, location_id: int):
        """
        Return a location the current manager has access to, or raise 403.

        Args:
            location_id: The PK of the requested location.

        Returns:
            Location instance.

        Raises:
            PermissionDenied: If the manager doesn't manage this location.
        """
        location = self.get_manager_locations().filter(pk=location_id).first()
        if not location:
            raise PermissionDenied("You don't manage this location.")
        return location


class StaffRequiredMixin(RoleRequiredMixin):
    """Restrict access to Staff users (all roles can access staff views)."""

    required_roles = ["admin", "manager", "staff"]