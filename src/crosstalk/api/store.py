from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping

from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import ExperimentConfig
from crosstalk.orchestration.jobs import MAX_ATTEMPTS
from crosstalk.orchestration.schema import (
    experiments,
    idempotency_keys,
    jobs,
    sessions,
)
from crosstalk.orchestration.stages import stage_key
from crosstalk.orchestration.state import (
    JobState,
    Queue,
    SessionState,
    derive_experiment_state,
)


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class ControlStore(Protocol):
    def healthy(self) -> bool: ...
    def create_experiment(self, config: ExperimentConfig) -> Mapping[str, object]: ...
    def create_run(
        self, experiment_id: str, idempotency_key: str, code_commit: str
    ) -> tuple[Mapping[str, object], bool]: ...
    def list_experiments(
        self, cursor: str | None, limit: int
    ) -> tuple[list[Mapping[str, object]], str | None]: ...
    def get_experiment(self, experiment_id: str) -> Mapping[str, object]: ...
    def get_session(self, session_id: str) -> Mapping[str, object]: ...
    def cancel_experiment(self, experiment_id: str) -> Mapping[str, object]: ...
    def retry_job(self, job_id: str) -> Mapping[str, object]: ...


class PostgresControlStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def healthy(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def create_experiment(self, config: ExperimentConfig) -> Mapping[str, object]:
        experiment_id = new_ulid()
        row = {
            "id": experiment_id,
            "name": config.name,
            "config": config.model_dump(mode="json"),
            "config_hash": config.sha256(),
            "cancel_requested": False,
        }
        with self.engine.begin() as connection:
            result = (
                connection.execute(insert(experiments).values(**row).returning(*experiments.c))
                .mappings()
                .one()
            )
            return self._dict(result)

    def create_run(
        self, experiment_id: str, idempotency_key: str, code_commit: str
    ) -> tuple[Mapping[str, object], bool]:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            experiment = (
                connection.execute(
                    select(experiments).where(experiments.c.id == experiment_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if experiment is None:
                raise NotFoundError("Experiment does not exist")
            # The experiment row lock serializes identical concurrent requests. The
            # idempotency lookup must happen after acquiring it or two transactions
            # can both observe a miss and create duplicate sessions.
            existing = connection.execute(
                select(idempotency_keys.c.response).where(
                    idempotency_keys.c.experiment_id == experiment_id,
                    idempotency_keys.c.key == idempotency_key,
                    idempotency_keys.c.expires_at > now,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return cast(Mapping[str, object], existing), True
            if experiment["cancel_requested"]:
                raise ConflictError("Experiment is cancelled")
            config = ExperimentConfig.model_validate(experiment["config"])
            session_ids: list[str] = []
            for plan in config.sessions:
                session_id = new_ulid()
                session_ids.append(session_id)
                connection.execute(
                    insert(sessions).values(
                        id=session_id,
                        experiment_id=experiment_id,
                        state=SessionState.PLANNED,
                        config=plan.model_dump(mode="json"),
                        cancel_requested=False,
                    )
                )
                key = stage_key("plan", {"session_id": session_id}, config.sha256(), code_commit)
                connection.execute(
                    insert(jobs).values(
                        id=new_ulid(),
                        experiment_id=experiment_id,
                        session_id=session_id,
                        kind="plan",
                        queue=Queue.COLLECT,
                        payload={"session_id": session_id},
                        state=JobState.QUEUED,
                        attempts=0,
                        max_attempts=MAX_ATTEMPTS,
                        run_after=now,
                        stage_key=key,
                    )
                )
            response: Mapping[str, object] = {
                "experiment_id": experiment_id,
                "session_ids": session_ids,
            }
            connection.execute(
                pg_insert(idempotency_keys)
                .values(
                    experiment_id=experiment_id,
                    key=idempotency_key,
                    response=dict(response),
                    expires_at=now + timedelta(hours=24),
                )
                .on_conflict_do_nothing()
            )
            return response, False

    def list_experiments(
        self, cursor: str | None, limit: int
    ) -> tuple[list[Mapping[str, object]], str | None]:
        statement = select(experiments).order_by(experiments.c.id.desc()).limit(limit + 1)
        if cursor is not None:
            statement = statement.where(experiments.c.id < cursor)
        with self.engine.connect() as connection:
            rows = list(connection.execute(statement).mappings())
            visible = rows[:limit]
            items = [self._experiment_summary(connection, row) for row in visible]
            return items, str(visible[-1]["id"]) if len(rows) > limit else None

    def get_experiment(self, experiment_id: str) -> Mapping[str, object]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(experiments).where(experiments.c.id == experiment_id))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFoundError("Experiment does not exist")
            summary = dict(self._experiment_summary(connection, row))
            summary["config"] = cast(dict[str, object], row["config"])
            summary["sessions"] = [
                self._session(connection, item)
                for item in connection.execute(
                    select(sessions)
                    .where(sessions.c.experiment_id == experiment_id)
                    .order_by(sessions.c.id)
                ).mappings()
            ]
            return summary

    def get_session(self, session_id: str) -> Mapping[str, object]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(sessions).where(sessions.c.id == session_id))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFoundError("Session does not exist")
            return self._session(connection, row)

    def cancel_experiment(self, experiment_id: str) -> Mapping[str, object]:
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(experiments.c.id).where(experiments.c.id == experiment_id).with_for_update()
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError("Experiment does not exist")
            connection.execute(
                update(experiments)
                .where(experiments.c.id == experiment_id)
                .values(cancel_requested=True, updated_at=func.now())
            )
            connection.execute(
                update(jobs)
                .where(jobs.c.experiment_id == experiment_id, jobs.c.state == JobState.QUEUED)
                .values(state=JobState.CANCELLED, updated_at=func.now())
            )
            connection.execute(
                update(sessions)
                .where(
                    sessions.c.experiment_id == experiment_id,
                    sessions.c.state == SessionState.PLANNED,
                )
                .values(state=SessionState.CANCELLED, cancel_requested=True, updated_at=func.now())
            )
            connection.execute(
                update(sessions)
                .where(sessions.c.experiment_id == experiment_id)
                .values(cancel_requested=True, updated_at=func.now())
            )
        return {"id": experiment_id, "state": "cancelled"}

    def retry_job(self, job_id: str) -> Mapping[str, object]:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(jobs.c.state).where(jobs.c.id == job_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError("Job does not exist")
            if row not in {JobState.FAILED, JobState.DEAD}:
                if row == JobState.QUEUED:
                    return {"id": job_id, "state": "queued"}
                raise ConflictError("Only failed or dead jobs can be retried")
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state=JobState.QUEUED,
                    attempts=0,
                    run_after=func.now(),
                    lease_until=None,
                    worker_id=None,
                    error=None,
                    updated_at=func.now(),
                )
            )
        return {"id": job_id, "state": "queued"}

    def _experiment_summary(self, connection: Connection, row: RowMapping) -> Mapping[str, object]:
        session_states = list(
            connection.execute(
                select(sessions.c.state).where(sessions.c.experiment_id == row["id"])
            ).scalars()
        )
        job_states = list(
            connection.execute(
                select(jobs.c.state).where(jobs.c.experiment_id == row["id"])
            ).scalars()
        )
        state = (
            "cancelled"
            if row["cancel_requested"]
            else derive_experiment_state(
                [SessionState(item) for item in session_states],
                [JobState(item) for item in job_states],
            )
        )
        return {**self._dict(row), "state": state}

    @staticmethod
    def _session(connection: Connection, row: RowMapping) -> Mapping[str, object]:
        job_rows = list(
            connection.execute(
                select(jobs).where(jobs.c.session_id == row["id"]).order_by(jobs.c.created_at)
            ).mappings()
        )
        return {
            **PostgresControlStore._dict(row),
            "jobs": [PostgresControlStore._dict(job) for job in job_rows],
        }

    @staticmethod
    def _dict(row: RowMapping) -> dict[str, object]:
        return {str(key): cast(object, value) for key, value in row.items()}
