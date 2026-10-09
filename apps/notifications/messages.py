"""Rendering of alert content (email subject/body and webhook JSON)."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from .models import EventType

_HEADLINES = {
    EventType.MONITOR_DOWN: "DOWN",
    EventType.MONITOR_DEGRADED: "DEGRADED",
    EventType.MONITOR_RECOVERED: "RECOVERED",
    EventType.ERROR_NEW: "NEW ERROR",
    EventType.ERROR_REGRESSION: "REGRESSION",
}


def _fmt(dt) -> str:
    return dt.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def render_incident(incident, event_type: str, escalation_level: int = 0) -> dict:
    monitor = incident.monitor
    project = incident.project
    headline = _HEADLINES.get(event_type, event_type.upper())
    reminder = f" (reminder #{escalation_level})" if escalation_level else ""
    subject = f"[WatchGrid] {headline}{reminder}: {monitor.name if monitor else incident.title} — {project.name}"
    link = f"{settings.SITE_URL}/incidents/{incident.id}/"
    lines = [
        f"{incident.title}{reminder}",
        "",
        f"Project:        {project.name} ({project.environment})",
    ]
    if monitor:
        lines += [f"Monitor:        {monitor.name}", f"Target:         {monitor.url}"]
    lines += [
        f"Severity:       {incident.severity}",
        f"Status:         {incident.status}",
        f"Detected at:    {_fmt(incident.detected_at)}",
        f"Confirmed at:   {_fmt(incident.confirmed_at)}",
    ]
    if event_type == EventType.MONITOR_RECOVERED:
        lines += [
            f"Resolved at:    {_fmt(incident.resolved_at)}",
            f"Duration:       {_duration(incident.duration_seconds)}",
        ]
    else:
        lines += [f"Failure:        {incident.failure_type or '—'}", f"Last error:     {incident.summary or '—'}"]
    lines += ["", f"View incident: {link}"]
    text = "\n".join(lines)
    return {
        "subject": subject,
        "text": text,
        "webhook": {
            "text": f"{subject}\n{link}",
            "event": event_type,
            "escalation_level": escalation_level,
            "incident": {
                "id": str(incident.id),
                "title": incident.title,
                "status": incident.status,
                "severity": incident.severity,
                "failure_type": incident.failure_type,
                "summary": incident.summary,
                "detected_at": incident.detected_at.isoformat() if incident.detected_at else None,
                "confirmed_at": incident.confirmed_at.isoformat() if incident.confirmed_at else None,
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
                "url": link,
            },
            "monitor": (
                {"id": str(monitor.id), "name": monitor.name, "url": monitor.url, "status": monitor.status}
                if monitor
                else None
            ),
            "project": {"id": str(project.id), "name": project.name, "environment": project.environment},
        },
    }


def render_error_group(group, event_type: str) -> dict:
    project = group.project
    headline = _HEADLINES.get(event_type, event_type.upper())
    subject = f"[WatchGrid] {headline}: {group.error_type} in {project.name} ({group.environment})"
    link = f"{settings.SITE_URL}/errors/{group.id}/"
    text = "\n".join(
        [
            f"{group.error_type}: {group.normalized_message}",
            "",
            f"Project:       {project.name}",
            f"Environment:   {group.environment}",
            f"Endpoint:      {group.endpoint or '—'}",
            f"Occurrences:   {group.occurrence_count}",
            f"Affected users: {group.affected_users}",
            f"First seen:    {_fmt(group.first_seen)}",
            f"Last seen:     {_fmt(group.last_seen)}",
            "",
            f"View error: {link}",
        ]
    )
    return {
        "subject": subject,
        "text": text,
        "webhook": {
            "text": f"{subject}\n{link}",
            "event": event_type,
            "error_group": {
                "id": str(group.id),
                "error_type": group.error_type,
                "message": group.normalized_message,
                "environment": group.environment,
                "level": group.level,
                "occurrence_count": group.occurrence_count,
                "affected_users": group.affected_users,
                "url": link,
            },
            "project": {"id": str(project.id), "name": project.name},
        },
    }
