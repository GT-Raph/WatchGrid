"""Monitor configuration workflows (FR-MON-001 … FR-MON-005)."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record_audit, record_security_event
from common.exceptions import Conflict, InvalidStateTransition, TargetNotAllowed, ValidationFailed
from common.permissions.tenancy import require_capability
from common.security.redaction import REDACTED

from .models import Monitor
from .validators import TargetBlockedError, UnsafeURLError, check_target

CONFIG_FIELDS = (
    "name",
    "monitor_type",
    "url",
    "http_method",
    "interval_seconds",
    "timeout_seconds",
    "expected_status",
    "response_time_threshold_ms",
    "failure_threshold",
    "recovery_threshold",
    "follow_redirects",
    "max_redirects",
    "body",
    "assertion_mode",
    "assertion_text",
)
# Changing any of these means previous observations no longer describe the target.
TARGET_FIELDS = frozenset({"url", "http_method", "expected_status", "assertion_mode", "assertion_text", "body"})


def validate_target(url: str, *, actor=None, organization=None, request=None) -> None:
    try:
        check_target(url)
    except UnsafeURLError as exc:
        raise ValidationFailed(str(exc), details={"url": [str(exc)]}) from exc
    except TargetBlockedError as exc:
        record_security_event(
            action="security.ssrf_blocked",
            organization=organization,
            actor=actor,
            metadata={"stage": "configuration", "host": exc.host, "address": exc.address},
            request=request,
        )
        raise TargetNotAllowed(
            "This URL resolves to a private, loopback, link-local or otherwise prohibited address.",
            details={"url": ["Target is not allowed by network policy."]},
        ) from exc


def _merge_headers(existing: dict[str, str], incoming: dict[str, str] | None) -> dict[str, str]:
    """Write-only semantics: a redacted placeholder keeps the stored secret value."""
    if incoming is None:
        return existing
    merged = {}
    for key, value in incoming.items():
        if value == REDACTED and key in existing:
            merged[key] = existing[key]
        else:
            merged[key] = value
    return merged


def create_monitor(*, actor, project, data: dict, request=None) -> Monitor:
    require_capability(actor, project.organization_id, "monitor.manage")
    if project.status != project.Status.ACTIVE:
        raise InvalidStateTransition("Monitors cannot be added to an archived project.")
    validate_target(data["url"], actor=actor, organization=project.organization, request=request)
    monitor = Monitor(project=project, created_by=actor, next_check_at=timezone.now())
    for field in CONFIG_FIELDS:
        if field in data:
            setattr(monitor, field, data[field])
    monitor.headers = data.get("headers") or {}
    try:
        with transaction.atomic():
            monitor.save()
            record_audit(
                actor=actor,
                organization=project.organization,
                action="monitor.created",
                resource=monitor,
                metadata={"name": monitor.name, "url": monitor.url, "interval_seconds": monitor.interval_seconds},
                request=request,
            )
    except IntegrityError as exc:
        raise Conflict("A monitor with this name already exists in the project.", code="NAME_TAKEN") from exc
    return monitor


def update_monitor(*, actor, monitor: Monitor, data: dict, request=None) -> Monitor:
    require_capability(actor, monitor.project.organization_id, "monitor.manage")
    if monitor.archived_at is not None:
        raise InvalidStateTransition("Archived monitors cannot be edited.")
    if "url" in data and data["url"] != monitor.url:
        validate_target(data["url"], actor=actor, organization=monitor.project.organization, request=request)

    changes: dict[str, list] = {}
    for field in CONFIG_FIELDS:
        if field in data and getattr(monitor, field) != data[field]:
            changes[field] = [getattr(monitor, field), data[field]]
            setattr(monitor, field, data[field])
    if "headers" in data:
        merged = _merge_headers(monitor.headers, data["headers"])
        if merged != monitor.headers:
            changes["headers"] = ["(changed)", sorted(merged.keys())]
            monitor.headers = merged

    if not changes:
        return monitor

    if TARGET_FIELDS & changes.keys() and monitor.is_active:
        _reset_runtime_state(monitor, Monitor.Status.UNKNOWN)
        monitor.next_check_at = timezone.now()
    try:
        with transaction.atomic():
            monitor.save()
            record_audit(
                actor=actor,
                organization=monitor.project.organization,
                action="monitor.updated",
                resource=monitor,
                metadata={"changes": changes},
                request=request,
            )
    except IntegrityError as exc:
        raise Conflict("A monitor with this name already exists in the project.", code="NAME_TAKEN") from exc
    return monitor


def _reset_runtime_state(monitor: Monitor, status: str) -> None:
    monitor.status = status
    monitor.consecutive_failures = 0
    monitor.consecutive_successes = 0
    monitor.consecutive_soft_failures = 0
    monitor.failing_since = None
    monitor.degraded_since = None
    monitor.last_status_change_at = timezone.now()


def pause_monitor(*, actor, monitor: Monitor, request=None) -> Monitor:
    """Stop scheduled checks and outage alerts without deleting history (BR-005)."""
    require_capability(actor, monitor.project.organization_id, "monitor.operate")
    if monitor.archived_at is not None:
        raise InvalidStateTransition("Archived monitors cannot be paused or resumed.")
    if not monitor.is_active and monitor.status == Monitor.Status.PAUSED:
        return monitor
    with transaction.atomic():
        monitor.is_active = False
        _reset_runtime_state(monitor, Monitor.Status.PAUSED)
        monitor.save()
        record_audit(
            actor=actor,
            organization=monitor.project.organization,
            action="monitor.paused",
            resource=monitor,
            request=request,
        )
    return monitor


def resume_monitor(*, actor, monitor: Monitor, request=None) -> Monitor:
    require_capability(actor, monitor.project.organization_id, "monitor.operate")
    if monitor.archived_at is not None:
        raise InvalidStateTransition("Archived monitors cannot be paused or resumed.")
    if monitor.project.status != monitor.project.Status.ACTIVE:
        raise InvalidStateTransition("Monitors in an archived project cannot be resumed.")
    if monitor.is_active:
        return monitor
    with transaction.atomic():
        monitor.is_active = True
        _reset_runtime_state(monitor, Monitor.Status.UNKNOWN)
        monitor.next_check_at = timezone.now()
        monitor.save()
        record_audit(
            actor=actor,
            organization=monitor.project.organization,
            action="monitor.resumed",
            resource=monitor,
            request=request,
        )
    return monitor


def archive_monitor(*, actor, monitor: Monitor, request=None) -> Monitor:
    require_capability(actor, monitor.project.organization_id, "monitor.manage")
    if monitor.archived_at is not None:
        return monitor
    with transaction.atomic():
        monitor.archived_at = timezone.now()
        monitor.is_active = False
        _reset_runtime_state(monitor, Monitor.Status.PAUSED)
        monitor.save()
        record_audit(
            actor=actor,
            organization=monitor.project.organization,
            action="monitor.archived",
            resource=monitor,
            request=request,
        )
    return monitor


def request_manual_check(*, actor, monitor: Monitor, request=None) -> str:
    """Queue a one-off check that does not alter cadence or state (FR-MON-004)."""
    require_capability(actor, monitor.project.organization_id, "monitor.operate")
    if monitor.archived_at is not None:
        raise InvalidStateTransition("Archived monitors cannot be tested.")
    from apps.checks.tasks import run_manual_check

    execution_key = f"m:{uuid.uuid4().hex}"
    run_manual_check.apply_async(
        args=[str(monitor.id), execution_key],
        expires=settings.MONITOR_MAX_TIMEOUT_SECONDS * 4,
    )
    return execution_key
