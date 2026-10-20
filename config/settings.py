"""Settings for the local curator prototype.

Domain data lives in a JSON file (see ``FITNESS_GRAPH_PATH``), not in a
database, so no database is configured and no sessions are used.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Local-only prototype: the key is not a secret. Override via the environment
# if you ever expose this beyond localhost (which is out of scope).
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-local-prototype-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

# Location of the canonical JSON document.
FITNESS_GRAPH_PATH = Path(
    os.environ.get("FITNESS_GRAPH_PATH", BASE_DIR / "data" / "fitness_graph.json")
)
# Maximum size accepted for an uploaded import file.
FITNESS_IMPORT_MAX_BYTES = 5 * 1024 * 1024

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "fitness",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
