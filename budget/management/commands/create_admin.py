"""Create an initial admin account with an explicitly configured password.

Username and email have convenience defaults; the password must come from the
POCKET_ADMIN_PASSWORD environment variable or the --password option.
"""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

DEFAULT_USERNAME = os.getenv("POCKET_ADMIN_USERNAME", "admin")
DEFAULT_EMAIL = os.getenv("POCKET_ADMIN_EMAIL", "admin@example.com")
DEFAULT_PASSWORD = os.getenv("POCKET_ADMIN_PASSWORD")


class Command(BaseCommand):
    help = "Create (or optionally reset) the initial superuser admin account."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=DEFAULT_USERNAME)
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Admin password (prefer POCKET_ADMIN_PASSWORD from a secret store).",
        )
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
        if not password:
            raise CommandError(
                "Set POCKET_ADMIN_PASSWORD or provide --password; no default "
                "admin password is configured."
            )

        try:
            validate_password(password, user=User(username=username, email=email))
        except ValidationError as exc:
            raise CommandError(
                "Admin password does not meet the configured password policy: "
                + "; ".join(exc.messages)
            ) from exc

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

        self.stdout.write("Admin account is ready; sign in at /admin-login/.")