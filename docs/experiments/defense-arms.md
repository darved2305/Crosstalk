# Defense arms D0, D1, D2 and D4

Status: **implemented and verified against the mock upstream on Windows, through real TLS and a reconstructed capture.** The Linux benchmark (B2), the G5/G6 re-run on the real shaper and the openai-node client remain pending.

## What exists

| Piece | Module | Behaviour |
|---|---|---|
| Event classification | `crosstalk.shaper.events` | Each upstream SSE event becomes `EventMeta` (raw size, content size, mergeable, terminal, arrival time). Mergeable means a content-only delta: no role, tool calls, logprobs, finish reason or usage |
| Framer | `crosstalk.shaper.framer` | Exact-size padding in an SSE comment line (`:<dots>\n`) placed before the event; comment-only dummy blocks; merged `chat.completion.chunk` that keeps `id`/`object`/`created`/`model` of the first event |
| Policies | `crosstalk.shaper.policies` | D0 passthrough; D1 bucket padding over a doubling ladder; D2 window batching on a grid anchored at the first content event (sent immediately, so TTFT is unchanged); D4 = D2 merges padded to the D1 ladder |
| Emitter | `crosstalk.shaper.emitter` | One `write()` per emission. Bounded upstream queue (4,096 events / 1 MiB). Waits for the next event or the policy deadline. Logs kind, framed size, content and pad bytes, events, queue delay and timer lateness. A client that stops reading past the stall timeout raises `StallTimeout` |
| Policy set | `experiments/policies/arms.yaml` | Loaded once at startup (`CT_SHAPER_POLICY_FILE`); the lab header `x-ct-arm` picks the arm per request; an unknown arm gets 400 |

Policies receive only `EventMeta`, never text, so no defense decision can depend on content.

## Verified

| Check | Test | Result |
|---|---|---|
| Every multiple of 16 in [16, 16384] is reachable as a dummy, and in [32, 16384] as a padded event | `test_every_ladder_bucket_is_reachable` | pass |
| Padding hits the target exactly or refuses; padded events parse back to the original event | `test_padding_is_exact_or_refused` (4 event sizes × every target) | pass |
| Decoded content is byte-identical under every arm, for arbitrary content, TCP-style chunking and arrival gaps; event order (role first, finish then usage last) is preserved | `test_content_fidelity_under_every_arm` (property) | pass |
| Official `openai` Python SDK 2.54: identical content, usage reported, one chunk per event-carrying emission (no spurious chunks) | `test_openai_sdk_sees_identical_content` × 4 arms | pass |
| Through the recording relay and the real reconstruction: 100% emission-to-record fidelity across all four arms | `test_defended_records_on_the_wire` | pass |
| D1/D4 response records are exactly `bucket + 17` bytes; D2/D4 timer emissions sit on one window grid | same | pass |

## Defects found and fixed

1. **Late ticks emitted one event at a time.** After a deadline had passed, `asyncio.wait_for(queue.get(), 0)` timed out even with events already queued, so each late tick sent only what was already pending. The emitter now moves everything that has arrived into the pending set before deciding to tick.
2. **Stall timeouts were never counted.** `run()` did not pass its metrics object to the connection handler, so `stall_timeouts` stayed 0. That may explain the Windows backpressure probe in `bench/RESULTS.md` reporting that the slow client never hit the stall timeout; re-run it on Linux before drawing conclusions.

## Deviations from the plan

- `Emit` carries a bucket **ladder** rather than one `pad_to` size. A policy cannot know the exact size of a merged chunk (it depends on JSON escaping of text the policy must not see), so the emitter pads to the smallest bucket the real payload fits.
- A single event too large for its ladder, or larger than 16 KiB, is sent unpadded and logged as such.
- The response header block is logged as an emission of kind `headers`.

## Pending

| Item | Needs |
|---|---|
| Freeze `first_bucket` (p99.9 single-event framed size, rounded up to 16) | The E0 pilot on vLLM |
| B2 per arm at C ∈ {1, 16, 64}; D2 lateness p99 ≤ 2.5 ms at C=16; G5/G6 on the real shaper | Linux with uvloop (Windows timers are ~15 ms) |
| openai-node compatibility | A Node test harness in CI |
| D3 constant-rate and the static-vs-adaptive attacker | Next stage |
