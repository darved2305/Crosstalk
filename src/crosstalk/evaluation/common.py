"""Pieces shared by the experiment reports: session discovery, scoring and output staging."""

import json
import shutil
from collections import defaultdict
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from crosstalk.attacks.models import FloatArray
from crosstalk.attacks.protocols import Prediction
from crosstalk.attacks.splits import IntArray
from crosstalk.common.errors import ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for
from crosstalk.features.traces import TraceSet
from crosstalk.stats.bootstrap import ClusterResamples, bootstrap_metrics
from crosstalk.stats.metrics import Scorer

BASELINE_ARM = "a0"
Key = tuple[str, str, str, str, str, str]


@dataclass(slots=True)
class Results:
    experiment_id: str
    bootstrap: int
    evaluations: list[dict[str, object]] = field(default_factory=lambda: [])
    predictions: list[dict[str, object]] = field(default_factory=lambda: [])
    features: dict[str, list[dict[str, object]]] = field(default_factory=lambda: {})
    # Balanced-accuracy bootstrap replicates, all drawn on the same family resamples.
    replicates: dict[Key, FloatArray] = field(default_factory=lambda: {})
    summary: dict[str, Any] = field(default_factory=lambda: {})

    def metric(
        self,
        regime: str,
        task: str,
        rep: str,
        model: str,
        name: str,
        arm: str = BASELINE_ARM,
        setting: str = "static",
    ) -> dict[str, object]:
        wanted = (regime, task, rep, model, name, arm, setting)
        for row in self.evaluations:
            key = tuple(
                row[k]
                for k in ("regime", "task", "representation", "model", "metric", "arm", "setting")
            )
            if key == wanted:
                return row
        raise KeyError(wanted)

    def value(self, *args: str, **kwargs: str) -> float:
        return float(str(self.metric(*args, **kwargs)["value"]))


def discover(root: Path, experiment_id: str) -> list[dict[str, object]]:
    manifests: list[dict[str, object]] = []
    for path in sorted((root / "sessions").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["experiment_id"] == experiment_id:
            manifests.append(manifest)
    return manifests


def reportable(manifests: list[dict[str, object]], allow_unvalidated: bool) -> bool:
    unreportable = [
        str(m["session_id"]) for m in manifests if m["status"] != "VALID" or m["git_dirty"]
    ]
    if unreportable and not allow_unvalidated:
        raise ValidationError(
            f"Sessions are not reportable (not VALID or dirty): {unreportable}. "
            "Use --allow-unvalidated for a development run."
        )
    return not unreportable


def codes(values: Sequence[object], vocabulary: Sequence[str]) -> IntArray:
    index = {name: position for position, name in enumerate(vocabulary)}
    return np.asarray([index[str(v)] for v in values], dtype=np.int64)


def score(
    results: Results,
    traces: TraceSet,
    regime: str,
    task: str,
    rep: str,
    predictions: list[Prediction],
    y: IntArray,
    vocabulary: Sequence[str],
    resamples: ClusterResamples,
    sensitive: int | None = None,
    base_rates: Sequence[int] = (),
    arm: str = BASELINE_ARM,
    setting: str = "static",
) -> None:
    """Score out-of-fold predictions on the rows they cover, with paired bootstrap CIs.

    ``resamples`` must index the full trace set, so every arm and setting shares the same
    family resamples.
    """
    rows = np.asarray(sorted({p.row for p in predictions}), dtype=np.int64)
    if rows.size == 0:
        return
    position = {int(row): index for index, row in enumerate(rows.tolist())}
    by_model: dict[str, dict[int, FloatArray]] = defaultdict(dict)
    for prediction in predictions:
        seeds = by_model[prediction.model]
        if prediction.seed not in seeds:
            seeds[prediction.seed] = np.full((rows.size, len(vocabulary)), np.nan)
        seeds[prediction.seed][position[prediction.row]] = prediction.proba
        trace = traces.traces[prediction.row]
        results.predictions.append(
            {
                "session_id": trace.session_id,
                "request_id": trace.request_id,
                "regime": regime,
                "task": task,
                "representation": rep,
                "fold": prediction.fold,
                "arm": arm,
                "setting": setting,
                "model": prediction.model,
                "seed": prediction.seed,
                "y_true": vocabulary[int(y[prediction.row])],
                "proba": [float(v) for v in prediction.proba],
            }
        )
    subset = resamples.restrict(rows)
    labels = y[rows]
    n_families = len({traces.traces[int(r)].labels["family_id"] for r in rows.tolist()})
    for model, seeds in by_model.items():
        probas = [seeds[seed] for seed in sorted(seeds)]
        if any(np.isnan(p).any() for p in probas):
            raise ValidationError(f"{model} left rows without an out-of-fold prediction")
        scorer = Scorer(labels, probas, len(vocabulary), sensitive, tuple(base_rates))
        for name, (value, low, high, replicates) in bootstrap_metrics(
            scorer.metrics, labels.size, subset
        ).items():
            if name == "balanced_accuracy":
                results.replicates[(regime, task, rep, model, arm, setting)] = replicates
            results.evaluations.append(
                {
                    "experiment_id": results.experiment_id,
                    "regime": regime,
                    "task": task,
                    "representation": rep,
                    "arm": arm,
                    "setting": setting,
                    "model": model,
                    "metric": name,
                    "value": value,
                    "ci_lo": low,
                    "ci_hi": high,
                    "n": int(labels.size),
                    "n_families": n_families,
                    "B": resamples.b,
                }
            )


def feature_rows(traces: TraceSet, x: FloatArray, names: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "session_id": trace.session_id,
            "request_id": trace.request_id,
            "arm": trace.labels["arm"],
            "feature_names": list(names),
            "values": [float(v) for v in row],
        }
        for trace, row in zip(traces.traces, x, strict=True)
    ]


def fmt(row: dict[str, object]) -> str:
    value, low, high = (float(str(row[key])) for key in ("value", "ci_lo", "ci_hi"))
    if np.isnan(value):
        return "n/a"
    return f"{value:.3f} [{low:.3f}, {high:.3f}]"


def write_rows(path: Path, table: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows, schema=schema_for(table)), path)


@contextmanager
def staged_output(root: Path, name: str, replace: bool) -> Generator[Path]:
    """Build an experiment directory in a temporary sibling and swap it in on success."""
    target = root / "experiments" / name
    if target.exists() and not replace:
        raise ValidationError(f"Experiment output already exists: {target} (use --replace)")
    staging = root / "experiments" / f".tmp-{new_ulid()}"
    staging.mkdir(parents=True)
    try:
        yield staging
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def write_results(staging: Path, results: Results, report: Callable[[], str]) -> None:
    for name, rows in results.features.items():
        write_rows(staging / "features" / f"{name}.parquet", "features", rows)
    write_rows(staging / "predictions" / "predictions.parquet", "predictions", results.predictions)
    write_rows(staging / "evaluations" / "evaluations.parquet", "evaluations", results.evaluations)
    (staging / "summary.json").write_text(
        json.dumps(results.summary, indent=2, default=float), encoding="utf-8"
    )
    (staging / "report.md").write_text(report(), encoding="utf-8")
