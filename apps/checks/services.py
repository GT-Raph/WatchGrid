"""Check pipeline: persist the result, advance the state machine, drive incidents.

Steps 8-11 of the check execution algorithm (Section 14.5) run in one transaction
with the monitor row locked:

* the ``(monitor, execution_key)`` unique constraint makes duplicate Celery deliveries
  a no-op (MON-005);
* counters, status and incident changes commit together or not at all;
* notifications are enqueued only after commit (``transaction.on_commit``).
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.db import IntegrityError, transaction

from apps.audit.services import record_security_event
from apps.incidents import services as incident_services
from apps.monitors.models import Monitor
from apps.monitors.state_machine import AVAILABILITY, PERFORMANCE, MonitorState, Thresholds, evaluate

from .models import MonitorCheck
from .probe import BLOCKED, REDIRECT, ProbeResult

logger = logging.getLogger("watchgrid.monitoring")


def _state_of(monitor: Monitor) -> MonitorState:
    return MonitorState(
        status=monitor.status,
        consecutive_failures=monitor.consecutive_failures,
        consecutive_successes=monitor.consecutive_successes,
        consecutive_soft_failures=monitor.consecutive_soft_failures,
        failing_since=monitor.failing_since,
        degraded_since=monitor.degraded_since,
    )


def _apply_state(monitor: Monitor, state: MonitorState, changed: bool, at: datetime) -> None:
    monitor.status = state.status
    monitor.consecutive_failures = state.consecutive_failures
    monitor.consecutive_successes = state.consecutive_successes
    monitor.consecutive_soft_failures = state.consecutive_soft_failures
    monitor.failing_since = state.failing_since
    monitor.degraded_since = state.degraded_since
    if changed:
        monitor.last_status_change_at = at


def record_check(
    *,
    monitor_id,
    execution_key: str,
    result: ProbeResult,
    kind: str = MonitorCheck.Kind.SCHEDULED,
    scheduled_for: datetime | None = None,
) -> MonitorCheck | None:
    """Persist a probe result and apply its consequences. Returns ``None`` for duplicates."""
    with transaction.atomic():
        monitor = (
            Monitor.objects.select_for_update(of=("self",)).select_related("project").filter(pk=monitor_id).first()
        )
        if monitor is None:
            return None
        try:
            with transaction.atomic():
                check = MonitorCheck.objects.create(
                    monitor=monitor,
                    execution_key=execution_key,
                    kind=kind,
                    scheduled_for=scheduled_for,
                    checked_at=result.checked_at,
                    outcome=result.outcome,
                    success=result.success,
                    status_code=result.status_code,
                    response_time_ms=result.response_time_ms,
                    error_category=result.error_category,
                    error_message=result.error_message[:500],
                    timeout_phase=result.timeout_phase,
                    resolved_ip=result.resolved_ip,
                    redirect_count=result.redirect_count,
                    response_size_bytes=result.response_size_bytes,
                    tls_expires_at=result.tls_expires_at,
                    tls_issuer=result.tls_issuer[:255],
                )
        except IntegrityError:
            logger.info("duplicate_check_execution", extra={"monitor_id": str(monitor_id), "key": execution_key})
            return None

        if result.error_category in (BLOCKED, REDIRECT) and result.blocked_address:
            record_security_event(
                action="security.ssrf_blocked",
                organization=monitor.project.organization,
                metadata={"stage": "execution", "monitor_id": str(monitor.id), "address": result.blocked_address},
            )

        if kind == MonitorCheck.Kind.MANUAL:
            return check

        # Telemetry about the latest observation is kept even when state is frozen.
        monitor.last_checked_at = result.checked_at
        monitor.last_response_time_ms = result.response_time_ms
        monitor.last_status_code = result.status_code
        monitor.last_error_category = result.error_category
        if result.tls_expires_at:
            monitor.tls_expires_at = result.tls_expires_at

        if not monitor.is_active or monitor.archived_at is not None:
            monitor.save(
                update_fields=[
                    "last_checked_at",
                    "last_response_time_ms",
                    "last_status_code",
                    "last_error_category",
                    "tls_expires_at",
                    "updated_at",
                ]
            )
            return check

        transition = evaluate(
            _state_of(monitor),
            result.outcome,
            Thresholds(monitor.failure_threshold, monitor.recovery_threshold),
            result.checked_at,
        )
        _apply_state(monitor, transition.state, transition.status_changed, result.checked_at)
        monitor.save()

        check.monitor_status_after = monitor.status
        check.save(update_fields=["monitor_status_after"])

        for key in transition.resolve_incidents:
            incident_services.resolve_automatically(
                monitor=monitor,
                incident_key=key,
                check=check,
                superseded=(key == PERFORMANCE and transition.open_incident == AVAILABILITY),
            )
        if transition.open_incident:
            started = (
                transition.state.failing_since
                if transition.open_incident == AVAILABILITY
                else transition.state.degraded_since
            )
            incident_services.open_or_update(
                monitor=monitor, incident_key=transition.open_incident, check=check, detected_at=started
            )
        elif transition.update_incident:
            incident = (
                monitor.incidents.select_for_update().filter(incident_key=transition.update_incident).active().first()
            )
            if incident is not None:
                incident_services.update_evidence(
                    incident=incident,
                    check=check,
                    severity=(
                        incident_services.severity_for(transition.update_incident, check.error_category)
                        if check.outcome == "failure"
                        else None
                    ),
                )
            elif check.outcome == "failure" and monitor.status == Monitor.Status.DOWN:
                # Down with no active incident (e.g. it was resolved manually): reopen.
                incident_services.open_or_update(
                    monitor=monitor,
                    incident_key=transition.update_incident,
                    check=check,
                    detected_at=monitor.failing_since,
                )
    return check
