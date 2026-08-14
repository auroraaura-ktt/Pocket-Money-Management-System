"""Django forms for Pocket Money Management System."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from budget.periods import PERIOD_DEFINITIONS, period_index_for_day

from .models import BudgetCategory, BudgetMonth, DailyExpense, PocketUser

INPUT_CLASS = (
    "w-full px-4 py-2.5 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)


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


class BudgetMonthForm(forms.ModelForm):
    month = forms.TypedChoiceField(
        choices=[],
        coerce=int,
        empty_value=None,
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    class Meta:
        model = BudgetMonth
        fields = ["user", "year", "month", "total_money"]
        widgets = {
            "user": forms.Select(attrs={"class": INPUT_CLASS}),
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
        super().__init__(*args, **kwargs)
        from django.conf import settings
        from django.utils import timezone

        self.fields["month"].choices = [
            (i, settings.MONTH_NAMES[i]) for i in range(1, 13)
        ]
        if not self.is_bound and not self.instance.pk:
            now = timezone.now()
            self.fields["year"].initial = now.year
            self.fields["month"].initial = now.month


class PeriodBudgetForm(forms.Form):
    """Set estimated amounts for the three default period categories."""

    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.select_related("user").all(),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def save(self):
        from budget.services.category_service import create_default_period_categories

        budget_month = self.cleaned_data["budget_month"]
        create_default_period_categories(budget_month)
        for period in PERIOD_DEFINITIONS:
            amount = to_decimal(self.cleaned_data.get(f"period_{period.index}", 0))
            category = budget_month.categories.get(period_index=period.index)
            category.estimated_amount = amount
            category.save(update_fields=["estimated_amount"])


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
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = BudgetCategory.objects.none()
        self.fields["category"].empty_label = "Select period..."

        month_id = None
        if self.data.get("budget_month"):
            month_id = self.data.get("budget_month")
        elif self.initial.get("budget_month"):
            month_id = self.initial["budget_month"]

        if month_id:
            self.fields["category"].queryset = BudgetCategory.objects.filter(
                budget_month_id=month_id
            ).order_by("period_index")

    def clean(self):
        cleaned = super().clean()
        budget_month = cleaned.get("budget_month")
        category = cleaned.get("category")
        expense_date = cleaned.get("expense_date")

        if budget_month and category and category.budget_month_id != budget_month.id:
            raise ValidationError(
                "Selected period category does not belong to the chosen budget month."
            )

        if budget_month and expense_date:
            if (
                expense_date.year != budget_month.year
                or expense_date.month != budget_month.month
            ):
                raise ValidationError(
                    "Expense date must fall within the selected budget month."
                )

        if category and expense_date:
            expected_index = period_index_for_day(expense_date.day)
            if category.period_index != expected_index:
                raise ValidationError(
                    f"Date {expense_date} belongs to "
                    f"'{PERIOD_DEFINITIONS[expected_index - 1].name}', "
                    f"not '{category.name}'."
                )

        return cleaned


class ReportSelectForm(forms.Form):
    budget_month = forms.ModelChoiceField(
        queryset=BudgetMonth.objects.select_related("user").all(),
        empty_label="Select a month...",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )


def to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("1"))
