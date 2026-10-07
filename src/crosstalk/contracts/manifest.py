from enum import StrEnum

from pydantic import Field

from crosstalk.contracts.config import PolicyConfig, StrictModel


class SessionStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    PARTIAL = "PARTIAL"


class RunManifest(StrictModel):
    schema_version: int = 1
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
    policies: list[PolicyConfig] = Field(default_factory=list[PolicyConfig])
    seeds: dict[str, int]
    stream_interval: int
    concurrency: int
    # One entry per concurrency block: index, level, slots and monotonic start/end.
    blocks: list[dict[str, int]] = Field(default_factory=list[dict[str, int]])
    environment: dict[str, str | int | bool]
    timing: dict[str, int]
    planned: int
    completed: int = 0
    failed: int = 0
    capture_drops: int | None = None
    validation: dict[str, bool] = Field(default_factory=dict)
    status: SessionStatus = SessionStatus.PARTIAL
    reasons: list[str] = Field(default_factory=list)

    def reportability_issues(self) -> list[str]:
        """Return objective reasons this session cannot support reported results."""
        issues: list[str] = []
        if self.git_dirty:
            issues.append("source tree was dirty")
        if self.status != SessionStatus.VALID:
            issues.append(f"session status is {self.status}")
        for service in ("shaper", "loadgen", "capture"):
            if not self.image_digests.get(service):
                issues.append(f"missing {service} image digest")
        if not ({"vllm", "mock-upstream"} & self.image_digests.keys()):
            issues.append("missing upstream image digest")
        if not self.model_revision:
            issues.append("missing model revision")
        if self.workload_version != "synthetic-validation-v1" and not self.tokenizer_revision:
            issues.append("missing tokenizer revision")
        if self.capture_drops not in (0, None):
            issues.append(f"capture reported {self.capture_drops} kernel drops")
        issues.extend(
            f"validation failed: {name}" for name, passed in self.validation.items() if not passed
        )
        return issues
