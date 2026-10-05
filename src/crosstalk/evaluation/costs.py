"""Defense cost from the same runs (PRODUCT.md §16.2).

This is the evaluate stage: it may read the oracle (client timings, emission log) but
never the restricted text store, and nothing here feeds an attacker.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from crosstalk.stats.bootstrap import Resamples, percentile_ci
from crosstalk.traces import observer, oracle

FloatArray = npt.NDArray[np.float64]
QUANTILES = (50, 95, 99)


@dataclass(frozen=True, slots=True)
class RequestCost:
    ttft_ms: float
    duration_ms: float
    itl_ms: tuple[float, ...]
    wire_bytes: float
    pad_bytes: float
    dummies: float
    timer_lateness_ms: tuple[float, ...]


def load_costs(root: Path, session_ids: Sequence[str]) -> dict[str, RequestCost]:
    costs: dict[str, RequestCost] = {}
    for session_id in session_ids:
        requests = oracle.load(root, session_id, "requests").to_pylist()
        events: dict[str, list[int]] = defaultdict(list)
        for row in oracle.load(root, session_id, "client_events").to_pylist():
            if row["is_content"]:
                events[row["request_id"]].append(int(row["t_recv_ns"]))
        emissions: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in oracle.load(root, session_id, "emissions").to_pylist():
            emissions[row["request_id"]].append(row)
        flows = {
            row["flow_id"]: row for row in observer.load(root, session_id, "flows").to_pylist()
        }
        wire = {
            row["request_id"]: float(
                flows[row["flow_id"]]["bytes_c2s"] + flows[row["flow_id"]]["bytes_s2c"]
            )
            for row in observer.load(root, session_id, "flow_map").to_pylist()
        }
        for row in requests:
            request_id = row["request_id"]
            if row["status"] != "completed" or row["t_first_content_ns"] is None:
                continue
            times = sorted(events[request_id])
            rows = emissions[request_id]
            costs[request_id] = RequestCost(
                (row["t_first_content_ns"] - row["t_sent_ns"]) / 1e6,
                (row["t_done_ns"] - row["t_sent_ns"]) / 1e6,
                tuple(float(v) / 1e6 for v in np.diff(times)) if len(times) > 1 else (),
                wire.get(request_id, float("nan")),
                float(sum(int(str(r["pad_bytes"])) for r in rows)),
                float(sum(r["kind"] == "dummy" for r in rows)),
                tuple(
                    int(str(r["timer_lateness_ns"])) / 1e6
                    for r in rows
                    if int(str(r["timer_lateness_ns"]))
                ),
            )
    return costs


def weighted_quantile(values: FloatArray, weights: FloatArray, q: float) -> float:
    mask = weights > 0
    if not mask.any():
        return float("nan")
    order = np.argsort(values[mask], kind="stable")
    sorted_values = values[mask][order]
    cumulative = np.cumsum(weights[mask][order])
    threshold = q / 100 * float(cumulative[-1])
    index = int(np.searchsorted(cumulative, threshold, side="left"))
    return float(sorted_values[min(index, sorted_values.size - 1)])


def summarize(
    costs: dict[str, RequestCost],
    request_ids: Sequence[str],
    arms: Sequence[str],
    resamples: Resamples,
    baseline: str = "a0",
) -> dict[str, dict[str, object]]:
    """Per-arm cost percentiles and paired differences against the baseline arm.

    ``request_ids``/``arms`` are aligned with the rows ``resamples`` indexes, so every arm
    is resampled with the same family weights.
    """
    rows = [index for index, rid in enumerate(request_ids) if rid in costs]
    by_arm: dict[str, list[int]] = defaultdict(list)
    for index in rows:
        by_arm[arms[index]].append(index)

    def column(indices: list[int], name: str) -> FloatArray:
        return np.asarray([getattr(costs[request_ids[i]], name) for i in indices], dtype=np.float64)

    def replicates(indices: list[int], name: str, statistic: str) -> tuple[float, FloatArray]:
        values = column(indices, name)
        subset = resamples.restrict(np.asarray(indices, dtype=np.int64))

        def stat(weights: FloatArray) -> float:
            if statistic == "mean":
                return float(np.sum(weights * values) / np.sum(weights))
            return weighted_quantile(values, weights, 50)

        point = stat(np.ones(values.size))
        return point, np.asarray([stat(subset.weights(b)) for b in range(subset.b)])

    summary: dict[str, dict[str, object]] = {}
    base = by_arm.get(baseline, [])
    base_bytes = replicates(base, "wire_bytes", "mean") if base else None
    base_ttft = replicates(base, "ttft_ms", "median") if base else None
    for arm in sorted(by_arm):
        indices = by_arm[arm]
        itl = np.asarray([v for i in indices for v in costs[request_ids[i]].itl_ms])
        lateness = np.asarray([v for i in indices for v in costs[request_ids[i]].timer_lateness_ms])
        entry: dict[str, object] = {"n": len(indices)}
        for name, values in (
            ("ttft_ms", column(indices, "ttft_ms")),
            ("itl_ms", itl),
            ("duration_ms", column(indices, "duration_ms")),
            ("timer_lateness_ms", lateness),
        ):
            entry[name] = (
                {f"p{q}": float(np.percentile(values, q)) for q in QUANTILES}
                if values.size
                else None
            )
        entry["wire_bytes_mean"] = float(np.mean(column(indices, "wire_bytes")))
        entry["pad_bytes_mean"] = float(np.mean(column(indices, "pad_bytes")))
        entry["dummies_mean"] = float(np.mean(column(indices, "dummies")))
        if base_bytes is not None and base_ttft is not None and arm != baseline:
            point, reps = replicates(indices, "wire_bytes", "mean")
            ratio = reps / base_bytes[1] - 1
            low, high = percentile_ci(ratio * 100)
            entry["bandwidth_overhead_pct"] = {
                "value": (point / base_bytes[0] - 1) * 100,
                "ci_lo": low,
                "ci_hi": high,
            }
            point, reps = replicates(indices, "ttft_ms", "median")
            low, high = percentile_ci(reps - base_ttft[1])
            entry["ttft_p50_delta_ms"] = {
                "value": point - base_ttft[0],
                "ci_lo": low,
                "ci_hi": high,
            }
        summary[arm] = entry
    return summary
