"""Tenant isolation (Section 23.1, TEN-001, US-007).

A member of organization A must not be able to list, read, change or delete any
resource of organization B, and the response must not reveal that it exists.
"""

import pytest
from django.utils import timezone

from apps.api_keys.services import create_api_key
from apps.errors.models import ErrorGroup
from apps.incidents.models import Incident
from apps.notifications.models import AlertPolicy, MaintenanceWindow
from tests.factories import MonitorFactory, OrganizationFactory, ProjectFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def foreign(db):
    """Organization B with one of every resource type."""
    other_owner = UserFactory(email="other-owner@example.com")
    org = OrganizationFactory(owner=other_owner, name="Other Co", slug="other-co")
    project = ProjectFactory(organization=org, name="Secret Project")
    monitor = MonitorFactory(project=project, name="Secret Monitor")
    now = timezone.now()
    incident = Incident.objects.create(
        project=project, monitor=monitor, title="Secret outage", detected_at=now, confirmed_at=now, last_seen_at=now
    )
    group = ErrorGroup.objects.create(
        project=project,
        fingerprint="f" * 64,
        error_type="SecretError",
        normalized_message="secret",
        first_seen=now,
        last_seen=now,
    )
    policy = AlertPolicy.objects.create(project=project, name="Secret policy", event_types=["monitor_down"])
    window = MaintenanceWindow.objects.create(
        project=project, title="Secret window", starts_at=now, ends_at=now + timezone.timedelta(hours=1)
    )
    api_key, _ = create_api_key(actor=other_owner, project=project, name="Secret key")
    return {
        "organization": org,
        "project": project,
        "monitor": monitor,
        "incident": incident,
        "error": group,
        "policy": policy,
        "window": window,
        "api_key": api_key,
    }


def assert_not_found_without_leak(response, secret_words=("Secret", "Other Co")):
    assert response.status_code == 404, response.content
    body = response.content.decode()
    for word in secret_words:
        assert word not in body


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/api/v1/organizations/{}/", "organization"),
        ("/api/v1/organizations/{}/members/", "organization"),
        ("/api/v1/projects/{}/", "project"),
        ("/api/v1/projects/{}/errors/", "project"),
        ("/api/v1/projects/{}/api-keys/", "project"),
        ("/api/v1/monitors/{}/", "monitor"),
        ("/api/v1/monitors/{}/checks/", "monitor"),
        ("/api/v1/monitors/{}/statistics/", "monitor"),
        ("/api/v1/incidents/{}/", "incident"),
        ("/api/v1/errors/{}/", "error"),
        ("/api/v1/errors/{}/events/", "error"),
        ("/api/v1/alert-policies/{}/", "policy"),
        ("/api/v1/maintenance-windows/{}/", "window"),
        ("/api/v1/api-keys/{}/", "api_key"),
    ],
)
def test_cross_tenant_reads_return_404(owner_client, foreign, path, key):
    assert_not_found_without_leak(owner_client.get(path.format(foreign[key].id)))


@pytest.mark.parametrize(
    ("method", "path", "key", "payload"),
    [
        ("patch", "/api/v1/organizations/{}/", "organization", {"name": "pwned"}),
        ("delete", "/api/v1/organizations/{}/", "organization", None),
        ("patch", "/api/v1/projects/{}/", "project", {"name": "pwned"}),
        ("delete", "/api/v1/projects/{}/", "project", None),
        ("patch", "/api/v1/monitors/{}/", "monitor", {"name": "pwned"}),
        ("delete", "/api/v1/monitors/{}/", "monitor", None),
        ("post", "/api/v1/monitors/{}/pause/", "monitor", {}),
        ("post", "/api/v1/monitors/{}/test/", "monitor", {}),
        ("post", "/api/v1/incidents/{}/acknowledge/", "incident", {}),
        ("post", "/api/v1/incidents/{}/resolve/", "incident", {}),
        ("post", "/api/v1/errors/{}/resolve/", "error", {}),
        ("patch", "/api/v1/alert-policies/{}/", "policy", {"name": "pwned"}),
        ("delete", "/api/v1/alert-policies/{}/", "policy", None),
        ("delete", "/api/v1/maintenance-windows/{}/", "window", None),
        ("post", "/api/v1/api-keys/{}/revoke/", "api_key", {}),
        ("post", "/api/v1/projects/{}/api-keys/", "project", {"name": "steal"}),
        ("post", "/api/v1/organizations/{}/invitations/", "organization", {"email": "x@example.com"}),
    ],
)
def test_cross_tenant_writes_return_404_and_change_nothing(owner_client, foreign, method, path, key, payload):
    response = getattr(owner_client, method)(path.format(foreign[key].id), payload, format="json")
    assert_not_found_without_leak(response)
    foreign["monitor"].refresh_from_db()
    foreign["project"].refresh_from_db()
    assert foreign["monitor"].name == "Secret Monitor" and foreign["monitor"].is_active
    assert foreign["project"].name == "Secret Project"
    foreign["api_key"].refresh_from_db()
    assert foreign["api_key"].revoked_at is None


def test_lists_never_include_foreign_resources(owner_client, foreign, project):
    MonitorFactory(project=project, name="My Monitor")
    for path in [
        "/api/v1/organizations/",
        "/api/v1/projects/",
        "/api/v1/monitors/",
        "/api/v1/incidents/",
        "/api/v1/errors/?status=all",
        "/api/v1/alert-policies/",
        "/api/v1/maintenance-windows/",
    ]:
        response = owner_client.get(path)
        assert response.status_code == 200, path
        assert "Secret" not in response.content.decode(), path


def test_filtering_by_foreign_ids_yields_nothing(owner_client, foreign):
    org_id, project_id = foreign["organization"].id, foreign["project"].id
    assert owner_client.get(f"/api/v1/monitors/?project={project_id}").data["count"] == 0
    assert owner_client.get(f"/api/v1/monitors/?organization={org_id}").data["count"] == 0
    assert owner_client.get(f"/api/v1/incidents/?project={project_id}").data["count"] == 0
    dashboard = owner_client.get(f"/api/v1/dashboard/?organization={org_id}").data
    assert dashboard["monitors"]["total"] == 0 and dashboard["incidents"]["active"] == 0


def test_cannot_create_resources_inside_foreign_project(owner_client, foreign):
    project_id = str(foreign["project"].id)
    responses = [
        owner_client.post("/api/v1/monitors/", {"project": project_id, "name": "x", "url": "https://example.com"}),
        owner_client.post(
            "/api/v1/alert-policies/",
            {
                "project": project_id,
                "name": "x",
                "event_types": ["monitor_down"],
                "channels": [{"type": "email", "to": ["a@example.com"]}],
            },
            format="json",
        ),
        owner_client.post(
            "/api/v1/maintenance-windows/",
            {
                "project": project_id,
                "title": "x",
                "starts_at": "2030-01-01T00:00:00Z",
                "ends_at": "2030-01-01T01:00:00Z",
            },
        ),
        owner_client.post("/api/v1/projects/", {"organization": str(foreign["organization"].id), "name": "x"}),
    ]
    for response in responses:
        assert_not_found_without_leak(response)


def test_unauthenticated_requests_are_rejected(api_client, foreign):
    response = api_client.get("/api/v1/monitors/")
    assert response.status_code == 403  # session auth without WWW-Authenticate challenge
    assert response.data["error"]["code"] in {"AUTHENTICATION_REQUIRED", "PERMISSION_DENIED"}


def test_invalid_uuid_is_404_not_500(owner_client):
    assert owner_client.get("/api/v1/monitors/not-a-uuid/").status_code == 404
