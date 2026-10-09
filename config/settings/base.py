"""Base settings shared by every environment.

Environment-specific modules (development, test, staging, production) import
from here and override only what differs. Secrets and infrastructure endpoints
are always read from the environment (see Appendix B of the specification).
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from celery.schedules import schedule
from kombu import Queue

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-development-key-change-me")
DEBUG = False
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# --------------------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.organizations",
    "apps.projects",
    "apps.monitors",
    "apps.checks",
    "apps.incidents",
    "apps.errors",
    "apps.notifications",
    "apps.analytics",
    "apps.api_keys",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "common.utilities.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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
            # Dashboard filters and tags (label, percent, duration, icon) need no {% load %}.
            "builtins": ["common.utilities.templatetags"],
        },
    },
]

# --------------------------------------------------------------------------------------
# Data stores
# --------------------------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=env_int("DATABASE_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_URL", REDIS_URL),
        "KEY_PREFIX": "watchgrid",
    }
}

# --------------------------------------------------------------------------------------
# Authentication & sessions (ADR-0002: session authentication, server-rendered dashboard)
# --------------------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_RESET_TIMEOUT = 60 * 60  # one hour, single-purpose tokens
EMAIL_VERIFICATION_TIMEOUT = 60 * 60 * 72
INVITATION_TTL_DAYS = 7

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_NAME = "watchgrid_session"
CSRF_COOKIE_NAME = "watchgrid_csrftoken"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --------------------------------------------------------------------------------------
# Internationalisation (BR-010: store UTC, convert at presentation time)
# --------------------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
LOGIN_URL = "web:login"

# --------------------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "common.utilities.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "common.exceptions.handlers.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", "60/min"),
        "user": env("THROTTLE_USER", "600/min"),
        "login": env("THROTTLE_LOGIN", "10/min"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", "5/hour"),
        "registration": env("THROTTLE_REGISTRATION", "10/hour"),
        "ingestion": env("THROTTLE_INGESTION", "600/min"),
        "manual_check": env("THROTTLE_MANUAL_CHECK", "20/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    # Reverse proxies in front of Django (Nginx = 1, Nginx behind a load balancer = 2).
    "NUM_PROXIES": env_int("TRUSTED_PROXY_COUNT", 0),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "WatchGrid API",
    "DESCRIPTION": "Web and Application Monitoring Platform — REST API v1.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
}

# --------------------------------------------------------------------------------------
# Celery (ADR-0003: queues separated by workload)
# --------------------------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 60 * 10}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = (
    Queue("default"),
    Queue("monitoring"),
    Queue("notifications"),
    Queue("ingestion"),
    Queue("analytics"),
)
CELERY_TASK_ROUTES = {
    "apps.checks.tasks.*": {"queue": "monitoring"},
    "apps.notifications.tasks.*": {"queue": "notifications"},
    "apps.errors.tasks.*": {"queue": "ingestion"},
    "apps.analytics.tasks.*": {"queue": "analytics"},
}

SCHEDULER_TICK_SECONDS = env_int("SCHEDULER_TICK_SECONDS", 15)
CELERY_BEAT_SCHEDULE = {
    "dispatch-due-monitors": {
        "task": "apps.checks.tasks.dispatch_due_monitors",
        "schedule": schedule(timedelta(seconds=SCHEDULER_TICK_SECONDS)),
        "options": {"expires": SCHEDULER_TICK_SECONDS * 2},
    },
    "incident-reminders": {
        "task": "apps.notifications.tasks.send_incident_reminders",
        "schedule": schedule(timedelta(seconds=60)),
        "options": {"expires": 120},
    },
    "rollup-monitor-stats": {
        "task": "apps.analytics.tasks.rollup_monitor_stats",
        "schedule": schedule(timedelta(minutes=5)),
        "options": {"expires": 60 * 10},
    },
    "enforce-retention": {
        "task": "apps.analytics.tasks.enforce_retention",
        "schedule": schedule(timedelta(hours=6)),
        "options": {"expires": 60 * 60},
    },
    "queue-heartbeats": {
        "task": "apps.checks.tasks.emit_queue_heartbeats",
        "schedule": schedule(timedelta(seconds=30)),
        "options": {"expires": 60},
    },
}

# --------------------------------------------------------------------------------------
# Email & notifications
# --------------------------------------------------------------------------------------

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "WatchGrid <alerts@watchgrid.local>")

# Public base URL used in emailed links (verification, resets, invitations, alerts).
SITE_URL = (env("SITE_URL") or env("FRONTEND_URL") or "http://localhost:8000").rstrip("/")
NOTIFICATION_MAX_RETRIES = env_int("NOTIFICATION_MAX_RETRIES", 5)
WEBHOOK_TIMEOUT_SECONDS = env_int("WEBHOOK_TIMEOUT_SECONDS", 10)

# --------------------------------------------------------------------------------------
# Monitoring engine policy (Section 14 / Appendix B)
# --------------------------------------------------------------------------------------

MONITOR_MIN_INTERVAL_SECONDS = env_int("MONITOR_MIN_INTERVAL_SECONDS", 60)
MONITOR_MAX_INTERVAL_SECONDS = env_int("MONITOR_MAX_INTERVAL_SECONDS", 60 * 60 * 24)
MONITOR_MAX_TIMEOUT_SECONDS = env_int("MONITOR_MAX_TIMEOUT_SECONDS", 30)
MONITOR_MAX_RESPONSE_BYTES = env_int("MONITOR_MAX_RESPONSE_BYTES", 1024 * 1024)
MONITOR_MAX_REQUEST_BODY_BYTES = env_int("MONITOR_MAX_REQUEST_BODY_BYTES", 64 * 1024)
MONITOR_MAX_REDIRECTS = env_int("MONITOR_MAX_REDIRECTS", 5)
MONITOR_USER_AGENT = env("MONITOR_USER_AGENT", "WatchGrid-Monitor/1.0 (+https://watchgrid.io/bot)")
MONITOR_DISPATCH_BATCH_SIZE = env_int("MONITOR_DISPATCH_BATCH_SIZE", 500)
MONITOR_DISPATCH_MAX_BATCHES = env_int("MONITOR_DISPATCH_MAX_BATCHES", 20)
# Explicit CIDR allowlist that bypasses the SSRF private-range block. MUST stay empty in
# production; production settings refuse to start otherwise. Useful for local test targets.
MONITOR_ALLOWED_PRIVATE_NETWORKS = env_list("MONITOR_ALLOWED_PRIVATE_NETWORKS")
# Additional CIDRs to block on top of the built-in non-global ranges.
MONITOR_EXTRA_BLOCKED_NETWORKS = env_list("MONITOR_EXTRA_BLOCKED_NETWORKS")

# --------------------------------------------------------------------------------------
# Error ingestion (Section 16)
# --------------------------------------------------------------------------------------

ERROR_EVENT_MAX_BYTES = env_int("ERROR_EVENT_MAX_BYTES", 256 * 1024)
ERROR_EVENT_MAX_STACK_TRACE_CHARS = env_int("ERROR_EVENT_MAX_STACK_TRACE_CHARS", 32 * 1024)
ERROR_EVENT_SAMPLE_THRESHOLD_PER_MINUTE = env_int("ERROR_EVENT_SAMPLE_THRESHOLD_PER_MINUTE", 120)
DATA_UPLOAD_MAX_MEMORY_SIZE = max(ERROR_EVENT_MAX_BYTES, 2_621_440)

# --------------------------------------------------------------------------------------
# Retention (Section 24.3)
# --------------------------------------------------------------------------------------

RETENTION_CHECK_DAYS = env_int("RETENTION_CHECK_DAYS", 90)
RETENTION_ERROR_EVENT_DAYS = env_int("RETENTION_ERROR_EVENT_DAYS", 30)
RETENTION_NOTIFICATION_DAYS = env_int("RETENTION_NOTIFICATION_DAYS", 180)
RETENTION_AUDIT_LOG_DAYS = env_int("RETENTION_AUDIT_LOG_DAYS", 400)
RETENTION_DELETE_BATCH_SIZE = env_int("RETENTION_DELETE_BATCH_SIZE", 5000)

# --------------------------------------------------------------------------------------
# Field encryption for sensitive monitor headers (NFR-SEC-004)
# --------------------------------------------------------------------------------------

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", "")

# --------------------------------------------------------------------------------------
# Platform observability
# --------------------------------------------------------------------------------------

OPS_HEARTBEAT_QUEUES = ["monitoring", "notifications", "ingestion", "analytics"]
OPS_SCHEDULER_MAX_LAG_SECONDS = env_int("OPS_SCHEDULER_MAX_LAG_SECONDS", 60)
OPS_QUEUE_MAX_LAG_SECONDS = env_int("OPS_QUEUE_MAX_LAG_SECONDS", 120)

LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOG_FORMAT = env("LOG_FORMAT", "json")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {"()": "common.utilities.logging.RequestContextFilter"},
        "redact": {"()": "common.utilities.logging.RedactingFilter"},
    },
    "formatters": {
        "json": {"()": "common.utilities.logging.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["request_context", "redact"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "watchgrid.security": {"level": "INFO"},
    },
}
