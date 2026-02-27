"""
Audit views for ShiftSync.

View inventory:
  AuditLogView → admin-only paginated audit trail with CSV export
"""

import csv
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from apps.audit.models import AuditLog
from core.permissions import AdminRequiredMixin

logger = logging.getLogger(__name__)


class AuditLogView(AdminRequiredMixin, View):
    """
    Immutable audit log viewer (admin only).

    Supports filtering by action, actor, and date range.
    Supports CSV export for compliance reporting.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """
        Render the audit log with optional filters.

        Query params:
          action: filter by action string (partial match)
          actor:  filter by actor user ID
          export: 'csv' triggers a file download
        """
        logs = AuditLog.objects.select_related("actor").order_by("-created_at")

        action_filter = request.GET.get("action", "").strip()
        if action_filter:
            logs = logs.filter(action__icontains=action_filter)

        actor_filter = request.GET.get("actor", "").strip()
        if actor_filter:
            logs = logs.filter(actor__id=actor_filter)

        if request.GET.get("export") == "csv":
            return self._export_csv(logs)

        return render(request, "audit/log.html", {
            "logs": logs[:200],
            "action_filter": action_filter,
        })

    @staticmethod
    def _export_csv(logs) -> HttpResponse:
        """
        Stream audit log entries as a CSV file download.

        Args:
            logs: AuditLog queryset to export.

        Returns:
            StreamingHttpResponse with CSV content.
        """
        def rows():
            yield ["Timestamp", "Actor", "Action", "Object ID", "Note"]
            for log in logs:
                yield [
                    log.created_at.isoformat(),
                    log.actor.get_full_name() if log.actor else "System",
                    log.action,
                    log.object_id or "",
                    log.note,
                ]

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="shiftsync_audit.csv"'
        writer = csv.writer(response)
        for row in rows():
            writer.writerow(row)
        return response