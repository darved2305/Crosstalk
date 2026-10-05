"""Defense arms D0-D2 behind one interface (IMPLEMENTATION_PLAN §12).

Policies are pure, clock-driven state machines over ``EventMeta`` (sizes and flags, never
text), so a decision can depend on sizes, counts and time but not on content.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from crosstalk.common.errors import ConfigError
from crosstalk.contracts.config import PolicyConfig
from crosstalk.shaper.events import EventMeta
from crosstalk.shaper.framer import MAX_PLAINTEXT

MIN_BUCKET = 16


@dataclass(frozen=True, slots=True)
class Emit:
    """Take ``take`` events from the head of the pending queue and send them as one chunk.

    ``merge`` concatenates content deltas (all taken events must be mergeable).
    ``ladder`` pads the chunk to the smallest bucket its actual framed size fits.
    ``dummy`` sends a comment-only block of that many framed bytes (``take`` must be 0).
    ``fill`` sends exactly that many framed bytes: as many head events as fit, merged and
    padded, or a dummy block when nothing is pending (``take`` must be 0). The emitter
    chooses how many events fit, so the policy still decides only when and how big.
    """

    take: int
    merge: bool = False
    ladder: tuple[int, ...] | None = None
    dummy: int | None = None
    fill: int | None = None


class Policy(Protocol):
    def on_start(self, now_ns: int) -> list[Emit]: ...
    def on_event(
        self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int
    ) -> list[Emit]: ...
    def on_timer(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]: ...
    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]: ...
    def next_deadline_ns(self, pending: Sequence[EventMeta], now_ns: int) -> int | None: ...


def ladder(first_bucket: int) -> tuple[int, ...]:
    """First bucket, then doubling, capped at the TLS plaintext limit."""
    if not MIN_BUCKET <= first_bucket <= MAX_PLAINTEXT or first_bucket % 16:
        raise ConfigError("first_bucket must be a multiple of 16 in [16, 16384]")
    buckets = [first_bucket]
    while buckets[-1] * 2 <= MAX_PLAINTEXT:
        buckets.append(buckets[-1] * 2)
    if buckets[-1] != MAX_PLAINTEXT:
        buckets.append(MAX_PLAINTEXT)
    return tuple(buckets)


class Passthrough:
    """D0: every upstream event is one emission, immediately."""

    def on_start(self, now_ns: int) -> list[Emit]:
        return []

    def on_event(self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return [Emit(1)] * len(pending)

    def on_timer(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return []

    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return [Emit(1)] * len(pending)

    def next_deadline_ns(self, pending: Sequence[EventMeta], now_ns: int) -> int | None:
        return None


class BucketPad(Passthrough):
    """D1: every event immediately, padded to the smallest ladder bucket it fits."""

    def __init__(self, buckets: tuple[int, ...]) -> None:
        self.buckets = buckets

    def on_event(self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return [Emit(1, ladder=self.buckets)] * len(pending)

    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return [Emit(1, ladder=self.buckets)] * len(pending)


class WindowBatch:
    """D2 (and D4 with a ladder): merge content arriving within a fixed window.

    The grid is anchored at the first content event, which goes out immediately, so TTFT is
    unchanged. At each later grid tick with pending events, consecutive mergeable events are
    merged (up to ``max_merge_bytes`` of content) and other events follow in order.
    """

    def __init__(
        self, window_ns: int, max_merge_bytes: int, buckets: tuple[int, ...] | None = None
    ) -> None:
        if window_ns <= 0 or max_merge_bytes <= 0:
            raise ConfigError("window and max_merge_bytes must be positive")
        self.window_ns = window_ns
        self.max_merge_bytes = max_merge_bytes
        self.buckets = buckets
        self.anchor: int | None = None

    def on_start(self, now_ns: int) -> list[Emit]:
        return []

    def on_event(self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        if self.anchor is not None:
            return []
        if meta.mergeable:
            self.anchor = now_ns
        # Before the grid exists (role event, first content) everything goes immediately.
        return [Emit(1, ladder=self.buckets)] * len(pending)

    def next_deadline_ns(self, pending: Sequence[EventMeta], now_ns: int) -> int | None:
        if self.anchor is None or not pending:
            return None
        # Events arriving in [t_k-1, t_k) leave at grid tick t_k.
        oldest = min(meta.arrival_ns for meta in pending)
        return self.anchor + ((oldest - self.anchor) // self.window_ns + 1) * self.window_ns

    def on_timer(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        emits: list[Emit] = []
        index = 0
        while index < len(pending):
            if not pending[index].mergeable:
                emits.append(Emit(1, ladder=self.buckets))
                index += 1
                continue
            take = 0
            size = 0
            while (
                index + take < len(pending)
                and pending[index + take].mergeable
                and (take == 0 or size + pending[index + take].content_len <= self.max_merge_bytes)
            ):
                size += pending[index + take].content_len
                take += 1
            emits.append(Emit(take, merge=take > 1, ladder=self.buckets))
            index += take
        return emits

    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        if self.anchor is None:
            return [Emit(1, ladder=self.buckets)] * len(pending)
        return []


class ConstantRate:
    """D3: one record of ``record_size`` every ``interval`` from request arrival.

    Each tick carries as much pending content as fits, or a dummy block. Backlog carries
    over, so latency grows when generation outpaces capacity. After the upstream ends and
    the backlog drains, ticks continue until the count is a multiple of ``quantum`` so the
    response length is visible only in quanta.
    """

    def __init__(self, interval_ns: int, record_size: int, quantum: int) -> None:
        if interval_ns <= 0 or quantum <= 0:
            raise ConfigError("interval and tail quantum must be positive")
        if not MIN_BUCKET <= record_size <= MAX_PLAINTEXT:
            raise ConfigError("record_size must lie in [16, 16384]")
        self.interval_ns = interval_ns
        self.record_size = record_size
        self.quantum = quantum
        self.start: int | None = None
        self.ticks = 0
        self.ended = False

    def on_start(self, now_ns: int) -> list[Emit]:
        self.start = now_ns
        return []

    def on_event(self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        return []

    def on_timer(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        self.ticks += 1
        return [Emit(0, fill=self.record_size)]

    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]:
        self.ended = True
        return []

    def next_deadline_ns(self, pending: Sequence[EventMeta], now_ns: int) -> int | None:
        if self.start is None:
            return None
        if self.ended and not pending and self.ticks % self.quantum == 0:
            return None
        return self.start + (self.ticks + 1) * self.interval_ns


def build_policy(config: PolicyConfig) -> Policy:
    parameters = config.parameters
    if config.kind == "passthrough":
        return Passthrough()
    if config.kind == "bucket_pad":
        return BucketPad(ladder(int(parameters.get("first_bucket", 256))))
    if config.kind in ("window_batch", "hybrid"):
        window_ms = float(parameters.get("window_ms", 100))
        buckets = (
            ladder(int(parameters.get("first_bucket", 256))) if config.kind == "hybrid" else None
        )
        return WindowBatch(
            int(window_ms * 1_000_000), int(parameters.get("max_merge_bytes", 8192)), buckets
        )
    if config.kind == "constant_rate":
        record_size = int(
            parameters.get("record_size", 2 * int(parameters.get("first_bucket", 256)))
        )
        return ConstantRate(
            int(float(parameters.get("interval_ms", 50)) * 1_000_000),
            record_size,
            int(parameters.get("tail_quantum_ticks", 32)),
        )
    raise ConfigError(f"Unknown policy kind: {config.kind}")
