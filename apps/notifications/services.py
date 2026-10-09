"""Alert routing: policy matching, deduplicated ledger rows and delivery attempts."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record_audit
from common.constants import SEVERITY_RANK, Role, Severity
from common.permissions.tenancy import require_capability
from common.security.crypto import decrypt_json, encrypt_json

from .messages import render_error_group, render_incident
from .models import AlertPolicy, EventType, MaintenanceWindow, Notification
from .providers import DeliveryError, get_provider

logger = logging.getLogger("watchgrid.notifications")

MAX_REMINDERS = 48
SENDING_LEASE = timedelta(minutes=5)

DEFAULT_POLICY_EVENTS = [EventType.MONITOR_DOWN, EventType.MONITOR_RECOVERED, EventType.ERROR_NEW]


# --------------------------------------------------------------------------------------
# Policy configuration helpers
# --------------------------------------------------------------------------------------


def mask_webhook_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "webhook"
    return f"{parts.scheme}://{parts.hostname}/…" if parts.hostname else "webhook"


def store_channels(channels: list[dict]) -> list[dict]:
    """Normalise channel config for storage; webhook URLs are encrypted at rest."""
    stored = []
    for channel in channels:
        kind = channel.get("type")
        if kind == "email":
            stored.append(
                {
                    "type": "email",
                    "to": sorted({e.strip().lower() for e in channel.get("to", []) if e.strip()}),
                    "to_roles": sorted(set(channel.get("to_roles", []))),
                }
            )
        elif kind == "webhook":
            if channel.get("url"):
                stored.append(
                    {
                        "type": "webhook",
                        "url_encrypted": encrypt_json(channel["url"]),
                        "url_display": mask_webhook_url(channel["url"]),
                    }
                )
            elif channel.get("url_encrypted"):
                stored.append(
                    {
                        "type": "webhook",
                        "url_encrypted": channel["url_encrypted"],
                        "url_display": channel.get("url_display", "webhook"),
                    }
                )
    return stored


def public_channels(channels: list[dict]) -> list[dict]:
    result = []
    for channel in channels or []:
        if channel.get("type") == "webhook":
            result.append({"type": "webhook", "url_display": channel.get("url_display", "webhook")})
        else:
            result.append(channel)
    return result


def create_default_alert_policy(project) -> AlertPolicy:
    return AlertPolicy.objects.create(
        project=project,
        name="Default — email owners and administrators",
        event_types=[str(e) for e in DEFAULT_POLICY_EVENTS],
        min_severity=Severity.INFO,
        channels=[{"type": "email", "to": [], "to_roles": [Role.OWNER.value, Role.ADMIN.value]}],
    )


def save_alert_policy(*, actor, project, data: dict, policy: AlertPolicy | None = None, request=None) -> AlertPolicy:
    require_capability(actor, project.organization_id, "alertpolicy.manage")
    creating = policy is None
    policy = policy or AlertPolicy(project=project, created_by=actor)
    previous_webhooks = [c for c in policy.channels or [] if c.get("type") == "webhook"]
    for field in (
        "name",
        "enabled",
        "monitor",
        "event_types",
        "min_severity",
        "delay_seconds",
        "repeat_interval_minutes",
    ):
        if field in data:
            setattr(policy, field, data[field])
    if "channels" in data:
        incoming = []
        webhook_index = 0
        for channel in data["channels"]:
            if channel.get("type") == "webhook" and not channel.get("url") and webhook_index < len(previous_webhooks):
                # Unchanged webhook entries arrive without the secret URL; keep the stored one.
                incoming.append(previous_webhooks[webhook_index])
            else:
                incoming.append(channel)
            if channel.get("type") == "webhook":
                webhook_index += 1
        policy.channels = store_channels(incoming)
    with transaction.atomic():
        policy.save()
        record_audit(
            actor=actor,
            organization=project.organization,
            action="alert_policy.created" if creating else "alert_policy.updated",
            resource=policy,
            metadata={
                "name": policy.name,
                "event_types": policy.event_types,
                "channels": public_channels(policy.channels),
            },
            request=request,
        )
    return policy


def delete_alert_policy(*, actor, policy: AlertPolicy, request=None) -> None:
    require_capability(actor, policy.project.organization_id, "alertpolicy.delete")
    with transaction.atomic():
        record_audit(
            actor=actor,
            organization=policy.project.organization,
            action="alert_policy.deleted",
            resource=policy,
            metadata={"name": policy.name},
            request=request,
        )
        policy.delete()


# --------------------------------------------------------------------------------------
# Matching and ledger creation
# --------------------------------------------------------------------------------------


def _incident_event_type(incident, transition: str) -> str:
    if transition == "resolved":
        return EventType.MONITOR_RECOVERED
    return EventType.MONITOR_DEGRADED if incident.incident_key == "performance" else EventType.MONITOR_DOWN


def matching_policies(*, project_id, event_type: str, severity: str, monitor_id=None):
    policies = AlertPolicy.objects.filter(project_id=project_id, enabled=True)
    for policy in policies:
        if policy.monitor_id and policy.monitor_id != monitor_id:
            continue
        if event_type not in (policy.event_types or []):
            continue
        if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(policy.min_severity, 0):
            continue
        yield policy


def _expand_recipients(policy: AlertPolicy):
    """Yield ``(channel, recipient, target_dict)`` for every destination in a policy."""
    from apps.organizations.models import Membership

    for channel in policy.channels or []:
        kind = channel.get("type")
        if kind == "email":
            addresses = set(channel.get("to") or [])
            roles = channel.get("to_roles") or []
            if roles:
                addresses.update(
                    Membership.objects.filter(
                        organization_id=policy.project.organization_id, role__in=roles, user__is_active=True
                    ).values_list("user__email", flat=True)
                )
            for address in sorted(addresses):
                yield Notification.Channel.EMAIL, address, {}
        elif kind == "webhook":
            url = decrypt_json(channel.get("url_encrypted"))
            if url:
                yield Notification.Channel.WEBHOOK, channel.get("url_display", "webhook"), {"url": url}


def _dedupe_key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def in_maintenance(project_id, monitor_id, moment=None) -> bool:
    """True when a project-wide (or this monitor's) maintenance window is active."""
    moment = moment or timezone.now()
    windows = MaintenanceWindow.objects.filter(project_id=project_id).active_at(moment)
    if monitor_id is not None:
        windows = windows.filter(Q(monitor__isnull=True) | Q(monitor_id=monitor_id))
    else:
        windows = windows.filter(monitor__isnull=True)
    return windows.exists()


def _create_ledger_rows(
    *,
    project,
    policy,
    event_type,
    transition,
    level,
    payload,
    dedupe_prefix,
    incident=None,
    error_group=None,
    suppressed_reason="",
) -> list[Notification]:
    created_rows = []
    deliver_after = timezone.now() + timedelta(seconds=policy.delay_seconds if transition == "opened" else 0)
    for channel, recipient, target in _expand_recipients(policy):
        key = _dedupe_key(dedupe_prefix, policy.id, transition, level, channel, recipient)
        try:
            with transaction.atomic():
                notification = Notification.objects.create(
                    project=project,
                    incident=incident,
                    error_group=error_group,
                    policy=policy,
                    event_type=event_type,
                    transition=transition,
                    escalation_level=level,
                    channel=channel,
                    recipient=recipient[:254],
                    target_encrypted=encrypt_json(target) if target else "",
                    dedupe_key=key,
                    status=Notification.Status.SUPPRESSED if suppressed_reason else Notification.Status.PENDING,
                    status_reason=suppressed_reason,
                    deliver_after=deliver_after,
                    payload=payload,
                )
        except IntegrityError:
            continue  # already queued for this exact event/policy/recipient
        if not suppressed_reason:
            created_rows.append(notification)
    return created_rows


def _schedule_delivery(rows: list[Notification]) -> None:
    from .tasks import deliver_notification

    def enqueue():
        now = timezone.now()
        for row in rows:
            countdown = max(0, int((row.deliver_after - now).total_seconds())) if row.deliver_after else 0
            deliver_notification.apply_async(args=[str(row.id)], countdown=countdown)

    transaction.on_commit(enqueue)


def queue_incident_event(incident, transition: str) -> None:
    """Called by incident services; ledger rows are created after the incident commits."""
    from .tasks import dispatch_incident_event

    incident_id = str(incident.id)
    transaction.on_commit(lambda: dispatch_incident_event.delay(incident_id, transition, 0))


def create_incident_notifications(incident, transition: str, level: int = 0) -> list[Notification]:
    event_type = _incident_event_type(incident, transition)
    monitor_id = incident.monitor_id
    suppressed = ""
    if transition in ("opened", "reminder") and in_maintenance(incident.project_id, monitor_id):
        suppressed = "Suppressed by an active maintenance window."
    if incident.monitor is not None and not incident.monitor.is_active and transition != "resolved":
        suppressed = "Monitor is paused."
    rows: list[Notification] = []
    payload = render_incident(incident, event_type, level)
    for policy in matching_policies(
        project_id=incident.project_id, event_type=event_type, severity=incident.severity, monitor_id=monitor_id
    ):
        if transition == "reminder" and not policy.repeat_interval_minutes:
            continue
        rows.extend(
            _create_ledger_rows(
                project=incident.project,
                policy=policy,
                event_type=event_type,
                transition=transition,
                level=level,
                payload=payload,
                dedupe_prefix=f"incident:{incident.id}",
                incident=incident,
                suppressed_reason=suppressed,
            )
        )
    _schedule_delivery(rows)
    return rows


ERROR_LEVEL_SEVERITY = {
    "fatal": Severity.CRITICAL,
    "critical": Severity.CRITICAL,
    "error": Severity.MAJOR,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
    "debug": Severity.INFO,
}


def create_error_notifications(group, event_type: str) -> list[Notification]:
    severity = ERROR_LEVEL_SEVERITY.get(group.level, Severity.MAJOR)
    payload = render_error_group(group, event_type)
    rows: list[Notification] = []
    suppressed = "Suppressed by an active maintenance window." if in_maintenance(group.project_id, None) else ""
    for policy in matching_policies(project_id=group.project_id, event_type=event_type, severity=severity):
        rows.extend(
            _create_ledger_rows(
                project=group.project,
                policy=policy,
                event_type=event_type,
                transition="opened",
                level=0,
                payload=payload,
                dedupe_prefix=f"error:{group.id}:{group.regression_count}",
                error_group=group,
                suppressed_reason=suppressed,
            )
        )
    _schedule_delivery(rows)
    return rows


def create_due_reminders(now=None) -> int:
    """Reminder/escalation notifications for unresolved incidents (Section 17.2)."""
    from apps.incidents.models import Incident

    now = now or timezone.now()
    created = 0
    policies = AlertPolicy.objects.filter(enabled=True, repeat_interval_minutes__gt=0).select_related("project")
    project_ids = {p.project_id for p in policies}
    if not project_ids:
        return 0
    incidents = Incident.objects.active().filter(project_id__in=project_ids).select_related("project", "monitor")
    for incident in incidents:
        if incident.monitor is None or not incident.monitor.is_active:
            continue
        if in_maintenance(incident.project_id, incident.monitor_id, now):
            continue
        event_type = _incident_event_type(incident, "reminder")
        for policy in matching_policies(
            project_id=incident.project_id,
            event_type=event_type,
            severity=incident.severity,
            monitor_id=incident.monitor_id,
        ):
            if not policy.repeat_interval_minutes:
                continue
            elapsed = (now - incident.confirmed_at).total_seconds()
            level = min(int(elapsed // (policy.repeat_interval_minutes * 60)), MAX_REMINDERS)
            if level < 1:
                continue
            rows = _create_ledger_rows(
                project=incident.project,
                policy=policy,
                event_type=event_type,
                transition="reminder",
                level=level,
                payload=render_incident(incident, event_type, level),
                dedupe_prefix=f"incident:{incident.id}",
                incident=incident,
            )
            _schedule_delivery(rows)
            created += len(rows)
    return created


# --------------------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------------------


def attempt_delivery(notification_id) -> str:
    """Try to deliver one ledger row. Returns sent | skipped | suppressed | retry | failed.

    The row is leased (status=SENDING) under a row lock before the network call, so a
    duplicated Celery message cannot produce a second successful delivery (NOT-001).
    """
    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update(of=("self",))
            .select_related("incident")
            .filter(pk=notification_id)
            .first()
        )
        if notification is None:
            return "skipped"
        if notification.status in (
            Notification.Status.SENT,
            Notification.Status.SUPPRESSED,
            Notification.Status.FAILED,
        ):
            return "skipped"
        if (
            notification.status == Notification.Status.SENDING
            and notification.updated_at > timezone.now() - SENDING_LEASE
        ):
            return "skipped"
        incident = notification.incident
        if incident is not None and notification.transition in ("opened", "reminder") and not incident.is_active:
            notification.status = Notification.Status.SUPPRESSED
            notification.status_reason = "Incident resolved before the alert was due."
            notification.save(update_fields=["status", "status_reason", "updated_at"])
            return "suppressed"
        notification.status = Notification.Status.SENDING
        notification.attempts += 1
        notification.save(update_fields=["status", "attempts", "updated_at"])

    try:
        provider = get_provider(notification.channel)
        provider.send(
            recipient=notification.recipient,
            target=decrypt_json(notification.target_encrypted, default={}) or {},
            payload=notification.payload,
        )
    except DeliveryError as exc:
        final = not exc.retryable or notification.attempts >= settings.NOTIFICATION_MAX_RETRIES
        Notification.objects.filter(pk=notification.pk).update(
            status=Notification.Status.FAILED if final else Notification.Status.PENDING,
            last_error=str(exc)[:500],
            updated_at=timezone.now(),
        )
        logger.warning(
            "notification_delivery_failed",
            extra={"notification_id": str(notification.pk), "channel": notification.channel, "final": final},
        )
        return "failed" if final else "retry"

    Notification.objects.filter(pk=notification.pk).update(
        status=Notification.Status.SENT, sent_at=timezone.now(), last_error="", updated_at=timezone.now()
    )
    return "sent"
