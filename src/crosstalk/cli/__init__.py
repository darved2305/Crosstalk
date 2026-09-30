import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import typer

from crosstalk.contracts.config import CaptureConfig, ExperimentConfig, PolicyConfig, SessionPlan
from crosstalk.contracts.manifest import RunManifest

app = typer.Typer(no_args_is_help=True)
schema_app = typer.Typer()
shaper_app = typer.Typer()
capture_app = typer.Typer()
reconstruct_app = typer.Typer()
session_app = typer.Typer()
report_app = typer.Typer()
lab_app = typer.Typer()
app.add_typer(schema_app, name="schema")
app.add_typer(shaper_app, name="shaper")
app.add_typer(capture_app, name="capture")
app.add_typer(reconstruct_app, name="reconstruct")
app.add_typer(session_app, name="session")
app.add_typer(report_app, name="report")
app.add_typer(lab_app, name="lab")


class _Uvloop(Protocol):
    def new_event_loop(self) -> asyncio.AbstractEventLoop: ...


def _run(coroutine: object) -> None:
    if not asyncio.iscoroutine(coroutine):
        raise TypeError("An async coroutine is required")
    if sys.platform == "win32":
        asyncio.run(coroutine)
    else:
        uvloop = cast(_Uvloop, importlib.import_module("uvloop"))
        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(coroutine)


@schema_app.command("export")
def schema_export(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for model in (PolicyConfig, CaptureConfig, SessionPlan, ExperimentConfig, RunManifest):
        target = directory / f"{model.__name__}.schema.json"
        target.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")


@shaper_app.command("run")
def shaper_run() -> None:
    from crosstalk.shaper.server import ShaperSettings, run

    _run(run(ShaperSettings()))


@capture_app.command("serve")
def capture_serve(root: Path = Path("artifacts"), interface: str | None = None) -> None:
    from crosstalk.capture.control import serve_control

    _run(serve_control(root, interface))


@reconstruct_app.command("run")
def reconstruct_run(session_id: str, root: Path = Path("artifacts")) -> None:
    from crosstalk.capture.reconstruct import map_flows, reconstruct

    session = root / "sessions" / session_id
    captures = sorted((session / "pcap").glob("*.pcapng"))
    if len(captures) != 1:
        raise typer.BadParameter("Exactly one capture file is required")
    result = reconstruct(captures[0], session / "observer", session_id)
    result["mapped_flows"] = map_flows(root, session_id)
    typer.echo(json.dumps(result, indent=2))


@reconstruct_app.command("validate")
def reconstruct_validate(session_id: str, root: Path = Path("artifacts")) -> None:
    from crosstalk.capture.validate import evaluate_session, validate_emissions, validate_tshark
    from crosstalk.common.artifacts import atomic_replace
    from crosstalk.contracts.manifest import SessionStatus
    from crosstalk.validation.keylogs import validate_keylogs

    session = root / "sessions" / session_id
    captures = sorted((session / "pcap").glob("*.pcapng"))
    if len(captures) != 1:
        raise typer.BadParameter("Exactly one capture file is required")
    validate_tshark(root, session_id, captures[0])
    validate_emissions(root, session_id)
    validate_keylogs(root, session_id, captures[0])
    result = evaluate_session(root, session_id)
    manifest_path = session / "manifest.json"
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    status = SessionStatus(result["status"])
    tshark = json.loads((session / "validation" / "tshark_agreement.json").read_text())
    fidelity = json.loads((session / "validation" / "emission_fidelity.json").read_text())
    keylog_path = session / "validation" / "keylog_check.json"
    checks = {
        "tshark": bool(tshark["exact"]),
        "emissions": fidelity["exact_fraction"] >= 0.9999,
    }
    if keylog_path.exists():
        checks["keylogs"] = bool(json.loads(keylog_path.read_text())["exact"])
    final = manifest.model_copy(
        update={"status": status, "reasons": result["reasons"], "validation": checks}
    )
    atomic_replace(manifest_path, final.model_dump_json(indent=2).encode())
    typer.echo(json.dumps(result, indent=2))


@session_app.command("run")
def session_run(
    plan: Path,
    root: Path = Path("artifacts"),
    host: str = "shaper",
    ca_file: str | None = "/run/secrets/lab.crt",
) -> None:
    from crosstalk.loadgen.session import run_plan

    _run(run_plan(plan, root, host, ca_file))


@lab_app.command("certs")
def lab_certs(directory: Path = Path(".secrets")) -> None:
    from crosstalk.common.certificates import create_lab_certificate

    cert, _ = create_lab_certificate(directory)
    for path in (
        Path("artifacts"),
        Path("artifacts/shaper-log"),
        Path("artifacts/lab"),
        Path("artifacts/sessions"),
        Path("artifacts/restricted"),
    ):
        path.mkdir(parents=True, exist_ok=True)
    typer.echo(str(cert))


@lab_app.command("provenance")
def lab_provenance(
    root: Path = Path("artifacts"),
    compose_file: Path = Path("deploy/compose/lab.yml"),
) -> None:
    from crosstalk.loadgen.provenance import write_lab_provenance

    typer.echo(str(write_lab_provenance(root, compose_file)))


@lab_app.command("netinit")
def lab_netinit(root: Path = Path("artifacts/lab")) -> None:
    from crosstalk.capture.netinit import configure

    configure(root)


@lab_app.command("impair")
def lab_impair(root: Path = Path("artifacts/lab"), interface: str = "eth0") -> None:
    from crosstalk.capture.netinit import impair

    impair(root, interface)


@report_app.command("waterfall")
def waterfall(
    session_id: str,
    request_id: str,
    root: Path = Path("artifacts"),
    output: Path = Path("waterfall.png"),
) -> None:
    from crosstalk.reporting.waterfall import render

    render(root, session_id, request_id, output)
