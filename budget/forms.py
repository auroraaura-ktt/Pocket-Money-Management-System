"""Django forms for Pocket Money Management System."""

from decimal import Decimal
from calendar import monthrange
from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from budget.periods import PERIOD_DEFINITIONS, period_index_for_day

from .models import (
    Balance,
    BudgetCategory,
    BudgetMonth,
    DailyExpense,
    PocketUser,
    SubBalance,
)

INPUT_CLASS = (
    "w-full px-4 py-2.5 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)

# Number of custom category slots in the budget-estimate form.
CUSTOM_CATEGORY_SLOTS = 5


def _primary_key_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


class AdminResetPasswordForm(forms.Form):
    """Set a new password for a user's login account (admin action).

    Only PocketUsers that actually have a linked ``auth_user`` account can be
    reset, since there is nothing to reset for legacy rows without one.
    """

    user = forms.ModelChoiceField(
        queryset=PocketUser.objects.none(),
        label="User",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = (
            PocketUser.objects.filter(auth_user__isnull=False)
            .select_related("auth_user")
            .order_by("name")
        )
        self.fields["password1"].help_text = (
            "Your password must contain at least 8 characters and cannot be "
            "entirely numeric or too common."
        )
        self.fields["password2"].help_text = ""
        self.fields["password1"].label = "New password"
        self.fields["password2"].label = "Confirm new password"

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        user_obj = self.cleaned_data.get("user")
        if user_obj is not None and user_obj.auth_user is not None:
            try:
                validate_password(password1, user=user_obj.auth_user)
            except ValidationError as exc:
                raise forms.ValidationError(exc.messages)
        return password1

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two password fields didn't match.")
        return cleaned


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

    name = forms.CharField(
        label="Main Balance name",
        max_length=100,
        strip=True,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "e.g. October 2026", "required": True}
        ),
    )

    balance = forms.ModelChoiceField(
        queryset=Balance.objects.none(),
        empty_label="Select a wallet group...",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    class Meta:
        model = BudgetMonth
        fields = ["name", "balance", "year", "month", "total_money"]
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

    total_money = forms.DecimalField(
        min_value=1,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(
            attrs={"class": INPUT_CLASS, "min": 1, "step": 1, "required": True}
        ),
    )

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

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Enter a Main Balance name.")
        return name

    def clean_year(self):
        year = self.cleaned_data["year"]
        if year < 2020 or year > 2100:
            raise forms.ValidationError("Enter a year from 2020 through 2100.")
        return year

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


class MainBalanceUpdateForm(forms.ModelForm):
    """Edit a monthly Main Balance without allowing its period to move."""

    class Meta:
        model = BudgetMonth
        fields = ["name", "total_money"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "total_money": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 1, "step": 1}
            ),
        }

    total_money = forms.DecimalField(
        min_value=1,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "step": 1}),
    )

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Enter a Main Balance name.")
        return value


class SubBalanceForm(forms.ModelForm):
    """Manage a named monthly child while preserving its parent and date."""

    balance_amount = forms.DecimalField(
        label="Opening Balance Amount (MMK)", required=False, min_value=0,
        max_digits=12, decimal_places=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": 1}),
    )
    balance_date = forms.DateField(
        label="Date",
        required=True,
        widget=forms.DateInput(
            attrs={"class": INPUT_CLASS, "type": "date", "id": "sub-balance-date"}
        ),
    )

    class Meta:
        model = SubBalance
        fields = ["name", "balance_amount", "balance_date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.parent_month = kwargs.pop("parent_month", None)
        month_qs = kwargs.pop("month_qs", None)
        super().__init__(*args, **kwargs)
        self.fields["name"].max_length = 100
        if self.parent_month is None:
            self.fields["budget_month"] = forms.ModelChoiceField(
                queryset=month_qs if month_qs is not None else BudgetMonth.objects.none(),
                empty_label="Select a Main Balance...",
                label="Main Balance",
                widget=forms.Select(
                    attrs={"class": INPUT_CLASS, "id": "sub-balance-budget-month"}
                ),
            )
            selected_month = (
                self.fields["budget_month"].queryset.filter(
                    pk=_primary_key_or_none(self.data.get("budget_month"))
                ).first()
                if self.is_bound and self.data.get("budget_month")
                else self.fields["budget_month"].queryset.first()
            )
        else:
            selected_month = self.parent_month

        if selected_month:
            date_attrs = self.fields["balance_date"].widget.attrs
            min_date = date(selected_month.year, selected_month.month, 1)
            max_date = date(
                selected_month.year,
                selected_month.month,
                monthrange(selected_month.year, selected_month.month)[1],
            )
            date_attrs.update({"min": min_date.isoformat(), "max": max_date.isoformat()})
            if not self.is_bound and not self.instance.pk:
                self.initial.setdefault("balance_date", min_date)
        elif not self.is_bound and self.instance.pk and self.instance.balance_date:
            self.initial["balance_date"] = self.instance.balance_date

    def clean(self):
        cleaned = super().clean()
        balance_amount = cleaned.get("balance_amount")
        if balance_amount is None:
            cleaned["balance_amount"] = Decimal("0")
        budget_month = self.parent_month or cleaned.get("budget_month")
        balance_date = cleaned.get("balance_date")
        if budget_month and balance_date and (
            balance_date.year != budget_month.year
            or balance_date.month != budget_month.month
        ):
            self.add_error(
                "balance_date",
                "Date must fall within the selected Main Balance month and year.",
            )
        return cleaned

    def clean_balance_amount(self):
        return self.cleaned_data.get("balance_amount") or Decimal("0")

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Enter a Sub Balance name.")
        return value

    def save(self, budget_month=None, commit=True):
        instance = super().save(commit=False)
        instance.budget_month = (
            budget_month
            or self.parent_month
            or self.cleaned_data.get("budget_month")
        )
        if commit:
            instance.save()
        return instance


class ExpenseCategoryCreateForm(forms.Form):
    """Create an owner-scoped custom category for Main or Sub-Balance spending."""

    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.none(),
        label="Main Balance month",
        widget=forms.Select(
            attrs={"class": INPUT_CLASS, "id": "expense-category-budget-month"}
        ),
    )
    sub_balance = forms.ModelChoiceField(
        queryset=SubBalance.objects.none(),
        label="Sub-Balance",
        required=False,
        widget=forms.Select(
            attrs={"class": INPUT_CLASS, "id": "expense-category-sub-balance"}
        ),
    )
    name = forms.CharField(
        max_length=100,
        strip=True,
        label="Category name",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "id": "expense-category-name",
                "placeholder": "e.g. Food, Transportation, or any custom category",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        month_qs = kwargs.pop("month_qs", BudgetMonth.objects.none())
        sub_balance_qs = kwargs.pop("sub_balance_qs", SubBalance.objects.none())
        self.sub_balance_required = kwargs.pop("sub_balance_required", False)
        initial = kwargs.get("initial") or {}
        super().__init__(*args, **kwargs)
        self.fields["budget_month"].queryset = month_qs
        self.fields["sub_balance"].queryset = sub_balance_qs
        self.fields["sub_balance"].required = self.sub_balance_required
        month_id = (
            self.data.get("budget_month")
            if self.is_bound
            else getattr(initial.get("budget_month"), "pk", initial.get("budget_month"))
        )
        if month_id:
            month_id = _primary_key_or_none(month_id)
            if month_id is not None:
                self.fields["sub_balance"].queryset = sub_balance_qs.filter(
                    budget_month_id=month_id
                ).order_by("name", "id")

    def clean(self):
        cleaned = super().clean()
        budget_month = cleaned.get("budget_month")
        sub_balance = cleaned.get("sub_balance")
        name = cleaned.get("name")
        if sub_balance and budget_month and sub_balance.budget_month_id != budget_month.id:
            self.add_error(
                "sub_balance",
                "Choose a Sub-Balance that belongs to the selected Main Balance month.",
            )
        if self.sub_balance_required and not sub_balance:
            self.add_error("sub_balance", "Select a Sub-Balance for this category.")
        if budget_month and name:
            duplicate = BudgetCategory.objects.filter(
                budget_month=budget_month,
                name__iexact=name,
            ).exists()
            if duplicate:
                self.add_error(
                    "name",
                    "A category with this name already exists in this Main Balance month.",
                )
        return cleaned

    def save(self):
        return BudgetCategory.objects.create(
            budget_month=self.cleaned_data["budget_month"],
            sub_balance=self.cleaned_data["sub_balance"],
            name=self.cleaned_data["name"],
            estimated_amount=Decimal("0"),
            period_index=None,
        )


class SubBalanceTransactionForm(forms.ModelForm):
    class Meta:
        model = DailyExpense
        fields = ["category", "expense_date", "amount", "note"]
        widgets = {
            "category": forms.Select(attrs={"class": INPUT_CLASS}),
            "expense_date": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "step": 1}),
            "note": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.sub_balance = kwargs.pop("sub_balance")
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = self.sub_balance.categories.all()
        self.instance.budget_month = self.sub_balance.budget_month
        self.instance.sub_balance = self.sub_balance

    def clean(self):
        cleaned = super().clean()
        category, expense_date, amount = (cleaned.get("category"), cleaned.get("expense_date"), cleaned.get("amount"))
        month = self.sub_balance.budget_month
        if category and category.sub_balance_id != self.sub_balance.id:
            self.add_error("category", "Select a category belonging to this Sub Balance.")
        if expense_date and (expense_date.year, expense_date.month) != (month.year, month.month):
            self.add_error("expense_date", "Transaction date must fall within the parent Main Balance month.")
        if amount is not None and amount <= 0:
            self.add_error("amount", "Transaction amount must be greater than zero.")
        return cleaned

    def save(self, commit=True):
        expense = super().save(commit=False)
        expense.budget_month = self.sub_balance.budget_month
        expense.sub_balance = self.sub_balance
        if commit:
            expense.save()
        return expense


class SubBalanceCategoryForm(forms.ModelForm):
    class Meta:
        model = BudgetCategory
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Food"}),
        }

    def __init__(self, *args, **kwargs):
        self.sub_balance = kwargs.pop("sub_balance")
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = BudgetCategory.objects.filter(
            budget_month=self.sub_balance.budget_month,
            name__iexact=name,
        )
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This category already exists in this Sub Balance.")
        return name

    def save(self, commit=True):
        category = super().save(commit=False)
        category.budget_month = self.sub_balance.budget_month
        category.sub_balance = self.sub_balance
        category.period_index = None
        if category.estimated_amount is None:
            category.estimated_amount = Decimal("0")
        if commit:
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
        required=False,
    )
    new_category_name = forms.CharField(
        label="Or Write a New Category Name",
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. Transport, Books...",
            }
        ),
    )
    estimated_amount = forms.DecimalField(
        label="Budget Amount (MMK)",
        required=False,
        initial=0,
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

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        new_category_name = (cleaned.get("new_category_name") or "").strip()
        budget_month = cleaned.get("budget_month")

        # A category must come from the dropdown OR be written manually.
        if category is None and not new_category_name:
            self.add_error(
                "category",
                "Select an existing custom category or write a new category name.",
            )
            return cleaned

        # If a name was typed manually, make sure it does not already exist
        # in the selected budget month (unique per month).
        if new_category_name and budget_month:
            existing = BudgetCategory.objects.filter(
                budget_month=budget_month, name=new_category_name
            ).first()
            if existing is not None:
                # Reuse the existing category instead of erroring out.
                cleaned["category"] = existing
            else:
                cleaned["new_category_name"] = new_category_name

        return cleaned

    def save(self):
        category = self.cleaned_data.get("category")
        new_category_name = self.cleaned_data.get("new_category_name")
        budget_month = self.cleaned_data["budget_month"]

        # Create the category manually written by the user if needed.
        if category is None and new_category_name:
            category, _ = BudgetCategory.objects.get_or_create(
                budget_month=budget_month,
                name=new_category_name,
                defaults={
                    "estimated_amount": Decimal("0"),
                    "period_index": None,
                },
            )

        category.estimated_amount = to_decimal(
            self.cleaned_data.get("estimated_amount", 0)
        )
        category.save(update_fields=["estimated_amount"])

        # Update only the category's estimated amount. The budget month's
        # total_money (main amount) must NOT be recalculated here — it is the
        # amount the user set when creating the budget month.
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

        # Update only the category's estimated amount. The budget month's
        # total_money (main amount) must NOT be recalculated here — it is the
        # amount the user set when creating the budget month.
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
                required=False,
                initial=0,
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

        # Validate custom category slots: amount without a name is an error.
        # (Names with empty amounts are allowed — estimated amounts are
        # optional; empty values are treated as 0.)
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            name = cleaned.get(f"custom_name_{i}")
            amount = cleaned.get(f"custom_amount_{i}")
            if amount and not name:
                self.add_error(
                    f"custom_name_{i}",
                    "Enter a name for this category.",
                )

        # Compute total money as the sum of all category amounts.
        # All amounts are optional; empty/default categories simply count as 0.
        total = Decimal("0")
        for period in PERIOD_DEFINITIONS:
            total += to_decimal(cleaned.get(f"period_{period.index}", 0))
        for i in range(1, CUSTOM_CATEGORY_SLOTS + 1):
            total += to_decimal(cleaned.get(f"custom_amount_{i}", 0))

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

        return budget_month


class DailyExpenseForm(forms.ModelForm):
    class Meta:
        model = DailyExpense
        fields = [
            "budget_month",
            "sub_balance",
            "category",
            "expense_date",
            "amount",
            "note",
        ]
        widgets = {
            "budget_month": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "expense-budget-month"}
            ),
            "sub_balance": forms.Select(
                attrs={"class": INPUT_CLASS, "id": "expense-sub-balance"}
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
        sub_balance_qs = kwargs.pop("sub_balance_qs", None)
        sub_balance_required = kwargs.pop("sub_balance_required", False)
        super().__init__(*args, **kwargs)
        if month_qs is not None:
            self.fields["budget_month"].queryset = month_qs
        self.fields["sub_balance"].queryset = SubBalance.objects.none()
        self.fields["sub_balance"].required = sub_balance_required
        self.fields["sub_balance"].empty_label = "Select a Sub-Balance..."
        self.fields["category"].queryset = BudgetCategory.objects.none()
        self.fields["category"].empty_label = "Select category..."

        month_id = None
        if self.is_bound and self.data.get("budget_month"):
            month_id = _primary_key_or_none(self.data.get("budget_month"))
        elif not self.is_bound and self.instance.pk:
            month_id = self.instance.budget_month_id
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]

        sub_balance_id = None
        if self.is_bound:
            sub_balance_id = _primary_key_or_none(self.data.get("sub_balance"))
        elif self.instance.pk:
            sub_balance_id = self.instance.sub_balance_id
        elif self.initial.get("sub_balance"):
            sub_balance_id = self.initial["sub_balance"]

        if month_id:
            base_sub_balances = (
                sub_balance_qs
                if sub_balance_qs is not None
                else SubBalance.objects.none()
            )
            self.fields["sub_balance"].queryset = base_sub_balances.filter(
                budget_month_id=month_id
            ).order_by("name", "id")

        if month_id:
            base_qs = (
                category_qs
                if category_qs is not None
                else BudgetCategory.objects
            )
            category_filters = {"budget_month_id": month_id}
            if sub_balance_id:
                category_filters["sub_balance_id"] = sub_balance_id
            else:
                category_filters["sub_balance__isnull"] = True
            self.fields["category"].queryset = base_qs.filter(
                **category_filters
            ).order_by("period_index", "id")

    def clean(self):
        cleaned = super().clean()
        budget_month = cleaned.get("budget_month")
        sub_balance = cleaned.get("sub_balance")
        category = cleaned.get("category")
        expense_date = cleaned.get("expense_date")
        amount = cleaned.get("amount")

        if sub_balance and budget_month and sub_balance.budget_month_id != budget_month.id:
            self.add_error(
                "sub_balance",
                "Selected Sub-Balance does not belong to the chosen Main Balance month.",
            )

        if budget_month and category and category.budget_month_id != budget_month.id:
            self.add_error(
                "category",
                "Selected category does not belong to the chosen budget month.",
            )

        if category and sub_balance and category.sub_balance_id != sub_balance.id:
            self.add_error(
                "category",
                "Select a category belonging to the chosen Sub-Balance.",
            )
        elif category and not sub_balance and category.sub_balance_id:
            self.add_error(
                "category",
                "Sub-Balance categories cannot be used for Main Balance spending.",
            )

        if budget_month and expense_date:
            if (
                expense_date.year != budget_month.year
                or expense_date.month != budget_month.month
            ):
                self.add_error(
                    "expense_date",
                    "Expense date must fall within the selected budget month.",
                )

        if amount is not None and amount <= 0:
            self.add_error("amount", "Expense amount must be greater than zero.")

        if category and expense_date and category.period_index is not None:
            expected_index = period_index_for_day(expense_date.day)
            if category.period_index != expected_index:
                self.add_error(
                    "category",
                    f"Date {expense_date} belongs to "
                    f"'{PERIOD_DEFINITIONS[expected_index - 1].name}', "
                    f"not '{category.name}'.",
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
                budget_month_id=month_id,
                sub_balance__isnull=True,
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
