"""Production settings. Every secret comes from the environment."""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import MONITOR_ALLOWED_PRIVATE_NETWORKS, SECRET_KEY, env, env_bool, env_int

DEBUG = False

if not SECRET_KEY or SECRET_KEY.startswith("insecure"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a strong secret in production.")

if MONITOR_ALLOWED_PRIVATE_NETWORKS and not env_bool("WATCHGRID_ALLOW_PRIVATE_TARGETS_I_UNDERSTAND_SSRF", False):
    raise ImproperlyConfigured("MONITOR_ALLOWED_PRIVATE_NETWORKS must be empty in production (NFR-SEC-003).")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", False)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_REDIRECT_EXEMPT = [r"^health/"]
# Always true in real deployments; SECURE_COOKIES=false exists only for plain-HTTP local trials.
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")

# Content-hashed file names (app.3f9c1a.js) so Nginx's long static cache never
# serves a stale stylesheet or script after a deploy. `collectstatic` writes the
# manifest into the shared static volume that the web container reads.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
