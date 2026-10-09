"""Monitor configuration API (FR-MON-001 … 005, US-001, US-008, SEC-001)."""

from unittest import mock

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.checks.models import MonitorCheck
from apps.monitors.models import Monitor
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


def payload(project, **overrides):
    return {
        "project": str(project.id),
        "name": "Main Production Site",
        "url": "https://example.com/health",
        "interval_seconds": 60,
        "timeout_seconds": 10,
        **overrides,
    }


def test_us_001_new_monitor_is_immediately_scheduled(owner_client, project):
    before = timezone.now()
    response = owner_client.post("/api/v1/monitors/", payload(project), format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "unknown"
    assert body["is_active"] is True
    monitor = Monitor.objects.get(pk=body["id"])
    assert before - timezone.timedelta(seconds=1) <= monitor.next_check_at <= timezone.now()
    assert AuditLog.objects.filter(action="monitor.created", resource_id=str(monitor.id)).exists()


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"interval_seconds": 5}, "interval_seconds"),
        ({"timeout_seconds": 120}, "timeout_seconds"),
        ({"timeout_seconds": 60, "interval_seconds": 60}, "timeout_seconds"),
        ({"url": "ftp://example.com"}, "url"),
        ({"url": "https://admin:pw@example.com/"}, "url"),
        ({"expected_status": "abc"}, "expected_status"),
        ({"expected_status": "299-200"}, "expected_status"),
        ({"body": "{}", "http_method": "GET"}, "body"),
        ({"assertion_mode": "contains"}, "assertion_text"),
        ({"failure_threshold": 0}, "failure_threshold"),
        ({"headers": {"Host": "evil.com"}}, "headers"),
    ],
)
def test_validation_errors(owner_client, project, overrides, field):
    response = owner_client.post("/api/v1/monitors/", payload(project, **overrides), format="json")
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in body["error"]["details"]
    assert body["request_id"].startswith("req_")


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8080/", "http://169.254.169.254/latest/meta-data/", "https://internal.example.com/"],
)
def test_sec_001_private_targets_rejected_and_logged(owner_client, project, url):
    response = owner_client.post("/api/v1/monitors/", payload(project, url=url), format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TARGET_NOT_ALLOWED"
    assert not Monitor.objects.exists()
    event = AuditLog.objects.get(action="security.ssrf_blocked")
    assert event.organization_id == project.organization_id
    assert event.metadata["stage"] == "configuration"


def test_sensitive_headers_are_encrypted_and_redacted(owner_client, project):
    headers = {"Authorization": "Bearer super-secret-token", "X-Trace": "on"}
    response = owner_client.post("/api/v1/monitors/", payload(project, headers=headers), format="json")
    assert response.status_code == 201
    assert response.json()["headers"] == {"Authorization": "[REDACTED]", "X-Trace": "on"}
    monitor = Monitor.objects.get(pk=response.json()["id"])
    assert "super-secret-token" not in monitor.headers_encrypted
    assert monitor.headers["Authorization"] == "Bearer super-secret-token"

    # Sending the redacted placeholder back keeps the stored secret.
    response = owner_client.patch(
        f"/api/v1/monitors/{monitor.id}/",
        {"headers": {"Authorization": "[REDACTED]", "X-Trace": "off"}},
        format="json",
    )
    assert response.status_code == 200
    monitor.refresh_from_db()
    assert monitor.headers == {"Authorization": "Bearer super-secret-token", "X-Trace": "off"}
    audit = AuditLog.objects.filter(action="monitor.updated").latest("created_at")
    assert "super-secret-token" not in str(audit.metadata)


def test_changing_target_resets_runtime_state(owner_client, project):
    monitor = MonitorFactory(project=project, status="down", consecutive_failures=5)
    response = owner_client.patch(f"/api/v1/monitors/{monitor.id}/", {"url": "https://api.example.com/v2"})
    assert response.status_code == 200
    monitor.refresh_from_db()
    assert monitor.status == "unknown" and monitor.consecutive_failures == 0


def test_us_008_pause_and_resume(owner_client, project):
    monitor = MonitorFactory(project=project, status="operational")
    response = owner_client.post(f"/api/v1/monitors/{monitor.id}/pause/")
    assert response.status_code == 200
    assert response.json()["status"] == "paused" and response.json()["is_active"] is False
    assert not Monitor.objects.due(timezone.now() + timezone.timedelta(days=1)).exists()

    response = owner_client.post(f"/api/v1/monitors/{monitor.id}/resume/")
    assert response.json()["status"] == "unknown" and response.json()["is_active"] is True
    monitor.refresh_from_db()
    assert monitor.next_check_at <= timezone.now()


def test_delete_archives_and_keeps_history(owner_client, project):
    monitor = MonitorFactory(project=project)
    MonitorCheck.objects.create(
        monitor=monitor, execution_key="s:1", checked_at=timezone.now(), outcome="success", success=True
    )
    assert owner_client.delete(f"/api/v1/monitors/{monitor.id}/").status_code == 204
    monitor.refresh_from_db()
    assert monitor.archived_at is not None and not monitor.is_active
    assert MonitorCheck.objects.filter(monitor=monitor).count() == 1
    listed = owner_client.get("/api/v1/monitors/").json()
    assert listed["count"] == 0
    # The name becomes reusable once the old monitor is archived.
    response = owner_client.post("/api/v1/monitors/", payload(project, name=monitor.name), format="json")
    assert response.status_code == 201


def test_duplicate_active_name_conflicts(owner_client, project):
    MonitorFactory(project=project, name="Main Production Site")
    response = owner_client.post("/api/v1/monitors/", payload(project), format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NAME_TAKEN"


def test_manual_test_is_queued(owner_client, project):
    monitor = MonitorFactory(project=project)
    with mock.patch("apps.checks.tasks.run_manual_check.apply_async") as apply_async:
        response = owner_client.post(f"/api/v1/monitors/{monitor.id}/test/")
    assert response.status_code == 202
    assert response.json()["execution_key"].startswith("m:")
    apply_async.assert_called_once()


def test_list_includes_uptime_and_recent_checks(owner_client, project):
    monitor = MonitorFactory(project=project)
    now = timezone.now()
    for i, outcome in enumerate(["success", "success", "failure", "success"]):
        MonitorCheck.objects.create(
            monitor=monitor,
            execution_key=f"s:{i}",
            checked_at=now - timezone.timedelta(minutes=10 - i),
            outcome=outcome,
            success=outcome == "success",
            response_time_ms=100 + i,
        )
    body = owner_client.get(f"/api/v1/monitors/?project={project.id}").json()
    row = body["results"][0]
    assert row["uptime_24h"] == 75.0
    assert [c["outcome"] for c in row["recent_checks"]] == ["success", "success", "failure", "success"]


def test_list_filters(owner_client, project):
    MonitorFactory(project=project, name="alpha", status="down")
    MonitorFactory(project=project, name="beta", status="operational")
    assert owner_client.get("/api/v1/monitors/?status=down").json()["count"] == 1
    assert owner_client.get("/api/v1/monitors/?search=bet").json()["results"][0]["name"] == "beta"


def test_checks_history_is_paginated_newest_first(owner_client, project):
    monitor = MonitorFactory(project=project)
    now = timezone.now()
    MonitorCheck.objects.bulk_create(
        [
            MonitorCheck(
                monitor=monitor,
                execution_key=f"s:{i}",
                checked_at=now - timezone.timedelta(minutes=i),
                outcome="success",
                success=True,
                response_time_ms=i,
            )
            for i in range(30)
        ]
    )
    body = owner_client.get(f"/api/v1/monitors/{monitor.id}/checks/").json()
    assert body["count"] == 30 and len(body["results"]) == 25 and body["next"]
    assert body["results"][0]["response_time_ms"] == 0


def test_archived_project_rejects_new_monitors(owner_client, project):
    project.status = "archived"
    project.save()
    response = owner_client.post("/api/v1/monitors/", payload(project), format="json")
    assert response.status_code == 409
