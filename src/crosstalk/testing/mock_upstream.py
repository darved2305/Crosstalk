import asyncio
import json
import time

import h11


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    connection = h11.Connection(h11.SERVER)
    request: h11.Request | None = None
    body = bytearray()
    try:
        while True:
            event = connection.next_event()
            if event is h11.NEED_DATA:
                data = await asyncio.wait_for(reader.read(65_536), timeout=30)
                if not data:
                    return
                connection.receive_data(data)
                continue
            if isinstance(event, h11.Request):
                request = event
            elif isinstance(event, h11.Data):
                body.extend(event.data)
                if len(body) > 256 * 1024:
                    return
            elif isinstance(event, h11.EndOfMessage):
                break
        if request is None:
            return
        if request.target == b"/health":
            outgoing = connection.send(
                h11.Response(status_code=200, headers=[(b"Content-Length", b"0")])
            )
            writer.write(outgoing + connection.send(h11.EndOfMessage()))
            await writer.drain()
            return
        if request.target != b"/v1/chat/completions":
            writer.write(
                connection.send(h11.Response(status_code=404, headers=[(b"Content-Length", b"0")]))
            )
            writer.write(connection.send(h11.EndOfMessage()))
            await writer.drain()
            return
        payload = json.loads(body)
        n = min(int(payload.get("mock_events", 50)), 1000)
        delay = min(float(payload.get("mock_delay_ms", 5)), 1000) / 1000
        writer.write(
            connection.send(
                h11.Response(
                    status_code=200,
                    headers=[(b"Content-Type", b"text/event-stream"), (b"Connection", b"close")],
                )
            )
        )
        await writer.drain()
        for index in range(n):
            text = str(payload.get("mock_text", "x"))
            event = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                "mock_sent_ns": time.monotonic_ns(),
            }
            raw = b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"
            writer.write(connection.send(h11.Data(data=raw)))
            await writer.drain()
            if delay and index < n - 1:
                await asyncio.sleep(delay)
        usage: dict[str, object] = {
            "id": "mock",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": n},
        }
        writer.write(
            connection.send(
                h11.Data(
                    data=b"data: " + json.dumps(usage, separators=(",", ":")).encode() + b"\n\n"
                )
            )
        )
        writer.write(connection.send(h11.Data(data=b"data: [DONE]\n\n")))
        writer.write(connection.send(h11.EndOfMessage()))
        await writer.drain()
    finally:
        writer.close()


async def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = await asyncio.start_server(_handle, host, port)
    async with server:
        await server.serve_forever()


def main() -> None:
    asyncio.run(run())
