import datetime
from django.test import TestCase
from apps.accounts.models import User
from apps.locations.models import Location, LocationCertification


class TestLocationModel(TestCase):
    def test_get_zoneinfo_and_now_local(self):
        loc = Location.objects.create(name="Westside", timezone="America/Los_Angeles")
        zone = loc.get_zoneinfo()
        self.assertEqual(zone.key, "America/Los_Angeles")

        now_local = loc.now_local()
        self.assertEqual(now_local.tzinfo.key, "America/Los_Angeles")


class TestLocationCertification(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="cert@example.com", password="pass123", first_name="Cert", last_name="Tester"
        )
        self.loc = Location.objects.create(name="Downtown", timezone="America/New_York")
        self.certifier = User.objects.create_user(
            email="admin@example.com", password="pass123", first_name="Admin", last_name="User", role=User.Role.ADMIN
        )

    def test_certification_creation_and_str(self):
        cert = LocationCertification.objects.create(
            user=self.user, location=self.loc, certified_by=self.certifier
        )
        self.assertTrue(cert.is_active)
        self.assertIn("Downtown", str(cert))

    def test_deactivate_certification(self):
        cert = LocationCertification.objects.create(
            user=self.user, location=self.loc, certified_by=self.certifier
        )
        cert.deactivate(reason="Employment ended")
        self.assertFalse(cert.is_active)
        self.assertEqual(cert.deactivated_reason, "Employment ended")