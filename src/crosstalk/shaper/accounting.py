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

    async def __aenter__(self) -> "EmissionWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._queue.put(None)
        if self._task is not None:
            await self._task

    async def record(self, emission: Emission) -> None:
        await self._queue.put(emission)

    async def _run(self) -> None:
        with self._path.open("x", encoding="utf-8") as output:
            while (row := await self._queue.get()) is not None:
                output.write(json.dumps(asdict(row), separators=(",", ":")) + "\n")
                if self._queue.empty():
                    await asyncio.to_thread(output.flush)
