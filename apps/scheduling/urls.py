"""URL patterns for the scheduling app."""
from django.urls import path
from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("schedule/", views.ScheduleView.as_view(), name="schedule"),
    path("shifts/manage/", views.ShiftManageView.as_view(), name="shift_manage"),
    path("shifts/create/", views.CreateShiftView.as_view(), name="create_shift"),
    path("shifts/assign/", views.AssignStaffView.as_view(), name="assign_staff"),
    path("shifts/<int:pk>/toggle-publish/", views.TogglePublishView.as_view(), name="toggle_publish"),
    path("shifts/<int:pk>/delete/", views.DeleteShiftView.as_view(), name="delete_shift"),
    path("shifts/publish-week/", views.PublishWeekView.as_view(), name="publish_week"),
    path("shifts/<int:pk>/claim/", views.claim_shift, name="claim_shift"),
    path("my-shifts/", views.MyShiftsView.as_view(), name="my_shifts"),
    path("swaps/", views.SwapListView.as_view(), name="swaps"),
    path("swaps/<int:pk>/review/", views.SwapReviewView.as_view(), name="swap_review"),
    path("on-duty/", views.on_duty_now, name="on_duty_now"),
]