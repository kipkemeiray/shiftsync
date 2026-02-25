"""
Locations models for ShiftSync.

Represents restaurant locations and staff certification to work at each.
Each location has a canonical timezone — all shift display for that location
uses this timezone regardless of where the viewer is located.
"""

import zoneinfo

from django.conf import settings
from django.db import models
from django.utils import timezone


# All valid IANA timezone names for the select widget
TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]


class Location(models.Model):
    """
    A physical restaurant location operated by Coastal Eats.

    The timezone field is the canonical timezone for all shift times displayed
    for this location. It is IANA-format (e.g., "America/Los_Angeles").

    When two managers at different locations share a staff member, each manager
    sees that staff member's shift times in their own location's timezone.
    """

    name = models.CharField(max_length=100, unique=True)
    timezone = models.CharField(
        max_length=50,
        choices=TIMEZONE_CHOICES,
        help_text="IANA timezone for this location (e.g., America/Los_Angeles).",
    )
    address = models.TextField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    # Managers assigned to oversee this location
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_locations",
        limit_choices_to={"role": "manager"},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Location"
        verbose_name_plural = "Locations"

    def __str__(self) -> str:
        """Return location name with timezone for unambiguous display."""
        return f"{self.name} ({self.timezone})"

    def get_zoneinfo(self) -> zoneinfo.ZoneInfo:
        """
        Return a ZoneInfo object for this location's timezone.

        Returns:
            ZoneInfo instance for the location's IANA timezone string.
        """
        return zoneinfo.ZoneInfo(self.timezone)

    def now_local(self):
        """
        Return the current datetime in this location's local timezone.

        Returns:
            timezone-aware datetime in the location's local time.
        """
        return timezone.now().astimezone(self.get_zoneinfo())


class LocationCertification(models.Model):
    """
    Records that a staff member is certified (approved) to work at a location.

    Design decision (from ambiguity resolution):
      When a certification is deactivated (is_active=False), past shift assignments
      are preserved. Only new assignments are blocked. This ensures payroll and
      audit integrity.

    The certified_by field records who approved the certification, which is
    important for accountability in multi-location contexts.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="location_certifications",
        limit_choices_to={"role": "staff"},
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="certified_staff",
    )
    certified_at = models.DateTimeField(default=timezone.now)
    certified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="certifications_granted",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive = staff cannot be assigned NEW shifts here; historical data preserved.",
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_reason = models.TextField(blank=True)

    class Meta:
        verbose_name = "Location Certification"
        verbose_name_plural = "Location Certifications"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "location"],
                name="unique_user_location_certification",
            )
        ]
        ordering = ["location__name", "user__last_name"]

    def __str__(self) -> str:
        """Return a description of this certification."""
        status = "Active" if self.is_active else "Inactive"
        return f"{self.user.get_full_name()} @ {self.location.name} [{status}]"

    def deactivate(self, reason: str = "") -> None:
        """
        Deactivate this certification, preventing future assignments.

        Args:
            reason: Optional human-readable reason for deactivation.
        """
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_reason = reason
        self.save(update_fields=["is_active", "deactivated_at", "deactivated_reason"])