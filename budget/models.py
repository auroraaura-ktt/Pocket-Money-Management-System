"""Database models for Pocket Money Management System."""

from django.conf import settings
from django.db import models


class PocketUser(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class BudgetMonth(models.Model):
    user = models.ForeignKey(
        PocketUser, on_delete=models.CASCADE, related_name="months"
    )
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    total_money = models.DecimalField(max_digits=12, decimal_places=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "year", "month"], name="uq_user_month"
            ),
        ]

    @property
    def month_label(self) -> str:
        return f"{settings.MONTH_NAMES[self.month]} {self.year}"

    def __str__(self):
        return f"{self.user.name} — {self.month_label}"


class BudgetCategory(models.Model):
    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=100)
    estimated_amount = models.DecimalField(max_digits=12, decimal_places=0)
    period_index = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name_plural = "budget categories"
        ordering = ["period_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget_month", "period_index"], name="uq_month_period"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.budget_month.month_label})"


class DailyExpense(models.Model):
    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="expenses"
    )
    category = models.ForeignKey(
        BudgetCategory, on_delete=models.CASCADE, related_name="expenses"
    )
    expense_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=0)
    note = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-expense_date", "-id"]

    def __str__(self):
        return f"{self.expense_date} — {self.amount} MMK"


class ReportEmailLog(models.Model):
    PERIOD_1 = "period_1"
    PERIOD_2 = "period_2"
    PERIOD_3 = "period_3"
    MONTHLY_FULL = "monthly_full"

    REPORT_TYPE_CHOICES = [
        (PERIOD_1, "Days 1-10"),
        (PERIOD_2, "Days 11-20"),
        (PERIOD_3, "Days 21-end"),
        (MONTHLY_FULL, "Monthly Full Report"),
    ]

    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="email_logs"
    )
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["budget_month", "report_type"],
                name="uq_budget_month_report_email",
            ),
        ]
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.budget_month} — {self.get_report_type_display()}"