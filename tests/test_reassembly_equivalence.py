"""The optimised TCP reassembly must behave exactly like the original per-byte algorithm."""

from dataclasses import dataclass, field

from hypothesis import given, settings
from hypothesis import strategies as st

from crosstalk.capture.packet import TCPPacket
from crosstalk.capture.tcp import FIN, RST, SYN, TCPStream


@dataclass(slots=True)
class ReferenceStream:
    """The per-byte reassembly as first written; kept as the executable specification."""

    initial_sequence: int | None = None
    data: bytearray = field(default_factory=bytearray)
    first_seen_ns: list[int] = field(default_factory=lambda: [])
    completion_frames: list[int] = field(default_factory=lambda: [])
    pending: dict[int, tuple[int, int, int]] = field(default_factory=lambda: {})
    retransmitted_bytes: int = 0
    duplicate_segments: int = 0
    out_of_order_segments: int = 0
    overlap_conflict: bool = False
    ended: bool = False
    reset: bool = False
    final_offset: int | None = None
    missing_syn: bool = False

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
        is_retrans = False
        is_ooo = start > len(self.data)
        if is_ooo and packet.payload:
            self.out_of_order_segments += 1
        for index, byte in enumerate(packet.payload):
            position = start + index
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
                self.pending[position] = (byte, packet.timestamp_ns, packet.frame_number)
        if is_retrans and packet.payload:
            self.duplicate_segments += 1
        while len(self.data) in self.pending:
            byte, timestamp, frame = self.pending.pop(len(self.data))
            self.data.append(byte)
            self.first_seen_ns.append(timestamp)
            self.completion_frames.append(frame)
        if packet.flags & FIN:
            self.final_offset = start + len(packet.payload)
            self.ended = True
        if packet.flags & RST:
            self.reset = True
            self.ended = True
        return is_retrans, is_ooo


FIELDS = (
    "initial_sequence",
    "data",
    "first_seen_ns",
    "completion_frames",
    "pending",
    "retransmitted_bytes",
    "duplicate_segments",
    "out_of_order_segments",
    "overlap_conflict",
    "ended",
    "reset",
    "final_offset",
    "missing_syn",
)


def _packet(frame: int, sequence: int, payload: bytes, flags: int) -> TCPPacket:
    return TCPPacket(
        frame, frame * 10, "1.1.1.1", "2.2.2.2", 50000, 8443, sequence, flags, 40, payload
    )


segment = st.tuples(
    st.integers(0, 120),  # start offset into the stream
    st.integers(0, 40),  # length
    st.sampled_from([0, 0, 0, 0, FIN, RST]),
    st.booleans(),  # corrupt the bytes (conflicting overlap)
)


@settings(max_examples=400, deadline=None)
@given(
    data=st.binary(min_size=1, max_size=160),
    segments=st.lists(segment, min_size=1, max_size=25),
    with_syn=st.booleans(),
    isn=st.integers(0, 2**32 - 1),
)
def test_optimised_reassembly_matches_reference(
    data: bytes, segments: list[tuple[int, int, int, bool]], with_syn: bool, isn: int
) -> None:
    fast, reference = TCPStream(), ReferenceStream()
    packets: list[TCPPacket] = []
    if with_syn:
        packets.append(_packet(1, isn, b"", SYN))
    for frame, (start, length, flags, corrupt) in enumerate(segments, start=2):
        payload = data[start : start + length]
        if corrupt and payload:
            payload = bytes((b + 1) % 256 for b in payload)
        packets.append(_packet(frame, (isn + 1 + start) & 0xFFFFFFFF, payload, flags))
    for packet in packets:
        assert fast.add(packet) == reference.add(packet)
    for name in FIELDS:
        assert getattr(fast, name) == getattr(reference, name), name
    # span() must agree with slicing the per-byte timestamps, as parse_records once did.
    times, frames = reference.first_seen_ns, reference.completion_frames
    for start in range(0, len(times), 7):
        for end in (start + 1, min(len(times), start + 23), len(times)):
            if end <= start:
                continue
            window = times[start:end]
            latest = max(window)
            expected = (window[0], latest, frames[start + window.index(latest)])
            assert fast.span(start, end) == expected
    assert fast.complete == (
        reference.initial_sequence is not None
        and reference.ended
        and not reference.reset
        and reference.final_offset == len(reference.data)
        and not reference.pending
        and not reference.overlap_conflict
    )
