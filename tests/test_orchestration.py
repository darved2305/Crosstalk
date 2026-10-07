import hashlib
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from crosstalk.common.errors import ValidationError
from crosstalk.orchestration.jobs import claim_statement, retry_delay
from crosstalk.orchestration.schema import metadata
from crosstalk.orchestration.stages import STAGES, atomic_stage_output, inventory, stage_key
from crosstalk.orchestration.state import (
    ExperimentState,
    JobState,
    Queue,
    SessionState,
    derive_experiment_state,
    require_transition,
)


def test_schema_has_durable_tables_and_constraints() -> None:
    assert set(metadata.tables) == {"experiments", "sessions", "jobs", "artifacts", "events"}
    jobs = metadata.tables["jobs"]
    assert jobs.c.max_attempts.default is not None
    assert {index.name for index in jobs.indexes} == {"ix_jobs_claim", "ix_jobs_lease"}
    assert any(constraint.name == "uq_jobs_stage_key" for constraint in jobs.constraints)


def test_claim_is_one_postgres_skip_locked_update() -> None:
    sql = str(
        claim_statement(Queue.ATTACK).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    ).upper()
    assert sql.startswith("UPDATE JOBS SET")
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "RETURNING" in sql
    assert "RUN_AFTER <= NOW()" in sql
    assert "ATTEMPTS=(JOBS.ATTEMPTS +" in sql


@pytest.mark.parametrize(
    ("attempts", "sample", "seconds"),
    [(1, 0.0, 0), (1, 1.0, 5), (2, 0.5, 5), (7, 1.0, 300), (20, 1.0, 300)],
)
def test_full_jitter_backoff(attempts: int, sample: float, seconds: float) -> None:
    assert retry_delay(attempts, sample).total_seconds() == seconds


def test_session_state_machine_preserves_terminal_meaning() -> None:
    require_transition(SessionState.PLANNED, SessionState.COLLECTING)
    require_transition(SessionState.COLLECTING, SessionState.PARTIAL)
    require_transition(SessionState.RECONSTRUCTED, SessionState.INVALID)
    with pytest.raises(ValidationError, match="Invalid session transition"):
        require_transition(SessionState.INVALID, SessionState.VALID)
    with pytest.raises(ValidationError, match="Invalid session transition"):
        require_transition(SessionState.PARTIAL, SessionState.INVALID)


@pytest.mark.parametrize(
    ("sessions", "jobs", "expected"),
    [
        ([SessionState.PLANNED], [JobState.QUEUED], ExperimentState.RUNNING),
        ([SessionState.VALID], [JobState.SUCCEEDED], ExperimentState.COMPLETED),
        ([SessionState.INVALID], [JobState.SUCCEEDED], ExperimentState.PARTIAL),
        ([SessionState.PARTIAL], [JobState.SUCCEEDED], ExperimentState.PARTIAL),
        ([SessionState.CANCELLED], [JobState.CANCELLED], ExperimentState.CANCELLED),
        ([SessionState.VALID], [JobState.DEAD], ExperimentState.FAILED),
    ],
)
def test_experiment_state_is_derived(
    sessions: list[SessionState], jobs: list[JobState], expected: ExperimentState
) -> None:
    assert derive_experiment_state(sessions, jobs) == expected


def test_stage_registry_enforces_access_and_dependencies() -> None:
    assert STAGES["collect"].queue == Queue.COLLECT
    assert STAGES["reconstruct"].queue == Queue.ATTACK
    assert not STAGES["attack"].oracle_allowed
    assert STAGES["validate"].oracle_allowed
    assert STAGES["stats"].dependencies == ("attack", "validate")


def test_stage_key_is_stable_and_input_sensitive() -> None:
    first = stage_key("features", {"b": "2", "a": "1"}, "config", "commit")
    assert first == stage_key("features", {"a": "1", "b": "2"}, "config", "commit")
    assert first != stage_key("features", {"a": "changed", "b": "2"}, "config", "commit")
    assert len(first) == 64


def test_atomic_stage_output_publishes_and_hashes(tmp_path: Path) -> None:
    target = tmp_path / "stage"
    with atomic_stage_output(target) as staging:
        (staging / "nested").mkdir()
        (staging / "nested" / "result.txt").write_text("complete", encoding="utf-8")
        assert not target.exists()
    assert (target / "nested" / "result.txt").read_text(encoding="utf-8") == "complete"
    files = inventory(target)
    assert files[0].path == "nested/result.txt"
    assert files[0].sha256 == hashlib.sha256(b"complete").hexdigest()


def test_atomic_stage_output_cleans_interrupted_write(tmp_path: Path) -> None:
    target = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="crash"), atomic_stage_output(target) as staging:
        (staging / "partial").write_text("not complete", encoding="utf-8")
        raise RuntimeError("crash")
    assert not target.exists()
    assert not list(tmp_path.glob(".tmp-*"))
