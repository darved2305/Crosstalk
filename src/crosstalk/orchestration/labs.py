"""Where a collect job sends its traffic.

``LocalDevLab`` runs the synthetic mock upstream, the production TLS shaper (with the
experiment's policy set) and a recording relay inside the worker process. It makes
dashboard runs execute end to end on a host without Docker, dumpcap or a GPU, and
every session it produces is labelled and validated as non-reportable.
``ExternalLab`` targets an already running shaper and the dumpcap capture supervisor.
"""

import asyncio
import socket
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import yaml

from crosstalk.common.certificates import create_lab_certificate
from crosstalk.contracts.config import ExperimentConfig, SessionPlan
from crosstalk.loadgen.session import HttpCaptureControl
from crosstalk.orchestration.pipeline import LabEndpoint, PipelineSettings
from crosstalk.workloads.tokens import WhitespaceCounter

MOCK_MODEL = "mock"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"Port {port} did not start listening") from None
            await asyncio.sleep(0.02)


class LocalDevLab:
    def __init__(self, settings: PipelineSettings) -> None:
        self.settings = settings
        self.root = settings.artifacts_root
        directory = self.root / "lab" / "local-dev-tls"
        self.cert, self.key = directory / "lab.crt", directory / "lab.key"
        if not (self.cert.is_file() and self.key.is_file()):
            self.cert, self.key = create_lab_certificate(directory)

    def prepare_plan(self, plan: SessionPlan, root: Path) -> tuple[SessionPlan, list[str]]:
        notes: list[str] = []
        if plan.model != MOCK_MODEL:
            notes.append(f"Configured model {plan.model} replaced by the synthetic mock upstream")
            plan = plan.model_copy(update={"model": MOCK_MODEL})
        if plan.validation and sys.platform == "win32":
            # CPython's keylog_filename aborts the process under the Windows OpenSSL DLL
            # build (OPENSSL_Uplink: no OPENSSL_Applink). Keylog checks need tshark anyway.
            notes.append("TLS keylog validation disabled: unsupported on the Windows worker")
            plan = plan.model_copy(update={"validation": False})
        if plan.workload is not None:
            workload = Path(plan.workload)
            resolved = workload if workload.is_absolute() else root / workload
            if not (resolved / "manifest.json").is_file():
                from crosstalk.workloads.corpus import build

                build(self.settings.corpus_source, resolved, WhitespaceCounter())
                notes.append(
                    f"Workload {plan.workload} built with the whitespace tokenizer "
                    "(mock-only development)"
                )
        return plan, notes

    @asynccontextmanager
    async def session(self, config: ExperimentConfig) -> AsyncGenerator[LabEndpoint]:
        from crosstalk.shaper.server import ShaperSettings
        from crosstalk.shaper.server import run as run_shaper
        from crosstalk.testing.mock_upstream import run as run_mock
        from crosstalk.testing.recorder import RecordingRelay

        mock_port, shaper_port, admin_port = _free_port(), _free_port(), _free_port()
        tasks: list[asyncio.Task[None]] = []
        with tempfile.TemporaryDirectory(prefix="crosstalk-policies-") as temporary:
            policy_file = Path(temporary) / "policies.yaml"
            policy_file.write_text(
                yaml.safe_dump([policy.model_dump(mode="json") for policy in config.policy_set]),
                encoding="utf-8",
            )
            default_arm = config.policy_set[0].arm
            relay: RecordingRelay | None = None
            try:
                tasks.append(
                    asyncio.create_task(
                        run_mock(
                            "127.0.0.1",
                            mock_port,
                            token_delay_ms=self.settings.mock_token_delay_ms,
                        )
                    )
                )
                await _wait_port(mock_port)
                shaper = ShaperSettings(
                    host="127.0.0.1",
                    port=shaper_port,
                    admin_host="127.0.0.1",
                    admin_port=admin_port,
                    cert=self.cert,
                    key=self.key,
                    upstream_url=f"http://127.0.0.1:{mock_port}",
                    oracle_dir=self.root / "shaper-log",
                    policy_file=policy_file,
                    default_arm=default_arm,
                )
                tasks.append(asyncio.create_task(run_shaper(shaper)))
                await _wait_port(admin_port)
                relay = RecordingRelay("127.0.0.1", shaper_port, self.root)
                relay_port = await relay.serve()
                yield LabEndpoint(
                    host="127.0.0.1",
                    port=relay_port,
                    ca_file=str(self.cert),
                    capture=relay,
                    environment={
                        "lab": "local-dev",
                        "upstream_kind": "mock",
                        "mock_token_delay_ms": str(self.settings.mock_token_delay_ms),
                    },
                )
            finally:
                if relay is not None:
                    await relay.close()
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task


class ExternalLab:
    def __init__(self, settings: PipelineSettings) -> None:
        self.settings = settings

    def prepare_plan(self, plan: SessionPlan, root: Path) -> tuple[SessionPlan, list[str]]:
        if plan.workload is not None:
            workload = Path(plan.workload)
            resolved = workload if workload.is_absolute() else root / workload
            if not (resolved / "manifest.json").is_file():
                raise FileNotFoundError(
                    f"Workload {plan.workload} is not built; run `crosstalk workloads build` "
                    "with the served tokenizer first"
                )
        return plan, []

    @asynccontextmanager
    async def session(self, config: ExperimentConfig) -> AsyncGenerator[LabEndpoint]:
        del config
        yield LabEndpoint(
            host=self.settings.shaper_host,
            port=self.settings.shaper_port,
            ca_file=self.settings.ca_file,
            capture=HttpCaptureControl(),
            environment={"lab": "external"},
        )
