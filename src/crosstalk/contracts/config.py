from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class SessionPlan(StrictModel):
    experiment_id: str
    requests: int = Field(ge=1)
    concurrency: int = Field(ge=1, le=256)
    seed: int = Field(ge=0)
    arm: str = "a0"
    model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    validation: bool = True


class ExperimentConfig(StrictModel):
    name: str
    sessions: list[SessionPlan] = Field(min_length=1)
    policy_set: list[PolicyConfig] = Field(default_factory=lambda: [PolicyConfig()])
    capture: CaptureConfig | None = None

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
