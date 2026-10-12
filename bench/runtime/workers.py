"""Runtime-gate harness v2: one role per OS process (see ``run.py``).

Each role runs its own event loop (uvloop on Linux), so the measured server never shares a
loop or a GIL with the load it is measured under. Results are written as JSON to ``--out``.
Latencies use ``time.monotonic_ns()`` (CLOCK_MONOTONIC, shared by every process on the host),
never ``loop.time()``, which uvloop quantizes to 1 ms.
"""

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any

import h11
import psutil

from crosstalk.loadgen.client import RequestResult, request
from crosstalk.loadgen.tls_client import client_context
from crosstalk.shaper.server import ShaperMetrics, ShaperSettings, run, tls_context
from crosstalk.shaper.sse import SSEParser
from crosstalk.testing.mock_upstream import run as run_mock

WARMUP_TICKS = 50
Outcome = RequestResult | BaseException


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 0.5),
        "p99_ms": percentile(values, 0.99),
        "p999_ms": percentile(values, 0.999),
        "max_ms": max(values),
    }


def _cpu(process: psutil.Process) -> float:
    times = process.cpu_times()
    return times.user + times.system


class CpuWindow:
    """CPU seconds per wall second, summed per role, over one measured phase."""

    def __init__(self, roles: dict[str, list[psutil.Process]]) -> None:
        self.roles = roles
        self.start_wall = time.perf_counter()
        self.start = {name: sum(map(_cpu, group)) for name, group in roles.items()}

    def stop(self) -> dict[str, float]:
        wall = time.perf_counter() - self.start_wall
        result: dict[str, float] = {"wall_seconds": wall}
        for name, group in self.roles.items():
            result[f"{name}_cpu_cores"] = (sum(map(_cpu, group)) - self.start[name]) / wall
            result[f"{name}_rss_mb"] = sum(p.memory_info().rss for p in group) / 1_048_576
        return result


def roles(args: argparse.Namespace) -> dict[str, list[psutil.Process]]:
    found = {"client": [psutil.Process()]}
    if args.shaper_pids:
        found["shaper"] = [psutil.Process(int(pid)) for pid in args.shaper_pids.split(",")]
    if args.mock_pid:
        found["mock"] = [psutil.Process(args.mock_pid)]
    return found


def body(events: int, delay_ms: float, **extra: object) -> dict[str, object]:
    return {
        "model": "mock",
        "messages": [{"role": "user", "content": "synthetic"}],
        "stream": True,
        "mock_events": events,
        "mock_delay_ms": delay_ms,
        **extra,
    }


def proxied_samples(results: list[Outcome]) -> list[float]:
    return [
        (event.received_ns - event.mock_sent_ns) / 1_000_000
        for result in results
        if not isinstance(result, BaseException)
        for event in result.events
        if event.mock_sent_ns is not None
    ]


def errors(results: list[Outcome]) -> int:
    return sum(isinstance(result, BaseException) or result.status != 200 for result in results)


def write_json(path: str, value: object) -> None:
    Path(path).write_text(json.dumps(value), encoding="utf-8")


# --------------------------------------------------------------------------- servers


async def mock_role(args: argparse.Namespace) -> None:
    await run_mock("127.0.0.1", args.port)


async def shaper_role(args: argparse.Namespace) -> None:
    """The production ``run()`` with settings from CT_SHAPER_*; metrics exported to a file."""
    settings = ShaperSettings()
    metrics = ShaperMetrics()
    path = Path(args.metrics_file)

    def dump() -> None:
        snapshot = {
            "t_ns": time.monotonic_ns(),
            "active_connections": metrics.active_connections,
            "peak_connections": metrics.peak_connections,
            "peak_write_buffer_bytes": metrics.peak_write_buffer_bytes,
            "stall_timeouts": metrics.stall_timeouts,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot), encoding="utf-8")
        os.replace(temporary, path)

    async def exporter() -> None:
        while True:
            dump()
            await asyncio.sleep(0.05)

    task = asyncio.create_task(exporter())
    try:
        await run(settings, metrics)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        dump()


def _peak_rss_mb() -> float | None:
    if sys.platform != "linux":
        return None
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def timer_server_role(args: argparse.Namespace) -> None:
    """G2: per-stream absolute deadlines, one ~200-byte TLS record per tick.

    Deadlines are on ``time.monotonic_ns()``. As in the shaper's emitter, a task sleeps for
    the remaining time and sleeps again if woken early, so lateness is never negative and is
    not rounded to the loop clock's millisecond resolution. All streams share one tick phase
    (worst case: every stream writes in the same loop iteration), as in harness v1.
    """
    n_streams: int = args.streams
    interval_ns = round(args.interval * 1e9)
    count = max(1, round(args.duration / args.interval))
    payload = b"x" * 200
    context = tls_context(ShaperSettings(cert=Path(args.cert), key=Path(args.key)))
    lateness: list[float] = []
    steady: list[float] = []
    ready = asyncio.Event()
    finished = asyncio.Event()
    connected = 0
    done = 0
    start_ns = 0
    marks: dict[str, float] = {}

    async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connected, done, start_ns
        connected += 1
        if connected == n_streams:
            start_ns = time.monotonic_ns() + 50_000_000
            marks["cpu"], marks["wall"] = time.process_time(), time.perf_counter()
            ready.set()
        await ready.wait()
        try:
            for tick in range(count):
                deadline = start_ns + tick * interval_ns
                while (remaining := deadline - time.monotonic_ns()) > 0:
                    await asyncio.sleep(remaining / 1e9)
                late = (time.monotonic_ns() - deadline) / 1_000_000
                lateness.append(late)
                if tick >= WARMUP_TICKS:
                    steady.append(late)
                writer.transport.write(payload)
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError, ssl.SSLError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 5)
            done += 1
            if done == n_streams:
                marks["cpu_end"], marks["wall_end"] = time.process_time(), time.perf_counter()
                finished.set()

    server = await asyncio.start_server(handle, "127.0.0.1", args.port, ssl=context, backlog=4096)
    async with server:
        await finished.wait()
    write_json(
        args.out,
        {
            "streams": n_streams,
            **summary(lateness),
            "steady_state_excluding_first_50_ticks": summary(steady),
            "server_cpu_cores": (marks["cpu_end"] - marks["cpu"])
            / (marks["wall_end"] - marks["wall"]),
            "server_rss_mb": psutil.Process().memory_info().rss / 1_048_576,
            "server_peak_rss_mb": _peak_rss_mb(),
        },
    )


# --------------------------------------------------------------------------- clients


async def timer_clients_role(args: argparse.Namespace) -> None:
    context = client_context(args.cert)
    window = CpuWindow(roles(args))

    async def client() -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", args.port, ssl=context)
        try:
            while await reader.read(65_536):
                continue
        finally:
            writer.close()
            with suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()

    await asyncio.gather(*(client() for _ in range(args.streams)))
    write_json(args.out, window.stop())


async def direct_latencies(port: int, n_events: int) -> list[float]:
    """Plain HTTP straight to the mock upstream, timed on the same clock as the proxy path."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    connection = h11.Connection(h11.CLIENT)
    payload = json.dumps(body(n_events, 25)).encode()
    writer.write(
        connection.send(
            h11.Request(
                method=b"POST",
                target=b"/v1/chat/completions",
                headers=[
                    (b"Host", b"localhost"),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(payload)).encode()),
                    (b"Connection", b"close"),
                ],
            )
        )
        + connection.send(h11.Data(data=payload))
        + connection.send(h11.EndOfMessage())
    )
    await writer.drain()
    parser = SSEParser()
    latencies: list[float] = []
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            connection.receive_data(await reader.read(65_536))
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


def decompose(results: list[Outcome], oracle_dir: Path) -> tuple[list[float], list[float]]:
    """Diagnostic split of proxied latency at the shaper's own write timestamp.

    For the passthrough arm, content event ``i`` of a request is emission ``seq = i + 1``
    (``seq 0`` is the header block). ``t_emit_ns`` is taken just before the shaper's
    ``transport.write``; every process reads the same CLOCK_MONOTONIC.
    """
    wanted = {r.request_id: r for r in results if not isinstance(r, BaseException)}
    emitted: dict[tuple[str, int], int] = {}
    for path in oracle_dir.glob("*.ndjson"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["request_id"] in wanted and row["kind"] == "content":
                emitted[(row["request_id"], row["seq"])] = row["t_emit_ns"]
    upstream_to_write: list[float] = []
    write_to_client: list[float] = []
    for request_id, result in wanted.items():
        for event in result.events:
            t_emit = emitted.get((request_id, event.index + 1))
            if event.mock_sent_ns is None or t_emit is None:
                continue
            upstream_to_write.append((t_emit - event.mock_sent_ns) / 1_000_000)
            write_to_client.append((event.received_ns - t_emit) / 1_000_000)
    return upstream_to_write, write_to_client


async def relay_clients_role(args: argparse.Namespace) -> None:
    """G3 client shard: direct then proxied streams from this one process, per repetition.

    Every shard of a run gets the same ``--start-at-ns`` and runs each phase in a fixed time
    slot, so the direct phases of all shards overlap one another and never a proxied phase.
    Raw samples are returned; the orchestrator pools the shards and takes percentiles.
    """
    watched = roles(args)
    slot_ns = round((args.events * 0.025 + args.slot_margin) * 1e9)
    repetitions: list[dict[str, Any]] = []
    proxied_by_rep: list[list[Outcome]] = []

    async def slot(index: int) -> float:
        begin = args.start_at_ns + index * slot_ns
        overrun = time.monotonic_ns() - begin
        if overrun < 0:
            await asyncio.sleep(-overrun / 1e9)
        return max(0, overrun) / 1e9

    for rep in range(args.reps):
        direct_overrun = await slot(2 * rep)
        window = CpuWindow(watched)
        direct = await asyncio.gather(
            *(direct_latencies(args.mock_port, args.events) for _ in range(args.concurrency))
        )
        direct_resources = window.stop()
        proxied_overrun = await slot(2 * rep + 1)
        window = CpuWindow(watched)
        proxied: list[Outcome] = await asyncio.gather(
            *(
                request("127.0.0.1", args.shaper_port, body(args.events, 25), args.cert)
                for _ in range(args.concurrency)
            ),
            return_exceptions=True,
        )
        proxied_resources = window.stop()
        proxied_by_rep.append(proxied)
        repetitions.append(
            {
                "direct_samples": [item for stream in direct for item in stream],
                "proxied_samples": proxied_samples(proxied),
                "errors": errors(proxied),
                "exceptions": sorted(
                    {type(item).__name__ for item in proxied if isinstance(item, BaseException)}
                ),
                "slot_overrun_seconds": max(direct_overrun, proxied_overrun),
                "resources_direct_phase": direct_resources,
                "resources_proxied_phase": proxied_resources,
            }
        )
    await asyncio.sleep(1)  # the shaper flushes its emission log once idle
    for repetition, proxied in zip(repetitions, proxied_by_rep, strict=True):
        upstream_to_write, write_to_client = decompose(proxied, Path(args.oracle_dir))
        repetition["split_upstream_to_write"] = upstream_to_write
        repetition["split_write_to_client"] = write_to_client
    write_json(args.out, repetitions)


def read_metrics(path: Path) -> dict[str, int]:
    for _ in range(50):
        with suppress(OSError, ValueError):
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.01)
    raise RuntimeError(f"Shaper metrics unreadable: {path}")


async def backpressure_clients_role(args: argparse.Namespace) -> None:
    """G5: 63 normal streams, twice without and then once with one 1 KiB/s slow reader.

    The two slow-free phases are an A/A control: how far normal-client p99 moves between
    identical phases bounds what the gate's 1 ms change can resolve on this host.
    """
    watched = roles(args)
    metrics_path = Path(args.metrics_file)
    normal = body(args.normal_events, 25)

    async def healthy() -> tuple[dict[str, Any], float, int]:
        window = CpuWindow(watched)
        results: list[Outcome] = await asyncio.gather(
            *(request("127.0.0.1", args.shaper_port, normal, args.cert) for _ in range(63)),
            return_exceptions=True,
        )
        samples = proxied_samples(results)
        failed = errors(results)
        phase: dict[str, Any] = {"errors": failed, **summary(samples), "resources": window.stop()}
        return phase, percentile(samples, 0.99), failed

    without_a, p99_a, _ = await healthy()
    without_b, p99_b, errors_b = await healthy()
    slow_start_ns = time.monotonic_ns()
    slow = asyncio.create_task(
        request(
            "127.0.0.1",
            args.shaper_port,
            body(1000, 0, mock_text="x" * 16_000),
            args.cert,
            read_delay_ms=1000,
            read_chunk_bytes=1024,
        )
    )
    await asyncio.sleep(0.1)
    with_slow, p99_slow, errors_slow = await healthy()
    abort_ns: int | None = None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        snapshot = read_metrics(metrics_path)
        if snapshot["stall_timeouts"] > 0:
            abort_ns = snapshot["t_ns"]
            break
        await asyncio.sleep(0.05)
    slow.cancel()
    with suppress(asyncio.CancelledError, h11.RemoteProtocolError, ssl.SSLError, OSError):
        await slow
    await asyncio.sleep(0.5)
    final = read_metrics(metrics_path)
    write_json(
        args.out,
        {
            "slow_reader_bytes_per_second": 1024,
            "normal_streams": 63,
            "normal_events_per_stream": args.normal_events,
            "stall_timeout_seconds": args.stall_timeout,
            "slow_stream_aborted": final["stall_timeouts"] > 0,
            "slow_abort_seen_after_seconds": None
            if abort_ns is None
            else (abort_ns - slow_start_ns) / 1e9,
            "peak_write_buffer_bytes": final["peak_write_buffer_bytes"],
            "buffer_cap_bytes": 1_048_576,
            "buffer_within_cap": final["peak_write_buffer_bytes"] <= 1_048_576,
            "normal_without_slow_a": without_a,
            "normal_without_slow_b": without_b,
            "normal_with_slow": with_slow,
            "normal_errors_without_slow": errors_b,
            "normal_errors_with_slow": errors_slow,
            "normal_p99_without_slow_ms": p99_b,
            "normal_p99_with_slow_ms": p99_slow,
            "normal_p99_change_ms": p99_slow - p99_b,
            "aa_control_p99_change_ms": p99_b - p99_a,
            "active_connections_after": final["active_connections"],
        },
    )


async def soak_clients_role(args: argparse.Namespace) -> None:
    """G4 soak: continuous relay streams through the shaper; shaper resources sampled."""
    watched = roles(args)
    shaper = watched["shaper"][0]
    start = time.monotonic()
    stop_at = start + args.duration
    window: list[float] = []
    totals = {"requests": 0, "errors": 0}
    samples: list[dict[str, object]] = []
    cpu_marks = {name: sum(map(_cpu, group)) for name, group in watched.items()}
    wall_mark = start

    async def stream() -> None:
        while time.monotonic() < stop_at:
            try:
                result = await request(
                    "127.0.0.1", args.shaper_port, body(args.events, 25), args.cert
                )
            except Exception as error:  # counted and logged; the stream keeps going
                totals["errors"] += 1
                print(f"soak request failed: {error!r}", file=sys.stderr)
                continue
            totals["requests"] += 1
            if result.status != 200:
                totals["errors"] += 1
            window.extend(proxied_samples([result]))

    def sample() -> None:
        nonlocal wall_mark
        now = time.monotonic()
        cpu = {name: sum(map(_cpu, group)) for name, group in watched.items()}
        elapsed = max(now - wall_mark, 1e-9)
        samples.append(
            {
                "elapsed_seconds": now - start,
                "shaper_rss_mb": shaper.memory_info().rss / 1_048_576,
                "shaper_fds": shaper.num_fds() if sys.platform == "linux" else None,
                "shaper_threads": shaper.num_threads(),
                "mock_rss_mb": watched["mock"][0].memory_info().rss / 1_048_576,
                "client_rss_mb": watched["client"][0].memory_info().rss / 1_048_576,
                **{f"{name}_cpu_cores": (cpu[name] - cpu_marks[name]) / elapsed for name in cpu},
                "requests_completed": totals["requests"],
                "errors": totals["errors"],
                "window_relay_p99_ms": percentile(window, 0.99) if window else None,
                "window_events": len(window),
            }
        )
        cpu_marks.update(cpu)
        wall_mark = now
        window.clear()

    async def monitor() -> None:
        sample()
        while (remaining := stop_at - time.monotonic()) > 0:
            await asyncio.sleep(min(args.sample_seconds, remaining))
            sample()

    await asyncio.gather(monitor(), *(stream() for _ in range(args.streams)))
    write_json(args.out, samples)


ROLES: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, None]]] = {
    "mock": mock_role,
    "shaper": shaper_role,
    "timer-server": timer_server_role,
    "timer-clients": timer_clients_role,
    "relay-clients": relay_clients_role,
    "backpressure-clients": backpressure_clients_role,
    "soak-clients": soak_clients_role,
}


def run_loop(main: Coroutine[Any, Any, None]) -> None:
    if sys.platform == "linux":
        import uvloop

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(main)
    else:
        asyncio.run(main)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=sorted(ROLES))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--mock-port", type=int, default=0)
    parser.add_argument("--shaper-port", type=int, default=0)
    parser.add_argument("--shaper-pids", default="")
    parser.add_argument("--mock-pid", type=int, default=0)
    parser.add_argument("--metrics-file", default="")
    parser.add_argument("--oracle-dir", default="")
    parser.add_argument("--cert", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--streams", type=int, default=64)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--events", type=int, default=400)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--start-at-ns", type=int, default=0)
    parser.add_argument("--slot-margin", type=float, default=6)
    parser.add_argument("--normal-events", type=int, default=80)
    parser.add_argument("--stall-timeout", type=float, default=2)
    parser.add_argument("--sample-seconds", type=float, default=10)
    args = parser.parse_args()
    run_loop(ROLES[args.role](args))


if __name__ == "__main__":
    main()
