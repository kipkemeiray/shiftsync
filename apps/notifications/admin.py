from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "body", "is_read", "created_at", "read_at")
    list_filter = ("is_read",)
    search_fields = ("recipient__email", "recipient__first_name", "recipient__last_name", "title", "body")
    ordering = ("-created_at",)
