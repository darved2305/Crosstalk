"""E1 end to end: interleaved defense arms → capture → static and adaptive attackers → costs."""

import json
from collections import defaultdict
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import crosstalk.contracts.parquet as parquet
from crosstalk.capture.reconstruct import map_flows, reconstruct
from crosstalk.cli import app
from crosstalk.contracts.config import ExperimentConfig
from crosstalk.contracts.parquet import read_table
from crosstalk.contracts.tables import schema_for
from crosstalk.loadgen.session import run_session
from crosstalk.testing.recorder import RecordingRelay
from crosstalk.workloads.corpus import build
from crosstalk.workloads.tokens import WhitespaceCounter
from test_defense_wire import POLICIES, _Lab

SOURCE = Path(__file__).parents[1] / "workloads" / "corpus" / "v1" / "corpus.yaml"
ARMS = ["a0", "a1", "a2", "a3", "a4"]


@pytest.mark.asyncio
async def test_e1_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "artifacts"
    workload = root / "workloads" / "v1"
    build(SOURCE, workload, WhitespaceCounter())
    config = ExperimentConfig.model_validate(
        {
            "name": "e1-e2e",
            "policy_set": POLICIES,
            "sessions": [
                {
                    "experiment_id": "e1-e2e",
                    "workload": str(workload),
                    "per_class": 8,
                    "arms": ARMS,
                    "concurrency": 8,
                    "seed": 21,
                    "model": "mock",
                    "validation": False,
                    "warmup": 0,
                    "determinism_every": 0,
                }
            ],
            "analysis": {
                # LightGBM is covered by the E0 end-to-end and unit tests; LR keeps this fast.
                "models": ["chance", "lr"],
                "representations": ["r1"],
                "seeds": 1,
                "bootstrap": 200,
                "primary_model": "lr",
            },
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
    session = root / "sessions" / session_id
    stats = reconstruct(session / "pcap" / "cap.pcapng", session / "observer", session_id)
    assert stats["incomplete_flows"] == 0
    map_flows(root, session_id)

    opened: list[str] = []
    original = parquet.read_table

    def spy(path: Path) -> object:
        opened.append(str(path))
        return original(path)

    import crosstalk.traces.observer as observer
    import crosstalk.traces.oracle as oracle

    for module in (parquet, observer, oracle):
        monkeypatch.setattr(module, "read_table", spy)
    experiment_file = tmp_path / "e1.yaml"
    experiment_file.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    result = CliRunner().invoke(
        app, ["e1", "run", str(experiment_file), "--root", str(root), "--allow-unvalidated"]
    )
    assert result.exit_code == 0, result.output
    assert not [p for p in opened if "restricted" in p or "plan.parquet" in p]

    output = root / "experiments" / "e1-e2e"
    report = (output / "report.md").read_text(encoding="utf-8")
    for heading in (
        "## Primary comparisons",
        "Static-to-adaptive recovery",
        "## All attackers",
        "## Cost",
    ):
        assert heading in report
    assert "NOT REPORTABLE" in report
    assert (output / "figures" / "e1_balanced_accuracy.png").stat().st_size > 10_000

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["arms"] == ARMS
    assert summary["data"]["NAT"]["a3"] == {"traces": 48, "families": 48}
    primary = summary["primary"]["NAT"]
    assert len(primary["tests"]) == 8
    for test in primary["tests"].values():
        assert set(test["holm"]) == {"p", "p_holm", "reject"}
        assert test["delta"]["ci_lo"] <= test["delta"]["value"] <= test["delta"]["ci_hi"]
        assert 0 <= test["delta"]["p"] <= 1
    assert set(primary["retained_leakage"]) == {"a1", "a2", "a3", "a4"}

    costs = summary["costs"]["NAT"]
    assert set(costs) == set(ARMS)
    assert costs["a0"]["pad_bytes_mean"] == 0 and costs["a0"]["dummies_mean"] == 0
    assert costs["a1"]["pad_bytes_mean"] > 0
    assert costs["a1"]["bandwidth_overhead_pct"]["ci_lo"] > 0
    assert costs["a3"]["dummies_mean"] > 0
    assert (
        costs["a3"]["bandwidth_overhead_pct"]["value"]
        > costs["a1"]["bandwidth_overhead_pct"]["value"]
    )
    assert costs["a3"]["timer_lateness_ms"] is not None
    assert all(costs[arm]["itl_ms"] is not None for arm in ARMS)

    evaluations = read_table(output / "evaluations" / "evaluations.parquet")
    assert evaluations.schema == schema_for("evaluations")
    settings = {
        (row["arm"], row["setting"])
        for row in evaluations.to_pylist()
        if row["metric"] == "balanced_accuracy"
    }
    assert settings == {("a0", "static")} | {
        (a, s) for a in ARMS[1:] for s in ("static", "adaptive")
    }

    labels = {
        row["request_id"]: row
        for row in read_table(root / "labels" / session_id / "labels.parquet").to_pylist()
    }
    folds: dict[str, set[int]] = defaultdict(set)
    for row in read_table(output / "predictions" / "predictions.parquet").to_pylist():
        label = labels[row["request_id"]]
        # Predictions for an arm are made only on that arm's traces.
        assert label["arm"] == row["arm"]
        folds[label["family_id"]].add(row["fold"])
    # One family fold map serves every arm and attacker: comparisons are paired.
    assert all(len(f) == 1 for f in folds.values())
    # Each arm and setting is scored on its own traces: logistic regression differs by arm.
    lr = {
        (row["arm"], row["setting"]): row["value"]
        for row in evaluations.to_pylist()
        if row["metric"] == "balanced_accuracy" and row["model"] == "lr"
    }
    assert len(set(lr.values())) > 1
