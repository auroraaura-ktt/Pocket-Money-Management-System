"""Database models for Pocket Money Management System."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
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
    category = models.CharField(max_length=100, blank=True, default="")
    total_money = models.DecimalField(max_digits=12, decimal_places=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month"]

    @property
    def month_label(self) -> str:
        return f"{settings.MONTH_NAMES[self.month]} {self.year}"

    def __str__(self):
        return f"{self.user.name} — {self.month_label}"


class InputBalance(models.Model):
    """An individual amount of money received during a budget month."""

    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="input_balances"
    )
    category = models.CharField(max_length=100, blank=True, default="")
    amount = models.DecimalField(
        max_digits=12, decimal_places=0, validators=[MinValueValidator(0)]
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.category or 'Income'} — {self.amount} MMK"


class SubBalance(models.Model):
    """A user-named balance record explicitly created under a Main Balance."""

    MANUAL_ALLOCATION = "manual"
    INHERIT_MAIN_TOTAL = "inherit_main_total"
    ALLOCATION_MODE_CHOICES = [
        (MANUAL_ALLOCATION, "Manual allocation"),
        (INHERIT_MAIN_TOTAL, "Inherit Main Balance total"),
    ]

    budget_month = models.ForeignKey(
        BudgetMonth, on_delete=models.CASCADE, related_name="sub_balances"
    )
    source_input_balance = models.ForeignKey(
        InputBalance,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="sub_allocations",
    )
    name = models.CharField(max_length=100)
    allocation_mode = models.CharField(
        max_length=24,
        choices=ALLOCATION_MODE_CHOICES,
        default=MANUAL_ALLOCATION,
    )
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
        if (
            self.source_input_balance_id
            and self.source_input_balance.budget_month_id != self.budget_month_id
        ):
            raise ValidationError(
                {"source_input_balance": "Choose a Money Entry from this Main Balance."}
            )
        if (
            self.allocation_mode == self.INHERIT_MAIN_TOTAL
            and self.source_input_balance_id
        ):
            raise ValidationError(
                {"source_input_balance": "Inherited Sub Balances use the Main Balance total."}
            )

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.name} ({self.budget_month.month_label})"

    @property
    def allocated_amount(self):
        if self.allocation_mode == self.INHERIT_MAIN_TOTAL:
            if not self.budget_month_id:
                return 0
            return self.budget_month.total_money

        legacy_amount = self.balance_amount
        if self.balance_amount is not None and self.balance_amount != 0:
            legacy_amount = self.balance_amount
        elif self.estimated_amount is not None:
            legacy_amount = self.estimated_amount
        else:
            legacy_amount = 0

        if self.pk:
            manual_total = self.allocations.aggregate(total=Sum("amount"))["total"] or 0
            return legacy_amount + manual_total
        return legacy_amount

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


class SubBalanceAllocation(models.Model):
    """A manually categorized amount allocated to one Sub Balance."""

    sub_balance = models.ForeignKey(
        SubBalance, on_delete=models.CASCADE, related_name="allocations"
    )
    category = models.CharField(max_length=100)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(1)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.category} — {self.amount} MMK"


class AuditEvent(models.Model):
    """Append-only snapshot of a successful user-visible balance operation."""

    ACTION_CHOICES = [
        ("created_main_balance", "Created Main Balance"),
        ("edited_main_balance", "Edited Main Balance"),
        ("deleted_main_balance", "Deleted Main Balance"),
        ("created_money_entry", "Added Money Entry"),
        ("edited_money_entry", "Edited Money Entry"),
        ("deleted_money_entry", "Deleted Money Entry"),
        ("created_sub_balance", "Added Sub Balance"),
        ("edited_sub_balance", "Edited Sub Balance"),
        ("deleted_sub_balance", "Deleted Sub Balance"),
        ("created_expense", "Recorded Expense"),
        ("edited_expense", "Updated Expense"),
        ("deleted_expense", "Deleted Expense"),
        ("user_login", "User Login"),
        ("user_logout", "User Logout"),
    ]
    OBJECT_TYPE_CHOICES = [
        ("main_balance", "Main Balance"),
        ("money_entry", "Money Entry"),
        ("sub_balance", "Sub Balance"),
        ("daily_expense", "Expense"),
        ("authentication", "Authentication"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balance_audit_events",
    )
    actor_name = models.CharField(max_length=150)
    actor_role = models.CharField(max_length=20)
    owner = models.ForeignKey(
        PocketUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balance_audit_events",
    )
    owner_name = models.CharField(max_length=100, blank=True)
    owner_email = models.EmailField(blank=True)
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    object_type = models.CharField(max_length=20, choices=OBJECT_TYPE_CHOICES)
    object_id = models.PositiveBigIntegerField()
    object_label = models.CharField(max_length=255)
    main_balance_id = models.PositiveBigIntegerField(null=True, blank=True)
    main_balance_name = models.CharField(max_length=100, blank=True)
    month_label = models.CharField(max_length=50, blank=True)
    money_entry_id = models.PositiveBigIntegerField(null=True, blank=True)
    money_entry_category = models.CharField(max_length=100, blank=True)
    money_entry_amount = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True
    )
    sub_balance_id = models.PositiveBigIntegerField(null=True, blank=True)
    sub_balance_name = models.CharField(max_length=100, blank=True)
    sub_balance_amount = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True
    )
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"], name="audit_created_idx"),
            models.Index(
                fields=["actor", "-created_at"], name="audit_actor_created_idx"
            ),
            models.Index(
                fields=["action", "-created_at"], name="audit_action_created_idx"
            ),
        ]


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
