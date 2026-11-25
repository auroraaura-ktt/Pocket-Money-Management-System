"""URL routes for the budget app."""

from django.urls import path

from budget import views

app_name = "budget"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("wallet-groups/<int:balance_id>/", views.wallet_group_detail, name="wallet_group_detail"),
    path("wallet-groups/<int:balance_id>/edit/", views.edit_wallet_group, name="edit_wallet_group"),
    path("reports/", views.reports, name="reports"),
    path("audit-history/", views.audit_history, name="audit_history"),
    path(
        "audit-history/all/",
        views.admin_audit_history,
        name="admin_audit_history",
    ),
    path(
        "main-balances/<int:month_id>/",
        views.main_balance_detail,
        name="main_balance_detail",
    ),
    path(
        "main-balances/<int:month_id>/edit/",
        views.edit_main_balance,
        name="edit_main_balance",
    ),
    path(
        "main-balances/<int:month_id>/delete/",
        views.delete_main_balance,
        name="delete_main_balance",
    ),
    path(
        "main-balances/<int:month_id>/inputs/add/",
        views.create_input_balance,
        name="create_input_balance",
    ),
    path(
        "input-balances/<int:input_balance_id>/edit/",
        views.edit_input_balance,
        name="edit_input_balance",
    ),
    path(
        "input-balances/<int:input_balance_id>/delete/",
        views.delete_input_balance,
        name="delete_input_balance",
    ),
    path(
        "main-balances/<int:month_id>/sub-balances/add/",
        views.create_sub_balance,
        name="create_sub_balance",
    ),
    path(
        "sub-balances/<int:sub_balance_id>/",
        views.sub_balance_detail,
        name="sub_balance_detail",
    ),
    path(
        "sub-balances/<int:sub_balance_id>/edit/",
        views.edit_sub_balance,
        name="edit_sub_balance",
    ),
    path(
        "sub-balances/<int:sub_balance_id>/allocations/add/",
        views.create_sub_balance_allocation,
        name="create_sub_balance_allocation",
    ),
    path(
        "sub-balance-allocations/<int:allocation_id>/edit/",
        views.edit_sub_balance_allocation,
        name="edit_sub_balance_allocation",
    ),
    path(
        "sub-balance-allocations/<int:allocation_id>/delete/",
        views.delete_sub_balance_allocation,
        name="delete_sub_balance_allocation",
    ),
    path(
        "sub-balances/<int:sub_balance_id>/delete/",
        views.delete_sub_balance,
        name="delete_sub_balance",
    ),
    path(
        "sub-balances/<int:sub_balance_id>/finish/",
        views.finish_sub_balance,
        name="finish_sub_balance",
    ),
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
