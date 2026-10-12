"""API → PostgreSQL queue → production worker → collection → analysis → report.

Opt-in: set CT_TEST_DATABASE_URL to a PostgreSQL server URL whose role may create
databases. Each test creates and migrates its own database and drops it afterwards.
The worker uses the local development lab (synthetic upstream, real TLS shaper,
recording relay), so sessions are PARTIAL and reports NOT REPORTABLE by construction.
"""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from crosstalk.api.app import ApiSettings, create_app
from crosstalk.common.ids import new_ulid
from crosstalk.orchestration.pipeline import PipelineSettings
from crosstalk.orchestration.runner import serve

DATABASE_URL = os.environ.get("CT_TEST_DATABASE_URL")
ROOT = Path(__file__).parents[1]
TOKEN = "o" * 32
ORACLE = "r" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="CT_TEST_DATABASE_URL is not set")


@pytest.fixture
def database() -> Iterator[str]:
    assert DATABASE_URL is not None
    server = make_url(DATABASE_URL)
    name = f"crosstalk_test_{new_ulid().lower()}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "CT_API_DATABASE_URL": url},
            check=True,
            capture_output=True,
        )
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _client(url: str, root: Path) -> TestClient:
    settings = ApiSettings(
        database_url=url,
        operator_token=TOKEN,
        oracle_token=ORACLE,
        code_commit="test",
        artifacts_root=root,
    )
    return TestClient(create_app(settings=settings))


def _drain(url: str, root: Path) -> int:
    settings = PipelineSettings(
        database_url=url,
        artifacts_root=root,
        code_commit="test",
        corpus_source=ROOT / "workloads" / "corpus" / "v1" / "corpus.yaml",
        mock_token_delay_ms=0.2,
        poll_seconds=0.1,
    )
    return asyncio.run(serve(settings, asyncio.Event(), once=True))


def _run(client: TestClient, config: dict[str, object]) -> str:
    created = client.post("/v1/experiments", json=config, headers=HEADERS)
    assert created.status_code == 201, created.text
    experiment_id = str(created.json()["id"])
    run = client.post(
        f"/v1/experiments/{experiment_id}/runs",
        headers={**HEADERS, "Idempotency-Key": new_ulid()},
    )
    assert run.status_code == 202, run.text
    return experiment_id


def test_dashboard_run_executes_every_stage(database: str, tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    client = _client(database, root)
    plan = {
        "experiment_id": "pipeline-e0",
        "workload": "workloads/v1",
        "per_class": 6,
        "concurrency": 4,
        "seed": 41,
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "validation": False,
        "warmup": 0,
        "determinism_every": 0,
    }
    experiment_id = _run(
        client,
        {
            "name": "pipeline-e0",
            "sessions": [plan, {**plan, "seed": 42}],
            "analysis": {
                "models": ["chance", "lr"],
                "null_models": ["lr"],
                "seeds": 1,
                "bootstrap": 100,
                "null_permutations": 0,
                "null_bootstrap": 100,
                "learning_curve": False,
                "primary_model": "lr",
            },
        },
    )
    queued = client.get(f"/v1/experiments/{experiment_id}", headers=HEADERS).json()
    assert queued["state"] == "running"
    assert [job["kind"] for s in queued["sessions"] for job in s["jobs"]] == ["plan", "plan"]

    # 2 sessions x (plan, collect, reconstruct, validate) + analyze + report.
    assert _drain(database, root) == 10

    detail = client.get(f"/v1/experiments/{experiment_id}", headers=HEADERS).json()
    assert detail["state"] == "partial"
    for session in detail["sessions"]:
        assert session["state"] == "PARTIAL"
        assert [job["kind"] for job in session["jobs"]] == [
            "plan",
            "collect",
            "reconstruct",
            "validate",
        ]
        assert {job["state"] for job in session["jobs"]} == {"succeeded"}
        manifest = (root / "sessions" / session["id"] / "manifest.json").read_text("utf-8")
        assert '"upstream_kind": "mock"' in manifest
        assert '"model_id": "mock"' in manifest
    assert [(job["kind"], job["state"]) for job in detail["jobs"]] == [
        ("analyze", "succeeded"),
        ("report", "succeeded"),
    ]
    kinds = {event["kind"] for event in detail["events"]}
    assert {"session_collected", "session_validated", "report_published"} <= kinds

    report = client.get(f"/v1/experiments/{experiment_id}/report", headers=HEADERS)
    assert report.status_code == 200
    assert "NOT REPORTABLE" in report.text
    results = client.get(f"/v1/experiments/{experiment_id}/results", headers=HEADERS).json()
    assert results["reportable"] is False
    assert any(row["model"] == "lr" for row in results["rows"])
    session_id = detail["sessions"][0]["id"]
    assert (root / "experiments" / experiment_id / "index.json").is_file()
    assert (root / "sessions" / session_id / "observer" / "flow_map.parquet").is_file()

    # A rerun of the same configuration is a new experiment and never pools sessions.
    assert _drain(database, root) == 0


def test_validation_experiment_and_cancellation(database: str, tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    client = _client(database, root)
    session = {"experiment_id": "pipeline-validation", "requests": 6, "concurrency": 3, "seed": 5}
    experiment_id = _run(
        client, {"name": "pipeline-validation", "sessions": [session], "kind": "validation"}
    )
    cancelled_id = _run(client, {"name": "pipeline-cancel", "sessions": [session]})
    response = client.post(f"/v1/experiments/{cancelled_id}/cancel", headers=HEADERS)
    assert response.status_code == 200

    drained = _drain(database, root)
    detail = client.get(f"/v1/experiments/{experiment_id}", headers=HEADERS).json()
    assert drained == 6, json.dumps(detail)[:3000]

    detail = client.get(f"/v1/experiments/{experiment_id}", headers=HEADERS).json()
    assert detail["state"] == "partial"
    report = client.get(f"/v1/experiments/{experiment_id}/report", headers=HEADERS).text
    assert "NOT REPORTABLE" in report and "recording-relay" in report
    cancelled = client.get(f"/v1/experiments/{cancelled_id}", headers=HEADERS).json()
    assert cancelled["state"] == "cancelled"
    assert cancelled["sessions"][0]["state"] == "CANCELLED"
    assert not (root / "sessions" / cancelled["sessions"][0]["id"]).exists()
