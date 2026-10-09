"""Scheduler cadence helpers (Section 14.2): pure functions, no database."""

from datetime import timedelta

from django.utils import timezone

from apps.checks import tasks


def test_scheduler_far_behind_reschedules_from_now_with_jitter():
    now = timezone.now()
    nxt = tasks.compute_next_check(now - timedelta(hours=2), 60, now)
    assert now + timedelta(seconds=60) <= nxt <= now + timedelta(seconds=72)


def test_execution_key_is_deterministic():
    moment = timezone.now()
    assert tasks.scheduled_execution_key(moment) == tasks.scheduled_execution_key(moment)
