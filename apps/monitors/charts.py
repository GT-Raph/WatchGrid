"""Server-side geometry for the response-time chart (rendered as inline SVG).

Points are placed by time, not by index, so missing data and outages show up as real
gaps. The plot SVG uses a fixed viewBox stretched to the container; strokes use
``vector-effect: non-scaling-stroke`` so lines stay 2px at any width. Labels are drawn
in unscaled SVG or flex HTML, so the page needs no inline styles (strict CSP).
"""

from __future__ import annotations

import math
from datetime import datetime
from itertools import pairwise

from django.utils import timezone
from django.utils.dateformat import format as date_format

WIDTH = 1000
HEIGHT = 200


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 100
    exponent = 10 ** math.floor(math.log10(value))
    fraction = value / exponent
    for step in (1, 2, 2.5, 5, 10):
        if fraction <= step:
            return step * exponent
    return 10 * exponent


def _ms_label(value: float) -> str:
    return f"{value / 1000:g} s" if value >= 1000 else f"{int(value)} ms"


def latency_chart(series: dict, start: datetime, end: datetime, threshold_ms: int | None = None) -> dict:
    points = series["points"]
    span = max((end - start).total_seconds(), 1)
    by_check = series["granularity"] == "check"

    def x_of(moment: datetime) -> float:
        return round((moment - start).total_seconds() / span * WIDTH, 2)

    values = [p["response_time_ms"] for p in points if p["response_time_ms"] is not None and _available(p, by_check)]
    ceiling = _nice_ceiling(max([*values, threshold_ms or 0, 1]) * 1.08)

    def y_of(value: float) -> float:
        return round(HEIGHT - value / ceiling * HEIGHT, 2)

    # Typical spacing between samples; a gap three times wider breaks the line.
    times = [p["t"] for p in points]
    steps = sorted((b - a).total_seconds() for a, b in pairwise(times))
    typical = steps[len(steps) // 2] if steps else 0

    segments: list[list[str]] = []
    current: list[str] = []
    failures: list[float] = []
    readings = []
    previous = None
    for point in points:
        x = x_of(point["t"])
        value = point["response_time_ms"]
        available = _available(point, by_check)
        failed = (point.get("outcome") == "failure") if by_check else bool(point.get("failed"))
        if failed:
            failures.append(x)
        gap = previous is not None and typical and (point["t"] - previous).total_seconds() > typical * 3
        if value is None or not available or gap:
            if current:
                segments.append(current)
            current = []
        if value is not None and available:
            current.append(f"{x},{y_of(value)}")
        previous = point["t"]
        readings.append(
            {
                "x": round(x / WIDTH * 100, 3),
                "y": round(y_of(value) / HEIGHT * 100, 3) if value is not None and available else None,
                "t": date_format(timezone.localtime(point["t"]), "M j, H:i:s" if by_check else "M j, H:i"),
                "value": value if available else None,
                "failed": failed,
                "code": point.get("status_code"),
                "checks": point.get("checks"),
                "failures": point.get("failed"),
            }
        )
    if current:
        segments.append(current)

    y_ticks = [{"y": round(HEIGHT - f * HEIGHT, 2), "label": _ms_label(ceiling * f)} for f in (1, 0.75, 0.5, 0.25, 0)]
    tick_format = "M j" if span > 3 * 86400 else ("M j, H:i" if span > 86400 else "H:i")
    x_ticks = []
    for i in range(5):
        moment = start + (end - start) * (i / 4)
        x_ticks.append({"label": date_format(timezone.localtime(moment), tick_format)})

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "lines": [" ".join(segment) for segment in segments if len(segment) > 1],
        "dots": [segment[0].split(",") for segment in segments if len(segment) == 1],
        "areas": [
            f"M{segment[0]} L{' L'.join(segment[1:])} L{segment[-1].split(',')[0]},{HEIGHT} "
            f"L{segment[0].split(',')[0]},{HEIGHT} Z"
            for segment in segments
            if len(segment) > 1
        ],
        "failures": failures,
        "threshold": _threshold(threshold_ms, y_of),
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "readings": readings,
        "has_data": bool(values),
    }


def _threshold(threshold_ms: int | None, y_of) -> dict | None:
    """Horizontal "slow above" line for monitors with a response-time threshold."""
    if not threshold_ms:
        return None
    y = y_of(threshold_ms)
    return {"y": y, "label": _ms_label(threshold_ms)}


def _available(point: dict, by_check: bool) -> bool:
    return point.get("outcome") != "failure" if by_check else True
