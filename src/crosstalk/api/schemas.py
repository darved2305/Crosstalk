from datetime import datetime
from typing import Literal

from pydantic import Field

from crosstalk.contracts.config import StrictModel


class ExperimentCreated(StrictModel):
    id: str
    name: str
    config_hash: str


class RunCreated(StrictModel):
    experiment_id: str
    session_ids: list[str]
    replayed: bool = False


class ExperimentSummary(StrictModel):
    id: str
    name: str
    config_hash: str
    state: Literal["running", "completed", "partial", "failed", "cancelled"]
    created_at: datetime
    updated_at: datetime


class ExperimentPage(StrictModel):
    items: list[ExperimentSummary]
    next_cursor: str | None = None


class JobView(StrictModel):
    id: str
    kind: str
    queue: str
    state: str
    attempts: int
    max_attempts: int
    error: dict[str, object] | None = None


class SessionView(StrictModel):
    id: str
    experiment_id: str
    state: str
    cancel_requested: bool
    jobs: list[JobView] = Field(default_factory=lambda: list[JobView]())


class ExperimentDetail(ExperimentSummary):
    config: dict[str, object]
    sessions: list[SessionView]


class ControlResult(StrictModel):
    id: str
    state: str


class Problem(StrictModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None
