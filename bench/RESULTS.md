# Runtime measurements

## Exploratory Windows probe, 2026-09-30

Command: `uv run python bench/runtime/run.py --duration 0.4 --output artifacts/bench/runtime-windows-quick.json`.

Environment: Windows, CPython 3.13.12, OpenSSL 3.5.5. The default asyncio loop was used because uvloop does not run on Windows. The timer duration was 0.4 seconds per repetition. Three repetitions were executed for each case. The machine-readable output is in the ignored local artifact path named above.

| Measurement | Median repetition result |
|---|---:|
| TLS record sequence, 50 spaced SSE events | Exact in 3/3 repetitions; 52 logged emissions each, including usage and terminator |
| TLS record sequence, 50 back-to-back SSE events | Exact in 3/3 repetitions; 52 logged emissions each |
| TLS record sequence, 50 events with delayed client reads | Exact in 3/3 repetitions; 52 logged emissions each |
| Timer lateness, one TLS stream, p50 / p99 / p99.9 / max | 7.58 / 14.54 / 14.54 / 15.23 ms |
| Timer lateness, 64 TLS streams, p50 / p99 / p99.9 / max | 10.16 / 23.50 / 24.22 / 24.35 ms |
| Timer lateness, 256 TLS streams, p50 / p99 / p99.9 / max | 272.87 / 386.12 / 388.08 / 388.35 ms |
| CPU and RSS, 256 TLS streams | 0.83 core; 156.0 MB after repetition |

The short Windows timer results do not qualify for the Linux plus uvloop runtime decision and do not meet the timer thresholds. They are not used to change the selected Python runtime. Relay latency, 30-minute soak, and the configured slow-client backpressure scenario were not measured by this run.

A second 0.4-second Windows invocation wrote `artifacts/bench/runtime-windows-quick-2.json`. It again found exact record sequences in all nine TLS cases and zero mismatches in 1,000 randomized SSE chunking cases. Median-repetition timer p99 values were 15.68 ms (one stream), 29.45 ms (64), and 1,395.15 ms (256). This variability reinforces that these short Windows probes cannot establish the Linux runtime thresholds.

## Pending host validation

Run the harness at its 20-second default in Linux with uvloop and three repetitions, then complete relay, backpressure, and soak measurements against the local mock path. The container laboratory also requires a running Docker Engine before capture and vLLM measurements can be made. No capture throughput or packet-drop result exists yet.
