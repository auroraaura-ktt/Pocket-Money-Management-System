"""URL routes for the budget app."""

from django.urls import path

from budget import views

app_name = "budget"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("reports/", views.reports, name="reports"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("admin-login/", views.admin_login_view, name="admin_login"),
    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("admin-users/", views.admin_users, name="admin_users"),
    path("change-password/", views.change_password_view, name="change_password"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "api/categories/<int:month_id>/",
        views.categories_for_month,
        name="categories_for_month",
    ),
]
