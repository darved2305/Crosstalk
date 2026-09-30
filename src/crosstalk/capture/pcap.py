import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from crosstalk.common.errors import CaptureError


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    number: int
    timestamp_ns: int
    linktype: int
    data: bytes


@dataclass(frozen=True, slots=True)
class CaptureStats:
    interface_drops: int | None
    os_drops: int | None


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise CaptureError("Truncated capture")
    return data


def read_capture(path: Path) -> Iterator[CapturedFrame]:
    with path.open("rb") as stream:
        magic = _read_exact(stream, 4)
        stream.seek(0)
        if magic == b"\x0a\x0d\x0d\x0a":
            yield from _pcapng(stream)
        else:
            yield from _pcap(stream)


def _pcap(stream: BinaryIO) -> Iterator[CapturedFrame]:
    header = _read_exact(stream, 24)
    magic = header[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1000),
        b"\xa1\xb2\xc3\xd4": (">", 1000),
        b"\x4d\x3c\xb2\xa1": ("<", 1),
        b"\xa1\xb2\x3c\x4d": (">", 1),
    }
    if magic not in formats:
        raise CaptureError("Unsupported capture format")
    endian, fraction_scale = formats[magic]
    _, _, _, _, _, linktype = struct.unpack_from(endian + "HHiIII", header, 4)
    number = 0
    while packet_header := stream.read(16):
        if len(packet_header) != 16:
            raise CaptureError("Truncated pcap packet header")
        seconds, fraction, captured, original = struct.unpack(endian + "IIII", packet_header)
        if captured < original:
            raise CaptureError("Truncated captured packet")
        data = _read_exact(stream, captured)
        number += 1
        yield CapturedFrame(
            number, seconds * 1_000_000_000 + fraction * fraction_scale, linktype, data
        )


def _options(data: memoryview, endian: str) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    offset = 0
    while offset + 4 <= len(data):
        code, length = struct.unpack_from(endian + "HH", data, offset)
        offset += 4
        if code == 0:
            break
        if offset + length > len(data):
            raise CaptureError("Truncated pcapng option")
        result[code] = bytes(data[offset : offset + length])
        offset += (length + 3) & ~3
    return result


def _pcapng(stream: BinaryIO) -> Iterator[CapturedFrame]:
    endian: str | None = None
    interfaces: list[tuple[int, int]] = []
    number = 0
    while head := stream.read(8):
        if len(head) != 8:
            raise CaptureError("Truncated pcapng block header")
        if head[:4] == b"\x0a\x0d\x0d\x0a":
            bom = _read_exact(stream, 4)
            if bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise CaptureError("Invalid pcapng byte order")
            length = struct.unpack(endian + "I", head[4:])[0]
            if length < 28 or length % 4:
                raise CaptureError("Invalid pcapng section length")
            tail = _read_exact(stream, length - 12)
            if struct.unpack_from(endian + "I", tail, len(tail) - 4)[0] != length:
                raise CaptureError("Pcapng block length mismatch")
            interfaces.clear()
            continue
        if endian is None:
            raise CaptureError("Pcapng interface before section")
        block_type, length = struct.unpack(endian + "II", head)
        if length < 12 or length % 4:
            raise CaptureError("Invalid pcapng block length")
        tail = _read_exact(stream, length - 8)
        if struct.unpack_from(endian + "I", tail, len(tail) - 4)[0] != length:
            raise CaptureError("Pcapng block length mismatch")
        body = memoryview(tail)[:-4]
        if block_type == 1:
            if len(body) < 8:
                raise CaptureError("Truncated pcapng interface")
            linktype = struct.unpack_from(endian + "H", body)[0]
            opts = _options(body[8:], endian)
            resolution = opts.get(9, b"\x06")[0]
            divisor = (2 ** (resolution & 0x7F)) if resolution & 0x80 else (10**resolution)
            interfaces.append((linktype, divisor))
        elif block_type == 6:
            if len(body) < 20:
                raise CaptureError("Truncated pcapng packet")
            interface, high, low, captured, original = struct.unpack_from(endian + "IIIII", body)
            if interface >= len(interfaces) or captured < original or 20 + captured > len(body):
                raise CaptureError("Invalid pcapng packet")
            linktype, divisor = interfaces[int(interface)]
            timestamp_ns = ((high << 32) | low) * 1_000_000_000 // divisor
            number += 1
            yield CapturedFrame(number, timestamp_ns, linktype, bytes(body[20 : 20 + captured]))


def read_interface_stats(path: Path) -> CaptureStats:
    with path.open("rb") as stream:
        if stream.read(4) != b"\x0a\x0d\x0d\x0a":
            return CaptureStats(None, None)
    interface_drops: int | None = None
    os_drops: int | None = None
    with path.open("rb") as stream:
        endian: str | None = None
        while head := stream.read(8):
            if len(head) != 8:
                raise CaptureError("Truncated pcapng block")
            if head[:4] == b"\x0a\x0d\x0d\x0a":
                bom = _read_exact(stream, 4)
                endian = "<" if bom == b"\x4d\x3c\x2b\x1a" else ">"
                length = struct.unpack(endian + "I", head[4:])[0]
                _read_exact(stream, length - 12)
                continue
            if endian is None:
                raise CaptureError("Invalid pcapng section")
            block_type, length = struct.unpack(endian + "II", head)
            body = _read_exact(stream, length - 8)[:-4]
            if block_type == 5 and len(body) >= 12:
                options = _options(memoryview(body)[12:], endian)
                if 5 in options:
                    interface_drops = struct.unpack(endian + "Q", options[5])[0]
                if 7 in options:
                    os_drops = struct.unpack(endian + "Q", options[7])[0]
    return CaptureStats(interface_drops, os_drops)
