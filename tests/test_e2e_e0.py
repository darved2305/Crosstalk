"""E0 end to end: corpus → plan → TLS sessions → capture → reconstruction → E0 report.

Capture uses the test relay (no dumpcap on this host), so segment boundaries are synthetic;
everything downstream of the pcapng is production code.
"""

import asyncio
import json
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import crosstalk.contracts.parquet as parquet
from crosstalk.capture.reconstruct import map_flows, reconstruct
from crosstalk.capture.validate import validate_emissions
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
from test_integration import _certificate, _port, _wait_port

SOURCE = Path(__file__).parents[1] / "workloads" / "corpus" / "v1" / "corpus.yaml"
# A template phrase every session's determinism probes contain: it may exist in the
# restricted text store, and nowhere an attacker can read.
CANARY = b"simple terms"


def _experiment(workload: Path) -> ExperimentConfig:
    session = {
        "experiment_id": "e0-e2e",
        "workload": str(workload),
        "per_class": 10,
        "concurrency": 6,
        "model": "mock",
        "validation": False,
        "warmup": 2,
        "determinism_every": 8,
    }
    return ExperimentConfig.model_validate(
        {
            "name": "e0-e2e",
            "sessions": [
                {**session, "regime": "NAT", "seed": 11},
                {**session, "regime": "MATCH-P", "seed": 12},
                {**session, "regime": "MATCH-PR", "seed": 13, "target_lengths": [24, 40]},
            ],
            "analysis": {
                "models": ["chance", "majority", "lr", "lgbm"],
                "seeds": 1,
                "bootstrap": 200,
                "null_permutations": 2,
                "null_models": ["lr"],
                "null_bootstrap": 100,
                "learning_curve": True,
            },
        }
    )


async def _collect(root: Path, config: ExperimentConfig, tmp: Path) -> list[str]:
    cert, key = _certificate(tmp)
    mock_port, shaper_port, admin_port = _port(), _port(), _port()
    mock = asyncio.create_task(run_mock("127.0.0.1", mock_port, token_delay_ms=0.2))
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
    )
    shaper = asyncio.create_task(run(settings))
    relay = RecordingRelay("127.0.0.1", shaper_port, root)
    sessions: list[str] = []
    try:
        await _wait_port(admin_port)
        relay_port = await relay.serve()
        for plan in config.sessions:
            sessions.append(
                await run_session(
                    plan,
                    root,
                    host="127.0.0.1",
                    port=relay_port,
                    ca_file=str(cert),
                    experiment=config,
                    capture=relay,
                )
            )
    finally:
        await relay.close()
        for task in (shaper, mock):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return sessions


@pytest.mark.asyncio
async def test_e0_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "artifacts"
    workload = root / "workloads" / "v1"
    build(SOURCE, workload, WhitespaceCounter())
    config = _experiment(workload)
    sessions = await _collect(root, config, tmp_path)
    assert len(sessions) == 3

    for session_id, plan in zip(sessions, config.sessions, strict=True):
        session = root / "sessions" / session_id
        manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["failed"] == 0
        assert (
            manifest["completed"]
            == manifest["planned"]
            == len(read_table(session / "plan.parquet"))
        )
        assert (
            manifest["workload_sha256"]
            == json.loads((workload / "manifest.json").read_text(encoding="utf-8"))[
                "workload_sha256"
            ]
        )
        if plan.regime.value == "MATCH-PR":
            assert manifest["validation"] == {"length_forcing": True}
        labels = read_table(root / "labels" / session_id / "labels.parquet")
        assert labels.schema == schema_for("labels")
        assert set(labels.column("regime").to_pylist()) == {plan.regime.value}

        stats = reconstruct(session / "pcap" / "cap.pcapng", session / "observer", session_id)
        assert stats["incomplete_flows"] == 0
        assert stats["flows"] == manifest["planned"]
        assert map_flows(root, session_id) == manifest["planned"]
        fidelity = validate_emissions(root, session_id)
        assert fidelity["exact_fraction"] == 1.0, fidelity["mismatches"]

    opened: list[str] = []
    original = parquet.read_table

    def spy(path: Path) -> object:
        opened.append(str(path))
        return original(path)

    monkeypatch.setattr(parquet, "read_table", spy)
    import crosstalk.traces.observer as observer

    monkeypatch.setattr(observer, "read_table", spy)
    experiment_file = tmp_path / "e0.yaml"
    experiment_file.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    runner = CliRunner()
    refused = runner.invoke(app, ["e0", "run", str(experiment_file), "--root", str(root)])
    assert refused.exit_code != 0
    assert "not reportable" in str(refused.exception)
    result = runner.invoke(
        app, ["e0", "run", str(experiment_file), "--root", str(root), "--allow-unvalidated"]
    )
    assert result.exit_code == 0, result.output
    assert opened
    assert not [p for p in opened if "oracle" in p or "restricted" in p or "plan.parquet" in p]

    output = root / "experiments" / "e0-e2e"
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "NOT REPORTABLE" in report
    for heading in (
        "## T1",
        "## T2",
        "## Null control",
        "## Noise floor",
        "## Power check",
        "## Learning curve",
    ):
        assert heading in report
    assert (output / "figures" / "e0_balanced_accuracy.png").stat().st_size > 10_000
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["regimes"] == ["NAT", "MATCH-P", "MATCH-PR"]
    assert summary["reportable"] is False
    assert summary["sensitive_class"] == "medical"
    assert summary["excluded"] == {}
    for regime in summary["regimes"]:
        assert summary["data"][regime]["per_class"] == {c: 10 for c in summary["classes"]}
        assert summary["null_control"][regime]["lr"]["permutations"] == 2
        assert summary["noise_floor"][regime]["n"] >= 6

    evaluations = read_table(output / "evaluations" / "evaluations.parquet")
    assert evaluations.schema == schema_for("evaluations")
    rows = evaluations.to_pylist()
    ba = [r for r in rows if r["metric"] == "balanced_accuracy" and r["task"] == "t1"]
    assert len(ba) == 3 * 3 * 4
    assert all(0 <= r["value"] <= 1 and r["ci_lo"] <= r["value"] <= r["ci_hi"] for r in ba)
    assert {r["B"] for r in rows} == {200}

    predictions = read_table(output / "predictions" / "predictions.parquet")
    assert predictions.schema == schema_for("predictions")
    labels = {
        row["request_id"]: row["family_id"]
        for session_id in sessions
        for row in read_table(root / "labels" / session_id / "labels.parquet").to_pylist()
    }
    folds: dict[tuple[str, str], set[int]] = {}
    for row in predictions.to_pylist():
        folds.setdefault((row["regime"], labels[row["request_id"]]), set()).add(row["fold"])
    assert all(len(f) == 1 for f in folds.values()), "a family appears in two outer folds"

    leaked = [
        path
        for directory in (
            root / "experiments",
            *(root / "sessions" / s / "observer" for s in sessions),
        )
        for path in directory.rglob("*")
        if path.is_file() and CANARY in path.read_bytes()
    ]
    assert not leaked
    restricted = b"".join((root / "restricted" / s / "text.parquet").read_bytes() for s in sessions)
    assert CANARY in restricted
