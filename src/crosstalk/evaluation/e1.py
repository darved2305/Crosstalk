"""E1: effectiveness and cost of each defense arm against static and adaptive attackers.

Static: trained on D0 traffic of the training families, tested on arm-k traffic of the
held-out families. Adaptive: trained on arm-k traffic of the training families only, tested
on the same held-out families. One family fold map and one set of family resamples serve
every arm and setting, so every comparison is paired.

Attackers see only observer features (``crosstalk.features``); cost metrics come from the
oracle in the evaluate stage and never reach an attacker.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from crosstalk.attacks.models import Model, resolve
from crosstalk.attacks.protocols import Design, cross_validate
from crosstalk.attacks.splits import family_folds
from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.contracts.config import AnalysisConfig, ExperimentConfig
from crosstalk.evaluation.common import (
    BASELINE_ARM,
    Results,
    codes,
    discover,
    feature_rows,
    fmt,
    reportable,
    require_single,
    score,
    staged_output,
    write_results,
)
from crosstalk.evaluation.costs import load_costs, summarize
from crosstalk.evaluation.figures import balanced_accuracy_by_arm
from crosstalk.features.extract import FEATURE_SCHEMA_VERSION, matrix
from crosstalk.features.traces import TraceSet, load_traces
from crosstalk.stats.bootstrap import ClusterResamples, percentile_ci
from crosstalk.stats.paired import delta, holm, retained_leakage

SETTINGS = ("static", "adaptive")


def _attack(
    results: Results,
    regime: str,
    traces: TraceSet,
    classes: list[str],
    arms: list[str],
    models: list[Model],
    analysis: AnalysisConfig,
    resamples: ClusterResamples,
) -> None:
    families = [str(f) for f in traces.column("family_id")]
    labels = [str(c) for c in traces.column("class")]
    row_arms = [str(a) for a in traces.column("arm")]
    y = codes(labels, classes)
    folds = family_folds(families, labels, analysis.outer_folds, analysis.seed)
    for rep in analysis.representations:
        x, names = matrix(traces, rep)
        results.features[f"{regime}-{rep}"] = feature_rows(traces, x, names)
        design = Design(x, y, families, labels, row_arms, folds, len(classes))
        for arm in arms:
            for setting in SETTINGS if arm != BASELINE_ARM else ("static",):
                train = BASELINE_ARM if setting == "static" else arm
                predictions = cross_validate(
                    design, models, analysis.seeds, analysis.inner_folds, train, arm
                )
                score(
                    results,
                    traces,
                    regime,
                    "t1",
                    rep,
                    predictions,
                    y,
                    classes,
                    resamples,
                    arm=arm,
                    setting=setting,
                )


def _primary(
    results: Results, regime: str, arms: list[str], chance: float, analysis: AnalysisConfig
) -> dict[str, Any]:
    model, rep = analysis.primary_model, analysis.primary_representation
    base_key = (regime, "t1", rep, model, BASELINE_ARM, "static", None)
    base_reps = results.replicates[base_key]
    base = results.value(regime, "t1", rep, model, "balanced_accuracy")
    tests: dict[str, dict[str, Any]] = {}
    rhos: dict[str, dict[str, Any]] = {}
    for arm in arms:
        if arm == BASELINE_ARM:
            continue
        ratios: dict[str, np.ndarray] = {}
        for setting in SETTINGS:
            key = (regime, "t1", rep, model, arm, setting, None)
            point = results.value(
                regime, "t1", rep, model, "balanced_accuracy", arm=arm, setting=setting
            )
            replicates = results.replicates[key]
            tests[f"{arm}/{setting}"] = {
                "arm": arm,
                "setting": setting,
                "balanced_accuracy": point,
                "delta": delta(point, base, replicates, base_reps),
                "rho": retained_leakage(point, base, replicates, base_reps, chance),
            }
            with np.errstate(divide="ignore", invalid="ignore"):
                ratios[setting] = (replicates - chance) / (base_reps - chance)
        static = tests[f"{arm}/static"]["rho"]["value"]
        adaptive = tests[f"{arm}/adaptive"]["rho"]["value"]
        recovery = np.full_like(ratios["adaptive"], np.nan)
        finite = np.isfinite(ratios["adaptive"]) & np.isfinite(ratios["static"])
        np.subtract(ratios["adaptive"], ratios["static"], out=recovery, where=finite)
        low, high = percentile_ci(recovery)
        defined = not (np.isnan(static) or np.isnan(adaptive))
        rhos[arm] = {
            "static": static,
            "adaptive": adaptive,
            "recovery": {
                "value": adaptive - static if defined else float("nan"),
                "ci_lo": low if defined else float("nan"),
                "ci_hi": high if defined else float("nan"),
            },
        }
    adjusted = holm({name: test["delta"]["p"] for name, test in tests.items()}, analysis.alpha)
    for name, test in tests.items():
        test["holm"] = adjusted[name]
    return {
        "model": model,
        "representation": rep,
        "baseline_balanced_accuracy": base,
        "chance": chance,
        "alpha": analysis.alpha,
        "tests": tests,
        "retained_leakage": rhos,
    }


def _num(value: float, digits: int = 3) -> str:
    return "n/a" if np.isnan(value) else f"{value:.{digits}f}"


def _ci(item: dict[str, float], digits: int = 3) -> str:
    if np.isnan(item["value"]):
        return "n/a"
    return f"{item['value']:.{digits}f} [{item['ci_lo']:.{digits}f}, {item['ci_hi']:.{digits}f}]"


def _report(results: Results, analysis: AnalysisConfig, figure: str | None) -> str:
    summary = results.summary
    lines = [f"# E1 report: `{results.experiment_id}`", ""]
    if not summary["reportable"]:
        lines += [
            "> **DEVELOPMENT RESULT, NOT REPORTABLE.** At least one session is not `VALID` or "
            "was collected from a dirty tree. These numbers exercise the pipeline; they are "
            "not evidence.",
            "",
        ]
    lines += [
        f"Feature schema v{FEATURE_SCHEMA_VERSION} · bootstrap B = {analysis.bootstrap} "
        f"(family clusters, shared by every arm and setting) · outer folds = "
        f"{analysis.outer_folds} (family-disjoint, one map for all arms) · seeds = "
        f"{analysis.seeds}.",
        "",
        "## Sessions",
        "",
        "| Session | Status | Dirty tree | Reasons |",
        "|---|---|---|---|",
    ]
    for session in summary["sessions"]:
        lines.append(
            f"| `{session['session_id']}` | {session['status']} | {session['git_dirty']} | "
            f"{'; '.join(session['reasons']) or '-'} |"
        )
    lines += ["", "## Data", "", "| Regime | Arm | Traces | Families |", "|---|---|---|---|"]
    for regime, per_arm in summary["data"].items():
        for arm, info in per_arm.items():
            lines.append(f"| {regime} | {arm} | {info['traces']} | {info['families']} |")
    lines += ["", f"Excluded traces: {summary['excluded'] or 'none'}.", ""]
    for regime, primary in summary["primary"].items():
        lines += [
            f"## Primary comparisons ({regime}; {primary['model']}, {primary['representation']})",
            "",
            f"Δ is balanced accuracy minus D0 static ({primary['baseline_balanced_accuracy']:.3f}); "
            f"chance = {primary['chance']:.3f}. p is the two-sided paired bootstrap p-value; "
            f"Holm-Bonferroni at alpha = {primary['alpha']} across the {len(primary['tests'])} "
            "pre-declared tests. rho = (m - chance) / (m_D0 - chance) is the retained leakage "
            "(n/a when D0 is not above chance).",
            "",
        ]
        if figure:
            lines += [f"![Balanced accuracy per arm]({figure})", ""]
        lines += [
            "| Arm | Attacker | Balanced accuracy | Δ vs D0 [95% CI] | p | p (Holm) | Reject H0 | rho [95% CI] |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for test in primary["tests"].values():
            lines.append(
                f"| {test['arm']} | {test['setting']} | {test['balanced_accuracy']:.3f} | "
                f"{_ci(test['delta'])} | {_num(test['delta']['p'])} | "
                f"{_num(test['holm']['p_holm'])} | {'yes' if test['holm']['reject'] else 'no'} | "
                f"{_ci(test['rho'])} |"
            )
        lines += [
            "",
            "Static-to-adaptive recovery (rho adaptive - rho static):",
            "",
            "| Arm | rho static | rho adaptive | Recovery [95% CI] |",
            "|---|---|---|---|",
        ]
        for arm, rho in primary["retained_leakage"].items():
            lines.append(
                f"| {arm} | {_num(rho['static'])} | {_num(rho['adaptive'])} | {_ci(rho['recovery'])} |"
            )
    lines += [
        "",
        "## All attackers",
        "",
        "| Regime | Arm | Attacker | Representation | Model | Balanced accuracy | Macro-F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in results.evaluations:
        if row["metric"] != "balanced_accuracy":
            continue
        f1 = results.metric(
            str(row["regime"]),
            "t1",
            str(row["representation"]),
            str(row["model"]),
            "macro_f1",
            arm=str(row["arm"]),
            setting=str(row["setting"]),
        )
        lines.append(
            f"| {row['regime']} | {row['arm']} | {row['setting']} | {row['representation']} | "
            f"{row['model']} | {fmt(row)} | {fmt(f1)} |"
        )
    lines += [
        "",
        "## Cost (client view, same runs)",
        "",
        "TTFT and inter-token latency are measured at the client; bandwidth is TCP payload in "
        "both directions per request; overhead and ΔTTFT are paired against D0 with family "
        "cluster-bootstrap intervals.",
        "",
        "| Regime | Arm | n | TTFT p50/p95/p99 ms | ITL p50/p95/p99 ms | Bytes / request | "
        "Bandwidth overhead % | Δ TTFT p50 ms | Pad bytes | Dummy records | Timer lateness p99 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for regime, per_arm in summary["costs"].items():
        for arm, cost in per_arm.items():

            def triple(name: str, cost: dict[str, Any] = cost) -> str:
                value = cost[name]
                return (
                    "n/a"
                    if value is None
                    else "/".join(f"{value[f'p{q}']:.1f}" for q in (50, 95, 99))
                )

            lateness = cost["timer_lateness_ms"]
            lines.append(
                f"| {regime} | {arm} | {cost['n']} | {triple('ttft_ms')} | {triple('itl_ms')} | "
                f"{cost['wire_bytes_mean']:.0f} | "
                f"{_ci(cost['bandwidth_overhead_pct'], 1) if 'bandwidth_overhead_pct' in cost else '-'} | "
                f"{_ci(cost['ttft_p50_delta_ms'], 1) if 'ttft_p50_delta_ms' in cost else '-'} | "
                f"{cost['pad_bytes_mean']:.0f} | {cost['dummies_mean']:.1f} | "
                f"{'-' if lateness is None else f'{lateness["p99"]:.2f}'} |"
            )
    lines += [
        "",
        "## Reading this report",
        "",
        "The adaptive attacker is the headline (Kerckhoffs: the observer knows the deployed "
        "defense). A defense that only defeats the static attacker has not been shown to "
        "protect anything. Costs come from the same runs as the leakage numbers.",
        "",
    ]
    return "\n".join(lines)


def run_e1(
    root: Path,
    config: ExperimentConfig,
    allow_unvalidated: bool = False,
    replace: bool = False,
) -> Path:
    analysis = config.analysis or AnalysisConfig()
    models = resolve(analysis.models)
    if analysis.primary_model not in analysis.models:
        raise ConfigError("The primary model must be one of the analysed models")
    if analysis.primary_representation not in analysis.representations:
        raise ConfigError("The primary representation must be analysed")
    manifests = discover(root, config.name)
    if not manifests:
        raise ValidationError(f"No sessions found for experiment {config.name}")
    is_reportable = reportable(manifests, allow_unvalidated)
    target = root / "experiments" / config.name
    if target.exists() and not replace:
        raise ValidationError(f"Experiment output already exists: {target} (use --replace)")
    session_ids = [str(m["session_id"]) for m in manifests]
    traces = load_traces(root, session_ids)
    main = TraceSet([t for t in traces.traces if t.labels["role"] == "main"], traces.excluded)
    require_single(main, "E1", "concurrency", "stream_interval")
    arms = sorted({str(a) for a in main.column("arm")})
    if BASELINE_ARM not in arms or len(arms) < 2:
        raise ConfigError("E1 needs the D0 arm and at least one defense arm")
    classes = sorted({str(c) for c in main.column("class")})
    chance = 1 / len(classes)
    regimes = sorted({str(r) for r in main.column("regime")})
    results = Results(config.name, analysis.bootstrap)
    costs = load_costs(root, session_ids)
    data: dict[str, Any] = {}
    primary: dict[str, Any] = {}
    cost_summary: dict[str, Any] = {}
    for regime in regimes:
        subset = main.select(regime=regime)
        families = [str(f) for f in subset.column("family_id")]
        labels = [str(c) for c in subset.column("class")]
        resamples = ClusterResamples.draw(families, labels, analysis.bootstrap, analysis.seed)
        data[regime] = {
            arm: {
                "traces": len(subset.select(arm=arm).traces),
                "families": len(set(subset.select(arm=arm).column("family_id"))),
            }
            for arm in arms
        }
        _attack(results, regime, subset, classes, arms, models, analysis, resamples)
        primary[regime] = _primary(results, regime, arms, chance, analysis)
        cost_summary[regime] = summarize(
            costs,
            [t.request_id for t in subset.traces],
            [str(t.labels["arm"]) for t in subset.traces],
            resamples,
        )
    results.summary = {
        "experiment_id": config.name,
        "config_sha256": config.sha256(),
        "reportable": is_reportable,
        "sessions": [
            {
                "session_id": m["session_id"],
                "status": m["status"],
                "git_dirty": m["git_dirty"],
                "git_commit": m["git_commit"],
                "reasons": m["reasons"],
            }
            for m in manifests
        ],
        "arms": arms,
        "classes": classes,
        "regimes": regimes,
        "data": data,
        "excluded": dict(Counter(e["reason"] for e in traces.excluded)),
        "primary": primary,
        "costs": cost_summary,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }
    with staged_output(root, config.name, replace) as staging:
        figure = "figures/e1_balanced_accuracy.png"
        regime = regimes[0]
        model, rep = analysis.primary_model, analysis.primary_representation
        points: list[tuple[str, str, float, float, float]] = []
        for arm in arms:
            for setting in SETTINGS if arm != BASELINE_ARM else ("static",):
                row = results.metric(
                    regime, "t1", rep, model, "balanced_accuracy", arm=arm, setting=setting
                )
                value, low, high = (float(str(row[k])) for k in ("value", "ci_lo", "ci_hi"))
                points.append((setting, arm, value, low, high))
        balanced_accuracy_by_arm(points, chance, model, staging / figure)
        write_results(staging, results, lambda: _report(results, analysis, figure))
    return target
