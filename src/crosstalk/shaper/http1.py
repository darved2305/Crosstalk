import h11

from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.contracts.config import ChatRequest, LabHeaders, Mode

MAX_BODY = 256 * 1024


def validate_request(
    request: h11.Request, body: bytes, mode: Mode
) -> tuple[ChatRequest, LabHeaders | None]:
    if request.method != b"POST" or request.target != b"/v1/chat/completions":
        raise ValidationError("Unsupported method or path")
    if len(body) > MAX_BODY:
        raise ValidationError("Request body exceeds limit")
    headers = {name.lower(): value for name, value in request.headers}
    if headers.get(b"content-type", b"").split(b";", 1)[0] != b"application/json":
        raise ValidationError("Content-Type must be application/json")
    lab_arm = headers.get(b"x-ct-arm")
    lab_rid = headers.get(b"x-ct-rid")
    if mode is Mode.PROD and (lab_arm is not None or lab_rid is not None):
        raise ConfigError("Lab headers are forbidden in production mode")
    if mode is Mode.LAB and ((lab_arm is None) != (lab_rid is None)):
        raise ConfigError("Lab headers must appear together")
    lab = None
    if lab_arm is not None and lab_rid is not None:
        lab = LabHeaders(arm=lab_arm.decode("ascii"), request_id=lab_rid.decode("ascii"))
        if lab.arm != "a0":
            raise ConfigError("Only passthrough is implemented")
    return ChatRequest.model_validate_json(body), lab


def response_headers(status: int, content_type: bytes = b"text/event-stream") -> bytes:
    reason = {200: b"OK", 400: b"Bad Request", 413: b"Content Too Large", 502: b"Bad Gateway"}[
        status
    ]
    return (
        b"HTTP/1.1 "
        + str(status).encode()
        + b" "
        + reason
        + b"\r\n"
        + b"Content-Type: "
        + content_type
        + b"\r\n"
        + b"Transfer-Encoding: chunked\r\nConnection: close\r\nCache-Control: no-cache\r\n\r\n"
    )
