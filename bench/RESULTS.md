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

A later 0.4-second Windows invocation of the expanded harness wrote `artifacts/bench/runtime-windows-backpressure.json`. Three repetitions ran 63 normal clients alongside a TLS client that read 1 KiB once per second. All normal clients completed, and task counts returned to baseline after cancellation. The median repetition had a 113.13 ms increase in normal-client p99 latency. The slow connection did not reach the configured two-second stall timeout in any repetition, so this is a measured failure of the backpressure probe, not a passing backpressure result. The observed application write buffer peak was zero; the probe did not establish a bound for kernel socket buffers. A subsequent single-case retry with a reduced socket receive buffer also did not trigger the stall timeout. Neither run qualifies for the Linux plus uvloop runtime decision.

## Pending host validation

Run the harness at its 20-second default in Linux with uvloop and three repetitions, then run `--soak-seconds 1800` for the continuous 64-stream resource check. The Linux relay and backpressure measurements remain pending. Docker Desktop briefly ran a diagnostic mock capture on 2026-09-30, but became unresponsive during a later container recreate. The diagnostic capture is described in `docs/experiments/encrypted-traffic-validation.md`; it does not establish capture throughput or full-session completeness.

## Bounded Windows probe, 2026-10-12

Command: `uv run python bench/runtime/run.py --duration 0.4 --output
artifacts/bench/runtime-windows-20261012.json`.

Environment: Windows, CPython 3.13.12, OpenSSL 3.5.5. The run used the real TLS
shaper and the repository's explicitly synthetic mock upstream. The raw ignored
artifact SHA-256 is
`d8885739e1c76c6fb6931c42cde5b1400acbc225cd5e5c8afb765121a5ffea13`.

| Measurement | Median repetition result |
|---|---:|
| Spaced TLS record sequence | exact in 3/3; 53 emissions each |
| Back-to-back TLS record sequence | exact in 3/3; 53 emissions each |
| Delayed-reader TLS record sequence | exact in 3/3; 53 emissions each |
| Randomized SSE chunking | 1,000 cases; 0 mismatches |
| Timer p99, 1 stream | 14.39 ms |
| Timer p99, 64 streams | 26.09 ms |
| Timer p99, 256 streams | 974.78 ms |
| 256-stream CPU / ending RSS | 0.917 core / 154.35 MB |
| Slow-reader write-buffer peak / cap | 534,039 / 1,048,576 bytes |
| Slow reader aborted at two-second timeout | yes, 3/3 repetitions |
| Normal-client errors with slow reader | 0, 3/3 repetitions |

This is a measured development probe, not a passing research gate. Its 0.4 s
duration, Windows event loop, synthetic upstream, missing Linux relay run, and
missing 30-minute soak make it ineligible for the Linux/uvloop decision in
`IMPLEMENTATION_PLAN.md` §3.2. The poor 256-stream timer result is reported as
observed; it is not hidden or generalized to the target Linux runtime.
