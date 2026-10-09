"""Scheduler and monitoring worker tasks (Sections 14.1-14.3).

Dispatch advances ``next_check_at`` in the same transaction that selects due monitors
(``SELECT … FOR UPDATE SKIP LOCKED`` on PostgreSQL), so concurrent scheduler ticks or
a second beat instance cannot dispatch the same scheduled execution twice. Each job
carries ``scheduled_for``; the worker derives a deterministic execution key from it,
and the ``(monitor, execution_key)`` unique constraint makes redelivery harmless.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.monitors.models import Monitor

from .models import MonitorCheck
from .probe import ProbeConfig, execute_probe
from .services import record_check

logger = logging.getLogger("watchgrid.monitoring")

SCHEDULER_TICK_KEY = "ops:scheduler:last_tick"
SCHEDULER_LAG_KEY = "ops:scheduler:last_lag_seconds"
SCHEDULER_DISPATCH_KEY = "ops:scheduler:last_dispatch_count"


def compute_next_check(scheduled_for: datetime, interval_seconds: int, now: datetime) -> datetime:
    """Keep cadence aligned; when far behind, restart from now with jitter (no herd)."""
    interval = timedelta(seconds=interval_seconds)
    candidate = scheduled_for + interval
    if candidate > now:
        return candidate
    jitter = random.uniform(0, min(interval_seconds * 0.2, 30))
    return now + interval + timedelta(seconds=jitter)


def scheduled_execution_key(scheduled_for: datetime) -> str:
    """Deterministic key for one scheduled execution (epoch milliseconds)."""
    return f"s:{int(scheduled_for.timestamp() * 1000)}"


@shared_task(name="apps.checks.tasks.dispatch_due_monitors", ignore_result=True)
def dispatch_due_monitors() -> int:
    now = timezone.now()
    batch_size = settings.MONITOR_DISPATCH_BATCH_SIZE
    dispatched = 0
    max_lag = 0.0

    for _ in range(settings.MONITOR_DISPATCH_MAX_BATCHES):
        with transaction.atomic():
            due = list(
                Monitor.objects.select_for_update(skip_locked=True)
                .due(now)
                .order_by("next_check_at")
                .only("id", "next_check_at", "interval_seconds")[:batch_size]
            )
            if not due:
                break
            jobs = []
            for monitor in due:
                scheduled_for = monitor.next_check_at
                max_lag = max(max_lag, (now - scheduled_for).total_seconds())
                monitor.next_check_at = compute_next_check(scheduled_for, monitor.interval_seconds, now)
                jobs.append((str(monitor.id), scheduled_for.isoformat(), monitor.interval_seconds))
            Monitor.objects.bulk_update(due, ["next_check_at"])
            transaction.on_commit(lambda jobs=jobs: _enqueue(jobs))
        dispatched += len(due)
        if len(due) < batch_size:
            break

    cache.set(SCHEDULER_TICK_KEY, now.isoformat(), timeout=None)
    cache.set(SCHEDULER_LAG_KEY, round(max_lag, 3), timeout=None)
    cache.set(SCHEDULER_DISPATCH_KEY, dispatched, timeout=None)
    if dispatched:
        logger.info("monitors_dispatched", extra={"count": dispatched, "max_lag_seconds": round(max_lag, 2)})
    return dispatched


def _enqueue(jobs: list[tuple[str, str, int]]) -> None:
    for monitor_id, scheduled_for, interval in jobs:
        # A check that waited longer than its own interval is stale; the next one is
        # already scheduled, so let the broker drop it rather than snowball a backlog.
        run_monitor_check.apply_async(args=[monitor_id, scheduled_for], expires=max(interval, 60))


def _acquire(key: str, ttl: int) -> bool:
    return cache.add(f"lock:check:{key}", "1", timeout=ttl)


def _release(key: str) -> None:
    cache.delete(f"lock:check:{key}")


@shared_task(name="apps.checks.tasks.run_monitor_check", ignore_result=True, acks_late=True)
def run_monitor_check(monitor_id: str, scheduled_for_iso: str) -> str:
    scheduled_for = parse_datetime(scheduled_for_iso)
    if scheduled_for is None:
        logger.error("invalid_scheduled_for", extra={"value": scheduled_for_iso})
        return "invalid"
    execution_key = scheduled_execution_key(scheduled_for)
    monitor = Monitor.objects.filter(pk=monitor_id).first()
    if monitor is None or not monitor.is_active or monitor.archived_at is not None:
        return "skipped"
    if MonitorCheck.objects.filter(monitor_id=monitor_id, execution_key=execution_key).exists():
        return "duplicate"

    lock_key = f"{monitor_id}:{execution_key}"
    if not _acquire(lock_key, ttl=monitor.timeout_seconds + 60):
        return "locked"
    try:
        result = execute_probe(ProbeConfig.from_monitor(monitor))
        check = record_check(
            monitor_id=monitor_id,
            execution_key=execution_key,
            result=result,
            kind=MonitorCheck.Kind.SCHEDULED,
            scheduled_for=scheduled_for,
        )
    finally:
        _release(lock_key)
    return "recorded" if check is not None else "duplicate"


@shared_task(name="apps.checks.tasks.run_manual_check", ignore_result=True, acks_late=True)
def run_manual_check(monitor_id: str, execution_key: str) -> str:
    monitor = Monitor.objects.filter(pk=monitor_id, archived_at__isnull=True).first()
    if monitor is None:
        return "skipped"
    if not _acquire(f"{monitor_id}:{execution_key}", ttl=monitor.timeout_seconds + 60):
        return "locked"
    try:
        result = execute_probe(ProbeConfig.from_monitor(monitor))
        check = record_check(
            monitor_id=monitor_id, execution_key=execution_key, result=result, kind=MonitorCheck.Kind.MANUAL
        )
    finally:
        _release(f"{monitor_id}:{execution_key}")
    return "recorded" if check is not None else "duplicate"


# --------------------------------------------------------------------------------------
# Watch-the-watcher heartbeats (Section 26)
#
# ``emit_queue_heartbeats`` (run by beat) sends a tiny task to every worker queue with
# its enqueue time. When a worker executes it, the end-to-end queue lag is recorded.
# A missing or stale heartbeat means no worker is consuming that queue.
# --------------------------------------------------------------------------------------


def heartbeat_key(queue: str) -> str:
    return f"ops:queue:{queue}:heartbeat"


@shared_task(name="apps.checks.tasks.emit_queue_heartbeats", ignore_result=True)
def emit_queue_heartbeats() -> None:
    sent_at = timezone.now().isoformat()
    for queue in settings.OPS_HEARTBEAT_QUEUES:
        queue_heartbeat.apply_async(args=[queue, sent_at], queue=queue, expires=600)


@shared_task(name="apps.checks.tasks.queue_heartbeat", ignore_result=True)
def queue_heartbeat(queue: str, sent_at: str) -> None:
    now = timezone.now()
    sent = parse_datetime(sent_at) or now
    cache.set(
        heartbeat_key(queue),
        {"received_at": now.isoformat(), "lag_seconds": round((now - sent).total_seconds(), 3)},
        timeout=None,
    )
