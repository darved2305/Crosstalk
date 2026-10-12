"""The shaper's upstream pool must not cap concurrent streams at aiohttp's default of 100.

aiohttp's ``ClientTimeout.connect`` also bounds the wait for a free pool slot, so with the
default 100-connection pool every stream beyond the 100th failed with 502 after five
seconds while the first 100 were still streaming (Linux runtime gate, 2026-10-12).
"""

import asyncio
import json
import socket
from contextlib import suppress
from pathlib import Path

import h11
import pytest

from crosstalk.common.certificates import create_lab_certificate
from crosstalk.loadgen.client import request
from crosstalk.shaper.server import ShaperSettings, run

STREAMS = 150
HOLD_SECONDS = 8.0


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_port(port: int) -> None:
    for _ in range(500):
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.01)
    raise AssertionError(f"Port {port} did not open")


class BarrierUpstream:
    """Holds every stream open until ``STREAMS`` streams are open at the same time."""

    def __init__(self) -> None:
        self.open = 0
        self.peak = 0
        self.all_open = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = h11.Connection(h11.SERVER)
        try:
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    data = await reader.read(65_536)
                    if not data:
                        return
                    connection.receive_data(data)
                elif isinstance(event, h11.EndOfMessage):
                    break
            self.open += 1
            self.peak = max(self.peak, self.open)
            if self.open >= STREAMS:
                self.all_open.set()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.all_open.wait(), HOLD_SECONDS)
            chunk = {
                "id": "pool",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}],
            }
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                + b"data: "
                + json.dumps(chunk).encode()
                + b"\n\ndata: [DONE]\n\n"
            )
            await writer.drain()
        finally:
            writer.close()


@pytest.mark.asyncio
async def test_more_than_100_concurrent_upstream_streams(tmp_path: Path) -> None:
    cert, key = create_lab_certificate(tmp_path)
    upstream = BarrierUpstream()
    server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0, backlog=1024)
    upstream_port = server.sockets[0].getsockname()[1]
    shaper_port, admin_port = _port(), _port()
    settings = ShaperSettings(
        host="127.0.0.1",
        port=shaper_port,
        admin_host="127.0.0.1",
        admin_port=admin_port,
        cert=cert,
        key=key,
        upstream_url=f"http://127.0.0.1:{upstream_port}",
        oracle_dir=tmp_path / "oracle",
    )
    shaper = asyncio.create_task(run(settings))
    try:
        await _wait_port(admin_port)
        body: dict[str, object] = {
            "model": "mock",
            "messages": [{"role": "user", "content": "pool"}],
            "stream": True,
        }
        results = await asyncio.gather(
            *(request("127.0.0.1", shaper_port, body, str(cert)) for _ in range(STREAMS))
        )
    finally:
        shaper.cancel()
        with suppress(asyncio.CancelledError):
            await shaper
        server.close()
        await server.wait_closed()
    assert upstream.peak == STREAMS
    assert [result.status for result in results] == [200] * STREAMS
    assert all(result.content == "x" for result in results)


def test_upstream_pool_and_timeouts_follow_settings() -> None:
    from crosstalk.shaper.server import upstream_timeout

    settings = ShaperSettings(upstream_connections=300, upstream_connect_timeout=4)
    timeout = upstream_timeout(settings)
    # Waiting for a pool slot is not a connect failure; only the TCP connect is bounded.
    assert timeout.connect is None
    assert timeout.sock_connect == 4
    assert timeout.total is None
    assert ShaperSettings().upstream_connections >= 1024
