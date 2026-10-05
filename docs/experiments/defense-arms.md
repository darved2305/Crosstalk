# Defense arms D0-D4 and the E1 analysis

Status: **all five arms and the E1 static-vs-adaptive analysis are implemented and verified against the mock upstream on Windows, through real TLS and a reconstructed capture.** No E1 data has been collected. The Linux benchmark (B2), the G5/G6 re-run on the real shaper and the openai-node client remain pending.

## Arms

| Arm | Policy | Behaviour |
|---|---|---|
| a0 | D0 passthrough | Every upstream event is one emission, immediately |
| a1 | D1 bucket padding | Every event immediately, padded to the smallest bucket of a doubling ladder |
| a2 | D2 window batching | Grid anchored at the first content event (sent immediately, so TTFT is unchanged); at each tick, consecutive content deltas merge into one chunk, other events follow in order |
| a3 | D3 constant-rate | From request arrival, one record of exactly `record_size` every `interval`: as much pending content as fits, or a comment-only dummy. Backlog carries over. After the upstream ends and the backlog drains, ticks continue to the next multiple of `tail_quantum_ticks` |
| a4 | D4 hybrid | D2 merges padded to the D1 ladder |

The policy set lives in `experiments/policies/arms.yaml`, is loaded once at startup (`CT_SHAPER_POLICY_FILE`), and the lab header `x-ct-arm` picks the arm per request. A test keeps the E1 experiment configs identical to that file, so manifests describe the parameters the shaper actually ran.

## Pieces

| Module | Role |
|---|---|
| `crosstalk.shaper.events` | Upstream SSE event → `EventMeta` (raw size, content size, mergeable, terminal, arrival). Policies see only this, never text |
| `crosstalk.shaper.framer` | Exact-size padding in an SSE comment line, comment-only dummies, merged `chat.completion.chunk` |
| `crosstalk.shaper.policies` | The five arms behind one interface. `Emit` carries a ladder (pad to the smallest bucket the real payload fits) or a `fill` size (D3: the emitter packs as many head events as fit) |
| `crosstalk.shaper.emitter` | One `write()` per emission; bounded upstream queue (4,096 events / 1 MiB); waits for the next event or deadline; logs kind, size, content and pad bytes, events, queue delay and timer lateness. A client that stops reading past the stall timeout raises `StallTimeout` |
| `crosstalk.evaluation.e1` | Static (trained on D0) and adaptive (trained on arm k, training families only) attackers per arm, one family fold map and one set of family resamples for everything, paired Δ against D0 static with bootstrap p-values, Holm across the pre-declared tests, retained leakage ρ and its static-to-adaptive recovery |
| `crosstalk.evaluation.costs` | Client TTFT and inter-token latency, bytes per request, bandwidth overhead and ΔTTFT against D0 with paired cluster-bootstrap intervals, padding, dummy records and timer lateness |

The import contracts keep features, attackers and statistics observer-only; the evaluate stage may read the oracle for costs but never the restricted text.

## Verified

| Check | Result |
|---|---|
| Every multiple of 16 in [16, 16384] is reachable as a dummy and in [32, 16384] as a padded event; padding is exact or refused | pass |
| Decoded content byte-identical under every arm for arbitrary content, chunking and arrival gaps; event order preserved (property test) | pass |
| D3: every record exactly `record_size`, tick count a multiple of the quantum, backlog packing, oversize events logged | pass |
| Official `openai` Python SDK 2.54: identical content and usage, one chunk per event-carrying emission, for all five arms | pass |
| Interleaved session through the recording relay: 100% emission-to-record fidelity; D1/D4 records exactly `bucket + 17`; D3 records exactly `record_size + 17`; D2/D4 timer emissions on one window grid | pass |
| E1 end to end: 8 primary tests with Holm, one fold map across all arms and attackers, predictions only on each arm's own traces, D1 bandwidth overhead CI above zero, D3 dummies present | pass |

In the mock E1 run (8 per class per arm, no real signal) the costs moved as expected: D1 +17% bytes, D2 -29% bytes (fewer records, less framing and TLS overhead) with inter-token latency doubled, D3 +193% bytes with about 21 dummy records per request, and no TTFT change in any arm.

## Defects found and fixed

1. **Late ticks emitted one event at a time.** `asyncio.wait_for(queue.get(), 0)` times out even with events queued, so a late D2 tick sent only what was already pending. The emitter now absorbs every arrived event before deciding to tick.
2. **Stall timeouts were never counted.** `run()` did not pass its metrics to the connection handler. That may explain the Windows backpressure probe in `bench/RESULTS.md`; re-run it on Linux.
3. **TCP reassembly was far too slow for the campaigns.** Every payload byte went through a dictionary, and two per-byte Python lists held timestamps. Contiguous segments now take a fast path and first-seen times are stored as runs: about 780 → 165,000 full-size segments/s (440,000 for 200-byte segments) on the Windows workstation. A property test checks the result is identical to the original algorithm, byte for byte.
4. **The SSE parser walked every byte in Python** (in the shaper hot path). It now scans line endings with `bytes.find`: about 5 → 29 MB/s, identical output to the original on 600 random streams (BOMs and CRLFs split across chunks, the event-size bound).

## Deviations from the plan

- `Emit` carries a bucket ladder or a fill size instead of one `pad_to`: a policy cannot know a merged chunk's exact size without seeing text.
- An event too large for its ladder or record size is sent unpadded and logged (`oversize` under D3).
- The response header block is logged as an emission of kind `headers`.

## Pending

| Item | Needs |
|---|---|
| Freeze `first_bucket` and D3 `record_size` | The E0 pilot on vLLM |
| B2 per arm at C ∈ {1, 16, 64}; D2 lateness p99 ≤ 2.5 ms at C=16; D3 spacing; G5/G6 on the real shaper; reconstruction throughput (B3) | Linux with uvloop (Windows timers are ~15 ms) |
| E1 pilot (`experiments/e1-pilot.yaml`) and full E1 (`experiments/e1.yaml`) | Validated lab and vLLM |
| openai-node compatibility; D1r random padding; transfer setting | Later |
