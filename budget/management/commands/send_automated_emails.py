"""Django management command to trigger automated report emails."""

from django.core.management.base import BaseCommand

from budget.services.automated_email_service import process_automated_emails


class Command(BaseCommand):
    help = "Send automated period and monthly report emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force send all period reports and monthly full report, ignoring schedule.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        results = process_automated_emails(force=force)

        for item in results["sent"]:
            self.stdout.write(self.style.SUCCESS(f"Sent: {item}"))
        for item in results["skipped"]:
            self.stdout.write(self.style.WARNING(f"Skipped: {item}"))

        if not results["sent"] and not results["skipped"]:
            self.stdout.write(self.style.WARNING("No emails were sent."))