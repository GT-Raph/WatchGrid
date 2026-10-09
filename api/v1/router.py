"""REST resources exposed under /api/v1/ (Section 20.2)."""

from rest_framework.routers import DefaultRouter

from apps.api_keys.api import APIKeyViewSet
from apps.errors.api import ErrorGroupViewSet
from apps.incidents.api import IncidentViewSet
from apps.monitors.api import MonitorViewSet
from apps.notifications.api import AlertPolicyViewSet, MaintenanceWindowViewSet
from apps.organizations.api import OrganizationViewSet
from apps.projects.api import ProjectViewSet

router = DefaultRouter(trailing_slash=True)
router.include_root_view = False
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("projects", ProjectViewSet, basename="project")
router.register("monitors", MonitorViewSet, basename="monitor")
router.register("incidents", IncidentViewSet, basename="incident")
router.register("errors", ErrorGroupViewSet, basename="error-group")
router.register("alert-policies", AlertPolicyViewSet, basename="alert-policy")
router.register("maintenance-windows", MaintenanceWindowViewSet, basename="maintenance-window")
router.register("api-keys", APIKeyViewSet, basename="api-key")
