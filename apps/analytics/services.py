"""Reliability analytics (FR-ANA-001 … FR-ANA-003, US-006).

Uptime calculation method (documented per US-006): check-based. For a period,
``uptime % = available checks / counted checks * 100`` where *available* means the
target answered acceptably (``success`` or ``degraded``) and *counted* excludes manual
test checks and platform errors, which say nothing about the target. Incident-based
downtime (sum of availability-incident durations clipped to the period) is reported
alongside as ``downtime_seconds``.

Ranges up to seven days read raw checks (exact). Longer ranges read hourly rollups:
uptime, averages, minimum and maximum stay exact; p95 is approximated as the
check-weighted mean of hourly p95 values.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from django.db import connection
from django.db.models import Aggregate, Avg, Count, F, FloatField, Func, IntegerField, Max, Min, Q, Sum, Window
from django.db.models.functions import RowNumber, TruncDay, TruncHour

from apps.checks.models import MonitorCheck
from apps.incidents.models import Incident
from apps.monitors.models import Monitor
from common.constants import Severity
from common.utilities.time import TimeRange

from .models import MonitorHourlyStat

RAW_RANGE_LIMIT = timedelta(days=7)
RAW_POINTS_RANGE_LIMIT = timedelta(hours=24)
MAX_RAW_POINTS = 1500

AVAILABLE = (MonitorCheck.Outcome.SUCCESS, MonitorCheck.Outcome.DEGRADED)
COUNTED = (MonitorCheck.Outcome.SUCCESS, MonitorCheck.Outcome.DEGRADED, MonitorCheck.Outcome.FAILURE)


class PercentileCont(Aggregate):
    """PostgreSQL ``percentile_cont(p) WITHIN GROUP (ORDER BY expr)``."""

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression, percentile: float, **extra):
        super().__init__(expression, percentile=percentile, **extra)


class EpochBucket(Func):
    """Integer bucket index ``floor((epoch(expr) - start) / size)`` (PostgreSQL & SQLite)."""

    output_field = IntegerField()

    def __init__(self, expression, *, start_epoch: int, size: int):
        super().__init__(expression)
        self.start_epoch = int(start_epoch)
        self.size = int(size)

    def as_sql(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        return (
            f"CAST(FLOOR((EXTRACT(EPOCH FROM {sql}) - %s) / %s) AS INTEGER)",
            [*params, self.start_epoch, self.size],
        )

    def as_sqlite(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        return f"((CAST(strftime('%%s', {sql}) AS INTEGER) - %s) / %s)", [*params, self.start_epoch, self.size]


def _python_percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lower, upper = math.floor(k), math.ceil(k)
    if lower == upper:
        return float(ordered[int(k)])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower)


def latency_percentiles(queryset, percentiles=(0.5, 0.95, 0.99)) -> dict[str, int | None]:
    queryset = queryset.filter(outcome__in=AVAILABLE, response_time_ms__isnull=False)
    if connection.vendor == "postgresql":
        result = queryset.aggregate(
            **{f"p{int(p * 100)}": PercentileCont("response_time_ms", percentile=p) for p in percentiles}
        )
        return {k: (round(v) if v is not None else None) for k, v in result.items()}
    values = list(queryset.values_list("response_time_ms", flat=True)[:200_000])
    return {f"p{int(p * 100)}": _rounded(_python_percentile(values, p)) for p in percentiles}


def _rounded(value):
    return round(value) if value is not None else None


def _pct(up: int, total: int) -> float | None:
    return round(up * 100.0 / total, 3) if total else None


def _scheduled_checks(monitor_ids, start: datetime, end: datetime):
    return MonitorCheck.objects.filter(
        monitor_id__in=monitor_ids,
        kind=MonitorCheck.Kind.SCHEDULED,
        checked_at__gte=start,
        checked_at__lt=end,
        outcome__in=COUNTED,
    )


def downtime_seconds(monitor_ids, start: datetime, end: datetime) -> int:
    incidents = Incident.objects.filter(
        monitor_id__in=monitor_ids,
        incident_key=Incident.Key.AVAILABILITY,
        detected_at__lt=end,
    ).filter(Q(resolved_at__isnull=True) | Q(resolved_at__gt=start))
    total = 0.0
    for detected_at, resolved_at in incidents.values_list("detected_at", "resolved_at"):
        clipped_start = max(detected_at, start)
        clipped_end = min(resolved_at or end, end)
        total += max(0.0, (clipped_end - clipped_start).total_seconds())
    return int(total)


def monitor_statistics(monitor: Monitor, tr: TimeRange) -> dict:
    ids = [monitor.id]
    if tr.duration <= RAW_RANGE_LIMIT:
        qs = _scheduled_checks(ids, tr.start, tr.end)
        agg = qs.aggregate(
            total=Count("id"),
            up=Count("id", filter=Q(outcome__in=AVAILABLE)),
            degraded=Count("id", filter=Q(outcome=MonitorCheck.Outcome.DEGRADED)),
            failed=Count("id", filter=Q(outcome=MonitorCheck.Outcome.FAILURE)),
            avg=Avg("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            min=Min("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            max=Max("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
        )
        percentiles = latency_percentiles(qs)
        source = "raw"
    else:
        rollups = MonitorHourlyStat.objects.filter(monitor_id__in=ids, hour__gte=tr.start, hour__lt=tr.end)
        agg = rollups.aggregate(
            total=Sum("total_checks"),
            up=Sum("up_checks"),
            degraded=Sum("degraded_checks"),
            failed=Sum("failed_checks"),
            rt_sum=Sum("response_time_sum"),
            rt_count=Sum("response_time_count"),
            min=Min("response_time_min"),
            max=Max("response_time_max"),
        )
        weighted = rollups.filter(response_time_p95__isnull=False).aggregate(
            num=Sum(F("response_time_p95") * F("response_time_count"), output_field=IntegerField()),
            den=Sum("response_time_count"),
        )
        agg["avg"] = (agg["rt_sum"] / agg["rt_count"]) if agg.get("rt_count") else None
        percentiles = {
            "p50": None,
            "p95": round(weighted["num"] / weighted["den"]) if weighted["den"] else None,
            "p99": None,
        }
        source = "hourly_rollup"

    total = agg.get("total") or 0
    up = agg.get("up") or 0
    incidents = Incident.objects.filter(monitor=monitor, detected_at__gte=tr.start, detected_at__lt=tr.end)
    return {
        "range": {"label": tr.label, "start": tr.start, "end": tr.end},
        "source": source,
        "checks": {
            "total": total,
            "available": up,
            "degraded": agg.get("degraded") or 0,
            "failed": agg.get("failed") or 0,
        },
        "uptime_percent": _pct(up, total),
        "downtime_seconds": downtime_seconds(ids, tr.start, tr.end),
        "incident_count": incidents.count(),
        "latency_ms": {
            "avg": _rounded(agg.get("avg")),
            "min": agg.get("min"),
            "max": agg.get("max"),
            **percentiles,
        },
        "calculation": "check_based",
    }


def monitor_series(monitor: Monitor, tr: TimeRange) -> dict:
    ids = [monitor.id]
    if tr.duration <= RAW_POINTS_RANGE_LIMIT:
        rows = list(
            _scheduled_checks(ids, tr.start, tr.end)
            .order_by("checked_at")
            .values("checked_at", "response_time_ms", "outcome", "status_code")
        )
        stride = max(1, math.ceil(len(rows) / MAX_RAW_POINTS))
        points = [
            {
                "t": row["checked_at"],
                "response_time_ms": row["response_time_ms"],
                "outcome": row["outcome"],
                "status_code": row["status_code"],
            }
            for row in rows[::stride]
        ]
        return {"granularity": "check", "points": points}

    if tr.duration <= RAW_RANGE_LIMIT:
        buckets = (
            _scheduled_checks(ids, tr.start, tr.end)
            .annotate(bucket=TruncHour("checked_at"))
            .values("bucket")
            .annotate(
                total=Count("id"),
                up=Count("id", filter=Q(outcome__in=AVAILABLE)),
                failed=Count("id", filter=Q(outcome=MonitorCheck.Outcome.FAILURE)),
                avg=Avg("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            )
            .order_by("bucket")
        )
        granularity = "hour"
    else:
        trunc = TruncHour("hour") if tr.duration <= timedelta(days=31) else TruncDay("hour")
        buckets = (
            MonitorHourlyStat.objects.filter(monitor_id__in=ids, hour__gte=tr.start, hour__lt=tr.end)
            .annotate(bucket=trunc)
            .values("bucket")
            .annotate(
                total=Sum("total_checks"),
                up=Sum("up_checks"),
                failed=Sum("failed_checks"),
                rt_sum=Sum("response_time_sum"),
                rt_count=Sum("response_time_count"),
            )
            .order_by("bucket")
        )
        granularity = "hour" if tr.duration <= timedelta(days=31) else "day"

    points = []
    for row in buckets:
        avg = row.get("avg")
        if avg is None and row.get("rt_count"):
            avg = row["rt_sum"] / row["rt_count"]
        points.append(
            {
                "t": row["bucket"],
                "response_time_ms": _rounded(avg),
                "uptime_percent": _pct(row["up"] or 0, row["total"] or 0),
                "checks": row["total"] or 0,
                "failed": row["failed"] or 0,
            }
        )
    return {"granularity": granularity, "points": points}


def uptime_for_monitors(monitor_ids, tr: TimeRange) -> dict:
    """``{monitor_id: uptime_percent}`` in one grouped query."""
    if not monitor_ids:
        return {}
    if tr.duration <= RAW_RANGE_LIMIT:
        rows = (
            _scheduled_checks(monitor_ids, tr.start, tr.end)
            .values("monitor_id")
            .annotate(total=Count("id"), up=Count("id", filter=Q(outcome__in=AVAILABLE)))
        )
    else:
        rows = (
            MonitorHourlyStat.objects.filter(monitor_id__in=monitor_ids, hour__gte=tr.start, hour__lt=tr.end)
            .values("monitor_id")
            .annotate(total=Sum("total_checks"), up=Sum("up_checks"))
        )
    return {row["monitor_id"]: _pct(row["up"] or 0, row["total"] or 0) for row in rows}


def recent_outcomes(monitor_ids, limit: int = 30) -> dict:
    """Last ``limit`` scheduled outcomes per monitor (status bars), one windowed query."""
    if not monitor_ids:
        return {}
    ranked = (
        MonitorCheck.objects.filter(monitor_id__in=monitor_ids, kind=MonitorCheck.Kind.SCHEDULED)
        .annotate(rank=Window(RowNumber(), partition_by=[F("monitor_id")], order_by=F("checked_at").desc()))
        .filter(rank__lte=limit)
        .values("monitor_id", "checked_at", "outcome", "response_time_ms")
    )
    result: dict = {}
    for row in ranked:
        result.setdefault(row["monitor_id"], []).append(
            {"t": row["checked_at"], "outcome": row["outcome"], "response_time_ms": row["response_time_ms"]}
        )
    for rows in result.values():
        rows.sort(key=lambda r: r["t"])
    return result


def dashboard_summary(*, user, tr: TimeRange, organization_id=None, project_id=None) -> dict:
    from apps.errors.models import ErrorGroup

    monitors = Monitor.objects.for_user(user).not_archived().filter(project__status="active")
    incidents = Incident.objects.for_user(user)
    errors = ErrorGroup.objects.for_user(user).filter(project__status="active")
    if organization_id:
        monitors = monitors.filter(project__organization_id=organization_id)
        incidents = incidents.filter(project__organization_id=organization_id)
        errors = errors.filter(project__organization_id=organization_id)
    if project_id:
        monitors = monitors.filter(project_id=project_id)
        incidents = incidents.filter(project_id=project_id)
        errors = errors.filter(project_id=project_id)

    counts = dict.fromkeys(Monitor.Status.values, 0)
    for row in monitors.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    monitor_ids = list(monitors.values_list("id", flat=True))

    if tr.duration <= RAW_RANGE_LIMIT:
        agg = _scheduled_checks(monitor_ids, tr.start, tr.end).aggregate(
            total=Count("id"),
            up=Count("id", filter=Q(outcome__in=AVAILABLE)),
            avg=Avg("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
        )
    else:
        agg = MonitorHourlyStat.objects.filter(
            monitor_id__in=monitor_ids, hour__gte=tr.start, hour__lt=tr.end
        ).aggregate(
            total=Sum("total_checks"),
            up=Sum("up_checks"),
            rt_sum=Sum("response_time_sum"),
            rt_count=Sum("response_time_count"),
        )
        agg["avg"] = agg["rt_sum"] / agg["rt_count"] if agg.get("rt_count") else None

    active_incidents = incidents.active()
    by_severity = dict.fromkeys(Severity.values, 0)
    for row in active_incidents.values("severity").annotate(n=Count("id")):
        by_severity[row["severity"]] = row["n"]

    recent = (
        incidents.filter(Q(detected_at__gte=tr.start) | ~Q(status=Incident.Status.RESOLVED))
        .select_related("monitor", "project")
        .order_by("-detected_at")[:10]
    )
    return {
        "range": {"label": tr.label, "start": tr.start, "end": tr.end},
        "monitors": {
            "total": len(monitor_ids),
            "by_status": counts,
            "operational": counts["operational"],
            "degraded": counts["degraded"],
            "down": counts["down"],
            "paused": counts["paused"],
        },
        "incidents": {"active": active_incidents.count(), "active_by_severity": by_severity},
        "uptime_percent": _pct(agg.get("up") or 0, agg.get("total") or 0),
        "avg_response_time_ms": _rounded(agg.get("avg")),
        "checks_in_range": agg.get("total") or 0,
        "errors": {
            "unresolved_groups": errors.filter(status=ErrorGroup.Status.UNRESOLVED).count(),
            "new_groups_in_range": errors.filter(first_seen__gte=tr.start).count(),
            "events_in_range": errors.filter(last_seen__gte=tr.start).aggregate(n=Sum("occurrence_count"))["n"] or 0,
        },
        "recent_incidents": [
            {
                "id": str(i.id),
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
                "detected_at": i.detected_at,
                "resolved_at": i.resolved_at,
                "monitor": {"id": str(i.monitor_id), "name": i.monitor.name} if i.monitor else None,
                "project": {"id": str(i.project_id), "name": i.project.name},
            }
            for i in recent
        ],
    }


# --------------------------------------------------------------------------------------
# Rollups and retention
# --------------------------------------------------------------------------------------


def rollup_hours(start: datetime, end: datetime) -> int:
    """Recompute hourly rollups for ``[start, end)`` idempotently."""
    start = start.replace(minute=0, second=0, microsecond=0)
    rows = (
        MonitorCheck.objects.filter(
            kind=MonitorCheck.Kind.SCHEDULED, checked_at__gte=start, checked_at__lt=end, outcome__in=COUNTED
        )
        .annotate(bucket=TruncHour("checked_at"))
        .values("monitor_id", "bucket")
        .annotate(
            total=Count("id"),
            up=Count("id", filter=Q(outcome__in=AVAILABLE)),
            degraded=Count("id", filter=Q(outcome=MonitorCheck.Outcome.DEGRADED)),
            failed=Count("id", filter=Q(outcome=MonitorCheck.Outcome.FAILURE)),
            rt_sum=Sum("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            rt_count=Count("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            rt_min=Min("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            rt_max=Max("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
        )
    )
    written = 0
    for row in rows:
        hour = row["bucket"]
        bucket_qs = MonitorCheck.objects.filter(
            monitor_id=row["monitor_id"],
            kind=MonitorCheck.Kind.SCHEDULED,
            checked_at__gte=hour,
            checked_at__lt=hour + timedelta(hours=1),
        )
        p95 = latency_percentiles(bucket_qs, percentiles=(0.95,))["p95"]
        MonitorHourlyStat.objects.update_or_create(
            monitor_id=row["monitor_id"],
            hour=hour,
            defaults={
                "total_checks": row["total"],
                "up_checks": row["up"],
                "degraded_checks": row["degraded"],
                "failed_checks": row["failed"],
                "response_time_sum": row["rt_sum"] or 0,
                "response_time_count": row["rt_count"] or 0,
                "response_time_min": row["rt_min"],
                "response_time_max": row["rt_max"],
                "response_time_p95": p95,
            },
        )
        written += 1
    return written


def delete_older_than(model, field: str, cutoff: datetime, batch_size: int) -> int:
    """Delete in primary-key batches to keep locks and WAL bursts small."""
    deleted = 0
    while True:
        ids = list(model.objects.filter(**{f"{field}__lt": cutoff}).values_list("pk", flat=True)[:batch_size])
        if not ids:
            return deleted
        count, _ = model.objects.filter(pk__in=ids).delete()
        deleted += count


STATUS_PRIORITY = {
    Monitor.Status.DOWN: 0,
    Monitor.Status.PENDING_FAILURE: 1,
    Monitor.Status.RECOVERING: 2,
    Monitor.Status.DEGRADED: 3,
    Monitor.Status.UNKNOWN: 4,
    Monitor.Status.OPERATIONAL: 5,
    Monitor.Status.PAUSED: 6,
}
GRID_MAX_MONITORS = 200


def status_grid(*, user, tr: TimeRange, organization_id=None, project_id=None, buckets: int = 60) -> dict:
    """Time-aligned outcome grid: one row per monitor, ``buckets`` equal time cells.

    Cells are aligned by wall-clock time (not by check index) so correlated outages
    across services line up vertically. Each cell is ``[total, failed, degraded]``.
    """
    buckets = max(12, min(buckets, 120))
    monitors = Monitor.objects.for_user(user).not_archived().filter(project__status="active")
    if organization_id:
        monitors = monitors.filter(project__organization_id=organization_id)
    if project_id:
        monitors = monitors.filter(project_id=project_id)
    total_monitors = monitors.count()
    rows = sorted(
        monitors.select_related("project"),
        key=lambda m: (STATUS_PRIORITY.get(m.status, 9), m.name.lower()),
    )[:GRID_MAX_MONITORS]
    ids = [m.id for m in rows]

    duration = max(int(tr.duration.total_seconds()), 60)
    size = max(60, math.ceil(duration / buckets))
    start_epoch = int(tr.start.timestamp())
    if tr.duration <= RAW_RANGE_LIMIT:
        cells_qs = (
            _scheduled_checks(ids, tr.start, tr.end)
            .annotate(bucket=EpochBucket("checked_at", start_epoch=start_epoch, size=size))
            .values("monitor_id", "bucket")
            .annotate(
                total=Count("id"),
                failed=Count("id", filter=Q(outcome=MonitorCheck.Outcome.FAILURE)),
                degraded=Count("id", filter=Q(outcome=MonitorCheck.Outcome.DEGRADED)),
                rt_sum=Sum("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
                rt_count=Count("response_time_ms", filter=Q(outcome__in=AVAILABLE)),
            )
        )
    else:
        cells_qs = (
            MonitorHourlyStat.objects.filter(monitor_id__in=ids, hour__gte=tr.start, hour__lt=tr.end)
            .annotate(bucket=EpochBucket("hour", start_epoch=start_epoch, size=size))
            .values("monitor_id", "bucket")
            .annotate(
                total=Sum("total_checks"),
                failed=Sum("failed_checks"),
                degraded=Sum("degraded_checks"),
                rt_sum=Sum("response_time_sum"),
                rt_count=Sum("response_time_count"),
            )
        )

    grid: dict = {mid: [None] * buckets for mid in ids}
    totals: dict = {mid: [0, 0, 0, 0] for mid in ids}  # total, failed, rt_sum, rt_count
    for row in cells_qs:
        index = int(row["bucket"])
        if not 0 <= index < buckets:
            continue
        total, failed, degraded = row["total"] or 0, row["failed"] or 0, row["degraded"] or 0
        grid[row["monitor_id"]][index] = [total, failed, degraded]
        acc = totals[row["monitor_id"]]
        acc[0] += total
        acc[1] += failed
        acc[2] += row["rt_sum"] or 0
        acc[3] += row["rt_count"] or 0

    def row_payload(m):
        total, failed, rt_sum, rt_count = totals[m.id]
        return {
            "id": str(m.id),
            "name": m.name,
            "url": m.url,
            "status": m.status,
            "project": {"id": str(m.project_id), "name": m.project.name},
            "last_checked_at": m.last_checked_at,
            "last_response_time_ms": m.last_response_time_ms,
            "uptime_percent": _pct(total - failed, total),
            "avg_response_time_ms": _rounded(rt_sum / rt_count) if rt_count else None,
            "cells": grid[m.id],
        }

    return {
        "range": {"label": tr.label, "start": tr.start, "end": tr.end},
        "bucket_seconds": size,
        "buckets": buckets,
        "total_monitors": total_monitors,
        "truncated": total_monitors > len(rows),
        "monitors": [row_payload(m) for m in rows],
    }
