"""``crosstalk-worker``: claim jobs from PostgreSQL and run the production stage handlers."""

import argparse
import asyncio
import importlib
import os
import signal
import socket
import sys
from contextlib import suppress
from typing import Protocol, cast

import structlog
from sqlalchemy import create_engine

from crosstalk.orchestration.jobs import JobRepository
from crosstalk.orchestration.labs import ExternalLab, LocalDevLab
from crosstalk.orchestration.pipeline import Lab, Pipeline, PipelineSettings, PipelineStore
from crosstalk.orchestration.stages import STAGES
from crosstalk.orchestration.state import Queue
from crosstalk.orchestration.worker import Worker

REAP_SECONDS = 15.0


async def serve(settings: PipelineSettings, stop: asyncio.Event, once: bool = False) -> int:
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=8)
    store = PipelineStore(engine, settings.code_commit)
    repository = JobRepository(engine)
    lab: Lab = LocalDevLab(settings) if settings.lab == "local-dev" else ExternalLab(settings)
    pipeline = Pipeline(store, settings, lab)
    handlers = pipeline.handlers()
    identity = f"{socket.gethostname()}-{os.getpid()}"
    log = structlog.get_logger(component="runner", worker_id=identity)
    workers = [
        Worker(
            repository,
            Queue(name),
            f"{identity}-{name}",
            {stage: handler for stage, handler in handlers.items() if STAGES[stage].queue == name},
            settings.poll_seconds,
        )
        for name in settings.queues
    ]
    log.info("worker_started", queues=settings.queues, lab=settings.lab)
    if once:
        # Drain everything currently runnable, including stages enqueued on the way.
        processed = 0
        while True:
            reaped = await asyncio.to_thread(repository.reap_expired)
            worked = [await worker.run_once() for worker in workers]
            processed += sum(worked)
            if not any(worked) and not reaped:
                engine.dispose()
                return processed

    async def reaper() -> None:
        while not stop.is_set():
            reaped = await asyncio.to_thread(repository.reap_expired)
            if reaped:
                log.warning("leases_reaped", count=reaped)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=REAP_SECONDS)

    async with asyncio.TaskGroup() as group:
        group.create_task(reaper())
        for worker in workers:
            group.create_task(worker.run_forever(stop))
    engine.dispose()
    return 0


class _Uvloop(Protocol):
    def new_event_loop(self) -> asyncio.AbstractEventLoop: ...


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Drain runnable jobs, then exit")
    arguments = parser.parse_args()
    settings = PipelineSettings()

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(signum, stop.set)
        processed = await serve(settings, stop, arguments.once)
        if arguments.once:
            print(f"processed {processed} jobs")

    if sys.platform == "win32":
        with suppress(KeyboardInterrupt):
            asyncio.run(run())
    else:
        uvloop = cast(_Uvloop, importlib.import_module("uvloop"))
        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(run())


if __name__ == "__main__":
    main()
