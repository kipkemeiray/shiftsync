"""URL patterns for the locations app."""


from django.urls import path
from . import views

app_name = "locations"


urlpatterns = [
    path("", views.LocationListView.as_view(), name="list"),
    path("<int:pk>/", views.LocationDetailView.as_view(), name="detail"),
    path("<int:pk>/certify/", views.CertificationView.as_view(), name="certify"),
]