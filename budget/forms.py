"""Django forms for Pocket Money Management System."""

from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from budget.periods import PERIOD_DEFINITIONS, period_index_for_day

from .models import (
    Balance,
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
)

INPUT_CLASS = (
    "w-full px-4 py-2.5 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)

# Number of custom category slots in the budget-estimate form.
CUSTOM_CATEGORY_SLOTS = 5


class UserForm(forms.ModelForm):
    class Meta:
        model = PocketUser
        fields = ["name", "email"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Your name"}
            ),
            "email": forms.EmailInput(
                attrs={"class": INPUT_CLASS, "placeholder": "you@example.com"}
            ),
        }


class UserRegistrationForm(UserCreationForm):
    """Create an authenticated (Django) user account plus a PocketUser."""

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={"class": INPUT_CLASS, "placeholder": "you@example.com"}
        ),
    )

    class Meta:
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {
                "class": INPUT_CLASS,
                "placeholder": "Choose a username",
                "autofocus": True,
            }
        )
        self.fields["username"].help_text = (
            "150 characters or fewer. Letters, digits and @/./+/-/_ only."
        )
        self.fields["password1"].widget.attrs.update(
            {
                "class": INPUT_CLASS,
                "placeholder": "Enter a password",
            }
        )
        self.fields["password1"].help_text = (
            "Your password must contain at least 8 characters and cannot be "
            "entirely numeric or too common."
        )
        self.fields["password2"].widget.attrs.update(
            {
                "class": INPUT_CLASS,
                "placeholder": "Confirm password",
            }
        )
        self.fields["password2"].help_text = ""
        self.fields["password1"].label = "Password"
        self.fields["password2"].label = "Confirm password"

    def save(self, commit=True):
        user = super().save(commit=commit)
        # Link/create the PocketUser that owns this account's budget data and
        # ensure it has at least one default sub-balance list.
        email = self.cleaned_data.get("email", "").strip().lower()
        pocket, created = PocketUser.objects.get_or_create(
            email=email,
            defaults={"name": user.username, "auth_user": user},
        )
        if not created:
            pocket.name = user.username
            pocket.auth_user = user
            pocket.save(update_fields=["name", "auth_user"])
        if not pocket.balances.exists():
            Balance.objects.create(user=pocket, name="Main Wallet")
        return user


class BalanceForm(forms.ModelForm):
    """Create a named sub-balance list within the user's account."""

    class Meta:
        model = Balance
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "e.g. Main Wallet, Savings",
                }
            ),
        }


class BudgetMonthForm(forms.ModelForm):
    """Create a budget month with a total money amount.

    Category budget estimates are set separately in the Budget tab
    via the PeriodBudgetForm (which handles all categories and sums
    them into the total money).
    """

    month = forms.TypedChoiceField(
        choices=[],
        coerce=int,
        empty_value=None,
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    balance = forms.ModelChoiceField(
        queryset=Balance.objects.none(),
        empty_label="Select a sub balance...",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    class Meta:
        model = BudgetMonth
        fields = ["balance", "year", "month", "total_money"]
        widgets = {
            "year": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 2020, "max": 2100}
            ),
            "total_money": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASS,
                    "min": 1,
                    "step": 1,
                    "placeholder": "500000",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        balance_qs = kwargs.pop("balance_qs", None)
        super().__init__(*args, **kwargs)
        from django.conf import settings
        from django.utils import timezone

        if balance_qs is not None:
            self.fields["balance"].queryset = balance_qs

        self.fields["month"].choices = [
            (i, settings.MONTH_NAMES[i]) for i in range(1, 13)
        ]
        if not self.is_bound and not self.instance.pk:
            now = timezone.now()
            self.fields["year"].initial = now.year
            self.fields["month"].initial = now.month

    def save(self, commit=True):
        from budget.services.category_service import create_default_period_categories

        budget_month = super().save(commit=commit)
        # Create the three default period categories with zero amounts.
        create_default_period_categories(budget_month)
        return budget_month


class CategoryForm(forms.ModelForm):
    """Create a custom budget category (period is optional)."""

    class Meta:
        model = BudgetCategory
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "e.g. Shopping",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Category name"

    def save(self, budget_month_id=None):
        budget_month = BudgetMonth.objects.filter(pk=budget_month_id).first()
        if not budget_month:
            raise ValidationError("Budget month not found.")
        category = super().save(commit=False)
        if budget_month_id:
            category.budget_month_id = budget_month_id
        category.estimated_amount = Decimal("0")
        category.period_index = None
        category.save()
        return category


class CustomCategoryBudgetForm(forms.Form):
    """Set the estimated budget amount for an existing custom category."""

    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.select_related("user").all(),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    category = forms.ModelChoiceField(
        queryset=BudgetCategory.objects.none(),
        empty_label="Select custom category...",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    estimated_amount = forms.DecimalField(
        label="Budget Amount (MMK)",
        min_value=0,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(
            attrs={
                "class": INPUT_CLASS,
                "min": 0,
                "step": 1,
                "placeholder": "0",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        category_qs = kwargs.pop("category_qs", None)
        super().__init__(*args, **kwargs)
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs
        month_id = None
        if self.data.get("budget_month"):
            month_id = self.data.get("budget_month")
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]
        if month_id:
            qs = BudgetCategory.objects.filter(
                budget_month_id=month_id, period_index__isnull=True
            ).order_by("id")
            if category_qs is not None:
                qs = qs.filter(pk__in=category_qs.values_list("pk", flat=True))
            self.fields["category"].queryset = qs

    def save(self):
        category = self.cleaned_data["category"]
        category.estimated_amount = to_decimal(
            self.cleaned_data.get("estimated_amount", 0)
        )
        category.save(update_fields=["estimated_amount"])

        month = category.budget_month
        month.total_money = sum(
            (item.estimated_amount for item in month.categories.all()),
            Decimal("0"),
        )
        month.save(update_fields=["total_money"])
        return category


class CategoryBudgetUpdateForm(forms.Form):
    """Update the estimated budget for any category."""

    category = forms.ModelChoiceField(
        queryset=BudgetCategory.objects.select_related("budget_month").all(),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    estimated_amount = forms.DecimalField(
        label="Estimated Amount (MMK)",
        min_value=0,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(
            attrs={
                "class": INPUT_CLASS,
                "min": 0,
                "step": 1,
                "placeholder": "0",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        category_qs = kwargs.pop("category_qs", None)
        super().__init__(*args, **kwargs)
        if category_qs is not None:
            self.fields["category"].queryset = category_qs
        month_id = None
        if self.data.get("budget_month"):
            month_id = self.data.get("budget_month")
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]
        if month_id and category_qs is None:
            self.fields["category"].queryset = BudgetCategory.objects.filter(
                budget_month_id=month_id
            ).order_by("period_index", "id")
        elif month_id and category_qs is not None:
            self.fields["category"].queryset = category_qs.filter(
                budget_month_id=month_id
            ).order_by("period_index", "id")

    def save(self):
        category = self.cleaned_data["category"]
        category.estimated_amount = to_decimal(
            self.cleaned_data.get("estimated_amount", 0)
        )
        category.save(update_fields=["estimated_amount"])

        month = category.budget_month
        month.total_money = sum(
            (item.estimated_amount for item in month.categories.all()),
            Decimal("0"),
        )
        month.save(update_fields=["total_money"])
        return category


class PeriodBudgetForm(forms.Form):
    """Set estimated amounts for all categories of a budget month.

    This is the main budget-estimate form. It handles the three default
    period categories plus custom categories (name + amount), and updates
    the budget month's total money to be the sum of all category amounts.
    """

    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.select_related("user").all(),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        super().__init__(*args, **kwargs)
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs
        for period in PERIOD_DEFINITIONS:
            self.fields[f"period_{period.index}"] = forms.DecimalField(
                label=period.name,
                min_value=0,
                max_digits=12,
                decimal_places=0,
                widget=forms.NumberInput(
                    attrs={
                        "class": INPUT_CLASS,
                        "min": 0,
                        "step": 1,
                        "placeholder": "0",
                    }
                ),
            )

        # Custom category slots (name + amount).
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            self.fields[f"custom_name_{i}"] = forms.CharField(
                label=f"Custom Category {i} Name",
                required=False,
                max_length=100,
                widget=forms.TextInput(
                    attrs={
                        "class": INPUT_CLASS,
                        "placeholder": f"e.g. Shopping {i}",
                    }
                ),
            )
            self.fields[f"custom_amount_{i}"] = forms.DecimalField(
                label=f"Custom Category {i} Amount (MMK)",
                required=False,
                min_value=0,
                max_digits=12,
                decimal_places=0,
                initial=0,
                widget=forms.NumberInput(
                    attrs={
                        "class": INPUT_CLASS,
                        "min": 0,
                        "step": 1,
                        "placeholder": "0",
                    }
                ),
            )

    def clean(self):
        cleaned = super().clean()

        # Validate custom category slots: name without amount (or vice versa).
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            name = cleaned.get(f"custom_name_{i}")
            amount = cleaned.get(f"custom_amount_{i}")
            if name and not amount:
                self.add_error(
                    f"custom_amount_{i}",
                    "Enter an amount for this category.",
                )
            if amount and not name:
                self.add_error(
                    f"custom_name_{i}",
                    "Enter a name for this category.",
                )

        # Compute total money as the sum of all category amounts.
        total = Decimal("0")
        for period in PERIOD_DEFINITIONS:
            total += to_decimal(cleaned.get(f"period_{period.index}", 0))
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            total += to_decimal(cleaned.get(f"custom_amount_{i}", 0))

        if total <= 0:
            raise ValidationError(
                "Total budget must be greater than zero. "
                "Enter amounts for at least one category."
            )

        cleaned["total_money"] = total
        return cleaned

    def save(self):
        from budget.services.category_service import create_default_period_categories

        cleaned = self.cleaned_data
        budget_month = cleaned["budget_month"]

        # Ensure the three default period categories exist.
        create_default_period_categories(budget_month)

        # Set the period category amounts.
        for period in PERIOD_DEFINITIONS:
            category = budget_month.categories.get(period_index=period.index)
            category.estimated_amount = to_decimal(
                cleaned.get(f"period_{period.index}", 0)
            )
            category.save(update_fields=["estimated_amount"])

        # Create or update custom categories.
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            name = cleaned.get(f"custom_name_{i}")
            amount = cleaned.get(f"custom_amount_{i}")
            if name:
                BudgetCategory.objects.get_or_create(
                    budget_month=budget_month,
                    name=name,
                    defaults={
                        "estimated_amount": to_decimal(amount),
                        "period_index": None,
                    },
                )

        # Update the budget month's total money to the sum of all categories.
        budget_month.total_money = cleaned["total_money"]
        budget_month.save(update_fields=["total_money"])

        return budget_month


class DailyExpenseForm(forms.ModelForm):
    class Meta:
        model = DailyExpense
        fields = ["budget_month", "category", "expense_date", "amount", "note"]
        widgets = {
            "budget_month": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "expense-budget-month"}
            ),
            "category": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "expense-category"}
            ),
            "expense_date": forms.DateInput(
                attrs={"class": INPUT_CLASS, "type": "date", "id": "expense-date"}
            ),
            "amount": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASS,
                    "min": 1,
                    "step": 1,
                    "placeholder": "5000",
                }
            ),
            "note": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Lunch, transport, etc."}
            ),
        }

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        category_qs = kwargs.pop("category_qs", None)
        super().__init__(*args, **kwargs)
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs
        self.fields["category"].queryset = BudgetCategory.objects.none()
        self.fields["category"].empty_label = "Select category..."

        month_id = None
        if self.data.get("budget_month"):
            month_id = self.data.get("budget_month")
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]

        if month_id:
            base_qs = (
                category_qs
                if category_qs is not None
                else BudgetCategory.objects
            )
            self.fields["category"].queryset = base_qs.filter(
                budget_month_id=month_id
            ).order_by("period_index", "id")

    def clean(self):
        cleaned = super().clean()
        budget_month = cleaned.get("budget_month")
        category = cleaned.get("category")
        expense_date = cleaned.get("expense_date")
        amount = cleaned.get("amount")

        if budget_month and category and category.budget_month_id != budget_month.id:
            raise ValidationError(
                "Selected category does not belong to the chosen budget month."
            )

        if budget_month and expense_date:
            if (
                expense_date.year != budget_month.year
                or expense_date.month != budget_month.month
            ):
                raise ValidationError(
                    "Expense date must fall within the selected budget month."
                )

        if amount is not None and amount <= 0:
            raise ValidationError("Expense amount must be greater than zero.")

        if category and expense_date and category.period_index is not None:
            expected_index = period_index_for_day(expense_date.day)
            if category.period_index != expected_index:
                raise ValidationError(
                    f"Date {expense_date} belongs to "
                    f"'{PERIOD_DEFINITIONS[expected_index - 1].name}', "
                    f"not '{category.name}'."
                )

        return cleaned


class UnexpectedMoneyForm(forms.ModelForm):
    """Record additional money received into a category."""

    new_category_name = forms.CharField(
        required=False,
        max_length=100,
        label="Or create new category",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. Bonus, Refund, Gift",
            }
        ),
    )

    class Meta:
        model = DailyExpense
        fields = ["budget_month", "category", "expense_date", "amount", "note"]
        widgets = {
            "budget_month": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "unexpected-money-budget-month"}
            ),
            "category": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "unexpected-money-category"}
            ),
            "expense_date": forms.DateInput(
                attrs={"class": INPUT_CLASS, "type": "date", "id": "unexpected-money-date"}
            ),
            "amount": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASS,
                    "min": 1,
                    "step": 1,
                    "placeholder": "5000",
                }
            ),
            "note": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Bonus, refund, etc."}
            ),
        }

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        category_qs = kwargs.pop("category_qs", None)
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].queryset = BudgetCategory.objects.none()
        self.fields["category"].empty_label = "Select category..."
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs

        month_id = None
        if self.data.get("budget_month"):
            month_id = self.data.get("budget_month")
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]

        if month_id:
            base_qs = (
                category_qs
                if category_qs is not None
                else BudgetCategory.objects
            )
            self.fields["category"].queryset = base_qs.filter(
                budget_month_id=month_id
            ).order_by("period_index", "id")

    def clean(self):
        cleaned = super().clean()
        budget_month = cleaned.get("budget_month")
        category = cleaned.get("category")
        new_category_name = (cleaned.get("new_category_name") or "").strip()
        expense_date = cleaned.get("expense_date")
        amount = cleaned.get("amount")

        if not category and new_category_name:
            category = BudgetCategory.objects.filter(
                budget_month=budget_month,
                name=new_category_name,
            ).first()
            if category is None and budget_month:
                category = BudgetCategory.objects.create(
                    budget_month=budget_month,
                    name=new_category_name,
                    estimated_amount=Decimal("0"),
                    period_index=None,
                )
            cleaned["category"] = category
            category = cleaned["category"]

        if not category:
            raise ValidationError(
                "Select an existing category or enter a new category name."
            )

        if budget_month and category and category.budget_month_id != budget_month.id:
            raise ValidationError(
                "Selected category does not belong to the chosen budget month."
            )

        if budget_month and expense_date:
            if (
                expense_date.year != budget_month.year
                or expense_date.month != budget_month.month
            ):
                raise ValidationError(
                    "Unexpected money date must fall within the selected budget month."
                )

        if amount is not None and amount <= 0:
            raise ValidationError("Unexpected money amount must be greater than zero.")

        if category and expense_date and category.period_index is not None:
            expected_index = period_index_for_day(expense_date.day)
            if category.period_index != expected_index:
                raise ValidationError(
                    f"Date {expense_date} belongs to "
                    f"'{PERIOD_DEFINITIONS[expected_index - 1].name}', "
                    f"not '{category.name}'."
                )

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.category:
            new_category_name = (self.cleaned_data.get("new_category_name") or "").strip()
            if new_category_name:
                category = BudgetCategory.objects.filter(
                    budget_month=instance.budget_month,
                    name=new_category_name,
                ).first()
                if category is None and instance.budget_month:
                    category = BudgetCategory.objects.create(
                        budget_month=instance.budget_month,
                        name=new_category_name,
                        estimated_amount=Decimal("0"),
                        period_index=None,
                    )
                instance.category = category

        instance.amount = -abs(instance.amount)
        if commit:
            instance.save()
        return instance


class ReportSelectForm(forms.Form):
    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.none(),
        empty_label="Select a month...",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", None)
        super().__init__(*args, **kwargs)
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs


def to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("1"))