from enum import StrEnum

from pydantic import Field

from crosstalk.contracts.config import StrictModel


class SessionStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    PARTIAL = "PARTIAL"


class RunManifest(StrictModel):
    session_id: str
    experiment_id: str
    config_sha256: str
    plan_sha256: str
    git_commit: str
    git_dirty: bool
    image_digests: dict[str, str] = Field(default_factory=dict)
    vllm_version: str | None = None
    model_id: str
    model_revision: str | None = None
    tokenizer_revision: str | None = None
    workload_version: str
    workload_sha256: str
    policy_set_sha256: str
    seeds: dict[str, int]
    stream_interval: int
    concurrency: int
    environment: dict[str, str | int | bool]
    timing: dict[str, int]
    planned: int
    completed: int = 0
    failed: int = 0
    capture_drops: int | None = None
    validation: dict[str, bool] = Field(default_factory=dict)
    status: SessionStatus = SessionStatus.PARTIAL
    reasons: list[str] = Field(default_factory=list)
