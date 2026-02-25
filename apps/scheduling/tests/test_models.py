from django.test import TestCase
from django.utils import timezone
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from apps.accounts.models import User, Skill
from apps.locations.models import Location
from apps.scheduling.models import Shift, ShiftAssignment, SwapRequest, ManagerOverride


class TestShiftModel(TestCase):
    def setUp(self):
        self.skill = Skill.objects.create(name="bartender", display_name="Bartender")
        self.location = Location.objects.create(name="Westside", timezone="America/Los_Angeles")

    def test_is_premium_shift(self):
        # Explicitly set a Friday 7pm PT shift 
        start_local = datetime(2026, 2, 27, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles")) # Friday 
        end_local = start_local + timedelta(hours=5) 
        
        # Convert to UTC for storage 
        start_utc = start_local.astimezone(ZoneInfo("UTC"))
        end_utc = end_local.astimezone(ZoneInfo("UTC"))

        shift = Shift.objects.create(location=self.location, required_skill=self.skill, start_utc=start_utc, end_utc=end_utc)
        
        self.assertTrue(shift.is_premium, "Friday evening shift should be premium")

class TestShiftAssignment(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="staff@example.com", password="pass123", first_name="Staff", last_name="Member")
        self.skill = Skill.objects.create(name="server", display_name="Server")
        self.location = Location.objects.create(name="Downtown", timezone="America/New_York")
        self.shift = Shift.objects.create(location=self.location, required_skill=self.skill, start_utc=timezone.now(), end_utc=timezone.now() + timedelta(hours=4))

    def test_assignment_creation(self):
        assignment = ShiftAssignment.objects.create(user=self.user, shift=self.shift, assigned_by=self.user)
        self.assertEqual(assignment.status, ShiftAssignment.Status.ASSIGNED)


class TestSwapRequest(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="u1@example.com", password="pass123")
        self.user2 = User.objects.create_user(email="u2@example.com", password="pass123")
        self.skill = Skill.objects.create(name="cook", display_name="Cook")
        self.location = Location.objects.create(name="Harbor", timezone="America/New_York")
        self.shift = Shift.objects.create(location=self.location, required_skill=self.skill, start_utc=timezone.now(), end_utc=timezone.now() + timedelta(hours=4))
        self.assignment = ShiftAssignment.objects.create(user=self.user1, shift=self.shift, assigned_by=self.user1)

    def test_swap_request_lifecycle(self):
        swap = SwapRequest.objects.create(requester=self.user1, target=self.user2, assignment=self.assignment, request_type=SwapRequest.Type.SWAP)
        self.assertEqual(swap.status, SwapRequest.Status.PENDING_ACCEPTANCE)
        swap.status = SwapRequest.Status.CANCELLED
        swap.save()
        self.assertEqual(swap.status, SwapRequest.Status.CANCELLED)


class TestManagerOverride(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(email="mgr@example.com", password="pass123", role=User.Role.MANAGER)
        self.user = User.objects.create_user(email="staff@example.com", password="pass123")
        self.skill = Skill.objects.create(name="expo", display_name="Expo")
        self.location = Location.objects.create(name="Marina", timezone="America/Los_Angeles")
        self.shift = Shift.objects.create(location=self.location, required_skill=self.skill, start_utc=timezone.now(), end_utc=timezone.now() + timedelta(hours=4))
        self.assignment = ShiftAssignment.objects.create(user=self.user, shift=self.shift, assigned_by=self.manager)

    def test_override_creation(self):
        override = ManagerOverride.objects.create(assignment=self.assignment, manager=self.manager, reason="Overtime exception")
        self.assertIn("Overtime", override.reason)
