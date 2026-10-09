"""Root URL configuration.

* ``/api/v1/`` — REST API (api/v1/urls.py)
* ``/health/`` — liveness and readiness probes
* ``/admin/`` — Django admin for platform administrators
* everything else — the server-rendered dashboard, one ``urls.py`` per app, all in the
  ``web`` namespace so templates link with ``{% url 'web:monitor-detail' pk %}``.
"""

from django.contrib import admin
from django.urls import include, path

from apps.accounts import urls as accounts_urls
from apps.analytics import urls as analytics_urls
from apps.api_keys import urls as api_keys_urls
from apps.audit import urls as audit_urls
from apps.checks import health
from apps.errors import urls as errors_urls
from apps.incidents import urls as incidents_urls
from apps.monitors import urls as monitors_urls
from apps.notifications import urls as notifications_urls
from apps.organizations import urls as organizations_urls
from apps.projects import urls as projects_urls

# Order matters only where paths overlap: project keys come before notification kinds.
web_urlpatterns = [
    *analytics_urls.urlpatterns,
    *accounts_urls.urlpatterns,
    *organizations_urls.urlpatterns,
    *projects_urls.urlpatterns,
    *monitors_urls.urlpatterns,
    *incidents_urls.urlpatterns,
    *errors_urls.urlpatterns,
    *api_keys_urls.urlpatterns,
    *notifications_urls.urlpatterns,
    *audit_urls.urlpatterns,
]

urlpatterns = [
    path("api/v1/", include("api.v1.urls")),
    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
    path("admin/", admin.site.urls),
    path("", include((web_urlpatterns, "web"))),
]

admin.site.site_header = "WatchGrid administration"
admin.site.site_title = "WatchGrid admin"
