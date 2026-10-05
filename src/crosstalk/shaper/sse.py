from dataclasses import dataclass

from crosstalk.common.errors import ValidationError


@dataclass(frozen=True, slots=True)
class SSEEvent:
    raw: bytes
    data: str
    event: str = "message"
    event_id: str | None = None


class SSEParser:
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
        index = 0
        # A byte-order mark is only possible in the first three bytes of the stream.
        while self._at_start and index < len(chunk):
            self._bom.append(chunk[index])
            index += 1
            if len(self._bom) < 3 and b"\xef\xbb\xbf".startswith(self._bom):
                continue
            prefix = bytes(self._bom)
            self._bom.clear()
            self._at_start = False
            if prefix == b"\xef\xbb\xbf":
                self._raw.extend(prefix)
            else:
                emitted.extend(self._scan(prefix))
        emitted.extend(self._scan(chunk[index:]))
        return emitted

    def _scan(self, chunk: bytes) -> list[SSEEvent]:
        """Line-at-a-time parsing; identical output to a byte-at-a-time state machine."""
        emitted: list[SSEEvent] = []
        index = 0
        size = len(chunk)
        while index < size:
            if self._cr:
                # LF after CR completes a CRLF that already ended the line.
                self._cr = False
                if chunk[index] == 10:
                    self._grow(chunk[index : index + 1])
                    index += 1
                    continue
            cr = chunk.find(b"\r", index)
            lf = chunk.find(b"\n", index)
            end = min(cr, lf) if cr >= 0 and lf >= 0 else max(cr, lf)
            if end < 0:
                self._grow(chunk[index:])
                self._line.extend(chunk[index:])
                break
            self._grow(chunk[index : end + 1])
            self._line.extend(chunk[index:end])
            emitted.extend(self._finish_line())
            self._cr = chunk[end] == 13
            index = end + 1
        return emitted

    def _grow(self, data: bytes) -> None:
        self._raw.extend(data)
        if len(self._raw) > self._max_event_bytes:
            raise ValidationError("SSE event exceeds configured bound")

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
