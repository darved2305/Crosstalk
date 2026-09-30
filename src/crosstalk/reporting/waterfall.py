import json
from pathlib import Path
from typing import Protocol, cast

import matplotlib

from crosstalk.common.errors import ValidationError
from crosstalk.contracts.parquet import read_table

matplotlib.use("Agg")


class _Axes(Protocol):
    def plot(self, *args: object, **kwargs: object) -> object: ...
    def set_yticks(self, *args: object, **kwargs: object) -> object: ...
    def set_xlabel(self, *args: object, **kwargs: object) -> object: ...
    def set_title(self, *args: object, **kwargs: object) -> object: ...
    def grid(self, *args: object, **kwargs: object) -> object: ...


class _Figure(Protocol):
    def savefig(self, *args: object, **kwargs: object) -> object: ...


def render(root: Path, session_id: str, request_id: str, output: Path) -> None:
    import matplotlib.pyplot as plt

    session = root / "sessions" / session_id
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    offset_ns = manifest["timing"]["start_realtime_ns"] - manifest["timing"]["start_monotonic_ns"]
    mapping = read_table(session / "observer" / "flow_map.parquet").to_pylist()
    flow_ids = [row["flow_id"] for row in mapping if row["request_id"] == request_id]
    if len(flow_ids) != 1:
        raise ValidationError("Request must map to exactly one flow")
    flow_id = flow_ids[0]
    emissions = [
        row
        for row in read_table(session / "oracle" / "emissions.parquet").to_pylist()
        if row["request_id"] == request_id
    ]
    records = [
        row
        for row in read_table(session / "observer" / "records.parquet").to_pylist()
        if row["flow_id"] == flow_id and row["dir"] == 1 and row["phase"] == "response"
    ]
    packets = [
        row
        for row in read_table(session / "observer" / "packets.parquet").to_pylist()
        if row["flow_id"] == flow_id and row["dir"] == 1 and row["tcp_len"] > 0
    ]
    if not emissions or not records or not packets:
        raise ValidationError("Waterfall requires all three populated layers")
    origin = min(
        emissions[0]["t_emit_ns"] + offset_ns, records[0]["t_start_ns"], packets[0]["ts_ns"]
    )
    raw_fig, raw_ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    fig = cast(_Figure, raw_fig)
    ax = cast(_Axes, raw_ax)
    for row in emissions:
        x = (row["t_emit_ns"] + offset_ns - origin) / 1_000_000
        ax.plot(x, 2, "o", color="#0b7285", markersize=3 + min(row["framed_len"] / 100, 12))
    for row in records:
        start = (row["t_start_ns"] - origin) / 1_000_000
        end = (row["t_end_ns"] - origin) / 1_000_000
        ax.plot([start, end], [1, 1], color="#5f3dc4", linewidth=2)
        ax.plot(end, 1, "s", color="#5f3dc4", markersize=3 + min(row["length"] / 100, 12))
    for row in packets:
        x = (row["ts_ns"] - origin) / 1_000_000
        ax.plot(x, 0, "|", color="#c92a2a", markersize=5 + min(row["tcp_len"] / 50, 14))
    ax.set_yticks([0, 1, 2], ["Captured packets", "TLS records", "Application emissions"])
    ax.set_xlabel("Milliseconds from first observed event")
    ax.set_title(f"Crosstalk waterfall: {request_id}")
    ax.grid(axis="x", alpha=0.25)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValidationError(f"Artifact already exists: {output}")
    fig.savefig(output, dpi=180)
    plt.close(raw_fig)
