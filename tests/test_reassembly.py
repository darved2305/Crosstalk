from hypothesis import given
from hypothesis import strategies as st

from crosstalk.capture.packet import TCPPacket
from crosstalk.capture.tcp import FIN, SYN, TCPStream
from crosstalk.capture.tls import TLSRecord, classify_records, parse_records
from crosstalk.common.errors import ReconstructionError


def packet(seq: int, payload: bytes, flags: int = 0, t: int = 1, frame: int = 1) -> TCPPacket:
    return TCPPacket(
        frame, t, "1.1.1.1", "2.2.2.2", 50000, 8443, seq, flags, 40 + len(payload), payload
    )


def test_out_of_order_retransmission_overlap_and_timestamps() -> None:
    stream = TCPStream()
    stream.add(packet(100, b"", SYN))
    stream.add(packet(105, b"ef", t=2, frame=2))
    stream.add(packet(101, b"abcd", t=3, frame=3))
    stream.add(packet(103, b"cdef", t=4, frame=4))
    stream.add(packet(107, b"", FIN))
    assert bytes(stream.data) == b"abcdef"
    assert stream.first_seen_ns == [3, 3, 3, 3, 2, 2]
    assert stream.retransmitted_bytes == 4
    assert stream.complete
    assert not stream.overlap_conflict


def test_conflicting_overlap_and_hole() -> None:
    stream = TCPStream()
    stream.add(packet(100, b"", SYN))
    stream.add(packet(101, b"abc"))
    stream.add(packet(102, b"X"))
    assert stream.overlap_conflict
    assert not stream.complete
    another = TCPStream()
    another.add(packet(100, b"", SYN))
    another.add(packet(105, b"later", FIN))
    assert another.pending
    assert not another.complete
    tail = TCPStream()
    tail.add(packet(100, b"", SYN))
    tail.add(packet(101, b"abc"))
    tail.add(packet(110, b"", FIN))
    assert not tail.complete


@given(
    st.binary(min_size=1, max_size=200),
    st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=30),
)
def test_arbitrary_segmentation(data: bytes, sizes: list[int]) -> None:
    stream = TCPStream()
    stream.add(packet(100, b"", SYN))
    index = 0
    for size in sizes:
        part = data[index : index + size]
        if not part:
            break
        stream.add(packet(101 + index, part))
        index += len(part)
    if index < len(data):
        stream.add(packet(101 + index, data[index:]))
    stream.add(packet(101 + len(data), b"", FIN))
    assert bytes(stream.data) == data
    assert stream.complete


def test_tls_record_completion() -> None:
    raw = b"\x16\x03\x01\x00\x03abc\x17\x03\x03\x00\x02xy"
    stream = TCPStream()
    stream.add(packet(100, b"", SYN))
    stream.add(packet(101, raw[:6], t=1, frame=2))
    stream.add(packet(107, raw[6:], t=5, frame=3))
    records = parse_records(stream)
    assert [(record.content_type, record.length, record.end_ns) for record in records] == [
        (22, 3, 5),
        (23, 2, 5),
    ]
    assert records[0].completion_frame == 3
    stream.data[0] = 99
    try:
        parse_records(stream)
        raise AssertionError("Invalid header accepted")
    except ReconstructionError:
        pass


def _record(index: int, content_type: int, length: int, end_ns: int) -> TLSRecord:
    return TLSRecord(index, 0, content_type, length, end_ns, end_ns, 0)


def test_phases_when_finished_and_request_share_a_timestamp() -> None:
    client = [
        _record(0, 22, 512, 1),
        _record(1, 20, 1, 1),
        _record(2, 23, 53, 5),
        _record(3, 23, 300, 5),
    ]
    server = [
        _record(0, 22, 90, 3),
        _record(1, 20, 1, 3),
        _record(2, 23, 900, 3),
        _record(3, 23, 120, 7),
        _record(4, 23, 19, 9),
    ]
    client_phases, server_phases = classify_records(client, server)
    assert client_phases == ["handshake", "handshake", "handshake", "request"]
    assert server_phases == ["handshake", "handshake", "handshake", "response", "probable_alert"]


def test_phases_without_a_request() -> None:
    client = [_record(0, 22, 512, 1), _record(1, 23, 53, 5)]
    server = [_record(0, 22, 90, 3), _record(1, 23, 120, 7)]
    assert classify_records(client, server)[1] == ["handshake", "handshake"]
