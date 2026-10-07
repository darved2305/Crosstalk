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


class EvaluationRow(StrictModel):
    session_id: str | None
    experiment_id: str
    regime: str
    concurrency: int
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


class TracePoint(StrictModel):
    t_start_ns: int
    t_end_ns: int
    min_value: int
    max_value: int
    count: int


class TraceResults(StrictModel):
    session_id: str
    request_id: str
    layer: str
    source_points: int
    downsampled: bool
    points: list[TracePoint]


class EmissionRow(StrictModel):
    seq: int
    t_emit_ns: int
    kind: str
    framed_len: int
    content_bytes: int
    pad_bytes: int
    n_events: int
    queue_delay_ns: int
    timer_lateness_ns: int
    arm: str


class EmissionResults(StrictModel):
    session_id: str
    request_id: str
    oracle: Literal[True] = True
    warning: str = "Validation-only oracle data; never attacker-visible."
    rows: list[EmissionRow]
