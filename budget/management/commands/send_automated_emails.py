"""Django management command to trigger automated report emails."""

from django.core.management.base import BaseCommand

from budget.services.automated_email_service import process_automated_emails


class Command(BaseCommand):
    help = "Send the scheduled full monthly report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send the full monthly report now, ignoring the month-end schedule.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        results = process_automated_emails(force=force)

        for item in results["sent"]:
            self.stdout.write(self.style.SUCCESS(f"Sent: {item}"))
        failed = set(results.get("failed", []))
        for item in results["skipped"]:
            if item in failed:
                continue
            self.stdout.write(self.style.WARNING(f"Skipped: {item}"))
        for item in results.get("failed", []):
            self.stdout.write(self.style.ERROR(f"Failed: {item}"))

        if not results["sent"] and not results["skipped"] and not failed:
            self.stdout.write(self.style.WARNING("No emails were sent."))