from django.contrib import admin
from .models import User, Skill, StaffAvailability


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "role", "is_active", "date_joined")
    list_filter = ("role", "is_active")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("last_name", "first_name")


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("display_name", "name", "created_at")
    search_fields = ("display_name", "name")
    ordering = ("display_name",)


@admin.register(StaffAvailability)
class StaffAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("user", "recurrence", "day_of_week", "specific_date", "start_time", "end_time", "timezone")
    list_filter = ("recurrence", "timezone")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    ordering = ("user", "recurrence", "day_of_week", "specific_date")
