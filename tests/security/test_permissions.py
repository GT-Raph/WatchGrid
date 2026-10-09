"""Role permission matrix (Section 10.2)."""

import pytest
from django.utils import timezone

from apps.audit.services import record_audit
from apps.incidents.models import Incident
from apps.notifications.models import AlertPolicy
from common.permissions.roles import CAPABILITIES
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db

MONITOR_PAYLOAD = {"name": "API", "url": "https://api.example.com/health", "interval_seconds": 60}


@pytest.fixture
def clients(client_for, owner, admin_user, developer, viewer):
    return {
        "owner": client_for(owner),
        "admin": client_for(admin_user),
        "developer": client_for(developer),
        "viewer": client_for(viewer),
    }


@pytest.mark.parametrize(("role", "expected"), [("owner", 201), ("admin", 201), ("developer", 201), ("viewer", 403)])
def test_create_monitor(clients, project, role, expected):
    response = clients[role].post("/api/v1/monitors/", {**MONITOR_PAYLOAD, "project": str(project.id)})
    assert response.status_code == expected, response.content


@pytest.mark.parametrize(("role", "expected"), [("owner", 201), ("admin", 201), ("developer", 403), ("viewer", 403)])
def test_create_project(clients, organization, role, expected):
    response = clients[role].post("/api/v1/projects/", {"organization": str(organization.id), "name": "New"})
    assert response.status_code == expected


@pytest.mark.parametrize(("role", "expected"), [("owner", 200), ("admin", 200), ("developer", 200), ("viewer", 403)])
def test_edit_project_details_developer_limited(clients, project, role, expected):
    assert clients[role].patch(f"/api/v1/projects/{project.id}/", {"description": "x"}).status_code == expected


@pytest.mark.parametrize(("role", "expected"), [("owner", 204), ("admin", 204), ("developer", 403), ("viewer", 403)])
def test_archive_project(clients, project, role, expected):
    assert clients[role].delete(f"/api/v1/projects/{project.id}/").status_code == expected


@pytest.mark.parametrize(("role", "expected"), [("owner", 200), ("admin", 200), ("developer", 200), ("viewer", 403)])
def test_pause_monitor(clients, project, role, expected):
    monitor = MonitorFactory(project=project)
    assert clients[role].post(f"/api/v1/monitors/{monitor.id}/pause/").status_code == expected


@pytest.mark.parametrize("role", ["owner", "admin", "developer", "viewer"])
def test_everyone_can_view_monitors_checks_and_incidents(clients, project, role):
    monitor = MonitorFactory(project=project)
    now = timezone.now()
    incident = Incident.objects.create(
        project=project, monitor=monitor, title="x", detected_at=now, confirmed_at=now, last_seen_at=now
    )
    client = clients[role]
    assert client.get(f"/api/v1/monitors/{monitor.id}/").status_code == 200
    assert client.get(f"/api/v1/monitors/{monitor.id}/checks/").status_code == 200
    assert client.get(f"/api/v1/incidents/{incident.id}/").status_code == 200


@pytest.mark.parametrize(("role", "expected"), [("owner", 200), ("admin", 200), ("developer", 200), ("viewer", 403)])
def test_acknowledge_incident(clients, project, role, expected):
    now = timezone.now()
    incident = Incident.objects.create(
        project=project,
        monitor=MonitorFactory(project=project),
        title="x",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
    )
    assert clients[role].post(f"/api/v1/incidents/{incident.id}/acknowledge/").status_code == expected


@pytest.mark.parametrize(("role", "expected"), [("owner", 201), ("admin", 201), ("developer", 201), ("viewer", 403)])
def test_manage_api_keys(clients, project, role, expected):
    assert clients[role].post(f"/api/v1/projects/{project.id}/api-keys/", {"name": "k"}).status_code == expected


@pytest.mark.parametrize(
    ("role", "create", "delete"),
    [("owner", 201, 204), ("admin", 201, 204), ("developer", 201, 403), ("viewer", 403, 403)],
)
def test_alert_policies_developer_cannot_delete(clients, project, role, create, delete):
    payload = {
        "project": str(project.id),
        "name": "P",
        "event_types": ["monitor_down"],
        "channels": [{"type": "email", "to": ["ops@example.com"]}],
    }
    assert clients[role].post("/api/v1/alert-policies/", payload, format="json").status_code == create
    policy = AlertPolicy.objects.create(project=project, name="Existing", event_types=["monitor_down"])
    assert clients[role].delete(f"/api/v1/alert-policies/{policy.id}/").status_code == delete


@pytest.mark.parametrize(("role", "expected"), [("owner", 201), ("admin", 201), ("developer", 403), ("viewer", 403)])
def test_manage_members(clients, organization, role, expected):
    response = clients[role].post(
        f"/api/v1/organizations/{organization.id}/invitations/", {"email": "new@example.com", "role": "viewer"}
    )
    assert response.status_code == expected


@pytest.mark.parametrize(("role", "expected"), [("owner", 200), ("admin", 200), ("developer", 403), ("viewer", 403)])
def test_manage_org_settings(clients, organization, role, expected):
    assert clients[role].patch(f"/api/v1/organizations/{organization.id}/", {"name": "Renamed"}).status_code == expected


def test_audit_log_visibility(clients, organization, project, owner):
    record_audit(actor=owner, organization=organization, action="membership.role_changed", resource_type="membership")
    record_audit(actor=owner, organization=organization, action="monitor.created", resource_type="monitor")
    url = f"/api/v1/organizations/{organization.id}/audit-logs/"
    owner_actions = {row["action"] for row in clients["owner"].get(url).data["results"]}
    developer_actions = {row["action"] for row in clients["developer"].get(url).data["results"]}
    assert "membership.role_changed" in owner_actions
    assert developer_actions == {"monitor.created"}
    assert clients["viewer"].get(url).status_code == 403


def test_capability_matrix_is_complete():
    for capability, roles in CAPABILITIES.items():
        assert roles, capability
        assert "owner" in roles, f"owners must hold every capability ({capability})"
