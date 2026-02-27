"""
Seed ShiftSync with 4 weeks of realistic Coastal Eats data.

Smoke-test scenarios included:
  1. Fully-staffed published shifts (happy path)
  2. Understaffed shifts (coverage gap visible on manager dashboard)
  3. Premium Fri/Sat evening shifts with deliberate fairness imbalance
  4. Bob overtime trap: 38h Mon-Fri, shift on Saturday would breach 40h limit
  5. Pending swap request (Alice ↔ Henry, awaiting manager approval)
  6. Pending drop request (Carol wants to drop a shift)
  7. Open/unclaimed shifts (staff dashboard "pick up" section)
  8. Past shifts for analytics history
  9. Multi-location staff (Eve certified at all 4 locations)
  10. Staff with limited weekday-only availability (Henry, Frank)

Usage:
    python manage.py seed_data
    python manage.py seed_data --reset
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone


PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")


def make_dt(base_monday: date, day_offset: int, hour: int, minute: int = 0, tz=PT) -> datetime:
    """Return a timezone-aware datetime relative to a Monday."""
    d = base_monday + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)


class Command(BaseCommand):
    help = "Seed ShiftSync with 4 weeks of Coastal Eats demo data"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete all existing data first (DESTRUCTIVE).")

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write(self.style.WARNING("⚠  Resetting all data..."))
            self._reset_data()

        self.stdout.write("🌱 Seeding ShiftSync demo data (4 weeks)...")

        skills    = self._create_skills()
        locations = self._create_locations()
        _admin    = self._create_admin()
        managers  = self._create_managers(locations)
        staff     = self._create_staff(skills, locations)
        self._create_schedule(staff, skills, locations)
        self._create_swap_requests(staff)

        self.stdout.write(self.style.SUCCESS("\n✅ Seed complete!\n"))
        self.stdout.write("=" * 55)
        self.stdout.write("ADMIN:    admin@coastaleats.com / ShiftSync2026!")
        self.stdout.write("MANAGERS: mgr.westside@coastaleats.com / ShiftSync2026!")
        self.stdout.write("          mgr.eastcoast@coastaleats.com / ShiftSync2026!")
        self.stdout.write("STAFF:    alice@coastaleats.com / ShiftSync2026!")
        self.stdout.write("          bob, carol, david, eve, frank, grace, henry")
        self.stdout.write("          (all @coastaleats.com / ShiftSync2026!)")
        self.stdout.write("=" * 55)

    # ------------------------------------------------------------------
    def _reset_data(self):
        from apps.audit.models import AuditLog
        from apps.notifications.models import Notification
        from apps.scheduling.models import ManagerOverride, Shift, ShiftAssignment, SwapRequest
        from apps.accounts.models import StaffAvailability, User
        from apps.locations.models import Location, LocationCertification

        for model in [AuditLog, Notification, ManagerOverride, SwapRequest,
                      ShiftAssignment, Shift, LocationCertification,
                      StaffAvailability, Location]:
            model.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        self.stdout.write(self.style.WARNING("  Cleared existing data."))

    # ------------------------------------------------------------------
    def _create_skills(self) -> dict:
        from apps.accounts.models import Skill
        skills = {}
        for name, display in [
            ("bartender", "Bartender"),
            ("server",    "Server"),
            ("line_cook", "Line Cook"),
            ("host",      "Host / Hostess"),
            ("expo",      "Expeditor"),
            ("busser",    "Busser"),
        ]:
            obj, created = Skill.objects.get_or_create(name=name, defaults={"display_name": display})
            skills[name] = obj
            if created:
                self.stdout.write(f"  ✓ Skill: {display}")
        return skills

    # ------------------------------------------------------------------
    def _create_locations(self) -> dict:
        from apps.locations.models import Location
        locations = {}
        for key, name, tz, addr in [
            ("westside", "Westside Bar & Grill",  "America/Los_Angeles", "1200 Ocean Ave, Santa Monica CA"),
            ("marina",   "Marina Seafood",         "America/Los_Angeles", "450 Admiralty Way, Marina del Rey CA"),
            ("downtown", "Downtown Coastal",       "America/New_York",    "350 5th Ave, New York NY"),
            ("harbor",   "Harbor House",           "America/New_York",    "1 Ferry Building, Manhattan NY"),
        ]:
            obj, created = Location.objects.get_or_create(
                name=name, defaults={"timezone": tz, "address": addr}
            )
            locations[key] = obj
            if created:
                self.stdout.write(f"  ✓ Location: {name}")
        return locations

    # ------------------------------------------------------------------
    def _create_admin(self):
        from apps.accounts.models import User
        admin, created = User.objects.get_or_create(
            email="admin@coastaleats.com",
            defaults={"first_name": "Corporate", "last_name": "Admin",
                      "role": User.Role.ADMIN, "is_staff": True},
        )
        if created:
            admin.set_password("ShiftSync2026!")
            admin.save()
            self.stdout.write("  ✓ Admin: admin@coastaleats.com")
        return admin

    # ------------------------------------------------------------------
    def _create_managers(self, locations: dict) -> dict:
        from apps.accounts.models import User
        managers = {}

        for email, first, last, locs, key in [
            ("mgr.westside@coastaleats.com",  "Jennifer", "Park",     ["westside", "marina"], "west"),
            ("mgr.eastcoast@coastaleats.com", "Marcus",   "Thompson", ["downtown", "harbor"], "east"),
        ]:
            obj, created = User.objects.get_or_create(
                email=email,
                defaults={"first_name": first, "last_name": last, "role": User.Role.MANAGER},
            )
            if created:
                obj.set_password("ShiftSync2026!")
                obj.save()
                self.stdout.write(f"  ✓ Manager: {first} {last}")
            obj.managed_locations.set([locations[k] for k in locs])
            managers[key] = obj

        return managers

    # ------------------------------------------------------------------
    def _create_staff(self, skills: dict, locations: dict) -> dict:
        from apps.accounts.models import Skill, StaffAvailability, User
        from apps.locations.models import LocationCertification

        staff_specs = [
            {
                "email": "alice@coastaleats.com",
                "first": "Alice", "last": "Chen",
                "skills": ["bartender", "server"],
                "locs":   ["westside", "marina"],
                "hours":  30,
                "avail":  [(i, "17:00", "23:00", "America/Los_Angeles") for i in range(5)]
                         + [(5, "14:00", "23:00", "America/Los_Angeles"),
                            (6, "14:00", "23:00", "America/Los_Angeles")],
            },
            {
                "email": "bob@coastaleats.com",
                "first": "Bob", "last": "Martinez",
                "skills": ["line_cook"],
                "locs":   ["westside"],
                "hours":  40,
                # Scenario: Bob has no Sunday availability — gaps appear on coverage report
                "avail":  [(i, "10:00", "22:00", "America/Los_Angeles") for i in range(6)],
            },
            {
                "email": "carol@coastaleats.com",
                "first": "Carol", "last": "Johnson",
                "skills": ["server", "host"],
                "locs":   ["downtown", "harbor"],
                "hours":  25,
                "avail":  [(i, "16:00", "23:00", "America/New_York") for i in range(7)],
            },
            {
                "email": "david@coastaleats.com",
                "first": "David", "last": "Kim",
                "skills": ["bartender"],
                "locs":   ["downtown"],
                "hours":  35,
                "avail":  [(i, "15:00", "23:00", "America/New_York") for i in range(5)]
                         + [(5, "12:00", "23:00", "America/New_York")],
            },
            {
                "email": "eve@coastaleats.com",
                "first": "Eve", "last": "Rodriguez",
                # Multi-location, multi-skill: she is the swing worker
                "skills": ["line_cook", "server"],
                "locs":   ["westside", "marina", "downtown", "harbor"],
                "hours":  40,
                "avail":  [(i, "08:00", "22:00", "America/Los_Angeles") for i in range(7)],
            },
            {
                "email": "frank@coastaleats.com",
                "first": "Frank", "last": "Williams",
                "skills": ["host", "busser"],
                "locs":   ["marina"],
                "hours":  20,
                # Scenario: Frank only weekends — forces understaffing Mon-Thu
                "avail":  [(4, "17:00", "23:00", "America/Los_Angeles"),
                           (5, "12:00", "23:00", "America/Los_Angeles"),
                           (6, "12:00", "22:00", "America/Los_Angeles")],
            },
            {
                "email": "grace@coastaleats.com",
                "first": "Grace", "last": "Lee",
                "skills": ["server", "expo"],
                "locs":   ["harbor", "downtown"],
                "hours":  32,
                "avail":  [(i, "11:00", "21:00", "America/New_York") for i in range(5)]
                         + [(5, "11:00", "23:00", "America/New_York")],
            },
            {
                "email": "henry@coastaleats.com",
                "first": "Henry", "last": "Davis",
                "skills": ["bartender", "expo"],
                "locs":   ["westside", "downtown"],
                "hours":  28,
                # Scenario: Henry only Wed-Sat
                "avail":  [(2, "18:00", "23:00", "America/Los_Angeles"),
                           (3, "18:00", "23:00", "America/Los_Angeles"),
                           (4, "18:00", "23:00", "America/Los_Angeles"),
                           (5, "12:00", "23:00", "America/Los_Angeles")],
            },
        ]

        staff = {}
        for spec in staff_specs:
            user, created = User.objects.get_or_create(
                email=spec["email"],
                defaults={
                    "first_name": spec["first"],
                    "last_name":  spec["last"],
                    "role":       User.Role.STAFF,
                    "desired_hours_per_week": spec["hours"],
                },
            )
            if created:
                user.set_password("ShiftSync2026!")
                user.save()
                self.stdout.write(f"  ✓ Staff: {spec['first']} {spec['last']}")

            user.skills.set([skills[s] for s in spec["skills"]])

            for loc_key in spec["locs"]:
                LocationCertification.objects.get_or_create(
                    user=user, location=locations[loc_key],
                    defaults={"is_active": True},
                )

            for day, start, end, tz_str in spec["avail"]:
                h_s, m_s = map(int, start.split(":"))
                h_e, m_e = map(int, end.split(":"))
                StaffAvailability.objects.get_or_create(
                    user=user,
                    recurrence=StaffAvailability.Recurrence.WEEKLY,
                    day_of_week=day,
                    defaults={
                        "start_time": time(h_s, m_s),
                        "end_time":   time(h_e, m_e),
                        "timezone":   tz_str,
                    },
                )

            staff[spec["first"].lower()] = user

        return staff

    # ------------------------------------------------------------------
    def _create_schedule(self, staff: dict, skills: dict, locations: dict):
        """
        Create 4 weeks of shifts: 2 past (analytics history) + 2 upcoming.

        Scenarios embedded:
          - Fully staffed (happy path)
          - Understaffed shifts (manager coverage alert)
          - Premium Fri/Sat nights (Alice gets most — fairness imbalance)
          - Overtime trap: Bob at 38h by Friday of current week
          - Open unclaimed drop shifts (staff dashboard pickup section)
          - Draft shifts not yet published (manager publish workflow)
        """
        from apps.scheduling.models import Shift, ShiftAssignment

        self.stdout.write("\n  Creating 4-week schedule...")

        now = timezone.now()
        today = now.date()
        this_monday = today - timedelta(days=today.weekday())

        # Build 4 week starting points: 2 past, 2 future
        weeks = [
            this_monday - timedelta(weeks=2),  # 2 weeks ago   (history)
            this_monday - timedelta(weeks=1),  # last week     (history)
            this_monday,                        # this week     (current)
            this_monday + timedelta(weeks=1),  # next week     (upcoming)
        ]

        for week_idx, monday in enumerate(weeks):
            is_past   = week_idx < 2
            is_future = week_idx == 3
            self._seed_week(
                monday, staff, skills, locations,
                is_past=is_past, is_future=is_future,
            )
            label = ["2 weeks ago", "last week", "this week", "next week"][week_idx]
            self.stdout.write(f"  ✓ Week seeded: {label} (Mon {monday})")

    def _seed_week(self, monday: date, staff: dict, skills: dict,
                   locations: dict, is_past: bool, is_future: bool):
        """Seed one full week of shifts across all 4 locations."""
        from apps.scheduling.models import Shift, ShiftAssignment

        def pt(d, h, m=0): return make_dt(monday, d, h, m, PT)
        def et(d, h, m=0): return make_dt(monday, d, h, m, ET)

        # Published for past/current, draft for future week
        # (demonstrates manager "publish week" workflow)
        publish_future = not is_future

        # ── WESTSIDE (PT) ────────────────────────────────────────────
        # Lunch service Mon-Fri: 2 servers needed
        for d in range(5):
            shift = self._get_or_create_shift(
                locations["westside"], skills["server"],
                pt(d, 11), pt(d, 15),
                headcount=2, published=publish_future
            )
            # Assign Alice + Eve (both certified, both servers)
            self._assign(shift, staff["alice"])
            self._assign(shift, staff["eve"])

        # Dinner service Mon-Sun: 1 bartender
        for d in range(7):
            is_premium_day = d in (4, 5)  # Friday, Saturday
            shift = self._get_or_create_shift(
                locations["westside"], skills["bartender"],
                pt(d, 17), pt(d, 23),
                headcount=1, published=publish_future
            )
            # SCENARIO: Alice gets ALL premium bartender shifts (fairness imbalance)
            self._assign(shift, staff["alice"])

        # Line cook Mon-Fri — OVERTIME TRAP for Bob (this week only)
        from datetime import datetime as dt_cls
        import pytz
        now_date = timezone.now().date()
        this_mon = now_date - timedelta(days=now_date.weekday())

        for d in range(5):
            # 7h36m = 7.6h → 5 × 7.6 = 38h (triggers overtime warning before weekend)
            shift = self._get_or_create_shift(
                locations["westside"], skills["line_cook"],
                pt(d, 10), pt(d, 17, 36),
                headcount=1, published=publish_future
            )
            self._assign(shift, staff["bob"])

        # Weekend line cook (Saturday) — intentionally UNDERSTAFFED
        # Bob has no Sunday avail, nobody else is assigned → coverage gap
        sat_cook = self._get_or_create_shift(
            locations["westside"], skills["line_cook"],
            pt(5, 11), pt(5, 19),
            headcount=2, published=publish_future
        )
        # Only assign Eve — 1/2 filled → understaffed
        self._assign(sat_cook, staff["eve"])

        # ── MARINA (PT) ──────────────────────────────────────────────
        # Dinner Fri-Sun only (Marina is a weekend venue)
        for d in (4, 5, 6):
            host_shift = self._get_or_create_shift(
                locations["marina"], skills["host"],
                pt(d, 17), pt(d, 22),
                headcount=1, published=publish_future
            )
            # Frank covers Fri-Sat; Sunday open/unclaimed
            if d in (4, 5):
                self._assign(host_shift, staff["frank"])
            # d==6 (Sunday) left unassigned → "open shift" for staff to claim

        # Server shifts Fri-Sun
        for d in (4, 5, 6):
            srv_shift = self._get_or_create_shift(
                locations["marina"], skills["server"],
                pt(d, 18), pt(d, 23),
                headcount=2, published=publish_future
            )
            self._assign(srv_shift, staff["alice"])
            # Second slot open → understaffed

        # ── DOWNTOWN (ET) ────────────────────────────────────────────
        # Dinner Mon-Fri: 2 servers needed
        for d in range(5):
            shift = self._get_or_create_shift(
                locations["downtown"], skills["server"],
                et(d, 17), et(d, 22),
                headcount=2, published=publish_future
            )
            self._assign(shift, staff["carol"])
            self._assign(shift, staff["grace"])

        # Bartender Mon-Fri: 1 needed
        for d in range(5):
            bar_shift = self._get_or_create_shift(
                locations["downtown"], skills["bartender"],
                et(d, 16), et(d, 23),
                headcount=1, published=publish_future
            )
            self._assign(bar_shift, staff["david"])

        # Weekend bartender — David unavailable Sunday; Henry fills Sat
        sat_bar = self._get_or_create_shift(
            locations["downtown"], skills["bartender"],
            et(5, 15), et(5, 23),
            headcount=1, published=publish_future
        )
        self._assign(sat_bar, staff["henry"])

        # Sunday downtown bar — OPEN (nobody assigned, staff can claim)
        self._get_or_create_shift(
            locations["downtown"], skills["bartender"],
            et(6, 16), et(6, 22),
            headcount=1, published=publish_future
        )
        # intentionally no assignment → claimable

        # ── HARBOR (ET) ──────────────────────────────────────────────
        # Dinner Tue-Sat
        for d in (1, 2, 3, 4, 5):
            shift = self._get_or_create_shift(
                locations["harbor"], skills["server"],
                et(d, 18), et(d, 23),
                headcount=2, published=publish_future
            )
            self._assign(shift, staff["carol"])
            # Only 1/2 filled Tue-Thu → understaffed (Carol has limited hours)
            if d in (4, 5):
                self._assign(shift, staff["grace"])

        # Expo shift Fri-Sat at Harbor
        for d in (4, 5):
            expo_shift = self._get_or_create_shift(
                locations["harbor"], skills["expo"],
                et(d, 17), et(d, 22),
                headcount=1, published=publish_future
            )
            self._assign(expo_shift, staff["grace"])

        # ── DRAFT SHIFTS (future week only) ──────────────────────────
        # Add a few extra draft shifts next week to demo the publish workflow
        if is_future:
            for d in range(5):
                self._get_or_create_shift(
                    locations["westside"], skills["expo"],
                    pt(d, 17), pt(d, 22),
                    headcount=1, published=False  # draft
                )

    def _get_or_create_shift(self, location, skill, start, end,
                              headcount: int = 1, published: bool = True):
        """Get or create a Shift record."""
        from apps.scheduling.models import Shift
        shift, _ = Shift.objects.get_or_create(
            location=location,
            required_skill=skill,
            start_utc=start,
            defaults={
                "end_utc": end,
                "headcount_needed": headcount,
                "is_published": published,
            },
        )
        return shift

    def _assign(self, shift, user):
        """Assign a user to a shift if not already assigned."""
        from apps.scheduling.models import ShiftAssignment
        ShiftAssignment.objects.get_or_create(
            shift=shift, user=user,
            defaults={"status": ShiftAssignment.Status.ASSIGNED},
        )

    # ------------------------------------------------------------------
    def _create_swap_requests(self, staff: dict):
        """
        Create example swap/drop requests for smoke testing.

        Scenarios:
          1. Pending SWAP: Alice wants to swap with Henry (pending manager approval)
          2. Pending DROP: Carol wants to drop a shift (open for anyone to claim)
          3. Approved swap (historical, shows in analytics)
          4. Rejected swap (shows rejection flow)
        """
        from apps.scheduling.models import ShiftAssignment, SwapRequest

        self.stdout.write("\n  Creating swap/drop scenarios...")

        # --- Scenario 1: Alice ↔ Henry swap, pending manager --------
        alice_assignment = ShiftAssignment.objects.filter(
            user=staff["alice"],
            status=ShiftAssignment.Status.ASSIGNED,
            shift__start_utc__gte=timezone.now(),
        ).select_related("shift").order_by("shift__start_utc").first()

        if alice_assignment:
            alice_assignment.status = ShiftAssignment.Status.SWAP_PENDING
            alice_assignment.save()
            SwapRequest.objects.get_or_create(
                requester=staff["alice"],
                assignment=alice_assignment,
                defaults={
                    "target":              staff["henry"],
                    "request_type":        SwapRequest.Type.SWAP,
                    "status":              SwapRequest.Status.PENDING_MANAGER,
                    "target_accepted_at":  timezone.now() - timedelta(hours=3),
                    "requester_note":      "Henry and I agreed to swap — I have a family thing.",
                },
            )
            self.stdout.write("  ✓ Scenario 1: Alice ↔ Henry swap (pending manager)")

        # --- Scenario 2: Carol drop, pending pickup -----------------
        carol_assignment = ShiftAssignment.objects.filter(
            user=staff["carol"],
            status=ShiftAssignment.Status.ASSIGNED,
            shift__start_utc__gte=timezone.now(),
        ).select_related("shift").order_by("shift__start_utc").first()

        if carol_assignment:
            carol_assignment.status = ShiftAssignment.Status.SWAP_PENDING
            carol_assignment.save()
            SwapRequest.objects.get_or_create(
                requester=staff["carol"],
                assignment=carol_assignment,
                defaults={
                    "request_type":  SwapRequest.Type.DROP,
                    "status":        SwapRequest.Status.PENDING_PICKUP,
                    "requester_note": "Doctor appointment, can someone cover?",
                },
            )
            self.stdout.write("  ✓ Scenario 2: Carol drop request (open for pickup)")

        # --- Scenario 3: David past swap — approved -----------------
        david_assignment = ShiftAssignment.objects.filter(
            user=staff["david"],
            status=ShiftAssignment.Status.ASSIGNED,
            shift__start_utc__lt=timezone.now(),
        ).select_related("shift").order_by("-shift__start_utc").first()

        if david_assignment:
            SwapRequest.objects.get_or_create(
                requester=staff["david"],
                assignment=david_assignment,
                defaults={
                    "target":               staff["henry"],
                    "request_type":         SwapRequest.Type.SWAP,
                    "status":               SwapRequest.Status.APPROVED,
                    "target_accepted_at":   timezone.now() - timedelta(days=3),
                    "manager_reviewed_at":  timezone.now() - timedelta(days=2),
                    "requester_note":       "Switching with Henry, we agreed.",
                },
            )
            self.stdout.write("  ✓ Scenario 3: David ↔ Henry swap (approved, historical)")

        # --- Scenario 4: Grace past swap — rejected -----------------
        grace_assignment = ShiftAssignment.objects.filter(
            user=staff["grace"],
            status=ShiftAssignment.Status.ASSIGNED,
            shift__start_utc__lt=timezone.now(),
        ).select_related("shift").order_by("-shift__start_utc").first()

        if grace_assignment:
            SwapRequest.objects.get_or_create(
                requester=staff["grace"],
                assignment=grace_assignment,
                defaults={
                    "request_type":         SwapRequest.Type.DROP,
                    "status":               SwapRequest.Status.REJECTED,
                    "manager_reviewed_at":  timezone.now() - timedelta(days=5),
                    "manager_note":         "Insufficient notice. Please find your own coverage first.",
                    "requester_note":       "Need this day off.",
                },
            )
            self.stdout.write("  ✓ Scenario 4: Grace drop request (rejected, historical)")