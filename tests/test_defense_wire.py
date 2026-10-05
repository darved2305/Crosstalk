"""Defense arms against a real TLS shaper: official SDK compatibility and on-the-wire sizes."""

import asyncio
import json
import ssl
from collections import defaultdict
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import yaml
from openai import OpenAI

from crosstalk.capture.reconstruct import map_flows, reconstruct
from crosstalk.capture.validate import validate_emissions
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import ExperimentConfig
from crosstalk.contracts.parquet import read_table
from crosstalk.loadgen.session import run_session
from crosstalk.shaper.policies import ladder
from crosstalk.shaper.server import ShaperSettings, run
from crosstalk.testing.mock_upstream import echo_plan
from crosstalk.testing.mock_upstream import run as run_mock
from crosstalk.testing.recorder import RecordingRelay
from crosstalk.workloads.corpus import build
from crosstalk.workloads.tokens import WhitespaceCounter
from test_integration import _certificate, _port, _wait_port

SOURCE = Path(__file__).parents[1] / "workloads" / "corpus" / "v1" / "corpus.yaml"
WINDOW_MS = 15
FIRST_BUCKET = 256
TICK_MS = 5
RECORD_SIZE = 512
QUANTUM = 4
POLICIES = [
    {"arm": "a0", "kind": "passthrough"},
    {"arm": "a1", "kind": "bucket_pad", "parameters": {"first_bucket": FIRST_BUCKET}},
    {"arm": "a2", "kind": "window_batch", "parameters": {"window_ms": WINDOW_MS}},
    {
        "arm": "a4",
        "kind": "hybrid",
        "parameters": {"window_ms": WINDOW_MS, "first_bucket": FIRST_BUCKET},
    },
    {
        "arm": "a3",
        "kind": "constant_rate",
        "parameters": {
            "interval_ms": TICK_MS,
            "record_size": RECORD_SIZE,
            "tail_quantum_ticks": QUANTUM,
        },
    },
]
EVENT_KINDS = {"content", "merged", "padded"}


class _Lab:
    def __init__(self, root: Path, tmp: Path) -> None:
        self.root = root
        self.cert, self.key = _certificate(tmp)
        self.policy_file = tmp / "policies.yaml"
        self.policy_file.write_text(yaml.safe_dump(POLICIES), encoding="utf-8")
        self.mock_port, self.shaper_port, self.admin_port = _port(), _port(), _port()
        self.tasks: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> "_Lab":
        self.tasks.append(
            asyncio.create_task(run_mock("127.0.0.1", self.mock_port, token_delay_ms=1.0))
        )
        await _wait_port(self.mock_port)
        settings = ShaperSettings(
            host="127.0.0.1",
            port=self.shaper_port,
            admin_host="127.0.0.1",
            admin_port=self.admin_port,
            cert=self.cert,
            key=self.key,
            upstream_url=f"http://127.0.0.1:{self.mock_port}",
            oracle_dir=self.root / "shaper-log",
            policy_file=self.policy_file,
        )
        self.tasks.append(asyncio.create_task(run(settings)))
        await _wait_port(self.admin_port)
        return self

    async def __aexit__(self, *_: object) -> None:
        for task in self.tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def emissions(self) -> dict[str, list[dict[str, object]]]:
        rows: dict[str, list[dict[str, object]]] = defaultdict(list)
        for path in (self.root / "shaper-log").glob("*.ndjson"):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                rows[row["request_id"]].append(row)
        return {key: sorted(value, key=lambda r: int(str(r["seq"]))) for key, value in rows.items()}


def _sdk_stream(
    port: int, cert: Path, arm: str, rid: str, prompt: str
) -> tuple[str, int, int | None]:
    context = ssl.create_default_context(cafile=str(cert))
    with httpx.Client(verify=context) as http:
        client = OpenAI(
            base_url=f"https://127.0.0.1:{port}/v1",
            api_key="unused",
            http_client=http,
            default_headers={"x-ct-arm": arm, "x-ct-rid": rid},
            max_retries=0,
        )
        stream = client.chat.completions.create(
            model="mock",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            stream_options={"include_usage": True},
        )
        chunks = 0
        text: list[str] = []
        usage: int | None = None
        for chunk in stream:
            chunks += 1
            for choice in chunk.choices:
                text.append(choice.delta.content or "")
            if chunk.usage is not None:
                usage = chunk.usage.completion_tokens
        return "".join(text), chunks, usage


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["a0", "a1", "a2", "a3", "a4"])
async def test_openai_sdk_sees_identical_content(arm: str, tmp_path: Path) -> None:
    prompt = "Explain sourdough starter maintenance with practical tips for a beginner baker"
    expected = "".join(echo_plan({"messages": [{"role": "user", "content": prompt}]}))
    async with _Lab(tmp_path / "artifacts", tmp_path) as lab:
        rid = new_ulid()
        text, chunks, usage = await asyncio.to_thread(
            _sdk_stream, lab.shaper_port, lab.cert, arm, rid, prompt
        )
        await asyncio.sleep(0.3)
    assert text == expected
    assert usage == len(expected.split())
    rows = lab.emissions()[rid]
    assert {row["arm"] for row in rows} == {arm}
    # One SDK chunk per event-carrying emission: no spurious or lost chunks.
    assert chunks == sum(row["kind"] in EVENT_KINDS for row in rows)
    if arm in ("a2", "a4"):
        assert any(row["kind"] == "merged" for row in rows)
    if arm in ("a1", "a4"):
        buckets = set(ladder(FIRST_BUCKET))
        assert all(row["framed_len"] in buckets for row in rows if row["kind"] in EVENT_KINDS)
    if arm == "a3":
        ticks = [row for row in rows if row["kind"] not in ("headers", "terminator")]
        assert {row["framed_len"] for row in ticks} == {RECORD_SIZE}
        assert len(ticks) % QUANTUM == 0


@pytest.mark.asyncio
async def test_unknown_arm_is_rejected(tmp_path: Path) -> None:
    async with _Lab(tmp_path / "artifacts", tmp_path) as lab:
        with pytest.raises(Exception, match="400"):
            await asyncio.to_thread(_sdk_stream, lab.shaper_port, lab.cert, "a7", new_ulid(), "hi")


@pytest.mark.asyncio
async def test_defended_records_on_the_wire(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    workload = root / "workloads" / "v1"
    build(SOURCE, workload, WhitespaceCounter())
    config = ExperimentConfig.model_validate(
        {
            "name": "wire",
            "policy_set": POLICIES,
            "sessions": [
                {
                    "experiment_id": "wire",
                    "workload": str(workload),
                    "per_class": 2,
                    "arms": ["a0", "a1", "a2", "a3", "a4"],
                    "concurrency": 4,
                    "seed": 5,
                    "model": "mock",
                    "validation": False,
                    "warmup": 0,
                    "determinism_every": 0,
                }
            ],
        }
    )
    async with _Lab(root, tmp_path) as lab:
        relay = RecordingRelay("127.0.0.1", lab.shaper_port, root)
        port = await relay.serve()
        try:
            session_id = await run_session(
                config.sessions[0], root, "127.0.0.1", port, str(lab.cert), config, relay
            )
        finally:
            await relay.close()
        emissions = lab.emissions()
    session = root / "sessions" / session_id
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["failed"] == 0 and manifest["planned"] == 6 * 5 * 2
    reconstruct(session / "pcap" / "cap.pcapng", session / "observer", session_id)
    map_flows(root, session_id)
    fidelity = validate_emissions(root, session_id)
    assert fidelity["exact_fraction"] == 1.0, fidelity["mismatches"]

    arms = {
        row["request_id"]: row["arm"]
        for row in read_table(root / "labels" / session_id / "labels.parquet").to_pylist()
    }
    records: dict[str, list[int]] = defaultdict(list)
    flows = {
        row["flow_id"]: row["request_id"]
        for row in read_table(session / "observer" / "flow_map.parquet").to_pylist()
    }
    for row in read_table(session / "observer" / "records.parquet").to_pylist():
        if row["dir"] == 1 and row["phase"] == "response":
            records[flows[row["flow_id"]]].append(row["length"])
    buckets = {bucket + 17 for bucket in ladder(FIRST_BUCKET)}
    window = WINDOW_MS * 1_000_000
    merged = 0
    for request_id, arm in arms.items():
        rows = emissions[request_id]
        lengths = records[request_id]
        assert lengths == [int(str(row["framed_len"])) + 17 for row in rows]
        events = [i for i, row in enumerate(rows) if row["kind"] in EVENT_KINDS]
        if arm in ("a1", "a4"):
            # Headers and terminator are fixed-size; every event-carrying record is a bucket.
            assert all(lengths[i] in buckets for i in events)
        if arm in ("a2", "a4"):
            # Timer-driven emissions sit on one grid: deadlines differ by whole windows.
            deadlines = [
                int(str(row["t_emit_ns"])) - int(str(row["timer_lateness_ns"]))
                for row in rows
                if int(str(row["timer_lateness_ns"]))
            ]
            assert all((d - deadlines[0]) % window == 0 for d in deadlines)
            merged += sum(row["kind"] == "merged" for row in rows)
        if arm == "a0":
            assert all(rows[i]["kind"] == "content" for i in events)
        if arm == "a3":
            ticks = lengths[1:-1]
            assert set(ticks) == {RECORD_SIZE + 17} and len(ticks) % QUANTUM == 0
    assert merged > 0
