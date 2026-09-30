import ipaddress
import struct
from pathlib import Path

import pytest

from crosstalk.capture.packet import parse_tcp
from crosstalk.capture.pcap import CapturedFrame, read_capture, read_interface_stats
from crosstalk.common.errors import CaptureError


def ipv4_tcp() -> bytes:
    tcp = struct.pack("!HHIIHHHH", 50000, 8443, 101, 0, 0x5018, 65535, 0, 0) + b"abc"
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(tcp), 1, 0, 64, 6, 0)
    return (
        ip
        + ipaddress.IPv4Address("10.0.0.2").packed
        + ipaddress.IPv4Address("10.0.0.3").packed
        + tcp
    )


def ipv6_tcp() -> bytes:
    tcp = struct.pack("!HHIIHHHH", 50001, 8443, 101, 0, 0x5018, 65535, 0, 0) + b"abc"
    return (
        struct.pack("!IHBB", 6 << 28, len(tcp), 6, 64)
        + ipaddress.IPv6Address("2001:db8::1").packed
        + ipaddress.IPv6Address("2001:db8::2").packed
        + tcp
    )


@pytest.mark.parametrize(
    ("linktype", "prefix"),
    [
        (1, b"\0" * 12 + b"\x08\x00"),
        (113, b"\0" * 14 + b"\x08\x00"),
        (276, b"\x08\x00" + b"\0" * 18),
    ],
)
def test_link_layers(linktype: int, prefix: bytes) -> None:
    packet = parse_tcp(CapturedFrame(1, 100, linktype, prefix + ipv4_tcp()))
    assert packet is not None
    assert packet.payload == b"abc"
    assert packet.destination_port == 8443
    assert packet.ip_length == 43


def test_ipv6() -> None:
    packet = parse_tcp(CapturedFrame(1, 100, 1, b"\0" * 12 + b"\x86\xdd" + ipv6_tcp()))
    assert packet is not None
    assert packet.source == "2001:db8::1"
    assert packet.payload == b"abc"


def test_pcap_micro_and_nano(tmp_path: Path) -> None:
    payload = b"\0" * 12 + b"\x08\x00" + ipv4_tcp()
    for magic, fraction, expected in (
        (b"\xd4\xc3\xb2\xa1", 2, 1000002000),
        (b"\x4d\x3c\xb2\xa1", 2, 1000000002),
    ):
        path = tmp_path / magic.hex()
        path.write_bytes(
            magic
            + struct.pack("<HHiIII", 2, 4, 0, 0, 65535, 1)
            + struct.pack("<IIII", 1, fraction, len(payload), len(payload))
            + payload
        )
        frames = list(read_capture(path))
        assert len(frames) == 1
        assert frames[0].timestamp_ns == expected
    broken = tmp_path / "broken.pcap"
    broken.write_bytes(
        magic
        + struct.pack("<HHiIII", 2, 4, 0, 0, 65535, 1)
        + struct.pack("<IIII", 1, 0, 8, 10)
        + b"abc"
    )
    with pytest.raises(CaptureError):
        list(read_capture(broken))


def _block(kind: int, body: bytes) -> bytes:
    padding = b"\0" * ((-len(body)) % 4)
    length = 12 + len(body) + len(padding)
    return struct.pack("<II", kind, length) + body + padding + struct.pack("<I", length)


def test_pcapng(tmp_path: Path) -> None:
    payload = b"\0" * 12 + b"\x08\x00" + ipv4_tcp()
    section = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    interface = _block(1, struct.pack("<HHI", 1, 0, 65535))
    enhanced = _block(6, struct.pack("<IIIII", 0, 0, 1000, len(payload), len(payload)) + payload)
    path = tmp_path / "test.pcapng"
    path.write_bytes(section + interface + enhanced)
    frames = list(read_capture(path))
    assert len(frames) == 1
    assert frames[0].timestamp_ns == 1_000_000
    assert parse_tcp(frames[0]) is not None


def test_pcapng_interface_drop_counters(tmp_path: Path) -> None:
    section = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    interface = _block(1, struct.pack("<HHI", 1, 0, 65535))
    options = struct.pack("<HHQHHQHH", 5, 8, 2, 7, 8, 3, 0, 0)
    statistics = _block(5, struct.pack("<III", 0, 0, 1000) + options)
    path = tmp_path / "stats.pcapng"
    path.write_bytes(section + interface + statistics)
    result = read_interface_stats(path)
    assert result.interface_drops == 2
    assert result.os_drops == 3
