from django.contrib import admin
from .models import Location, LocationCertification


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "timezone", "created_at")
    search_fields = ("name", "timezone")
    ordering = ("name",)


@admin.register(LocationCertification)
class LocationCertificationAdmin(admin.ModelAdmin):
    list_display = ("user", "location", "is_active", "certified_by", "certified_at", "deactivated_at", "deactivated_reason")
    list_filter = ("is_active", "location")
    search_fields = ("user__email", "user__first_name", "user__last_name", "location__name")
    ordering = ("location", "user")
