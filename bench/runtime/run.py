import argparse
import asyncio
import json
import platform
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import h11
import psutil

from crosstalk.common.certificates import create_lab_certificate
from crosstalk.loadgen.client import request
from crosstalk.loadgen.tls_client import client_context
from crosstalk.shaper.server import ShaperMetrics, ShaperSettings, run, tls_context
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


async def soak_case(duration: float, cert: Path, key: Path) -> dict[str, object]:
    n_streams = 64
    interval = 0.02
    ticks = round(duration / interval)
    server_context = tls_context(ShaperSettings(cert=cert, key=key))
    client_tls = client_context(str(cert))
    ready = asyncio.Event()
    connected = 0
    process = psutil.Process()
    samples: list[dict[str, float | int]] = []
    cpu_before = time.process_time()

    async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connected
        connected += 1
        if connected == n_streams:
            ready.set()
        await ready.wait()
        loop = asyncio.get_running_loop()
        start = loop.time() + 0.05
        for tick in range(ticks):
            deadline = start + tick * interval
            future: asyncio.Future[None] = loop.create_future()
            loop.call_at(deadline, future.set_result, None)
            await future
            writer.transport.write(b"x" * 200)
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

    async def monitor() -> None:
        await ready.wait()
        start = time.monotonic()
        while time.monotonic() - start < duration:
            samples.append(
                {
                    "elapsed_seconds": time.monotonic() - start,
                    "rss_mb": process.memory_info().rss / 1_048_576,
                    "tasks": len(asyncio.all_tasks()),
                    "fds": process.num_fds()
                    if platform.system() == "Linux"
                    else process.num_handles(),
                }
            )
            await asyncio.sleep(min(10, max(0.01, duration - (time.monotonic() - start))))
        samples.append(
            {
                "elapsed_seconds": time.monotonic() - start,
                "rss_mb": process.memory_info().rss / 1_048_576,
                "tasks": len(asyncio.all_tasks()),
                "fds": process.num_fds() if platform.system() == "Linux" else process.num_handles(),
            }
        )

    start_wall = time.perf_counter()
    async with server, asyncio.TaskGroup() as tasks:
        tasks.create_task(monitor())
        for _ in range(n_streams):
            tasks.create_task(client())
    wall = time.perf_counter() - start_wall
    first_rss = float(samples[0]["rss_mb"])
    last_rss = float(samples[-1]["rss_mb"])
    return {
        "measured": True,
        "duration_seconds": duration,
        "streams": n_streams,
        "rss_growth_fraction": (last_rss - first_rss) / first_rss,
        "peak_rss_mb": max(float(sample["rss_mb"]) for sample in samples),
        "cpu_cores": (time.process_time() - cpu_before) / wall,
        "samples": samples,
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


async def backpressure_case(directory: Path, cert: Path, key: Path) -> dict[str, object]:
    mock_port, shaper_port, admin_port = free_port(), free_port(), free_port()
    mock = asyncio.create_task(run_mock("127.0.0.1", mock_port))
    await wait_port(mock_port)
    metrics = ShaperMetrics()
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
                oracle_dir=directory / "backpressure-log",
                stall_timeout=2,
            ),
            metrics,
        )
    )
    await wait_port(admin_port)
    payload: dict[str, object] = {
        "model": "mock",
        "messages": [{"role": "user", "content": "synthetic"}],
        "stream": True,
        "mock_events": 10,
        "mock_delay_ms": 25,
    }

    async def healthy() -> tuple[int, float]:
        results = await asyncio.gather(
            *(request("127.0.0.1", shaper_port, payload, str(cert)) for _ in range(63)),
            return_exceptions=True,
        )
        samples = [
            (event.received_ns - event.mock_sent_ns) / 1_000_000
            for result in results
            if not isinstance(result, BaseException)
            for event in result.events
            if event.mock_sent_ns is not None
        ]
        errors = sum(
            isinstance(result, BaseException) or result.status != 200 for result in results
        )
        return errors, percentile(samples, 0.99)

    slow: asyncio.Task[object] | None = None
    try:
        baseline_errors, baseline_p99 = await healthy()
        tasks_before = len(asyncio.all_tasks())
        slow_payload = {
            **payload,
            "mock_events": 1000,
            "mock_delay_ms": 0,
            "mock_text": "x" * 16_000,
        }
        slow = asyncio.create_task(
            request(
                "127.0.0.1",
                shaper_port,
                slow_payload,
                str(cert),
                read_delay_ms=1000,
                read_chunk_bytes=1024,
            )
        )
        await asyncio.sleep(0.1)
        healthy_task = asyncio.create_task(healthy())
        peak_tasks = len(asyncio.all_tasks())
        while not healthy_task.done():
            peak_tasks = max(peak_tasks, len(asyncio.all_tasks()))
            await asyncio.sleep(0.01)
        healthy_errors, healthy_p99 = await healthy_task
        deadline = asyncio.get_running_loop().time() + 8
        while (
            metrics.stall_timeouts == 0
            and not slow.done()
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.05)
        slow.cancel()
        with suppress(
            asyncio.CancelledError, h11.RemoteProtocolError, ssl.SSLError, ConnectionError
        ):
            await slow
        await asyncio.sleep(0)
        return {
            "measured": True,
            "slow_reader_bytes_per_second": 1024,
            "stall_timeout_seconds": 2,
            "slow_stream_aborted": metrics.stall_timeouts > 0,
            "peak_write_buffer_bytes": metrics.peak_write_buffer_bytes,
            "buffer_cap_bytes": 1_048_576,
            "buffer_within_cap": metrics.peak_write_buffer_bytes <= 1_048_576,
            "normal_errors_without_slow": baseline_errors,
            "normal_errors_with_slow": healthy_errors,
            "normal_p99_without_slow_ms": baseline_p99,
            "normal_p99_with_slow_ms": healthy_p99,
            "normal_p99_change_ms": healthy_p99 - baseline_p99,
            "tasks_before": tasks_before,
            "peak_tasks_with_slow_and_healthy_clients": peak_tasks,
            "tasks_after": len(asyncio.all_tasks()),
            "active_connections_after": metrics.active_connections,
        }
    finally:
        if slow is not None and not slow.done():
            slow.cancel()
            with suppress(
                asyncio.CancelledError, h11.RemoteProtocolError, ssl.SSLError, ConnectionError
            ):
                await slow
        shaper.cancel()
        mock.cancel()
        for task in (shaper, mock):
            with suppress(asyncio.CancelledError):
                await task


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


async def main(duration: float, output: Path, soak_seconds: float) -> None:
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
        backpressure = [
            await backpressure_case(root / f"backpressure-{index}", cert, key) for index in range(3)
        ]
        soak = await soak_case(soak_seconds, cert, key) if soak_seconds > 0 else {"measured": False}
        timers: dict[str, object] = {}
        for n_streams in (1, 64, 256):
            repetitions = [await timer_case(n_streams, duration, cert, key) for _ in range(3)]
            timers[str(n_streams)] = {
                "repetitions": repetitions,
                "median": sorted(repetitions, key=lambda item: item["p99_ms"])[1],
            }
    result = {
        "environment": {
            "measured_at": datetime.now(UTC).isoformat(),
            "command": sys.argv,
            "kernel": platform.release(),
            "event_loop": type(asyncio.get_running_loop()).__module__,
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip(),
            "git_dirty": bool(
                subprocess.run(
                    ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
                ).stdout.strip()
            ),
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
        "backpressure": {
            "repetitions": backpressure,
            "median": sorted(backpressure, key=lambda item: item["normal_p99_change_ms"])[1],
        },
        "soak": soak,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"Benchmark artifact already exists: {output}")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--soak-seconds", type=float, default=0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/bench/runtime.json"))
    args = parser.parse_args()
    if platform.system() == "Linux":
        import uvloop

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(main(args.duration, args.output, args.soak_seconds))
    else:
        asyncio.run(main(args.duration, args.output, args.soak_seconds))
