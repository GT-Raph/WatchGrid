"""Local developer workstation settings."""

from .base import *
from .base import env, env_bool, env_list

DEBUG = True
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")

# Without Redis running locally fall back to in-process cache and eager Celery so the
# dashboard can be exercised with `manage.py runserver` alone.
if env_bool("WATCHGRID_LOCAL_NO_REDIS", True):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    CELERY_BROKER_URL = "memory://"
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

LOG_FORMAT = env("LOG_FORMAT", "plain")
LOGGING["handlers"]["console"]["formatter"] = LOG_FORMAT
