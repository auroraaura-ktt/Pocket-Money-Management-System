"""Two independent automatic monthly report schedules.

Schedule A — Monthly case report for the PREVIOUS calendar month.
    Fires on the 1st day of each month at 00:00 (project time zone).
    Reuses the existing monthly report generation and email service.
    Example: on 1 Nov it sends the report for October.

Schedule B — Overall report for cases WITHOUT present estimated money.
    Fires on the 16th day of each month at 00:00.
    Covers the current month's days 1-15 and includes only categories whose
    ``estimated_amount`` is 0 (no present estimated money) that recorded
    actual spending inside that window.
    Example: on 16 Nov it reports on November 1-15 spending.

Both schedules:
- are triggered once per day by ``budget.middleware.AutoEmailMiddleware``
  (and can be run manually or from an external cron at 00:00 with
  ``python manage.py send_monthly_schedules``),
- deduplicate delivery through the existing ``ReportEmailLog`` model using
  dedicated, non-colliding ``report_type`` keys, so retries / restarts never
  resend a report that was already delivered,
- are fully independent of each other and of the existing category-specific
  and monthly-full automated emails (each runs inside its own try/except and
  uses its own dedup key, so one failing never blocks the others).

No model, schema, migration, or existing-behaviour change is required: the
dedup keys are stored as plain strings in ``ReportEmailLog.report_type`` exactly
like the existing ``cat_<id>`` keys.
"""

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from budget.currency import format_mmk
from budget.models import BudgetCategory, BudgetMonth, ReportEmailLog
from budget.services.email_service import (
    # Reuse the private SMTP sender so the from-name / SMTP handling is
    # identical to every other report email in the project.
    _send_email,
    send_monthly_report_email,
)
from budget.services.report_service import (
    # Reuse the existing date-range spending aggregation.
    _get_total_actual_spent,
    generate_monthly_report,
)

logger = logging.getLogger(__name__)

# Isolated dedup keys stored in ReportEmailLog.report_type (<= 20 chars).
# They never collide with existing keys (period_1..3, monthly_full, category,
# cat_<id>), so existing automated emails are completely unaffected.
MONTHLY_PREVIOUS = "monthly_prev"      # Schedule A
NO_ESTIMATE_FIRST_HALF = "noest_1_15"  # Schedule B

# Day of month each schedule fires on (00:00 project time zone).
MONTHLY_PREVIOUS_DAY = 1
NO_ESTIMATE_FIRST_HALF_DAY = 16

# Reporting window for Schedule B: days 1..15 inclusive.
FIRST_HALF_END_DAY = 15


def _previous_month(year: int, month: int) -> tuple[int, int]:
    """Return (year, month) of the calendar month before the given one."""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _already_sent(budget_month_id: int, report_type: str) -> bool:
    return ReportEmailLog.objects.filter(
        budget_month_id=budget_month_id, report_type=report_type
    ).exists()


def _log_sent(budget_month_id: int, report_type: str) -> None:
    ReportEmailLog.objects.get_or_create(
        budget_month_id=budget_month_id, report_type=report_type
    )


# ---------------------------------------------------------------------------
# Schedule A — monthly case report for the previous calendar month (day 1)
# ---------------------------------------------------------------------------
def send_previous_month_report(today: date, force: bool = False) -> dict:
    """Generate and email the monthly report for the previous calendar month."""
    year, month = _previous_month(today.year, today.month)
    results = {"sent": [], "skipped": []}

    budget_months = BudgetMonth.objects.select_related("user").filter(
        year=year, month=month
    )
    if not budget_months.exists():
        results["skipped"].append(f"No budget months for {year}-{month:02d}")
        return results

    for budget_month in budget_months:
        if force:
            ReportEmailLog.objects.filter(
                budget_month=budget_month, report_type=MONTHLY_PREVIOUS
            ).delete()
        elif _already_sent(budget_month.id, MONTHLY_PREVIOUS):
            results["skipped"].append(f"{budget_month.month_label} — already sent")
            continue

        user = budget_month.user
        try:
            report = generate_monthly_report(budget_month.id)
        except Exception:
            logger.exception(
                "Failed to generate monthly report for %s", budget_month.month_label
            )
            results["skipped"].append(f"{budget_month.month_label} — report error")
            continue

        if send_monthly_report_email(user.email, user.name, report):
            _log_sent(budget_month.id, MONTHLY_PREVIOUS)
            results["sent"].append(f"{budget_month.month_label} — monthly previous")
        else:
            # Not logged on purpose so the send can be retried later.
            results["skipped"].append(f"{budget_month.month_label} — email not sent")

    return results



# ---------------------------------------------------------------------------
# Schedule B — overall report for cases without estimated money (day 16)
# ---------------------------------------------------------------------------
def _no_estimate_category_spending(
    budget_month: BudgetMonth, start_date: date, end_date: date
) -> list[dict]:
    """Categories with no present estimated money (estimated_amount == 0) that
    recorded actual spending within [start_date, end_date]."""
    rows: list[dict] = []
    categories = BudgetCategory.objects.filter(
        budget_month=budget_month, estimated_amount=0
    )
    for category in categories:
        actual = _get_total_actual_spent(
            budget_month.id,
            category_id=category.id,
            start_date=start_date,
            end_date=end_date,
        )
        if actual > 0:
            rows.append({"name": category.name, "actual": actual})
    return rows


def _build_no_estimate_html(
    user_name: str, period_label: str, rows: list[dict], total: Decimal
) -> str:
    table_rows = "".join(
        "<tr>"
        f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{row['name']}</td>"
        "<td style=\"padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;\">"
        f"{format_mmk(row['actual'])}</td>"
        "</tr>"
        for row in rows
    )
    return f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h1 style="color: #2563eb;">Pocket Money — Spending Without Budget</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Here is an overall report of spending recorded in categories that have
    <strong>no estimated budget</strong> for <strong>{period_label}</strong>.</p>

    <div style="background: #fff7ed; padding: 16px; border-radius: 8px; margin: 16px 0;">
        <h2 style="margin-top: 0;">Categories Without Estimated Money</h2>
        <table style="width: 100%; border-collapse: collapse;">
            <tr style="background: #fed7aa;">
                <th style="padding: 8px; text-align: left;">Category</th>
                <th style="padding: 8px; text-align: right;">Actually Spent</th>
            </tr>
            {table_rows}
            <tr>
                <td style="padding: 8px;"><strong>Total</strong></td>
                <td style="padding: 8px; text-align: right;">
                    <strong>{format_mmk(total)}</strong>
                </td>
            </tr>
        </table>
    </div>

    <p style="color: #6b7280; font-size: 0.9em; margin-top: 32px;">
        This report was generated by Pocket Money Management System.
    </p>
    </body></html>
    """


def send_no_estimate_overview(today: date, force: bool = False) -> dict:
    """Email an overall report of no-estimate spending for the current month's
    first half (days 1-15)."""
    results = {"sent": [], "skipped": []}
    start_date = date(today.year, today.month, 1)
    end_date = date(today.year, today.month, FIRST_HALF_END_DAY)

    budget_months = BudgetMonth.objects.select_related("user").filter(
        year=today.year, month=today.month
    )
    if not budget_months.exists():
        results["skipped"].append(
            f"No budget months for {today.year}-{today.month:02d}"
        )
        return results

    month_name = settings.MONTH_NAMES[today.month]
    period_label = f"{month_name} {today.year} (Days 1-{FIRST_HALF_END_DAY})"

    for budget_month in budget_months:
        if force:
            ReportEmailLog.objects.filter(
                budget_month=budget_month, report_type=NO_ESTIMATE_FIRST_HALF
            ).delete()
        elif _already_sent(budget_month.id, NO_ESTIMATE_FIRST_HALF):
            results["skipped"].append(f"{budget_month.month_label} — already sent")
            continue

        rows = _no_estimate_category_spending(budget_month, start_date, end_date)
        if not rows:
            results["skipped"].append(
                f"{budget_month.month_label} — no spending without estimated money"
            )
            continue

        total = sum((row["actual"] for row in rows), Decimal("0"))
        user = budget_month.user
        subject = f"Pocket Money — Spending Without Budget ({period_label})"
        html = _build_no_estimate_html(user.name, period_label, rows, total)

        if _send_email(user.email, subject, html):
            _log_sent(budget_month.id, NO_ESTIMATE_FIRST_HALF)
            results["sent"].append(f"{budget_month.month_label} — no-estimate overview")
        else:
            # Not logged on purpose so the send can be retried later.
            results["skipped"].append(f"{budget_month.month_label} — email not sent")

    return results


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def process_monthly_schedules(
    today: date | None = None, force: bool = False
) -> dict:
    """Run the schedule(s) that are due for ``today``.

    Schedule A runs on the 1st, Schedule B on the 16th. Each runs inside its
    own try/except so a failure in one never blocks the other or any existing
    automated email.
    """
    today = today or timezone.localdate()
    results = {"sent": [], "skipped": []}

    def _merge(sub: dict) -> None:
        results["sent"].extend(sub.get("sent", []))
        results["skipped"].extend(sub.get("skipped", []))

    if force or today.day == MONTHLY_PREVIOUS_DAY:
        try:
            _merge(send_previous_month_report(today, force=force))
        except Exception:
            logger.exception("Schedule A (previous-month report) failed")
            results["skipped"].append("monthly previous — schedule error")

    if force or today.day == NO_ESTIMATE_FIRST_HALF_DAY:
        try:
            _merge(send_no_estimate_overview(today, force=force))
        except Exception:
            logger.exception("Schedule B (no-estimate overview) failed")
            results["skipped"].append("no-estimate overview — schedule error")

    return results

