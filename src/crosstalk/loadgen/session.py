import asyncio
import json
import subprocess
import time
from pathlib import Path

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


async def run_session(
    plan: SessionPlan,
    root: Path,
    host: str = "shaper",
    port: int = 8443,
    ca_file: str | None = None,
) -> str:
    session_id = new_ulid()
    session = root / "sessions" / session_id
    session.mkdir(parents=True, exist_ok=False)
    started = time.time_ns()
    start_mono = time.monotonic_ns()
    await capture_control(b"/capture/start", session_id)
    capture_started = True
    semaphore = asyncio.Semaphore(plan.concurrency)

    async def one(slot: int) -> RequestResult:
        async with semaphore:
            request_id = new_ulid()
            body: dict[str, object] = {
                "model": plan.model,
                "messages": [
                    {"role": "user", "content": f"Explain a simple synthetic topic {slot}."}
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
                "cache_salt": new_ulid(),
            }
            keylog = (
                root / "restricted" / session_id / "keylogs" / f"{request_id}.keylog"
                if plan.validation
                else None
            )
            return await request(
                host, port, body, ca_file, request_id=request_id, keylog_file=keylog
            )

    results: list[RequestResult | BaseException] = []
    try:
        results = await asyncio.gather(
            *(one(slot) for slot in range(plan.requests)), return_exceptions=True
        )
    finally:
        if capture_started:
            await capture_control(b"/capture/stop", session_id)
    requests: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    reads: list[dict[str, object]] = []
    hints: list[dict[str, object]] = []
    text: list[dict[str, object]] = []
    failures = 0
    request_ids: set[str] = set()
    for slot, result in enumerate(results):
        if isinstance(result, BaseException):
            failures += 1
            continue
        request_ids.add(result.request_id)
        requests.append(
            {
                "request_id": result.request_id,
                "slot": slot,
                "arm": plan.arm,
                "stream_interval": 1,
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
                "prompt_text": f"Explain a simple synthetic topic {slot}.",
                "response_text": result.content,
            }
        )
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
    _write_parquet(root / "restricted" / session_id / "text.parquet", "text", session_id, text)
    _collect_emissions(root, session_id, request_ids)
    git_commit, git_dirty = _git_state()
    manifest = RunManifest(
        session_id=session_id,
        experiment_id=plan.experiment_id,
        config_sha256=sha256_bytes(plan.model_dump_json().encode()),
        plan_sha256=sha256_bytes(plan.model_dump_json().encode()),
        git_commit=git_commit,
        git_dirty=git_dirty,
        model_id=plan.model,
        workload_version="synthetic-validation-v1",
        workload_sha256=sha256_bytes(b"synthetic-validation-v1"),
        policy_set_sha256=sha256_bytes(b"a0:passthrough"),
        seeds={"plan": plan.seed},
        stream_interval=1,
        concurrency=plan.concurrency,
        environment={"client_host": host},
        timing={
            "start_realtime_ns": started,
            "start_monotonic_ns": start_mono,
            "end_realtime_ns": time.time_ns(),
            "end_monotonic_ns": time.monotonic_ns(),
        },
        planned=plan.requests,
        completed=len(requests),
        failed=failures,
        status=SessionStatus.PARTIAL,
        reasons=["Reconstruction and independent validation pending"],
    )
    atomic_write(session / "manifest.json", manifest.model_dump_json(indent=2).encode())
    return session_id


async def run_plan(path: Path, root: Path, host: str, ca_file: str | None) -> None:
    config = ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for plan in config.sessions:
        session_id = await run_session(plan, root, host=host, ca_file=ca_file)
        print(session_id)
