"""URL patterns for the accounts app."""
from django.urls import path
from . import views

app_name = "accounts"


urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("availability/", views.AvailabilityView.as_view(), name="availability"),
    path("staff/", views.StaffListView.as_view(), name="staff_list"),
]