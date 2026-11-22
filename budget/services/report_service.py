"""Report generation: compare category budgets vs actual spending."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import Abs

from budget.currency import to_mmk
from budget.models import BudgetCategory, BudgetMonth, DailyExpense, InputBalance, SubBalance
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
    income_by_category: list[dict]
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
    budget_month_id: int | list[int],
    category_id: int | list[int] | None = None,
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
    if isinstance(budget_month_id, list):
        qs = DailyExpense.objects.filter(budget_month_id__in=budget_month_id)
    else:
        qs = DailyExpense.objects.filter(budget_month_id=budget_month_id)
    if category_id:
        if isinstance(category_id, list):
            qs = qs.filter(category_id__in=category_id)
        else:
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


def generate_monthly_report(
    budget_month_id: int, *, aggregate_same_month: bool = True
) -> MonthlyReport:
    """Compare each category budget estimate against actual spending."""
    budget_month = (
        BudgetMonth.objects.select_related("user")
        .prefetch_related("categories")
        .filter(pk=budget_month_id)
        .first()
    )
    if not budget_month:
        raise ValueError(f"Budget month {budget_month_id} not found")

    monthly_months = BudgetMonth.objects.filter(
        user=budget_month.user,
        year=budget_month.year,
        month=budget_month.month,
    )
    if not aggregate_same_month:
        monthly_months = monthly_months.filter(pk=budget_month.pk)
    monthly_month_ids = list(monthly_months.values_list("id", flat=True))
    categories = list(
        BudgetCategory.objects.filter(budget_month_id__in=monthly_month_ids)
        .select_related("sub_balance")
        .order_by("period_index", "id")
    )
    main_expenses = list(
        DailyExpense.objects.filter(
            budget_month_id__in=monthly_month_ids,
            sub_balance__isnull=True,
        )
        .select_related("category")
        .order_by("expense_date", "id")
    )
    direct_actual_spent = _get_total_actual_spent(
        monthly_month_ids,
        main_only=True,
    )
    income_totals: dict[str, dict] = {}
    for income in InputBalance.objects.filter(
        budget_month_id__in=monthly_month_ids
    ).values("category").annotate(amount=Sum("amount"), entry_count=Count("id")):
        category_name = income["category"] or "Income"
        category_total = income_totals.setdefault(
            category_name,
            {"category_name": category_name, "amount": Decimal("0"), "entry_count": 0},
        )
        category_total["amount"] += income["amount"] or Decimal("0")
        category_total["entry_count"] += income["entry_count"]
    income_by_category = sorted(
        (
            {
                **item,
                "amount": to_mmk(item["amount"]),
            }
            for item in income_totals.values()
        ),
        key=lambda item: item["category_name"].casefold(),
    )

    sub_balance_rows = []
    for row in SubBalance.objects.filter(
        budget_month_id__in=monthly_month_ids
    ).prefetch_related(
        "categories__expenses", "expenses__category", "allocations"
    ):
        row_expenses = list(
            row.expenses.filter(
                budget_month_id__in=monthly_month_ids
            ).select_related("category")
        )
        actual_by_category: dict[str, Decimal] = {}
        for expense in row_expenses:
            category_name = expense.category.name
            actual_by_category[category_name] = (
                actual_by_category.get(category_name, Decimal("0"))
                + abs(expense.amount)
            )

        if row.allocation_mode == SubBalance.INHERIT_MAIN_TOTAL:
            balance_categories = [
                {
                    "name": category.name,
                    "basis": "Budget estimate",
                    "allocated_amount": to_mmk(category.estimated_amount),
                    "actual_amount": to_mmk(actual_by_category.get(category.name, 0)),
                    "remaining_balance": to_mmk(
                        category.estimated_amount
                        - actual_by_category.get(category.name, Decimal("0"))
                    ),
                }
                for category in row.categories.all()
            ]
        else:
            allocation_by_category: dict[str, Decimal] = {}
            for allocation in row.allocations.all():
                allocation_by_category[allocation.category] = (
                    allocation_by_category.get(allocation.category, Decimal("0"))
                    + allocation.amount
                )
            balance_categories = []
            for category_name in sorted(
                set(allocation_by_category) | set(actual_by_category),
                key=str.casefold,
            ):
                allocated_amount = allocation_by_category.get(
                    category_name, Decimal("0")
                )
                actual_amount = actual_by_category.get(category_name, Decimal("0"))
                balance_categories.append(
                    {
                        "name": category_name,
                        "basis": "Category allocation",
                        "allocated_amount": to_mmk(allocated_amount),
                        "actual_amount": to_mmk(actual_amount),
                        "remaining_balance": to_mmk(
                            allocated_amount - actual_amount
                        ),
                    }
                )
            legacy_amount = (
                row.balance_amount
                if row.balance_amount
                else (row.estimated_amount or Decimal("0"))
            )
            if legacy_amount:
                balance_categories.insert(
                    0,
                    {
                        "name": "Uncategorized opening balance",
                        "basis": "Opening balance",
                        "allocated_amount": to_mmk(legacy_amount),
                        "actual_amount": Decimal("0"),
                        "remaining_balance": to_mmk(legacy_amount),
                    },
                )

        sub_balance_rows.append({
            "id": row.id,
            "name": row.name,
            "allocation_mode": row.allocation_mode,
            "balance_amount": to_mmk(row.balance_amount),
            "allocated_amount": to_mmk(row.allocated_amount),
            "estimated_amount": (
                to_mmk(row.estimated_amount)
                if row.estimated_amount is not None else None
            ),
            "total_actual_spent": to_mmk(row.total_actual_spent),
            "remaining_balance": to_mmk(row.remaining_balance) if row.remaining_balance is not None else None,
            "transaction_count": row.expenses.filter(
                budget_month_id__in=monthly_month_ids
            ).count(),
            "categories": [
                {
                    "name": category.name,
                    "budget_amount": to_mmk(category.estimated_amount),
                    "actual_amount": to_mmk(
                        category.expenses.filter(
                            sub_balance=row,
                            budget_month_id__in=monthly_month_ids,
                        ).aggregate(
                            total=Sum(Abs(F("amount")))
                        )["total"]
                    ),
                    "transaction_count": category.expenses.filter(
                        sub_balance=row,
                        budget_month_id__in=monthly_month_ids,
                    ).count(),
                }
                for category in row.categories.all()
            ],
            "balance_categories": balance_categories,
            "transactions": [
                {
                    "date": expense.expense_date,
                    "category_name": expense.category.name,
                    "note": expense.note,
                    "amount": expense.amount,
                }
                for expense in row_expenses
            ],
        })
    main_categories = [cat for cat in categories if cat.sub_balance_id is None]
    categories_by_period = {
        index: [cat for cat in main_categories if cat.period_index == index]
        for index in {cat.period_index for cat in main_categories if cat.period_index is not None}
    }
    custom_categories = [cat for cat in main_categories if cat.period_index is None]

    period_reports: list[CategoryComparison] = []
    total_budget_estimated = Decimal("0")
    zero_spend_periods: list[str] = []

    # Period categories (Days 1-10, 11-20, 21-end)
    for period in PERIOD_DEFINITIONS:
        period_categories = categories_by_period.get(period.index, [])
        budget_amount = to_mmk(
            sum((category.estimated_amount for category in period_categories), Decimal("0"))
        )
        start_date, end_date = period_date_range(
            budget_month.year, budget_month.month, period.index
        )
        actual_amount = (
            _get_total_actual_spent(
                monthly_month_ids,
                category_id=[category.id for category in period_categories],
                start_date=start_date,
                end_date=end_date,
                main_only=True,
            )
            if period_categories
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
        comparison.category_id = (
            period_categories[0].id if period_categories else None
        )
        period_reports.append(comparison)
        total_budget_estimated += budget_amount

        # If no money was spent in this period, its budget is added back to available money
        if actual_amount == 0:
            zero_spend_periods.append(period.name)

    # Custom categories (whole month)
    for category in custom_categories:
        budget_amount = to_mmk(category.estimated_amount)
        actual_amount = _get_total_actual_spent(
            monthly_month_ids, category_id=category.id, main_only=True
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
    input_total = InputBalance.objects.filter(
        budget_month_id__in=monthly_month_ids
    ).aggregate(total=Sum("amount"))["total"]
    total_money = to_mmk(input_total)

    # For each period with zero spending, subtract its budget from available money
    # (that money is effectively "saved" and remains available)
    zero_spend_budget = Decimal("0")
    for period in PERIOD_DEFINITIONS:
        if period.name in zero_spend_periods:
            period_categories = categories_by_period.get(period.index, [])
            zero_spend_budget += sum(
                (to_mmk(category.estimated_amount) for category in period_categories),
                Decimal("0"),
            )

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
        income_by_category=income_by_category,
        remaining_balance=remaining_balance,
        overall_difference=abs(overall_diff),
        overall_status="surplus" if overall_diff >= 0 else "deficit",
        period_reports=period_reports,
        zero_spend_periods=zero_spend_periods,
        sub_balance_rows=sub_balance_rows,
    )
