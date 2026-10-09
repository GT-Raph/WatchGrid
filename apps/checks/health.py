"""Platform health (Section 26): liveness, readiness and the operator status data.

The monitoring platform must itself be monitored independently, so these checks look
at the scheduler tick, per-queue heartbeats and the data stores rather than at any
customer target."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .tasks import SCHEDULER_DISPATCH_KEY, SCHEDULER_LAG_KEY, SCHEDULER_TICK_KEY, heartbeat_key


def check_database() -> dict:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"ok": True}
    except Exception as exc:  # pragma: no cover - exercised only when the DB is down
        return {"ok": False, "error": exc.__class__.__name__}


def check_cache() -> dict:
    try:
        cache.set("ops:ping", "1", timeout=10)
        return {"ok": cache.get("ops:ping") == "1"}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": exc.__class__.__name__}


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    parsed = parse_datetime(iso)
    return round((timezone.now() - parsed).total_seconds(), 1) if parsed else None


def scheduler_status() -> dict:

    age = _age_seconds(cache.get(SCHEDULER_TICK_KEY))
    lag = cache.get(SCHEDULER_LAG_KEY)
    ok = age is not None and age <= settings.OPS_SCHEDULER_MAX_LAG_SECONDS
    return {
        "ok": ok,
        "last_tick_age_seconds": age,
        "last_dispatch_lag_seconds": lag,
        "last_dispatch_count": cache.get(SCHEDULER_DISPATCH_KEY),
    }


def queue_status() -> dict:
    queues = {}
    for queue in settings.OPS_HEARTBEAT_QUEUES:
        beat = cache.get(heartbeat_key(queue)) or {}
        age = _age_seconds(beat.get("received_at"))
        queues[queue] = {
            "ok": age is not None and age <= settings.OPS_QUEUE_MAX_LAG_SECONDS,
            "last_heartbeat_age_seconds": age,
            "lag_seconds": beat.get("lag_seconds"),
            "depth": _broker_depth(queue),
        }
    return queues


def _broker_depth(queue: str) -> int | None:
    broker = settings.CELERY_BROKER_URL or ""
    if not broker.startswith(("redis://", "rediss://")):
        return None
    try:
        import redis

        client = redis.Redis.from_url(broker, socket_timeout=1, socket_connect_timeout=1)
        return int(client.llen(queue))
    except Exception:
        return None


def monitoring_quality() -> dict:
    from apps.checks.models import MonitorCheck
    from apps.monitors.models import Monitor
    from apps.notifications.models import Notification

    now = timezone.now()
    window = now - timedelta(minutes=15)
    overdue = Monitor.objects.filter(
        is_active=True, archived_at__isnull=True, next_check_at__lt=now - timedelta(minutes=2)
    ).count()
    checks = MonitorCheck.objects.filter(checked_at__gte=window).aggregate(
        total=Count("id"), platform_errors=Count("id", filter=Q(outcome=MonitorCheck.Outcome.PLATFORM_ERROR))
    )
    notifications = Notification.objects.filter(created_at__gte=now - timedelta(hours=1)).aggregate(
        total=Count("id"),
        failed=Count("id", filter=Q(status=Notification.Status.FAILED)),
        pending=Count("id", filter=Q(status__in=[Notification.Status.PENDING, Notification.Status.SENDING])),
    )
    return {
        "active_monitors": Monitor.objects.filter(is_active=True, archived_at__isnull=True).count(),
        "overdue_monitors": overdue,
        "checks_last_15m": checks["total"],
        "platform_errors_last_15m": checks["platform_errors"],
        "notifications_last_hour": notifications,
    }


def platform_status() -> dict:
    database = check_database()
    cache_state = check_cache()
    scheduler = scheduler_status()
    queues = queue_status()
    quality = monitoring_quality() if database["ok"] else {}
    healthy = database["ok"] and cache_state["ok"] and scheduler["ok"] and all(q["ok"] for q in queues.values())
    return {
        "status": "ok" if healthy else "degraded",
        "checked_at": timezone.now(),
        "components": {"database": database, "cache": cache_state, "scheduler": scheduler, "queues": queues},
        "monitoring": quality,
    }


@never_cache
@require_GET
def live(request):
    """Liveness: the process answers. No dependencies are touched."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request):
    """Readiness: database and cache reachable (load balancer health check)."""
    database = check_database()
    cache_state = check_cache()
    ok = database["ok"] and cache_state["ok"]
    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "database": database, "cache": cache_state},
        status=200 if ok else 503,
    )
