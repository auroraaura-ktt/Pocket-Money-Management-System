"""Report generation: compare category budgets vs actual spending."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from budget.currency import to_mmk
from budget.models import BudgetMonth, DailyExpense
from budget.periods import PERIOD_DEFINITIONS, period_date_range, period_end_day


@dataclass
class CategoryComparison:
    category_id: int | None
    category_name: str
    start_day: int | None
    end_day: int | None
    budget_amount: Decimal
    actual_amount: Decimal
    difference: Decimal
    status: str  # "surplus" or "deficit"


@dataclass
class MonthlyReport:
    year: int
    month: int
    total_money: Decimal
    total_budget_estimated: Decimal
    total_actual_spent: Decimal
    overall_difference: Decimal
    overall_status: str
    period_reports: list[CategoryComparison]


def _build_comparison(
    category_name: str,
    start_day: int | None,
    end_day: int | None,
    budget_amount: Decimal,
    actual_amount: Decimal,
) -> CategoryComparison:
    budget_amount = to_mmk(budget_amount)
    actual_amount = to_mmk(actual_amount)
    difference = budget_amount - actual_amount
    if actual_amount < 0:
        status = "deficit"
    else:
        status = "surplus" if difference >= 0 else "deficit"
    return CategoryComparison(
        category_id=None,
        category_name=category_name,
        start_day=start_day,
        end_day=end_day,
        budget_amount=budget_amount,
        actual_amount=actual_amount,
        difference=abs(difference),
        status=status,
    )


def _get_total_actual_spent(
    budget_month_id: int,
    category_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Decimal:
    """Direct sum of daily expenses in the date range (optionally by category)."""
    qs = DailyExpense.objects.filter(budget_month_id=budget_month_id)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if start_date:
        qs = qs.filter(expense_date__gte=start_date)
    if end_date:
        qs = qs.filter(expense_date__lte=end_date)
    total = qs.aggregate(total=Sum("amount"))["total"]
    return to_mmk(total)


def generate_monthly_report(budget_month_id: int) -> MonthlyReport:
    """Compare each category budget estimate against actual spending."""
    budget_month = (
        BudgetMonth.objects.select_related("user")
        .prefetch_related("categories")
        .filter(pk=budget_month_id)
        .first()
    )
    if not budget_month:
        raise ValueError(f"Budget month {budget_month_id} not found")

    categories = list(budget_month.categories.all())
    categories_by_period = {
        cat.period_index: cat for cat in categories if cat.period_index is not None
    }
    custom_categories = [cat for cat in categories if cat.period_index is None]

    period_reports: list[CategoryComparison] = []
    total_budget_estimated = Decimal("0")
    total_actual_spent = Decimal("0")

    # Period categories (Days 1-10, 11-20, 21-end)
    for period in PERIOD_DEFINITIONS:
        category = categories_by_period.get(period.index)
        budget_amount = to_mmk(category.estimated_amount if category else 0)
        start_date, end_date = period_date_range(
            budget_month.year, budget_month.month, period.index
        )
        actual_amount = _get_total_actual_spent(
            budget_month.id,
            category_id=category.id if category else None,
            start_date=start_date,
            end_date=end_date,
        )
        end_day = period_end_day(budget_month.year, budget_month.month, period.index)

        comparison = _build_comparison(
            period.name,
            period.start_day,
            end_day,
            budget_amount,
            actual_amount,
        )
        comparison.category_id = category.id if category else None
        period_reports.append(comparison)
        total_budget_estimated += budget_amount
        total_actual_spent += actual_amount

    # Custom categories (whole month)
    for category in custom_categories:
        budget_amount = to_mmk(category.estimated_amount)
        actual_amount = _get_total_actual_spent(
            budget_month.id, category_id=category.id
        )
        comparison = _build_comparison(
            category.name,
            None,
            None,
            budget_amount,
            actual_amount,
        )
        comparison.category_id = category.id
        period_reports.append(comparison)
        total_budget_estimated += budget_amount
        total_actual_spent += actual_amount

    total_budget_estimated = to_mmk(total_budget_estimated)
    total_actual_spent = to_mmk(total_actual_spent)
    total_money = to_mmk(budget_month.total_money)
    overall_diff = total_money - total_actual_spent

    return MonthlyReport(
        year=budget_month.year,
        month=budget_month.month,
        total_money=total_money,
        total_budget_estimated=total_budget_estimated,
        total_actual_spent=total_actual_spent,
        overall_difference=abs(overall_diff),
        overall_status="surplus" if overall_diff >= 0 else "deficit",
        period_reports=period_reports,
    )