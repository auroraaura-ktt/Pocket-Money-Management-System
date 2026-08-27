"""Django admin configuration for Pocket Money Management System."""

from django.contrib import admin

from budget.models import (
    Balance,
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    ReportEmailLog,
)


@admin.register(Balance)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at")
    list_filter = ("user",)
    search_fields = ("name", "user__name", "user__email")
    ordering = ("user", "name")


@admin.register(PocketUser)
class PocketUserAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "created_at")
    search_fields = ("name", "email")
    ordering = ("name",)


@admin.register(BudgetMonth)
class BudgetMonthAdmin(admin.ModelAdmin):
    list_display = ("user", "month_label", "total_money", "created_at")
    list_filter = ("year", "month", "user")
    search_fields = ("user__name", "user__email")
    ordering = ("-year", "-month")


@admin.register(BudgetCategory)
class BudgetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "budget_month", "estimated_amount", "period_index")
    list_filter = ("budget_month__user", "budget_month__year", "budget_month__month")
    search_fields = ("name", "budget_month__user__name")
    ordering = ("budget_month", "period_index", "id")


@admin.register(DailyExpense)
class DailyExpenseAdmin(admin.ModelAdmin):
    list_display = ("expense_date", "category", "budget_month", "amount", "note")
    list_filter = ("budget_month__user", "budget_month__year", "budget_month__month")
    search_fields = ("note", "category__name", "budget_month__user__name")
    date_hierarchy = "expense_date"
    ordering = ("-expense_date", "-id")


@admin.register(ReportEmailLog)
class ReportEmailLogAdmin(admin.ModelAdmin):
    list_display = ("budget_month", "report_type", "sent_at")
    list_filter = ("report_type", "budget_month__user")
    search_fields = ("budget_month__user__name", "budget_month__user__email")
    ordering = ("-sent_at",)