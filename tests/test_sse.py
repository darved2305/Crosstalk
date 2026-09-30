from hypothesis import given
from hypothesis import strategies as st

from crosstalk.shaper.framer import MAX_PLAINTEXT, chunk, framed_segments, terminator
from crosstalk.shaper.sse import SSEParser


def test_sse_spec_lines() -> None:
    parser = SSEParser()
    events = parser.feed(b"\xef\xbb\xbf:comment\rdata: first\r\ndata: second\nid: x\n\n")
    assert len(events) == 1
    assert events[0].data == "first\nsecond"
    assert events[0].event_id == "x"
    assert events[0].raw.startswith(b"\xef\xbb\xbf:comment")


@given(st.lists(st.binary(min_size=1, max_size=20), min_size=1, max_size=30))
def test_sse_arbitrary_chunking(parts: list[bytes]) -> None:
    raw = b"data: " + b"".join(parts).hex().encode() + b"\r\n\r\n"
    expected = SSEParser().feed(raw)
    parser = SSEParser()
    actual = []
    for byte in raw:
        actual.extend(parser.feed(bytes([byte])))
    assert actual == expected


def test_framing_split_and_terminator() -> None:
    payload = b"data: " + b"x" * 25_000 + b"\n\n"
    segments = framed_segments(payload)
    assert all(len(part) <= MAX_PLAINTEXT for part in segments)
    assert b"".join(segments) == chunk(payload)
    assert terminator().endswith(b"0\r\n\r\n")
