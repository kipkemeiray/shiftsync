import datetime

from django.test import TestCase
from apps.accounts.models import User, StaffAvailability


class TestStaffAvailability(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="avail@example.com",
            password="pass123",
            first_name="Avail",
            last_name="Tester",
            role=User.Role.STAFF,
        )

    def test_weekly_availability(self):
        avail = StaffAvailability.objects.create(
            user=self.user,
            recurrence=StaffAvailability.Recurrence.WEEKLY,
            day_of_week=0,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(17, 0),
            timezone="UTC",
        )
        self.assertEqual(avail.day_of_week, 0)
        self.assertFalse(avail.is_unavailable_day)
        self.assertIn("Mon", str(avail))

    def test_unavailable_day(self):
        avail = StaffAvailability.objects.create(
            user=self.user,
            recurrence=StaffAvailability.Recurrence.ONE_OFF,
            specific_date="2026-02-25",
            start_time=None,
            end_time=None,
            timezone="UTC",
        )
        self.assertTrue(avail.is_unavailable_day)
        self.assertIn("Unavailable", str(avail))
