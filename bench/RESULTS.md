# Runtime measurements

## Linux/uvloop WSL2 gate run, harness v2, 2026-10-12

Raw data:
[`../docs/evidence/runtime-linux-v2-20261012.json`](../docs/evidence/runtime-linux-v2-20261012.json)
(gates) and
[`../docs/evidence/runtime-linux-v2-soak-20261012.json`](../docs/evidence/runtime-linux-v2-soak-20261012.json)
(30-minute soak). Each file records its command, kernel, CPU, commit, dirty flag
and dirty-file list, plus the SHA-256 of the executed sources.

Commands. Both ran in WSL2 Ubuntu with uvloop, from an ext4 snapshot of the working tree,
so no import goes through the `/mnt/c` mount during a measurement:

```
python bench/runtime/run.py --suite gates --duration 20 --output .../runtime-linux-v2-20261012.json
python bench/runtime/run.py --suite soak --soak-seconds 1800 --output .../runtime-linux-v2-soak-20261012.json
```

Environment: kernel 6.6.87.2-microsoft-standard-WSL2, 10 vCPUs (i5-12450HX laptop),
CPython 3.13.12, uvloop 0.22.1, aiohttp 3.14.3, OpenSSL 3.5.5.

Timing and source:
- The gate run (10:05–10:19 UTC) recorded commit `5087876` with uncommitted changes.
- The soak (10:19–10:49 UTC) recorded `dda7e67`, because commits landed during it.
- Both runs executed the same source snapshot, SHA-256 `69ff7d3c…2649`. The shaper and
  harness files in that snapshot are byte-identical, ignoring line endings, to those
  committed in `ffdabbb`.

**Harness v2 topology.** Every role runs in its own OS process with its own event loop:
- the real `crosstalk.shaper.server.run()`, configured through `CT_SHAPER_*`;
- the mock upstream;
- the G2 timer server;
- the clients.

Lateness and latency use `time.monotonic_ns()`, which every process shares. They no longer
use uvloop's `loop.time()`, whose 1 ms resolution caused the exact 1.0/2.0/9.0 ms values in v1.

The G3 relay clients are four processes, each with N/4 streams. Every process runs its
direct phase, then its proxied phase. The phases sit in common time slots, so a direct
phase never overlaps a proxied phase. The JSON also contains two labelled
non-gate configurations:
- all relay clients in a single client process;
- four SO_REUSEPORT shaper processes.

Three repetitions per benchmark; the median repetition decides.

| Gate | Threshold | Measured (median repetition) | Result |
|---|---|---|---|
| G1 record fidelity | exact record sequence in (a), (b), (c) | exact in 9/9 cases | PASS |
| G2 timer, N=64 | p99 ≤ 2.5 ms, p99.9 ≤ 6 ms | p99 1.54 ms, p99.9 2.04 ms | PASS |
| G2 timer, N=256 | p99 ≤ 5 ms, p99.9 ≤ 12 ms | p99 4.04 ms, p99.9 5.77 ms (max 17.3 ms) | PASS |
| G3 relay, N=64 | Δp99 ≤ 2 ms | Δp99 +16.28 ms (direct 2.41, proxied 18.69 ms); 0 errors | **FAIL** |
| G3 relay, N=256 | 0 errors and Δp99 ≤ 8 ms | 0 errors in 3/3; Δp99 +309.69 ms (direct 4.32, proxied 314.01 ms) | **FAIL** |
| G4 CPU/RSS, N=256 timer server | ≤ 1.0 core, RSS ≤ 256 MB | 0.20 core, 86.8 MB | PASS |
| G4 soak, N=64, 30 min (shaper process) | RSS growth ≤ 10 % | +13.9 % (59.17 → 67.42 MB; baseline at 60 s); +33.1 % from t=0 | **FAIL** |
| G5 buffer cap | ≤ 1 MiB | peak 533,973 B | PASS |
| G5 slow-stream abort | aborted at stall timeout (2 s) | aborted in 3/3; seen 2.23–2.28 s after start | PASS |
| G5 normal-client impact | p99 change ≤ 1 ms | +3.20 ms (repetitions +4.84, +3.20, +1.72); 0 errors | **FAIL** |
| G6 SSE fidelity | 0 mismatches | 10,000 cases, 0 mismatches | PASS |

**The Linux runtime gate still fails, on G3, the G4 soak, and the G5 p99 criterion.**

**Soak.** v2 runs the real shaper for 1,800 s with 64 continuous relay streams (400
events × 25 ms each). In v1 the soak ran for 30 s against the timer server. The gate
baseline was fixed before the run as the first sample at or after 60 s, to exclude
warm-up.

Results:
- 11,153 requests completed with 0 errors.
- File descriptors stayed at 146 and threads at 7.
- Shaper CPU was 0.50–0.71 core.
- RSS grew in steps: 59.5 MB at 150 s, 64.9 MB at 600 s, 65.4 MB from 750 s to 1,500 s,
  then 67.4 MB at 1,650 s.

The two largest steps coincide with host stalls. The 10-minute window had a relay p99 of
2,953 ms, and the 27.5-minute window had 191 ms. This pattern fits allocator high-water
growth during backlog bursts better than a per-request leak, but 30 minutes cannot tell
them apart. The result is a FAIL against the 10 % threshold.

### What changed since v1, and why

1. **256-stream relay errors (v1: 156 per repetition): fixed and confirmed.** The shaper
   used `aiohttp.ClientSession()` with the default 100-connection pool. The per-request
   `ClientTimeout(connect=5)` also bounds the wait for a free pool slot. Streams 101–256
   therefore waited behind ~10 s streams and failed with 502 after 5 s: 256 − 100 = 156.
   The shaper now builds an explicit `TCPConnector(limit=limit_per_host=upstream_connections)`
   with a default of 1024. Its timeout is `sock_connect=5` (TCP connect) plus `sock_read=120`
   (response head), with no pool-wait bound. The emitter still enforces the 120 s first-read
   and 30 s idle limits on the body. `tests/test_shaper_upstream_pool.py` holds 150 upstream
   streams open at once. Before the fix it failed with an upstream peak of exactly 100;
   now it passes. v2 recorded 0 errors at N=256 in all repetitions.
2. **G2 timer lateness (v1: N=256 p99 9.0 ms): the v1 numbers were mostly a harness
   artifact.** v1 timed lateness with uvloop's millisecond `loop.time()`. It also ran 256 TLS
   reader clients on the same loop as the timer server, so their decryption counted as server
   lateness. With the server in its own process and a nanosecond clock, N=256 is p99 4.04 ms
   at 0.20 core.
3. **G3 relay latency: a real shaper cost.** v2 splits each proxied event at the shaper's
   own `t_emit_ns`, taken from the emission log just before `transport.write`. In the N=64
   median repetition, upstream send → shaper write is p50 2.60 / p99 15.12 ms, and shaper
   write → client receive is p50 1.03 / p99 5.38 ms. The direct path is p99 2.41 ms. The
   shaper process used 0.65 core for 2,560 events/s, about 250 µs of CPU per relayed event.
   Day-0's bare relay used 0.23 core at the same load (RESEARCH §11.3). At N=256 (10,240
   events/s) the shaper runs at 0.6–1.0 core and saturates. Upstream → write is p50 41.8 /
   p99 246 ms in the median repetition, and one repetition reached 950 ms. The clients were
   not the bottleneck: each client shard used ≤ 0.29 core. In the single-client-process
   diagnostic, the medians were +61.97 ms at N=64 and +258.75 ms at N=256.

   Likely per-event costs are in modules this change did not touch:
   - two `asyncio.wait_for` timers and three task wake-ups per event in `emitter.py`;
   - `json.dumps(asdict(row))` per emission on the event loop;
   - `asyncio.to_thread(output.flush)` every time the emission queue goes empty in
     `accounting.py`. Plan §11.1 specifies one flush per 250 ms.

   These are unproven candidates from a cProfile run, not measured fixes.
4. **G5 normal-client impact: not resolvable on this host.** Each repetition ran 63 normal
   streams of 80 events (2 s, so the slow reader is present for the whole phase) in three
   phases:
   - A: no slow reader (first phase on a fresh shaper);
   - B: no slow reader;
   - S: with the slow reader.

   The gate compares S with B. The A → B control between two identical slow-free phases
   moved p99 by −13.1 to −17.3 ms, because the first phase on a fresh shaper is slower.
   The S − B changes were +1.72 to +4.84 ms. Shaper CPU was 0.58–0.70 core with the slow
   reader and 0.63–0.64 core without, so the slow reader has no CPU signature. The excess
   is within the run-to-run noise of this host, which is much larger than the 1 ms
   threshold. Under the decision rule it is still recorded as a FAIL.
5. **SO_REUSEPORT scale-out (4 shaper processes, labelled, not the gate).** The best
   repetitions were N=64 Δp99 +3.59 ms and N=256 +11.36 ms. Both are above the 2 ms and
   8 ms thresholds. The shaper's own upstream → write p99 was 4.54 and 11.47 ms in those
   repetitions. Other repetitions had 0.25–1.5 s stalls, giving medians of +251.70 ms
   (N=64) and +81.89 ms (N=256), with 0 errors. This run does not show that the escape hatch passes. Its variance on this
   laptop is larger than any effect it could prove.

### Limitations

- WSL2 on a shared Windows laptop is not the GCE lab host. Other work ran on the host
  during the run, and repetition-to-repetition spikes of hundreds of milliseconds appear
  in several configurations.
- The upstream is the repository's synthetic mock (25 ms per event), not vLLM.
- The client, shaper and mock share 10 vCPUs over loopback without CPU pinning. The direct
  path is plain HTTP and the proxied path is Python-level TLS, so Δp99 is an upper bound
  on shaper-added latency, as in Day-0.
- G2 uses one tick phase for all streams, as in v1: every stream writes in the same loop
  iteration. That is a worst case compared with real streams, which start independently.

## Linux/uvloop WSL2 probe, 2026-10-12 (harness v1, single process; superseded by v2 above)

The full 20-second runtime invocation ran in native Ubuntu WSL2 with uvloop,
three repetitions, and a bounded 30-second soak. The committed raw data,
command, timestamp, commit, and environment are in
[`../docs/evidence/runtime-linux-20261012.json`](../docs/evidence/runtime-linux-20261012.json).

Median timer p99 was 2.0 ms at 64 streams and 9.0 ms at 256. Added relay p99
was 20.64 ms at 64 and 35.44 ms at 256; the 256-stream case returned 156 errors
in each repetition. All nine TLS record cases matched and 10,000 randomized
SSE chunking cases had zero mismatches. All three slow readers hit the configured
stall timeout; peak application buffer was 533,940 bytes. Normal-client p99
increased by 10.69 ms in the median slow-reader repetition. The 30-second soak
recorded zero RSS growth and a 170.07 MiB peak RSS.

**The Linux runtime gate failed.** These are actual observations, not substituted
Windows values or a cloud deployment. The 30-second soak does not close the
30-minute gate. The relay errors are consistent with aiohttp's default
100-connection pool and the shaper's five-second connection timeout for
approximately ten-second streams; no fix or passing remeasurement is claimed.

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
