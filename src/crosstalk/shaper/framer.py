from crosstalk.common.errors import ValidationError

MAX_PLAINTEXT = 16_384


def chunk(payload: bytes) -> bytes:
    return f"{len(payload):x}\r\n".encode() + payload + b"\r\n"


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
