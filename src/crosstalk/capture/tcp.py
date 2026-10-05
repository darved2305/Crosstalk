from bisect import bisect_right
from dataclasses import dataclass, field

from crosstalk.capture.packet import TCPPacket
from crosstalk.common.errors import ReconstructionError

SYN = 0x02
FIN = 0x01
RST = 0x04


@dataclass(slots=True)
class TCPStream:
    initial_sequence: int | None = None
    data: bytearray = field(default_factory=bytearray)
    pending: dict[int, tuple[int, int, int]] = field(default_factory=lambda: {})
    retransmitted_bytes: int = 0
    duplicate_segments: int = 0
    out_of_order_segments: int = 0
    overlap_conflict: bool = False
    ended: bool = False
    reset: bool = False
    final_offset: int | None = None
    missing_syn: bool = False
    max_bytes: int = 16 * 1024 * 1024
    max_pending_bytes: int = 1024 * 1024
    # When each stream byte was first seen, stored as runs: byte ``offset`` onwards (up to
    # the next run) arrived at ``time`` in ``frame``.
    _run_offsets: list[int] = field(default_factory=lambda: [])
    _run_times: list[int] = field(default_factory=lambda: [])
    _run_frames: list[int] = field(default_factory=lambda: [])

    def add(self, packet: TCPPacket) -> tuple[bool, bool]:
        if packet.flags & SYN:
            if self.initial_sequence is None:
                self.initial_sequence = packet.sequence
            elif self.initial_sequence != packet.sequence:
                self.overlap_conflict = True
        if self.initial_sequence is None:
            self.missing_syn = True
            return False, False
        start = (
            packet.sequence + (1 if packet.flags & SYN else 0) - self.initial_sequence - 1
        ) & 0xFFFFFFFF
        if start >= 0x80000000:
            raise ReconstructionError("TCP sequence outside supported half-space")
        is_retrans = False
        is_ooo = start > len(self.data)
        if is_ooo and packet.payload:
            self.out_of_order_segments += 1
        if packet.payload and not self.pending and not is_ooo:
            return self._contiguous(packet, start), is_ooo
        for index, byte in enumerate(packet.payload):
            position = start + index
            if position >= self.max_bytes:
                raise ReconstructionError("TCP stream exceeds configured bound")
            if position < len(self.data):
                is_retrans = True
                self.retransmitted_bytes += 1
                if self.data[position] != byte:
                    self.overlap_conflict = True
            elif position in self.pending:
                is_retrans = True
                self.retransmitted_bytes += 1
                if self.pending[position][0] != byte:
                    self.overlap_conflict = True
            else:
                if len(self.pending) >= self.max_pending_bytes:
                    raise ReconstructionError("Out-of-order TCP buffer exceeds bound")
                self.pending[position] = (byte, packet.timestamp_ns, packet.frame_number)
        if is_retrans and packet.payload:
            self.duplicate_segments += 1
        while len(self.data) in self.pending:
            byte, timestamp, frame = self.pending.pop(len(self.data))
            self._stamp(timestamp, frame)
            self.data.append(byte)
        self._close(packet, start)
        return is_retrans, is_ooo

    def _contiguous(self, packet: TCPPacket, start: int) -> bool:
        """Fast path: no buffered gap and the segment starts at or before the stream end.

        Equivalent to the per-byte path (property-tested against it): bytes already
        received count as retransmitted and must match; the rest is appended in order.
        """
        payload = packet.payload
        have = len(self.data)
        end = start + len(payload)
        if end > self.max_bytes:
            raise ReconstructionError("TCP stream exceeds configured bound")
        overlap = min(end, have) - start
        if overlap > 0:
            self.retransmitted_bytes += overlap
            self.duplicate_segments += 1
            if bytes(self.data[start : start + overlap]) != payload[:overlap]:
                self.overlap_conflict = True
        if end > have:
            self._stamp(packet.timestamp_ns, packet.frame_number)
            self.data += payload[have - start :]
        self._close(packet, start)
        return overlap > 0

    def _stamp(self, timestamp: int, frame: int) -> None:
        """Open a run at the current end of the stream unless the last run continues."""
        if self._run_times and self._run_times[-1] == timestamp and self._run_frames[-1] == frame:
            return
        self._run_offsets.append(len(self.data))
        self._run_times.append(timestamp)
        self._run_frames.append(frame)

    def _close(self, packet: TCPPacket, start: int) -> None:
        if packet.flags & FIN:
            self.final_offset = start + len(packet.payload)
            self.ended = True
        if packet.flags & RST:
            self.reset = True
            self.ended = True

    def span(self, start: int, end: int) -> tuple[int, int, int]:
        """First-seen time of byte ``start``, and the time and frame at which the bytes
        ``[start, end)`` were complete (the earliest byte carrying the latest time)."""
        if not 0 <= start < end <= len(self.data):
            raise ReconstructionError("Byte span outside the reassembled stream")
        run = bisect_right(self._run_offsets, start) - 1
        first = latest = self._run_times[run]
        frame = self._run_frames[run]
        run += 1
        while run < len(self._run_offsets) and self._run_offsets[run] < end:
            if self._run_times[run] > latest:
                latest, frame = self._run_times[run], self._run_frames[run]
            run += 1
        return first, latest, frame

    def _expand(self, values: list[int]) -> list[int]:
        if not values:
            return []
        bounds = [*self._run_offsets[1:], len(self.data)]
        return [
            value
            for value, begin, stop in zip(values, self._run_offsets, bounds, strict=True)
            for _ in range(stop - begin)
        ]

    @property
    def first_seen_ns(self) -> list[int]:
        """Per-byte view of the first-seen times (for tests and diagnostics)."""
        return self._expand(self._run_times)

    @property
    def completion_frames(self) -> list[int]:
        return self._expand(self._run_frames)

    @property
    def complete(self) -> bool:
        return (
            self.initial_sequence is not None
            and self.ended
            and not self.reset
            and self.final_offset == len(self.data)
            and not self.pending
            and not self.overlap_conflict
        )
