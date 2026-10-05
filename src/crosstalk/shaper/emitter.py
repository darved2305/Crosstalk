"""Per-stream scheduler: upstream events → policy → exactly one ``write()`` per emission.

A reader task fills a bounded queue (upstream backpressure); the emitter waits for the next
event or the policy's next deadline, whichever comes first. Every write is logged to the
oracle emission log with its queueing delay and timer lateness. Nothing else is logged per
emission (ADR-012).
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from crosstalk.common.errors import ValidationError
from crosstalk.shaper.accounting import Emission, EmissionWriter
from crosstalk.shaper.events import UpstreamEvent
from crosstalk.shaper.framer import dummy, framed_length, framed_segments, merged_event, padded
from crosstalk.shaper.policies import Emit, Policy

MAX_PENDING_EVENTS = 4096
MAX_PENDING_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class EmitterLimits:
    stall_timeout: float = 30.0
    write_high_water: int = 65_536
    first_read_timeout: float = 120.0
    idle_read_timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class Transport:
    write: Callable[[bytes], None]
    buffered: Callable[[], int]
    drain: Callable[[], Awaitable[None]]


class StallTimeout(TimeoutError):
    """The client stopped reading for longer than the stall timeout."""


class _End:
    pass


Item = UpstreamEvent | _End | Exception


class StreamEmitter:
    def __init__(
        self,
        policy: Policy,
        transport: Transport,
        emissions: EmissionWriter,
        request_id: str,
        arm: str,
        sequence: int,
        limits: EmitterLimits | None = None,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.policy = policy
        self.transport = transport
        self.emissions = emissions
        self.request_id = request_id
        self.arm = arm
        self.sequence = sequence
        self.limits = limits or EmitterLimits()
        self.clock = clock
        self.pending: deque[UpstreamEvent] = deque()
        self._queued_bytes = 0
        self._room = asyncio.Event()
        self._room.set()

    async def run(self, events: AsyncIterator[UpstreamEvent]) -> None:
        queue: asyncio.Queue[Item] = asyncio.Queue(MAX_PENDING_EVENTS)
        reader = asyncio.create_task(self._read(events, queue))
        try:
            await self._loop(queue)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _read(
        self, events: AsyncIterator[UpstreamEvent], queue: "asyncio.Queue[Item]"
    ) -> None:
        try:
            timeout = self.limits.first_read_timeout
            while True:
                try:
                    item = await asyncio.wait_for(anext(events), timeout)
                except StopAsyncIteration:
                    break
                timeout = self.limits.idle_read_timeout
                # Stop reading upstream while this stream already buffers too much.
                while self._queued_bytes >= MAX_PENDING_BYTES:
                    self._room.clear()
                    await self._room.wait()
                self._queued_bytes += item.meta.raw_len
                await queue.put(item)
            await queue.put(_End())
        except Exception as error:  # handed to the emitter loop, which re-raises it
            await queue.put(error)

    async def _loop(self, queue: "asyncio.Queue[Item]") -> None:
        ended = False
        await self._execute(self.policy.on_start(self.clock()), None)
        while True:
            # Everything that has already arrived joins the pending set before any tick fires:
            # a late timer must still see every event that arrived before its deadline.
            while not ended and not queue.empty():
                ended = await self._accept(queue.get_nowait())
            metas = [event.meta for event in self.pending]
            deadline = self.policy.next_deadline_ns(metas, self.clock())
            if ended:
                # A policy may keep ticking after the upstream ends (D3 tail quantum).
                if deadline is None:
                    if self.pending:
                        raise ValidationError("Policy left events pending with no deadline")
                    return
                await asyncio.sleep(max(0.0, (deadline - self.clock()) / 1e9))
                await self._tick(deadline)
                continue
            if deadline is not None and deadline <= self.clock():
                await self._tick(deadline)
                continue
            timeout = None if deadline is None else (deadline - self.clock()) / 1e9
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                continue
            ended = await self._accept(item)

    async def _accept(self, item: Item) -> bool:
        """Hand one queued item to the policy; returns True at the end of the upstream."""
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _End):
            metas = [event.meta for event in self.pending]
            await self._execute(self.policy.on_upstream_end(metas, self.clock()), None)
            return True
        self._queued_bytes -= item.meta.raw_len
        self._room.set()
        self.pending.append(item)
        metas = [event.meta for event in self.pending]
        await self._execute(self.policy.on_event(item.meta, metas, self.clock()), None)
        return False

    async def _tick(self, deadline: int) -> None:
        metas = [event.meta for event in self.pending]
        await self._execute(self.policy.on_timer(metas, self.clock()), deadline)

    async def _execute(self, emits: list[Emit], deadline: int | None) -> None:
        for emit in emits:
            await self._emit(emit, deadline)

    async def _emit(self, emit: Emit, deadline: int | None) -> None:
        if emit.fill is not None:
            if emit.take:
                raise ValidationError("A fill emission chooses its own events")
            await self._fill(emit.fill, deadline)
            return
        if emit.take > len(self.pending):
            raise ValidationError("Policy took more events than are pending")
        taken = [self.pending.popleft() for _ in range(emit.take)]
        if emit.dummy is not None:
            if taken:
                raise ValidationError("A dummy emission cannot carry events")
            await self._send(dummy(emit.dummy), "dummy", 0, taken, deadline)
            return
        if not taken:
            raise ValidationError("An emission must carry events or be a dummy")
        if len(taken) > 1:
            if not emit.merge or not all(event.meta.mergeable for event in taken):
                raise ValidationError("Several events need merge=True and mergeable events")
            payload = merged_event(taken[0].header, taken[0].index, [e.content for e in taken])
        else:
            payload = taken[0].raw
        kind = "merged" if len(taken) > 1 else "content"
        natural = framed_length(len(payload))
        for target in emit.ladder or ():
            if target >= natural and (frame := padded(payload, target)) is not None:
                await self._send(
                    frame,
                    kind if target == natural else _padded_kind(kind),
                    natural,
                    taken,
                    deadline,
                )
                return
        for segment in framed_segments(payload):
            await self._send(segment, kind, len(segment), taken, deadline)

    async def _fill(self, size: int, deadline: int | None) -> None:
        """Exactly ``size`` framed bytes: the longest head run that fits, else a dummy."""
        if not self.pending:
            await self._send(dummy(size), "dummy", 0, [], deadline)
            return
        best: tuple[int, bytes, bytes] | None = None
        head = self.pending[0]
        frame = padded(head.raw, size)
        if frame is not None:
            best = (1, head.raw, frame)
        if head.meta.mergeable:
            count = 2
            while count <= len(self.pending) and self.pending[count - 1].meta.mergeable:
                run = [self.pending[index] for index in range(count)]
                payload = merged_event(run[0].header, run[0].index, [e.content for e in run])
                frame = padded(payload, size)
                if frame is None:
                    break
                best = (count, payload, frame)
                count += 1
        if best is None:
            # One event larger than the record size: sent unpadded, which breaks constant
            # size for this record; the emission log keeps it visible as "oversize".
            event = self.pending.popleft()
            for segment in framed_segments(event.raw):
                await self._send(segment, "oversize", len(segment), [event], deadline)
            return
        count, payload, frame = best
        taken = [self.pending.popleft() for _ in range(count)]
        kind = "merged" if count > 1 else "padded"
        await self._send(frame, kind, framed_length(len(payload)), taken, deadline)

    async def _send(
        self,
        frame: bytes,
        kind: str,
        natural: int,
        taken: list[UpstreamEvent],
        deadline: int | None,
    ) -> None:
        now = self.clock()
        self.transport.write(frame)
        oldest = min((event.meta.arrival_ns for event in taken), default=now)
        await self.emissions.record(
            Emission(
                self.request_id,
                self.sequence,
                now,
                kind,
                len(frame),
                sum(event.meta.content_len for event in taken),
                len(frame) - natural,
                len(taken),
                now - oldest,
                now - deadline if deadline is not None else 0,
                self.arm,
            )
        )
        self.sequence += 1
        if self.transport.buffered() >= self.limits.write_high_water:
            try:
                await asyncio.wait_for(self.transport.drain(), self.limits.stall_timeout)
            except TimeoutError:
                raise StallTimeout(f"Client stalled for {self.limits.stall_timeout} s") from None


def _padded_kind(kind: str) -> str:
    # A merged chunk stays "merged" (pad_bytes shows the padding); a single event is "padded".
    return "merged" if kind == "merged" else "padded"
