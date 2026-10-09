"""Notification workers — a separate queue so slow providers never delay probes."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from . import services

logger = logging.getLogger("watchgrid.notifications")


def _backoff(retries: int) -> int:
    return min(30 * (2**retries), 60 * 30)


@shared_task(name="apps.notifications.tasks.dispatch_incident_event", ignore_result=True, acks_late=True)
def dispatch_incident_event(incident_id: str, transition: str, level: int = 0) -> int:
    from apps.incidents.models import Incident

    incident = Incident.objects.select_related("project", "monitor").filter(pk=incident_id).first()
    if incident is None:
        return 0
    return len(services.create_incident_notifications(incident, transition, level))


@shared_task(name="apps.notifications.tasks.dispatch_error_event", ignore_result=True, acks_late=True)
def dispatch_error_event(group_id: str, event_type: str) -> int:
    from apps.errors.models import ErrorGroup

    group = ErrorGroup.objects.select_related("project").filter(pk=group_id).first()
    if group is None:
        return 0
    return len(services.create_error_notifications(group, event_type))


@shared_task(
    bind=True,
    name="apps.notifications.tasks.deliver_notification",
    ignore_result=True,
    acks_late=True,
    max_retries=None,  # bounded by NOTIFICATION_MAX_RETRIES via the ledger's attempt count
)
def deliver_notification(self, notification_id: str) -> str:
    outcome = services.attempt_delivery(notification_id)
    if outcome == "retry" and not self.request.is_eager:
        raise self.retry(countdown=_backoff(self.request.retries))
    return outcome


@shared_task(name="apps.notifications.tasks.send_incident_reminders", ignore_result=True)
def send_incident_reminders() -> int:
    return services.create_due_reminders()


@shared_task(
    bind=True,
    name="apps.notifications.tasks.send_email_message",
    ignore_result=True,
    acks_late=True,
    max_retries=5,
)
def send_email_message(self, to: list[str], subject: str, body: str) -> None:
    """Transactional account email (verification, password reset, invitations)."""
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    except OSError as exc:
        logger.warning("account_email_failed", extra={"error": exc.__class__.__name__})
        if self.request.is_eager:
            return
        raise self.retry(countdown=_backoff(self.request.retries)) from exc
