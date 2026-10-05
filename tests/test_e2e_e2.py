"""E2 end to end: alternating load blocks, stream-interval variants, two replicate sessions."""

import asyncio
import json
import math
from collections import defaultdict
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from crosstalk.capture.reconstruct import map_flows, reconstruct
from crosstalk.cli import app
from crosstalk.contracts.config import ExperimentConfig
from crosstalk.contracts.parquet import read_table
from crosstalk.contracts.tables import schema_for
from crosstalk.loadgen.session import run_session
from crosstalk.shaper.server import ShaperSettings, run
from crosstalk.testing.mock_upstream import run as run_mock
from crosstalk.testing.recorder import RecordingRelay
from crosstalk.workloads.corpus import build
from crosstalk.workloads.tokens import WhitespaceCounter
from test_defense_wire import POLICIES
from test_integration import _certificate, _port, _wait_port

SOURCE = Path(__file__).parents[1] / "workloads" / "corpus" / "v1" / "corpus.yaml"
LEVELS = [1, 4]


def _config(workload: Path) -> ExperimentConfig:
    session = {
        "experiment_id": "e2-e2e",
        "workload": str(workload),
        "classes": ["cooking", "legal", "medical"],
        "per_class": 6,
        "arms": ["a0", "a2", "a3"],
        "concurrency_levels": LEVELS,
        "concurrency": max(LEVELS),
        "cycles": 2,
        "stream_interval_variants": [4],
        "stream_interval_arms": ["a0", "a2"],
        "model": "mock",
        "validation": False,
        "warmup": 2,
        "determinism_every": 10,
    }
    return ExperimentConfig.model_validate(
        {
            "name": "e2-e2e",
            "policy_set": POLICIES,
            "sessions": [{**session, "seed": 31}, {**session, "seed": 32}],
            "analysis": {
                "models": ["chance", "lr"],
                "representations": ["r1"],
                "seeds": 1,
                "bootstrap": 200,
                "primary_model": "lr",
            },
        }
    )


async def _collect(root: Path, config: ExperimentConfig, tmp: Path) -> list[str]:
    cert, key = _certificate(tmp)
    policy_file = tmp / "policies.yaml"
    policy_file.write_text(yaml.safe_dump(POLICIES), encoding="utf-8")
    mock_port, shaper_port, admin_port = _port(), _port(), _port()
    tasks = [
        asyncio.create_task(run_mock("127.0.0.1", mock_port, token_delay_ms=0.5, load_factor=0.5))
    ]
    await _wait_port(mock_port)
    settings = ShaperSettings(
        host="127.0.0.1",
        port=shaper_port,
        admin_host="127.0.0.1",
        admin_port=admin_port,
        cert=cert,
        key=key,
        upstream_url=f"http://127.0.0.1:{mock_port}",
        oracle_dir=root / "shaper-log",
        policy_file=policy_file,
    )
    tasks.append(asyncio.create_task(run(settings)))
    sessions: list[str] = []
    relay = RecordingRelay("127.0.0.1", shaper_port, root)
    try:
        await _wait_port(admin_port)
        port = await relay.serve()
        for plan in config.sessions:
            sessions.append(
                await run_session(plan, root, "127.0.0.1", port, str(cert), config, relay)
            )
    finally:
        await relay.close()
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return sessions


@pytest.mark.asyncio
async def test_e2_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    workload = root / "workloads" / "v1"
    build(SOURCE, workload, WhitespaceCounter())
    config = _config(workload)
    sessions = await _collect(root, config, tmp_path)

    for session_id in sessions:
        session = root / "sessions" / session_id
        manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["failed"] == 0
        assert [(b["cblock"], b["concurrency"]) for b in manifest["blocks"]] == [
            (0, 1),
            (1, 4),
            (2, 1),
            (3, 4),
        ]
        plan = {r["request_id"]: r for r in read_table(session / "plan.parquet").to_pylist()}
        requests = read_table(session / "oracle" / "requests.parquet").to_pylist()
        by_block: dict[int, list[dict[str, object]]] = defaultdict(list)
        for row in requests:
            by_block[int(plan[row["request_id"]]["cblock"])].append(row)
        for block in range(1, 4):
            # Drained between blocks: nothing of block k+1 starts before block k is done.
            last_done = max(int(str(r["t_done_ns"])) for r in by_block[block - 1])
            first_start = min(int(str(r["t_connect_ns"])) for r in by_block[block])
            assert first_start >= last_done
        events: dict[str, int] = defaultdict(int)
        for row in read_table(session / "oracle" / "client_events.parquet").to_pylist():
            if row["is_content"]:
                events[row["request_id"]] += 1
        for row in requests:
            interval = int(plan[row["request_id"]]["stream_interval"])
            arm = plan[row["request_id"]]["arm"]
            tokens = int(str(row["completion_tokens"]))
            if arm == "a0":
                # Passthrough shows vLLM's aggregation: first token alone, then groups.
                assert events[row["request_id"]] == 1 + math.ceil((tokens - 1) / interval)
        stats = reconstruct(session / "pcap" / "cap.pcapng", session / "observer", session_id)
        assert stats["incomplete_flows"] == 0
        map_flows(root, session_id)

    experiment_file = tmp_path / "e2.yaml"
    experiment_file.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    runner = CliRunner()
    refused = runner.invoke(
        app, ["e1", "run", str(experiment_file), "--root", str(root), "--allow-unvalidated"]
    )
    assert refused.exit_code != 0
    assert "expects one concurrency" in str(refused.exception)
    result = runner.invoke(
        app, ["e2", "run", str(experiment_file), "--root", str(root), "--allow-unvalidated"]
    )
    assert result.exit_code == 0, result.output

    output = root / "experiments" / "e2-e2e"
    report = (output / "report.md").read_text(encoding="utf-8")
    for heading in (
        "## Primary contrasts",
        "## Leakage per load level",
        "## Server-side aggregation",
        "## Cost per load level",
        "## Determinism probes per block",
    ):
        assert heading in report
    assert (output / "figures" / "e2_adaptive_balanced_accuracy.png").stat().st_size > 10_000

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["levels"] == LEVELS and summary["base_interval"] == 1
    assert summary["conditions"] == ["a0", "a0/si4", "a2", "a2/si4", "a3"]
    assert len(summary["sessions"]) == 2
    for level in map(str, LEVELS):
        for condition in summary["conditions"]:
            # 2 sessions x 3 classes x 6 prompts; each session draws 18 distinct families,
            # and replicate sessions (different seeds) mostly draw different ones.
            info = summary["data"][level][condition]
            assert info["traces"] == 36 and 18 <= info["families"] <= 36
    tests = summary["contrasts"]["tests"]
    assert set(tests) == {"a0 leakage", "a2 adaptive rho", "a3 adaptive rho"}
    assert all({"p", "p_holm", "reject"} <= set(t["holm"]) for t in tests.values())
    assert summary["aggregation"]
    costs = summary["costs"]
    assert set(costs) == {"1", "4"}
    assert costs["1"]["a3"]["dummies_mean"] > 0
    # The mock's load model slows generation: duration grows with concurrency.
    assert costs["4"]["a0"]["duration_ms"]["p50"] > costs["1"]["a0"]["duration_ms"]["p50"]
    assert {(row["cblock"], row["concurrency"]) for row in summary["probes"]} <= {
        (0, 1),
        (1, 4),
        (2, 1),
        (3, 4),
    }

    frontier = read_table(output / "frontier" / "frontier.parquet")
    assert frontier.schema == schema_for("frontier")
    assert frontier.num_rows == len(LEVELS) * 4 * 2
    evaluations = read_table(output / "evaluations" / "evaluations.parquet")
    assert set(evaluations.column("concurrency").to_pylist()) == set(LEVELS)


def test_condition_naming_and_base_interval() -> None:
    import numpy as np

    from crosstalk.common.errors import ConfigError
    from crosstalk.evaluation.e2 import _base_interval, condition_of
    from crosstalk.features.traces import Trace, TraceSet

    def trace(arm: str, interval: int) -> Trace:
        empty = np.zeros(0)
        return Trace(
            "S",
            "R",
            empty,
            empty.astype(np.int64),
            empty,
            empty.astype(np.int64),
            empty.astype(np.int64),
            empty,
            0,
            {"arm": arm, "stream_interval": interval},
        )

    mixed = TraceSet(
        [trace("a0", 1), trace("a0", 4), trace("a2", 1), trace("a2", 4), trace("a3", 1)]
    )
    assert _base_interval(mixed) == 1
    assert [condition_of(t, 1) for t in mixed.traces] == ["a0", "a0/si4", "a2", "a2/si4", "a3"]
    with pytest.raises(ConfigError, match="shared by every arm"):
        _base_interval(TraceSet([trace("a0", 1), trace("a2", 4)]))
    # Two intervals shared by every arm make the base ambiguous.
    with pytest.raises(ConfigError):
        _base_interval(TraceSet([trace("a0", 1), trace("a0", 4), trace("a2", 1), trace("a2", 4)]))
