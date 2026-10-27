"""Independent 15-day reports for monthly Sub Balances.

The deadline is the category's local creation date plus 15 calendar days. The
first scheduler run on or after that date attempts delivery. A dedicated
ReportEmailLog key makes retries idempotent without touching other schedules.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from budget.models import SubBalance, ReportEmailLog
from budget.services.email_service import send_sub_balance_report_email

logger = logging.getLogger(__name__)


def sub_balance_15day_key(sub_balance_id: int) -> str:
    return f"sb15_{sub_balance_id}"


def process_sub_balance_schedules(today=None) -> dict:
    today = today or timezone.localdate()
    result = {"sent": [], "skipped": []}
    sub_balances = SubBalance.objects.select_related(
        "budget_month", "budget_month__user"
    ).all()
    for sub_balance in sub_balances.iterator():
        created_date = timezone.localtime(sub_balance.created_at).date()
        deadline = created_date + timedelta(days=15)
        if today < deadline:
            continue
        key = sub_balance_15day_key(sub_balance.id)
        try:
            with transaction.atomic():
                ReportEmailLog.objects.create(
                    budget_month_id=sub_balance.budget_month_id, report_type=key
                )
        except IntegrityError:
            continue
        try:
            delivered = send_sub_balance_report_email(sub_balance)
        except Exception:
            logger.exception("15-day Sub Balance report failed for %s", sub_balance.id)
            delivered = False
        if delivered:
            result["sent"].append(sub_balance.id)
        else:
            ReportEmailLog.objects.filter(
                budget_month_id=sub_balance.budget_month_id, report_type=key
            ).delete()
            result["skipped"].append(sub_balance.id)
    return result
