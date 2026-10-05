# E2: serving load and server-side aggregation

Status: **design, collection path and analysis implemented and verified end to end against the mock upstream** (two replicate sessions through the real shaper and a reconstructed capture). No E2 data has been collected; it needs the validated Linux lab with vLLM.

## Design

| Factor | Implementation |
|---|---|
| Concurrency | `concurrency_levels` (default plan: 1, 8, 32). Each session runs one block per level per cycle, in order C1 → C8 → C32, repeated `cycles` times. A block runs closed-loop at its level and fully drains before the next starts, so load never overlaps and is not confounded with session time |
| Pairing across load | Every level replays the same prompts (same families, same randomization blocks), so differences between levels are paired by family |
| Stream interval | `stream_interval_variants` (plan: 4) for `stream_interval_arms` (plan: a0, a2), sent per request as vLLM's `stream_interval`; every other request uses the base interval |
| Randomization | Each randomization block holds every (class, arm, stream interval) once, shuffled |
| Warmup | `warmup` slots open every concurrency block (excluded from analysis) |
| Replicates | Two sessions with different seeds, collected on different days |

Each session manifest records its blocks (index, level, slot count, monotonic start and end) so vLLM queue metrics scraped by Prometheus can be joined by time window later.

## Analysis (`crosstalk e2 run experiments/e2.yaml`)

- **Conditions:** an arm at the base stream interval is named by the arm (`a2`); a variant is `a2/si4`.
- **Attackers:** at each level, static (trained on D0 at that level) and adaptive (trained on the condition at that level, training families only), on one family fold map shared by every level and condition.
- **Uncertainty:** a two-stage bootstrap: sessions with replacement, then families within class inside each drawn session. One draw serves every level, condition and setting, so contrasts are paired. With one session it reduces to the family bootstrap, and the report says so.
- **Pre-declared tests (Holm within the family):** D0 balanced accuracy at the highest minus the lowest level; for each defense arm, adaptive ρ at the highest minus the lowest level, with ρ normalised by D0 at the same level. A test whose ρ is undefined (D0 not above chance) is reported and left out of the correction.
- **Exploratory (H5):** server-side aggregation against gateway batching at each level (`a0/si4` vs `a2`, and `a2/si4` vs `a2`).
- **Cost per level:** TTFT, inter-token latency, bytes, bandwidth overhead and ΔTTFT against D0 at the same level with paired intervals, dummy records and timer lateness.
- **Frontier:** `frontier/frontier.parquet`, one point per (level, condition, attacker) with ρ and its interval against bandwidth overhead, TTFT p95 and ITL p99.
- **Drift:** determinism-probe medians per concurrency block.

E0 and E1 refuse sessions that mix concurrency levels or stream intervals instead of silently pooling them.

## Verified (mock upstream, `tests/test_e2e_e2.py`)

| Check | Result |
|---|---|
| Blocks run in the planned order and each fully drains before the next (first connection of block k+1 after the last completion of block k) | pass |
| Per-request stream interval honoured: D0 responses carry 1 + ⌈(tokens − 1)/interval⌉ content events | pass |
| Every (level, condition) has the same traces; one fold map across all of them | pass |
| Pre-declared family, Holm, H5 contrasts, costs per level, frontier table, block drift table, figure | pass |
| E1 refuses the mixed-load sessions | pass |

With the mock's load model on (each token slows by half its base delay per extra concurrent stream), the costs move in the direction the plan's hypothesis H4 expects: D3's bandwidth overhead falls from +131% at C1 to +74% at C4, because slower generation fills more of its fixed-rate ticks with content and fewer with dummies. D3 adds about one tick of TTFT. Stream interval 4 raises D0's inter-token latency from about 1 ms to 46 ms. These are mock numbers, not findings.

## Pending

| Item | Needs |
|---|---|
| Choose the levels from the vLLM saturation curve (B4) and cap them at the load validated by the runtime gate | Linux lab with vLLM |
| Two replicate sessions on different days | The lab |
| Join vLLM queue time and running/waiting counts per block | Prometheus scraping vLLM (the loadgen cannot reach the backend network) |
