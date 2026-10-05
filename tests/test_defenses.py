import asyncio
import itertools
import json
import random
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.contracts.config import PolicyConfig
from crosstalk.shaper.accounting import EmissionWriter
from crosstalk.shaper.emitter import EmitterLimits, StallTimeout, StreamEmitter, Transport
from crosstalk.shaper.events import EventMeta, UpstreamEvent, classify
from crosstalk.shaper.framer import (
    MAX_PLAINTEXT,
    dummy,
    framed_length,
    merged_event,
    padded,
)
from crosstalk.shaper.policies import (
    BucketPad,
    Emit,
    Passthrough,
    WindowBatch,
    build_policy,
    ladder,
)
from crosstalk.shaper.sse import SSEParser

MS = 1_000_000


def _dechunk(frames: list[bytes]) -> bytes:
    """Undo HTTP/1.1 chunk framing, checking every chunk is well formed."""
    body = bytearray()
    for frame in frames:
        header, _, rest = frame.partition(b"\r\n")
        size = int(header, 16)
        assert rest[size:] == b"\r\n"
        body.extend(rest[:size])
    return bytes(body)


def _events(frames: list[bytes]) -> list[dict[str, object]]:
    return [json.loads(event.data) for event in SSEParser().feed(_dechunk(frames))]


def test_every_ladder_bucket_is_reachable() -> None:
    for target in range(16, MAX_PLAINTEXT + 1, 16):
        assert len(dummy(target)) == target
    # A 10-byte event frames to 14; the padding comment costs at least 2 more bytes.
    for target in range(32, MAX_PLAINTEXT + 1, 16):
        frame = padded(b"data: {}\n\n", target)
        assert frame is not None and len(frame) == target


@pytest.mark.parametrize("size", [1, 10, 180, 1000])
def test_padding_is_exact_or_refused(size: int) -> None:
    event = b"data: " + b"x" * size + b"\n\n"
    natural = framed_length(len(event))
    reachable = 0
    for target in range(16, MAX_PLAINTEXT + 1):
        frame = padded(event, target)
        if frame is None:
            continue
        reachable += 1
        assert len(frame) == target
        assert target == natural or target >= natural + 2
        if target % 97 == 0 or target in (natural, natural + 2, MAX_PLAINTEXT):
            parsed = SSEParser().feed(_dechunk([frame]))
            assert [e.data for e in parsed] == ["x" * size]
    # Only sizes below the event or at a hex-width boundary are unreachable.
    assert reachable > MAX_PLAINTEXT - natural - 10


def test_dummy_blocks_yield_no_events() -> None:
    assert SSEParser().feed(_dechunk([dummy(64), dummy(4096)])) == []
    with pytest.raises(ValidationError):
        dummy(7)


def test_merged_event_is_a_valid_chunk() -> None:
    raw = merged_event(
        {"id": "c", "object": "chat.completion.chunk", "model": "m"}, 0, ['a"', "é", "c"]
    )
    (event,) = SSEParser().feed(raw)
    data = json.loads(event.data)
    assert data["choices"] == [{"index": 0, "delta": {"content": 'a"éc'}, "finish_reason": None}]
    assert data["model"] == "m"


def _sse(obj: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def _chunk(
    delta: dict[str, object], finish: str | None = None, **extra: object
) -> dict[str, object]:
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }


def _classify(obj: dict[str, object], arrival: int = 0) -> UpstreamEvent:
    (event,) = SSEParser().feed(_sse(obj))
    return classify(event, arrival)


def test_classification() -> None:
    assert _classify(_chunk({"content": "hi"})).meta.mergeable
    assert _classify(_chunk({"content": "hi"}, mock_sent_ns=5)).meta.mergeable
    assert not _classify(_chunk({"role": "assistant", "content": ""})).meta.mergeable
    finish = _classify(_chunk({}, "stop")).meta
    assert finish.terminal and not finish.mergeable
    usage = _classify({"id": "x", "choices": [], "usage": {"completion_tokens": 3}}).meta
    assert usage.terminal and not usage.mergeable
    assert not _classify(_chunk({"content": "a", "tool_calls": []})).meta.mergeable
    logprobs = _chunk({"content": "a"})
    logprobs["choices"][0]["logprobs"] = {"content": []}  # type: ignore[index]
    assert not _classify(logprobs).meta.mergeable
    (broken,) = SSEParser().feed(b"data: not json\n\n")
    assert not classify(broken, 0).meta.mergeable
    assert _classify(_chunk({"content": "hé"})).meta.content_len == 3


def _meta(arrival: int, mergeable: bool = True, size: int = 4, terminal: bool = False) -> EventMeta:
    return EventMeta(60 + size, size, mergeable, terminal, arrival)


def test_ladder() -> None:
    assert ladder(256) == (256, 512, 1024, 2048, 4096, 8192, 16384)
    assert ladder(208)[-1] == MAX_PLAINTEXT and ladder(208)[:2] == (208, 416)
    for bad in (8, 100, 20_000):
        with pytest.raises(ConfigError):
            ladder(bad)


def test_passthrough_and_bucket_policies() -> None:
    meta = _meta(0)
    assert Passthrough().on_event(meta, [meta], 0) == [Emit(1)]
    assert Passthrough().next_deadline_ns([meta], 0) is None
    assert BucketPad((64, 128)).on_event(meta, [meta], 0) == [Emit(1, ladder=(64, 128))]


def test_window_batch_schedule() -> None:
    policy = WindowBatch(100 * MS, max_merge_bytes=10)
    role = _meta(0, mergeable=False)
    assert policy.on_event(role, [role], 0) == [Emit(1)]
    assert policy.anchor is None
    first = _meta(5 * MS)
    assert policy.on_event(first, [first], 5 * MS) == [Emit(1)]
    assert policy.anchor == 5 * MS
    assert policy.next_deadline_ns([], 6 * MS) is None
    pending = [_meta(20 * MS), _meta(40 * MS), _meta(60 * MS), _meta(70 * MS)]
    for index, meta in enumerate(pending):
        assert policy.on_event(meta, pending[: index + 1], meta.arrival_ns) == []
    assert policy.next_deadline_ns(pending, 80 * MS) == 105 * MS
    late = [_meta(230 * MS)]
    assert policy.next_deadline_ns(late, 231 * MS) == 305 * MS
    exact = [_meta(205 * MS)]
    assert policy.next_deadline_ns(exact, 205 * MS) == 305 * MS
    # 4 bytes of content each: the 10-byte cap merges two at a time.
    assert policy.on_timer(pending, 105 * MS) == [Emit(2, merge=True), Emit(2, merge=True)]
    mixed = [
        _meta(1),
        _meta(2),
        _meta(3, mergeable=False, terminal=True),
        _meta(4, mergeable=False),
    ]
    assert policy.on_timer(mixed, 0) == [Emit(2, merge=True), Emit(1), Emit(1)]
    assert policy.on_timer([_meta(1)], 0) == [Emit(1, merge=False)]
    assert policy.on_upstream_end(mixed, 0) == []


def test_window_batch_without_content_flushes_at_end() -> None:
    policy = WindowBatch(100 * MS, 8192)
    role = _meta(0, mergeable=False)
    policy.on_event(role, [role], 0)
    finish = _meta(1, mergeable=False, terminal=True)
    assert policy.on_upstream_end([finish], 1) == [Emit(1)]


def test_build_policy() -> None:
    assert isinstance(build_policy(PolicyConfig()), Passthrough)
    bucket = build_policy(
        PolicyConfig(arm="a1", kind="bucket_pad", parameters={"first_bucket": 192})
    )
    assert isinstance(bucket, BucketPad) and bucket.buckets[0] == 192
    hybrid = build_policy(
        PolicyConfig(arm="a4", kind="hybrid", parameters={"window_ms": 20, "first_bucket": 128})
    )
    assert isinstance(hybrid, WindowBatch) and hybrid.window_ns == 20 * MS and hybrid.buckets
    with pytest.raises(ConfigError):
        build_policy(PolicyConfig(arm="a9", kind="magic"))


ARMS = {
    "a0": PolicyConfig(),
    "a1": PolicyConfig(arm="a1", kind="bucket_pad", parameters={"first_bucket": 128}),
    "a2": PolicyConfig(
        arm="a2", kind="window_batch", parameters={"window_ms": 3, "max_merge_bytes": 40}
    ),
    "a4": PolicyConfig(arm="a4", kind="hybrid", parameters={"window_ms": 3, "first_bucket": 128}),
}


def _upstream(contents: list[str], extra: bool) -> bytes:
    events = [_chunk({"role": "assistant", "content": ""})]
    events += [_chunk({"content": text}) for text in contents]
    events.append(_chunk({}, "stop"))
    if extra:
        events.append({"id": "x", "choices": [], "usage": {"completion_tokens": len(contents)}})
    return b"".join(_sse(event) for event in events) + b"data: [DONE]\n\n"


async def _run_emitter(
    arm: str, stream: bytes, cuts: list[int], gaps_ms: list[float]
) -> tuple[list[bytes], list[dict[str, object]]]:
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory() as directory:
        log = Path(directory) / "log.ndjson"
        async with EmissionWriter(log) as emissions:

            async def events() -> AsyncIterator[UpstreamEvent]:
                parser = SSEParser()
                bounds = [0, *sorted(set(cuts)), len(stream)]
                for index, (start, end) in enumerate(itertools.pairwise(bounds)):
                    if gaps_ms and gaps_ms[index % len(gaps_ms)]:
                        await asyncio.sleep(gaps_ms[index % len(gaps_ms)] / 1000)
                    for event in parser.feed(stream[start:end]):
                        if event.data == "[DONE]":
                            return
                        yield classify(event, time.monotonic_ns())

            async def drain() -> None:
                return None

            emitter = StreamEmitter(
                build_policy(ARMS[arm]),
                Transport(frames.append, lambda: 0, drain),
                emissions,
                "R",
                arm,
                0,
            )
            await emitter.run(events())
        rows = [json.loads(line) for line in log.read_text().splitlines()]
    return frames, rows


@settings(max_examples=40, deadline=None)
@given(
    arm=st.sampled_from(sorted(ARMS)),
    contents=st.lists(st.text(min_size=0, max_size=12), min_size=0, max_size=25),
    extra=st.booleans(),
    cut_seed=st.integers(0, 10_000),
    gaps=st.lists(st.sampled_from([0.0, 0.0, 0.5, 2.0, 4.0]), min_size=1, max_size=5),
)
def test_content_fidelity_under_every_arm(
    arm: str, contents: list[str], extra: bool, cut_seed: int, gaps: list[float]
) -> None:
    stream = _upstream(contents, extra)
    rng = random.Random(cut_seed)
    cuts = [rng.randrange(1, len(stream)) for _ in range(rng.randrange(0, 12))]
    frames, rows = asyncio.run(_run_emitter(arm, stream, cuts, gaps))
    decoded = _events(frames)
    text = "".join(
        str(choice.get("delta", {}).get("content") or "")  # type: ignore[union-attr]
        for event in decoded
        for choice in event.get("choices", [])  # type: ignore[union-attr]
    )
    assert text == "".join(contents)
    assert all(len(frame) <= MAX_PLAINTEXT for frame in frames)
    assert [row["framed_len"] for row in rows] == [len(frame) for frame in frames]
    assert [row["seq"] for row in rows] == list(range(len(rows)))
    assert sum(row["n_events"] for row in rows) == len(contents) + 2 + extra
    # Order of non-content events is preserved: role first, finish then usage last.
    assert decoded[0]["choices"][0]["delta"].get("role") == "assistant"  # type: ignore[index]
    finish = [i for i, e in enumerate(decoded) if e["choices"] and e["choices"][0]["finish_reason"]]  # type: ignore[index]
    assert finish == [len(decoded) - 1 - extra]
    if arm in ("a1", "a4"):
        assert all(len(frame) in ladder(128) for frame in frames)
        for row, frame in zip(rows, frames, strict=True):
            has_comment = _dechunk([frame]).startswith(b":")
            assert (row["pad_bytes"] > 0) == has_comment
    if arm == "a0":
        assert all(row["kind"] == "content" and row["n_events"] == 1 for row in rows)


def test_window_batch_merges_and_logs_grid_lateness() -> None:
    stream = _upstream([f"t{i} " for i in range(30)], True)
    frames, rows = asyncio.run(_run_emitter("a2", stream, list(range(40, len(stream), 60)), [0.0]))
    assert any(row["kind"] == "merged" for row in rows)
    assert len(frames) < 30
    timed = [row for row in rows if row["timer_lateness_ns"]]
    assert timed and all(row["timer_lateness_ns"] >= 0 for row in timed)
    assert all(row["queue_delay_ns"] >= 0 for row in rows)


def test_stalled_client_times_out() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as directory:
            async with EmissionWriter(Path(directory) / "log.ndjson") as emissions:

                async def events() -> AsyncIterator[UpstreamEvent]:
                    yield _classify(_chunk({"content": "x"}))

                async def never() -> None:
                    await asyncio.sleep(10)

                emitter = StreamEmitter(
                    Passthrough(),
                    Transport(lambda frame: None, lambda: 1_000_000, never),
                    emissions,
                    "R",
                    "a0",
                    0,
                    EmitterLimits(stall_timeout=0.05),
                )
                await emitter.run(events())

    with pytest.raises(StallTimeout):
        asyncio.run(scenario())


def test_upstream_errors_propagate() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as directory:
            async with EmissionWriter(Path(directory) / "log.ndjson") as emissions:

                async def events() -> AsyncIterator[UpstreamEvent]:
                    yield _classify(_chunk({"content": "x"}))
                    raise ConnectionResetError("upstream went away")

                async def drain() -> None:
                    return None

                emitter = StreamEmitter(
                    Passthrough(),
                    Transport(lambda f: None, lambda: 0, drain),
                    emissions,
                    "R",
                    "a0",
                    0,
                )
                await emitter.run(events())

    with pytest.raises(ConnectionResetError):
        asyncio.run(scenario())


def test_policy_contract_violations_are_rejected() -> None:
    class Greedy(Passthrough):
        def on_event(self, meta: EventMeta, pending: object, now_ns: int) -> list[Emit]:
            return [Emit(2)]

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as directory:
            async with EmissionWriter(Path(directory) / "log.ndjson") as emissions:

                async def events() -> AsyncIterator[UpstreamEvent]:
                    yield _classify(_chunk({"content": "x"}))

                async def drain() -> None:
                    return None

                emitter = StreamEmitter(
                    Greedy(), Transport(lambda f: None, lambda: 0, drain), emissions, "R", "a0", 0
                )
                await emitter.run(events())

    with pytest.raises(ValidationError):
        asyncio.run(scenario())


def test_policy_set_loading(tmp_path: Path) -> None:
    from crosstalk.shaper.server import ShaperSettings, load_policies

    shipped = Path(__file__).parents[1] / "experiments" / "policies" / "arms.yaml"
    assert sorted(load_policies(ShaperSettings(policy_file=shipped))) == ["a0", "a1", "a2", "a4"]
    assert list(load_policies(ShaperSettings())) == ["a0"]
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("- {arm: a0}\n- {arm: a0}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_policies(ShaperSettings(policy_file=duplicate))
    no_default = tmp_path / "no_default.yaml"
    no_default.write_text("- {arm: a1, kind: bucket_pad}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="default arm"):
        load_policies(ShaperSettings(policy_file=no_default))
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- {arm: a1, kind: bucket_pad, parameters: {first_bucket: 100}}\n- {arm: a0}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_policies(ShaperSettings(policy_file=bad))
