from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from apps.accounts.models import User, Skill
from apps.locations.models import Location
from apps.scheduling.models import ManagerOverride, Shift, ShiftAssignment, SwapRequest
from apps.notifications.models import Notification
from apps.audit.models import AuditLog


class TestShiftAssignmentIntegration(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="staff@example.com", password="pass123")
        self.manager = User.objects.create_user(email="mgr@example.com", password="pass123", role=User.Role.MANAGER)
        self.skill = Skill.objects.create(name="server", display_name="Server")
        self.location = Location.objects.create(name="Downtown", timezone="America/New_York")
        self.shift = Shift.objects.create(
            location=self.location,
            required_skill=self.skill,
            start_utc=timezone.now(),
            end_utc=timezone.now() + timedelta(hours=4),
        )

    def test_assignment_triggers_notification_and_audit(self):
        assignment = ShiftAssignment.objects.create(user=self.user, shift=self.shift, assigned_by=self.manager)

        # Notification created
        notif = Notification.objects.filter(recipient=self.user).first()
        self.assertIsNotNone(notif)
        self.assertIn("Shift", notif.title)

        # Audit log created
        log = AuditLog.objects.filter(actor=self.manager, action="shift_assignment.created").first()
        self.assertIsNotNone(log)
        self.assertIn("shift_assignment.created", str(log))


class TestSwapRequestIntegration(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="u1@example.com", password="pass123", first_name="User", last_name="One")
        self.user2 = User.objects.create_user(email="u2@example.com", password="pass123", first_name="User", last_name="Two")
        self.skill = Skill.objects.create(name="cook", display_name="Cook")
        self.location = Location.objects.create(name="Harbor", timezone="America/New_York")
        self.shift = Shift.objects.create(
            location=self.location,
            required_skill=self.skill,
            start_utc=timezone.now(),
            end_utc=timezone.now() + timedelta(hours=4),
        )
        self.assignment = ShiftAssignment.objects.create(user=self.user1, shift=self.shift, assigned_by=self.user1)

    def test_swap_request_triggers_notification_and_audit(self):
        swap = SwapRequest.objects.create(
            requester=self.user1,
            target=self.user2,
            assignment=self.assignment,
            request_type=SwapRequest.Type.SWAP,
        )

        # Notification created for assignment owner
        notif = Notification.objects.filter(recipient=self.assignment.user).first()
        self.assertIsNotNone(notif)
        self.assertIn("Swap Request Received", notif.title)

        # Audit log created for requester
        log = AuditLog.objects.filter(actor=self.user1, action="swap_request.swap.created").first()
        self.assertIsNotNone(log)
        self.assertIn("swap_request.swap.created", str(log))


class TestManagerOverrideIntegration(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            email="mgr@example.com", password="pass123", role=User.Role.MANAGER
        )
        self.user = User.objects.create_user(
            email="staff@example.com", password="pass123"
        )
        self.skill = Skill.objects.create(name="expo", display_name="Expo")
        self.location = Location.objects.create(name="Marina", timezone="America/Los_Angeles")
        self.shift = Shift.objects.create(
            location=self.location,
            required_skill=self.skill,
            start_utc=timezone.now(),
            end_utc=timezone.now() + timedelta(hours=4),
        )
        self.assignment = ShiftAssignment.objects.create(
            user=self.user, shift=self.shift, assigned_by=self.manager
        )

    def test_manager_override_triggers_audit_and_notification(self):
        override = ManagerOverride.objects.create(
            assignment=self.assignment,
            manager=self.manager,
            reason="Overtime exception"
        )

        # Notification created for staff
        notif = Notification.objects.filter(recipient=self.user).first()
        self.assertIsNotNone(notif)
        self.assertIn("Manager Override", notif.title)

        # Audit log created for manager
        log = AuditLog.objects.filter(actor=self.manager, action="manager_override.created").first()
        self.assertIsNotNone(log)
        self.assertIn("manager_override.created", str(log))
