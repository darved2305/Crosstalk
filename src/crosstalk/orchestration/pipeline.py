"""Production stage handlers: API run → durable queue → collection → analysis → report.

Each handler is idempotent with respect to its own outputs, so a job that is retried
after a crash or an expired lease either resumes or republishes the same artifacts.
Session state changes follow ``require_transition``; experiment-level stages are
enqueued exactly once by the last session to reach a terminal state.
"""

import asyncio
import json
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from crosstalk.common.artifacts import atomic_replace, canonical_json, sha256_file
from crosstalk.common.errors import ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import ExperimentConfig, ExperimentKind, SessionPlan
from crosstalk.contracts.manifest import RunManifest, SessionStatus
from crosstalk.loadgen.session import CaptureController
from crosstalk.orchestration.jobs import JobRepository
from crosstalk.orchestration.schema import events, experiments, sessions
from crosstalk.orchestration.stages import STAGES, inventory, stage_key
from crosstalk.orchestration.state import SessionState, require_transition

Cancelled = Callable[[], Awaitable[bool]]
COLLECT_POLL_SECONDS = 2.0
TERMINAL = {
    SessionState.VALID,
    SessionState.INVALID,
    SessionState.PARTIAL,
    SessionState.CANCELLED,
}


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CT_WORKER_", extra="ignore")

    database_url: str = "postgresql+psycopg://crosstalk:crosstalk@localhost:5432/crosstalk"
    artifacts_root: Path = Path("artifacts")
    code_commit: str = "unknown"
    queues: list[str] = Field(default_factory=lambda: ["collect", "attack", "evaluate"])
    poll_seconds: float = Field(default=1.0, gt=0)
    # local-dev: in-process synthetic upstream, the production TLS shaper and a recording
    # relay (synthetic packet segmentation, never reportable). external: an already
    # running shaper plus the dumpcap capture supervisor of the Linux laboratory.
    lab: Literal["local-dev", "external"] = "local-dev"
    corpus_source: Path = Path("workloads/corpus/v1/corpus.yaml")
    mock_token_delay_ms: float = Field(default=1.0, ge=0, le=100)
    shaper_host: str = "shaper"
    shaper_port: int = 8443
    ca_file: str | None = "/run/secrets/lab.crt"


@dataclass(frozen=True, slots=True)
class LabEndpoint:
    host: str
    port: int
    ca_file: str | None
    capture: CaptureController | None
    environment: Mapping[str, str]


class Lab(Protocol):
    def session(self, config: ExperimentConfig) -> AbstractAsyncContextManager[LabEndpoint]: ...

    def prepare_plan(self, plan: SessionPlan, root: Path) -> tuple[SessionPlan, list[str]]: ...


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: str
    experiment_id: str
    config: ExperimentConfig
    plan: SessionPlan
    state: SessionState
    cancel_requested: bool


def analysis_config(experiment_id: str, config: ExperimentConfig) -> ExperimentConfig:
    """Scope collection and analysis to one control-plane experiment.

    Manifests and experiment outputs are keyed by the experiment ULID, so rerunning a
    configuration with the same human name never pools sessions or overwrites results.
    """
    return config.model_copy(
        update={
            "name": experiment_id,
            "sessions": [
                plan.model_copy(update={"experiment_id": experiment_id}) for plan in config.sessions
            ],
        }
    )


class PipelineStore:
    def __init__(self, engine: Engine, code_commit: str) -> None:
        self.engine = engine
        self.jobs = JobRepository(engine)
        self.code_commit = code_commit

    def session(self, session_id: str) -> SessionContext:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    sessions.c.id,
                    sessions.c.experiment_id,
                    sessions.c.state,
                    sessions.c.config.label("plan"),
                    sessions.c.cancel_requested,
                    experiments.c.config,
                    experiments.c.cancel_requested.label("experiment_cancelled"),
                )
                .join(experiments, experiments.c.id == sessions.c.experiment_id)
                .where(sessions.c.id == session_id)
            ).one_or_none()
        if row is None:
            raise ValidationError(f"Session does not exist: {session_id}")
        return SessionContext(
            session_id=session_id,
            experiment_id=str(row.experiment_id),
            config=ExperimentConfig.model_validate(row.config),
            plan=SessionPlan.model_validate(row.plan),
            state=SessionState(row.state),
            cancel_requested=bool(row.cancel_requested or row.experiment_cancelled),
        )

    def experiment(self, experiment_id: str) -> tuple[ExperimentConfig, bool]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(experiments.c.config, experiments.c.cancel_requested).where(
                    experiments.c.id == experiment_id
                )
            ).one_or_none()
        if row is None:
            raise ValidationError(f"Experiment does not exist: {experiment_id}")
        return ExperimentConfig.model_validate(row.config), bool(row.cancel_requested)

    def transition(self, session_id: str, target: SessionState) -> None:
        with self.engine.begin() as connection:
            current = connection.execute(
                select(sessions.c.state).where(sessions.c.id == session_id).with_for_update()
            ).scalar_one()
            require_transition(SessionState(current), target)
            connection.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(state=target, updated_at=func.now())
            )

    def event(
        self,
        experiment_id: str,
        session_id: str | None,
        kind: str,
        data: Mapping[str, object],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(events).values(
                    id=new_ulid(),
                    experiment_id=experiment_id,
                    session_id=session_id,
                    kind=kind,
                    data=json.loads(canonical_json(dict(data))),
                )
            )

    def enqueue(
        self,
        stage: str,
        experiment_id: str,
        session_id: str | None,
        config: ExperimentConfig,
    ) -> None:
        subject = {"session_id": session_id} if session_id else {"experiment_id": experiment_id}
        payload: dict[str, object] = {"experiment_id": experiment_id, **subject}
        self.jobs.enqueue(
            experiment_id=experiment_id,
            session_id=session_id,
            kind=stage,
            queue=STAGES[stage].queue,
            payload=payload,
            stage_key=stage_key(
                stage,
                {key: str(value) for key, value in subject.items()},
                config.sha256(),
                self.code_commit,
            ),
        )

    def finish_session(self, context: SessionContext, target: SessionState) -> bool:
        """Record a terminal session state; enqueue analysis once all sessions are done.

        The experiment row lock serializes concurrent validators so exactly one of them
        observes the final terminal session and enqueues the experiment-level stage.
        """
        with self.engine.begin() as connection:
            cancelled = connection.execute(
                select(experiments.c.cancel_requested)
                .where(experiments.c.id == context.experiment_id)
                .with_for_update()
            ).scalar_one()
            current = connection.execute(
                select(sessions.c.state).where(sessions.c.id == context.session_id)
            ).scalar_one()
            require_transition(SessionState(current), target)
            connection.execute(
                update(sessions)
                .where(sessions.c.id == context.session_id)
                .values(state=target, updated_at=func.now())
            )
            states = [
                SessionState(state)
                for state in connection.execute(
                    select(sessions.c.state).where(
                        sessions.c.experiment_id == context.experiment_id
                    )
                ).scalars()
            ]
        ready = (
            not cancelled
            and all(state in TERMINAL for state in states)
            and any(state != SessionState.CANCELLED for state in states)
        )
        if ready:
            self.enqueue("analyze", context.experiment_id, None, context.config)
        return ready


Collector = Callable[[Mapping[str, object], Cancelled], Awaitable[bool]]


async def collect_in_child(spec: Mapping[str, object], cancelled: Cancelled) -> bool:
    """Run ``crosstalk.orchestration.collector`` and wait; False if cancelled meanwhile."""
    with tempfile.TemporaryDirectory(prefix="crosstalk-collect-") as temporary:
        spec_path = Path(temporary) / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        log_path = Path(temporary) / "collector.log"
        with log_path.open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "crosstalk.orchestration.collector",
                str(spec_path),
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                try:
                    code = await asyncio.wait_for(process.wait(), COLLECT_POLL_SECONDS)
                    break
                except TimeoutError:
                    if await cancelled():
                        process.terminate()
                        await process.wait()
                        return False
                except asyncio.CancelledError:
                    process.kill()
                    await process.wait()
                    raise
        if code != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Collector exited with status {code}: {tail}")
    return True


class Pipeline:
    def __init__(
        self,
        store: PipelineStore,
        settings: PipelineSettings,
        lab: Lab,
        collector: Collector = collect_in_child,
    ) -> None:
        self.store = store
        self.settings = settings
        self.root = settings.artifacts_root
        self.lab = lab
        self.collector = collector
        self.log = structlog.get_logger(component="pipeline")

    def handlers(self) -> dict[str, Callable[[Mapping[str, object], Cancelled], Awaitable[None]]]:
        return {
            "plan": self.plan,
            "collect": self.collect,
            "reconstruct": self.reconstruct,
            "validate": self.validate,
            "analyze": self.analyze,
            "report": self.report,
        }

    async def _context(self, payload: Mapping[str, object]) -> SessionContext:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str):
            raise ValidationError("Session stage payload requires session_id")
        return await asyncio.to_thread(self.store.session, session_id)

    async def _cancel_session(self, context: SessionContext) -> None:
        if context.state not in TERMINAL:
            await asyncio.to_thread(
                self.store.transition, context.session_id, SessionState.CANCELLED
            )
        await asyncio.to_thread(
            self.store.event, context.experiment_id, context.session_id, "session_cancelled", {}
        )

    async def plan(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        context = await self._context(payload)
        if context.cancel_requested or await cancelled():
            await self._cancel_session(context)
            return
        plan, notes = await asyncio.to_thread(self.lab.prepare_plan, context.plan, self.root)
        await asyncio.to_thread(
            self.store.event,
            context.experiment_id,
            context.session_id,
            "session_planned",
            {"model": plan.model, "workload": plan.workload, "notes": notes},
        )
        await asyncio.to_thread(
            self.store.enqueue,
            "collect",
            context.experiment_id,
            context.session_id,
            context.config,
        )

    async def collect(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        context = await self._context(payload)
        if context.cancel_requested or await cancelled():
            await self._cancel_session(context)
            return
        session_dir = self.root / "sessions" / context.session_id
        if context.state == SessionState.COLLECTED and (session_dir / "manifest.json").is_file():
            await self._after_collect(context)
            return
        if context.state == SessionState.COLLECTING:
            # A previous attempt died mid-collection. Keep its partial artifacts for
            # inspection, out of the analysis glob, and collect again from scratch.
            self._set_aside(context.session_id)
        await asyncio.to_thread(self.store.transition, context.session_id, SessionState.COLLECTING)
        scoped = analysis_config(context.experiment_id, context.config)
        plan, notes = await asyncio.to_thread(
            self.lab.prepare_plan,
            context.plan.model_copy(update={"experiment_id": context.experiment_id}),
            self.root,
        )
        spec: dict[str, object] = {
            "settings": json.loads(self.settings.model_dump_json()),
            "config": scoped.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "session_id": context.session_id,
        }
        if not await self.collector(spec, cancelled):
            self._set_aside(context.session_id)
            await self._cancel_session(context)
            return
        manifest = RunManifest.model_validate_json(
            (session_dir / "manifest.json").read_text(encoding="utf-8")
        )
        await asyncio.to_thread(self.store.transition, context.session_id, SessionState.COLLECTED)
        await asyncio.to_thread(
            self.store.event,
            context.experiment_id,
            context.session_id,
            "session_collected",
            {
                "planned": manifest.planned,
                "completed": manifest.completed,
                "failed": manifest.failed,
                "notes": notes,
                "lab": manifest.environment.get("lab"),
                "capture": manifest.environment.get("capture_interface"),
            },
        )
        await self._after_collect(context)

    async def _after_collect(self, context: SessionContext) -> None:
        await asyncio.to_thread(
            self.store.enqueue,
            "reconstruct",
            context.experiment_id,
            context.session_id,
            context.config,
        )

    def _set_aside(self, session_id: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for kind in ("sessions", "labels", "restricted"):
            source = self.root / kind / session_id
            if source.exists():
                target = self.root / "attempts" / kind / f"{session_id}-{stamp}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(source, target)

    async def reconstruct(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        from crosstalk.capture.reconstruct import map_flows, reconstruct

        context = await self._context(payload)
        if context.cancel_requested or await cancelled():
            await self._cancel_session(context)
            return
        session = self.root / "sessions" / context.session_id
        observer = session / "observer"
        result: dict[str, object] = {}
        if not (observer / "flow_map.parquet").is_file():
            captures = sorted((session / "pcap").glob("*.pcapng"))
            if len(captures) != 1:
                raise ValidationError("Exactly one capture file is required")
            if not (observer / "records.parquet").is_file():
                stats = await asyncio.to_thread(
                    reconstruct, captures[0], observer, context.session_id
                )
                result.update(stats)
            result["mapped_flows"] = await asyncio.to_thread(
                map_flows, self.root, context.session_id
            )
        if context.state == SessionState.COLLECTED:
            await asyncio.to_thread(
                self.store.transition, context.session_id, SessionState.RECONSTRUCTED
            )
        await asyncio.to_thread(
            self.store.event,
            context.experiment_id,
            context.session_id,
            "session_reconstructed",
            result,
        )
        await asyncio.to_thread(
            self.store.enqueue,
            "validate",
            context.experiment_id,
            context.session_id,
            context.config,
        )

    async def validate(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        context = await self._context(payload)
        if context.cancel_requested or await cancelled():
            await self._cancel_session(context)
            await asyncio.to_thread(self.store.finish_session, context, SessionState.CANCELLED)
            return
        if context.state in TERMINAL:
            await asyncio.to_thread(self.store.finish_session, context, context.state)
            return
        status, reasons, checks = await asyncio.to_thread(
            validate_session, self.root, context.session_id
        )
        target = SessionState(status.value)
        await asyncio.to_thread(
            self.store.event,
            context.experiment_id,
            context.session_id,
            "session_validated",
            {"status": status.value, "reasons": reasons, "checks": checks},
        )
        await asyncio.to_thread(self.store.finish_session, context, target)

    def _experiment_id(self, payload: Mapping[str, object]) -> str:
        experiment_id = payload.get("experiment_id")
        if not isinstance(experiment_id, str):
            raise ValidationError("Experiment stage payload requires experiment_id")
        return experiment_id

    async def analyze(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        experiment_id = self._experiment_id(payload)
        config, cancel_requested = await asyncio.to_thread(self.store.experiment, experiment_id)
        if cancel_requested or await cancelled():
            return
        scoped = analysis_config(experiment_id, config)
        kind = config.resolved_kind()
        target = self.root / "experiments" / experiment_id
        await asyncio.to_thread(
            self.store.event, experiment_id, None, "analysis_started", {"kind": kind.value}
        )
        await asyncio.to_thread(run_analysis, self.root, scoped, kind, target.exists())
        await asyncio.to_thread(
            self.store.event, experiment_id, None, "analysis_finished", {"kind": kind.value}
        )
        await asyncio.to_thread(self.store.enqueue, "report", experiment_id, None, config)

    async def report(self, payload: Mapping[str, object], cancelled: Cancelled) -> None:
        del cancelled
        experiment_id = self._experiment_id(payload)
        target = self.root / "experiments" / experiment_id
        for required in ("report.md", "summary.json"):
            if not (target / required).is_file():
                raise ValidationError(f"Analysis did not produce {required}")
        summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
        files = [
            {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in inventory(target)
            if item.path != "index.json"
        ]
        index = {
            "experiment_id": experiment_id,
            "reportable": summary.get("reportable"),
            "published_at": datetime.now(UTC).isoformat(),
            "files": files,
        }
        index_path = target / "index.json"
        data = json.dumps(index, indent=2).encode()
        if index_path.exists():
            atomic_replace(index_path, data)
        else:
            index_path.write_bytes(data)
        await asyncio.to_thread(
            self.store.event,
            experiment_id,
            None,
            "report_published",
            {
                "reportable": summary.get("reportable"),
                "files": len(files),
                "report_sha256": sha256_file(target / "report.md"),
            },
        )


def validate_session(
    root: Path, session_id: str
) -> tuple[SessionStatus, list[str], dict[str, bool]]:
    """Run every independent check available on this host and record the session status.

    A full laboratory capture (dumpcap metadata plus a tshark oracle) goes through the
    production ``evaluate_session`` gate. A relay-recorded development capture can
    only check emission fidelity, so it is PARTIAL by construction.
    """
    from crosstalk.capture.validate import evaluate_session, validate_emissions, validate_tshark
    from crosstalk.validation.keylogs import validate_keylogs

    session = root / "sessions" / session_id
    manifest_path = session / "manifest.json"
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    captures = sorted((session / "pcap").glob("*.pcapng"))
    if len(captures) != 1:
        raise ValidationError("Exactly one capture file is required")
    fidelity_path = session / "validation" / "emission_fidelity.json"
    fidelity = (
        cast(dict[str, object], json.loads(fidelity_path.read_text(encoding="utf-8")))
        if fidelity_path.is_file()
        else validate_emissions(root, session_id)
    )
    checks = {**manifest.validation, "emissions": float(str(fidelity["exact_fraction"])) >= 0.9999}
    reasons: list[str]
    tshark_available = shutil.which("tshark") is not None
    full_capture = (session / "capture.json").is_file() and tshark_available
    if full_capture:
        if not (session / "validation" / "tshark_agreement.json").is_file():
            validate_tshark(root, session_id, captures[0])
        if not (session / "validation" / "keylog_check.json").is_file():
            validate_keylogs(root, session_id, captures[0])
        result = evaluate_session(root, session_id)
        status = SessionStatus(str(result["status"]))
        reasons = [str(item) for item in cast(list[object], result["reasons"])]
        tshark = json.loads((session / "validation" / "tshark_agreement.json").read_text("utf-8"))
        checks["tshark"] = bool(tshark["exact"])
        keylog = session / "validation" / "keylog_check.json"
        if keylog.is_file():
            checks["keylogs"] = bool(json.loads(keylog.read_text("utf-8"))["exact"])
    else:
        status = SessionStatus.PARTIAL
        reasons = []
        if manifest.environment.get("capture_interface") == "recording-relay":
            reasons.append("Recording relay: synthetic TCP segmentation, not packet capture")
        if manifest.environment.get("upstream_kind") == "mock":
            reasons.append("Synthetic mock upstream, not a served model")
        if not (session / "capture.json").is_file():
            reasons.append("No dumpcap capture metadata")
        if not tshark_available:
            reasons.append("tshark oracle unavailable on the worker host")
        if not checks["emissions"]:
            reasons.append("Emission to record fidelity below 99.99%")
        if manifest.failed:
            reasons.append(f"{manifest.failed} of {manifest.planned} requests failed")
        if manifest.git_dirty:
            reasons.append("Source tree was dirty during collection")
    final = manifest.model_copy(update={"status": status, "reasons": reasons, "validation": checks})
    atomic_replace(manifest_path, final.model_dump_json(indent=2).encode())
    return status, reasons, checks


def run_analysis(root: Path, config: ExperimentConfig, kind: ExperimentKind, replace: bool) -> None:
    if kind == ExperimentKind.E0:
        from crosstalk.evaluation.e0 import run_e0

        run_e0(root, config, allow_unvalidated=True, replace=replace)
    elif kind == ExperimentKind.E1:
        from crosstalk.evaluation.e1 import run_e1

        run_e1(root, config, allow_unvalidated=True, replace=replace)
    elif kind == ExperimentKind.E2:
        from crosstalk.evaluation.e2 import run_e2

        run_e2(root, config, allow_unvalidated=True, replace=replace)
    else:
        validation_report(root, config, replace)


def validation_report(root: Path, config: ExperimentConfig, replace: bool) -> Path:
    """Summarize a synthetic validation experiment, which has no attacker analysis."""
    from crosstalk.evaluation.common import discover

    manifests = discover(root, config.name)
    if not manifests:
        raise ValidationError(f"No sessions found for experiment {config.name}")
    target = root / "experiments" / config.name
    if target.exists() and not replace:
        raise ValidationError(f"Experiment output already exists: {target}")
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for manifest in manifests:
        session = root / "sessions" / str(manifest["session_id"])
        fidelity_path = session / "validation" / "emission_fidelity.json"
        fidelity: dict[str, object] = (
            json.loads(fidelity_path.read_text(encoding="utf-8")) if fidelity_path.is_file() else {}
        )
        rows.append(
            {
                "session_id": manifest["session_id"],
                "status": manifest["status"],
                "planned": manifest["planned"],
                "completed": manifest["completed"],
                "failed": manifest["failed"],
                "concurrency": manifest["concurrency"],
                "capture": cast(dict[str, object], manifest["environment"]).get(
                    "capture_interface"
                ),
                "emission_exact_fraction": fidelity.get("exact_fraction"),
                "reasons": manifest["reasons"],
            }
        )
    reportable = all(row["status"] == "VALID" for row in rows) and not any(
        m["git_dirty"] for m in manifests
    )
    summary = {"kind": "validation", "reportable": reportable, "sessions": rows}
    lines = [
        f"# Validation experiment {config.name}",
        "",
        "**REPORTABLE**" if reportable else "**NOT REPORTABLE** — development validation run.",
        "",
        "| Session | Status | Completed / planned | Failed | Concurrency | Capture "
        "| Emission fidelity |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for row in rows:
        exact = row["emission_exact_fraction"]
        lines.append(
            f"| `{row['session_id']}` | {row['status']} | {row['completed']} / {row['planned']}"
            f" | {row['failed']} | {row['concurrency']} | {row['capture']} | "
            f"{'n/a' if exact is None else f'{float(str(exact)):.4f}'} |"
        )
    lines += ["", "## Reasons", ""]
    for row in rows:
        reasons = cast(list[str], row["reasons"]) or ["none"]
        lines.append(f"- `{row['session_id']}`: " + "; ".join(reasons))
    (target / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (target / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
