import ipaddress
import struct
from dataclasses import dataclass

from crosstalk.capture.pcap import CapturedFrame
from crosstalk.common.errors import ReconstructionError


@dataclass(frozen=True, slots=True)
class TCPPacket:
    frame_number: int
    timestamp_ns: int
    source: str
    destination: str
    source_port: int
    destination_port: int
    sequence: int
    flags: int
    ip_length: int
    payload: bytes


def parse_tcp(frame: CapturedFrame) -> TCPPacket | None:
    data = memoryview(frame.data)
    if frame.linktype == 1:
        if len(data) < 14:
            raise ReconstructionError("Truncated Ethernet frame")
        protocol = struct.unpack_from("!H", data, 12)[0]
        offset = 14
        while protocol in (0x8100, 0x88A8):
            if len(data) < offset + 4:
                raise ReconstructionError("Truncated VLAN header")
            protocol = struct.unpack_from("!H", data, offset + 2)[0]
            offset += 4
    elif frame.linktype == 113:
        if len(data) < 16:
            raise ReconstructionError("Truncated Linux SLL frame")
        protocol = struct.unpack_from("!H", data, 14)[0]
        offset = 16
    elif frame.linktype == 276:
        if len(data) < 20:
            raise ReconstructionError("Truncated Linux SLL2 frame")
        protocol = struct.unpack_from("!H", data, 0)[0]
        offset = 20
    else:
        raise ReconstructionError(f"Unsupported link type: {frame.linktype}")
    if protocol == 0x0800:
        if len(data) < offset + 20:
            raise ReconstructionError("Truncated IPv4 header")
        ihl = (data[offset] & 15) * 4
        ip_length = struct.unpack_from("!H", data, offset + 2)[0]
        fragment = struct.unpack_from("!H", data, offset + 6)[0]
        if ihl < 20 or len(data) < offset + ip_length or ip_length < ihl:
            raise ReconstructionError("Invalid IPv4 length")
        if fragment & 0x3FFF:
            raise ReconstructionError("Fragmented IPv4 is unsupported")
        if data[offset + 9] != 6:
            return None
        source = str(ipaddress.IPv4Address(bytes(data[offset + 12 : offset + 16])))
        destination = str(ipaddress.IPv4Address(bytes(data[offset + 16 : offset + 20])))
        tcp_offset = offset + ihl
        end = offset + ip_length
    elif protocol == 0x86DD:
        if len(data) < offset + 40:
            raise ReconstructionError("Truncated IPv6 header")
        payload_length = struct.unpack_from("!H", data, offset + 4)[0]
        ip_length = 40 + payload_length
        if len(data) < offset + ip_length:
            raise ReconstructionError("Truncated IPv6 payload")
        source = str(ipaddress.IPv6Address(bytes(data[offset + 8 : offset + 24])))
        destination = str(ipaddress.IPv6Address(bytes(data[offset + 24 : offset + 40])))
        next_header = data[offset + 6]
        tcp_offset = offset + 40
        end = offset + ip_length
        while next_header in (0, 43, 60):
            if tcp_offset + 2 > end:
                raise ReconstructionError("Truncated IPv6 extension")
            length = (data[tcp_offset + 1] + 1) * 8
            next_header = data[tcp_offset]
            tcp_offset += length
        if next_header == 44:
            raise ReconstructionError("Fragmented IPv6 is unsupported")
        if next_header != 6:
            return None
    else:
        return None
    if tcp_offset + 20 > end:
        raise ReconstructionError("Truncated TCP header")
    source_port, destination_port, sequence = struct.unpack_from("!HHI", data, tcp_offset)
    tcp_header = (data[tcp_offset + 12] >> 4) * 4
    if tcp_header < 20 or tcp_offset + tcp_header > end:
        raise ReconstructionError("Invalid TCP header length")
    flags = data[tcp_offset + 13]
    return TCPPacket(
        frame.number,
        frame.timestamp_ns,
        source,
        destination,
        source_port,
        destination_port,
        sequence,
        flags,
        ip_length,
        bytes(data[tcp_offset + tcp_header : end]),
    )
