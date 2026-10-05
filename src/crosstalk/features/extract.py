"""R1 summary, R2 CUMUL and T-len features (IMPLEMENTATION_PLAN §15.1).

Pure functions of one observer trace; golden-vector tested.
"""

import numpy as np

from crosstalk.common.errors import ConfigError
from crosstalk.features.traces import FloatArray, Trace, TraceSet

FEATURE_SCHEMA_VERSION = "1"
BURST_GAP_MS = 20.0
FIRST_N = 20
LAST_N = 5
CUMUL_POINTS = 100
SIZE_QUANTILES = (10, 25, 50, 75, 90, 99)
GAP_QUANTILES = (10, 50, 90, 99)
# Fixed record-length bin edges (bytes); the final bin is open-ended.
HIST_EDGES = (0, 32, 48, 64, 80, 96, 128, 160, 192, 256, 384, 512, 1024, 2048, 4096, 8192)
TLEN_FEATURES = ("req_bytes", "req_count", "resp_count", "resp_bytes", "duration_ms")


def _quantiles(values: FloatArray, points: tuple[int, ...]) -> list[float]:
    if values.size == 0:
        return [0.0] * len(points)
    return [float(v) for v in np.percentile(values, points)]


R1_NAMES: tuple[str, ...] = (
    "req_bytes",
    "req_count",
    "resp_count",
    "resp_bytes",
    "size_mean",
    "size_std",
    "size_min",
    "size_max",
    *(f"size_q{q}" for q in SIZE_QUANTILES),
    *(f"size_hist{index:02d}" for index in range(len(HIST_EDGES))),
    *(f"gap_q{q}" for q in GAP_QUANTILES),
    "gap_mean",
    "gap_cv",
    "ttft_ms",
    "duration_ms",
    "records_per_s",
    "burst_count",
    "burst_mean_bytes",
    "burst_max_bytes",
    *(f"first_size{index:02d}" for index in range(FIRST_N)),
    *(f"first_gap{index:02d}" for index in range(FIRST_N)),
    *(f"last_size{index}" for index in range(LAST_N)),
)
R2_NAMES: tuple[str, ...] = (
    *(f"cumul{index:03d}" for index in range(CUMUL_POINTS)),
    "n_in",
    "n_out",
    "bytes_in",
    "bytes_out",
)


def _padded(values: FloatArray, size: int, from_end: bool = False) -> list[float]:
    chosen = values[-size:] if from_end else values[:size]
    result = [float(v) for v in chosen]
    return result + [0.0] * (size - len(result))


def r1(trace: Trace) -> FloatArray:
    sizes = trace.response_sizes
    ends_ms = trace.response_end_ns.astype(np.float64) / 1e6
    gaps = np.diff(ends_ms)
    duration = float(ends_ms[-1] - trace.response_start_ns[0] / 1e6)
    last_request = (
        float(trace.request_end_ns[-1]) / 1e6 if trace.request_end_ns.size else float("nan")
    )
    ttft = float(trace.response_start_ns[0] / 1e6 - last_request)
    histogram = np.histogram(sizes, bins=[*HIST_EDGES, np.inf])[0].astype(np.float64)
    bursts: list[float] = [float(sizes[0])]
    for gap, size in zip(gaps, sizes[1:], strict=True):
        if gap < BURST_GAP_MS:
            bursts[-1] += float(size)
        else:
            bursts.append(float(size))
    gap_mean = float(gaps.mean()) if gaps.size else 0.0
    values = [
        float(trace.request_sizes.sum()),
        float(trace.request_sizes.size),
        float(sizes.size),
        float(sizes.sum()),
        float(sizes.mean()),
        float(sizes.std()),
        float(sizes.min()),
        float(sizes.max()),
        *_quantiles(sizes, SIZE_QUANTILES),
        *(float(v) for v in histogram),
        *_quantiles(gaps, GAP_QUANTILES),
        gap_mean,
        float(gaps.std() / gap_mean) if gap_mean > 0 else 0.0,
        0.0 if np.isnan(ttft) else ttft,
        duration,
        float(sizes.size) / max(duration / 1000, 1e-6),
        float(len(bursts)),
        float(np.mean(bursts)),
        float(np.max(bursts)),
        *_padded(sizes, FIRST_N),
        *_padded(gaps, FIRST_N),
        *_padded(sizes, LAST_N, from_end=True),
    ]
    return np.asarray(values, dtype=np.float64)


def r2(trace: Trace) -> FloatArray:
    signed = trace.signed_sizes
    cumulative = np.cumsum(signed)
    if cumulative.size == 1:
        curve = np.full(CUMUL_POINTS, cumulative[0])
    else:
        positions = np.arange(1, cumulative.size + 1, dtype=np.float64)
        curve = np.interp(np.linspace(1, cumulative.size, CUMUL_POINTS), positions, cumulative)
    incoming = signed[signed > 0]
    outgoing = -signed[signed < 0]
    totals = [incoming.size, outgoing.size, incoming.sum(), outgoing.sum()]
    return np.concatenate([curve, np.asarray(totals, dtype=np.float64)])


def matrix(traces: TraceSet, representation: str) -> tuple[FloatArray, tuple[str, ...]]:
    if representation == "r1":
        rows, names = [r1(t) for t in traces.traces], R1_NAMES
    elif representation == "r2":
        rows, names = [r2(t) for t in traces.traces], R2_NAMES
    elif representation == "tlen":
        indices = [R1_NAMES.index(name) for name in TLEN_FEATURES]
        rows, names = [r1(t)[indices] for t in traces.traces], TLEN_FEATURES
    else:
        raise ConfigError(f"Unknown representation: {representation}")
    if not rows:
        return np.zeros((0, len(names))), names
    result = np.vstack(rows)
    if not np.isfinite(result).all():
        raise ConfigError(f"Non-finite {representation} feature values")
    return result, names
