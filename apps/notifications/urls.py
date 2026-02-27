"""URL patterns for the notifications app."""
from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationCenterView.as_view(), name="center"),
    path("mark-read/", views.mark_read, name="mark_read"),
]