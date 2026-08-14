"""Create and manage default period budget categories."""

from budget.models import BudgetCategory, BudgetMonth, DailyExpense
from budget.periods import PERIOD_DEFINITIONS, period_index_for_day


def create_default_period_categories(budget_month: BudgetMonth) -> list[BudgetCategory]:
    """Ensure the three default period categories exist for a budget month."""
    categories = []
    for period in PERIOD_DEFINITIONS:
        category, _ = BudgetCategory.objects.get_or_create(
            budget_month=budget_month,
            period_index=period.index,
            defaults={"name": period.name, "estimated_amount": 0},
        )
        if category.name != period.name:
            category.name = period.name
            category.save(update_fields=["name"])
        categories.append(category)
    return categories


def ensure_all_months_have_period_categories() -> None:
    """Backfill period categories for every existing budget month."""
    for budget_month in BudgetMonth.objects.all():
        create_default_period_categories(budget_month)


def category_for_expense_date(budget_month: BudgetMonth, expense_date) -> BudgetCategory:
    """Return the period category that matches an expense date."""
    period_index = period_index_for_day(expense_date.day)
    return budget_month.categories.get(period_index=period_index)


def reassign_expenses_to_period_categories(budget_month: BudgetMonth) -> None:
    """Move each expense to the period category matching its date."""
    create_default_period_categories(budget_month)
    for expense in DailyExpense.objects.filter(budget_month=budget_month):
        correct_category = category_for_expense_date(budget_month, expense.expense_date)
        if expense.category_id != correct_category.id:
            expense.category = correct_category
            expense.save(update_fields=["category"])
