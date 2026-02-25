from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User, Skill
from apps.locations.models import Location, LocationCertification
from apps.scheduling.models import Shift, ShiftAssignment


class Command(BaseCommand):
    help = "Seed demo data for ShiftSync (users, skills, locations, certifications, shifts)"

    def handle(self, *args, **options):
        # --- Skills ---
        skills = {
            "bartender": Skill.objects.get_or_create(name="bartender", defaults={"display_name": "Bartender"})[0],
            "server": Skill.objects.get_or_create(name="server", defaults={"display_name": "Server"})[0],
            "line_cook": Skill.objects.get_or_create(name="line_cook", defaults={"display_name": "Line Cook"})[0],
            "host": Skill.objects.get_or_create(name="host", defaults={"display_name": "Host"})[0],
            "expo": Skill.objects.get_or_create(name="expo", defaults={"display_name": "Expo"})[0],
            "busser": Skill.objects.get_or_create(name="busser", defaults={"display_name": "Busser"})[0],
        }

        # --- Locations ---
        westside = Location.objects.get_or_create(name="Westside", timezone="America/Los_Angeles")[0]
        marina = Location.objects.get_or_create(name="Marina", timezone="America/Los_Angeles")[0]
        downtown = Location.objects.get_or_create(name="Downtown", timezone="America/New_York")[0]
        harbor = Location.objects.get_or_create(name="Harbor", timezone="America/New_York")[0]

        # --- Admin ---
        admin = User.objects.get_or_create(
            email="admin@coastaleats.com",
            defaults={
                "first_name": "Admin",
                "last_name": "User",
                "role": User.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
            },
        )[0]
        admin.set_password("ShiftSync2026!")
        admin.save()

        # --- Managers ---
        mgr_west = User.objects.get_or_create(
            email="mgr.westside@coastaleats.com",
            defaults={"first_name": "West", "last_name": "Manager", "role": User.Role.MANAGER},
        )[0]
        mgr_west.set_password("ShiftSync2026!")
        mgr_west.save()
        westside.managers.add(mgr_west)
        marina.managers.add(mgr_west)

        mgr_east = User.objects.get_or_create(
            email="mgr.eastcoast@coastaleats.com",
            defaults={"first_name": "East", "last_name": "Manager", "role": User.Role.MANAGER},
        )[0]
        mgr_east.set_password("ShiftSync2026!")
        mgr_east.save()
        downtown.managers.add(mgr_east)
        harbor.managers.add(mgr_east)

        # --- Staff ---
        staff_data = [
            ("alice@coastaleats.com", "Alice", "Staff", [skills["bartender"], skills["server"]], [westside, marina]),
            ("bob@coastaleats.com", "Bob", "Staff", [skills["line_cook"]], [westside]),
            ("carol@coastaleats.com", "Carol", "Staff", [skills["server"], skills["host"]], [downtown, harbor]),
            ("david@coastaleats.com", "David", "Staff", [skills["bartender"]], [downtown]),
            ("eve@coastaleats.com", "Eve", "Staff", [skills["line_cook"], skills["server"]], [westside, marina, downtown, harbor]),
            ("frank@coastaleats.com", "Frank", "Staff", [skills["host"], skills["busser"]], [marina]),
            ("grace@coastaleats.com", "Grace", "Staff", [skills["server"], skills["expo"]], [harbor, downtown]),
            ("henry@coastaleats.com", "Henry", "Staff", [skills["bartender"], skills["expo"]], [westside, downtown]),
        ]

        for email, first, last, skill_list, locs in staff_data:
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={"first_name": first, "last_name": last, "role": User.Role.STAFF},
            )
            user.set_password("ShiftSync2026!")
            user.save()
            user.skills.set(skill_list)
            for loc in locs:
                LocationCertification.objects.get_or_create(user=user, location=loc, defaults={"certified_by": admin})

        # --- Demo Shifts & Assignments ---
        now = timezone.now()

        # Bob: Overtime trap (5 shifts ~8h each)
        bob = User.objects.get(email="bob@coastaleats.com")
        for i in range(5):
            start = now - timedelta(days=i+2, hours=10)
            end = start + timedelta(hours=8)
            shift = Shift.objects.create(
                location=westside,
                required_skill=skills["line_cook"],
                headcount_needed=1,
                start_utc=start,
                end_utc=end,
                is_published=True,
                published_at=start,
                published_by=admin,
                created_by=admin,
            )
            ShiftAssignment.objects.create(shift=shift, user=bob, assigned_by=admin)

        # Alice: Premium shifts (Friday & Saturday evenings)
        alice = User.objects.get(email="alice@coastaleats.com")
        for i in range(2):
            start = now - timedelta(days=i+7, hours=17)
            end = start + timedelta(hours=6)
            shift = Shift.objects.create(
                location=marina,
                required_skill=skills["server"],
                headcount_needed=1,
                start_utc=start,
                end_utc=end,
                is_published=True,
                published_at=start,
                published_by=admin,
                created_by=admin,
            )
            ShiftAssignment.objects.create(shift=shift, user=alice, assigned_by=admin)

        # Carol: 6 consecutive days
        carol = User.objects.get(email="carol@coastaleats.com")
        for i in range(6):
            start = now - timedelta(days=i+1, hours=9)
            end = start + timedelta(hours=8)
            shift = Shift.objects.create(
                location=downtown,
                required_skill=skills["server"],
                headcount_needed=1,
                start_utc=start,
                end_utc=end,
                is_published=True,
                published_at=start,
                published_by=admin,
                created_by=admin,
            )
            ShiftAssignment.objects.create(shift=shift, user=carol, assigned_by=admin)

        self.stdout.write(self.style.SUCCESS("Seed data created successfully with demo shifts and assignments."))
