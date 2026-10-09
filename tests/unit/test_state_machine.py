"""Unit tests for the monitor state machine (Section 14.7, Appendix C)."""

from datetime import datetime, timedelta, timezone

import pytest

from apps.monitors.state_machine import (
    AVAILABILITY,
    DEGRADED,
    DOWN,
    OPERATIONAL,
    OUTCOME_DEGRADED,
    OUTCOME_FAILURE,
    OUTCOME_PLATFORM_ERROR,
    OUTCOME_SUCCESS,
    PAUSED,
    PENDING_FAILURE,
    PERFORMANCE,
    RECOVERING,
    UNKNOWN,
    MonitorState,
    Thresholds,
    evaluate,
)

T0 = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)
DEFAULT = Thresholds(failure_threshold=3, recovery_threshold=2)


def run(outcomes, state=None, thresholds=DEFAULT):
    state = state or MonitorState(status=UNKNOWN)
    transitions = []
    for i, outcome in enumerate(outcomes):
        transition = evaluate(state, outcome, thresholds, T0 + timedelta(minutes=i))
        transitions.append(transition)
        state = transition.state
    return state, transitions


def test_unknown_to_operational_on_healthy_check():
    state, (t,) = run([OUTCOME_SUCCESS])
    assert state.status == OPERATIONAL
    assert t.status_changed


def test_single_failure_is_pending_not_down():
    state, _ = run([OUTCOME_SUCCESS, OUTCOME_FAILURE])
    assert state.status == PENDING_FAILURE
    assert state.consecutive_failures == 1


def test_down_only_after_failure_threshold_and_opens_one_incident():
    state, transitions = run([OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_FAILURE, OUTCOME_FAILURE, OUTCOME_FAILURE])
    assert [t.state.status for t in transitions] == [OPERATIONAL, PENDING_FAILURE, PENDING_FAILURE, DOWN, DOWN]
    opened = [t for t in transitions if t.open_incident]
    assert len(opened) == 1 and opened[0].open_incident == AVAILABILITY
    assert transitions[-1].update_incident == AVAILABILITY
    assert state.failing_since == T0 + timedelta(minutes=1)


def test_threshold_of_one_declares_down_immediately():
    state, (t,) = run([OUTCOME_FAILURE], thresholds=Thresholds(1, 1))
    assert state.status == DOWN
    assert t.open_incident == AVAILABILITY


def test_transient_failure_resets_counter():
    state, _ = run([OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_SUCCESS])
    assert state.status == OPERATIONAL
    assert state.consecutive_failures == 0
    assert state.failing_since is None


def test_recovery_requires_consecutive_successes():
    down = MonitorState(status=DOWN, consecutive_failures=3, failing_since=T0)
    state, transitions = run([OUTCOME_SUCCESS, OUTCOME_SUCCESS], state=down)
    assert [t.state.status for t in transitions] == [RECOVERING, OPERATIONAL]
    assert transitions[-1].resolve_incidents == (AVAILABILITY, PERFORMANCE)
    assert state.failing_since is None


def test_failed_recovery_attempt_returns_to_down():
    down = MonitorState(status=DOWN, consecutive_failures=3, failing_since=T0)
    state, transitions = run([OUTCOME_SUCCESS, OUTCOME_FAILURE], state=down)
    assert [t.state.status for t in transitions] == [RECOVERING, DOWN]
    assert state.consecutive_successes == 0
    assert transitions[-1].open_incident is None  # same outage, incident updated
    assert transitions[-1].update_incident == AVAILABILITY


def test_soft_failures_degrade_after_threshold_and_recover():
    _, transitions = run([OUTCOME_SUCCESS] + [OUTCOME_DEGRADED] * 3 + [OUTCOME_SUCCESS] * 2)
    statuses = [t.state.status for t in transitions]
    assert statuses == [OPERATIONAL, OPERATIONAL, OPERATIONAL, DEGRADED, DEGRADED, OPERATIONAL]
    assert transitions[3].open_incident == PERFORMANCE
    assert transitions[-1].resolve_incidents == (PERFORMANCE,)


def test_hard_failure_from_degraded_goes_pending_then_down_superseding_performance():
    degraded = MonitorState(status=DEGRADED, consecutive_soft_failures=3, degraded_since=T0)
    _, transitions = run([OUTCOME_FAILURE] * 3, state=degraded)
    assert [t.state.status for t in transitions] == [PENDING_FAILURE, PENDING_FAILURE, DOWN]
    assert transitions[-1].open_incident == AVAILABILITY
    assert transitions[-1].resolve_incidents == (PERFORMANCE,)


def test_slow_responses_while_down_count_toward_recovery():
    down = MonitorState(status=DOWN, consecutive_failures=3, failing_since=T0)
    _, transitions = run([OUTCOME_DEGRADED, OUTCOME_SUCCESS], state=down)
    assert [t.state.status for t in transitions] == [RECOVERING, OPERATIONAL]
    assert AVAILABILITY in transitions[-1].resolve_incidents


@pytest.mark.parametrize("status", [UNKNOWN, OPERATIONAL, DOWN, RECOVERING, DEGRADED])
def test_platform_errors_never_change_state(status):
    state = MonitorState(status=status, consecutive_failures=2)
    t = evaluate(state, OUTCOME_PLATFORM_ERROR, DEFAULT, T0)
    assert t.state == state
    assert not t.open_incident and not t.resolve_incidents


def test_paused_monitor_ignores_results():
    state = MonitorState(status=PAUSED)
    assert evaluate(state, OUTCOME_FAILURE, DEFAULT, T0).state == state


def test_resume_into_operational_resolves_orphaned_incidents():
    # After pause/resume the monitor is UNKNOWN; a healthy check closes leftovers.
    t = evaluate(MonitorState(status=UNKNOWN), OUTCOME_SUCCESS, DEFAULT, T0)
    assert t.resolve_incidents == (AVAILABILITY, PERFORMANCE)


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError):
        evaluate(MonitorState(status=UNKNOWN), "bogus", DEFAULT, T0)
