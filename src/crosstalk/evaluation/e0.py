"""E0 calibration: does the pipeline reproduce known leakage, and how much survives matching?

Reads only observer tables and labels (through ``crosstalk.features.traces``); never the
oracle or restricted stores.
"""

import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from crosstalk.attacks.models import FloatArray, Model, resolve
from crosstalk.attacks.protocols import Design, Prediction, cross_validate
from crosstalk.attacks.splits import IntArray, family_folds
from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import AnalysisConfig, ExperimentConfig
from crosstalk.contracts.parquet import write_table
from crosstalk.contracts.tables import schema_for
from crosstalk.evaluation.figures import balanced_accuracy_by_regime
from crosstalk.features.extract import FEATURE_SCHEMA_VERSION, R1_NAMES, matrix, r1
from crosstalk.features.traces import TraceSet, load_traces
from crosstalk.stats.bootstrap import ClusterResamples, bootstrap_metrics
from crosstalk.stats.controls import (
    drift,
    icc,
    permutation_p_value,
    permute_family_classes,
    power_check,
)
from crosstalk.stats.metrics import Scorer

LEARNING_FRACTIONS = (0.25, 0.5, 0.75, 1.0)
REGIME_ORDER = ("NAT", "MATCH-P", "MATCH-PR")
NOISE_FEATURES = ("resp_count", "duration_ms", "ttft_ms")
BASELINE_ARM = "a0"


@dataclass(slots=True)
class Results:
    experiment_id: str
    bootstrap: int
    evaluations: list[dict[str, object]] = field(default_factory=lambda: [])
    predictions: list[dict[str, object]] = field(default_factory=lambda: [])
    features: dict[str, list[dict[str, object]]] = field(default_factory=lambda: {})
    replicates: dict[tuple[str, str, str, str], FloatArray] = field(default_factory=lambda: {})
    summary: dict[str, Any] = field(default_factory=lambda: {})

    def metric(self, regime: str, task: str, rep: str, model: str, name: str) -> dict[str, object]:
        for row in self.evaluations:
            if (row["regime"], row["task"], row["representation"], row["model"], row["metric"]) == (
                regime,
                task,
                rep,
                model,
                name,
            ):
                return row
        raise KeyError((regime, task, rep, model, name))


def discover(root: Path, experiment_id: str) -> list[dict[str, object]]:
    manifests: list[dict[str, object]] = []
    for path in sorted((root / "sessions").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["experiment_id"] == experiment_id:
            manifests.append(manifest)
    return manifests


def _codes(values: Sequence[object], vocabulary: Sequence[str]) -> IntArray:
    index = {name: position for position, name in enumerate(vocabulary)}
    return np.asarray([index[str(v)] for v in values], dtype=np.int64)


def _score(
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
) -> None:
    by_model: dict[str, dict[int, FloatArray]] = defaultdict(dict)
    for prediction in predictions:
        seeds = by_model[prediction.model]
        if prediction.seed not in seeds:
            seeds[prediction.seed] = np.full((y.size, len(vocabulary)), np.nan)
        seeds[prediction.seed][prediction.row] = prediction.proba
        results.predictions.append(
            {
                "session_id": traces.traces[prediction.row].session_id,
                "request_id": traces.traces[prediction.row].request_id,
                "regime": regime,
                "task": task,
                "representation": rep,
                "fold": prediction.fold,
                "arm": BASELINE_ARM,
                "setting": "static",
                "model": prediction.model,
                "seed": prediction.seed,
                "y_true": vocabulary[int(y[prediction.row])],
                "proba": [float(v) for v in prediction.proba],
            }
        )
    n_families = len({t.labels["family_id"] for t in traces.traces})
    for model, seeds in by_model.items():
        probas = [seeds[seed] for seed in sorted(seeds)]
        if any(np.isnan(p).any() for p in probas):
            raise ValidationError(f"{model} left rows without an out-of-fold prediction")
        scorer = Scorer(y, probas, len(vocabulary), sensitive, tuple(base_rates))
        for name, (value, low, high, replicates) in bootstrap_metrics(
            scorer.metrics, y.size, resamples
        ).items():
            if name == "balanced_accuracy":
                results.replicates[(regime, task, rep, model)] = replicates
            results.evaluations.append(
                {
                    "experiment_id": results.experiment_id,
                    "regime": regime,
                    "task": task,
                    "representation": rep,
                    "arm": BASELINE_ARM,
                    "setting": "static",
                    "model": model,
                    "metric": name,
                    "value": value,
                    "ci_lo": low,
                    "ci_hi": high,
                    "n": int(y.size),
                    "n_families": n_families,
                    "B": resamples.b,
                }
            )


def _feature_rows(traces: TraceSet, x: FloatArray, names: Sequence[str]) -> list[dict[str, object]]:
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


def _attack_regime(
    results: Results,
    regime: str,
    traces: TraceSet,
    classes: list[str],
    sensitive_class: str | None,
    models: list[Model],
    analysis: AnalysisConfig,
) -> None:
    families = [str(f) for f in traces.column("family_id")]
    labels = [str(c) for c in traces.column("class")]
    arms = [str(a) for a in traces.column("arm")]
    y = _codes(labels, classes)
    folds = family_folds(families, labels, analysis.outer_folds, analysis.seed)
    resamples = ClusterResamples.draw(families, labels, analysis.bootstrap, analysis.seed)
    for rep in analysis.representations:
        x, names = matrix(traces, rep)
        results.features[f"{regime}-{rep}"] = _feature_rows(traces, x, names)
        design = Design(x, y, families, labels, arms, folds, len(classes))
        predictions = cross_validate(
            design, models, analysis.seeds, analysis.inner_folds, BASELINE_ARM, BASELINE_ARM
        )
        _score(results, traces, regime, "t1", rep, predictions, y, classes, resamples)
    if sensitive_class is not None and "r1" in analysis.representations:
        binary = ["sensitive" if c == sensitive_class else "background" for c in labels]
        vocabulary = ["background", "sensitive"]
        y2 = _codes(binary, vocabulary)
        x, _ = matrix(traces, "r1")
        design = Design(x, y2, families, labels, arms, folds, 2)
        predictions = cross_validate(
            design, models, analysis.seeds, analysis.inner_folds, BASELINE_ARM, BASELINE_ARM
        )
        _score(
            results,
            traces,
            regime,
            "t2",
            "r1",
            predictions,
            y2,
            vocabulary,
            resamples,
            sensitive=1,
            base_rates=analysis.sensitive_base_rates,
        )


def _null_control(
    results: Results,
    regime: str,
    traces: TraceSet,
    classes: list[str],
    analysis: AnalysisConfig,
) -> dict[str, object]:
    families = [str(f) for f in traces.column("family_id")]
    labels = [str(c) for c in traces.column("class")]
    arms = [str(a) for a in traces.column("arm")]
    x, _ = matrix(traces, "r1")
    chance = 1.0 / len(classes)
    models = resolve(analysis.null_models)
    per_model: dict[str, list[dict[str, float]]] = defaultdict(list)
    for permutation in range(analysis.null_permutations):
        seed = analysis.seed + 10_000 + permutation
        permuted = permute_family_classes(families, labels, seed)
        y = _codes(permuted, classes)
        folds = family_folds(families, permuted, analysis.outer_folds, seed)
        design = Design(x, y, families, permuted, arms, folds, len(classes))
        predictions = cross_validate(
            design, models, 1, analysis.inner_folds, BASELINE_ARM, BASELINE_ARM
        )
        resamples = ClusterResamples.draw(families, permuted, analysis.null_bootstrap, seed)
        for model in models:
            proba = np.full((y.size, len(classes)), np.nan)
            for prediction in predictions:
                if prediction.model == model.name:
                    proba[prediction.row] = prediction.proba
            scorer = Scorer(y, [proba], len(classes))
            value, low, high, _ = bootstrap_metrics(
                lambda w, s=scorer: {"ba": s.metrics(w)["balanced_accuracy"]}, y.size, resamples
            )["ba"]
            per_model[model.name].append({"value": value, "ci_lo": low, "ci_hi": high})
    summary: dict[str, object] = {}
    for model, runs in per_model.items():
        observed_row = results.metric(regime, "t1", "r1", model, "balanced_accuracy")
        observed = float(str(observed_row["value"]))
        values = [run["value"] for run in runs]
        within = sum(run["ci_lo"] <= chance <= run["ci_hi"] for run in runs)
        summary[model] = {
            "permutations": len(runs),
            "chance": chance,
            "null_mean": float(np.mean(values)),
            "null_max": float(np.max(values)),
            "chance_within_ci": within,
            "passes": within == len(runs),
            "observed": observed,
            "p_value": permutation_p_value(observed, values),
            "runs": runs,
        }
    return summary


def _learning_curve(
    results: Results,
    regime: str,
    traces: TraceSet,
    classes: list[str],
    models: list[Model],
    analysis: AnalysisConfig,
) -> dict[str, object]:
    families = [str(f) for f in traces.column("family_id")]
    labels = [str(c) for c in traces.column("class")]
    arms = [str(a) for a in traces.column("arm")]
    y = _codes(labels, classes)
    x, _ = matrix(traces, "r1")
    folds = family_folds(families, labels, analysis.outer_folds, analysis.seed)
    resamples = ClusterResamples.draw(families, labels, analysis.null_bootstrap, analysis.seed)
    design = Design(x, y, families, labels, arms, folds, len(classes))
    curve: dict[str, list[dict[str, object]]] = defaultdict(list)
    learners = [m for m in models if m.name in ("lr", "lgbm")]
    for fraction in LEARNING_FRACTIONS:
        try:
            predictions = cross_validate(
                design,
                learners,
                1,
                analysis.inner_folds,
                BASELINE_ARM,
                BASELINE_ARM,
                fraction=fraction,
                fraction_seed=analysis.seed,
            )
        except ConfigError as error:
            # Too few training families left for the inner folds at this fraction.
            for model in learners:
                curve[model.name].append({"fraction": fraction, "skipped": str(error)})
            continue
        for model in learners:
            proba = np.full((y.size, len(classes)), np.nan)
            for prediction in predictions:
                if prediction.model == model.name:
                    proba[prediction.row] = prediction.proba
            scorer = Scorer(y, [proba], len(classes))
            value, low, high, _ = bootstrap_metrics(
                lambda w, s=scorer: {"ba": s.metrics(w)["balanced_accuracy"]}, y.size, resamples
            )["ba"]
            curve[model.name].append(
                {"fraction": fraction, "value": value, "ci_lo": low, "ci_hi": high}
            )
    return dict(curve)


def _noise_floor(traces: TraceSet, analysis: AnalysisConfig) -> dict[str, object]:
    if len(traces.traces) < 4:
        return {"n": len(traces.traces), "note": "too few determinism probes"}
    indices = [R1_NAMES.index(name) for name in NOISE_FEATURES]
    values = np.vstack([r1(t)[indices] for t in traces.traces])
    prompts = [str(t.labels["prompt_id"]) for t in traces.traces]
    first_syn: dict[str, int] = {}
    for trace in traces.traces:
        first_syn[trace.session_id] = min(
            first_syn.get(trace.session_id, trace.t_syn_ns), trace.t_syn_ns
        )
    hours = np.asarray(
        [(t.t_syn_ns - first_syn[t.session_id]) / 3.6e12 for t in traces.traces], dtype=np.float64
    )
    keyed = [f"{t.session_id}:{t.labels['prompt_id']}" for t in traces.traces]
    result: dict[str, object] = {"n": len(traces.traces), "prompts": len(set(prompts))}
    for column, name in enumerate(NOISE_FEATURES):
        result[name] = {
            **icc(values[:, column], prompts),
            "drift": drift(values[:, column], keyed, hours, 1000, analysis.seed),
        }
    return result


def _fmt(row: dict[str, object]) -> str:
    value, low, high = (float(str(row[key])) for key in ("value", "ci_lo", "ci_hi"))
    if np.isnan(value):
        return "n/a"
    return f"{value:.3f} [{low:.3f}, {high:.3f}]"


def _report(results: Results, analysis: AnalysisConfig, figure: str | None) -> str:
    summary = results.summary
    lines = [f"# E0 report: `{results.experiment_id}`", ""]
    if not summary["reportable"]:
        lines += [
            "> **DEVELOPMENT RESULT, NOT REPORTABLE.** At least one session is not `VALID` or "
            "was collected from a dirty tree (see Sessions). These numbers exercise the "
            "pipeline; they are not evidence.",
            "",
        ]
    lines += [
        f"Feature schema v{FEATURE_SCHEMA_VERSION} · bootstrap B = {analysis.bootstrap} "
        f"(family clusters, stratified by class) · outer folds = {analysis.outer_folds} "
        f"(family-disjoint) · inner folds = {analysis.inner_folds} · seeds = {analysis.seeds}.",
        "",
        "## Sessions",
        "",
        "| Session | Regime(s) | Status | Dirty tree | Reasons |",
        "|---|---|---|---|---|",
    ]
    for session in summary["sessions"]:
        lines.append(
            f"| `{session['session_id']}` | {session['regimes']} | {session['status']} | "
            f"{session['git_dirty']} | {'; '.join(session['reasons']) or '-'} |"
        )
    lines += ["", "## Data", "", "| Regime | Traces | Families | Per class |", "|---|---|---|---|"]
    for regime, info in summary["data"].items():
        lines.append(f"| {regime} | {info['traces']} | {info['families']} | {info['per_class']} |")
    lines += ["", f"Excluded traces: {summary['excluded'] or 'none'}.", ""]
    classes = summary["classes"]
    chance = 1 / len(classes)
    lines += [
        "## T1: topic (balanced accuracy, macro-F1, macro PR-AUC)",
        "",
        f"{len(classes)} classes; chance balanced accuracy = {chance:.3f}. Values are point "
        "estimates with 95% percentile cluster-bootstrap intervals.",
        "",
    ]
    if figure:
        lines += [
            f"![Balanced accuracy by regime and model]({figure})",
            "",
        ]
    lines += [
        "| Regime | Representation | Model | Balanced accuracy | Macro-F1 | Macro PR-AUC | Log-loss |",
        "|---|---|---|---|---|---|---|",
    ]
    models = list(analysis.models)
    for regime in summary["regimes"]:
        for rep in analysis.representations:
            for model in models:
                cells = [
                    _fmt(results.metric(regime, "t1", rep, model, metric))
                    for metric in ("balanced_accuracy", "macro_f1", "macro_pr_auc", "log_loss")
                ]
                lines.append(f"| {regime} | {rep} | {model} | " + " | ".join(cells) + " |")
    if "tlen" in analysis.representations:
        lines += [
            "",
            "The `tlen` rows are the T-len diagnostic: the attacker sees only request bytes and "
            "count, response record count and bytes, and duration. The gap between `r1` and "
            "`tlen` is leakage beyond length.",
        ]
    sensitive = summary.get("sensitive_class")
    if sensitive:
        lines += [
            "",
            f"## T2: sensitive topic (`{sensitive}` vs rest, R1)",
            "",
            "PR-AUC and precision at fixed recall, re-weighted to explicit base rates.",
            "",
            "| Regime | Model | Base rate | PR-AUC | P@R0.05 | P@R0.10 | P@R0.25 |",
            "|---|---|---|---|---|---|---|",
        ]
        for regime in summary["regimes"]:
            for model in models:
                for rate in analysis.sensitive_base_rates:
                    cells = [_fmt(results.metric(regime, "t2", "r1", model, f"pr_auc_1to{rate}"))]
                    cells += [
                        _fmt(
                            results.metric(
                                regime, "t2", "r1", model, f"precision_at_recall{level}_1to{rate}"
                            )
                        )
                        for level in ("0.05", "0.10", "0.25")
                    ]
                    lines.append(f"| {regime} | {model} | 1:{rate} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Null control",
        "",
        "Family → class mapping permuted (class sizes preserved); full D0 static pipeline on R1. "
        "Passing means chance lies inside every permutation's bootstrap interval.",
        "",
        "| Regime | Model | Permutations | Null mean | Null max | Chance in CI | Passes | Observed | p |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for regime, per_model in summary["null_control"].items():
        for model, info in per_model.items():
            lines.append(
                f"| {regime} | {model} | {info['permutations']} | {info['null_mean']:.3f} | "
                f"{info['null_max']:.3f} | {info['chance_within_ci']}/{info['permutations']} | "
                f"{'yes' if info['passes'] else '**NO**'} | {info['observed']:.3f} | "
                f"{info['p_value']:.3f} |"
            )
    lines += [
        "",
        "## Noise floor (determinism probe)",
        "",
        "Repeated probe prompts. ICC near 1 means repeats of one prompt agree far more than "
        "different prompts do; drift is the within-prompt slope over session time.",
        "",
        "| Regime | Feature | n | ICC | Within var | Between var | Drift / hour [95% CI] |",
        "|---|---|---|---|---|---|---|",
    ]
    for regime, info in summary["noise_floor"].items():
        if "note" in info:
            lines.append(f"| {regime} | - | {info['n']} | {info['note']} | | | |")
            continue
        for name in NOISE_FEATURES:
            item = info[name]
            d = item["drift"]
            if np.isnan(item["icc"]):
                lines.append(
                    f"| {regime} | {name} | {info['n']} | n/a (needs repeated probes) | | | |"
                )
                continue
            lines.append(
                f"| {regime} | {name} | {info['n']} | {item['icc']:.3f} | {item['within_var']:.4g} | "
                f"{item['between_var']:.4g} | {d['slope_per_hour']:.4g} "
                f"[{d['ci_lo']:.4g}, {d['ci_hi']:.4g}] |"
            )
    power: dict[str, Any] | None = summary.get("power")
    if power is not None:
        lines += [
            "",
            "## Power check",
            "",
            f"From `{power['regime']}` / `{power['model']}` (R1): bootstrap SE of balanced "
            f"accuracy {power['bootstrap_se']:.4f} at {power['pilot_per_class']} per class. "
            f"Projected 95% half-width of a paired Δ at {power['planned_per_class']} per class: "
            f"{power['planned_half_width']:.3f} (target {power['target_half_width']:.3f}). "
            + (
                f"Recommended n per class for E1/E2: **{power['recommended_per_class']}**."
                if power["meets_target_at_max"]
                else f"Target not reachable at {analysis.max_per_class} per class; resolution "
                f"limit there is {power['half_width_at_max']:.3f}."
            ),
        ]
    curve: dict[str, Any] = summary.get("learning_curve") or {}
    if curve:
        lines += [
            "",
            "## Learning curve (R1, fraction of training families)",
            "",
            "| Regime | Model | 25% | 50% | 75% | 100% |",
            "|---|---|---|---|---|---|",
        ]
        for regime, per_model in curve.items():
            for model, points in per_model.items():
                cells = [
                    "n/a (too few families)"
                    if "skipped" in p
                    else f"{p['value']:.3f} [{p['ci_lo']:.3f}, {p['ci_hi']:.3f}]"
                    for p in points
                ]
                lines.append(f"| {regime} | {model} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Reading this report",
        "",
        "E0 is a reproduction and calibration step, not a contribution. Absolute numbers depend "
        "on the synthetic corpus and the served model; the claims that matter later are paired "
        "comparisons with intervals (E1/E2).",
        "",
    ]
    return "\n".join(lines)


def _write(path: Path, table: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows, schema=schema_for(table)), path)


def run_e0(
    root: Path,
    config: ExperimentConfig,
    allow_unvalidated: bool = False,
    replace: bool = False,
) -> Path:
    analysis = config.analysis or AnalysisConfig()
    models = resolve(analysis.models)
    resolve(analysis.null_models)
    manifests = discover(root, config.name)
    if not manifests:
        raise ValidationError(f"No sessions found for experiment {config.name}")
    unreportable = [
        str(m["session_id"]) for m in manifests if m["status"] != "VALID" or m["git_dirty"]
    ]
    if unreportable and not allow_unvalidated:
        raise ValidationError(
            f"Sessions are not reportable (not VALID or dirty): {unreportable}. "
            "Use --allow-unvalidated for a development run."
        )
    target = root / "experiments" / config.name
    if target.exists() and not replace:
        raise ValidationError(f"Experiment output already exists: {target} (use --replace)")
    traces = load_traces(root, [str(m["session_id"]) for m in manifests])
    main = TraceSet(
        [t for t in traces.traces if t.labels["role"] == "main" and t.labels["arm"] == BASELINE_ARM]
    )
    classes = sorted({str(c) for c in main.column("class")})
    if len(classes) < 2:
        raise ConfigError("E0 needs at least two classes")
    sensitive = sorted({str(t.labels["class"]) for t in main.traces if t.labels["t2_sensitive"]})
    sensitive_class = sensitive[0] if len(sensitive) == 1 else None
    regimes = [r for r in REGIME_ORDER if r in {t.labels["regime"] for t in main.traces}]
    results = Results(config.name, analysis.bootstrap)
    data: dict[str, object] = {}
    null: dict[str, object] = {}
    noise: dict[str, object] = {}
    curve: dict[str, object] = {}
    for regime in regimes:
        subset = main.select(regime=regime)
        counts = Counter(str(c) for c in subset.column("class"))
        data[regime] = {
            "traces": len(subset.traces),
            "families": len(set(subset.column("family_id"))),
            "per_class": dict(sorted(counts.items())),
        }
        _attack_regime(results, regime, subset, classes, sensitive_class, models, analysis)
        if analysis.null_permutations and "r1" in analysis.representations:
            null[regime] = _null_control(results, regime, subset, classes, analysis)
        if analysis.learning_curve and "r1" in analysis.representations:
            curve[regime] = _learning_curve(results, regime, subset, classes, models, analysis)
        probes = TraceSet(
            [
                t
                for t in traces.traces
                if t.labels["role"] == "determinism"
                and t.labels["regime"] == regime
                and t.labels["arm"] == BASELINE_ARM
            ]
        )
        noise[regime] = _noise_floor(probes, analysis)
    power: dict[str, object] | None = None
    learners = [m.name for m in models if m.name in ("lgbm", "lr")]
    if regimes and learners and "r1" in analysis.representations:
        pilot = regimes[0]
        best = max(
            learners,
            key=lambda m: float(
                str(results.metric(pilot, "t1", "r1", m, "balanced_accuracy")["value"])
            ),
        )
        per_class = min(Counter(str(c) for c in main.select(regime=pilot).column("class")).values())
        power = {
            "regime": pilot,
            "model": best,
            **power_check(
                results.replicates[(pilot, "t1", "r1", best)],
                per_class,
                analysis.planned_per_class,
                analysis.max_per_class,
                analysis.target_half_width,
            ),
        }
    results.summary = {
        "experiment_id": config.name,
        "config_sha256": config.sha256(),
        "reportable": not unreportable,
        "sessions": [
            {
                "session_id": m["session_id"],
                "status": m["status"],
                "git_dirty": m["git_dirty"],
                "git_commit": m["git_commit"],
                "workload_sha256": m["workload_sha256"],
                "reasons": m["reasons"],
                "regimes": ", ".join(
                    sorted(
                        {
                            str(t.labels["regime"])
                            for t in traces.traces
                            if t.session_id == m["session_id"]
                        }
                    )
                ),
            }
            for m in manifests
        ],
        "classes": classes,
        "sensitive_class": sensitive_class,
        "regimes": regimes,
        "data": data,
        "excluded": dict(Counter(e["reason"] for e in traces.excluded)),
        "null_control": null,
        "noise_floor": noise,
        "power": power,
        "learning_curve": curve,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }
    staging = root / "experiments" / f".tmp-{new_ulid()}"
    try:
        for name, rows in results.features.items():
            _write(staging / "features" / f"{name}.parquet", "features", rows)
        _write(staging / "predictions" / "predictions.parquet", "predictions", results.predictions)
        _write(staging / "evaluations" / "evaluations.parquet", "evaluations", results.evaluations)
        figure: str | None = None
        points = [
            (
                regime,
                model.name,
                *(
                    float(
                        str(
                            results.metric(regime, "t1", "r1", model.name, "balanced_accuracy")[key]
                        )
                    )
                    for key in ("value", "ci_lo", "ci_hi")
                ),
            )
            for regime in regimes
            for model in models
            if "r1" in analysis.representations
        ]
        if points:
            figure = "figures/e0_balanced_accuracy.png"
            balanced_accuracy_by_regime(
                points,  # type: ignore[arg-type]
                1 / len(classes),
                staging / figure,
            )
        (staging / "summary.json").write_text(
            json.dumps(results.summary, indent=2, default=float), encoding="utf-8"
        )
        (staging / "report.md").write_text(_report(results, analysis, figure), encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target
