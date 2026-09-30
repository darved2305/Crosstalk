import json
import os
import platform
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from crosstalk.common.artifacts import atomic_replace, atomic_write


class LabProvenance(BaseModel):
    git_commit: str
    git_dirty: bool
    image_digests: dict[str, str] = Field(default_factory=dict)
    vllm_version: str | None = None
    model_revision: str | None = None
    tokenizer_revision: str | None = None
    environment: dict[str, str | int | bool] = Field(default_factory=dict)


class CaptureMetadata(BaseModel):
    interface: str
    mtu: int
    offloads: str = ""
    command: list[str] = Field(default_factory=list)
    kernel_drops: int | None = None


def _run(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def write_lab_provenance(root: Path, compose_file: Path) -> Path:
    commit = _run("git", "rev-parse", "HEAD")
    dirty = bool(_run("git", "status", "--porcelain"))
    compose = ("docker", "compose", "-f", str(compose_file))
    images: dict[str, str] = {}
    for service in ("shaper", "loadgen", "capture", "mock-upstream", "vllm"):
        container = _run(*compose, "ps", "-q", service)
        if container:
            images[service] = _run("docker", "inspect", "--format", "{{.Image}}", container)
    if not all(name in images for name in ("shaper", "loadgen", "capture")):
        raise RuntimeError("Start the laboratory containers before recording provenance")
    environment = {
        "host_kernel": platform.release(),
        "host_cpu": platform.processor(),
        "dumpcap_version": _run(
            *compose, "exec", "-T", "capture", "dumpcap", "--version"
        ).splitlines()[0],
        "tshark_version": _run(
            *compose, "exec", "-T", "capture", "tshark", "--version"
        ).splitlines()[0],
    }
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        gpu = None
    if gpu is not None and gpu.returncode == 0 and gpu.stdout.strip():
        model, driver = gpu.stdout.splitlines()[0].split(",", maxsplit=1)
        environment["host_gpu"] = model.strip()
        environment["host_gpu_driver"] = driver.strip()
    offloads = root / "lab" / "offloads-shaper.json"
    if offloads.exists():
        environment["shaper_offloads"] = offloads.read_text(encoding="utf-8")
    upstream_kind = "mock" if "mock-upstream" in images else "vllm"
    environment["upstream_kind"] = upstream_kind
    model_revision = (
        images["mock-upstream"] if upstream_kind == "mock" else os.environ.get("CT_MODEL_REVISION")
    )
    value = {
        "git_commit": commit,
        "git_dirty": dirty,
        "image_digests": images,
        "vllm_version": "0.29.0" if "vllm" in images else None,
        "model_revision": model_revision,
        "tokenizer_revision": os.environ.get("CT_TOKENIZER_REVISION"),
        "environment": environment,
    }
    target = root / "lab" / "provenance.json"
    data = json.dumps(value, indent=2).encode()
    if target.exists():
        atomic_replace(target, data)
    else:
        atomic_write(target, data)
    return target
