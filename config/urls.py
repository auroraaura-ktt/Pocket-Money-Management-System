"""URL configuration for Pocket Money Management System."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("budget.urls")),
]
