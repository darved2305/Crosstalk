import random
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.engine import Engine, RowMapping

from crosstalk.common.errors import ValidationError
from crosstalk.orchestration.schema import jobs
from crosstalk.orchestration.state import JobState, Queue

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
BACKOFF_BASE_SECONDS = 5
BACKOFF_CAP_SECONDS = 300
MAX_ATTEMPTS = 5


def retry_delay(attempts: int, sample: float) -> timedelta:
    if attempts < 1 or not 0 <= sample <= 1:
        raise ValueError("attempts must be positive and sample must lie in [0, 1]")
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)))
    return timedelta(seconds=ceiling * sample)


def claim_statement(queue: Queue) -> Any:
    candidate = (
        select(jobs.c.id)
        .where(
            jobs.c.state == JobState.QUEUED,
            jobs.c.queue == queue,
            jobs.c.run_after <= func.now(),
        )
        .order_by(jobs.c.id)
        .with_for_update(skip_locked=True)
        .limit(1)
        .scalar_subquery()
    )
    return (
        update(jobs)
        .where(jobs.c.id == candidate)
        .values(
            state=JobState.RUNNING,
            attempts=jobs.c.attempts + 1,
            lease_until=func.now() + timedelta(seconds=LEASE_SECONDS),
            worker_id=bindparam("claim_worker_id"),
            updated_at=func.now(),
        )
        .returning(*jobs.c)
    )


class JobRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def claim(self, queue: Queue, worker_id: str) -> RowMapping | None:
        with self.engine.begin() as connection:
            row = (
                connection.execute(claim_statement(queue), {"claim_worker_id": worker_id})
                .mappings()
                .first()
            )
            return row

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.worker_id == worker_id,
                    jobs.c.state == JobState.RUNNING,
                    jobs.c.lease_until > func.now(),
                )
                .values(
                    lease_until=func.now() + timedelta(seconds=LEASE_SECONDS),
                    updated_at=func.now(),
                )
            )
            return result.rowcount == 1

    def succeed(self, job_id: str, worker_id: str) -> None:
        self._finish(job_id, worker_id, JobState.SUCCEEDED, None)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: Mapping[str, object],
        sample: float | None = None,
    ) -> JobState:
        jitter = random.SystemRandom().random() if sample is None else sample
        with self.engine.begin() as connection:
            row = connection.execute(
                select(jobs.c.attempts, jobs.c.max_attempts)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.worker_id == worker_id,
                    jobs.c.state == JobState.RUNNING,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise ValidationError("Job is not running under this worker")
            attempts, maximum = cast(tuple[int, int], tuple(row))
            state = JobState.DEAD if attempts >= maximum else JobState.QUEUED
            values: dict[str, object] = {
                "state": state,
                "error": dict(error),
                "worker_id": None,
                "lease_until": None,
                "updated_at": func.now(),
            }
            if state == JobState.QUEUED:
                values["run_after"] = datetime.now(UTC) + retry_delay(attempts, jitter)
            connection.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
            return state

    def reap_expired(self) -> int:
        with self.engine.begin() as connection:
            expired = connection.execute(
                select(jobs.c.id, jobs.c.attempts, jobs.c.max_attempts)
                .where(jobs.c.state == JobState.RUNNING, jobs.c.lease_until <= func.now())
                .with_for_update(skip_locked=True)
            ).all()
            for job_id, attempts, maximum in expired:
                state = JobState.DEAD if attempts >= maximum else JobState.QUEUED
                connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.state == JobState.RUNNING)
                    .values(
                        state=state,
                        worker_id=None,
                        lease_until=None,
                        run_after=func.now(),
                        error={"type": "lease_expired"},
                        updated_at=func.now(),
                    )
                )
            return len(expired)

    def cancel_experiment(self, experiment_id: str) -> int:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.experiment_id == experiment_id,
                    jobs.c.state == JobState.QUEUED,
                )
                .values(state=JobState.CANCELLED, updated_at=func.now())
            )
            return result.rowcount

    def _finish(
        self,
        job_id: str,
        worker_id: str,
        state: JobState,
        error: Mapping[str, object] | None,
    ) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.worker_id == worker_id,
                    jobs.c.state == JobState.RUNNING,
                )
                .values(
                    state=state,
                    error=None if error is None else dict(error),
                    worker_id=None,
                    lease_until=None,
                    updated_at=func.now(),
                )
            )
            if result.rowcount != 1:
                raise ValidationError("Job is not running under this worker")
