"""Django settings for Pocket Money Management System."""

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-me-in-production")

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

# True when the app runs inside a serverless (read-only) Vercel container.
ON_VERCEL = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))

ALLOWED_HOSTS = [
    host.strip().replace("https://", "").replace("http://", "").rstrip("/")
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

if ON_VERCEL:
    ALLOWED_HOSTS.append(".vercel.app")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

if ON_VERCEL:
    CSRF_TRUSTED_ORIGINS.append("https://*.vercel.app")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "budget",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "budget.middleware.AutoEmailMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# On Vercel the deployment bundle (/var/task) is READ-ONLY, so a SQLite file
# living next to the code can never be written to ("attempt to write a readonly
# database").  For any real deployment set DATABASE_URL to a hosted Postgres
# instance (Vercel Postgres, Neon, Supabase, Railway...).
#
# If DATABASE_URL is not set while running on Vercel we fall back to copying the
# bundled SQLite file into /tmp (the only writable location).  That keeps the
# site functional, but /tmp is ephemeral: data is lost when the lambda is
# recycled.  It is a stop-gap only.

if "test" in sys.argv:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test_db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }
else:
    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

    if DATABASE_URL:
        import dj_database_url

        DATABASES = {
            "default": dj_database_url.parse(
                DATABASE_URL,
                conn_max_age=0,
                ssl_require=os.getenv("DATABASE_SSL_REQUIRE", "True").lower()
                in ("true", "1", "yes"),
            )
        }
    else:
        SQLITE_PATH = BASE_DIR / "db.sqlite3"

        if ON_VERCEL:
            # /tmp is the only writable path in the serverless runtime.
            TMP_SQLITE_PATH = Path("/tmp") / "db.sqlite3"
            if not TMP_SQLITE_PATH.exists():
                try:
                    if SQLITE_PATH.exists():
                        shutil.copy(SQLITE_PATH, TMP_SQLITE_PATH)
                    else:
                        TMP_SQLITE_PATH.touch()
                except OSError:
                    pass
            SQLITE_PATH = TMP_SQLITE_PATH

        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": SQLITE_PATH,
                "OPTIONS": {"timeout": 20},
            }
        }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Where @login_required sends anonymous users (our custom login page).
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email settings
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.getenv("SMTP_FROM", EMAIL_HOST_USER)
EMAIL_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Pocket Management System")

MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
