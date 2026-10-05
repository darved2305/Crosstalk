import asyncio
import signal
import socket
import ssl
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import h11
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from crosstalk.common.errors import ConfigError, UpstreamError, ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import Mode
from crosstalk.shaper.accounting import Emission, EmissionWriter
from crosstalk.shaper.framer import framed_segments, terminator
from crosstalk.shaper.http1 import MAX_BODY, response_headers, validate_request
from crosstalk.shaper.sse import SSEParser


class ShaperSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CT_SHAPER_", extra="forbid")
    host: str = "0.0.0.0"
    port: int = 8443
    admin_host: str = "0.0.0.0"
    admin_port: int = 9100
    cert: Path = Path("/run/secrets/tls.crt")
    key: Path = Path("/run/secrets/tls.key")
    upstream_url: str = "http://vllm:8000"
    mode: Mode = Mode.LAB
    oracle_dir: Path = Path("artifacts/shaper-log")
    stall_timeout: float = Field(default=30.0, gt=0)
    write_high_water: int = Field(default=65_536, ge=16_384, le=1_048_576)


@dataclass(slots=True)
class ShaperMetrics:
    active_connections: int = 0
    peak_connections: int = 0
    peak_write_buffer_bytes: int = 0
    stall_timeouts: int = 0

    def observe_buffer(self, writer: asyncio.StreamWriter) -> None:
        self.peak_write_buffer_bytes = max(
            self.peak_write_buffer_bytes, writer.transport.get_write_buffer_size()
        )


def tls_context(settings: ShaperSettings) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["http/1.1"])
    context.num_tickets = 0
    context.load_cert_chain(settings.cert, settings.key)
    return context


async def _read_request(reader: asyncio.StreamReader) -> tuple[h11.Request, bytes]:
    parser = h11.Connection(h11.SERVER)
    request: h11.Request | None = None
    body = bytearray()
    while True:
        event = parser.next_event()
        if event is h11.NEED_DATA:
            data = await asyncio.wait_for(reader.read(65_536), timeout=30)
            if not data:
                raise ValidationError("Incomplete HTTP request")
            parser.receive_data(data)
            continue
        if isinstance(event, h11.Request):
            request = event
        elif isinstance(event, h11.Data):
            body.extend(event.data)
            if len(body) > MAX_BODY:
                raise ValidationError("Request body exceeds limit")
        elif isinstance(event, h11.EndOfMessage):
            if request is None:
                raise ValidationError("Missing request line")
            return request, bytes(body)


async def _drain(
    writer: asyncio.StreamWriter, timeout: float, metrics: ShaperMetrics | None = None
) -> None:
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
    except TimeoutError:
        if metrics is not None:
            metrics.stall_timeouts += 1
        raise


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    settings: ShaperSettings,
    upstream: aiohttp.ClientSession,
    emissions: EmissionWriter,
    metrics: ShaperMetrics | None = None,
) -> None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        writer.close()
        return
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 1:
        raise ConfigError("TCP_NODELAY could not be enabled")
    headers_sent = False
    request_id = ""
    sequence = 0
    try:
        request, body = await _read_request(reader)
        _, lab = validate_request(request, body, settings.mode)
        request_id = lab.request_id if lab else ""
        timeout = aiohttp.ClientTimeout(total=None, connect=5)
        async with upstream.post(
            settings.upstream_url.rstrip("/") + "/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        ) as response:
            if response.status != 200:
                raise UpstreamError(f"Upstream returned {response.status}")
            head = response_headers(200)
            writer.write(head)
            # The header block is its own write and therefore its own TLS record; log it so
            # every client-visible response record has an emission.
            await emissions.record(
                Emission(
                    request_id,
                    sequence,
                    time.monotonic_ns(),
                    "headers",
                    len(head),
                    0,
                    0,
                    0,
                    0,
                    0,
                    "a0",
                )
            )
            sequence += 1
            await _drain(writer, settings.stall_timeout, metrics)
            headers_sent = True
            parser = SSEParser()
            done = False
            transport = writer.transport
            incoming = response.content.iter_any().__aiter__()
            first_read = True
            while True:
                try:
                    data = await asyncio.wait_for(
                        anext(incoming), timeout=120 if first_read else 30
                    )
                except StopAsyncIteration:
                    break
                first_read = False
                for event in parser.feed(data):
                    if event.data == "[DONE]":
                        done = True
                        break
                    for segment in framed_segments(event.raw):
                        t_emit = time.monotonic_ns()
                        transport.write(segment)
                        if metrics is not None:
                            metrics.observe_buffer(writer)
                        await emissions.record(
                            Emission(
                                request_id,
                                sequence,
                                t_emit,
                                "content",
                                len(segment),
                                len(event.raw),
                                0,
                                1,
                                0,
                                0,
                                "a0",
                            )
                        )
                        sequence += 1
                        if transport.get_write_buffer_size() >= settings.write_high_water:
                            await _drain(writer, settings.stall_timeout, metrics)
                if done:
                    break
            final = terminator()
            transport.write(final)
            if metrics is not None:
                metrics.observe_buffer(writer)
            await emissions.record(
                Emission(
                    request_id,
                    sequence,
                    time.monotonic_ns(),
                    "terminator",
                    len(final),
                    0,
                    0,
                    0,
                    0,
                    0,
                    "a0",
                )
            )
            await _drain(writer, settings.stall_timeout, metrics)
    except (
        ConfigError,
        ValidationError,
        ValueError,
        UnicodeError,
        h11.RemoteProtocolError,
    ):
        if not headers_sent:
            writer.write(response_headers(400))
            writer.write(b"0\r\n\r\n")
    except (aiohttp.ClientError, TimeoutError, UpstreamError):
        if not headers_sent:
            writer.write(response_headers(502))
            writer.write(b"0\r\n\r\n")
        else:
            error_event = (
                b'data: {"error":{"type":"upstream_error","message":"Upstream stream failed"}}\n\n'
            )
            for segment in framed_segments(error_event):
                writer.transport.write(segment)
                await emissions.record(
                    Emission(
                        request_id,
                        sequence,
                        time.monotonic_ns(),
                        "error",
                        len(segment),
                        0,
                        0,
                        0,
                        0,
                        0,
                        "a0",
                    )
                )
                sequence += 1
            writer.transport.write(terminator())
    finally:
        writer.close()
        with suppress(TimeoutError, ConnectionError, ssl.SSLError):
            await asyncio.wait_for(writer.wait_closed(), timeout=5)


async def run(settings: ShaperSettings, metrics: ShaperMetrics | None = None) -> None:
    context = tls_context(settings)
    active: set[asyncio.Task[None]] = set()
    shutdown = asyncio.Event()
    async with (
        EmissionWriter(settings.oracle_dir / f"{new_ulid()}.ndjson") as emissions,
        aiohttp.ClientSession() as upstream,
    ):

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task is not None:
                active.add(task)
            if metrics is not None:
                metrics.active_connections += 1
                metrics.peak_connections = max(metrics.peak_connections, metrics.active_connections)
            try:
                await handle_connection(reader, writer, settings, upstream, emissions)
            finally:
                if task is not None:
                    active.discard(task)
                if metrics is not None:
                    metrics.active_connections -= 1

        async def admin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            line = await reader.readline()
            path = line.split(b" ")[1] if b" " in line else b""
            if path == b"/healthz":
                status = 200
            elif path == b"/readyz":
                try:
                    async with upstream.get(
                        settings.upstream_url.rstrip("/") + "/health",
                        timeout=aiohttp.ClientTimeout(total=3),
                    ) as check:
                        status = 200 if check.status == 200 else 503
                except (aiohttp.ClientError, TimeoutError):
                    status = 503
            else:
                status = 404
            writer.write(
                f"HTTP/1.1 {status} OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(
            serve, settings.host, settings.port, ssl=context, backlog=4096
        )
        admin_server = await asyncio.start_server(admin, settings.admin_host, settings.admin_port)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(signum, shutdown.set)
        async with server, admin_server:
            try:
                await shutdown.wait()
            finally:
                server.close()
                admin_server.close()
                await asyncio.gather(server.wait_closed(), admin_server.wait_closed())
                if active:
                    _, pending = await asyncio.wait(active, timeout=60)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
