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
workloads_app = typer.Typer()
e0_app = typer.Typer()
e1_app = typer.Typer()
e2_app = typer.Typer()
api_app = typer.Typer()
manifest_app = typer.Typer()
app.add_typer(schema_app, name="schema")
app.add_typer(shaper_app, name="shaper")
app.add_typer(capture_app, name="capture")
app.add_typer(reconstruct_app, name="reconstruct")
app.add_typer(session_app, name="session")
app.add_typer(report_app, name="report")
app.add_typer(lab_app, name="lab")
app.add_typer(workloads_app, name="workloads")
app.add_typer(e0_app, name="e0")
app.add_typer(e1_app, name="e1")
app.add_typer(e2_app, name="e2")
app.add_typer(api_app, name="api")
app.add_typer(manifest_app, name="manifest")


def _load_experiment(path: Path) -> ExperimentConfig:
    import yaml

    return ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


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


@api_app.command("openapi")
def api_openapi(output: Path = Path("docs/openapi.json")) -> None:
    from crosstalk.api.app import ApiSettings, create_app
    from crosstalk.common.artifacts import atomic_replace, atomic_write

    placeholder = "openapi-export-placeholder-token"
    document = create_app(
        settings=ApiSettings(operator_token=placeholder, oracle_token=placeholder)
    ).openapi()
    data = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    if output.exists():
        atomic_replace(output, data)
    else:
        atomic_write(output, data)


@api_app.command("run")
def api_run() -> None:
    from crosstalk.api.app import main

    main()


@manifest_app.command("audit")
def manifest_audit(path: Path) -> None:
    """Check whether a recorded session is eligible to support reported results."""
    manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    issues = manifest.reportability_issues()
    typer.echo(
        json.dumps(
            {
                "session_id": manifest.session_id,
                "reportable": not issues,
                "issues": issues,
            },
            indent=2,
        )
    )
    if issues:
        raise typer.Exit(code=1)


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
        **manifest.validation,
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


@session_app.command("plan")
def session_plan(
    experiment: Path,
    output: Path,
    index: int = 0,
    root: Path = Path("artifacts"),
) -> None:
    """Write the deterministic slot plan of one session for inspection."""
    import pyarrow as pa

    from crosstalk.contracts.parquet import write_table
    from crosstalk.contracts.tables import schema_for
    from crosstalk.workloads.corpus import load_corpus
    from crosstalk.workloads.planner import plan_session

    config = _load_experiment(experiment)
    plan = config.sessions[index]
    corpus = None
    if plan.workload is not None:
        workload = Path(plan.workload)
        corpus = load_corpus(workload if workload.is_absolute() else root / workload)
    planned = plan_session(plan, corpus)
    rows = [{"session_id": "", **row} for row in planned.rows]
    write_table(pa.Table.from_pylist(rows, schema=schema_for("plan")), output)
    typer.echo(json.dumps({"slots": len(rows), "plan_sha256": planned.sha256()}))


@workloads_app.command("build")
def workloads_build(
    source: Path = Path("workloads/corpus/v1/corpus.yaml"),
    output: Path = Path("artifacts/workloads/v1"),
    tokenizer: str = typer.Option(
        ..., help="Served model ID (Hugging Face), or 'whitespace' for mock-only development"
    ),
    revision: str | None = typer.Option(None, help="Pinned tokenizer revision (required for HF)"),
) -> None:
    """Render, check and hash the prompt corpus with the served tokenizer."""
    from crosstalk.workloads.corpus import build
    from crosstalk.workloads.tokens import HuggingFaceCounter, TokenCounter, WhitespaceCounter

    counter: TokenCounter
    if tokenizer == "whitespace":
        counter = WhitespaceCounter()
    elif revision is None:
        raise typer.BadParameter("--revision is required for a model tokenizer")
    else:
        counter = HuggingFaceCounter(tokenizer, revision)
    typer.echo(json.dumps(build(source, output, counter), indent=2))


@e0_app.command("run")
def e0_run(
    experiment: Path,
    root: Path = Path("artifacts"),
    allow_unvalidated: bool = typer.Option(
        False, help="Analyse sessions that are not VALID; the report is marked not reportable"
    ),
    replace: bool = typer.Option(False, help="Replace an existing experiment output"),
) -> None:
    """Features, attackers, statistics and report for every session of an E0 experiment."""
    from crosstalk.evaluation.e0 import run_e0

    target = run_e0(root, _load_experiment(experiment), allow_unvalidated, replace)
    typer.echo(str(target / "report.md"))


@e1_app.command("run")
def e1_run(
    experiment: Path,
    root: Path = Path("artifacts"),
    allow_unvalidated: bool = typer.Option(
        False, help="Analyse sessions that are not VALID; the report is marked not reportable"
    ),
    replace: bool = typer.Option(False, help="Replace an existing experiment output"),
) -> None:
    """Static and adaptive attackers per defense arm, paired statistics and cost."""
    from crosstalk.evaluation.e1 import run_e1

    target = run_e1(root, _load_experiment(experiment), allow_unvalidated, replace)
    typer.echo(str(target / "report.md"))


@e2_app.command("run")
def e2_run(
    experiment: Path,
    root: Path = Path("artifacts"),
    allow_unvalidated: bool = typer.Option(
        False, help="Analyse sessions that are not VALID; the report is marked not reportable"
    ),
    replace: bool = typer.Option(False, help="Replace an existing experiment output"),
) -> None:
    """Leakage and defense cost across concurrency levels, with two-stage bootstrap CIs."""
    from crosstalk.evaluation.e2 import run_e2

    target = run_e2(root, _load_experiment(experiment), allow_unvalidated, replace)
    typer.echo(str(target / "report.md"))


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
