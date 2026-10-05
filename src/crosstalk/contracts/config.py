from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crosstalk.common.artifacts import sha256_bytes
from crosstalk.common.ids import is_ulid


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Mode(StrEnum):
    LAB = "lab"
    PROD = "prod"


class PolicyConfig(StrictModel):
    arm: str = "a0"
    kind: str = "passthrough"
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @field_validator("arm")
    @classmethod
    def check_arm(cls, value: str) -> str:
        if len(value) != 2 or value[0] != "a" or not value[1].isdigit():
            raise ValueError("arm must be a fixed-width aN identifier")
        return value


class CaptureConfig(StrictModel):
    interface: str
    bpf: str = "tcp port 8443"
    ring_mb: int = Field(default=512, ge=64, le=4096)
    buffer_mb: int = Field(default=64, ge=1, le=1024)
    snaplen: int = 0

    @field_validator("bpf")
    @classmethod
    def check_bpf(cls, value: str) -> str:
        if value != "tcp port 8443":
            raise ValueError("capture filter must isolate the measured TLS leg")
        return value


class Regime(StrEnum):
    NAT = "NAT"
    MATCH_P = "MATCH-P"
    MATCH_PR = "MATCH-PR"


class SessionPlan(StrictModel):
    """One collected session.

    Either ``requests`` (synthetic validation traffic) or ``workload`` plus ``per_class``
    (corpus-driven, block-randomized traffic) defines the slots.
    """

    experiment_id: str
    requests: int | None = Field(default=None, ge=1)
    concurrency: int = Field(ge=1, le=256)
    seed: int = Field(ge=0)
    arm: str = "a0"
    model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    validation: bool = True
    workload: str | None = None
    regime: Regime = Regime.NAT
    per_class: int | None = Field(default=None, ge=1, le=10_000)
    classes: list[str] | None = None
    arms: list[str] | None = None
    max_completion_tokens: int = Field(default=512, ge=100, le=999)
    temperature: float = Field(default=0.7, ge=0, le=2)
    target_lengths: list[int] = Field(default_factory=lambda: [96, 160, 224], min_length=1)
    determinism_every: int = Field(default=25, ge=0)
    warmup: int = Field(default=30, ge=0)
    stream_interval: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_source(self) -> "SessionPlan":
        if (self.requests is None) == (self.workload is None):
            raise ValueError("set exactly one of requests or workload")
        if self.workload is not None and self.per_class is None:
            raise ValueError("workload sessions require per_class")
        if any(not 1 <= length <= self.max_completion_tokens for length in self.target_lengths):
            # Lengths are assigned identically across classes, so a length-dependent body
            # width never correlates with the class.
            raise ValueError("target lengths must be positive and within the token cap")
        for arm in self.session_arms:
            PolicyConfig(arm=arm)
        return self

    @property
    def session_arms(self) -> list[str]:
        return self.arms if self.arms is not None else [self.arm]


class AnalysisConfig(StrictModel):
    models: list[str] = Field(default_factory=lambda: ["chance", "majority", "lr", "lgbm"])
    representations: list[str] = Field(default_factory=lambda: ["r1", "r2", "tlen"])
    seeds: int = Field(default=5, ge=1, le=20)
    outer_folds: int = Field(default=5, ge=2, le=10)
    inner_folds: int = Field(default=3, ge=2, le=10)
    bootstrap: int = Field(default=2000, ge=100, le=100_000)
    null_permutations: int = Field(default=20, ge=0, le=1000)
    null_models: list[str] = Field(default_factory=lambda: ["lr", "lgbm"])
    null_bootstrap: int = Field(default=500, ge=100, le=100_000)
    learning_curve: bool = True
    planned_per_class: int = Field(default=200, ge=1)
    max_per_class: int = Field(default=300, ge=1)
    target_half_width: float = Field(default=0.05, gt=0, lt=1)
    sensitive_base_rates: list[int] = Field(default_factory=lambda: [10, 100, 1000])
    seed: int = Field(default=0, ge=0)
    # Pre-declared before collection (E1/E2 primary comparisons, Holm within the family).
    primary_model: str = "lgbm"
    primary_representation: str = "r1"
    alpha: float = Field(default=0.05, gt=0, lt=1)


class ExperimentConfig(StrictModel):
    name: str
    sessions: list[SessionPlan] = Field(min_length=1)
    policy_set: list[PolicyConfig] = Field(default_factory=lambda: [PolicyConfig()])
    capture: CaptureConfig | None = None
    analysis: AnalysisConfig | None = None

    def sha256(self) -> str:
        return sha256_bytes(self.model_dump_json(exclude_none=True).encode())


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = Field(min_length=1)
    messages: list[dict[str, object]] = Field(min_length=1)
    stream: bool
    stream_options: dict[str, object] = Field(default_factory=dict)

    @field_validator("stream")
    @classmethod
    def require_stream(cls, value: bool) -> bool:
        if not value:
            raise ValueError("only streaming requests are supported")
        return value


class LabHeaders(StrictModel):
    arm: str
    request_id: str

    @field_validator("request_id")
    @classmethod
    def check_request_id(cls, value: str) -> str:
        if not is_ulid(value):
            raise ValueError("request_id must be a ULID")
        return value

    @field_validator("arm")
    @classmethod
    def check_arm(cls, value: str) -> str:
        return PolicyConfig(arm=value).arm
