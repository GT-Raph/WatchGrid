"""Analytics and data-lifecycle tasks (low-priority ``analytics`` queue)."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Min
from django.utils import timezone

from . import services

logger = logging.getLogger("watchgrid.analytics")

ROLLUP_LOOKBACK = timedelta(hours=3)


@shared_task(name="apps.analytics.tasks.rollup_monitor_stats", ignore_result=True)
def rollup_monitor_stats(hours: int | None = None) -> int:
    end = timezone.now()
    start = end - (timedelta(hours=hours) if hours else ROLLUP_LOOKBACK)
    return services.rollup_hours(start, end)


@shared_task(name="apps.analytics.tasks.enforce_retention", ignore_result=True)
def enforce_retention() -> dict:
    """Apply the retention baseline (Section 24.3, NFR-DATA-002)."""
    from apps.audit.models import AuditLog
    from apps.checks.models import MonitorCheck
    from apps.errors.models import ErrorEvent
    from apps.notifications.models import Notification

    now = timezone.now()
    batch = settings.RETENTION_DELETE_BATCH_SIZE
    # Align to a whole hour so no hourly rollup is ever recomputed from a partial hour.
    check_cutoff = (now - timedelta(days=settings.RETENTION_CHECK_DAYS)).replace(minute=0, second=0, microsecond=0)
    oldest = MonitorCheck.objects.filter(checked_at__lt=check_cutoff).aggregate(oldest=Min("checked_at"))["oldest"]
    if oldest is not None:
        # Summarise every hour that is about to lose its raw data, even if the regular
        # rollup task missed it (e.g. the analytics worker was down).
        services.rollup_hours(oldest, check_cutoff)
    result = {
        "checks": services.delete_older_than(MonitorCheck, "checked_at", check_cutoff, batch),
        "error_events": services.delete_older_than(
            ErrorEvent, "received_at", now - timedelta(days=settings.RETENTION_ERROR_EVENT_DAYS), batch
        ),
        "notifications": services.delete_older_than(
            Notification, "created_at", now - timedelta(days=settings.RETENTION_NOTIFICATION_DAYS), batch
        ),
        "audit_logs": services.delete_older_than(
            AuditLog, "created_at", now - timedelta(days=settings.RETENTION_AUDIT_LOG_DAYS), batch
        ),
    }
    logger.info("retention_enforced", extra={"deleted": result})
    return result
