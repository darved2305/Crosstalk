"""Capture read-only Google Cloud command evidence with no account identifiers."""

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def audit(project: str, output: Path) -> None:
    executable = shutil.which("gcloud")
    if executable is None:
        raise RuntimeError("Google Cloud SDK is not installed")
    commands = [
        ["billing", "projects", "describe", project, "--format=json"],
        ["compute", "instances", "list", f"--project={project}", "--format=json(name,status,zone)"],
        [
            "run",
            "services",
            "list",
            f"--project={project}",
            "--format=json(metadata.name,status.conditions)",
        ],
    ]
    records: list[dict[str, object]] = []
    for arguments in commands:
        completed = subprocess.run(
            [executable, *arguments], capture_output=True, text=True, timeout=90, check=False
        )
        records.append(
            {
                "command": "gcloud " + " ".join(arguments),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    document = {
        "checked_at": datetime.now(UTC).isoformat(),
        "project_id": project,
        "read_only": True,
        "deployment_performed": False,
        "screenshots_available": False,
        "commands": records,
        "limitation": "Billing disabled; live CrossTalk deployment cannot be tested under the no-billing constraint.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="inspired-campus-436516-k4")
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/gcp-audit.json"))
    arguments = parser.parse_args()
    audit(arguments.project, arguments.output)
