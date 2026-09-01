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


class AutoEmailMiddleware:
    """Run the scheduled email job at most once per day per process."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._maybe_run_automated_emails()
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