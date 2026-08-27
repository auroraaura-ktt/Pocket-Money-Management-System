"""Django management command to create an initial admin account.

Creates a superuser with configurable default credentials (username, email,
password). Useful on first setup so the admin dashboard has a known login.

Defaults can be overridden either through command-line options or the
environment variables POCKET_ADMIN_USERNAME / POCKET_ADMIN_EMAIL /
POCKET_ADMIN_PASSWORD (for example on a fresh Vercel deploy).
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEFAULT_USERNAME = os.getenv("POCKET_ADMIN_USERNAME", "admin")
DEFAULT_EMAIL = os.getenv("POCKET_ADMIN_EMAIL", "admin@example.com")
DEFAULT_PASSWORD = os.getenv("POCKET_ADMIN_PASSWORD", "admin12345")


class Command(BaseCommand):
    help = "Create (or optionally reset) the initial superuser admin account."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=DEFAULT_USERNAME)
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Force the given password on an existing admin account.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        email = options["email"]
        password = options["password"]

        admin = User.objects.filter(username=username).first()

        if admin is None:
            admin = User.objects.create_superuser(
                username=username, email=email, password=password
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created admin account '{username}' (is_staff, is_superuser)."
                )
            )
        elif options["reset_password"]:
            admin.email = email
            admin.set_password(password)
            admin.is_staff = True
            admin.is_superuser = True
            admin.save()
            self.stdout.write(
                self.style.WARNING(
                    f"Admin '{username}' already existed; password was reset."
                )
            )
        else:
            admin.email = email
            admin.is_staff = True
            admin.is_superuser = True
            admin.save()
            self.stdout.write(
                self.style.WARNING(
                    f"Admin '{username}' already exists; password left unchanged. "
                    "Use --reset-password to force the configured password."
                )
            )

        self.stdout.write("You can now log in at /admin-login/ with these credentials.")