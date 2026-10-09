"""Incident lifecycle (FR-INC-002, FR-INC-004, Section 15).

Automatic transitions are invoked by the check pipeline inside its transaction, with
the monitor row locked, so "find active incident or create one" cannot race for the
same monitor. The partial unique constraint is the database backstop.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_audit
from apps.monitors.state_machine import AVAILABILITY, PERFORMANCE
from common.constants import SEVERITY_RANK, Severity
from common.exceptions import InvalidStateTransition, ValidationFailed
from common.permissions.tenancy import require_capability

from .models import Incident, IncidentEvent

UNREACHABLE = frozenset({"dns", "connection", "timeout", "tls", "blocked"})
MAX_EVIDENCE_SAMPLES = 10


def severity_for(incident_key: str, failure_category: str) -> str:
    """Section 15.3: unreachable → CRITICAL, failing responses → MAJOR, slow → MINOR."""
    if incident_key == PERFORMANCE:
        return Severity.MINOR
    if failure_category in UNREACHABLE:
        return Severity.CRITICAL
    return Severity.MAJOR


def check_sample(check) -> dict:
    """One check as stored in ``Incident.evidence`` (failure or recovery samples)."""
    return {
        "check_id": check.pk,
        "checked_at": check.checked_at.isoformat(),
        "outcome": check.outcome,
        "status_code": check.status_code,
        "response_time_ms": check.response_time_ms,
        "error_category": check.error_category,
        "error_message": check.error_message,
    }


def _append_sample(incident: Incident, check, field: str = "samples") -> None:
    evidence = dict(incident.evidence or {})
    samples = list(evidence.get(field, []))
    samples.append(check_sample(check))
    evidence[field] = samples[-MAX_EVIDENCE_SAMPLES:]
    incident.evidence = evidence


def _emit(incident: Incident, event_type: str) -> None:
    from apps.notifications.services import queue_incident_event

    queue_incident_event(incident, event_type)


def open_or_update(*, monitor, incident_key: str, check, detected_at) -> tuple[Incident, bool]:
    """Create the active incident for ``(monitor, key)`` or refresh the existing one."""
    incident = Incident.objects.select_for_update().filter(monitor=monitor, incident_key=incident_key).active().first()
    severity = severity_for(incident_key, check.error_category)
    if incident is not None:
        update_evidence(incident=incident, check=check, severity=severity)
        return incident, False

    title = f"{monitor.name} is down" if incident_key == AVAILABILITY else f"{monitor.name} is degraded"
    incident = Incident(
        project_id=monitor.project_id,
        monitor=monitor,
        incident_key=incident_key,
        title=title,
        severity=severity,
        failure_type=check.error_category,
        summary=check.error_message,
        detected_at=detected_at or check.checked_at,
        confirmed_at=check.checked_at,
        last_seen_at=check.checked_at,
        failure_count=max(monitor.consecutive_failures, monitor.consecutive_soft_failures, 1),
    )
    _append_sample(incident, check)
    incident.save()
    IncidentEvent.objects.create(
        incident=incident,
        kind=IncidentEvent.Kind.OPENED,
        to_status=incident.status,
        message=check.error_message,
        data={"check_id": check.pk, "severity": severity},
    )
    _emit(incident, "opened")
    return incident, True


def update_evidence(*, incident: Incident, check, severity: str | None = None) -> Incident:
    incident.last_seen_at = check.checked_at
    fields = ["last_seen_at", "evidence", "updated_at"]
    if check.outcome != "success":
        incident.failure_count += 1
        fields.append("failure_count")
        if check.error_message:
            incident.summary = check.error_message
            fields.append("summary")
        _append_sample(incident, check)
    else:
        _append_sample(incident, check, field="recovery_samples")
    if severity and SEVERITY_RANK[severity] > SEVERITY_RANK[incident.severity]:
        IncidentEvent.objects.create(
            incident=incident,
            kind=IncidentEvent.Kind.SEVERITY_CHANGED,
            message=f"Severity raised from {incident.severity} to {severity}.",
            data={"from": incident.severity, "to": severity},
        )
        incident.severity = severity
        fields.append("severity")
    incident.save(update_fields=list(dict.fromkeys(fields)))
    return incident


def resolve_automatically(*, monitor, incident_key: str, check, superseded: bool = False) -> Incident | None:
    incident = Incident.objects.select_for_update().filter(monitor=monitor, incident_key=incident_key).active().first()
    if incident is None:
        return None
    previous = incident.status
    incident.status = Incident.Status.RESOLVED
    incident.resolved_at = check.checked_at
    incident.last_seen_at = check.checked_at
    incident.resolution_type = Incident.ResolutionType.SUPERSEDED if superseded else Incident.ResolutionType.AUTOMATIC
    if not superseded:
        _append_sample(incident, check, field="recovery_samples")
    incident.save()
    message = (
        "Superseded by an availability outage."
        if superseded
        else f"Recovered after {monitor.recovery_threshold} consecutive successful check(s)."
    )
    IncidentEvent.objects.create(
        incident=incident,
        kind=IncidentEvent.Kind.RESOLVED,
        from_status=previous,
        to_status=incident.status,
        message=message,
        data={"check_id": check.pk, "resolution": incident.resolution_type},
    )
    if not superseded:
        _emit(incident, "resolved")
    return incident


# --------------------------------------------------------------------------------------
# Operator actions
# --------------------------------------------------------------------------------------

OPERATOR_STATUSES = (
    Incident.Status.ACKNOWLEDGED,
    Incident.Status.INVESTIGATING,
    Incident.Status.MONITORING,
)


def acknowledge(*, actor, incident: Incident, note: str = "", request=None) -> Incident:
    require_capability(actor, incident.project.organization_id, "incident.manage")
    with transaction.atomic():
        incident = Incident.objects.select_for_update().get(pk=incident.pk)
        if not incident.is_active:
            raise InvalidStateTransition("Resolved incidents cannot be acknowledged.")
        if incident.acknowledged_at is None:
            incident.acknowledged_at = timezone.now()
            incident.acknowledged_by = actor
        previous = incident.status
        if incident.status == Incident.Status.OPEN:
            incident.status = Incident.Status.ACKNOWLEDGED
        incident.save()
        IncidentEvent.objects.create(
            incident=incident,
            kind=IncidentEvent.Kind.ACKNOWLEDGED,
            actor=actor,
            from_status=previous,
            to_status=incident.status,
            message=note,
        )
        record_audit(
            actor=actor,
            organization=incident.project.organization,
            action="incident.acknowledged",
            resource=incident,
            request=request,
        )
    return incident


def change_status(*, actor, incident: Incident, status: str, note: str = "", request=None) -> Incident:
    require_capability(actor, incident.project.organization_id, "incident.manage")
    if status == Incident.Status.RESOLVED:
        return resolve_manually(actor=actor, incident=incident, note=note, request=request)
    if status not in OPERATOR_STATUSES:
        raise ValidationFailed("Unsupported incident status.", details={"status": ["invalid"]})
    with transaction.atomic():
        incident = Incident.objects.select_for_update().get(pk=incident.pk)
        if not incident.is_active:
            raise InvalidStateTransition("Resolved incidents cannot change status.")
        previous = incident.status
        if previous == status:
            return incident
        incident.status = status
        if incident.acknowledged_at is None:
            incident.acknowledged_at = timezone.now()
            incident.acknowledged_by = actor
        incident.save()
        IncidentEvent.objects.create(
            incident=incident,
            kind=IncidentEvent.Kind.STATUS_CHANGED,
            actor=actor,
            from_status=previous,
            to_status=status,
            message=note,
        )
    return incident


def resolve_manually(*, actor, incident: Incident, note: str = "", request=None) -> Incident:
    """Manual resolution. If the monitor is still failing, the next confirmed failure
    opens a new incident — the platform never hides an ongoing outage."""
    require_capability(actor, incident.project.organization_id, "incident.manage")
    with transaction.atomic():
        incident = Incident.objects.select_for_update().get(pk=incident.pk)
        if not incident.is_active:
            raise InvalidStateTransition("The incident is already resolved.")
        previous = incident.status
        now = timezone.now()
        incident.status = Incident.Status.RESOLVED
        incident.resolved_at = now
        incident.resolved_by = actor
        incident.resolution_type = Incident.ResolutionType.MANUAL
        incident.save()
        IncidentEvent.objects.create(
            incident=incident,
            kind=IncidentEvent.Kind.RESOLVED,
            actor=actor,
            from_status=previous,
            to_status=incident.status,
            message=note or "Resolved manually.",
        )
        record_audit(
            actor=actor,
            organization=incident.project.organization,
            action="incident.resolved_manually",
            resource=incident,
            metadata={"note": note[:200]},
            request=request,
        )
        _emit(incident, "resolved")
    return incident


def add_note(*, actor, incident: Incident, message: str) -> IncidentEvent:
    require_capability(actor, incident.project.organization_id, "incident.manage")
    if not message.strip():
        raise ValidationFailed("A note cannot be empty.", details={"message": ["required"]})
    return IncidentEvent.objects.create(
        incident=incident, kind=IncidentEvent.Kind.NOTE, actor=actor, message=message.strip()[:5000]
    )
