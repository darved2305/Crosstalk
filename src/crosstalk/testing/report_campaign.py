"""Import retained E0/E1/E2 development test runs for console/report inspection.

Run the actual end-to-end tests first with a fresh --basetemp. This command
publishes their measured artifacts and records PARTIAL sessions in PostgreSQL.
It does not promote relay-generated packets to validated scientific evidence.
"""

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import create_engine, insert, select

from crosstalk.api.app import ApiSettings
from crosstalk.api.store import PostgresControlStore
from crosstalk.common.artifacts import sha256_file
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import ExperimentConfig
from crosstalk.contracts.parquet import read_table
from crosstalk.orchestration.schema import jobs, sessions


def publish(source: Path, destination: Path) -> None:
    """Copy only to a new destination, or verify an identical previous copy."""
    if destination.exists():
        for path in source.rglob("*"):
            if path.is_file():
                existing = destination / path.relative_to(source)
                if not existing.is_file() or sha256_file(path) != sha256_file(existing):
                    raise ValueError(f"Refusing to overwrite different artifact: {existing}")
        return
    shutil.copytree(source, destination)


def import_campaign(source: Path, evidence: Path, settings: ApiSettings) -> dict[str, object]:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    store = PostgresControlStore(engine)
    root = settings.artifacts_root
    result: dict[str, object] = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(source.resolve()),
        "execution": "retained pytest E0/E1/E2 end-to-end runs",
        "upstream": "synthetic mock OpenAI-compatible SSE server",
        "capture": "RecordingRelay ciphertext; synthetic TCP packet segmentation",
        "reportable": False,
        "experiments": [],
    }
    imported: list[dict[str, object]] = []
    for config_file in sorted(source.glob("*/e[012].yaml")):
        config = ExperimentConfig.model_validate(yaml.safe_load(config_file.read_text("utf-8")))
        run_root = config_file.parent / "artifacts"
        output = run_root / "experiments" / config.name
        summary = json.loads((output / "summary.json").read_text("utf-8"))
        if summary["reportable"] is not False:
            raise ValueError("Development campaign must remain explicitly non-reportable")
        publish(output, root / "experiments" / config.name)
        with engine.connect() as connection:
            from crosstalk.orchestration.schema import experiments

            existing = connection.execute(
                select(experiments.c.id).where(experiments.c.name == config.name)
            ).scalar_one_or_none()
        experiment_id = str(existing or store.create_experiment(config)["id"])
        session_info: list[dict[str, object]] = []
        manifests = sorted((run_root / "sessions").glob("*/manifest.json"))
        for index, manifest_path in enumerate(manifests):
            manifest = json.loads(manifest_path.read_text("utf-8"))
            session_id = manifest["session_id"]
            if manifest["failed"] != 0 or manifest["completed"] != manifest["planned"]:
                raise ValueError(f"Collection did not complete: {session_id}")
            publish(manifest_path.parent, root / "sessions" / session_id)
            publish(run_root / "labels" / session_id, root / "labels" / session_id)
            with engine.begin() as connection:
                found = connection.execute(
                    select(sessions.c.id).where(sessions.c.id == session_id)
                ).first()
                if found is None:
                    connection.execute(
                        insert(sessions).values(
                            id=session_id,
                            experiment_id=experiment_id,
                            state="PARTIAL",
                            config=config.sessions[index].model_dump(mode="json"),
                            cancel_requested=False,
                        )
                    )
                    connection.execute(
                        insert(jobs).values(
                            id=new_ulid(),
                            experiment_id=experiment_id,
                            session_id=session_id,
                            kind="import_development_evidence",
                            queue="evaluate",
                            state="succeeded",
                            payload={"source_manifest": str(manifest_path), "reportable": False},
                            attempts=1,
                            max_attempts=1,
                            run_after=datetime.now(UTC),
                            stage_key=sha256_file(manifest_path),
                        )
                    )
            requests = read_table(manifest_path.parent / "oracle" / "requests.parquet").to_pylist()
            session_info.append(
                {
                    "id": session_id,
                    "planned": manifest["planned"],
                    "completed": manifest["completed"],
                    "failed": manifest["failed"],
                    "request_id": requests[0]["request_id"],
                    "status": manifest["status"],
                    "manifest_sha256": sha256_file(manifest_path),
                }
            )
        visible = evidence / config.name
        visible.mkdir(parents=True, exist_ok=True)
        for filename in ("report.md", "summary.json"):
            shutil.copyfile(output / filename, visible / filename)
        if (output / "figures").is_dir():
            publish(output / "figures", visible / "figures")
        shutil.copyfile(config_file, visible / "config.yaml")
        evaluations = read_table(output / "evaluations" / "evaluations.parquet").to_pylist()
        measured = [
            r for r in evaluations if r["metric"] == "balanced_accuracy" and r["task"] == "t1"
        ]
        imported.append(
            {
                "id": experiment_id,
                "name": config.name,
                "reportable": False,
                "sessions": session_info,
                "evaluations": len(evaluations),
                "balanced_accuracy": measured,
                "report_sha256": sha256_file(output / "report.md"),
            }
        )
        print(
            f"Imported {config.name}: {len(session_info)} sessions, {len(evaluations)} evaluations",
            flush=True,
        )
    if len(imported) != 3:
        raise ValueError(f"Expected retained E0, E1 and E2 runs; found {len(imported)}")
    result["experiments"] = imported
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "campaign.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--evidence", type=Path, default=Path("docs/evidence/campaign"))
    arguments = parser.parse_args()
    import_campaign(
        arguments.source,
        arguments.evidence,
        ApiSettings(),  # pyright: ignore[reportCallIssue]
    )
