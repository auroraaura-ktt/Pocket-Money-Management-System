"""Django management command to trigger the two monthly report schedules.

Run manually or from an external cron / Task Scheduler at 00:00 on the 1st and
16th of each month. The service itself only acts on the relevant day, so this
command is safe to run daily; use ``--force`` to run both schedules immediately.
"""

from django.core.management.base import BaseCommand

from budget.services.monthly_schedule_service import process_monthly_schedules


class Command(BaseCommand):
    help = "Run the monthly report schedules (day 1: previous month; day 16: no-estimate overview)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force run both monthly schedules immediately, ignoring the day of month.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        results = process_monthly_schedules(force=force)

        for item in results["sent"]:
            self.stdout.write(self.style.SUCCESS(f"Sent: {item}"))
        for item in results["skipped"]:
            self.stdout.write(self.style.WARNING(f"Skipped: {item}"))

        if not results["sent"] and not results["skipped"]:
            self.stdout.write(self.style.WARNING("Nothing to do today."))
