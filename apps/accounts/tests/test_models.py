from django.test import TestCase
from apps.accounts.models import User, Skill


class UserModelTests(TestCase):
    def setUp(self):
        self.skill = Skill.objects.create(name="bartender", display_name="Bartender")
        self.user = User.objects.create_user(
            email="test@example.com",
            password="pass123",
            first_name="Test",
            last_name="User",
            role=User.Role.STAFF,
        )
        self.user.skills.add(self.skill)

    def test_user_str(self):
        self.assertIn("Test User", str(self.user))

    def test_has_skill(self):
        self.assertTrue(self.user.has_skill(self.skill))

    def test_role_properties(self):
        self.assertTrue(self.user.is_staff_member)
        self.assertFalse(self.user.is_admin)
        self.assertFalse(self.user.is_manager)

    def test_get_full_and_short_name(self):
        self.assertEqual(self.user.get_full_name(), "Test User")
        self.assertEqual(self.user.get_short_name(), "Test")
