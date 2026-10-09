"""Response-time chart geometry (apps/monitors/charts.py): pure functions, no database."""

from datetime import datetime, timedelta, timezone

from apps.monitors.charts import HEIGHT, WIDTH, latency_chart

START = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def point(minute, ms, outcome="success", code=200):
    return {"t": START + timedelta(minutes=minute), "response_time_ms": ms, "outcome": outcome, "status_code": code}


def test_points_are_placed_by_time_and_failures_break_the_line():
    series = {
        "granularity": "check",
        "points": [point(0, 100), point(1, 200), point(2, None, "failure", None), point(3, 150), point(4, 120)],
    }
    chart = latency_chart(series, START, END)
    assert len(chart["lines"]) == 2  # the failed check splits the series
    first_x = float(chart["lines"][0].split()[0].split(",")[0])
    assert first_x == 0
    assert chart["failures"] == [WIDTH * 2 / 10]
    assert chart["readings"][2]["failed"] is True and chart["readings"][2]["value"] is None
    assert chart["has_data"]


def test_large_time_gap_breaks_the_line():
    series = {"granularity": "check", "points": [point(0, 100), point(1, 110), point(8, 120), point(9, 130)]}
    assert len(latency_chart(series, START, END)["lines"]) == 2


def test_isolated_sample_is_drawn_as_a_dot():
    series = {"granularity": "check", "points": [point(0, 100), point(1, None, "failure"), point(2, 90)]}
    chart = latency_chart(series, START, END)
    assert chart["lines"] == []
    assert len(chart["dots"]) == 2


def test_threshold_and_axis_ceiling():
    chart = latency_chart({"granularity": "check", "points": [point(0, 300)]}, START, END, threshold_ms=900)
    assert chart["threshold"]["label"] == "900 ms"
    assert 0 < chart["threshold"]["y"] < HEIGHT
    assert chart["y_ticks"][0]["label"] == "1 s"  # ceiling rounds up past the threshold
    assert len(chart["x_ticks"]) == 5


def test_hourly_buckets_mark_failures_from_counts():
    series = {
        "granularity": "hour",
        "points": [
            {"t": START, "response_time_ms": 120, "checks": 60, "failed": 0},
            {"t": START + timedelta(minutes=5), "response_time_ms": 140, "checks": 60, "failed": 3},
        ],
    }
    chart = latency_chart(series, START, END)
    assert chart["failures"] == [WIDTH / 2]
    assert chart["readings"][1]["failures"] == 3


def test_empty_series():
    chart = latency_chart({"granularity": "check", "points": []}, START, END)
    assert chart["has_data"] is False and chart["lines"] == []
