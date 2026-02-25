from django.test import TestCase
from apps.accounts.models import User
from apps.audit.models import AuditLog


class TestAuditLogModel(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="audit@example.com",
            password="pass123",
            first_name="Audit",
            last_name="Tester",
        )

    def test_audit_log_creation_and_str(self):
        log = AuditLog.objects.create(
            actor=self.user,
            action="shift.created",
            before={},
            after={"shift_id": 1},
        )
        self.assertIn("shift.created", str(log))
        self.assertEqual(log.actor, self.user)

    def test_audit_log_is_immutable(self):
        log = AuditLog.objects.create(
            actor=self.user,
            action="shift.updated",
            before={"shift_id": 1, "status": "ASSIGNED"},
            after={"shift_id": 1, "status": "COVERED"},
        )
        # Attempting to update should raise RuntimeError
        log.action = "shift.reassigned"
        with self.assertRaises(RuntimeError):
            log.save()
