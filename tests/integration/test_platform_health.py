"""Platform health and watch-the-watcher signals (Section 26)."""

import pytest

from apps.checks import tasks as check_tasks
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_liveness_and_readiness(client):
    assert client.get("/health/live/").json() == {"status": "ok"}
    ready = client.get("/health/ready/")
    assert ready.status_code == 200 and ready.json()["database"]["ok"] is True


def test_request_id_header_round_trip(client):
    response = client.get("/health/live/", HTTP_X_REQUEST_ID="trace-12345678")
    assert response["X-Request-ID"] == "trace-12345678"
    generated = client.get("/health/live/")["X-Request-ID"]
    assert generated.startswith("req_")


def test_platform_status_requires_staff(client_for, owner):
    assert client_for(owner).get("/api/v1/platform/status/").status_code == 403


def test_platform_status_reports_scheduler_and_queue_heartbeats(client_for):
    staff = UserFactory(is_staff=True)
    client = client_for(staff)
    body = client.get("/api/v1/platform/status/").json()
    assert body["status"] == "degraded"  # nothing has ticked yet
    assert body["components"]["scheduler"]["ok"] is False

    check_tasks.dispatch_due_monitors()
    check_tasks.emit_queue_heartbeats()  # eager: every queue answers immediately
    body = client.get("/api/v1/platform/status/").json()
    assert body["components"]["scheduler"]["ok"] is True
    assert all(q["ok"] for q in body["components"]["queues"].values())
    assert body["status"] == "ok"
    assert "overdue_monitors" in body["monitoring"]


def test_api_schema_is_generated(client_for, owner):
    response = client_for(owner).get("/api/v1/schema/")
    assert response.status_code == 200
