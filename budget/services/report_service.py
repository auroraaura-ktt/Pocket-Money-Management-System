"""Report generation: compare category budgets vs actual spending."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import F, Sum
from django.db.models.functions import Abs

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
    main_balance_name: str
    total_money: Decimal
    adjusted_total_money: Decimal
    total_budget_estimated: Decimal
    direct_actual_spent: Decimal
    sub_balance_total_actual_spent: Decimal
    combined_actual_spent: Decimal
    main_transaction_count: int
    main_transactions: list[dict]
    remaining_balance: Decimal
    overall_difference: Decimal
    overall_status: str
    period_reports: list[CategoryComparison]
    zero_spend_periods: list[str]
    sub_balance_rows: list[dict]

    @property
    def total_actual_spent(self) -> Decimal:
        """Compatibility alias for the explicitly named combined total."""
        return self.combined_actual_spent


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
    main_only: bool = False,
    sub_balance_id: int | None = None,
) -> Decimal:
    """Direct sum of daily expense amounts in the date range (optionally by category).

    Every expense is treated as money spent, so the amount is summed as its
    magnitude. A negative amount (which is how "extra money" is stored) is
    therefore counted as an outgoing expense rather than adding money back to
    the balance. This keeps remaining_balance = total_money - actual_spent
    correct and non-inflated.
    """
    qs = DailyExpense.objects.filter(budget_month_id=budget_month_id)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if main_only:
        qs = qs.filter(sub_balance__isnull=True)
    elif sub_balance_id is not None:
        qs = qs.filter(sub_balance_id=sub_balance_id)
    if start_date:
        qs = qs.filter(expense_date__gte=start_date)
    if end_date:
        qs = qs.filter(expense_date__lte=end_date)
    total = qs.aggregate(total=Sum(Abs(F("amount"))))["total"]
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
    main_expenses = list(
        DailyExpense.objects.filter(
            budget_month=budget_month,
            sub_balance__isnull=True,
        )
        .select_related("category")
        .order_by("expense_date", "id")
    )
    direct_actual_spent = _get_total_actual_spent(
        budget_month.id,
        main_only=True,
    )
    sub_balance_rows = [
        {
            "id": row.id,
            "name": row.name,
            "balance_amount": to_mmk(row.balance_amount),
            "estimated_amount": (
                to_mmk(row.estimated_amount)
                if row.estimated_amount is not None else None
            ),
            "total_actual_spent": to_mmk(row.total_actual_spent),
            "remaining_balance": to_mmk(row.remaining_balance) if row.remaining_balance is not None else None,
            "transaction_count": row.expenses.filter(
                budget_month=budget_month
            ).count(),
            "categories": [
                {
                    "name": category.name,
                    "budget_amount": to_mmk(category.estimated_amount),
                    "actual_amount": to_mmk(
                        category.expenses.filter(
                            sub_balance=row, budget_month=budget_month
                        ).aggregate(
                            total=Sum(Abs(F("amount")))
                        )["total"]
                    ),
                    "transaction_count": category.expenses.filter(
                        sub_balance=row, budget_month=budget_month
                    ).count(),
                }
                for category in row.categories.all()
            ],
            "transactions": [
                {
                    "date": expense.expense_date,
                    "category_name": expense.category.name,
                    "note": expense.note,
                    "amount": expense.amount,
                }
                for expense in row.expenses.filter(
                    budget_month=budget_month
                ).select_related("category")
            ],
        }
        for row in budget_month.sub_balances.prefetch_related(
            "categories__expenses", "expenses__category"
        ).all()
    ]
    main_categories = [cat for cat in categories if cat.sub_balance_id is None]
    categories_by_period = {
        cat.period_index: cat for cat in main_categories if cat.period_index is not None
    }
    custom_categories = [cat for cat in main_categories if cat.period_index is None]

    period_reports: list[CategoryComparison] = []
    total_budget_estimated = Decimal("0")
    zero_spend_periods: list[str] = []

    # Period categories (Days 1-10, 11-20, 21-end)
    for period in PERIOD_DEFINITIONS:
        category = categories_by_period.get(period.index)
        budget_amount = to_mmk(category.estimated_amount if category else 0)
        start_date, end_date = period_date_range(
            budget_month.year, budget_month.month, period.index
        )
        actual_amount = (
            _get_total_actual_spent(
                budget_month.id,
                category_id=category.id,
                start_date=start_date,
                end_date=end_date,
                main_only=True,
            )
            if category
            else Decimal("0")
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

        # If no money was spent in this period, its budget is added back to available money
        if actual_amount == 0:
            zero_spend_periods.append(period.name)

    # Custom categories (whole month)
    for category in custom_categories:
        budget_amount = to_mmk(category.estimated_amount)
        actual_amount = _get_total_actual_spent(
            budget_month.id, category_id=category.id, main_only=True
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

    total_sub_balance_actual_spent = sum(
        (row["total_actual_spent"] for row in sub_balance_rows),
        Decimal("0"),
    )
    combined_actual_spent = to_mmk(
        direct_actual_spent + total_sub_balance_actual_spent
    )

    total_budget_estimated = to_mmk(total_budget_estimated)
    total_money = to_mmk(budget_month.total_money)

    # For each period with zero spending, subtract its budget from available money
    # (that money is effectively "saved" and remains available)
    zero_spend_budget = Decimal("0")
    for period in PERIOD_DEFINITIONS:
        if period.name in zero_spend_periods:
            category = categories_by_period.get(period.index)
            if category:
                zero_spend_budget += to_mmk(category.estimated_amount)

    adjusted_total_money = to_mmk(total_money - zero_spend_budget)
    overall_diff = adjusted_total_money - direct_actual_spent

    # Main Balance remaining funds continue to exclude separately allocated Sub-Balances.
    remaining_balance = to_mmk(total_money - direct_actual_spent)

    main_transactions = [
        {
            "date": expense.expense_date,
            "category_name": expense.category.name,
            "note": expense.note,
            "amount": expense.amount,
        }
        for expense in main_expenses
    ]

    return MonthlyReport(
        year=budget_month.year,
        month=budget_month.month,
        main_balance_name=budget_month.name or budget_month.month_label,
        total_money=total_money,
        adjusted_total_money=adjusted_total_money,
        total_budget_estimated=total_budget_estimated,
        direct_actual_spent=direct_actual_spent,
        sub_balance_total_actual_spent=total_sub_balance_actual_spent,
        combined_actual_spent=combined_actual_spent,
        main_transaction_count=len(main_transactions),
        main_transactions=main_transactions,
        remaining_balance=remaining_balance,
        overall_difference=abs(overall_diff),
        overall_status="surplus" if overall_diff >= 0 else "deficit",
        period_reports=period_reports,
        zero_spend_periods=zero_spend_periods,
        sub_balance_rows=sub_balance_rows,
    )
