import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Protocol, cast

import structlog

from crosstalk.orchestration.jobs import HEARTBEAT_SECONDS, JobRepository
from crosstalk.orchestration.stages import STAGES
from crosstalk.orchestration.state import Queue

Cancelled = Callable[[], Awaitable[bool]]
Handler = Callable[[Mapping[str, object], Cancelled], Awaitable[None]]


class JobStore(Protocol):
    def claim(self, queue: Queue, worker_id: str) -> Mapping[str, object] | None: ...
    def heartbeat(self, job_id: str, worker_id: str) -> bool: ...
    def succeed(self, job_id: str, worker_id: str) -> None: ...
    def cancel(self, job_id: str, worker_id: str) -> None: ...
    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: Mapping[str, object],
        sample: float | None = None,
    ) -> object: ...
    def cancellation_requested(self, job_id: str) -> bool: ...


class Worker:
    def __init__(
        self,
        store: JobStore | JobRepository,
        queue: Queue,
        worker_id: str,
        handlers: Mapping[str, Handler],
        poll_seconds: float = 1.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.store = store
        self.queue = queue
        self.worker_id = worker_id
        self.handlers = handlers
        self.poll_seconds = poll_seconds
        self.log = structlog.get_logger(component="worker", queue=queue, worker_id=worker_id)
        for name in handlers:
            if name not in STAGES or STAGES[name].queue != queue:
                raise ValueError(f"Stage {name!r} is not permitted on the {queue} queue")

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            worked = await self.run_once()
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)

    async def run_once(self) -> bool:
        row = await asyncio.to_thread(self.store.claim, self.queue, self.worker_id)
        if row is None:
            return False
        job_id = str(row["id"])
        kind = str(row["kind"])
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            await asyncio.to_thread(
                self.store.fail,
                job_id,
                self.worker_id,
                {"type": "invalid_payload"},
                0.0,
            )
            return True
        typed_payload = cast(Mapping[str, object], payload)
        handler = self.handlers.get(kind)
        if handler is None:
            await asyncio.to_thread(
                self.store.fail,
                job_id,
                self.worker_id,
                {"type": "unknown_stage", "stage": kind},
                0.0,
            )
            return True

        async def cancelled() -> bool:
            return await asyncio.to_thread(self.store.cancellation_requested, job_id)

        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        try:
            await handler(typed_payload, cancelled)
            if await cancelled():
                await asyncio.to_thread(self.store.cancel, job_id, self.worker_id)
            else:
                await asyncio.to_thread(self.store.succeed, job_id, self.worker_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.log.exception("job_failed", job_id=job_id, stage=kind)
            await asyncio.to_thread(
                self.store.fail,
                job_id,
                self.worker_id,
                {"type": type(error).__name__, "message": str(error)},
                None,
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            extended = await asyncio.to_thread(self.store.heartbeat, job_id, self.worker_id)
            if not extended:
                raise RuntimeError(f"Lost lease for job {job_id}")
