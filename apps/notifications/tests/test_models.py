import datetime
from django.test import TestCase
from apps.accounts.models import User
from apps.notifications.models import Notification


class TestNotificationModel(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="notify@example.com",
            password="pass123",
            first_name="Notify",
            last_name="Tester",
        )

    def test_notification_creation(self):
        notif = Notification.objects.create(
            recipient=self.user,
            notification_type=Notification.Type.SHIFT_ASSIGNED,
            title="Shift Assigned",
            body="You have been assigned a new shift.",
            data={"shift_id": 1},
        )
        self.assertFalse(notif.is_read)
        self.assertIn("Shift Assigned", notif.title)
        self.assertIn("Notify", str(notif))  # __str__ includes recipient short name

    def test_mark_read(self):
        notif = Notification.objects.create(
            recipient=self.user,
            notification_type=Notification.Type.SHIFT_ASSIGNED,
            title="Shift Assigned",
            body="You have been assigned a new shift.",
        )
        notif.mark_read()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)
        self.assertLessEqual(notif.read_at, datetime.datetime.now(datetime.timezone.utc))
