from enum import StrEnum

from crosstalk.common.errors import ValidationError


class Queue(StrEnum):
    COLLECT = "collect"
    ATTACK = "attack"
    EVALUATE = "evaluate"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class SessionState(StrEnum):
    PLANNED = "PLANNED"
    COLLECTING = "COLLECTING"
    COLLECTED = "COLLECTED"
    RECONSTRUCTED = "RECONSTRUCTED"
    VALID = "VALID"
    INVALID = "INVALID"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"


class ExperimentState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.PLANNED: frozenset({SessionState.COLLECTING, SessionState.CANCELLED}),
    SessionState.COLLECTING: frozenset(
        {SessionState.COLLECTED, SessionState.PARTIAL, SessionState.CANCELLED}
    ),
    SessionState.COLLECTED: frozenset(
        {SessionState.RECONSTRUCTED, SessionState.PARTIAL, SessionState.CANCELLED}
    ),
    SessionState.RECONSTRUCTED: frozenset(
        {
            SessionState.VALID,
            SessionState.INVALID,
            SessionState.PARTIAL,
            SessionState.CANCELLED,
        }
    ),
    SessionState.VALID: frozenset(),
    SessionState.INVALID: frozenset(),
    SessionState.PARTIAL: frozenset(),
    SessionState.CANCELLED: frozenset(),
}


def require_transition(current: SessionState, target: SessionState) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise ValidationError(f"Invalid session transition: {current} -> {target}")


def derive_experiment_state(sessions: list[SessionState], jobs: list[JobState]) -> ExperimentState:
    if any(state in {JobState.DEAD, JobState.FAILED} for state in jobs):
        return ExperimentState.FAILED
    if sessions and all(state == SessionState.CANCELLED for state in sessions):
        return ExperimentState.CANCELLED
    if any(state in {SessionState.INVALID, SessionState.PARTIAL} for state in sessions):
        return ExperimentState.PARTIAL
    if (
        sessions
        and all(state == SessionState.VALID for state in sessions)
        and all(state in {JobState.SUCCEEDED, JobState.CANCELLED} for state in jobs)
    ):
        return ExperimentState.COMPLETED
    return ExperimentState.RUNNING
