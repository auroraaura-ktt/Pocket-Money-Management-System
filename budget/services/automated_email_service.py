"""Automated scheduled report emails."""

import calendar
import logging
from datetime import date

from django.utils import timezone

from budget.models import BudgetMonth, ReportEmailLog
from budget.services.email_service import send_monthly_report_email
from budget.services.report_service import generate_monthly_report

logger = logging.getLogger(__name__)


def _already_sent(budget_month_id: int, report_type: str) -> bool:
    return ReportEmailLog.objects.filter(
        budget_month_id=budget_month_id, report_type=report_type
    ).exists()


def _log_sent(budget_month_id: int, report_type: str) -> None:
    ReportEmailLog.objects.get_or_create(
        budget_month_id=budget_month_id, report_type=report_type
    )


def _send_monthly_full_email(budget_month: BudgetMonth) -> bool:
    if _already_sent(budget_month.id, ReportEmailLog.MONTHLY_FULL):
        return False

    report = generate_monthly_report(budget_month.id)

    # Only email the report when there is a zero-spend adjustment.
    # When a period had no spending, its budget is subtracted from the
    # available bill (adjusted_total_money) — that's the case the user needs
    # to be notified about.
    if not report.zero_spend_periods:
        logger.info(
            "No zero-spend periods for %s - skipping report email.",
            budget_month.month_label,
        )
        return False

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
    """
    Send a single total monthly report email.
    Sends on Day 11, Day 21, and the last day of the month.
    """
    today = today or timezone.localdate()
    results = {"sent": [], "skipped": []}

    budget_months = BudgetMonth.objects.select_related("user").filter(
        year=today.year, month=today.month
    )

    if not budget_months.exists():
        results["skipped"].append(f"No budget months for {today.year}-{today.month}")
        return results

    last_day = calendar.monthrange(today.year, today.month)[1]
    send_monthly = False

    if today.day == 11 or today.day == 21 or today.day == last_day or force:
        send_monthly = True

    if not send_monthly:
        results["skipped"].append(
            f"No scheduled emails for day {today.day} (sends on 11, 21, and last day)"
        )
        return results

    for budget_month in budget_months:
        if force:
            ReportEmailLog.objects.filter(
                budget_month=budget_month,
                report_type=ReportEmailLog.MONTHLY_FULL,
            ).delete()
        if _send_monthly_full_email(budget_month):
            results["sent"].append(f"{budget_month.month_label} — monthly full")

    return results
