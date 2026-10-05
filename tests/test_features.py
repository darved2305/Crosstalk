from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from crosstalk.common.errors import ConfigError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for
from crosstalk.features.extract import R1_NAMES, R2_NAMES, TLEN_FEATURES, matrix, r1, r2
from crosstalk.features.traces import Trace, TraceSet, load_traces

MS = 1_000_000


def _trace(
    response: list[tuple[int, int, int]],
    request: list[tuple[int, int]] | None = None,
) -> Trace:
    request = request if request is not None else [(200, 1 * MS), (50, 2 * MS)]
    posts = sorted(
        [(end, -size) for size, end in request] + [(end, size) for size, _, end in response]
    )
    return Trace(
        "S",
        "R",
        np.asarray([s for s, _ in request], dtype=np.float64),
        np.asarray([e for _, e in request], dtype=np.int64),
        np.asarray([s for s, _, _ in response], dtype=np.float64),
        np.asarray([b for _, b, _ in response], dtype=np.int64),
        np.asarray([e for _, _, e in response], dtype=np.int64),
        np.asarray([v for _, v in posts], dtype=np.float64),
        0,
        {"class": "c", "family_id": "f", "role": "main", "arm": "a0", "regime": "NAT"},
    )


GOLDEN = _trace([(100, 10 * MS, 11 * MS), (120, 15 * MS, 16 * MS), (300, 60 * MS, 61 * MS)])


def test_r1_golden_vector() -> None:
    values = dict(zip(R1_NAMES, r1(GOLDEN), strict=True))
    assert len(R1_NAMES) == 87
    expected = {
        "req_bytes": 250,
        "req_count": 2,
        "resp_count": 3,
        "resp_bytes": 520,
        "size_min": 100,
        "size_max": 300,
        "size_q50": 120,
        "gap_q50": 25,
        "gap_mean": 25,
        "gap_cv": 0.8,
        "ttft_ms": 8,
        "duration_ms": 51,
        "burst_count": 2,
        "burst_mean_bytes": 260,
        "burst_max_bytes": 300,
        "size_hist05": 2,
        "size_hist09": 1,
        "first_size00": 100,
        "first_size02": 300,
        "first_size03": 0,
        "first_gap00": 5,
        "first_gap01": 45,
        "first_gap02": 0,
        "last_size0": 100,
        "last_size2": 300,
        "last_size4": 0,
    }
    for name, value in expected.items():
        assert values[name] == pytest.approx(value), name
    assert values["size_mean"] == pytest.approx(520 / 3)
    assert values["records_per_s"] == pytest.approx(3 / 0.051)
    assert sum(values[f"size_hist{i:02d}"] for i in range(16)) == 3


def test_r2_golden_vector() -> None:
    values = r2(GOLDEN)
    assert len(values) == len(R2_NAMES) == 104
    assert values[0] == pytest.approx(-200)
    assert values[99] == pytest.approx(270)
    assert list(values[100:]) == [3, 2, 520, 250]
    assert np.all(np.diff(values[:100])[-10:] >= 0)


def test_single_record_and_no_request_records() -> None:
    trace = _trace([(42, 5 * MS, 6 * MS)], request=[])
    one = dict(zip(R1_NAMES, r1(trace), strict=True))
    assert one["resp_count"] == 1
    assert one["gap_mean"] == one["gap_cv"] == one["ttft_ms"] == 0
    assert one["burst_count"] == 1
    assert np.isfinite(r1(trace)).all()
    assert np.all(r2(trace)[:100] == 42)


def test_tlen_is_a_length_only_subset() -> None:
    traces = TraceSet([GOLDEN, GOLDEN])
    x, names = matrix(traces, "tlen")
    assert names == TLEN_FEATURES
    assert x.shape == (2, 5)
    assert list(x[0]) == [250, 2, 3, 520, 51]
    with pytest.raises(ConfigError):
        matrix(traces, "r9")
    assert matrix(TraceSet(), "r1")[0].shape == (0, 87)


def _write(path: Path, table: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows, schema=schema_for(table)), path)


def test_loader_excludes_incomplete_and_unlabelled_flows(tmp_path: Path) -> None:
    session = new_ulid()
    good, broken, unmapped = new_ulid(), new_ulid(), new_ulid()
    observer = tmp_path / "sessions" / session / "observer"
    flow = {
        "session_id": session,
        "server_port": 8443,
        "t_last_ns": 9,
        "bytes_c2s": 1,
        "bytes_s2c": 1,
        "packets_c2s": 1,
        "packets_s2c": 1,
        "records_c2s": 1,
        "records_s2c": 1,
        "retrans_bytes_c2s": 0,
        "retrans_bytes_s2c": 0,
        "max_ip_len": 100,
        "cipher_suite": None,
    }
    _write(
        observer / "flows.parquet",
        "flows",
        [
            {
                **flow,
                "flow_id": 0,
                "client_port": 1,
                "t_syn_ns": 5,
                "complete": True,
                "anomalies": [],
            },
            {
                **flow,
                "flow_id": 1,
                "client_port": 2,
                "t_syn_ns": 6,
                "complete": False,
                "anomalies": ["tcp_hole:1"],
            },
        ],
    )
    records = []
    for flow_id in (0, 1):
        for index, (direction, phase, length, end) in enumerate(
            [
                (-1, "handshake", 300, 1),
                (-1, "request", 250, 2),
                (1, "response", 90, 3),
                (1, "response", 95, 4),
            ]
        ):
            records.append(
                {
                    "session_id": session,
                    "flow_id": flow_id,
                    "dir": direction,
                    "idx": index,
                    "t_start_ns": end * MS,
                    "t_end_ns": end * MS,
                    "content_type": 23,
                    "length": length,
                    "stream_offset": 0,
                    "phase": phase,
                }
            )
    _write(observer / "records.parquet", "records", records)
    _write(
        observer / "flow_map.parquet",
        "flow_map",
        [
            {"session_id": session, "flow_id": 0, "request_id": good},
            {"session_id": session, "flow_id": 1, "request_id": broken},
        ],
    )
    label = {
        "session_id": session,
        "prompt_id": "p",
        "class": "c",
        "family_id": "f",
        "template_id": "t0",
        "regime": "NAT",
        "role": "main",
        "arm": "a0",
        "prompt_bucket": 1,
        "target_len": None,
        "t2_sensitive": False,
    }
    _write(
        tmp_path / "labels" / session / "labels.parquet",
        "labels",
        [{**label, "request_id": rid} for rid in (good, broken, unmapped)],
    )
    traces = load_traces(tmp_path, [session])
    assert [t.request_id for t in traces.traces] == [good]
    trace = traces.traces[0]
    assert list(trace.response_sizes) == [90, 95]
    assert list(trace.request_sizes) == [250]
    assert list(trace.signed_sizes) == [-250, 90, 95]
    assert trace.t_syn_ns == 5
    assert sorted(e["reason"] for e in traces.excluded) == ["incomplete_flow", "no_flow"]
