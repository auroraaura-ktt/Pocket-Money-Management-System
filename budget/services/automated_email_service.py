"""Automated report emails.

Two kinds of automated emails:

1. Per-category report emails — sent as soon as the user records actual
   spending for a category (default or manually created). A category with
   no spending (0 / nothing recorded) never triggers an email. Each
   category gets at most one email per budget month, deduplicated via
   ReportEmailLog (report_type = ``cat_<category_id>``).

2. Final full monthly report — sent automatically on the last day of the
   month (when the month expires), triggered once per day by
   budget.middleware.AutoEmailMiddleware, or manually with
   `python manage.py send_automated_emails`.
"""

import calendar
import logging
from datetime import date
from decimal import Decimal

from django.db.models import F, Sum
from django.db.models.functions import Abs
from django.utils import timezone

from budget.models import BudgetCategory, BudgetMonth, DailyExpense, ReportEmailLog
from budget.services.email_service import (
    send_category_report_email as _send_category_report_email,
    send_monthly_report_email,
)
from budget.services.report_service import generate_monthly_report

logger = logging.getLogger(__name__)


def category_report_type(category_id: int) -> str:
    """ReportEmailLog key used to deduplicate per-category emails."""
    return f"cat_{category_id}"


def _already_sent(budget_month_id: int, report_type: str) -> bool:
    return ReportEmailLog.objects.filter(
        budget_month_id=budget_month_id, report_type=report_type
    ).exists()


def _log_sent(budget_month_id: int, report_type: str) -> None:
    ReportEmailLog.objects.get_or_create(
        budget_month_id=budget_month_id, report_type=report_type
    )


def category_actual_spent(category: BudgetCategory) -> Decimal:
    """Total actual money spent on a category (magnitude of all expenses)."""
    total = DailyExpense.objects.filter(category=category).aggregate(
        total=Sum(Abs(F("amount")))
    )["total"]
    return Decimal(str(total or 0))


def send_category_report_email(category: BudgetCategory) -> bool:
    """Send the report email for one category after actual spending exists.

    Skips when the category has no spending (0 or nothing recorded) and
    when an email was already sent for it this budget month.
    """
    actual = category_actual_spent(category)
    if actual <= 0:
        logger.info(
            "No spending recorded for category '%s' - skipping email.",
            category.name,
        )
        return False

    report_type = category_report_type(category.id)
    if _already_sent(category.budget_month_id, report_type):
        return False

    user = category.budget_month.user
    sent = _send_category_report_email(
        to_email=user.email,
        user_name=user.name,
        category_name=category.name,
        budget=category.estimated_amount,
        actual=actual,
    )
    if sent:
        _log_sent(category.budget_month_id, report_type)
        logger.info(
            "Sent category report for '%s' to %s (%s)",
            category.name,
            user.email,
            category.budget_month.month_label,
        )
    return sent


def _send_monthly_full_email(budget_month: BudgetMonth) -> bool:
    if _already_sent(budget_month.id, ReportEmailLog.MONTHLY_FULL):
        return False

    report = generate_monthly_report(budget_month.id)
    user = budget_month.user

    sent = send_monthly_report_email(user.email, user.name, report)
    if sent:
        _log_sent(budget_month.id, ReportEmailLog.MONTHLY_FULL)
        logger.info(
            "Sent monthly full report to %s for %s",
            user.email,
            budget_month.month_label,
        )
    return sent


def process_automated_emails(today: date | None = None, force: bool = False) -> dict:
    """Send the final full monthly report when the month expires (last day)."""
    today = today or timezone.localdate()
    results = {"sent": [], "skipped": []}

    budget_months = BudgetMonth.objects.select_related("user").filter(
        year=today.year, month=today.month
    )

    if not budget_months.exists():
        results["skipped"].append(f"No budget months for {today.year}-{today.month}")
        return results

    last_day = calendar.monthrange(today.year, today.month)[1]

    if today.day != last_day and not force:
        results["skipped"].append(
            f"Not the last day of the month (day {today.day} of {last_day})"
        )
        return results

    for budget_month in budget_months:
        if force:
            ReportEmailLog.objects.filter(budget_month=budget_month).delete()
        if _send_monthly_full_email(budget_month):
            results["sent"].append(f"{budget_month.month_label} — monthly full")

    return results
