from django.contrib import admin
from .models import Shift, ShiftAssignment, SwapRequest, ManagerOverride


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("location", "required_skill", "start_utc", "end_utc", "headcount_needed", "is_published")
    list_filter = ("location", "required_skill", "is_published")
    search_fields = ("location__name", "required_skill__name")
    ordering = ("start_utc",)


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("shift", "user", "status", "assigned_by", "assigned_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__email", "user__first_name", "user__last_name")
    ordering = ("shift", "user")


@admin.register(SwapRequest)
class SwapRequestAdmin(admin.ModelAdmin):
    list_display = ("assignment", "requester", "target", "status", "request_type", "created_at")
    list_filter = ("status", "request_type")
    search_fields = ("requester__email", "target__email")
    ordering = ("created_at",)


@admin.register(ManagerOverride)
class ManagerOverrideAdmin(admin.ModelAdmin):
    list_display = ("assignment", "manager", "reason", "created_at")
    search_fields = ("manager__email", "reason")
    ordering = ("created_at",)
