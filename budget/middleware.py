"""Request-triggered delivery for the project's scheduled report emails.

The middleware runs final-month reports on the last day, monthly reports on
the 1st and 16th, and due Sub-Balance reports. Successful runs are cached for
the day; failed deliveries are retried after a short cooldown. ReportEmailLog
deduplicates messages that were successfully sent.
"""

import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = "pmms_auto_emails_last_run"
CACHE_TTL = 60 * 60 * 12  # 12 hours; date-specific keys reset at midnight
FAILURE_RETRY_TTL = 5 * 60

# Separate cache key for the two monthly report schedules (1st / 16th).
MONTHLY_CACHE_KEY = "pmms_monthly_schedules_last_run"
SUB_BALANCE_CACHE_KEY = "pmms_sub_balance_schedules_last_run"


class AutoEmailMiddleware:
    """Run scheduled jobs daily, retrying failures after a short cooldown."""

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

        retry = False
        try:
            from budget.services.automated_email_service import (
                process_automated_emails,
            )

            results = process_automated_emails(today=today)
            if results["sent"]:
                logger.info("Automated emails sent: %s", results["sent"])
            retry = bool(results.get("failed"))
        except Exception:
            # Never break the request because of the email scheduler.
            logger.exception("Automated email scheduler failed")
            retry = True

        cache.set(cache_key, True, FAILURE_RETRY_TTL if retry else CACHE_TTL)

    def _maybe_run_monthly_schedules(self) -> None:
        """Run the monthly report schedules (1st / 16th).

        Fully isolated from ``_maybe_run_automated_emails``: its own cache key,
        its own try/except, and delivery deduplication handled inside
        ``process_monthly_schedules`` via ``ReportEmailLog``.
        """
        today = timezone.localdate()
        cache_key = f"{MONTHLY_CACHE_KEY}:{today.isoformat()}"
        if cache.get(cache_key):
            return

        retry = False
        try:
            from budget.services.monthly_schedule_service import (
                process_monthly_schedules,
            )

            results = process_monthly_schedules(today=today)
            if results["sent"]:
                logger.info("Monthly schedule emails sent: %s", results["sent"])
            retry = bool(results.get("failed"))
        except Exception:
            # Never break the request because of the monthly scheduler.
            logger.exception("Monthly schedule email scheduler failed")
            retry = True

        cache.set(cache_key, True, FAILURE_RETRY_TTL if retry else CACHE_TTL)

    def _maybe_run_sub_balance_schedules(self) -> None:
        """Run independent per-Sub-Balance 15-day reports once per day."""
        today = timezone.localdate()
        cache_key = f"{SUB_BALANCE_CACHE_KEY}:{today.isoformat()}"
        if cache.get(cache_key):
            return
        retry = False
        try:
            from budget.services.sub_balance_schedule_service import (
                process_sub_balance_schedules,
            )

            results = process_sub_balance_schedules(today=today)
            if results["sent"]:
                logger.info("15-day Sub Balance reports sent: %s", results["sent"])
            retry = bool(results.get("failed"))
        except Exception:
            logger.exception("15-day Sub Balance report scheduler failed")
            retry = True
        cache.set(cache_key, True, FAILURE_RETRY_TTL if retry else CACHE_TTL)
