"""URL routes for the budget app."""

from django.urls import path

from budget import views

app_name = "budget"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("reports/", views.reports, name="reports"),
    path(
        "api/categories/<int:month_id>/",
        views.categories_for_month,
        name="categories_for_month",
    ),
]
