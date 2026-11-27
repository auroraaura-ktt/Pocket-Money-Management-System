"""Email service for sending budget reports to users."""

import logging
import re
from email.message import EmailMessage as MimeEmailMessage
from email.utils import formataddr
from html import escape, unescape
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

from budget.currency import format_mmk
from budget.services.report_service import MonthlyReport

logger = logging.getLogger(__name__)


class RelatedEmailMultiAlternatives(EmailMultiAlternatives):
    logo_content: bytes | None = None

    def message(self, *args, **kwargs):
        message = super().message(*args, **kwargs)
        if not self.logo_content:
            return message

        html_part = next(
            (
                part
                for part in message.walk()
                if part.get_content_type() == "text/html"
            ),
            None,
        )
        if html_part is None:
            return message

        html_part["Content-ID"] = "<pmms-html>"
        related = MimeEmailMessage()
        related.make_related()
        related.set_param("type", "text/html")
        related.set_param("start", "<pmms-html>")
        related.attach(html_part)

        logo = MimeEmailMessage()
        logo.set_content(self.logo_content, maintype="image", subtype="jpeg")
        logo["Content-ID"] = "<pmms-logo>"
        logo["Content-Disposition"] = 'inline; filename="pmms-logo.jpg"'
        related.attach(logo)
        message.set_payload(
            [related if part is html_part else part for part in message.get_payload()]
        )
        return message


def _from_email() -> str:
    """Return the configured address with the product's exact display name."""
    raw = settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER
    return formataddr(("Pocket Money Management System", raw))


def _brand_email_html(
    html_content: str, report_title: str = "Monthly Financial Report"
) -> str:
    """Add the shared top-left logo and wordmark to every HTML report email."""
    brand = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="font-family:Arial,sans-serif;background:#f2f7ff;
                  border-bottom:3px solid #245cff;margin-bottom:20px;">
      <tr>
        <td width="150" style="padding:12px 16px;vertical-align:middle;">
          <img src="cid:pmms-logo" alt="PMMS by K's" width="130"
               style="display:block;width:130px;max-width:100%;height:auto;">
        </td>
        <td style="padding:12px 16px 12px 0;vertical-align:middle;text-align:left;">
          <strong style="display:block;color:#092d72;font-size:18px;line-height:1.35;">
            Pocket Money Management System
          </strong>
          <span style="display:block;color:#385a91;font-size:12px;margin-top:4px;">
            {escape(report_title)}
          </span>
        </td>
      </tr>
    </table>
    """
    body_match = re.search(r"<body\b[^>]*>", html_content, flags=re.I)
    if not body_match:
        return html_content
    html_content = (
        html_content[:body_match.start()]
        + '<body bgcolor="#edf4ff" style="margin:0;padding:24px 12px;'
        'background-color:#edf4ff;font-family:Arial,sans-serif;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'bgcolor="#edf4ff"><tr><td align="center">'
        '<table role="presentation" width="760" cellpadding="0" cellspacing="0" '
        'bgcolor="#ffffff" style="width:100%;max-width:760px;background:#ffffff;'
        'border:1px solid #dce9ff;"><tr><td style="padding:22px;">'
        + brand
        + html_content[body_match.end():]
    )
    return re.sub(
        r"</body\s*>",
        "</td></tr></table></td></tr></table></body>",
        html_content,
        count=1,
        flags=re.I,
    )


def _build_report_html(report: MonthlyReport, user_name: str) -> str:
    month_name = settings.MONTH_NAMES[report.month]
    from django.utils.html import escape

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h1 style="color: #2563eb;">Pocket Money Report</h1>
    <p>Hello <strong>{escape(user_name)}</strong>,</p>
    <p>Here is your budget report for <strong>{month_name} {report.year}</strong>.</p>
    <p><strong>Main Balances included:</strong> {escape(", ".join(report.main_balance_names) or report.main_balance_name)}</p>
    <p><strong>Sub-Balances included:</strong> {escape(", ".join(report.sub_balance_names) or "None")}</p>

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
    """
    html += """
    <h2>Income by Category</h2>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
      <tr style="background:#e5e7eb;"><th style="padding:8px;text-align:left;">Income category</th><th style="padding:8px;text-align:right;">Total income</th><th style="padding:8px;text-align:right;">Entries</th></tr>
    """
    if report.income_by_category:
        for item in report.income_by_category:
            html += (
                "<tr>"
                f"<td style=\"padding:8px;border-bottom:1px solid #e5e7eb;\">{escape(item['category_name'])}</td>"
                f"<td style=\"padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;\">{format_mmk(item['amount'])}</td>"
                f"<td style=\"padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;\">{item['entry_count']}</td>"
                "</tr>"
            )
    else:
        html += "<tr><td colspan=\"3\" style=\"padding:8px;\">No income entries recorded.</td></tr>"
    html += "</table>"
    html += """
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
            if row["balance_categories"]:
                html += (
                    "<tr><td colspan=\"5\" style=\"padding:8px 16px;\">"
                    "<strong>Category balances</strong>"
                    "<table style=\"width:100%;border-collapse:collapse;margin-top:8px;\">"
                    "<tr style=\"background:#f3f6fb;\"><th style=\"padding:6px;text-align:left;\">Category</th>"
                    "<th style=\"padding:6px;text-align:left;\">Basis</th>"
                    "<th style=\"padding:6px;text-align:right;\">Allocated</th>"
                    "<th style=\"padding:6px;text-align:right;\">Spent</th>"
                    "<th style=\"padding:6px;text-align:right;\">Remaining</th></tr>"
                )
                for category in row["balance_categories"]:
                    html += (
                        "<tr>"
                        f"<td style=\"padding:6px;\">{escape(category['name'])}</td>"
                        f"<td style=\"padding:6px;\">{escape(category['basis'])}</td>"
                        f"<td style=\"padding:6px;text-align:right;\">{format_mmk(category['allocated_amount'])}</td>"
                        f"<td style=\"padding:6px;text-align:right;\">{format_mmk(category['actual_amount'])}</td>"
                        f"<td style=\"padding:6px;text-align:right;\">{format_mmk(category['remaining_balance'])}</td>"
                        "</tr>"
                    )
                html += "</table></td></tr>"
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


def _send_email(
    to_email: str,
    subject: str,
    html_content: str,
    report_title: str = "Monthly Financial Report",
) -> bool:
    """Send an HTML email. Returns True on success."""
    if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
        logger.warning("SMTP not configured. Skipping email send.")
        return False

    try:
        html_content = _brand_email_html(html_content, report_title)
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
        logo_path = Path(settings.BASE_DIR) / "static" / "images" / "pmms-logo.jpg"
        msg = RelatedEmailMultiAlternatives(
            subject=subject,
            body=plain_content,
            from_email=_from_email(),
            to=[to_email],
        )
        with logo_path.open("rb") as logo_file:
            msg.logo_content = logo_file.read()
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
    scope = ", ".join(report.main_balance_names) or report.main_balance_name
    subject = f"Pocket Money Report - {month_name} {report.year} - {scope}"
    html_content = _build_report_html(report, user_name)
    return _send_email(to_email, subject, html_content, report.report_title)


def send_monthly_report_email(to_email: str, user_name: str, report: MonthlyReport) -> bool:
    """Send the full monthly report via email (alias for send_report_email)."""
    return send_report_email(to_email, user_name, report)


def send_report_dataset_email(to_email: str, user_name: str, dataset: dict) -> bool:
    """Send the selected multi-balance report with its transaction detail."""
    from django.utils.html import escape

    totals = dataset["totals"]
    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#19345f;max-width:760px;margin:auto;line-height:1.5">
      <h1 style="color:#315fff">Pocket Money Monthly Report</h1>
      <p>Hello <strong>{escape(user_name)}</strong>, here is your report for <strong>{escape(dataset['month'])}</strong>.</p>
      <p><strong>Main Balances included:</strong> {escape(", ".join(dataset['main_balance_names']) or "None")}</p>
      <p><strong>Sub-Balances included:</strong> {escape(", ".join(dataset['sub_balance_names']) or "None")}</p>
      <div style="padding:16px;border:1px solid #dce7ff;border-radius:12px;background:#f5f8ff">
        <h2 style="margin-top:0">Monthly overview</h2>
        <p>Main Balances: {dataset['total_main_balances']}</p>
        <p>Total Money: <strong>{format_mmk(totals['money'])}</strong></p>
        <p>Main Balance spending ({dataset['main_transaction_count']} transactions): {format_mmk(totals['expense'])}</p>
        <p>Sub-Balance spending ({dataset['sub_transaction_count']} transactions): {format_mmk(totals['sub_expense'])}</p>
        <p>Combined spending ({dataset['total_transactions']} transactions): <strong>{format_mmk(totals['combined_expense'])}</strong></p>
        <p>Main Balance remaining after direct spending: <strong>{format_mmk(totals['remaining'])}</strong></p>
      </div>
    """

    def table(headers, rows):
        head = "".join(
            f'<th style="padding:8px;text-align:left;background:#edf2fc">{escape(str(item))}</th>'
            for item in headers
        )
        body = "".join(
            "<tr>"
            + "".join(
                f'<td style="padding:8px;border-bottom:1px solid #e8edf5">{escape(str(value or "—"))}</td>'
                for value in row
            )
            + "</tr>"
            for row in rows
        )
        return f'<table style="width:100%;border-collapse:collapse;margin:12px 0 22px"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    for record in dataset["main_balances"]:
        html += f"""
        <section style="margin-top:28px;border-top:2px solid #e4eaff;padding-top:16px">
          <h2>{escape(record['display_name'])}</h2>
          <p>Available: {format_mmk(record['total_money'])} · Main Balance spent: {format_mmk(record['total_expense'])} ({record['main_transaction_count']} transactions) · Remaining: {format_mmk(record['remaining_balance'])}</p>
          <p>Sub-Balances spent: {format_mmk(record['sub_actual_spent'])} ({record['sub_transaction_count']} transactions) · Combined spending: {format_mmk(record['combined_expense'])}</p>
        """
        income_rows = [
            [
                item["category_name"],
                format_mmk(item["amount"]),
                str(item["entry_count"]),
            ]
            for item in record["income_by_category"]
        ]
        html += "<h3>Income by Category</h3>"
        html += table(["Income category", "Total income", "Entries"], income_rows)
        if not income_rows:
            html += "<p>No income entries recorded.</p>"
        html += "<h3>Main Balance transactions</h3>"
        main_rows = [
            [
                str(item["date"]),
                item["category_name"],
                item["note"],
                format_mmk(item["amount"]),
            ]
            for item in record["main_transactions"]
        ]
        html += table(["Date", "Category", "Note", "Spent"], main_rows)
        if not main_rows:
            html += "<p>No direct Main Balance transactions recorded.</p>"
        html += "<h3>Sub-Balance transactions and remaining amounts</h3>"
        for sub_balance in record["sub_balance_details"]:
            html += (
                f"<h4>{escape(sub_balance['name'])}</h4>"
                f"<p>Allocated: {format_mmk(sub_balance['allocated_amount'])} · "
                f"Spent: {format_mmk(sub_balance['total_actual_spent'])} · "
                f"Remaining: {format_mmk(sub_balance['remaining_balance'])} · "
                f"{sub_balance['transaction_count']} transactions</p>"
            )
            category_rows = [
                [
                    item["name"],
                    item["basis"],
                    format_mmk(item["allocated_amount"]),
                    format_mmk(item["actual_amount"]),
                    format_mmk(item["remaining_balance"]),
                ]
                for item in sub_balance["balance_categories"]
            ]
            html += "<h5>Category balances</h5>"
            html += table(
                ["Category", "Basis", "Allocated", "Spent", "Remaining"],
                category_rows,
            )
            if not category_rows:
                html += "<p>No category allocations recorded.</p>"
            rows = [
                [
                    str(item["date"]),
                    item["category_name"],
                    item["note"],
                    format_mmk(item["amount"]),
                ]
                for item in sub_balance["transactions"]
            ]
            html += table(["Date", "Category", "Note", "Spent"], rows)
            if not rows:
                html += "<p>No transactions recorded.</p>"
        html += "</section>"

    html += "<p style='margin-top:28px;color:#71819a'>Generated by Pocket Money Management System.</p></body></html>"
    return _send_email(
        to_email,
        f"Pocket Money Report - {dataset['month']} - "
        f"{', '.join(dataset['main_balance_names']) or 'Main Balances'}",
        html,
        dataset["report_title"],
    )


def _build_category_html(
    category_name: str,
    budget,
    actual,
    user_name: str,
    balance_name: str,
    period_label: str,
    transactions: list[dict] | None = None,
) -> str:
    """Build a budget-vs-actual HTML email for one category."""
    from django.utils.html import escape

    difference = budget - actual
    color = "#16a34a" if difference >= 0 else "#dc2626"

    transaction_rows = "".join(
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{escape(str(item['date']))}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{escape(item['note'] or 'No note')}</td>"
        f"<td style='padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;'>{format_mmk(item['amount'])}</td>"
        "</tr>"
        for item in (transactions or [])
    )
    if not transaction_rows:
        transaction_rows = (
            "<tr><td colspan='3' style='padding:8px;'>"
            "No transactions recorded for this category.</td></tr>"
        )

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

    <h2>Category Transactions</h2>
    <table style="width: 100%; border-collapse: collapse;">
      <tr style="background:#e5e7eb;">
        <th style="padding:8px;text-align:left;">Date</th>
        <th style="padding:8px;text-align:left;">Note</th>
        <th style="padding:8px;text-align:right;">Amount spent</th>
      </tr>
      {transaction_rows}
    </table>

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
    transactions: list[dict] | None = None,
) -> bool:
    """Send a single-category budget report via email."""
    subject = f"Pocket Money Report — {category_name} — {balance_name}"
    html_content = _build_category_html(
        category_name,
        budget,
        actual,
        user_name,
        balance_name,
        period_label,
        transactions,
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
    actual_by_category = {}
    for expense in expenses:
        category_name = expense.category.name
        actual_by_category[category_name] = (
            actual_by_category.get(category_name, 0) + abs(expense.amount)
        )
    if sub_balance.allocation_mode == sub_balance.INHERIT_MAIN_TOTAL:
        balance_rows = [
            {
                "name": category.name,
                "basis": "Budget estimate",
                "allocated_amount": category.estimated_amount,
                "actual_amount": actual_by_category.get(category.name, 0),
                "remaining_balance": (
                    category.estimated_amount
                    - actual_by_category.get(category.name, 0)
                ),
            }
            for category in categories
        ]
    else:
        allocated_by_category = {}
        for allocation in sub_balance.allocations.all():
            allocated_by_category[allocation.category] = (
                allocated_by_category.get(allocation.category, 0)
                + allocation.amount
            )
        balance_rows = []
        for category_name in sorted(
            set(allocated_by_category) | set(actual_by_category),
            key=str.casefold,
        ):
            allocated_amount = allocated_by_category.get(category_name, 0)
            actual_amount = actual_by_category.get(category_name, 0)
            balance_rows.append(
                {
                    "name": category_name,
                    "basis": "Category allocation",
                    "allocated_amount": allocated_amount,
                    "actual_amount": actual_amount,
                    "remaining_balance": allocated_amount - actual_amount,
                }
            )
        legacy_amount = (
            sub_balance.balance_amount
            if sub_balance.balance_amount
            else (sub_balance.estimated_amount or 0)
        )
        if legacy_amount:
            balance_rows.insert(
                0,
                {
                    "name": "Uncategorized opening balance",
                    "basis": "Opening balance",
                    "allocated_amount": legacy_amount,
                    "actual_amount": 0,
                    "remaining_balance": legacy_amount,
                },
            )

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

    balance_rows_html = "".join(
        (
            "<tr>"
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(item["name"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(item["basis"])}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(item["allocated_amount"])}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(item["actual_amount"])}</td>'
            f'<td style="padding:8px;text-align:right;border-bottom:1px solid #e5e7eb;">{format_mmk(item["remaining_balance"])}</td>'
            "</tr>"
        )
        for item in balance_rows
    )
    if not balance_rows_html:
        balance_rows_html = (
            '<tr><td colspan="5" style="padding:12px;color:#6b7280;">'
            "No category allocations recorded.</td></tr>"
        )

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

        <h2>Category balances</h2>
        <table style="width:100%;border-collapse:collapse;">
          <tr style="background:#e5e7eb;">
            <th style="padding:8px;text-align:left;">Category</th>
            <th style="padding:8px;text-align:left;">Basis</th>
            <th style="padding:8px;text-align:right;">Allocated</th>
            <th style="padding:8px;text-align:right;">Spent</th>
            <th style="padding:8px;text-align:right;">Remaining</th>
          </tr>
          {balance_rows_html}
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
    return _send_email(
        user.email, subject, html, "Sub-Balance Financial Report"
    )
