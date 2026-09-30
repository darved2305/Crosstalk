import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Emission:
    request_id: str
    seq: int
    t_emit_ns: int
    kind: str
    framed_len: int
    content_bytes: int
    pad_bytes: int
    n_events: int
    queue_delay_ns: int
    timer_lateness_ns: int
    arm: str


class EmissionWriter:
    def __init__(self, path: Path, capacity: int = 8192) -> None:
        self._path = path
        self._queue: asyncio.Queue[Emission | None] = asyncio.Queue(maxsize=capacity)
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None

    async def __aenter__(self) -> "EmissionWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run())
        try:
            await self._ready
        except BaseException:
            await self._task
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            await self._enqueue(None)
            await self._task

    async def record(self, emission: Emission) -> None:
        if self._task is None:
            raise RuntimeError("Emission writer is not running")
        if self._task.done():
            await self._task
            raise RuntimeError("Emission writer stopped unexpectedly")
        try:
            self._queue.put_nowait(emission)
        except asyncio.QueueFull:
            await self._enqueue(emission)

    async def _enqueue(self, emission: Emission | None) -> None:
        if self._task is None:
            raise RuntimeError("Emission writer is not running")
        if self._task.done():
            await self._task
            raise RuntimeError("Emission writer stopped unexpectedly")
        pending = asyncio.create_task(self._queue.put(emission))
        done, _ = await asyncio.wait((pending, self._task), return_when=asyncio.FIRST_COMPLETED)
        if self._task in done:
            if not pending.done():
                pending.cancel()
            await self._task
            if not pending.done() or pending.cancelled():
                raise RuntimeError("Emission writer stopped unexpectedly")
        await pending

    async def _run(self) -> None:
        try:
            with self._path.open("x", encoding="utf-8") as output:
                if self._ready is not None:
                    self._ready.set_result(None)
                while (row := await self._queue.get()) is not None:
                    output.write(json.dumps(asdict(row), separators=(",", ":")) + "\n")
                    if self._queue.empty():
                        await asyncio.to_thread(output.flush)
        except BaseException as error:
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)
            raise
