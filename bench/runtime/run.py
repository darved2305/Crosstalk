import argparse
import asyncio
import json
import platform
import socket
import ssl
import statistics
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import h11
import psutil

from crosstalk.common.certificates import create_lab_certificate
from crosstalk.loadgen.client import request
from crosstalk.loadgen.tls_client import client_context
from crosstalk.shaper.server import ShaperSettings, run, tls_context
from crosstalk.shaper.sse import SSEParser
from crosstalk.testing.mock_upstream import run as run_mock


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_port(port: int) -> None:
    for _ in range(500):
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.01)
    raise RuntimeError(f"Port {port} failed to listen")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


async def record_fidelity(directory: Path, cert: Path, key: Path) -> dict[str, object]:
    mock_port, shaper_port, admin_port = free_port(), free_port(), free_port()
    mock = asyncio.create_task(run_mock("127.0.0.1", mock_port))
    await wait_port(mock_port)
    settings = ShaperSettings(
        host="127.0.0.1",
        port=shaper_port,
        admin_host="127.0.0.1",
        admin_port=admin_port,
        cert=cert,
        key=key,
        upstream_url=f"http://127.0.0.1:{mock_port}",
        oracle_dir=directory / "shaper-log",
    )
    shaper = asyncio.create_task(run(settings))
    await wait_port(admin_port)
    cases: dict[str, list[dict[str, object]]] = {
        "spaced": [],
        "back_to_back": [],
        "slow_reader": [],
    }
    client_results = []
    try:
        for case, delay, reader_delay in (
            ("spaced", 5, 0),
            ("back_to_back", 0, 0),
            ("slow_reader", 0, 25),
        ):
            for _ in range(3):
                result = await request(
                    "127.0.0.1",
                    shaper_port,
                    {
                        "model": "mock",
                        "messages": [{"role": "user", "content": "synthetic"}],
                        "stream": True,
                        "mock_events": 50,
                        "mock_delay_ms": delay,
                    },
                    str(cert),
                    read_delay_ms=reader_delay,
                )
                client_results.append((case, result))
    finally:
        shaper.cancel()
        mock.cancel()
        for task in (shaper, mock):
            with suppress(asyncio.CancelledError):
                await task
    rows = [
        json.loads(line)
        for path in (directory / "shaper-log").glob("*.ndjson")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    by_request: dict[str, list[int]] = {}
    for row in rows:
        by_request.setdefault(row["request_id"], []).append(row["framed_len"] + 17)
    for case, result in client_results:
        expected = by_request.get(result.request_id, [])
        observed = [record.length for record in result.records if record.content_type == 23]
        matched = any(
            observed[index : index + len(expected)] == expected
            for index in range(len(observed) - len(expected) + 1)
        )
        cases[case].append(
            {
                "emissions": len(expected),
                "exact_sequence": matched,
                "observed_application_records": len(observed),
            }
        )
    return {
        case: {
            "repetitions": repetitions,
            "median_exact": bool(
                statistics.median(int(item["exact_sequence"]) for item in repetitions)
            ),
        }
        for case, repetitions in cases.items()
    }


async def timer_case(
    n_streams: int, duration: float, cert: Path, key: Path
) -> dict[str, float | int]:
    interval = 0.02
    count = max(1, round(duration / interval))
    latencies: list[float] = []
    payload = b"x" * 200
    server_context = tls_context(ShaperSettings(cert=cert, key=key))
    client_tls = client_context(str(cert))
    ready = asyncio.Event()
    connected = 0
    process = psutil.Process()
    rss_before = process.memory_info().rss
    cpu_before = time.process_time()
    wall_before = time.perf_counter()

    async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connected
        connected += 1
        if connected == n_streams:
            ready.set()
        await ready.wait()
        loop = asyncio.get_running_loop()
        start = loop.time() + 0.05
        for tick in range(count):
            deadline = start + tick * interval
            future: asyncio.Future[None] = loop.create_future()
            loop.call_at(deadline, future.set_result, None)
            await future
            latencies.append((loop.time() - deadline) * 1000)
            writer.transport.write(payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]

    async def client() -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=client_tls)
        try:
            while await reader.read(65_536):
                continue
        finally:
            writer.close()
            await writer.wait_closed()

    async with server, asyncio.TaskGroup() as tasks:
        for _ in range(n_streams):
            tasks.create_task(client())
    wall = time.perf_counter() - wall_before
    return {
        "streams": n_streams,
        "samples": len(latencies),
        "p50_ms": percentile(latencies, 0.5),
        "p99_ms": percentile(latencies, 0.99),
        "p999_ms": percentile(latencies, 0.999),
        "max_ms": max(latencies),
        "cpu_cores": (time.process_time() - cpu_before) / wall,
        "rss_before_mb": rss_before / 1_048_576,
        "rss_after_mb": process.memory_info().rss / 1_048_576,
    }


async def direct_latencies(port: int, n_events: int) -> list[float]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    connection = h11.Connection(h11.CLIENT)
    body = json.dumps(
        {
            "model": "mock",
            "messages": [{"role": "user", "content": "synthetic"}],
            "stream": True,
            "mock_events": n_events,
            "mock_delay_ms": 25,
        }
    ).encode()
    writer.write(
        connection.send(
            h11.Request(
                method=b"POST",
                target=b"/v1/chat/completions",
                headers=[
                    (b"Host", b"localhost"),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                    (b"Connection", b"close"),
                ],
            )
        )
        + connection.send(h11.Data(data=body))
        + connection.send(h11.EndOfMessage())
    )
    await writer.drain()
    parser = SSEParser()
    latencies: list[float] = []
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            raw = await reader.read(65_536)
            connection.receive_data(raw)
        elif isinstance(event, h11.Data):
            now = time.monotonic_ns()
            for sse in parser.feed(event.data):
                if sse.data != "[DONE]":
                    sent = json.loads(sse.data).get("mock_sent_ns")
                    if sent is not None:
                        latencies.append((now - sent) / 1_000_000)
        elif isinstance(event, h11.EndOfMessage):
            break
    writer.close()
    await writer.wait_closed()
    return latencies


async def relay_latency(
    directory: Path, cert: Path, key: Path, n_events: int = 400
) -> dict[str, object]:
    mock_port, shaper_port, admin_port = free_port(), free_port(), free_port()
    mock = asyncio.create_task(run_mock("127.0.0.1", mock_port))
    await wait_port(mock_port)
    shaper = asyncio.create_task(
        run(
            ShaperSettings(
                host="127.0.0.1",
                port=shaper_port,
                admin_host="127.0.0.1",
                admin_port=admin_port,
                cert=cert,
                key=key,
                upstream_url=f"http://127.0.0.1:{mock_port}",
                oracle_dir=directory / "relay-log",
            )
        )
    )
    await wait_port(admin_port)
    results: dict[str, object] = {}
    try:
        for concurrency in (64, 256):
            repetitions: list[dict[str, float | int]] = []
            for _ in range(3):
                direct = await asyncio.gather(
                    *(direct_latencies(mock_port, n_events) for _ in range(concurrency))
                )
                proxied = await asyncio.gather(
                    *(
                        request(
                            "127.0.0.1",
                            shaper_port,
                            {
                                "model": "mock",
                                "messages": [{"role": "user", "content": "synthetic"}],
                                "stream": True,
                                "mock_events": n_events,
                                "mock_delay_ms": 25,
                            },
                            str(cert),
                        )
                        for _ in range(concurrency)
                    )
                )
                direct_samples = [item for stream in direct for item in stream]
                proxy_samples = [
                    (event.received_ns - event.mock_sent_ns) / 1_000_000
                    for result in proxied
                    for event in result.events
                    if event.mock_sent_ns is not None
                ]
                repetitions.append(
                    {
                        "direct_p99_ms": percentile(direct_samples, 0.99),
                        "proxy_p99_ms": percentile(proxy_samples, 0.99),
                        "added_p99_ms": percentile(proxy_samples, 0.99)
                        - percentile(direct_samples, 0.99),
                        "errors": sum(result.status != 200 for result in proxied),
                    }
                )
            results[str(concurrency)] = {
                "repetitions": repetitions,
                "median": sorted(repetitions, key=lambda item: item["added_p99_ms"])[1],
            }
    finally:
        shaper.cancel()
        mock.cancel()
        for task in (shaper, mock):
            with suppress(asyncio.CancelledError):
                await task
    return results


def stream_fidelity(cases: int) -> dict[str, int]:
    import random

    randomizer = random.Random(1847)
    for _ in range(cases):
        values = [
            "".join(
                randomizer.choice("abcdef0123456789") for _ in range(randomizer.randrange(1, 40))
            )
            for _ in range(randomizer.randrange(1, 20))
        ]
        raw = b"".join(b"data: " + value.encode() + b"\n\n" for value in values)
        parser = SSEParser()
        output = []
        offset = 0
        while offset < len(raw):
            size = randomizer.randrange(1, 32)
            output.extend(parser.feed(raw[offset : offset + size]))
            offset += size
        if [event.data for event in output] != values or b"".join(
            event.raw for event in output
        ) != raw:
            raise AssertionError("SSE content or raw bytes changed under chunking")
    return {"cases": cases, "mismatches": 0}


async def main(duration: float, output: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cert, key = create_lab_certificate(root)
        records = await record_fidelity(root, cert, key)
        fidelity = stream_fidelity(10_000 if duration >= 20 else 1000)
        relay = (
            await relay_latency(root, cert, key)
            if duration >= 20 and platform.system() == "Linux"
            else {"measured": False}
        )
        timers: dict[str, object] = {}
        for n_streams in (1, 64, 256):
            repetitions = [await timer_case(n_streams, duration, cert, key) for _ in range(3)]
            timers[str(n_streams)] = {
                "repetitions": repetitions,
                "median": sorted(repetitions, key=lambda item: item["p99_ms"])[1],
            }
    result = {
        "environment": {
            "system": platform.system(),
            "python": platform.python_version(),
            "openssl": ssl.OPENSSL_VERSION,
            "duration_seconds": duration,
            "runtime_gate_eligible": platform.system() == "Linux" and duration >= 20,
        },
        "record_fidelity": records,
        "stream_fidelity": fidelity,
        "timers": timers,
        "relay_latency": relay,
        "backpressure": {"measured": False},
        "soak": {"measured": False},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"Benchmark artifact already exists: {output}")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--output", type=Path, default=Path("artifacts/bench/runtime.json"))
    args = parser.parse_args()
    if platform.system() == "Linux":
        import uvloop

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(main(args.duration, args.output))
    else:
        asyncio.run(main(args.duration, args.output))
