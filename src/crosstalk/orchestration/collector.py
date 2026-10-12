"""Collect one session in a child process of the worker.

Collection loads only the measurement stack (no database driver). This keeps the TLS
data path free of a second OpenSSL build (psycopg's libpq on Windows aborts the process
when TLS keylogging is enabled), and a crash during collection cannot kill the worker.
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Protocol, cast

from crosstalk.common.artifacts import atomic_replace
from crosstalk.contracts.config import ExperimentConfig, SessionPlan
from crosstalk.contracts.manifest import RunManifest
from crosstalk.orchestration.pipeline import Lab, PipelineSettings


def lab_for(settings: PipelineSettings) -> Lab:
    from crosstalk.orchestration.labs import ExternalLab, LocalDevLab

    return LocalDevLab(settings) if settings.lab == "local-dev" else ExternalLab(settings)


async def collect(spec: dict[str, object]) -> None:
    from crosstalk.loadgen.session import run_session

    settings = PipelineSettings.model_validate(spec["settings"])
    config = ExperimentConfig.model_validate(spec["config"])
    plan = SessionPlan.model_validate(spec["plan"])
    session_id = str(spec["session_id"])
    root = settings.artifacts_root
    async with lab_for(settings).session(config) as endpoint:
        await run_session(
            plan,
            root,
            host=endpoint.host,
            port=endpoint.port,
            ca_file=endpoint.ca_file,
            experiment=config,
            capture=endpoint.capture,
            session_id=session_id,
        )
    manifest_path = root / "sessions" / session_id / "manifest.json"
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    atomic_replace(
        manifest_path,
        manifest.model_copy(
            update={"environment": {**manifest.environment, **endpoint.environment}}
        )
        .model_dump_json(indent=2)
        .encode(),
    )


class _Uvloop(Protocol):
    def new_event_loop(self) -> asyncio.AbstractEventLoop: ...


def main() -> None:
    spec = cast(dict[str, object], json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    if sys.platform == "win32":
        asyncio.run(collect(spec))
    else:
        uvloop = cast(_Uvloop, importlib.import_module("uvloop"))
        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(collect(spec))


if __name__ == "__main__":
    main()
