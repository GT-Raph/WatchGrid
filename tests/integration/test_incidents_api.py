"""Incident lifecycle API (FR-INC-004, Section 15)."""

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.incidents.models import Incident
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def incident(project):
    monitor = MonitorFactory(project=project, status="down")
    now = timezone.now()
    return Incident.objects.create(
        project=project,
        monitor=monitor,
        title="Main site is down",
        severity="critical",
        detected_at=now - timezone.timedelta(minutes=3),
        confirmed_at=now,
        last_seen_at=now,
    )


def test_lifecycle_acknowledge_investigate_monitor_resolve(owner_client, incident, run_on_commit):
    url = f"/api/v1/incidents/{incident.id}/"
    response = owner_client.post(f"{url}acknowledge/", {"note": "On it"})
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    assert response.json()["acknowledged_by_email"] == "owner@example.com"

    assert owner_client.patch(url, {"status": "investigating"}).json()["status"] == "investigating"
    assert owner_client.patch(url, {"status": "monitoring", "note": "Fix deployed"}).json()["status"] == "monitoring"
    assert owner_client.post(f"{url}notes/", {"message": "Root cause: pool exhaustion"}).status_code == 201

    with run_on_commit():
        response = owner_client.post(f"{url}resolve/", {"note": "Confirmed fixed"})
    body = response.json()
    assert body["status"] == "resolved"
    assert body["resolution_type"] == "manual"
    assert body["resolved_by_email"] == "owner@example.com"
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["acknowledged", "status_changed", "status_changed", "note", "resolved"]
    assert body["duration_seconds"] >= 180


def test_resolved_incident_rejects_further_actions(owner_client, incident):
    owner_client.post(f"/api/v1/incidents/{incident.id}/resolve/")
    response = owner_client.post(f"/api/v1/incidents/{incident.id}/acknowledge/")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_list_filters(owner_client, incident, project):
    now = timezone.now()
    Incident.objects.create(
        project=project,
        title="old",
        status="resolved",
        severity="minor",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
        resolved_at=now,
    )
    assert owner_client.get("/api/v1/incidents/?status=active").json()["count"] == 1
    assert owner_client.get("/api/v1/incidents/?severity=minor").json()["count"] == 1
    assert owner_client.get(f"/api/v1/incidents/?monitor={incident.monitor_id}").json()["count"] == 1


def test_database_prevents_two_active_incidents_for_same_monitor_key(incident):
    now = timezone.now()
    with pytest.raises(IntegrityError):
        Incident.objects.create(
            project=incident.project,
            monitor=incident.monitor,
            incident_key="availability",
            title="dup",
            detected_at=now,
            confirmed_at=now,
            last_seen_at=now,
        )


def test_resolved_incident_allows_new_active_one(incident):
    incident.status = "resolved"
    incident.save()
    now = timezone.now()
    Incident.objects.create(
        project=incident.project,
        monitor=incident.monitor,
        incident_key="availability",
        title="new",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
    )
