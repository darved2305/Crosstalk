import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

import h11

from crosstalk.common.ids import new_ulid
from crosstalk.loadgen.tls_client import ObservedRecord, TLSClient, client_context
from crosstalk.shaper.sse import SSEParser


@dataclass(frozen=True, slots=True)
class ClientEvent:
    index: int
    received_ns: int
    is_content: bool
    framed_len: int
    mock_sent_ns: int | None = None


@dataclass(frozen=True, slots=True)
class ClientRead:
    index: int
    received_ns: int
    plaintext_len: int


@dataclass(frozen=True, slots=True)
class RequestResult:
    request_id: str
    client_port: int
    t_connect_ns: int
    t_tls_ns: int
    t_sent_ns: int
    t_first_byte_ns: int | None
    t_first_content_ns: int | None
    t_done_ns: int
    status: int
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    events: tuple[ClientEvent, ...]
    reads: tuple[ClientRead, ...]
    records: tuple[ObservedRecord, ...]


async def request(
    host: str,
    port: int,
    body: dict[str, object],
    ca_file: str | None,
    request_id: str | None = None,
    read_delay_ms: float = 0,
    read_chunk_bytes: int = 65_536,
    keylog_file: Path | None = None,
    arm: str = "a0",
) -> RequestResult:
    rid = request_id or new_ulid()
    context = client_context(ca_file, keylog_file)
    async with TLSClient(host, port, context, read_chunk_bytes) as client:
        connection = h11.Connection(h11.CLIENT)
        payload = json.dumps(body, separators=(",", ":")).encode()
        headers = [
            (b"Host", host.encode()),
            (b"Content-Type", b"application/json"),
            (b"Content-Length", str(len(payload)).encode()),
            (b"Connection", b"close"),
            (b"x-ct-arm", arm.encode("ascii")),
            (b"x-ct-rid", rid.encode()),
        ]
        outgoing = b"".join(
            (
                connection.send(
                    h11.Request(method=b"POST", target=b"/v1/chat/completions", headers=headers)
                ),
                connection.send(h11.Data(data=payload)),
                connection.send(h11.EndOfMessage()),
            )
        )
        await client.send(outgoing)
        t_sent_ns = time.monotonic_ns()
        parser = SSEParser()
        chunks: list[str] = []
        events: list[ClientEvent] = []
        reads: list[ClientRead] = []
        first_byte: int | None = None
        first_content: int | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        status = 0
        done = False
        while not done:
            raw, read_ns = await client.recv()
            reads.append(ClientRead(len(reads), read_ns, len(raw)))
            if not raw:
                connection.receive_data(b"")
            else:
                if first_byte is None:
                    first_byte = read_ns
                connection.receive_data(raw)
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    break
                if isinstance(event, h11.Response):
                    status = event.status_code
                elif isinstance(event, h11.Data):
                    for sse in parser.feed(event.data):
                        if sse.data == "[DONE]":
                            continue
                        data = json.loads(sse.data)
                        choices = data.get("choices", [])
                        content = "".join(
                            choice.get("delta", {}).get("content") or "" for choice in choices
                        )
                        if content:
                            chunks.append(content)
                            if first_content is None:
                                first_content = read_ns
                        usage = data.get("usage")
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")
                        events.append(
                            ClientEvent(
                                len(events),
                                read_ns,
                                bool(content),
                                len(sse.raw),
                                data.get("mock_sent_ns"),
                            )
                        )
                elif isinstance(event, h11.EndOfMessage):
                    done = True
                    break
            if not raw:
                done = True
            elif read_delay_ms:
                await asyncio.sleep(read_delay_ms / 1000)
        return RequestResult(
            rid,
            client.client_port,
            client.t_connect_ns,
            client.t_tls_ns,
            t_sent_ns,
            first_byte,
            first_content,
            time.monotonic_ns(),
            status,
            "".join(chunks),
            prompt_tokens,
            completion_tokens,
            tuple(events),
            tuple(reads),
            tuple(client.records),
        )
