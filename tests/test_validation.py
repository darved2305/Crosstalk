import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pytest

from crosstalk.capture.reconstruct import map_flows
from crosstalk.capture.validate import evaluate_session, validate_emissions, validate_tshark
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for


def _table(path: Path, name: str, session_id: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(
        pa.Table.from_pylist(
            [{"session_id": session_id, **row} for row in rows], schema=schema_for(name)
        ),
        path,
    )


def test_flow_mapping_and_emission_length(tmp_path: Path) -> None:
    session_id = new_ulid()
    request_id = new_ulid()
    observer = tmp_path / "sessions" / session_id / "observer"
    oracle = tmp_path / "sessions" / session_id / "oracle"
    _table(
        observer / "flow_hints.parquet",
        "flow_hints",
        session_id,
        [{"request_id": request_id, "client_port": 51000, "t_connect_ns": 100}],
    )
    _table(
        observer / "flows.parquet",
        "flows",
        session_id,
        [
            {
                "flow_id": 0,
                "client_port": 51000,
                "server_port": 8443,
                "t_syn_ns": 200,
                "t_last_ns": 300,
                "bytes_c2s": 0,
                "bytes_s2c": 0,
                "packets_c2s": 1,
                "packets_s2c": 1,
                "records_c2s": 0,
                "records_s2c": 1,
                "retrans_bytes_c2s": 0,
                "retrans_bytes_s2c": 0,
                "max_ip_len": 100,
                "cipher_suite": None,
                "complete": True,
                "anomalies": [],
            }
        ],
    )
    _table(
        observer / "records.parquet",
        "records",
        session_id,
        [
            {
                "flow_id": 0,
                "dir": 1,
                "idx": 0,
                "t_start_ns": 220,
                "t_end_ns": 230,
                "content_type": 23,
                "length": 117,
                "stream_offset": 0,
                "phase": "response",
            }
        ],
    )
    _table(
        oracle / "emissions.parquet",
        "emissions",
        session_id,
        [
            {
                "request_id": request_id,
                "seq": 0,
                "t_emit_ns": 10,
                "kind": "content",
                "framed_len": 100,
                "content_bytes": 95,
                "pad_bytes": 0,
                "n_events": 1,
                "queue_delay_ns": 0,
                "timer_lateness_ns": 0,
                "arm": "a0",
            }
        ],
    )
    assert map_flows(tmp_path, session_id) == 1
    fidelity = validate_emissions(tmp_path, session_id)
    assert fidelity["exact_fraction"] == 1.0
    (tmp_path / "sessions" / session_id / "capture.json").write_text(
        json.dumps(
            {
                "kernel_drops": 1,
                "interface_drops": 0,
                "os_drops": 0,
                "exit_code": 0,
                "offloads": "generic-receive-offload: off\nlarge-receive-offload: off\ntcp-segmentation-offload: off\ngeneric-segmentation-offload: off",
                "mtu": 1500,
            }
        )
    )
    (tmp_path / "sessions" / session_id / "manifest.json").write_text(
        json.dumps({"git_dirty": False, "failed": 0, "planned": 1})
    )
    validation = tmp_path / "sessions" / session_id / "validation"
    (validation / "tshark_agreement.json").write_text(json.dumps({"exact": True}))
    outcome = evaluate_session(tmp_path, session_id)
    assert outcome["status"] == "INVALID"
    assert "kernel_drops nonzero" in outcome["reasons"]


def test_tshark_tls13_opaque_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = new_ulid()
    observer = tmp_path / "sessions" / session_id / "observer"
    _table(observer / "flows.parquet", "flows", session_id, [{"flow_id": 0, "client_port": 51000}])
    _table(
        observer / "records.parquet",
        "records",
        session_id,
        [
            {
                "flow_id": 0,
                "dir": 1,
                "idx": index,
                "t_end_ns": 1_000_000_000,
                "content_type": content_type,
                "length": length,
            }
            for index, (content_type, length) in enumerate(
                ((22, 122), (20, 1), (23, 38), (23, 363))
            )
        ],
    )
    output = "6\t1.000000000\t8443\t51000\t22,20\t23,23\t122,1,38,363\n"
    monkeypatch.setattr(
        "crosstalk.capture.validate.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, output, ""),
    )
    result = validate_tshark(tmp_path, session_id, tmp_path / "capture.pcapng")
    assert result["exact"] is True
    assert result["ours"] == result["tshark"] == 4
