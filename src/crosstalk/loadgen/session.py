import asyncio
import json
import os
import platform
import ssl
import subprocess
import time
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import h11
import pyarrow as pa
import yaml

from crosstalk.common.artifacts import atomic_write, sha256_bytes
from crosstalk.common.errors import CaptureError, ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import ExperimentConfig, SessionPlan
from crosstalk.contracts.manifest import RunManifest, SessionStatus
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for
from crosstalk.loadgen.client import RequestResult, request
from crosstalk.loadgen.provenance import CaptureMetadata, LabProvenance
from crosstalk.workloads.corpus import Corpus, load_corpus
from crosstalk.workloads.planner import plan_session, request_body


class CaptureController(Protocol):
    async def start(self, session_id: str) -> CaptureMetadata: ...
    async def stop(self, session_id: str) -> CaptureMetadata: ...


class HttpCaptureControl:
    """Drives the capture supervisor that shares the loadgen network namespace."""

    async def start(self, session_id: str) -> CaptureMetadata:
        return CaptureMetadata.model_validate(await capture_control(b"/capture/start", session_id))

    async def stop(self, session_id: str) -> CaptureMetadata:
        return CaptureMetadata.model_validate(await capture_control(b"/capture/stop", session_id))


async def capture_control(path: bytes, session_id: str) -> dict[str, object]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 9200), timeout=5)
    connection = h11.Connection(h11.CLIENT)
    payload: dict[str, object] = {"session_id": session_id}
    if path == b"/capture/start":
        payload.update({"bpf": "tcp port 8443", "ring_mb": 512})
    body = json.dumps(payload).encode()
    writer.write(
        connection.send(
            h11.Request(
                method=b"POST",
                target=path,
                headers=[
                    (b"Host", b"localhost"),
                    (b"Content-Length", str(len(body)).encode()),
                    (b"Connection", b"close"),
                ],
            )
        )
        + connection.send(h11.Data(data=body))
        + connection.send(h11.EndOfMessage())
    )
    await writer.drain()
    status = 0
    response = bytearray()
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=35)
            if not chunk:
                raise CaptureError("Capture control closed before completing its response")
            connection.receive_data(chunk)
            continue
        if isinstance(event, h11.Response):
            status = event.status_code
        elif isinstance(event, h11.Data):
            response.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            break
    writer.close()
    await writer.wait_closed()
    result = json.loads(response)
    if status != 200:
        raise CaptureError(str(result))
    return result


def _write_parquet(path: Path, table: str, session_id: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValidationError(f"Artifact already exists: {path}")
    converted = [{"session_id": session_id, **row} for row in rows]
    temporary = path.with_suffix(path.suffix + ".partial")
    if temporary.exists():
        raise ValidationError(f"Stale temporary artifact: {temporary}")
    write_table(pa.Table.from_pylist(converted, schema=schema_for(table)), temporary)
    temporary.replace(path)


def _collect_emissions(root: Path, session_id: str, request_ids: set[str]) -> None:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "shaper-log").glob("*.ndjson")):
        with path.open(encoding="utf-8") as source:
            for line in source:
                value = json.loads(line)
                if value["request_id"] in request_ids:
                    rows.append(value)
    _write_parquet(
        root / "sessions" / session_id / "oracle" / "emissions.parquet",
        "emissions",
        session_id,
        rows,
    )


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    return commit, dirty


def _provenance() -> LabProvenance:
    path = os.environ.get("CT_PROVENANCE_FILE")
    if path is not None:
        return LabProvenance.model_validate_json(Path(path).read_text(encoding="utf-8"))
    commit, dirty = _git_state()
    return LabProvenance(git_commit=commit, git_dirty=dirty)


def _corpus_tokenizer(corpus: Corpus | None) -> str | None:
    if corpus is None or not corpus.manifest.get("tokenizer_revision"):
        return None
    return str(corpus.manifest["tokenizer_revision"])


async def run_session(
    plan: SessionPlan,
    root: Path,
    host: str = "shaper",
    port: int = 8443,
    ca_file: str | None = None,
    experiment: ExperimentConfig | None = None,
    capture: CaptureController | None = None,
) -> str:
    provenance = _provenance()
    capture = capture or HttpCaptureControl()
    policies = experiment.policy_set if experiment is not None else []
    corpus: Corpus | None = None
    if plan.workload is not None:
        workload = Path(plan.workload)
        corpus = load_corpus(workload if workload.is_absolute() else root / workload)
    planned = plan_session(plan, corpus)
    slots = planned.rows
    session_id = new_ulid()
    session = root / "sessions" / session_id
    session.mkdir(parents=True, exist_ok=False)
    session.chmod(0o775)
    _write_parquet(session / "plan.parquet", "plan", session_id, slots)
    started = time.time_ns()
    start_mono = time.monotonic_ns()
    capture_start = await capture.start(session_id)

    async def one(slot: dict[str, object], semaphore: asyncio.Semaphore) -> RequestResult:
        async with semaphore:
            request_id = str(slot["request_id"])
            body = request_body(slot, planned.texts[request_id], plan.model)
            keylog = (
                root / "restricted" / session_id / "keylogs" / f"{request_id}.keylog"
                if plan.validation
                else None
            )
            try:
                return await request(
                    host,
                    port,
                    body,
                    ca_file,
                    request_id=request_id,
                    keylog_file=keylog,
                    arm=str(slot["arm"]),
                )
            finally:
                if keylog is not None and keylog.exists():
                    keylog.chmod(0o640)

    by_block: dict[int, list[int]] = {}
    for index, slot in enumerate(slots):
        by_block.setdefault(int(str(slot["cblock"])), []).append(index)
    outcomes: dict[int, RequestResult | BaseException] = {}
    blocks: list[dict[str, int]] = []
    capture_stop: CaptureMetadata | None = None
    try:
        # Closed loop at each block's concurrency; a block fully drains before the next.
        for cblock in sorted(by_block):
            indices = by_block[cblock]
            level = int(str(slots[indices[0]]["block_concurrency"]))
            semaphore = asyncio.Semaphore(level)
            block_start = time.monotonic_ns()
            done = await asyncio.gather(
                *(one(slots[i], semaphore) for i in indices), return_exceptions=True
            )
            outcomes.update(zip(indices, done, strict=True))
            blocks.append(
                {
                    "cblock": cblock,
                    "concurrency": level,
                    "slots": len(indices),
                    "start_monotonic_ns": block_start,
                    "end_monotonic_ns": time.monotonic_ns(),
                }
            )
    finally:
        capture_stop = await capture.stop(session_id)
    results = [outcomes.get(index, CaptureError("Slot did not run")) for index in range(len(slots))]
    requests: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    reads: list[dict[str, object]] = []
    hints: list[dict[str, object]] = []
    text: list[dict[str, object]] = []
    failures = 0
    request_ids: set[str] = set()
    length_mismatches = 0
    for slot, result in zip(slots, results, strict=True):
        if isinstance(result, BaseException):
            failures += 1
            continue
        request_ids.add(result.request_id)
        if result.status != 200:
            failures += 1
        requests.append(
            {
                "request_id": result.request_id,
                "slot": slot["slot"],
                "arm": slot["arm"],
                "stream_interval": slot["stream_interval"],
                "client_port": result.client_port,
                "t_connect_ns": result.t_connect_ns,
                "t_tls_ns": result.t_tls_ns,
                "t_sent_ns": result.t_sent_ns,
                "t_first_byte_ns": result.t_first_byte_ns,
                "t_first_content_ns": result.t_first_content_ns,
                "t_done_ns": result.t_done_ns,
                "status": "completed" if result.status == 200 else "failed",
                "error_code": None if result.status == 200 else str(result.status),
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            }
        )
        hints.append(
            {
                "request_id": result.request_id,
                "client_port": result.client_port,
                "t_connect_ns": result.t_connect_ns,
            }
        )
        text.append(
            {
                "request_id": result.request_id,
                "prompt_text": planned.texts[result.request_id],
                "response_text": result.content,
            }
        )
        if (
            slot["target_len"] is not None
            and result.status == 200
            and result.completion_tokens != slot["target_len"]
        ):
            length_mismatches += 1
        for event in result.events:
            events.append(
                {
                    "request_id": result.request_id,
                    "idx": event.index,
                    "t_recv_ns": event.received_ns,
                    "is_content": event.is_content,
                    "framed_len": event.framed_len,
                }
            )
        for read in result.reads:
            reads.append(
                {
                    "request_id": result.request_id,
                    "idx": read.index,
                    "t_recv_ns": read.received_ns,
                    "plaintext_len": read.plaintext_len,
                }
            )
    _write_parquet(session / "oracle" / "requests.parquet", "requests", session_id, requests)
    _write_parquet(
        session / "oracle" / "client_events.parquet", "client_events", session_id, events
    )
    _write_parquet(session / "oracle" / "client_reads.parquet", "client_reads", session_id, reads)
    _write_parquet(session / "observer" / "flow_hints.parquet", "flow_hints", session_id, hints)
    (session / "observer").chmod(0o775)
    _write_parquet(root / "restricted" / session_id / "text.parquet", "text", session_id, text)
    _collect_emissions(root, session_id, request_ids)
    labels = [
        {
            "request_id": slot["request_id"],
            "prompt_id": slot["prompt_id"],
            "class": slot["class"],
            "family_id": slot["family_id"],
            "template_id": slot["template_id"],
            "regime": slot["regime"],
            "role": slot["role"],
            "arm": slot["arm"],
            "concurrency": slot["block_concurrency"],
            "stream_interval": slot["stream_interval"],
            "cblock": slot["cblock"],
            "prompt_bucket": slot["prompt_bucket"],
            "target_len": slot["target_len"],
            "t2_sensitive": slot["t2_sensitive"],
        }
        for slot in slots
    ]
    _write_parquet(root / "labels" / session_id / "labels.parquet", "labels", session_id, labels)
    validation: dict[str, bool] = {}
    reasons = ["Reconstruction and independent validation pending"]
    if any(slot["target_len"] is not None for slot in slots):
        # MATCH-PR is only meaningful if the engine produced exactly the forced length.
        validation["length_forcing"] = length_mismatches == 0
        if length_mismatches:
            reasons.append(f"{length_mismatches} responses missed their forced length")
    environment = {
        "client_host": host,
        "kernel": platform.release(),
        "cpu": platform.processor(),
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "uvloop": version("uvloop") if platform.system() == "Linux" else "unavailable",
        "capture_interface": capture_start.interface,
        "capture_mtu": capture_start.mtu,
        "capture_offloads": capture_stop.offloads,
        "capture_command": " ".join(capture_stop.command),
    }
    environment.update(provenance.environment)
    manifest = RunManifest(
        session_id=session_id,
        experiment_id=plan.experiment_id,
        config_sha256=experiment.sha256()
        if experiment is not None
        else sha256_bytes(plan.model_dump_json().encode()),
        plan_sha256=planned.sha256(),
        git_commit=provenance.git_commit,
        git_dirty=provenance.git_dirty,
        image_digests=provenance.image_digests,
        vllm_version=provenance.vllm_version,
        model_id=plan.model,
        model_revision=provenance.model_revision,
        tokenizer_revision=provenance.tokenizer_revision or _corpus_tokenizer(corpus),
        workload_version=str(corpus.manifest["workload_version"])
        if corpus is not None
        else "synthetic-validation-v1",
        workload_sha256=str(corpus.manifest["workload_sha256"])
        if corpus is not None
        else sha256_bytes("\n".join(sorted(planned.texts.values())).encode()),
        policy_set_sha256=sha256_bytes(
            json.dumps([policy.model_dump() for policy in policies], sort_keys=True).encode()
        ),
        policies=policies,
        seeds={"plan": plan.seed, "model": 0},
        stream_interval=plan.stream_interval,
        concurrency=plan.concurrency,
        blocks=blocks,
        environment=environment,
        timing={
            "start_realtime_ns": started,
            "start_monotonic_ns": start_mono,
            "end_realtime_ns": time.time_ns(),
            "end_monotonic_ns": time.monotonic_ns(),
        },
        planned=len(slots),
        completed=sum(
            result.status == 200 for result in results if isinstance(result, RequestResult)
        ),
        failed=failures,
        capture_drops=capture_stop.kernel_drops,
        validation=validation,
        status=SessionStatus.PARTIAL,
        reasons=reasons,
    )
    atomic_write(session / "manifest.json", manifest.model_dump_json(indent=2).encode())
    return session_id


async def run_plan(path: Path, root: Path, host: str, ca_file: str | None) -> None:
    config = ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for plan in config.sessions:
        session_id = await run_session(plan, root, host=host, ca_file=ca_file, experiment=config)
        print(session_id)
