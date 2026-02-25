"""
Accounts models for ShiftSync.

Defines the custom User model, skills system, and staff availability windows.
The User model uses email as the unique identifier (no username).

Key design decisions:
  - AbstractBaseUser gives us full control over the user model
  - Role is a simple enum field; permissions derived from role in views/mixins
  - Availability is stored with an explicit timezone so DST-safe conversion is possible
  - One-off availability overrides take precedence over recurring windows
"""

from zoneinfo import ZoneInfo

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Skill(models.Model):
    """
    A named capability that staff members can possess and shifts can require.

    Examples: bartender, line_cook, server, host, expo
    """

    name = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]
        verbose_name = "Skill"
        verbose_name_plural = "Skills"

    def __str__(self) -> str:
        """Return the human-readable skill name."""
        return self.display_name


class UserManager(BaseUserManager):
    """Custom manager for the ShiftSync User model (email-based auth)."""

    def create_user(self, email: str, password: str, **extra_fields) -> "User":
        """
        Create and save a regular user with the given email and password.

        Args:
            email: The user's email address (used as login identifier).
            password: The raw password (will be hashed).
            **extra_fields: Additional fields to set on the User model.

        Returns:
            The newly created User instance.

        Raises:
            ValueError: If email is not provided.
        """
        if not email:
            raise ValueError(_("The Email field must be set"))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields) -> "User":
        """
        Create and save a superuser (admin) with the given email and password.

        Args:
            email: The admin's email address.
            password: The raw password.
            **extra_fields: Additional fields (is_staff and is_superuser forced to True).

        Returns:
            The newly created admin User instance.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model for ShiftSync.

    Uses email as the unique identifier. Role determines what the user
    can see and do throughout the platform:
      - ADMIN: corporate-level, sees all locations
      - MANAGER: manages one or more specific locations
      - STAFF: works shifts, can swap/drop, sets availability
    """

    class Role(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MANAGER = "manager", _("Manager")
        STAFF = "staff", _("Staff")

    # Core identity
    email = models.EmailField(_("email address"), unique=True)
    first_name = models.CharField(_("first name"), max_length=150)
    last_name = models.CharField(_("last name"), max_length=150)

    # Role & status
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin access

    # Staff-specific fields
    skills = models.ManyToManyField(
        Skill,
        blank=True,
        related_name="staff_members",
        help_text="Skills this staff member is certified to perform.",
    )
    desired_hours_per_week = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Staff's preferred weekly hours. Used for fairness analytics only; "
            "does not constrain scheduling directly."
        ),
    )
    phone_number = models.CharField(max_length=20, blank=True)

    # Notification preferences (stored as simple flags; extend as needed)
    notify_in_app = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=False)

    # Timestamps
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        """Return the user's full name and role for display."""
        return f"{self.get_full_name()} ({self.get_role_display()})"

    def get_full_name(self) -> str:
        """Return the first_name plus the last_name, with a space in between."""
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        """Return the first name for the user."""
        return self.first_name

    @property
    def is_admin(self) -> bool:
        """Check if this user has the Admin role."""
        return self.role == self.Role.ADMIN

    @property
    def is_manager(self) -> bool:
        """Check if this user has the Manager role."""
        return self.role == self.Role.MANAGER

    @property
    def is_staff_member(self) -> bool:
        """Check if this user has the Staff role (avoids collision with is_staff)."""
        return self.role == self.Role.STAFF

    def has_skill(self, skill: "Skill") -> bool:
        """
        Check if this user possesses the given skill.

        Args:
            skill: The Skill instance to check for.

        Returns:
            True if the user has this skill, False otherwise.
        """
        return self.skills.filter(pk=skill.pk).exists()


class StaffAvailability(models.Model):
    """
    Represents a window of time when a staff member is available to work.

    Two recurrence types are supported:
      - WEEKLY: Repeats every week on the same day (e.g., every Monday 9am-5pm)
      - ONE_OFF: A single specific date override (e.g., available Dec 25th 10am-2pm)

    Timezone handling:
      The availability is stored with an explicit timezone string. When the
      constraint engine checks availability, it converts both the shift time
      (stored as UTC) and the availability window to UTC for comparison.

    One-off entries take precedence over weekly entries for the same day.
    An entry with start_time == end_time == None means the staff member is
    UNAVAILABLE for that entire day (useful for one-off unavailability).
    """

    class Recurrence(models.TextChoices):
        WEEKLY = "weekly", _("Weekly (recurring)")
        ONE_OFF = "one_off", _("One-off (specific date)")

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="availability_windows",
        limit_choices_to={"role": User.Role.STAFF},
    )
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices)

    # For WEEKLY recurrence: 0=Monday, 6=Sunday (Python weekday() convention)
    day_of_week = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="0=Monday, 6=Sunday. Only for weekly recurrence."
    )

    # For ONE_OFF recurrence
    specific_date = models.DateField(null=True, blank=True)

    # Null times mean unavailable for that day
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # The timezone the staff member used when entering these times
    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="Timezone in which start_time/end_time were entered (e.g., America/Los_Angeles).",
    )

    notes = models.TextField(blank=True, help_text="Optional note (e.g., 'School pickup at 3pm')")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Staff Availability"
        verbose_name_plural = "Staff Availability Windows"
        ordering = ["recurrence", "day_of_week", "specific_date"]
        # Prevent duplicate weekly entries for the same user+day
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recurrence", "day_of_week"],
                condition=models.Q(recurrence="weekly"),
                name="unique_weekly_availability_per_day",
            ),
            models.UniqueConstraint(
                fields=["user", "recurrence", "specific_date"],
                condition=models.Q(recurrence="one_off"),
                name="unique_one_off_availability_per_date",
            ),
        ]

    def __str__(self) -> str:
        """Return a human-readable description of this availability window."""
        if self.recurrence == self.Recurrence.WEEKLY:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            day_str = days[self.day_of_week] if self.day_of_week is not None else "?"
        else:
            day_str = str(self.specific_date)

        if self.start_time and self.end_time:
            time_str = f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')} {self.timezone}"
        else:
            time_str = "Unavailable"

        return f"{self.user.get_short_name()} | {day_str} | {time_str}"

    @property
    def is_unavailable_day(self) -> bool:
        """Return True if this entry marks the person as fully unavailable for the day."""
        return self.start_time is None and self.end_time is None