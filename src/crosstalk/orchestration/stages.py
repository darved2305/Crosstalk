import hashlib
import os
import shutil
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from crosstalk.common.artifacts import canonical_json, sha256_file
from crosstalk.common.errors import ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.orchestration.state import Queue


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    queue: Queue
    dependencies: tuple[str, ...]
    oracle_allowed: bool = False


# Per-session stages run once per collected session. Feature extraction, attacker
# training and statistics pool every session of an experiment (family-disjoint folds
# span sessions), so they run together in the experiment-level ``analyze`` stage.
STAGES: Mapping[str, Stage] = {
    "plan": Stage("plan", Queue.COLLECT, ()),
    "collect": Stage("collect", Queue.COLLECT, ("plan",), oracle_allowed=True),
    "reconstruct": Stage("reconstruct", Queue.ATTACK, ("collect",)),
    "validate": Stage("validate", Queue.EVALUATE, ("reconstruct",), oracle_allowed=True),
    "analyze": Stage("analyze", Queue.EVALUATE, ("validate",), oracle_allowed=True),
    "report": Stage("report", Queue.EVALUATE, ("analyze",), oracle_allowed=True),
}


def stage_key(
    stage: str, input_hashes: Mapping[str, str], config_hash: str, code_commit: str
) -> str:
    if stage not in STAGES:
        raise ValidationError(f"Unknown stage: {stage}")
    value: dict[str, object] = {
        "stage": stage,
        "inputs": dict(sorted(input_hashes.items())),
        "config_hash": config_hash,
        "code_commit": code_commit,
    }
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    path: str
    sha256: str
    size_bytes: int


def inventory(directory: Path) -> list[ArtifactFile]:
    return [
        ArtifactFile(
            path=path.relative_to(directory).as_posix(),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]


@contextmanager
def atomic_stage_output(target: Path) -> Generator[Path]:
    """Expose a sibling staging directory and publish it only after successful work."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValidationError(f"Stage output already exists: {target}")
    temporary = target.parent / f".tmp-{new_ulid()}"
    temporary.mkdir()
    try:
        yield temporary
        if target.exists():
            raise ValidationError(f"Stage output already exists: {target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
