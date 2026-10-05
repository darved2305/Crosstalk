from dataclasses import dataclass

from crosstalk.capture.tcp import TCPStream
from crosstalk.common.errors import ReconstructionError


@dataclass(frozen=True, slots=True)
class TLSRecord:
    index: int
    stream_offset: int
    content_type: int
    length: int
    start_ns: int
    end_ns: int
    completion_frame: int


def parse_records(stream: TCPStream) -> list[TLSRecord]:
    result: list[TLSRecord] = []
    offset = 0
    data = stream.data
    while offset + 5 <= len(data):
        content_type = data[offset]
        version = bytes(data[offset + 1 : offset + 3])
        length = int.from_bytes(data[offset + 3 : offset + 5], "big")
        if (
            content_type not in (20, 21, 22, 23)
            or version not in (b"\x03\x01", b"\x03\x03")
            or length > 16_640
        ):
            raise ReconstructionError(f"TLS record desynchronization at offset {offset}")
        end = offset + 5 + length
        if end > len(data):
            raise ReconstructionError(f"Incomplete TLS record at offset {offset}")
        times = stream.first_seen_ns[offset:end]
        frames = stream.completion_frames[offset:end]
        end_ns = max(times)
        completion_frame = frames[times.index(end_ns)]
        result.append(
            TLSRecord(len(result), offset, content_type, length, times[0], end_ns, completion_frame)
        )
        offset = end
    if offset != len(data):
        raise ReconstructionError(f"Incomplete TLS header at offset {offset}")
    return result


def classify_records(
    client: list[TLSRecord], server: list[TLSRecord]
) -> tuple[list[str], list[str]]:
    # Stream order, not timestamps: the client Finished and the first request record can
    # complete in the same segment and therefore carry the same timestamp.
    finished = next((index for index, r in enumerate(client) if r.content_type == 23), None)
    first_request = (
        next((r.end_ns for r in client[finished + 1 :] if r.content_type == 23), None)
        if finished is not None
        else None
    )
    client_labels: list[str] = []
    seen_finished = False
    for record in client:
        if record.content_type in (20, 22) or not seen_finished:
            client_labels.append("handshake")
            if record.content_type == 23:
                seen_finished = True
        elif record.length == 19:
            client_labels.append("probable_alert")
        else:
            client_labels.append("request")
    server_labels = [
        "probable_alert"
        if record.content_type == 23 and record.length == 19
        else "response"
        if record.content_type == 23
        and first_request is not None
        and record.start_ns >= first_request
        else "handshake"
        for record in server
    ]
    return client_labels, server_labels
