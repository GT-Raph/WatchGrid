"""End-to-end monitoring flow: scheduler → queue → worker → check → incident → email.

Covers MON-001 … MON-005 and PERF-001 from Section 27.2. Probes are replaced with
scripted results so the tests are deterministic and never touch the network.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from apps.checks import tasks
from apps.checks.models import MonitorCheck
from apps.checks.probe import ProbeResult
from apps.checks.services import record_check
from apps.incidents.models import Incident
from apps.monitors.models import Monitor
from apps.monitors.state_machine import OUTCOME_FAILURE, OUTCOME_PLATFORM_ERROR, OUTCOME_SUCCESS
from apps.notifications.models import Notification
from apps.notifications.services import create_default_alert_policy
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


def ok(ms=120):
    return ProbeResult(outcome=OUTCOME_SUCCESS, checked_at=timezone.now(), status_code=200, response_time_ms=ms)


def http_500():
    return ProbeResult(
        outcome=OUTCOME_FAILURE,
        checked_at=timezone.now(),
        status_code=500,
        response_time_ms=80,
        error_category="http",
        error_message="Unexpected HTTP status 500 (expected 200-299).",
    )


def timeout():
    return ProbeResult(
        outcome=OUTCOME_FAILURE,
        checked_at=timezone.now(),
        response_time_ms=10000,
        error_category="timeout",
        error_message="Timed out waiting for the response.",
        timeout_phase="read",
    )


@pytest.fixture
def monitor(project):
    create_default_alert_policy(project)
    return MonitorFactory(project=project, name="Main Production Site", failure_threshold=3, recovery_threshold=2)


def run_cycle(monitor, result, run_on_commit):
    """Make the monitor due, run the scheduler tick, execute the queued check."""
    Monitor.objects.filter(pk=monitor.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
    with mock.patch.object(tasks, "execute_probe", return_value=result), run_on_commit():
        dispatched = tasks.dispatch_due_monitors()
    monitor.refresh_from_db()
    return dispatched


def test_mon_001_healthy_endpoint(monitor, run_on_commit):
    assert run_cycle(monitor, ok(), run_on_commit) == 1
    assert monitor.status == Monitor.Status.OPERATIONAL
    check = MonitorCheck.objects.get(monitor=monitor)
    assert check.success and check.status_code == 200 and check.response_time_ms == 120
    assert check.monitor_status_after == Monitor.Status.OPERATIONAL
    assert not Incident.objects.exists()
    assert monitor.next_check_at > timezone.now()


def test_mon_002_three_consecutive_500s_create_one_incident_and_email(monitor, run_on_commit):
    run_cycle(monitor, ok(), run_on_commit)
    for _ in range(3):
        run_cycle(monitor, http_500(), run_on_commit)
    assert monitor.status == Monitor.Status.DOWN
    incidents = Incident.objects.filter(monitor=monitor)
    assert incidents.count() == 1
    incident = incidents.get()
    assert incident.severity == "major"
    assert incident.failure_type == "http"
    assert incident.detected_at < incident.confirmed_at  # duration starts at first failure
    assert [e.kind for e in incident.events.all()] == ["opened"]

    # Continuing failures update the same incident (US-003) and do not re-alert.
    run_cycle(monitor, http_500(), run_on_commit)
    assert Incident.objects.filter(monitor=monitor).count() == 1
    incident.refresh_from_db()
    assert incident.failure_count == 4
    assert len(mail.outbox) == 1
    assert "DOWN" in mail.outbox[0].subject
    assert mail.outbox[0].to == ["owner@example.com"]


def test_mon_003_transient_failure_does_not_open_incident(monitor, run_on_commit):
    run_cycle(monitor, ok(), run_on_commit)
    run_cycle(monitor, timeout(), run_on_commit)
    assert monitor.status == Monitor.Status.PENDING_FAILURE
    run_cycle(monitor, ok(), run_on_commit)
    assert monitor.status == Monitor.Status.OPERATIONAL
    assert monitor.consecutive_failures == 0
    assert not Incident.objects.exists()
    assert not mail.outbox


def test_mon_004_recovery_resolves_incident_and_queues_recovery_alert(monitor, run_on_commit):
    for _ in range(3):
        run_cycle(monitor, timeout(), run_on_commit)
    incident = Incident.objects.get(monitor=monitor)
    assert incident.severity == "critical"  # unreachable

    run_cycle(monitor, ok(), run_on_commit)
    assert monitor.status == Monitor.Status.RECOVERING
    incident.refresh_from_db()
    assert incident.is_active

    run_cycle(monitor, ok(), run_on_commit)
    assert monitor.status == Monitor.Status.OPERATIONAL
    incident.refresh_from_db()
    assert incident.status == Incident.Status.RESOLVED
    assert incident.resolution_type == Incident.ResolutionType.AUTOMATIC
    assert incident.resolved_at is not None
    subjects = [m.subject for m in mail.outbox]
    assert any("DOWN" in s for s in subjects) and any("RECOVERED" in s for s in subjects)
    assert Notification.objects.filter(incident=incident, status=Notification.Status.SENT).count() == 2


def test_mon_005_duplicate_task_delivery_records_once(monitor, run_on_commit):
    scheduled_for = timezone.now().replace(microsecond=0)
    with mock.patch.object(tasks, "execute_probe", return_value=http_500()) as probe, run_on_commit():
        first = tasks.run_monitor_check(str(monitor.id), scheduled_for.isoformat())
        second = tasks.run_monitor_check(str(monitor.id), scheduled_for.isoformat())
    assert (first, second) == ("recorded", "duplicate")
    assert probe.call_count == 1
    assert MonitorCheck.objects.filter(monitor=monitor).count() == 1
    monitor.refresh_from_db()
    assert monitor.consecutive_failures == 1


def test_duplicate_persistence_is_rejected_by_constraint(monitor):
    first = record_check(monitor_id=monitor.id, execution_key="s:1", result=http_500())
    second = record_check(monitor_id=monitor.id, execution_key="s:1", result=http_500())
    assert first is not None and second is None
    monitor.refresh_from_db()
    assert monitor.consecutive_failures == 1


def test_platform_errors_do_not_blame_target(monitor, run_on_commit):
    run_cycle(monitor, ok(), run_on_commit)
    platform = ProbeResult(outcome=OUTCOME_PLATFORM_ERROR, checked_at=timezone.now(), error_category="platform")
    for _ in range(5):
        run_cycle(monitor, platform, run_on_commit)
    assert monitor.status == Monitor.Status.OPERATIONAL
    assert not Incident.objects.exists()


def test_manual_check_does_not_change_state_or_cadence(monitor, run_on_commit):
    run_cycle(monitor, ok(), run_on_commit)
    next_check = monitor.next_check_at
    with mock.patch.object(tasks, "execute_probe", return_value=http_500()):
        tasks.run_manual_check(str(monitor.id), "m:abc")
    monitor.refresh_from_db()
    assert monitor.status == Monitor.Status.OPERATIONAL
    assert monitor.consecutive_failures == 0
    assert monitor.next_check_at == next_check
    assert MonitorCheck.objects.filter(monitor=monitor, kind="manual").count() == 1


def test_manual_resolve_while_still_down_reopens_on_next_failure(monitor, run_on_commit, owner):
    from apps.incidents.services import resolve_manually

    for _ in range(3):
        run_cycle(monitor, http_500(), run_on_commit)
    incident = Incident.objects.get(monitor=monitor)
    resolve_manually(actor=owner, incident=incident)
    run_cycle(monitor, http_500(), run_on_commit)
    assert Incident.objects.filter(monitor=monitor).count() == 2
    assert Incident.objects.filter(monitor=monitor).active().count() == 1


# --------------------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------------------


def test_scheduler_ignores_paused_archived_and_future_monitors(project, run_on_commit):
    due = MonitorFactory(project=project)
    MonitorFactory(project=project, is_active=False, status=Monitor.Status.PAUSED)
    MonitorFactory(project=project, archived_at=timezone.now(), is_active=False)
    MonitorFactory(project=project, next_check_at=timezone.now() + timedelta(minutes=5))
    with mock.patch.object(tasks, "run_monitor_check") as task, run_on_commit():
        dispatched = tasks.dispatch_due_monitors()
    assert dispatched == 1
    assert task.apply_async.call_count == 1
    assert task.apply_async.call_args.kwargs["args"][0] == str(due.id)


def test_scheduler_advances_cadence_so_ticks_do_not_double_dispatch(project, run_on_commit):
    monitor = MonitorFactory(project=project)
    original = monitor.next_check_at
    with mock.patch.object(tasks, "run_monitor_check"), run_on_commit():
        assert tasks.dispatch_due_monitors() == 1
        assert tasks.dispatch_due_monitors() == 0
    monitor.refresh_from_db()
    assert monitor.next_check_at == original + timedelta(seconds=60)
