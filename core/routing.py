"""WebSocket URL routing for ShiftSync Channels consumers."""

from django.urls import re_path

from core.consumers import AdminDashboardConsumer, ScheduleConsumer, UserConsumer

websocket_urlpatterns = [
    # Location schedule room — staff and managers join to receive live schedule updates
    re_path(r"ws/schedule/(?P<location_id>\d+)/$", ScheduleConsumer.as_asgi()),
    # Personal notification stream for each user
    re_path(r"ws/user/$", UserConsumer.as_asgi()),
    # Admin-only live dashboard
    re_path(r"ws/admin/dashboard/$", AdminDashboardConsumer.as_asgi()),
]