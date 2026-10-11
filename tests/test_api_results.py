import shutil
from pathlib import Path

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from crosstalk.api.app import ApiSettings, create_app
from crosstalk.api.results import DuckDBResultStore, ResultNotFoundError
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for
from test_api import (
    EXPERIMENT_ID,
    ORACLE_TOKEN,
    SESSION_ID,
    TOKEN,
    FakeControlStore,
    auth,
)

REQUEST_ID = "01K00000000000000000000004"


def _write(root: Path, relative: str, table: str, rows: list[dict[str, object]]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows, schema=schema_for(table)), path)


def result_fixture(root: Path, points: int = 4) -> DuckDBResultStore:
    _write(
        root,
        f"experiments/{EXPERIMENT_ID}/evaluations/evaluations.parquet",
        "evaluations",
        [
            {
                "session_id": SESSION_ID,
                "experiment_id": EXPERIMENT_ID,
                "regime": "NAT",
                "concurrency": 1,
                "task": "t1",
                "representation": "r1",
                "arm": "a1",
                "setting": "adaptive",
                "model": "lr",
                "metric": "balanced_accuracy",
                "value": 0.6,
                "ci_lo": 0.5,
                "ci_hi": 0.7,
                "n": 100,
                "n_families": 30,
                "B": 2000,
            }
        ],
    )
    _write(
        root,
        f"experiments/{EXPERIMENT_ID}/frontier/frontier.parquet",
        "frontier",
        [
            {
                "session_id": None,
                "experiment_id": EXPERIMENT_ID,
                "concurrency": 1,
                "condition": "a1",
                "setting": "adaptive",
                "model": "lr",
                "representation": "r1",
                "balanced_accuracy": 0.6,
                "rho": 0.4,
                "rho_lo": 0.3,
                "rho_hi": 0.5,
                "bandwidth_overhead_pct": 20.0,
                "ttft_p95_ms": 50.0,
                "itl_p99_ms": 100.0,
            }
        ],
    )
    _write(
        root,
        f"sessions/{SESSION_ID}/observer/flow_map.parquet",
        "flow_map",
        [{"session_id": SESSION_ID, "flow_id": 1, "request_id": REQUEST_ID}],
    )
    _write(
        root,
        f"sessions/{SESSION_ID}/observer/records.parquet",
        "records",
        [
            {
                "session_id": SESSION_ID,
                "flow_id": 1,
                "dir": 1,
                "idx": index,
                "t_start_ns": index * 10,
                "t_end_ns": index * 10 + 5,
                "content_type": 23,
                "length": 100 + index % 17,
                "stream_offset": index * 100,
                "phase": "response",
            }
            for index in range(points)
        ],
    )
    _write(
        root,
        f"sessions/{SESSION_ID}/oracle/emissions.parquet",
        "emissions",
        [
            {
                "session_id": SESSION_ID,
                "request_id": REQUEST_ID,
                "seq": 1,
                "t_emit_ns": 100,
                "kind": "content",
                "framed_len": 128,
                "content_bytes": 8,
                "pad_bytes": 0,
                "n_events": 1,
                "queue_delay_ns": 0,
                "timer_lateness_ns": 0,
                "arm": "a1",
            }
        ],
    )
    return DuckDBResultStore(root)


def api_client(root: Path, points: int = 4) -> TestClient:
    return TestClient(
        create_app(
            FakeControlStore(),
            ApiSettings(operator_token=TOKEN, oracle_token=ORACLE_TOKEN),
            result_fixture(root, points),
        )
    )


def test_result_and_frontier_queries(tmp_path: Path) -> None:
    with api_client(tmp_path) as api:
        results = api.get(f"/v1/experiments/{EXPERIMENT_ID}/results", headers=auth())
        frontier = api.get(
            f"/v1/experiments/{EXPERIMENT_ID}/frontier?setting=adaptive&cost=bandwidth",
            headers=auth(),
        )
    assert results.status_code == frontier.status_code == 200
    assert results.json()["rows"][0]["metric"] == "balanced_accuracy"
    assert frontier.json()["rows"][0]["rho"] == pytest.approx(0.4)


def test_trace_query_and_large_trace_downsampling(tmp_path: Path) -> None:
    store = result_fixture(tmp_path, 20_101)
    result = store.trace(SESSION_ID, REQUEST_ID, "records")
    assert result["source_points"] == 20_101
    assert result["downsampled"] is True
    assert len(result["points"]) <= 20_000
    points = result["points"]
    assert points[0]["t_start_ns"] == 5
    assert points[-1]["t_end_ns"] == 201_005
    assert sum(int(point["count"]) for point in points) == 20_101


def test_oracle_emissions_require_backend_scope(tmp_path: Path) -> None:
    path = f"/v1/sessions/{SESSION_ID}/traces/{REQUEST_ID}/emissions"
    with api_client(tmp_path) as api:
        denied = api.get(path, headers=auth())
        allowed = api.get(path, headers=auth(ORACLE_TOKEN))
    assert denied.status_code == 403
    assert denied.headers["content-type"] == "application/problem+json"
    assert allowed.status_code == 200
    assert allowed.json()["oracle"] is True
    assert "validation-only" in allowed.json()["warning"].lower()


def test_artifact_paths_are_contained_and_missing_is_explicit(tmp_path: Path) -> None:
    store = DuckDBResultStore(tmp_path)
    try:
        store.evaluations("../../outside")
    except ResultNotFoundError as error:
        assert "escapes" in str(error)
    else:
        raise AssertionError("path traversal was accepted")


def test_report_requires_operator_and_reads_existing_artifact(tmp_path: Path) -> None:
    path = tmp_path / "experiments" / "api-test" / "report.md"
    path.parent.mkdir(parents=True)
    path.write_text("# E1 report\n\nMeasured evidence.\n", encoding="utf-8")
    url = f"/v1/experiments/{EXPERIMENT_ID}/report"
    with api_client(tmp_path) as api:
        assert api.get(url).status_code == 401
        response = api.get(url, headers=auth())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# E1 report")
    path.unlink()
    with api_client(tmp_path) as api:
        missing = api.get(url, headers=auth())
    assert missing.status_code == 404


def test_results_resolve_name_based_evaluation_output(tmp_path: Path) -> None:
    result_fixture(tmp_path)
    source = tmp_path / "experiments" / EXPERIMENT_ID
    target = tmp_path / "experiments" / "api-test"
    shutil.move(str(source), str(target))
    with api_client(tmp_path) as api:
        results = api.get(f"/v1/experiments/{EXPERIMENT_ID}/results", headers=auth())
        frontier = api.get(f"/v1/experiments/{EXPERIMENT_ID}/frontier", headers=auth())
    assert results.status_code == 200
    assert frontier.status_code == 200
    assert len(results.json()["rows"]) == len(frontier.json()["rows"]) == 1
