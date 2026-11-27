"""Build consistent web, PDF, and CSV reports from authorized Main Balances."""

import csv
import re
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SpreadsheetImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from budget.currency import format_mmk, to_mmk
from budget.models import BudgetMonth, DailyExpense, InputBalance, SubBalance
from budget.services.report_service import generate_monthly_report


PDF_MYANMAR_FONT = "Pyidaungsu"
PDF_MYANMAR_BOLD_FONT = "Pyidaungsu-Bold"
PDF_ENGLISH_FONT = "Helvetica"
PDF_ENGLISH_BOLD_FONT = "Helvetica-Bold"
_MYANMAR_RUN_RE = re.compile(
    r"([\u1000-\u109f\ua9e0-\ua9ff\uaa60-\uaa7f\ue000-\uf8ff"
    r"\u200b-\u200d\ufe00-\ufe0f]+)"
)


def _pdf_text(value):
    return str(value).replace("\uffff" * 9, "ကတ်လောင်း")


def _pdf_markup(value):
    """Escape text and use the embedded Myanmar font only for Myanmar runs."""
    escaped_text = escape(_pdf_text(value))
    return _MYANMAR_RUN_RE.sub(
        lambda match: f'<font name="{PDF_MYANMAR_FONT}">{match.group(0)}</font>',
        escaped_text,
    )


def _pdf_paragraph(value, style):
    text = _pdf_text(value)
    if _MYANMAR_RUN_RE.search(text):
        style = style.clone(f"{style.name}Myanmar")
        style.shaping = 1
    return Paragraph(_pdf_markup(text), style)


def _register_pdf_fonts():
    font_dir = Path(settings.BASE_DIR) / "static" / "fonts"
    for font_name, filename in (
        (PDF_MYANMAR_FONT, "Pyidaungsu-2.5.3_Regular.ttf"),
        (PDF_MYANMAR_BOLD_FONT, "Pyidaungsu-2.5.3_Bold.ttf"),
    ):
        if font_name not in pdfmetrics.getRegisteredFontNames():
            font = TTFont(font_name, str(font_dir / filename), shapable=True)
            if not font.shapable:
                raise RuntimeError(
                    "Myanmar PDF text requires HarfBuzz shaping. "
                    "Install the project dependencies from requirements.txt."
                )
            pdfmetrics.registerFont(font)
    pdfmetrics.registerFontFamily(
        PDF_MYANMAR_FONT,
        normal=PDF_MYANMAR_FONT,
        bold=PDF_MYANMAR_BOLD_FONT,
        italic=PDF_MYANMAR_FONT,
        boldItalic=PDF_MYANMAR_BOLD_FONT,
    )
    pdfmetrics.registerFontFamily(
        PDF_ENGLISH_FONT,
        normal=PDF_ENGLISH_FONT,
        bold=PDF_ENGLISH_BOLD_FONT,
        italic=PDF_ENGLISH_FONT,
        boldItalic=PDF_ENGLISH_BOLD_FONT,
    )


def build_monthly_report_dataset(main_balances):
    """Return one report dataset for the selected, already-authorized balances."""
    balances = list(main_balances.select_related("user").order_by("id"))
    if not balances:
        raise ValueError("Select at least one Main Balance.")

    month = balances[0].month_label
    records = []
    totals = {
        "money": Decimal("0"),
        "predicted": Decimal("0"),
        "sub_balance": Decimal("0"),
        "expense": Decimal("0"),
        "sub_expense": Decimal("0"),
        "combined_expense": Decimal("0"),
        "remaining": Decimal("0"),
    }
    transaction_totals = {"main": 0, "sub": 0}

    for balance in balances:
        display_name = balance.name or balance.category or "Main Balance"
        report = generate_monthly_report(
            balance.pk, aggregate_same_month=False
        )
        entries = list(
            InputBalance.objects.filter(budget_month=balance).order_by(
                "created_at", "id"
            )
        )
        sub_balances = list(
            SubBalance.objects.filter(budget_month=balance).order_by(
                "created_at", "id"
            )
        )
        expenses = list(
            DailyExpense.objects.filter(budget_month=balance)
            .select_related("category", "sub_balance")
            .order_by("expense_date", "id")
        )
        sub_balance_total = sum(
            (to_mmk(item.allocated_amount) for item in sub_balances),
            Decimal("0"),
        )
        sub_report_by_id = {row["id"]: row for row in report.sub_balance_rows}
        sub_balance_details = []
        for sub_balance in sub_balances:
            sub_report = sub_report_by_id.get(sub_balance.pk, {})
            sub_balance_details.append(
                {
                    "id": sub_balance.pk,
                    "name": sub_balance.name,
                    "allocated_amount": to_mmk(sub_balance.allocated_amount),
                    "total_actual_spent": to_mmk(
                        sub_report.get("total_actual_spent", 0)
                    ),
                    "remaining_balance": to_mmk(
                        sub_report.get("remaining_balance", sub_balance.allocated_amount)
                    ),
                    "transaction_count": sub_report.get("transaction_count", 0),
                    "balance_categories": sub_report.get("balance_categories", []),
                    "transactions": [
                        {
                            **transaction,
                            "amount": to_mmk(abs(transaction["amount"])),
                        }
                        for transaction in sub_report.get("transactions", [])
                    ],
                }
            )
        main_transactions = [
            {
                "date": expense.expense_date,
                "category_name": expense.category.name,
                "note": expense.note,
                "amount": to_mmk(abs(expense.amount)),
            }
            for expense in expenses
            if expense.sub_balance_id is None
        ]
        main_transaction_count = len(main_transactions)
        sub_transaction_count = sum(
            item["transaction_count"] for item in sub_balance_details
        )
        sub_actual_spent = to_mmk(report.sub_balance_total_actual_spent)

        record = {
            "main_balance": balance,
            "display_name": display_name,
            "report": report,
            "money_entries": entries,
            "income_by_category": report.income_by_category,
            "sub_balances": sub_balances,
            "sub_balance_details": sub_balance_details,
            "main_transactions": main_transactions,
            "expenses": expenses,
            "total_sub_balance": to_mmk(sub_balance_total),
            "sub_actual_spent": sub_actual_spent,
            "main_transaction_count": main_transaction_count,
            "sub_transaction_count": sub_transaction_count,
            "transaction_count": main_transaction_count + sub_transaction_count,
            "total_money": report.total_money,
            "predicted_amount": report.total_budget_estimated,
            "total_expense": report.direct_actual_spent,
            "combined_expense": report.combined_actual_spent,
            "remaining_balance": report.remaining_balance,
        }
        records.append(record)

        totals["money"] += record["total_money"]
        totals["predicted"] += record["predicted_amount"]
        totals["sub_balance"] += record["total_sub_balance"]
        totals["expense"] += record["total_expense"]
        totals["sub_expense"] += record["sub_actual_spent"]
        totals["combined_expense"] += record["combined_expense"]
        totals["remaining"] += record["remaining_balance"]
        transaction_totals["main"] += record["main_transaction_count"]
        transaction_totals["sub"] += record["sub_transaction_count"]

    totals = {key: to_mmk(value) for key, value in totals.items()}
    main_balance_names = [record["display_name"] for record in records]
    sub_balance_names = list(dict.fromkeys(
        detail["name"]
        for record in records
        for detail in record["sub_balance_details"]
    ))
    month_balance_ids = set(
        BudgetMonth.objects.filter(
            user_id=balances[0].user_id,
            year=balances[0].year,
            month=balances[0].month,
        ).values_list("id", flat=True)
    )
    selected_balance_ids = {balance.pk for balance in balances}
    report_title = (
        "Monthly Financial Report"
        if selected_balance_ids == month_balance_ids or len(balances) > 1
        else "Main Balance Financial Report"
    )
    return {
        "month": month,
        "report_title": report_title,
        "main_balance_names": main_balance_names,
        "sub_balance_names": sub_balance_names,
        "main_balances": records,
        "totals": totals,
        "total_main_balances": len(records),
        "main_transaction_count": transaction_totals["main"],
        "sub_transaction_count": transaction_totals["sub"],
        "total_transactions": transaction_totals["main"] + transaction_totals["sub"],
    }


CSV_COLUMNS = [
    "Organization",
    "Report Title",
    "Main Balances Included",
    "Sub-Balances Included",
    "Record Type",
    "Month",
    "Main Balance Name",
    "Category",
    "Money Entry Amount",
    "Total Budget Estimated",
    "Sub Balance Name",
    "Sub-Balance Allocated Amount",
    "Transaction Category",
    "Transaction Date",
    "Transaction Amount",
    "Transaction Note",
    "Main Balance Transaction Count",
    "Sub-Balance Transaction Count",
    "Sub-Balance Actual Spending",
    "Combined Actual Spending",
    "Sub-Balance Spent",
    "Sub-Balance Remaining Balance",
    "Total Money Available",
    "Main Balance Direct Actual Spending",
    "Main Balance Remaining Balance",
    "Income Category Total",
    "Balance Category Basis",
    "Category Allocated Amount",
    "Category Actual Spending",
    "Category Remaining Balance",
]


def build_report_csv(dataset):
    """Write all selected entry, Sub Balance, and expense records as CSV rows."""
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()

    for record in dataset["main_balances"]:
        balance = record["main_balance"]
        common = {
            "Organization": "Pocket Money Management System",
            "Report Title": f"{dataset['report_title']} — {dataset['month']}",
            "Main Balances Included": ", ".join(dataset["main_balance_names"]),
            "Sub-Balances Included": ", ".join(dataset["sub_balance_names"]),
            "Month": dataset["month"],
            "Main Balance Name": record["display_name"],
            "Total Budget Estimated": record["predicted_amount"],
            "Main Balance Transaction Count": record["main_transaction_count"],
            "Sub-Balance Transaction Count": record["sub_transaction_count"],
            "Sub-Balance Actual Spending": record["sub_actual_spent"],
            "Combined Actual Spending": record["combined_expense"],
            "Total Money Available": record["total_money"],
            "Main Balance Direct Actual Spending": record["total_expense"],
            "Main Balance Remaining Balance": record["remaining_balance"],
        }
        for entry in record["money_entries"]:
            writer.writerow(
                {
                    **common,
                    "Record Type": "Money Entry",
                    "Category": entry.category,
                    "Money Entry Amount": entry.amount,
                }
            )
        for income_category in record["income_by_category"]:
            writer.writerow(
                {
                    **common,
                    "Record Type": "Income Category Summary",
                    "Category": income_category["category_name"],
                    "Income Category Total": income_category["amount"],
                }
            )
        sub_details_by_id = {
            item["id"]: item for item in record["sub_balance_details"]
        }
        for sub_balance in record["sub_balances"]:
            sub_detail = sub_details_by_id[sub_balance.pk]
            writer.writerow(
                {
                    **common,
                    "Record Type": "Sub Balance",
                    "Sub Balance Name": sub_balance.name,
                    "Sub-Balance Allocated Amount": sub_detail["allocated_amount"],
                    "Sub-Balance Transaction Count": sub_detail["transaction_count"],
                    "Sub-Balance Spent": sub_detail["total_actual_spent"],
                    "Sub-Balance Remaining Balance": sub_detail["remaining_balance"],
                }
            )
            for category in sub_detail["balance_categories"]:
                writer.writerow(
                    {
                        **common,
                        "Record Type": "Sub-Balance Category Balance",
                        "Sub Balance Name": sub_balance.name,
                        "Category": category["name"],
                        "Balance Category Basis": category["basis"],
                        "Category Allocated Amount": category["allocated_amount"],
                        "Category Actual Spending": category["actual_amount"],
                        "Category Remaining Balance": category["remaining_balance"],
                    }
                )
        for expense in record["expenses"]:
            sub_detail = sub_details_by_id.get(expense.sub_balance_id, {})
            writer.writerow(
                {
                    **common,
                    "Record Type": "Expense",
                    "Sub Balance Name": (
                        expense.sub_balance.name if expense.sub_balance else ""
                    ),
                    "Transaction Category": expense.category.name,
                    "Transaction Date": expense.expense_date.isoformat(),
                    "Transaction Amount": abs(to_mmk(expense.amount)),
                    "Transaction Note": expense.note or "",
                    "Sub-Balance Spent": sub_detail.get("total_actual_spent", ""),
                    "Sub-Balance Remaining Balance": sub_detail.get(
                        "remaining_balance", ""
                    ),
                }
            )
        if not record["money_entries"] and not record["sub_balances"] and not record["expenses"]:
            writer.writerow({**common, "Record Type": "Main Balance"})

    return output.getvalue().encode("utf-8-sig")


def build_report_xlsx(dataset):
    """Create a branded, color-formatted workbook with every selected record."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Monthly Report"
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_properties.tabColor = "245CFF"
    worksheet.merge_cells("B1:F1")
    worksheet.merge_cells("B2:F2")
    worksheet.merge_cells("B3:F3")
    worksheet.merge_cells("B4:F4")
    worksheet.merge_cells("B5:F5")
    worksheet["B1"] = "Pocket Money Management System"
    worksheet["B1"].font = Font(name="Arial", size=16, bold=True, color="092D72")
    worksheet["B2"] = dataset["report_title"]
    worksheet["B2"].font = Font(name="Arial", size=12, bold=True, color="245CFF")
    worksheet["B3"] = f"Month: {dataset['month']}"
    worksheet["B3"].font = Font(name="Arial", size=10, color="385A91")
    worksheet["B4"] = "Main Balances: " + (", ".join(dataset["main_balance_names"]) or "None")
    worksheet["B4"].font = Font(name="Arial", size=10, color="385A91")
    worksheet["B5"] = "Sub-Balances: " + (", ".join(dataset["sub_balance_names"]) or "None")
    worksheet["B5"].font = Font(name="Arial", size=10, color="385A91")
    worksheet.row_dimensions[1].height = 38
    worksheet.row_dimensions[2].height = 23
    worksheet.row_dimensions[3].height = 20
    worksheet.row_dimensions[4].height = 30
    worksheet.row_dimensions[5].height = 30
    worksheet.row_dimensions[6].height = 10
    worksheet.column_dimensions["A"].width = 21

    logo_path = Path(settings.BASE_DIR) / "static" / "images" / "pmms-logo.jpg"
    logo = SpreadsheetImage(str(logo_path))
    logo.width = 120
    logo.height = 60
    worksheet.add_image(logo, "A1")

    header_row = 7
    header_fill = PatternFill("solid", fgColor="092D72")
    odd_fill = PatternFill("solid", fgColor="F2F7FF")
    even_fill = PatternFill("solid", fgColor="FFFFFF")
    thin_blue = Side(style="thin", color="DCE9FF")
    number_columns = {
        "Main Balance Transaction Count",
        "Sub-Balance Transaction Count",
    }
    money_columns = {
        "Money Entry Amount",
        "Total Budget Estimated",
        "Sub-Balance Allocated Amount",
        "Transaction Amount",
        "Sub-Balance Actual Spending",
        "Combined Actual Spending",
        "Sub-Balance Spent",
        "Sub-Balance Remaining Balance",
        "Total Money Available",
        "Main Balance Direct Actual Spending",
        "Main Balance Remaining Balance",
        "Income Category Total",
        "Category Allocated Amount",
        "Category Actual Spending",
        "Category Remaining Balance",
    }
    rows = list(
        csv.DictReader(
            StringIO(build_report_csv(dataset).decode("utf-8-sig"))
        )
    )
    for column_index, heading in enumerate(CSV_COLUMNS, start=1):
        cell = worksheet.cell(row=header_row, column=column_index, value=heading)
        cell.fill = header_fill
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = Border(bottom=Side(style="medium", color="245CFF"))
    worksheet.row_dimensions[header_row].height = 34

    for row_index, record in enumerate(rows, start=header_row + 1):
        fill = odd_fill if row_index % 2 == 0 else even_fill
        for column_index, heading in enumerate(CSV_COLUMNS, start=1):
            value = record.get(heading, "")
            if value and heading in number_columns:
                value = int(value)
            elif value and heading in money_columns:
                value = float(value)
            cell = worksheet.cell(
                row=row_index, column=column_index, value=value or None
            )
            cell.fill = fill
            cell.font = Font(name="Arial", size=10, color="183263")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(bottom=thin_blue)
            if heading in money_columns:
                cell.number_format = '#,##0.##;[Red]-#,##0.##'

    last_column = get_column_letter(len(CSV_COLUMNS))
    last_row = max(header_row, worksheet.max_row)
    worksheet.freeze_panes = "A8"
    worksheet.auto_filter.ref = f"A{header_row}:{last_column}{last_row}"
    worksheet.print_title_rows = f"1:{header_row}"
    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.fitToWidth = 1
    worksheet.page_setup.fitToHeight = 0
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True
    for column_index, heading in enumerate(CSV_COLUMNS, start=1):
        values = [heading] + [str(row.get(heading, "")) for row in rows]
        width = min(max(max(len(value) for value in values) + 2, 14), 38)
        worksheet.column_dimensions[get_column_letter(column_index)].width = width

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_report_pdf(dataset):
    """Render a printable PDF from the same structured dataset as the web report."""
    _register_pdf_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=f"PMMS {dataset['report_title']} — {dataset['month']}",
    )
    styles = getSampleStyleSheet()
    for style_name, style in styles.byName.items():
        if "Bold" in style_name or "Heading" in style_name or "Title" in style_name:
            style.fontName = PDF_ENGLISH_BOLD_FONT
        else:
            style.fontName = PDF_ENGLISH_FONT
    styles["Title"].textColor = colors.HexColor("#092d72")
    styles["Heading2"].textColor = colors.HexColor("#092d72")
    styles["Heading3"].textColor = colors.HexColor("#245cff")
    styles["BodyText"].textColor = colors.HexColor("#183263")
    styles["BodyText"].fontSize = 11
    styles["BodyText"].leading = 15
    logo = Image(
        str(Path(settings.BASE_DIR) / "static" / "images" / "pmms-logo.jpg"),
        width=1.45 * inch,
        height=0.72 * inch,
        kind="proportional",
    )
    brand_style = styles["Heading2"].clone("ReportBrand")
    brand_style.fontSize = 15
    brand_style.leading = 19
    report_style = styles["BodyText"].clone("ReportBrandSubtitle")
    report_style.textColor = colors.HexColor("#385a91")
    story = [
        Table(
            [[
                logo,
                [
                    Paragraph("Pocket Money Management System", brand_style),
                    _pdf_paragraph(dataset["report_title"], report_style),
                ],
            ]],
            colWidths=[1.75 * inch, 5.25 * inch],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#edf4ff")),
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b7ccff")),
                    ("LINEBELOW", (0, 0), (-1, -1), 2, colors.HexColor("#245cff")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            ),
        ),
        Spacer(1, 12),
        _pdf_paragraph(f"Month: {dataset['month']}", styles["Heading2"]),
        _pdf_paragraph(
            "Main Balances: " + (", ".join(dataset["main_balance_names"]) or "None"),
            styles["BodyText"],
        ),
        _pdf_paragraph(
            "Sub-Balances: " + (", ".join(dataset["sub_balance_names"]) or "None"),
            styles["BodyText"],
        ),
        Spacer(1, 8),
        Paragraph("Monthly Overview", styles["Heading2"]),
        Paragraph(f"Total Main Balances: {dataset['total_main_balances']}", styles["BodyText"]),
    ]
    for label, key in (
        ("Total Money Available", "money"),
        ("Total Budget Estimated", "predicted"),
        ("Total Sub-Balance Allocated Amount", "sub_balance"),
        ("Main Balance — Direct Actual Spending", "expense"),
        ("Sub-Balance Actual Spending", "sub_expense"),
        ("Combined Actual Spending", "combined_expense"),
        ("Main Balance Remaining Balance", "remaining"),
    ):
        story.append(
            Paragraph(
                f"{label}: {format_mmk(dataset['totals'][key])}",
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            f"Transactions: {dataset['total_transactions']} total "
            f"({dataset['main_transaction_count']} Main Balance, "
            f"{dataset['sub_transaction_count']} Sub-Balance)",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 12))

    for record in dataset["main_balances"]:
        balance = record["main_balance"]
        story.extend(
            [
                _pdf_paragraph(
                    f"Main Balance: {record['display_name']}",
                    styles["Heading2"],
                ),
                Paragraph("Money Entries", styles["Heading3"]),
            ]
        )
        entry_rows = [["Category", "Amount"]]
        entry_rows.extend(
            [
                [entry.category or "Income", format_mmk(entry.amount)]
                for entry in record["money_entries"]
            ]
        )
        if not record["money_entries"]:
            entry_rows.append(["No money entries.", ""])
        entry_rows.append(["Total Money", format_mmk(record["total_money"])])
        story.append(_report_table(entry_rows))
        income_rows = [["Income Category", "Income Amount", "Entries"]]
        income_rows.extend(
            [
                item["category_name"],
                format_mmk(item["amount"]),
                str(item["entry_count"]),
            ]
            for item in record["income_by_category"]
        )
        if not record["income_by_category"]:
            income_rows.append(["No income entries.", "", ""])
        story.extend(
            [
                Paragraph("Income by Category", styles["Heading3"]),
                _report_table(income_rows),
            ]
        )
        story.extend(
            [
                Spacer(1, 6),
                Paragraph("Total Budget Estimated", styles["Heading3"]),
                Paragraph(
                    f"Total Budget Estimated: {format_mmk(record['predicted_amount'])}",
                    styles["BodyText"],
                ),
                Paragraph(
                    f"Main Balance — Direct Actual Spending: {format_mmk(record['total_expense'])} "
                    f"({record['main_transaction_count']} transactions)",
                    styles["BodyText"],
                ),
                Paragraph(
                    f"Sub-Balance Actual Spending: {format_mmk(record['sub_actual_spent'])} "
                    f"({record['sub_transaction_count']} transactions)",
                    styles["BodyText"],
                ),
                Paragraph(
                    f"Combined Actual Spending: {format_mmk(record['combined_expense'])} "
                    f"({record['transaction_count']} transactions)",
                    styles["BodyText"],
                ),
                Paragraph(
                    f"Total Sub-Balance Allocated Amount: {format_mmk(record['total_sub_balance'])}",
                    styles["BodyText"],
                ),
                Paragraph("Sub Balances", styles["Heading3"]),
            ]
        )
        sub_rows = [["Name", "Allocated", "Spent", "Remaining", "Transactions"]]
        sub_rows.extend(
            [
                item["name"],
                format_mmk(item["allocated_amount"]),
                format_mmk(item["total_actual_spent"]),
                format_mmk(item["remaining_balance"]),
                str(item["transaction_count"]),
            ]
            for item in record["sub_balance_details"]
        )
        if not record["sub_balance_details"]:
            sub_rows.append(["No sub balances yet.", "", "", "", ""])
        sub_rows.append(
            [
                "Totals",
                format_mmk(record["total_sub_balance"]),
                format_mmk(record["sub_actual_spent"]),
                "",
                str(record["sub_transaction_count"]),
            ]
        )
        story.append(_report_table(sub_rows))
        for sub_balance in record["sub_balance_details"]:
            category_rows = [
                ["Sub-Balance Category", "Basis", "Allocated", "Spent", "Remaining"]
            ]
            category_rows.extend(
                [
                    category["name"],
                    category["basis"],
                    format_mmk(category["allocated_amount"]),
                    format_mmk(category["actual_amount"]),
                    format_mmk(category["remaining_balance"]),
                ]
                for category in sub_balance["balance_categories"]
            )
            if len(category_rows) == 1:
                category_rows.append(["No category allocations.", "", "", "", ""])
            story.extend(
                [
                    _pdf_paragraph(
                        f"{sub_balance['name']} Category Balances",
                        styles["Heading3"],
                    ),
                    _report_table(category_rows),
                ]
            )
        story.extend(
            [Spacer(1, 6), Paragraph("Main Balance Transactions", styles["Heading3"])]
        )
        main_transaction_rows = [
            ["Transaction Date", "Category", "Transaction Note", "Amount Spent"]
        ]
        main_transaction_rows.extend(
            [
                transaction["date"].isoformat(),
                transaction["category_name"],
                transaction["note"] or "—",
                format_mmk(transaction["amount"]),
            ]
            for transaction in record["main_transactions"]
        )
        if not record["main_transactions"]:
            main_transaction_rows.append(
                ["", "No direct transactions recorded.", "", ""]
            )
        story.append(_report_table(main_transaction_rows))
        story.extend(
            [Spacer(1, 6), Paragraph("Sub-Balance Transactions", styles["Heading3"])]
        )
        for sub_balance in record["sub_balance_details"]:
            story.append(
                _pdf_paragraph(
                    f"{sub_balance['name']}: "
                    f"{format_mmk(sub_balance['total_actual_spent'])} spent; "
                    f"{format_mmk(sub_balance['remaining_balance'])} remaining; "
                    f"{sub_balance['transaction_count']} transactions",
                    styles["BodyText"],
                )
            )
            sub_transaction_rows = [
                ["Transaction Date", "Category", "Transaction Note", "Amount Spent"]
            ]
            sub_transaction_rows.extend(
                [
                    transaction["date"].isoformat(),
                    transaction["category_name"],
                    transaction["note"] or "—",
                    format_mmk(transaction["amount"]),
                ]
                for transaction in sub_balance["transactions"]
            )
            if not sub_balance["transactions"]:
                sub_transaction_rows.append(
                    ["", "No transactions recorded.", "", ""]
                )
            story.append(_report_table(sub_transaction_rows))
        story.extend(
            [
                Spacer(1, 6),
                Paragraph(
                    f"Combined Actual Spending: {format_mmk(record['combined_expense'])}",
                    styles["BodyText"],
                ),
                Paragraph(
                    f"Main Balance Remaining Balance: {format_mmk(record['remaining_balance'])}",
                    styles["BodyText"],
                ),
                Spacer(1, 14),
            ]
        )

    story.append(Paragraph("Monthly Summary", styles["Heading2"]))
    summary_rows = [["Metric", "Amount"]]
    summary_rows.extend(
        [
            [label, format_mmk(dataset["totals"][key])]
            for label, key in (
                ("Total Money Available", "money"),
                ("Total Budget Estimated", "predicted"),
                ("Total Sub-Balance Allocated Amount", "sub_balance"),
                ("Main Balance — Direct Actual Spending", "expense"),
                ("Sub-Balance Actual Spending", "sub_expense"),
                ("Combined Actual Spending", "combined_expense"),
                ("Main Balance Remaining Balance", "remaining"),
            )
        ]
    )
    story.append(_report_table(summary_rows))
    def paint_report_page(canvas, _document):
        canvas.saveState()
        canvas.setFont(PDF_ENGLISH_FONT, 10)
        canvas.setFillColor(colors.HexColor("#e7f0ff"))
        canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#245cff"))
        canvas.rect(0, letter[1] - 0.12 * inch, letter[0], 0.12 * inch, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#9db8e8"))
        canvas.setLineWidth(1)
        canvas.rect(
            0.32 * inch,
            0.32 * inch,
            letter[0] - 0.64 * inch,
            letter[1] - 0.64 * inch,
            fill=0,
            stroke=1,
        )
        canvas.restoreState()

    document.build(story, onFirstPage=paint_report_page, onLaterPages=paint_report_page)
    return buffer.getvalue()


def _report_table(rows):
    header_style = ParagraphStyle(
        "MyanmarTableHeader",
        fontName=PDF_ENGLISH_BOLD_FONT,
        fontSize=12,
        leading=13,
        textColor=colors.HexColor("#092d72"),
    )
    body_style = ParagraphStyle(
        "MyanmarTableBody",
        fontName=PDF_ENGLISH_FONT,
        fontSize=11,
        leading=13,
        textColor=colors.HexColor("#183263"),
    )
    shaped_header_style = header_style.clone("MyanmarReportTableHeader")
    shaped_header_style.fontName = PDF_MYANMAR_BOLD_FONT
    shaped_header_style.shaping = 1
    shaped_body_style = body_style.clone("MyanmarReportTableBody")
    shaped_body_style.fontName = PDF_MYANMAR_FONT
    shaped_body_style.shaping = 1

    def make_cell_paragraph(cell, row_index):
        text = _pdf_text(cell)
        if _MYANMAR_RUN_RE.search(text):
            style = shaped_header_style if row_index == 0 else shaped_body_style
        else:
            style = header_style if row_index == 0 else body_style
        return Paragraph(escape(text), style)

    rows = [
        [make_cell_paragraph(cell, row_index) for cell in row]
        for row_index, row in enumerate(rows)
    ]
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5efff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#092d72")),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#183263")),
                ("FONTNAME", (0, 0), (-1, 0), PDF_ENGLISH_BOLD_FONT),
                ("FONTNAME", (0, 1), (-1, -1), PDF_ENGLISH_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("FONTSIZE", (0, 1), (-1, -1), 11),
                ("LEADING", (0, 0), (-1, -1), 13),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b7ccff")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table
