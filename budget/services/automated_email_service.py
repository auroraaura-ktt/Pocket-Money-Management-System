"""Automated scheduled report emails."""

import calendar
import logging
from datetime import date

from django.utils import timezone

from budget.models import BudgetMonth, ReportEmailLog
from budget.services.email_service import send_monthly_report_email, send_period_report_email
from budget.services.report_service import generate_monthly_report

logger = logging.getLogger(__name__)

PERIOD_TYPE_MAP = {
    1: ReportEmailLog.PERIOD_1,
    2: ReportEmailLog.PERIOD_2,
    3: ReportEmailLog.PERIOD_3,
}


def _already_sent(budget_month_id: int, report_type: str) -> bool:
    return ReportEmailLog.objects.filter(
        budget_month_id=budget_month_id, report_type=report_type
    ).exists()


def _log_sent(budget_month_id: int, report_type: str) -> None:
    ReportEmailLog.objects.get_or_create(
        budget_month_id=budget_month_id, report_type=report_type
    )


def _send_period_email(budget_month: BudgetMonth, period_index: int) -> bool:
    report_type = PERIOD_TYPE_MAP[period_index]
    if _already_sent(budget_month.id, report_type):
        return False

    report = generate_monthly_report(budget_month.id)
    period = report.period_reports[period_index - 1]
    user = budget_month.user
    sent = send_period_report_email(
        user.email, user.name, report, period, budget_month.month_label
    )
    if sent:
        _log_sent(budget_month.id, report_type)
        logger.info(
            "Sent %s report to %s for %s",
            period.category_name,
            user.email,
            budget_month.month_label,
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
    """
    Send automated emails based on today's date:
      - Day 11  → Days 1-10 period report
      - Day 21  → Days 11-20 period report
      - Last day of month → Days 21-end report + monthly full report
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
    periods_to_send: list[int] = []
    send_monthly = False

    if today.day == 11 or force:
        periods_to_send.append(1)
    if today.day == 21 or force:
        periods_to_send.append(2)
    if today.day == last_day or force:
        periods_to_send.append(3)
        send_monthly = True

    if not periods_to_send and not send_monthly:
        results["skipped"].append(
            f"No scheduled emails for day {today.day} (sends on 11, 21, and last day)"
        )
        return results

    for budget_month in budget_months:
        for period_index in periods_to_send:
            if force:
                ReportEmailLog.objects.filter(
                    budget_month=budget_month,
                    report_type=PERIOD_TYPE_MAP[period_index],
                ).delete()
            if _send_period_email(budget_month, period_index):
                results["sent"].append(
                    f"{budget_month.month_label} — period {period_index}"
                )

        if send_monthly:
            if force:
                ReportEmailLog.objects.filter(
                    budget_month=budget_month,
                    report_type=ReportEmailLog.MONTHLY_FULL,
                ).delete()
            if _send_monthly_full_email(budget_month):
                results["sent"].append(f"{budget_month.month_label} — monthly full")

    return results
