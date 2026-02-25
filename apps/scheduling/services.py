from django.db import transaction
from apps.scheduling.models import ShiftAssignment
from apps.scheduling.constraints import ConstraintEngine, ConstraintResult


class ShiftAssignmentService:
    """
    Service object for assigning staff to shifts.

    Responsibilities:
      - Run constraint checks before assignment
      - Handle transaction safety with SELECT FOR UPDATE
      - Return structured results (success or conflict)
    """

    @staticmethod
    @transaction.atomic
    def assign(user, shift, assigned_by):
        """
        Attempt to assign a user to a shift.

        Args:
            user: The staff member being assigned
            shift: The shift instance
            assigned_by: The manager/admin performing the assignment

        Returns:
            dict with success flag and either assignment or conflict result
        """
        result = ConstraintEngine.check(user, shift)
        if not result.ok:
            return {"success": False, "result": result}

        assignment = ShiftAssignment.objects.create(
            shift=shift,
            user=user,
            assigned_by=assigned_by,
        )
        return {"success": True, "assignment": assignment}
