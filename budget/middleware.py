"""Automatic daily trigger for scheduled report emails.

On every request this middleware checks whether the scheduled automated
emails have already run today. If not, it runs
``process_automated_emails`` which sends:

- each budget portion's report on the portion's last day (10, 20),
- the final full monthly report on the last day of the month.

The check is cached once per day so the cost on normal requests is a
single cache lookup. Actual email delivery is deduplicated via the
ReportEmailLog model, so repeated runs on the same day never send
duplicate emails.
"""

import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = "pmms_auto_emails_last_run"
CACHE_TTL = 60 * 60 * 12  # 12 hours; also reset naturally at midnight

# Separate cache key for the two monthly report schedules (1st / 16th).
MONTHLY_CACHE_KEY = "pmms_monthly_schedules_last_run"
SUB_BALANCE_CACHE_KEY = "pmms_sub_balance_schedules_last_run"


class AutoEmailMiddleware:
    """Run the scheduled email job at most once per day per process."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._maybe_run_automated_emails()
        self._maybe_run_monthly_schedules()
        self._maybe_run_sub_balance_schedules()
        return self.get_response(request)

    def _maybe_run_automated_emails(self) -> None:
        today = timezone.localdate()
        cache_key = f"{CACHE_KEY}:{today.isoformat()}"
        if cache.get(cache_key):
            return

        try:
            from budget.services.automated_email_service import (
                process_automated_emails,
            )

            results = process_automated_emails(today=today)
            if results["sent"]:
                logger.info("Automated emails sent: %s", results["sent"])
        except Exception:
            # Never break the request because of the email scheduler.
            logger.exception("Automated email scheduler failed")

        cache.set(cache_key, True, CACHE_TTL)


    def _maybe_run_monthly_schedules(self) -> None:
        """Run the two monthly report schedules (1st / 16th) once per day.

        Fully isolated from ``_maybe_run_automated_emails``: its own cache key,
        its own try/except, and delivery deduplication handled inside
        ``process_monthly_schedules`` via ``ReportEmailLog``.
        """
        today = timezone.localdate()
        cache_key = f"{MONTHLY_CACHE_KEY}:{today.isoformat()}"
        if cache.get(cache_key):
            return

        try:
            from budget.services.monthly_schedule_service import (
                process_monthly_schedules,
            )

            results = process_monthly_schedules(today=today)
            if results["sent"]:
                logger.info("Monthly schedule emails sent: %s", results["sent"])
        except Exception:
            # Never break the request because of the monthly scheduler.
            logger.exception("Monthly schedule email scheduler failed")

        cache.set(cache_key, True, CACHE_TTL)

    def _maybe_run_sub_balance_schedules(self) -> None:
        """Run independent per-Sub-Balance 15-day reports once per day."""
        today = timezone.localdate()
        cache_key = f"{SUB_BALANCE_CACHE_KEY}:{today.isoformat()}"
        if cache.get(cache_key):
            return
        try:
            from budget.services.sub_balance_schedule_service import (
                process_sub_balance_schedules,
            )

            results = process_sub_balance_schedules(today=today)
            if results["sent"]:
                logger.info("15-day Sub Balance reports sent: %s", results["sent"])
        except Exception:
            logger.exception("15-day Sub Balance report scheduler failed")
        cache.set(cache_key, True, CACHE_TTL)
