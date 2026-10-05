"""The optimised SSE parser must emit exactly what the original per-byte parser emits."""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from crosstalk.common.errors import ValidationError
from crosstalk.shaper.sse import SSEEvent, SSEParser


class ReferenceParser:
    """The per-byte parser as first written; kept as the executable specification."""

    def __init__(self, max_event_bytes: int = 1024 * 1024) -> None:
        self._raw = bytearray()
        self._line = bytearray()
        self._data: list[str] = []
        self._event = "message"
        self._id: str | None = None
        self._cr = False
        self._at_start = True
        self._bom = bytearray()
        self._max_event_bytes = max_event_bytes

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        emitted: list[SSEEvent] = []
        for byte in chunk:
            if self._at_start:
                self._bom.append(byte)
                if len(self._bom) < 3 and b"\xef\xbb\xbf".startswith(self._bom):
                    continue
                prefix = bytes(self._bom)
                self._bom.clear()
                self._at_start = False
                if prefix == b"\xef\xbb\xbf":
                    self._raw.extend(prefix)
                    continue
                emitted.extend(self.feed(prefix))
                continue
            self._raw.append(byte)
            if len(self._raw) > self._max_event_bytes:
                raise ValidationError("SSE event exceeds configured bound")
            if self._cr:
                self._cr = False
                if byte == 10:
                    continue
            if byte == 13:
                emitted.extend(self._finish_line())
                self._cr = True
            elif byte == 10:
                emitted.extend(self._finish_line())
            else:
                self._line.append(byte)
        return emitted

    def _finish_line(self) -> list[SSEEvent]:
        line = bytes(self._line)
        self._line.clear()
        if not line:
            if not self._data:
                self._raw.clear()
                self._event = "message"
                return []
            result = SSEEvent(bytes(self._raw), "\n".join(self._data), self._event, self._id)
            self._raw.clear()
            self._data.clear()
            self._event = "message"
            return [result]
        if line.startswith(b":"):
            return []
        name, _, value = line.partition(b":")
        if value.startswith(b" "):
            value = value[1:]
        decoded = value.decode("utf-8", errors="replace")
        if name == b"data":
            self._data.append(decoded)
        elif name == b"event":
            self._event = decoded
        elif name == b"id" and "\x00" not in decoded:
            self._id = decoded
        return []


PIECES = [
    b"data",
    b"data:",
    b": ",
    b"x",
    b"{}",
    b"event: e",
    b"id: 7",
    b":c",
    b"\r",
    b"\n",
    b"\r\n",
    b"\xef\xbb\xbf",
    b"\xef",
    b"\xbb",
]


def _run(parser: SSEParser | ReferenceParser, stream: bytes, cuts: list[int]) -> list[object]:
    out: list[object] = []
    bounds = sorted({0, len(stream), *[c % (len(stream) + 1) for c in cuts]})
    for start, end in itertools.pairwise(bounds):
        try:
            out.extend(parser.feed(stream[start:end]))
        except ValidationError:
            out.append("bound")
            break
    return out


@settings(max_examples=600, deadline=None)
@given(
    pieces=st.lists(st.sampled_from(PIECES), max_size=60),
    cuts=st.lists(st.integers(0, 400), max_size=12),
    bound=st.sampled_from([16, 64, 1024 * 1024]),
)
def test_optimised_parser_matches_reference(
    pieces: list[bytes], cuts: list[int], bound: int
) -> None:
    stream = b"".join(pieces)
    fast = _run(SSEParser(max_event_bytes=bound), stream, cuts)
    slow = _run(ReferenceParser(max_event_bytes=bound), stream, cuts)
    assert fast == slow


def test_events_are_dataclasses() -> None:
    assert SSEParser().feed(b"data: a\n\n") == [SSEEvent(b"data: a\n\n", "a")]
