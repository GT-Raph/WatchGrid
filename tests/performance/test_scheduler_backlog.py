"""PERF-001: a large due backlog is dispatched in bounded batches (Section 27.2)."""

from unittest import mock

import pytest

from apps.checks import tasks
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


def test_perf_001_large_backlog_dispatched_in_bounded_batches(project, settings, run_on_commit):
    settings.MONITOR_DISPATCH_BATCH_SIZE = 10
    settings.MONITOR_DISPATCH_MAX_BATCHES = 3
    MonitorFactory.create_batch(45, project=project)
    with mock.patch.object(tasks, "run_monitor_check") as task, run_on_commit():
        first_tick = tasks.dispatch_due_monitors()
        second_tick = tasks.dispatch_due_monitors()
    assert first_tick == 30  # capped per tick: the scheduler stays responsive
    assert second_tick == 15
    assert task.apply_async.call_count == 45
    for call in task.apply_async.call_args_list:
        assert call.kwargs["expires"] >= 60  # stale checks are dropped, not piled up
