"""Settings for the local curator prototype.

Domain data lives in one JSON file per user (see ``FITNESS_USERS_DIR``), not in a
database, so no database is configured and no sessions are used.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Local-only prototype: the key is not a secret. Override via the environment
# if you ever expose this beyond localhost (which is out of scope).
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-local-prototype-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"] + [
    h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h
]
# Needed when TLS terminates at an ingress (e.g. "https://data-card.k8s.ucar.edu").
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

# One canonical JSON document per user: <FITNESS_USERS_DIR>/<name>.json, served at /u/<name>/.
FITNESS_USERS_DIR = Path(os.environ.get("FITNESS_USERS_DIR", BASE_DIR / "data" / "users"))
# When "1", every save is committed and pushed from the git checkout at FITNESS_GIT_REPO.
FITNESS_GIT_SYNC = os.environ.get("FITNESS_GIT_SYNC", "0") == "1"
FITNESS_GIT_REPO = Path(os.environ.get("FITNESS_GIT_REPO", BASE_DIR / "data"))
# Maximum size accepted for an uploaded import file.
FITNESS_IMPORT_MAX_BYTES = 5 * 1024 * 1024

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "fitness",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "fitness.middleware.UserPrefixMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "fitness.context.user",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# No ORM models: the JSON file is the source of truth.
DATABASES = {}

# Flash messages are stored in a cookie so no session table is needed.
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# Absolute: a relative value would pick up the per-request /u/<name>/ script prefix.
STATIC_URL = "/static/"
# Target of ``collectstatic`` in the container image; served by WhiteNoise.
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
