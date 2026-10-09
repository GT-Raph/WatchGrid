"""Error ingestion pipeline (Section 16, Figure 4).

Request path (synchronous, cheap): authenticate key → validate → redact → pseudonymise
the user reference → normalise → fingerprint → sampling decision → enqueue.
Worker path: idempotent upsert of the group, affected-user counting, optional raw
event storage and new/regression notifications.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.services import record_audit
from common.exceptions import InvalidStateTransition, ValidationFailed
from common.permissions.tenancy import require_capability
from common.security.crypto import keyed_hash
from common.security.redaction import redact_data, redact_text

from .fingerprint import FINGERPRINT_VERSION, compute_fingerprint, normalize_endpoint, normalize_message, top_frame
from .models import ErrorEvent, ErrorGroup, ErrorGroupUser

logger = logging.getLogger("watchgrid.errors")

DEDUPE_TTL_SECONDS = 60 * 60 * 24
MAX_FUTURE_SKEW = timedelta(minutes=5)


def prepare_event(*, project, data: dict) -> dict:
    """Build the JSON-serialisable job payload for :func:`process_event`."""
    now = timezone.now()
    occurred_at = data.get("occurred_at") or now
    if occurred_at > now + MAX_FUTURE_SKEW:
        occurred_at = now
    max_age = timedelta(days=settings.RETENTION_ERROR_EVENT_DAYS)
    if occurred_at < now - max_age:
        raise ValidationFailed("The event is older than the retention period.", details={"occurred_at": ["too old"]})

    environment = (data.get("environment") or "production").strip().lower()
    message = redact_text(data["message"])[:2000]
    stack_trace = redact_text(data.get("stack_trace") or "")[: settings.ERROR_EVENT_MAX_STACK_TRACE_CHARS]
    endpoint = redact_text(data.get("endpoint") or "")[:500]
    normalized = normalize_message(message)
    frame = top_frame(stack_trace)
    endpoint_key = normalize_endpoint(endpoint)
    fingerprint = compute_fingerprint(
        project_id=project.id,
        environment=environment,
        error_type=data["error_type"],
        normalized_message=normalized,
        endpoint=endpoint_key,
        frame=frame,
        custom=data.get("fingerprint"),
    )
    user_reference = (data.get("user_reference") or "").strip()
    user_hash = keyed_hash(f"{project.id}:{user_reference}", purpose="error-user") if user_reference else ""

    # Sampling: beyond the per-minute threshold for one fingerprint, keep counting but
    # stop storing raw events (Section 16.4).
    bucket = f"errors:rate:{project.id}:{fingerprint}:{now:%Y%m%d%H%M}"
    cache.add(bucket, 0, timeout=120)
    try:
        seen_this_minute = cache.incr(bucket)
    except ValueError:
        seen_this_minute = 1
    store_raw = seen_this_minute <= settings.ERROR_EVENT_SAMPLE_THRESHOLD_PER_MINUTE

    return {
        "event_id": str(data.get("event_id") or uuid.uuid4()),
        "project_id": str(project.id),
        "fingerprint": fingerprint,
        "environment": environment[:64],
        "level": data.get("level") or "error",
        "error_type": data["error_type"][:200],
        "message": message,
        "normalized_message": normalized,
        "endpoint": endpoint,
        "http_method": (data.get("http_method") or "").upper()[:10],
        "application_version": (data.get("application_version") or "")[:100],
        "user_hash": user_hash,
        "stack_trace": stack_trace,
        "top_frame": frame,
        "metadata": redact_data(data.get("metadata") or {}),
        "occurred_at": occurred_at.isoformat(),
        "received_at": now.isoformat(),
        "store_raw": store_raw,
    }


def enqueue_event(payload: dict) -> None:
    from .tasks import process_error_event

    process_error_event.delay(payload)


def _parse(value: str) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"invalid datetime {value!r}")
    return parsed


def process_event(payload: dict) -> str:
    """Idempotently fold one event into its group. Returns created | updated | duplicate."""
    event_id = payload["event_id"]
    if not cache.add(f"errors:event:{event_id}", 1, timeout=DEDUPE_TTL_SECONDS):
        return "duplicate"
    occurred_at = _parse(payload["occurred_at"])
    received_at = _parse(payload["received_at"])
    notify: str | None = None

    try:
        with transaction.atomic():
            group = (
                ErrorGroup.objects.select_for_update()
                .filter(
                    project_id=payload["project_id"],
                    fingerprint=payload["fingerprint"],
                    environment=payload["environment"],
                )
                .first()
            )
            created = False
            if group is None:
                try:
                    with transaction.atomic():
                        group = ErrorGroup.objects.create(
                            project_id=payload["project_id"],
                            fingerprint=payload["fingerprint"],
                            fingerprint_version=FINGERPRINT_VERSION,
                            environment=payload["environment"],
                            level=payload["level"],
                            error_type=payload["error_type"],
                            normalized_message=payload["normalized_message"],
                            sample_message=payload["message"],
                            endpoint=payload["endpoint"],
                            http_method=payload["http_method"],
                            top_frame=payload["top_frame"],
                            first_seen=occurred_at,
                            last_seen=occurred_at,
                            last_release=payload["application_version"],
                        )
                    created = True
                except IntegrityError:
                    group = ErrorGroup.objects.select_for_update().get(
                        project_id=payload["project_id"],
                        fingerprint=payload["fingerprint"],
                        environment=payload["environment"],
                    )

            if payload["store_raw"]:
                try:
                    with transaction.atomic():
                        ErrorEvent.objects.create(
                            id=event_id,
                            error_group=group,
                            project_id=payload["project_id"],
                            occurred_at=occurred_at,
                            received_at=received_at,
                            level=payload["level"],
                            message=payload["message"],
                            endpoint=payload["endpoint"],
                            http_method=payload["http_method"],
                            application_version=payload["application_version"],
                            user_reference_hash=payload["user_hash"],
                            stack_trace=payload["stack_trace"],
                            metadata=payload["metadata"],
                        )
                except IntegrityError:
                    return "duplicate"
                group.stored_event_count += 1

            group.occurrence_count += 1
            if occurred_at > group.last_seen:
                group.last_seen = occurred_at
                group.level = payload["level"]
                if payload["application_version"]:
                    group.last_release = payload["application_version"]
            if occurred_at < group.first_seen:
                group.first_seen = occurred_at

            if payload["user_hash"]:
                user_ref, user_created = ErrorGroupUser.objects.get_or_create(
                    error_group=group,
                    user_hash=payload["user_hash"],
                    defaults={"first_seen": occurred_at, "last_seen": occurred_at},
                )
                if user_created:
                    group.affected_users += 1
                elif occurred_at > user_ref.last_seen:
                    ErrorGroupUser.objects.filter(pk=user_ref.pk).update(last_seen=occurred_at)

            if created:
                notify = "error_new"
            elif group.status == ErrorGroup.Status.RESOLVED:
                group.status = ErrorGroup.Status.UNRESOLVED
                group.resolved_at = None
                group.resolved_by = None
                group.regression_count += 1
                notify = "error_regression"
            group.save()

            if notify:
                group_id = str(group.id)
                from apps.notifications.tasks import dispatch_error_event

                transaction.on_commit(lambda: dispatch_error_event.delay(group_id, notify))
    except Exception:
        cache.delete(f"errors:event:{event_id}")  # allow a retry to process it
        raise
    return "created" if created else "updated"


# --------------------------------------------------------------------------------------
# Operator actions
# --------------------------------------------------------------------------------------


def set_status(*, actor, group: ErrorGroup, status: str, request=None) -> ErrorGroup:
    require_capability(actor, group.project.organization_id, "errors.manage")
    if status not in ErrorGroup.Status.values:
        raise ValidationFailed("Unknown status.", details={"status": ["invalid"]})
    if group.status == status:
        raise InvalidStateTransition(f"The error group is already {status}.")
    previous = group.status
    group.status = status
    if status == ErrorGroup.Status.RESOLVED:
        group.resolved_at = timezone.now()
        group.resolved_by = actor
    else:
        group.resolved_at = None
        group.resolved_by = None
    group.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
    record_audit(
        actor=actor,
        organization=group.project.organization,
        action=f"error_group.{status}",
        resource=group,
        metadata={"from": previous, "to": status},
        request=request,
    )
    return group
