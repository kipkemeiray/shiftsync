from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("actor", "action", "created_at")
    search_fields = ("actor__email", "actor__first_name", "actor__last_name", "action")
    ordering = ("-created_at",)
