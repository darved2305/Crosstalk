import argparse
import asyncio
import functools
import json
import time
from typing import cast

import h11

ECHO_TOKEN_DELAY_MS = 5.0
SSE_END = bytes([10, 10])


def prompt_words(payload: dict[str, object]) -> list[str]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    items = cast(list[object], messages)
    if not items or not isinstance(items[-1], dict):
        return []
    return str(cast(dict[str, object], items[-1]).get("content", "")).split()


def echo_plan(payload: dict[str, object]) -> list[str]:
    """Deterministic, prompt-dependent tokens: the prompt's words, twice over.

    Natural length is twice the word count, capped by ``max_completion_tokens`` and raised
    to ``min_tokens``, so MATCH-PR length forcing behaves as it does in vLLM.
    """
    words = prompt_words(payload) or ["empty"]
    count = 2 * len(words)
    minimum = payload.get("min_tokens")
    if isinstance(minimum, int):
        count = max(count, minimum)
    cap = payload.get("max_completion_tokens", payload.get("max_tokens", 512))
    count = min(count, int(cap) if isinstance(cap, int) else 512)
    return [words[index % len(words)] + " " for index in range(count)]


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    token_delay_ms: float = ECHO_TOKEN_DELAY_MS,
) -> None:
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
        if "mock_events" in payload:
            n = min(int(payload["mock_events"]), 1000)
            tokens = [str(payload.get("mock_text", "x"))] * n
            delays = [min(float(payload.get("mock_delay_ms", 5)), 1000) / 1000] * n
        else:
            tokens = echo_plan(payload)
            n = len(tokens)
            # Longer words take slightly longer, giving the timing channel a content signal.
            delays = [(token_delay_ms + 0.05 * len(token)) / 1000 for token in tokens]
        prompt_tokens = len(prompt_words(payload))
        writer.write(
            connection.send(
                h11.Response(
                    status_code=200,
                    headers=[(b"Content-Type", b"text/event-stream"), (b"Connection", b"close")],
                )
            )
        )
        await writer.drain()
        echo = "mock_events" not in payload
        header: dict[str, object] = {"id": "mock", "object": "chat.completion.chunk"}
        if echo:
            # vLLM's shape: a role event, content deltas, then a finish event and usage.
            header.update({"created": int(time.time()), "model": str(payload.get("model", "mock"))})

        async def send(choice: dict[str, object], **extra: object) -> None:
            event = {**header, "choices": [{"index": 0, **choice}], **extra}
            raw = b"data: " + json.dumps(event, separators=(",", ":")).encode() + SSE_END
            writer.write(connection.send(h11.Data(data=raw)))
            await writer.drain()

        if echo:
            await send({"delta": {"role": "assistant", "content": ""}, "finish_reason": None})
        for index, text in enumerate(tokens):
            await send(
                {"delta": {"content": text}, "finish_reason": None},
                mock_sent_ns=time.monotonic_ns(),
            )
            if delays[index] and index < n - 1:
                await asyncio.sleep(delays[index])
        if echo:
            await send(
                {"delta": {}, "finish_reason": "length" if payload.get("min_tokens") else "stop"}
            )
        usage: dict[str, object] = {
            "id": "mock",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": n},
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
    except ConnectionError:
        return
    finally:
        writer.close()


async def run(
    host: str = "0.0.0.0", port: int = 8000, token_delay_ms: float = ECHO_TOKEN_DELAY_MS
) -> None:
    handler = functools.partial(_handle, token_delay_ms=token_delay_ms)
    server = await asyncio.start_server(handler, host, port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible streaming mock upstream")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token-delay-ms", type=float, default=ECHO_TOKEN_DELAY_MS)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.host, arguments.port, arguments.token_delay_ms))
