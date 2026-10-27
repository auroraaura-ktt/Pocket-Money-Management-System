"""Email service for sending budget reports to users."""

import logging
import re
from html import unescape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

from budget.currency import format_mmk
from budget.services.report_service import MonthlyReport

logger = logging.getLogger(__name__)

def _from_email() -> str:
    """Return the from-email with the display name 'Pocket Management System'."""
    raw = settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER
    sender_name = getattr(settings, "EMAIL_FROM_NAME", "Pocket Management System")
    return f"{sender_name} <{raw}>"


def _build_report_html(report: MonthlyReport, user_name: str) -> str:
    month_name = settings.MONTH_NAMES[report.month]
    from django.utils.html import escape

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h1 style="color: #2563eb;">Pocket Money Report</h1>
    <p>Hello <strong>{escape(user_name)}</strong>,</p>
    <p>Here is your budget report for <strong>{month_name} {report.year}</strong>.</p>

    <div style="background: #f0f9ff; padding: 16px; border-radius: 8px; margin: 16px 0;">
        <h2 style="margin-top: 0;">Monthly Summary</h2>
        <table style="width: 100%; border-collapse: collapse;">
             <tr><td>Main Balance</td><td style="text-align:right;">{escape(report.main_balance_name)}</td></tr>
             <tr><td>Total Money Available</td><td style="text-align:right;"><strong>{format_mmk(report.total_money)}</strong></td></tr>
            <tr><td>Adjusted Available Money</td><td style="text-align:right;"><strong>{format_mmk(report.adjusted_total_money)}</strong></td></tr>
            <tr><td>Total Budget Estimated</td><td style="text-align:right;">{format_mmk(report.total_budget_estimated)}</td></tr>
            <tr><td>Main Balance — Direct Actual Spending ({report.main_transaction_count} transactions)</td><td style="text-align:right;">{format_mmk(report.direct_actual_spent)}</td></tr>
            <tr><td>Sub-Balance Actual Spending</td><td style="text-align:right;">{format_mmk(report.sub_balance_total_actual_spent)}</td></tr>
            <tr><td>Combined Actual Spending</td><td style="text-align:right;"><strong>{format_mmk(report.combined_actual_spent)}</strong></td></tr>
            <tr style="font-size: 1.1em; color: {'#2563eb' if report.remaining_balance >= 0 else '#dc2626'};">
                <td><strong>Remaining Balance</strong></td>
                <td style="text-align:right;"><strong>{format_mmk(report.remaining_balance)}</strong></td>
            </tr>
            <tr style="font-size: 1.2em; color: {'#16a34a' if report.overall_status == 'surplus' else '#dc2626'};">
                <td><strong>Overall {report.overall_status.title()}</strong></td>
                <td style="text-align:right;"><strong>{format_mmk(report.overall_difference)}</strong></td>
            </tr>
        </table>
        <p style="font-size:0.85em;color:#6b7280;">Main Balance remaining balance is available money minus direct Main Balance spending. Sub-Balance spending is reported separately.</p>
    </div>

    <h2>Category Comparison (Budget vs Actual)</h2>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
        <tr style="background: #e5e7eb;">
            <th style="padding: 8px; text-align: left;">Category</th>
            <th style="padding: 8px; text-align: right;">Budget</th>
            <th style="padding: 8px; text-align: right;">Actual</th>
            <th style="padding: 8px; text-align: right;">Status</th>
            <th style="padding: 8px; text-align: right;">Amount</th>
        </tr>
    """

    for item in report.period_reports:
        color = "#16a34a" if item.status == "surplus" else "#dc2626"
        range_label = ""
        if item.start_day is not None and item.end_day is not None:
            range_label = f" (Day {item.start_day}-{item.end_day})"
        html += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{escape(item.category_name)}{range_label}</td>
            <td style="padding: 8px; text-align: right; border-bottom: 1px solid #e5e7eb;">{format_mmk(item.budget_amount)}</td>
            <td style="padding: 8px; text-align: right; border-bottom: 1px solid #e5e7eb;">{format_mmk(item.actual_amount)}</td>
            <td style="padding: 8px; text-align: right; color: {color}; border-bottom: 1px solid #e5e7eb;">{item.status.title()}</td>
            <td style="padding: 8px; text-align: right; color: {color}; border-bottom: 1px solid #e5e7eb;">{format_mmk(item.difference)}</td>
        </tr>
        """

    html += "</table>"

    if report.main_transactions:
        html += """
        <h2>Main Balance Direct Transactions</h2>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
          <tr style="background:#e5e7eb;"><th style="padding:8px;text-align:left;">Date</th><th style="padding:8px;text-align:left;">Category</th><th style="padding:8px;text-align:left;">Note</th><th style="padding:8px;text-align:right;">Amount</th></tr>
        """
        for item in report.main_transactions:
            html += (
                "<tr>"
                f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{item['date']}</td>"
                f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(item['category_name'])}</td>"
                f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(item['note'] or 'No note')}</td>"
                f"<td style=\"padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;\">{format_mmk(item['amount'])}</td>"
                "</tr>"
            )
        html += "</table>"
    else:
        html += "<h2>Main Balance Direct Transactions</h2><p>No direct Main Balance transactions recorded.</p>"

    if report.sub_balance_rows:
        html += """
        <h2>Sub-Balance Actual Spending</h2>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
          <tr style="background:#e5e7eb;"><th style="padding:8px;text-align:left;">Sub-Balance</th><th style="padding:8px;text-align:right;">Allocated amount</th><th style="padding:8px;text-align:right;">Actual spent</th><th style="padding:8px;text-align:right;">Transactions</th><th style="padding:8px;text-align:right;">Remaining</th></tr>
        """
        for row in report.sub_balance_rows:
            html += (
                "<tr>"
                f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(row['name'])}</td>"
                f"<td style=\"padding:8px;text-align:right;\">{format_mmk(row['balance_amount'])}</td>"
                f"<td style=\"padding:8px;text-align:right;\">{format_mmk(row['total_actual_spent'])}</td>"
                f"<td style=\"padding:8px;text-align:right;\">{row['transaction_count']}</td>"
                f"<td style=\"padding:8px;text-align:right;\">{format_mmk(row['remaining_balance']) if row['remaining_balance'] is not None else 'Not set'}</td>"
                "</tr>"
            )
            html += (
                "<tr><td colspan=\"5\" style=\"padding:8px 16px;\">"
                f"<strong>{escape(row['name'])} categories:</strong> "
                + (
                    "; ".join(
                        f"{escape(category['name'])}: {format_mmk(category['actual_amount'])}"
                        for category in row["categories"]
                    )
                    or "No categories recorded."
                )
                + "</td></tr>"
            )
        html += "</table>"
        for row in report.sub_balance_rows:
            html += f"<h3>{escape(row['name'])} transactions</h3>"
            if row["transactions"]:
                html += """
                <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
                  <tr style="background:#e5e7eb;"><th style="padding:8px;text-align:left;">Date</th><th style="padding:8px;text-align:left;">Category</th><th style="padding:8px;text-align:left;">Note</th><th style="padding:8px;text-align:right;">Amount</th></tr>
                """
                for item in row["transactions"]:
                    html += (
                        "<tr>"
                        f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{item['date']}</td>"
                        f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(item['category_name'])}</td>"
                        f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(item['note'] or 'No note')}</td>"
                        f"<td style=\"padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;\">{format_mmk(item['amount'])}</td>"
                        "</tr>"
                    )
                html += "</table>"
            else:
                html += "<p>No Sub-Balance transactions recorded.</p>"
    else:
        html += "<h2>Sub-Balance Actual Spending</h2><p>No Sub-Balances recorded.</p>"

    if report.zero_spend_periods:
        html += """
        <div style="background: #fefce8; padding: 16px; border-radius: 8px; margin: 16px 0; border: 1px solid #fde047;">
            <h3 style="margin-top: 0; color: #854d0e;">No Spending Periods</h3>
            <p style="margin: 0; color: #713f12;">
                No money was spent in: <strong>{periods}</strong>.<br>
                The budget for these periods has been added back to your available money.
            </p>
        </div>
        """.format(periods=", ".join(report.zero_spend_periods))

    html += """
    <p style="color: #6b7280; font-size: 0.9em; margin-top: 32px;">
        This report was generated by Pocket Money Management System.
    </p>
    </body></html>
    """
    return html


def _send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Send an HTML email. Returns True on success."""
    if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
        logger.warning("SMTP not configured. Skipping email send.")
        return False

    try:
        text_markup = re.sub(
            r"(?i)<br\s*/?>|</(?:p|h[1-6]|tr|td|th|li|div|table)>",
            "\n",
            html_content,
        )
        plain_content = unescape(strip_tags(text_markup))
        plain_content = "\n".join(
            " ".join(line.split())
            for line in plain_content.splitlines()
            if line.strip()
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_content,
            from_email=_from_email(),
            to=[to_email],
        )
        msg.attach_alternative(html_content, "text/html")
        accepted_count = msg.send(fail_silently=False)
        if accepted_count != 1:
            logger.warning("Email backend did not accept the message for %s", to_email)
            return False
        logger.info("Email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


def send_report_email(to_email: str, user_name: str, report: MonthlyReport) -> bool:
    """Send the full monthly report via email. Returns True on success."""
    month_name = settings.MONTH_NAMES[report.month]
    subject = f"Pocket Money Report - {month_name} {report.year}"
    html_content = _build_report_html(report, user_name)
    return _send_email(to_email, subject, html_content)


def send_monthly_report_email(to_email: str, user_name: str, report: MonthlyReport) -> bool:
    """Send the full monthly report via email (alias for send_report_email)."""
    return send_report_email(to_email, user_name, report)


def _build_category_html(
    category_name: str,
    budget,
    actual,
    user_name: str,
    balance_name: str,
    period_label: str,
) -> str:
    """Build a budget-vs-actual HTML email for one category."""
    from django.utils.html import escape

    difference = budget - actual
    color = "#16a34a" if difference >= 0 else "#dc2626"

    return f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h1 style="color: #2563eb;">Pocket Money Report — {escape(category_name)}</h1>
    <p>Hello <strong>{escape(user_name)}</strong>,</p>
    <p>You have recorded spending in the <strong>{escape(category_name)}</strong>
    category under <strong>{escape(balance_name)}</strong> for {escape(period_label)}.
    Here is its budget summary.</p>

    <div style="background: #f0f9ff; padding: 16px; border-radius: 8px; margin: 16px 0;">
        <h2 style="margin-top: 0;">Category Summary</h2>
        <table style="width: 100%; border-collapse: collapse;">
            <tr><td>Estimated Budget</td><td style="text-align:right;"><strong>{format_mmk(budget)}</strong></td></tr>
            <tr><td>Actual Spending in this Sub-Balance</td><td style="text-align:right;"><strong>{format_mmk(actual)}</strong></td></tr>
            <tr><td>Remaining</td><td style="text-align:right; color: {color};"><strong>{format_mmk(difference)}</strong></td></tr>
        </table>
    </div>

    <p style="color: #6b7280; font-size: 0.9em; margin-top: 32px;">
        This report was generated by Pocket Money Management System.
    </p>
    </body></html>
    """


def send_category_report_email(
    to_email: str,
    user_name: str,
    category_name: str,
    budget,
    actual,
    balance_name: str = "Main Balance",
    period_label: str = "",
) -> bool:
    """Send a single-category budget report via email."""
    subject = f"Pocket Money Report — {category_name} — {balance_name}"
    html_content = _build_category_html(
        category_name, budget, actual, user_name, balance_name, period_label
    )
    return _send_email(to_email, subject, html_content)


def send_sub_balance_report_email(sub_balance) -> bool:
    """Send a readable Sub Balance report using the persisted amount and actual spending."""
    from django.utils import timezone
    from django.utils.html import escape

    month = sub_balance.budget_month
    user = month.user
    categories = list(sub_balance.categories.select_related("budget_month").all())
    expenses = list(
        sub_balance.expenses.filter(budget_month=month)
        .select_related("category")
        .all()
    )
    actual = sub_balance.total_actual_spent
    created = timezone.localtime(sub_balance.created_at).strftime("%B %d, %Y")
    period = month.month_label
    safe_name = escape(sub_balance.name)
    safe_user_name = escape(user.name)

    rows_html = "".join(
        (
            '<tr>'
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(category.name)}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(category.estimated_amount)}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(sum(abs(item.amount) for item in category.expenses.filter(sub_balance=sub_balance, budget_month=month)))}</td>'
            '</tr>'
        )
        for category in categories
    )
    if not rows_html:
        rows_html = '<tr><td colspan="3" style="padding:12px;color:#6b7280;">No spending categories recorded for this Sub Balance yet.</td></tr>'

    transaction_rows_html = "".join(
        (
            '<tr>'
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{item.expense_date}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(item.category.name)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(item.note or "No note")}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(item.amount)}</td>'
            '</tr>'
        )
        for item in expenses
    )
    if not transaction_rows_html:
        transaction_rows_html = '<tr><td colspan="4" style="padding:12px;color:#6b7280;">No spending transactions recorded.</td></tr>'

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;background:#f9fafb;padding:24px;">
      <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:24px;">
        <h1 style="color:#2563eb;margin-top:0;">Sub Balance Report</h1>
        <p>Hello <strong>{safe_user_name}</strong>,</p>
        <p>This report covers <strong>{safe_name}</strong> for <strong>{period}</strong>. It was created on {created}.</p>

        <div style="background:#f0f9ff;padding:16px;border-radius:8px;margin:16px 0;">
          <h2 style="margin-top:0;">Summary</h2>
          <table style="width:100%;border-collapse:collapse;">
            <tr><td style="padding:6px 0;">Allocated amount</td><td style="padding:6px 0;text-align:right;">{format_mmk(sub_balance.allocated_amount)}</td></tr>
            <tr><td style="padding:6px 0;">Total actual spending</td><td style="padding:6px 0;text-align:right;">{format_mmk(actual)}</td></tr>
            <tr><td style="padding:6px 0;">Remaining balance</td><td style="padding:6px 0;text-align:right;"><strong>{format_mmk(sub_balance.remaining_balance)}</strong></td></tr>
          </table>
        </div>

        <h2>Category spending</h2>
        <table style="width:100%;border-collapse:collapse;">
          <tr style="background:#e5e7eb;">
            <th style="padding:8px;text-align:left;">Category</th>
            <th style="padding:8px;text-align:right;">Budget</th>
            <th style="padding:8px;text-align:right;">Actual</th>
          </tr>
          {rows_html}
        </table>

        <h2 style="margin-top:24px;">Parent Main Balance</h2>
        <p>{escape(month.name or month.month_label)} · {month.month_label}</p>

        <h2>Transactions</h2>
        <table style="width:100%;border-collapse:collapse;">
          <tr style="background:#e5e7eb;">
            <th style="padding:8px;text-align:left;">Date</th>
            <th style="padding:8px;text-align:left;">Category</th>
            <th style="padding:8px;text-align:left;">Note</th>
            <th style="padding:8px;text-align:right;">Amount</th>
          </tr>
          {transaction_rows_html}
        </table>
      </div>
    </body></html>
    """
    subject = f"Sub Balance Report - {sub_balance.name} - {period}"
    return _send_email(user.email, subject, html)
