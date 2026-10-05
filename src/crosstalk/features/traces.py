"""Observer-view traces per request, assembled only through the observer loader."""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from crosstalk.traces.observer import load, load_labels

LABEL_COLUMNS = (
    "prompt_id",
    "class",
    "family_id",
    "template_id",
    "regime",
    "role",
    "arm",
    "concurrency",
    "stream_interval",
    "cblock",
    "t2_sensitive",
    "prompt_bucket",
    "target_len",
)

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Trace:
    session_id: str
    request_id: str
    request_sizes: FloatArray
    request_end_ns: npt.NDArray[np.int64]
    response_sizes: FloatArray
    response_start_ns: npt.NDArray[np.int64]
    response_end_ns: npt.NDArray[np.int64]
    signed_sizes: FloatArray
    t_syn_ns: int
    labels: dict[str, object]


@dataclass(slots=True)
class TraceSet:
    traces: list[Trace] = field(default_factory=lambda: [])
    excluded: list[dict[str, str]] = field(default_factory=lambda: [])

    def select(self, **criteria: object) -> "TraceSet":
        kept = [t for t in self.traces if all(t.labels[k] == v for k, v in criteria.items())]
        return TraceSet(kept, self.excluded)

    def column(self, name: str) -> list[object]:
        return [trace.labels[name] for trace in self.traces]


def load_traces(root: Path, session_ids: list[str]) -> TraceSet:
    result = TraceSet()
    for session_id in session_ids:
        _load_session(root, session_id, result)
    return result


def _load_session(root: Path, session_id: str, result: TraceSet) -> None:
    records = load(root, session_id, "records")
    flows = {row["flow_id"]: row for row in load(root, session_id, "flows").to_pylist()}
    flow_map = {
        row["flow_id"]: row["request_id"] for row in load(root, session_id, "flow_map").to_pylist()
    }
    labels = {row["request_id"]: row for row in load_labels(root, session_id).to_pylist()}
    columns = {
        name: records.column(name).to_numpy(zero_copy_only=False)
        for name in ("flow_id", "dir", "t_start_ns", "t_end_ns", "length", "phase")
    }
    by_flow: dict[int, list[int]] = defaultdict(list)
    for index, flow_id in enumerate(columns["flow_id"].tolist()):
        by_flow[int(flow_id)].append(index)
    mapped = set(flow_map.values())
    for request_id in labels:
        if request_id not in mapped:
            result.excluded.append(
                {"session_id": session_id, "request_id": request_id, "reason": "no_flow"}
            )
    for flow_id, request_id in sorted(flow_map.items()):
        flow = flows[flow_id]
        if not flow["complete"]:
            result.excluded.append(
                {"session_id": session_id, "request_id": request_id, "reason": "incomplete_flow"}
            )
            continue
        if request_id not in labels:
            result.excluded.append(
                {"session_id": session_id, "request_id": request_id, "reason": "no_label"}
            )
            continue
        rows = np.asarray(by_flow.get(flow_id, []), dtype=np.int64)
        direction = columns["dir"][rows]
        phase = columns["phase"][rows]
        ends = columns["t_end_ns"][rows].astype(np.int64)
        starts = columns["t_start_ns"][rows].astype(np.int64)
        lengths = columns["length"][rows].astype(np.float64)
        request = (direction == -1) & (phase == "request")
        response = (direction == 1) & (phase == "response")
        if not response.any():
            result.excluded.append(
                {"session_id": session_id, "request_id": request_id, "reason": "no_response"}
            )
            continue
        post = phase != "handshake"
        order = np.argsort(ends[post], kind="stable")
        signed = (lengths[post] * direction[post])[order]
        response_order = np.argsort(starts[response], kind="stable")
        request_order = np.argsort(ends[request], kind="stable")
        label = labels[request_id]
        result.traces.append(
            Trace(
                session_id,
                request_id,
                lengths[request][request_order],
                ends[request][request_order],
                lengths[response][response_order],
                starts[response][response_order],
                ends[response][response_order],
                signed.astype(np.float64),
                int(flow["t_syn_ns"]),
                {name: label[name] for name in LABEL_COLUMNS},
            )
        )
