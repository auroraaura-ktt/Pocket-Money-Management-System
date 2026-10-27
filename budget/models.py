"""Database models for Pocket Money Management System."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class PocketUser(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    auth_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pocket_user_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Balance(models.Model):
    """A named sub-balance list a user creates to group their budget months."""

    user = models.ForeignKey(
        PocketUser, on_delete=models.CASCADE, related_name="balances"
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="uq_user_balance_name"
            ),
        ]

    def __str__(self):
        return self.name


class BudgetMonth(models.Model):
    name = models.CharField(max_length=100, blank=True, default="")
    user = models.ForeignKey(
        PocketUser, on_delete=models.CASCADE, related_name="months"
    )
    balance = models.ForeignKey(
        Balance,
        on_delete=models.CASCADE,
        related_name="months",
        null=True,
        blank=True,
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


class SubBalance(models.Model):
    """A user-named balance record explicitly created under a Main Balance."""

    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="sub_balances"
    )
    name = models.CharField(max_length=100)
    balance_amount = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    balance_date = models.DateField(null=True, blank=True)
    estimated_amount = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.balance_amount is not None and self.balance_amount < 0:
            raise ValidationError({"balance_amount": "Opening balance cannot be negative."})
        if self.balance_date and self.budget_month_id:
            parent_month = self.budget_month
            if (self.balance_date.year, self.balance_date.month) != (
                parent_month.year,
                parent_month.month,
            ):
                raise ValidationError(
                    {
                        "balance_date": (
                            "Sub-Balance date must fall within its parent Main Balance month."
                        )
                    }
                )

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.name} ({self.budget_month.month_label})"

    @property
    def allocated_amount(self):
        if self.balance_amount is not None and self.balance_amount != 0:
            return self.balance_amount
        if self.estimated_amount is not None:
            return self.estimated_amount
        return 0

    @property
    def total_actual_spent(self):
        from django.db.models import Sum
        from django.db.models.functions import Abs
        from django.db.models import F

        return self.expenses.filter(
            budget_month_id=self.budget_month_id
        ).aggregate(total=Sum(Abs(F("amount"))))["total"] or 0

    @property
    def remaining_balance(self):
        return self.allocated_amount - self.total_actual_spent


class BudgetCategory(models.Model):
    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="categories"
    )
    sub_balance = models.ForeignKey(
        SubBalance, on_delete=models.CASCADE, related_name="categories",
        null=True, blank=True,
    )
    name = models.CharField(max_length=100)
    estimated_amount = models.DecimalField(max_digits=12, decimal_places=0)
    period_index = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name_plural = "budget categories"
        ordering = ["period_index", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget_month", "name"], name="uq_month_category_name"
            ),
            models.UniqueConstraint(
                fields=["sub_balance", "name"], condition=models.Q(sub_balance__isnull=False),
                name="uq_sub_balance_category_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.budget_month.month_label})"


class DailyExpense(models.Model):
    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="expenses"
    )
    sub_balance = models.ForeignKey(
        SubBalance, on_delete=models.CASCADE, related_name="expenses",
        null=True, blank=True,
    )
    category = models.ForeignKey(
        BudgetCategory, on_delete=models.CASCADE, related_name="expenses"
    )
    expense_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=0)
    note = models.CharField(max_length=255, blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.category_id and self.budget_month_id != self.category.budget_month_id:
            raise ValidationError({"category": "Category must belong to the selected Main Balance month."})
        if self.sub_balance_id:
            if self.sub_balance.budget_month_id != self.budget_month_id:
                raise ValidationError({"sub_balance": "Sub Balance must belong to the selected Main Balance month."})
            if self.category_id and self.category.sub_balance_id != self.sub_balance_id:
                raise ValidationError({"category": "Category must belong to the selected Sub Balance."})
        elif self.category_id and self.category.sub_balance_id:
            raise ValidationError({"category": "Sub Balance categories cannot be assigned to Main Balance transactions."})
        if self.amount is not None and self.amount == 0:
            raise ValidationError({"amount": "Transaction amount cannot be zero."})

    class Meta:
        ordering = ["-expense_date", "-id"]

    def __str__(self):
        return f"{self.expense_date} — {self.amount} MMK"


class ReportEmailLog(models.Model):
    PERIOD_1 = "period_1"
    PERIOD_2 = "period_2"
    PERIOD_3 = "period_3"
    MONTHLY_FULL = "monthly_full"
    FINISH = "finish"
    CATEGORY = "category"

    REPORT_TYPE_CHOICES = [
        (PERIOD_1, "Days 1-10"),
        (PERIOD_2, "Days 11-20"),
        (PERIOD_3, "Days 21-end"),
        (MONTHLY_FULL, "Monthly Full Report"),
        (FINISH, "Finish Report"),
        (CATEGORY, "Category Report"),
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
