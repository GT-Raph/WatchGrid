"""Monitor status state machine (Section 14.7, Appendix C).

Pure functions only — no database access — so every transition is unit-testable.

Check outcomes:

* ``SUCCESS``   — healthy and within thresholds.
* ``DEGRADED``  — reachable but a soft rule was breached (response time).
* ``FAILURE``   — hard failure (DNS, connection, timeout, TLS, HTTP status, content,
  redirect, blocked destination).
* ``PLATFORM_ERROR`` — the platform itself failed; never blamed on the target and
  never changes monitor state (principle 05 "fail safely").

Incident keys: ``availability`` (monitor DOWN) and ``performance`` (DEGRADED). Only one
active incident per monitor and key may exist (BR-003).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

UNKNOWN = "unknown"
OPERATIONAL = "operational"
PENDING_FAILURE = "pending_failure"
DEGRADED = "degraded"
DOWN = "down"
RECOVERING = "recovering"
PAUSED = "paused"

OUTCOME_SUCCESS = "success"
OUTCOME_DEGRADED = "degraded"
OUTCOME_FAILURE = "failure"
OUTCOME_PLATFORM_ERROR = "platform_error"

AVAILABILITY = "availability"
PERFORMANCE = "performance"


@dataclass(frozen=True)
class MonitorState:
    status: str
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    consecutive_soft_failures: int = 0
    failing_since: datetime | None = None
    degraded_since: datetime | None = None


@dataclass(frozen=True)
class Thresholds:
    failure_threshold: int
    recovery_threshold: int


@dataclass(frozen=True)
class Transition:
    previous: MonitorState
    state: MonitorState
    open_incident: str | None = None  # incident key to open (or update if already active)
    update_incident: str | None = None  # incident key whose evidence is refreshed
    resolve_incidents: tuple[str, ...] = ()

    @property
    def status_changed(self) -> bool:
        return self.previous.status != self.state.status


def evaluate(state: MonitorState, outcome: str, thresholds: Thresholds, checked_at: datetime) -> Transition:
    """Compute the next monitor state for one scheduled check outcome."""
    if state.status == PAUSED or outcome == OUTCOME_PLATFORM_ERROR:
        return Transition(previous=state, state=state)
    if outcome == OUTCOME_FAILURE:
        return _on_failure(state, thresholds, checked_at)
    if outcome == OUTCOME_DEGRADED:
        return _on_degraded(state, thresholds, checked_at)
    if outcome == OUTCOME_SUCCESS:
        return _on_success(state, thresholds)
    raise ValueError(f"Unknown check outcome: {outcome}")


def _on_failure(state: MonitorState, t: Thresholds, checked_at: datetime) -> Transition:
    failing_since = state.failing_since or checked_at
    base = replace(
        state,
        consecutive_failures=state.consecutive_failures + 1,
        consecutive_successes=0,
        consecutive_soft_failures=0,
        failing_since=failing_since,
    )
    if state.status in (DOWN, RECOVERING):
        # Still down, or a failed recovery attempt returns to DOWN.
        return Transition(state, replace(base, status=DOWN), update_incident=AVAILABILITY)
    if base.consecutive_failures >= t.failure_threshold:
        return Transition(
            state,
            replace(base, status=DOWN, degraded_since=None),
            open_incident=AVAILABILITY,
            resolve_incidents=(PERFORMANCE,),  # superseded by the outage
        )
    return Transition(state, replace(base, status=PENDING_FAILURE))


def _on_degraded(state: MonitorState, t: Thresholds, checked_at: datetime) -> Transition:
    soft = state.consecutive_soft_failures + 1
    degraded_since = state.degraded_since or checked_at
    if state.status in (DOWN, RECOVERING):
        # The target is reachable again: slow responses still count towards recovery.
        successes = state.consecutive_successes + 1
        base = replace(
            state,
            consecutive_failures=0,
            consecutive_successes=successes,
            consecutive_soft_failures=soft,
            degraded_since=degraded_since,
        )
        if successes >= t.recovery_threshold:
            recovered = replace(base, failing_since=None)
            if soft >= t.failure_threshold:
                return Transition(
                    state,
                    replace(recovered, status=DEGRADED),
                    open_incident=PERFORMANCE,
                    resolve_incidents=(AVAILABILITY,),
                )
            return Transition(state, replace(recovered, status=OPERATIONAL), resolve_incidents=(AVAILABILITY,))
        return Transition(state, replace(base, status=RECOVERING), update_incident=AVAILABILITY)

    base = replace(
        state,
        consecutive_failures=0,
        consecutive_successes=0,
        consecutive_soft_failures=soft,
        failing_since=None,
        degraded_since=degraded_since,
    )
    if state.status == DEGRADED:
        return Transition(state, replace(base, status=DEGRADED), update_incident=PERFORMANCE)
    if soft >= t.failure_threshold:
        return Transition(state, replace(base, status=DEGRADED), open_incident=PERFORMANCE)
    return Transition(state, replace(base, status=OPERATIONAL))


def _on_success(state: MonitorState, t: Thresholds) -> Transition:
    successes = state.consecutive_successes + 1
    base = replace(
        state,
        consecutive_failures=0,
        consecutive_successes=successes,
        consecutive_soft_failures=0,
    )
    if state.status in (DOWN, RECOVERING):
        if successes >= t.recovery_threshold:
            return Transition(
                state,
                replace(base, status=OPERATIONAL, failing_since=None, degraded_since=None),
                resolve_incidents=(AVAILABILITY, PERFORMANCE),
            )
        return Transition(state, replace(base, status=RECOVERING), update_incident=AVAILABILITY)
    if state.status == DEGRADED:
        if successes >= t.recovery_threshold:
            return Transition(
                state,
                replace(base, status=OPERATIONAL, failing_since=None, degraded_since=None),
                resolve_incidents=(PERFORMANCE,),
            )
        return Transition(state, replace(base, status=DEGRADED))
    # UNKNOWN, OPERATIONAL, PENDING_FAILURE → OPERATIONAL. Entering OPERATIONAL from any
    # other state also closes orphaned incidents (e.g. after pause/resume).
    resolve = (AVAILABILITY, PERFORMANCE) if state.status != OPERATIONAL else ()
    return Transition(
        state,
        replace(base, status=OPERATIONAL, failing_since=None, degraded_since=None),
        resolve_incidents=resolve,
    )
