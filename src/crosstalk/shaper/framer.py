"""HTTP/1.1 chunk framing with exact sizes (IMPLEMENTATION_PLAN §11.4).

One emission is one chunk and one ``write()``; its framed length is the TLS record
plaintext length. Padding travels in an SSE comment line placed before the event's first
field, which every conformant SSE client ignores.
"""

import json

from crosstalk.common.errors import ValidationError

MAX_PLAINTEXT = 16_384
FILLER = b"."
MIN_FRAMED = 16


def chunk(payload: bytes) -> bytes:
    return f"{len(payload):x}\r\n".encode() + payload + b"\r\n"


def framed_length(payload_length: int) -> int:
    return len(f"{payload_length:x}") + 2 + payload_length + 2


def framed_segments(payload: bytes) -> list[bytes]:
    framed = chunk(payload)
    if len(framed) <= MAX_PLAINTEXT:
        return [framed]
    segments = [
        framed[index : index + MAX_PLAINTEXT] for index in range(0, len(framed), MAX_PLAINTEXT)
    ]
    if any(len(segment) > MAX_PLAINTEXT for segment in segments):
        raise ValidationError("TLS emission exceeds plaintext limit")
    return segments


def terminator() -> bytes:
    result = chunk(b"data: [DONE]\n\n") + b"0\r\n\r\n"
    if len(result) > MAX_PLAINTEXT:
        raise ValidationError("Terminator exceeds plaintext limit")
    return result


def _payload_length_for(target: int) -> int | None:
    """Chunk payload length L whose framed size is exactly ``target``, if one exists."""
    for width in range(1, 5):
        length = target - 4 - width
        if length >= 0 and len(f"{length:x}") == width:
            return length
    return None


def padded(event: bytes, target: int) -> bytes | None:
    """Frame ``event`` to exactly ``target`` bytes, or None if the target cannot be hit.

    Padding is a comment line ``:<filler>\\n`` costing ``filler + 2`` bytes.
    """
    if framed_length(len(event)) == target:
        return chunk(event)
    length = _payload_length_for(target)
    if length is None or length < len(event) + 2:
        return None
    filler = length - len(event) - 2
    return chunk(b":" + FILLER * filler + b"\n" + event)


def dummy(target: int) -> bytes:
    """Comment-only block of exactly ``target`` framed bytes; clients yield no event."""
    length = _payload_length_for(target)
    if length is None or length < 3:
        raise ValidationError(f"No dummy block fits {target} bytes")
    return chunk(b":" + FILLER * (length - 3) + b"\n\n")


def merged_event(header: dict[str, object], index: int, contents: list[str]) -> bytes:
    """One valid ``chat.completion.chunk`` carrying the concatenated content deltas.

    ``header`` holds the first merged event's ``id``/``object``/``created``/``model``.
    """
    body: dict[str, object] = dict(header)
    body["choices"] = [
        {"index": index, "delta": {"content": "".join(contents)}, "finish_reason": None}
    ]
    encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    return b"data: " + encoded + b"\n\n"
