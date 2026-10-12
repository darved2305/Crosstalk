from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

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
    updated_at: datetime | None = None


class SessionView(StrictModel):
    id: str
    experiment_id: str
    state: str
    cancel_requested: bool
    jobs: list[JobView] = Field(default_factory=lambda: list[JobView]())


class EventView(StrictModel):
    id: str
    session_id: str | None = None
    kind: str
    data: dict[str, object]
    created_at: datetime


class ExperimentDetail(ExperimentSummary):
    config: dict[str, object]
    sessions: list[SessionView]
    # Experiment-level stages (analysis and report) that belong to no single session.
    jobs: list[JobView] = Field(default_factory=lambda: list[JobView]())
    # Most recent pipeline events first.
    events: list[EventView] = Field(default_factory=lambda: list[EventView]())


class ControlResult(StrictModel):
    id: str
    state: str


class Problem(StrictModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None


class EvaluationRow(StrictModel):
    session_id: str | None
    experiment_id: str
    regime: str
    concurrency: int | None
    task: str
    representation: str
    arm: str
    setting: str
    model: str
    metric: str
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n: int
    n_families: int
    B: int


class EvaluationResults(StrictModel):
    experiment_id: str
    rows: list[EvaluationRow]
    reportable: bool | None = None
    warning: str = "Reportability has not been verified."


class FrontierRow(StrictModel):
    session_id: str | None
    experiment_id: str
    concurrency: int
    condition: str
    setting: str
    model: str
    representation: str
    balanced_accuracy: float | None
    rho: float | None
    rho_lo: float | None
    rho_hi: float | None
    bandwidth_overhead_pct: float | None
    ttft_p95_ms: float | None
    itl_p99_ms: float | None


class FrontierResults(StrictModel):
    experiment_id: str
    setting: str
    cost: str
    rows: list[FrontierRow]
    reportable: bool | None = None
    warning: str = "Reportability has not been verified."


class TracePoint(StrictModel):
    t_start_ns: str
    t_end_ns: str
    min_value: int
    max_value: int
    count: int

    @field_validator("t_start_ns", "t_end_ns", mode="before")
    @classmethod
    def lossless_nanoseconds(cls, value: object) -> str:
        if isinstance(value, int) or (isinstance(value, str) and value.isdecimal()):
            return str(value)
        raise ValueError("nanosecond timestamps must be integer decimal values")


class TraceResults(StrictModel):
    session_id: str
    request_id: str
    layer: str
    source_points: int
    downsampled: bool
    points: list[TracePoint]
    reportable: bool | None = None
    warning: str = "Session provenance has not been verified."


class EmissionRow(StrictModel):
    seq: int
    t_emit_ns: str
    kind: str
    framed_len: int
    content_bytes: int
    pad_bytes: int
    n_events: int
    queue_delay_ns: int
    timer_lateness_ns: int
    arm: str

    @field_validator("t_emit_ns", mode="before")
    @classmethod
    def lossless_nanoseconds(cls, value: object) -> str:
        return TracePoint.lossless_nanoseconds(value)


class EmissionResults(StrictModel):
    session_id: str
    request_id: str
    oracle: Literal[True] = True
    warning: str = "Validation-only oracle data; never attacker-visible."
    rows: list[EmissionRow]
