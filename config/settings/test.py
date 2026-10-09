"""Settings used by the automated test suite."""

from .base import *

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
FIELD_ENCRYPTION_KEY = ""
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
import os  # noqa: E402

if os.environ.get("TEST_DATABASE_URL"):
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(os.environ["TEST_DATABASE_URL"])}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

MONITOR_MIN_INTERVAL_SECONDS = 30
MONITOR_ALLOWED_PRIVATE_NETWORKS = []

REST_FRAMEWORK = {**REST_FRAMEWORK}
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    **REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
    "anon": "10000/min",
    "user": "10000/min",
    "login": "10000/min",
    "password_reset": "10000/min",
    "registration": "10000/min",
    "ingestion": "10000/min",
    "manual_check": "10000/min",
}

LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}

# The test suite controls scheduler dispatch explicitly.
MONITOR_IMMEDIATE_CHECKS = False
