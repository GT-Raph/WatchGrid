"""Time-range parsing for analytics endpoints (FR-ANA-003)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.exceptions import ValidationFailed

PRESET_RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
MAX_CUSTOM_RANGE = timedelta(days=366)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    label: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def parse_time_range(params, default: str = "24h") -> TimeRange:
    now = timezone.now()
    label = params.get("range") or default
    if label == "custom" or (params.get("start") and params.get("end")):
        start = parse_datetime(params.get("start") or "")
        end = parse_datetime(params.get("end") or "")
        if start is None or end is None:
            raise ValidationFailed("Custom ranges require ISO-8601 'start' and 'end'.", details={"range": ["invalid"]})
        if timezone.is_naive(start):
            start = timezone.make_aware(start, dt_timezone.utc)
        if timezone.is_naive(end):
            end = timezone.make_aware(end, dt_timezone.utc)
        end = min(end, now)
        if start >= end:
            raise ValidationFailed("'start' must be before 'end'.", details={"range": ["invalid"]})
        if end - start > MAX_CUSTOM_RANGE:
            raise ValidationFailed("Custom ranges are limited to one year.", details={"range": ["too_long"]})
        return TimeRange(start, end, "custom")
    if label not in PRESET_RANGES:
        raise ValidationFailed(
            f"Unknown range '{label}'.", details={"range": [f"Choose one of {', '.join(PRESET_RANGES)} or custom."]}
        )
    return TimeRange(now - PRESET_RANGES[label], now, label)
