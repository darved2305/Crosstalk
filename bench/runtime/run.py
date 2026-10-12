"""Day-1 runtime gate harness, version 2 (IMPLEMENTATION_PLAN §3.2).

Version 1 (evidence ``docs/evidence/runtime-linux-20261012.json``) ran the clients, the mock
upstream and the measured server on one event loop in one process, and timed lateness with
uvloop's millisecond ``loop.time()``. Version 2 runs each role as its own OS process, as the
shaper is deployed (RESEARCH §11.3): the measured server (shaper or timer server), the mock
upstream, and one client process. Direct and proxied relay streams come from the same client
process. All latencies use ``time.monotonic_ns()``.

``--suite gates`` runs G1, G2, G3, G4 (CPU/RSS) and G5, G6; ``--suite soak`` runs the G4
30-minute RSS soak through the real shaper. Three repetitions; the median repetition decides.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import signal
import ssl
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from workers import percentile, summary

import crosstalk
from crosstalk.common.certificates import create_lab_certificate
from crosstalk.loadgen.client import RequestResult, request
from crosstalk.shaper.sse import SSEEvent, SSEParser

HARNESS_VERSION = 2
WORKERS = Path(__file__).with_name("workers.py")
REPO = Path(__file__).resolve().parents[2]

TOPOLOGY = {
    "harness_version": HARNESS_VERSION,
    "processes": {
        "orchestrator": "bench/runtime/run.py; G1 client requests and G6 property cases",
        "shaper": "bench/runtime/workers.py shaper: crosstalk.shaper.server.run() with "
        "ShaperSettings from CT_SHAPER_* env (uvloop), metrics exported to a file",
        "mock_upstream": "bench/runtime/workers.py mock: crosstalk.testing.mock_upstream.run "
        "(uvloop)",
        "timer_server": "bench/runtime/workers.py timer-server: TLS server measured for G2/G4",
        "clients": "bench/runtime/workers.py *-clients: one process per measured phase; G3 "
        "direct and proxied streams come from the same client process",
    },
    "clock": "time.monotonic_ns() (CLOCK_MONOTONIC, system-wide); loop.time() not used",
    "cpu_pinning": None,
    "notes": "All processes share one host and its CPUs over TCP loopback.",
}


class Process:
    def __init__(self, name: str, logs: Path, *args: str, env: dict[str, str] | None = None):
        self.name = name
        self.log_path = logs / f"{name}-{time.monotonic_ns()}.log"
        self.log = self.log_path.open("wb")
        self.popen = subprocess.Popen(
            [sys.executable, str(WORKERS), *args],
            stdout=self.log,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})},
        )

    @property
    def pid(self) -> int:
        return self.popen.pid

    async def wait(self, timeout: float) -> None:
        code = await asyncio.wait_for(asyncio.to_thread(self.popen.wait), timeout)
        if code != 0:
            raise RuntimeError(f"{self.name} exited {code}: {self.tail()}")

    def tail(self) -> str:
        self.log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-3000:]

    async def stop(self) -> None:
        if self.popen.poll() is None:
            if os.name == "nt":
                self.popen.terminate()
            else:
                self.popen.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.popen.wait), 70)
            except TimeoutError:
                self.popen.kill()
                await asyncio.to_thread(self.popen.wait)
        self.log.close()


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_port(port: int, process: Process | None = None) -> None:
    for _ in range(1000):
        if process is not None and process.popen.poll() is not None:
            raise RuntimeError(f"{process.name} exited early: {process.tail()}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.01)
    raise RuntimeError(f"Port {port} failed to listen")


class Stack:
    """Mock upstream and ``workers`` shaper processes, each in its own OS process.

    With ``workers > 1`` the shapers share the listening port via SO_REUSEPORT (the §3.2
    scale-out escape hatch); each has its own admin port, metrics file and emission log.
    """

    def __init__(
        self, root: Path, cert: Path, key: Path, name: str, workers: int = 1, **settings: object
    ):
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.cert, self.key = cert, key
        self.workers = workers
        self.settings = settings
        self.mock_port, self.shaper_port = free_port(), free_port()
        self.admin_ports = [free_port() for _ in range(workers)]
        self.metrics_file = self.root / "metrics-0.json"
        self.oracle_dir = self.root / "shaper-log"
        self.shapers: list[Process] = []

    async def __aenter__(self) -> "Stack":
        self.mock = Process("mock", self.root, "mock", "--port", str(self.mock_port))
        await wait_port(self.mock_port, self.mock)
        for index, admin_port in enumerate(self.admin_ports):
            env = {
                "CT_SHAPER_HOST": "127.0.0.1",
                "CT_SHAPER_PORT": str(self.shaper_port),
                "CT_SHAPER_ADMIN_HOST": "127.0.0.1",
                "CT_SHAPER_ADMIN_PORT": str(admin_port),
                "CT_SHAPER_CERT": str(self.cert),
                "CT_SHAPER_KEY": str(self.key),
                "CT_SHAPER_UPSTREAM_URL": f"http://127.0.0.1:{self.mock_port}",
                "CT_SHAPER_ORACLE_DIR": str(self.oracle_dir),
                "CT_SHAPER_REUSE_PORT": str(self.workers > 1).lower(),
                **{f"CT_SHAPER_{key.upper()}": str(value) for key, value in self.settings.items()},
            }
            metrics = self.root / f"metrics-{index}.json"
            shaper = Process(
                f"shaper-{index}", self.root, "shaper", "--metrics-file", str(metrics), env=env
            )
            self.shapers.append(shaper)
            await wait_port(admin_port, shaper)
        return self

    async def __aexit__(self, *_: object) -> None:
        for shaper in self.shapers:
            await shaper.stop()
        await self.mock.stop()

    def client_args(self) -> list[str]:
        return [
            "--shaper-port",
            str(self.shaper_port),
            "--mock-port",
            str(self.mock_port),
            "--shaper-pids",
            ",".join(str(shaper.pid) for shaper in self.shapers),
            "--mock-pid",
            str(self.mock.pid),
            "--metrics-file",
            str(self.metrics_file),
            "--oracle-dir",
            str(self.oracle_dir),
            "--cert",
            str(self.cert),
        ]


async def client(root: Path, role: str, *args: str, timeout: float = 600) -> Any:
    out = root / f"{role}-{time.monotonic_ns()}.json"
    process = Process(role, root, role, "--out", str(out), *args)
    try:
        await process.wait(timeout)
    finally:
        process.log.close()
    return json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- G1


async def record_fidelity(root: Path, cert: Path, key: Path) -> dict[str, object]:
    cases: dict[str, list[dict[str, object]]] = {
        "spaced": [],
        "back_to_back": [],
        "slow_reader": [],
    }
    results: list[tuple[str, RequestResult]] = []
    async with Stack(root, cert, key, "fidelity") as stack:
        for case, delay, reader_delay in (
            ("spaced", 5, 0),
            ("back_to_back", 0, 0),
            ("slow_reader", 0, 25),
        ):
            for _ in range(3):
                result = await request(
                    "127.0.0.1",
                    stack.shaper_port,
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
                results.append((case, result))
    rows = [
        json.loads(line)
        for path in stack.oracle_dir.glob("*.ndjson")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    by_request: dict[str, list[int]] = {}
    for row in rows:
        by_request.setdefault(row["request_id"], []).append(row["framed_len"] + 17)
    for case, result in results:
        expected = by_request.get(result.request_id, [])
        observed = [record.length for record in result.records if record.content_type == 23]
        matched = bool(expected) and any(
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
                statistics.median(int(bool(item["exact_sequence"])) for item in repetitions)
            ),
        }
        for case, repetitions in cases.items()
    }


# --------------------------------------------------------------------------- G6


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
        output: list[SSEEvent] = []
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


# --------------------------------------------------------------------------- G2 / G4


async def timer_case(root: Path, cert: Path, key: Path, streams: int, duration: float) -> Any:
    port = free_port()
    out = root / f"timer-{streams}-{time.monotonic_ns()}.json"
    server = Process(
        "timer-server",
        root,
        "timer-server",
        "--port",
        str(port),
        "--streams",
        str(streams),
        "--duration",
        str(duration),
        "--cert",
        str(cert),
        "--key",
        str(key),
        "--out",
        str(out),
    )
    try:
        await wait_port(port, server)
        clients = await client(
            root,
            "timer-clients",
            "--port",
            str(port),
            "--streams",
            str(streams),
            "--cert",
            str(cert),
        )
        await server.wait(60)
    finally:
        await server.stop()
    result = json.loads(out.read_text(encoding="utf-8"))
    result["client_process"] = clients
    return result


def median_by(repetitions: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return sorted(repetitions, key=lambda item: item[key])[len(repetitions) // 2]


# --------------------------------------------------------------------------- main


@contextmanager
def scratch() -> Generator[Path]:
    path = Path(tempfile.mkdtemp(prefix="crosstalk-runtime-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def environment(args: argparse.Namespace, started: str) -> dict[str, object]:
    git_dir = Path(os.environ.get("CT_BENCH_GIT_DIR", REPO))

    def git(*command: str) -> str:
        return subprocess.run(
            ["git", *command], cwd=git_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    dirty = git("status", "--porcelain")
    loop = "uvloop" if platform.system() == "Linux" else "asyncio"
    return {
        "measured_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "command": sys.argv,
        "suite": args.suite,
        "kernel": platform.release(),
        "system": platform.system(),
        "cpu_count": os.cpu_count(),
        "cpu_model": _cpu_model(),
        "event_loop": loop,
        "uvloop": metadata.version("uvloop") if loop == "uvloop" else None,
        "aiohttp": metadata.version("aiohttp"),
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(dirty),
        "git_dirty_files": dirty.splitlines(),
        "git_checkout": str(git_dir),
        "executed_source_dir": str(REPO),
        "executed_source_sha256": source_digest(REPO),
        "crosstalk_module": crosstalk.__file__,
        "client_processes_for_relay": args.client_procs,
        "duration_seconds": args.duration,
        "runtime_gate_eligible": platform.system() == "Linux" and args.duration >= 20,
        "topology": TOPOLOGY,
    }


def source_digest(base: Path) -> str:
    """SHA-256 over the shaper and harness sources (LF-normalized), tying evidence to files."""
    digest = hashlib.sha256()
    files = sorted(
        [*(base / "src" / "crosstalk").rglob("*.py"), *(base / "bench" / "runtime").glob("*.py")]
    )
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _cpu_model() -> str | None:
    with suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def merge_relay(shards: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pool the client shards' raw samples per repetition, then take percentiles."""
    merged: list[dict[str, Any]] = []
    for parts in zip(*shards, strict=True):

        def pooled(key: str, parts: tuple[dict[str, Any], ...] = parts) -> list[float]:
            return [value for part in parts for value in part[key]]

        def resources(phase: str, parts: tuple[dict[str, Any], ...] = parts) -> dict[str, Any]:
            # Shaper and mock CPU are the same processes in every shard; clients add up.
            first = dict(parts[0][phase])
            first["client_cpu_cores"] = sum(part[phase]["client_cpu_cores"] for part in parts)
            first["client_rss_mb"] = sum(part[phase]["client_rss_mb"] for part in parts)
            first["client_cpu_cores_max_shard"] = max(
                part[phase]["client_cpu_cores"] for part in parts
            )
            return first

        direct, proxied = pooled("direct_samples"), pooled("proxied_samples")
        direct_p99 = percentile(direct, 0.99)
        proxy_p99 = percentile(proxied, 0.99) if proxied else None
        merged.append(
            {
                "direct": summary(direct),
                "proxied": summary(proxied),
                "direct_p99_ms": direct_p99,
                "proxy_p99_ms": proxy_p99,
                "added_p99_ms": None if proxy_p99 is None else proxy_p99 - direct_p99,
                "errors": sum(part["errors"] for part in parts),
                "exceptions": sorted({name for part in parts for name in part["exceptions"]}),
                "slot_overrun_seconds": max(part["slot_overrun_seconds"] for part in parts),
                "diagnostic_split": {
                    "upstream_send_to_shaper_write": summary(pooled("split_upstream_to_write")),
                    "shaper_write_to_client_receive": summary(pooled("split_write_to_client")),
                },
                "resources_direct_phase": resources("resources_direct_phase"),
                "resources_proxied_phase": resources("resources_proxied_phase"),
            }
        )
    return merged


async def relay_latency(
    root: Path, cert: Path, key: Path, workers: int, events: int, client_procs: int
) -> dict[str, object]:
    relay: dict[str, object] = {"shaper_processes": workers, "client_processes": client_procs}
    async with Stack(root, cert, key, f"relay-{workers}-{client_procs}", workers=workers) as stack:
        for concurrency in (64, 256):
            if concurrency % client_procs:
                raise ValueError("Concurrency must divide evenly across client processes")
            start_at = time.monotonic_ns() + 3_000_000_000
            shards = await asyncio.gather(
                *(
                    client(
                        root,
                        "relay-clients",
                        *stack.client_args(),
                        "--concurrency",
                        str(concurrency // client_procs),
                        "--events",
                        str(events),
                        "--reps",
                        "3",
                        "--start-at-ns",
                        str(start_at),
                    )
                    for _ in range(client_procs)
                )
            )
            repetitions = merge_relay(list(shards))
            relay[str(concurrency)] = {
                "repetitions": repetitions,
                "median": median_by(repetitions, "added_p99_ms"),
            }
    return relay


async def gates(
    root: Path, cert: Path, key: Path, duration: float, scale_out_workers: int, client_procs: int
) -> dict[str, object]:
    full = duration >= 20
    records = await record_fidelity(root, cert, key)
    fidelity = stream_fidelity(10_000 if full else 1000)
    events = 400 if full else 40
    relay = await relay_latency(root, cert, key, 1, events, client_procs)
    single_client = {
        "label": "Diagnostic: one shaper, all clients in ONE client process (the client can "
        "saturate at N=256); not the gate configuration",
        **await relay_latency(root, cert, key, 1, events, 1),
    }
    scale_out = (
        {
            "label": f"SO_REUSEPORT scale-out, {scale_out_workers} shaper processes "
            "(IMPLEMENTATION_PLAN §3.2 escape hatch); not the single-process gate result",
            "workers": scale_out_workers,
            **await relay_latency(root, cert, key, scale_out_workers, events, client_procs),
        }
        if scale_out_workers > 1
        else {"measured": False}
    )
    backpressure: list[dict[str, Any]] = []
    for index in range(3):
        async with Stack(root, cert, key, f"backpressure-{index}", stall_timeout=2) as stack:
            backpressure.append(
                await client(
                    root,
                    "backpressure-clients",
                    *stack.client_args(),
                    "--normal-events",
                    "80",
                    "--stall-timeout",
                    "2",
                )
            )
    timers: dict[str, object] = {}
    for streams in (1, 64, 256):
        repetitions = [await timer_case(root, cert, key, streams, duration) for _ in range(3)]
        timers[str(streams)] = {
            "repetitions": repetitions,
            "median": median_by(repetitions, "p99_ms"),
        }
    return {
        "record_fidelity": records,
        "stream_fidelity": fidelity,
        "timers": timers,
        "relay_latency": relay,
        "relay_latency_single_client_process": single_client,
        "relay_latency_scale_out": scale_out,
        "backpressure": {
            "repetitions": backpressure,
            "median": median_by(backpressure, "normal_p99_change_ms"),
        },
    }


async def soak(root: Path, cert: Path, key: Path, seconds: float) -> dict[str, object]:
    async with Stack(root, cert, key, "soak") as stack:
        samples = await client(
            root,
            "soak-clients",
            *stack.client_args(),
            "--streams",
            "64",
            "--duration",
            str(seconds),
            "--events",
            "400",
            timeout=seconds + 600,
        )
    first = float(samples[0]["shaper_rss_mb"])
    last = float(samples[-1]["shaper_rss_mb"])
    warm = next((s for s in samples if s["elapsed_seconds"] >= 60), samples[0])
    warm_rss = float(warm["shaper_rss_mb"])
    return {
        "measured": True,
        "measured_process": "shaper",
        "duration_seconds": seconds,
        "streams": 64,
        "events_per_stream": 400,
        "event_interval_ms": 25,
        "baseline_rule": "gate baseline is the first sample at or after 60 s (warm-up); "
        "growth from the t=0 sample is reported alongside",
        "rss_growth_fraction": (last - warm_rss) / warm_rss,
        "rss_growth_fraction_from_t0": (last - first) / first,
        "baseline_rss_mb": warm_rss,
        "final_rss_mb": last,
        "peak_rss_mb": max(float(sample["shaper_rss_mb"]) for sample in samples),
        "requests_completed": samples[-1]["requests_completed"],
        "errors": samples[-1]["errors"],
        "samples": samples,
    }


async def main(args: argparse.Namespace) -> None:
    started = datetime.now(UTC).isoformat()
    if args.output.exists():
        raise RuntimeError(f"Benchmark artifact already exists: {args.output}")
    with scratch() as root:
        cert, key = create_lab_certificate(root)
        if args.suite == "gates":
            measured = await gates(
                root, cert, key, args.duration, args.scale_out_workers, args.client_procs
            )
        else:
            measured = {"soak": await soak(root, cert, key, args.soak_seconds)}
    result = {"environment": environment(args, started), **measured}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("gates", "soak"), default="gates")
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--soak-seconds", type=float, default=1800)
    parser.add_argument("--scale-out-workers", type=int, default=4)
    parser.add_argument("--client-procs", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("artifacts/bench/runtime.json"))
    arguments = parser.parse_args()
    if sys.platform == "linux":
        import uvloop

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(main(arguments))
    else:
        asyncio.run(main(arguments))


if __name__ == "__main__":
    cli()
