"""Uptime, latency, dashboard, rollups and retention (FR-ANA-*, US-006, Section 24.3)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.analytics import services, tasks
from apps.analytics.models import MonitorHourlyStat
from apps.audit.models import AuditLog
from apps.checks.models import MonitorCheck
from apps.incidents.models import Incident
from common.utilities.time import TimeRange
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


def make_checks(monitor, outcomes, start, step=timedelta(minutes=1), latency=100):
    MonitorCheck.objects.bulk_create(
        [
            MonitorCheck(
                monitor=monitor,
                execution_key=f"s:{start.timestamp()}:{i}",
                checked_at=start + step * i,
                outcome=outcome,
                success=outcome in ("success", "degraded"),
                response_time_ms=latency + i if outcome != "failure" else None,
            )
            for i, outcome in enumerate(outcomes)
        ]
    )


@pytest.fixture
def monitor(project):
    return MonitorFactory(project=project)


def test_check_based_uptime_and_latency(monitor):
    now = timezone.now()
    make_checks(monitor, ["success"] * 8 + ["failure"] * 2, now - timedelta(minutes=30))
    MonitorCheck.objects.create(
        monitor=monitor,
        execution_key="m:1",
        kind="manual",
        checked_at=now - timedelta(minutes=1),
        outcome="failure",
        success=False,
    )
    MonitorCheck.objects.create(
        monitor=monitor,
        execution_key="s:p",
        checked_at=now - timedelta(minutes=2),
        outcome="platform_error",
        success=False,
    )
    stats = services.monitor_statistics(monitor, TimeRange(now - timedelta(hours=1), now, "1h"))
    assert stats["uptime_percent"] == 80.0  # manual and platform checks excluded
    assert stats["checks"] == {"total": 10, "available": 8, "degraded": 0, "failed": 2}
    assert stats["latency_ms"]["min"] == 100 and stats["latency_ms"]["max"] == 107
    assert stats["latency_ms"]["p50"] in (103, 104)
    assert stats["source"] == "raw"


def test_downtime_is_clipped_to_range(monitor):
    now = timezone.now()
    Incident.objects.create(
        project=monitor.project,
        monitor=monitor,
        title="x",
        detected_at=now - timedelta(hours=3),
        confirmed_at=now - timedelta(hours=3),
        last_seen_at=now,
        resolved_at=now - timedelta(minutes=30),
        status="resolved",
    )
    assert services.downtime_seconds([monitor.id], now - timedelta(hours=1), now) == 30 * 60


def test_statistics_endpoint_includes_series(owner_client, monitor):
    now = timezone.now()
    make_checks(monitor, ["success", "degraded", "failure"], now - timedelta(minutes=10))
    body = owner_client.get(f"/api/v1/monitors/{monitor.id}/statistics/?range=24h").json()
    assert body["series"]["granularity"] == "check"
    assert [p["outcome"] for p in body["series"]["points"]] == ["success", "degraded", "failure"]
    weekly = owner_client.get(f"/api/v1/monitors/{monitor.id}/statistics/?range=7d").json()
    assert weekly["series"]["granularity"] == "hour"
    assert owner_client.get(f"/api/v1/monitors/{monitor.id}/statistics/?range=bogus").status_code == 400


def test_rollups_back_long_ranges(owner_client, monitor):
    start = (timezone.now() - timedelta(days=20)).replace(minute=0, second=0, microsecond=0)
    make_checks(monitor, ["success"] * 50 + ["failure"] * 10, start)
    written = services.rollup_hours(start, start + timedelta(hours=2))
    assert written == 1
    stat = MonitorHourlyStat.objects.get(monitor=monitor)
    assert (stat.total_checks, stat.up_checks, stat.failed_checks) == (60, 50, 10)
    assert stat.response_time_p95 is not None
    assert services.rollup_hours(start, start + timedelta(hours=2)) == 1  # idempotent
    assert MonitorHourlyStat.objects.count() == 1

    body = owner_client.get(f"/api/v1/monitors/{monitor.id}/statistics/?range=30d").json()
    assert body["source"] == "hourly_rollup"
    assert round(body["uptime_percent"], 2) == 83.33


def test_dashboard_summary(owner_client, project):
    up = MonitorFactory(project=project, status="operational")
    MonitorFactory(project=project, status="down")
    MonitorFactory(project=project, status="degraded")
    now = timezone.now()
    make_checks(up, ["success"] * 9 + ["failure"], now - timedelta(minutes=20))
    Incident.objects.create(
        project=project,
        monitor=up,
        title="Active",
        severity="critical",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
    )
    body = owner_client.get(f"/api/v1/dashboard/?project={project.id}").json()
    assert body["monitors"]["total"] == 3
    assert (body["monitors"]["operational"], body["monitors"]["down"], body["monitors"]["degraded"]) == (1, 1, 1)
    assert body["incidents"]["active"] == 1
    assert body["incidents"]["active_by_severity"]["critical"] == 1
    assert body["uptime_percent"] == 90.0
    assert body["recent_incidents"][0]["title"] == "Active"


def test_retention_removes_only_expired_rows(monitor, settings):
    now = timezone.now()
    make_checks(monitor, ["success"] * 3, now - timedelta(days=settings.RETENTION_CHECK_DAYS + 2))
    make_checks(monitor, ["success"] * 2, now - timedelta(days=1))
    old_audit = AuditLog.objects.create(action="old")
    AuditLog.objects.filter(pk=old_audit.pk).update(
        created_at=now - timedelta(days=settings.RETENTION_AUDIT_LOG_DAYS + 1)
    )
    AuditLog.objects.create(action="recent")
    result = tasks.enforce_retention()
    assert result["checks"] == 3
    assert MonitorCheck.objects.count() == 2
    assert list(AuditLog.objects.values_list("action", flat=True)) == ["recent"]
    # Expired raw data was summarised before deletion.
    assert MonitorHourlyStat.objects.filter(monitor=monitor).exists()


def test_custom_time_range_validation(owner_client, monitor):
    url = f"/api/v1/monitors/{monitor.id}/statistics/"
    assert (
        owner_client.get(f"{url}?range=custom&start=2026-01-02T00:00:00Z&end=2026-01-01T00:00:00Z").status_code == 400
    )
    assert (
        owner_client.get(f"{url}?range=custom&start=2020-01-01T00:00:00Z&end=2026-01-01T00:00:00Z").status_code == 400
    )
    ok = owner_client.get(f"{url}?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z")
    assert ok.status_code == 200 and ok.json()["range"]["label"] == "custom"


def test_status_grid_aligns_cells_by_time(owner_client, project):
    calm = MonitorFactory(project=project, name="Calm", status="operational")
    broken = MonitorFactory(project=project, name="Broken", status="down")
    now = timezone.now()
    make_checks(calm, ["success"] * 60, now - timedelta(minutes=59, seconds=30))
    make_checks(broken, ["success"] * 50 + ["failure"] * 10, now - timedelta(minutes=59, seconds=30))
    body = owner_client.get(f"/api/v1/dashboard/grid/?project={project.id}&range=1h&buckets=12").json()
    assert body["buckets"] == 12 and body["bucket_seconds"] == 300
    assert [m["name"] for m in body["monitors"]] == ["Broken", "Calm"]  # problems first
    broken_row, calm_row = body["monitors"]
    assert sum(c[0] for c in calm_row["cells"] if c) == 60
    assert sum(c[1] for c in broken_row["cells"] if c) == 10
    failing = [i for i, c in enumerate(broken_row["cells"]) if c and c[1]]
    assert failing and min(failing) >= 9  # failures sit at the end of the window
    assert broken_row["uptime_percent"] == round(50 * 100 / 60, 3)


def test_status_grid_long_range_uses_rollups(owner_client, project):
    monitor = MonitorFactory(project=project)
    start = (timezone.now() - timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    make_checks(monitor, ["success"] * 30 + ["failure"] * 30, start)
    services.rollup_hours(start, start + timedelta(hours=2))
    body = owner_client.get("/api/v1/dashboard/grid/?range=30d").json()
    cells = [c for c in body["monitors"][0]["cells"] if c]
    assert sum(c[0] for c in cells) == 60 and sum(c[1] for c in cells) == 30
