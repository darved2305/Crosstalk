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
    max_bytes: int = 16 * 1024 * 1024
    max_pending_bytes: int = 1024 * 1024

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
