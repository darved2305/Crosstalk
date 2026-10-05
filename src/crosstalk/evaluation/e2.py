"""E2: does a defense that looks effective at low concurrency stay effective under load?

Sessions alternate concurrency blocks in time (C1 → C8 → C32, repeated), so load is not
confounded with drift; replicate sessions come from different days. Within each level,
attackers are trained and tested at that level only, and retained leakage is normalised by
D0 at the same level. One family fold map and one set of two-stage resamples (sessions,
then families) serve every level, condition and setting, so every contrast is paired.
"""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from crosstalk.attacks.models import FloatArray, Model, resolve
from crosstalk.attacks.protocols import Design, cross_validate
from crosstalk.attacks.splits import family_folds
from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.contracts.config import AnalysisConfig, ExperimentConfig
from crosstalk.evaluation.common import (
    BASELINE_ARM,
    Results,
    codes,
    discover,
    fmt,
    reportable,
    score,
    staged_output,
    write_results,
    write_rows,
)
from crosstalk.evaluation.costs import load_costs, summarize
from crosstalk.evaluation.figures import balanced_accuracy_by_level
from crosstalk.features.extract import FEATURE_SCHEMA_VERSION, R1_NAMES, matrix, r1
from crosstalk.features.traces import Trace, TraceSet, load_traces
from crosstalk.stats.bootstrap import TwoStageResamples, percentile_ci
from crosstalk.stats.paired import bootstrap_p_value, delta, holm, retained_leakage

SETTINGS = ("static", "adaptive")
REGIME = "NAT"


def condition_of(trace: Trace, base_interval: int) -> str:
    arm = str(trace.labels["arm"])
    interval = int(str(trace.labels["stream_interval"]))
    return arm if interval == base_interval else f"{arm}/si{interval}"


def _base_interval(traces: TraceSet) -> int:
    """The stream interval every arm was run at; variants are the others."""
    by_arm: dict[str, set[int]] = defaultdict(set)
    for trace in traces.traces:
        by_arm[str(trace.labels["arm"])].add(int(str(trace.labels["stream_interval"])))
    groups = list(by_arm.values())
    shared: set[int] = groups[0].intersection(*groups[1:]) if groups else set()
    if len(shared) != 1:
        raise ConfigError(f"No single stream interval is shared by every arm: {dict(by_arm)}")
    return shared.pop()


def _ratio(
    results: Results, arm: str, setting: str, level: int, model: str, rep: str, chance: float
) -> tuple[float, FloatArray]:
    point = results.value(
        REGIME, "t1", rep, model, "balanced_accuracy", arm=arm, setting=setting, concurrency=level
    )
    base = results.value(REGIME, "t1", rep, model, "balanced_accuracy", concurrency=level)
    reps = results.replicates[(REGIME, "t1", rep, model, arm, setting, level)]
    base_reps = results.replicates[(REGIME, "t1", rep, model, BASELINE_ARM, "static", level)]
    base_low, _ = percentile_ci(base_reps)
    if not base_low > chance:
        return float("nan"), np.full(reps.size, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = (reps - chance) / (base_reps - chance)
    ratios[~np.isfinite(ratios)] = np.nan
    return (point - chance) / (base - chance), ratios


def _contrasts(
    results: Results,
    conditions: list[str],
    levels: list[int],
    chance: float,
    analysis: AnalysisConfig,
) -> dict[str, Any]:
    """The pre-declared E2 family: D0 leakage and each arm's adaptive rho, C_max vs C_min."""
    model, rep = analysis.primary_model, analysis.primary_representation
    low, high = min(levels), max(levels)
    tests: dict[str, dict[str, Any]] = {}
    key_low = (REGIME, "t1", rep, model, BASELINE_ARM, "static", low)
    key_high = (REGIME, "t1", rep, model, BASELINE_ARM, "static", high)
    m_low = results.value(REGIME, "t1", rep, model, "balanced_accuracy", concurrency=low)
    m_high = results.value(REGIME, "t1", rep, model, "balanced_accuracy", concurrency=high)
    tests["a0 leakage"] = {
        "quantity": f"balanced accuracy(a0, C{high}) - balanced accuracy(a0, C{low})",
        "low": m_low,
        "high": m_high,
        **delta(m_high, m_low, results.replicates[key_high], results.replicates[key_low]),
    }
    for condition in conditions:
        if condition == BASELINE_ARM or "/" in condition:
            continue
        rho_low, reps_low = _ratio(results, condition, "adaptive", low, model, rep, chance)
        rho_high, reps_high = _ratio(results, condition, "adaptive", high, model, rep, chance)
        differences = reps_high - reps_low
        ci_lo, ci_hi = percentile_ci(differences)
        defined = not (np.isnan(rho_low) or np.isnan(rho_high))
        tests[f"{condition} adaptive rho"] = {
            "quantity": f"rho_adaptive({condition}, C{high}) - rho_adaptive({condition}, C{low})",
            "low": rho_low,
            "high": rho_high,
            "value": rho_high - rho_low if defined else float("nan"),
            "ci_lo": ci_lo if defined else float("nan"),
            "ci_hi": ci_hi if defined else float("nan"),
            "p": bootstrap_p_value(differences) if defined else float("nan"),
        }
    adjusted = holm({name: test["p"] for name, test in tests.items()}, analysis.alpha)
    for name, test in tests.items():
        test["holm"] = adjusted[name]
    return {"model": model, "representation": rep, "low": low, "high": high, "tests": tests}


def _per_level(
    results: Results,
    conditions: list[str],
    levels: list[int],
    chance: float,
    analysis: AnalysisConfig,
) -> dict[str, Any]:
    model, rep = analysis.primary_model, analysis.primary_representation
    table: dict[str, Any] = {}
    for level in levels:
        base = results.value(REGIME, "t1", rep, model, "balanced_accuracy", concurrency=level)
        base_reps = results.replicates[(REGIME, "t1", rep, model, BASELINE_ARM, "static", level)]
        rows: dict[str, Any] = {}
        for condition in conditions:
            for setting in SETTINGS if condition != BASELINE_ARM else ("static",):
                key = (REGIME, "t1", rep, model, condition, setting, level)
                point = results.value(
                    REGIME,
                    "t1",
                    rep,
                    model,
                    "balanced_accuracy",
                    arm=condition,
                    setting=setting,
                    concurrency=level,
                )
                rows[f"{condition}/{setting}"] = {
                    "condition": condition,
                    "setting": setting,
                    "balanced_accuracy": results.metric(
                        REGIME,
                        "t1",
                        rep,
                        model,
                        "balanced_accuracy",
                        arm=condition,
                        setting=setting,
                        concurrency=level,
                    ),
                    "rho": retained_leakage(
                        point, base, results.replicates[key], base_reps, chance
                    ),
                }
        table[str(level)] = rows
    return table


def _aggregation(
    results: Results, conditions: list[str], levels: list[int], analysis: AnalysisConfig
) -> dict[str, Any]:
    """H5 (exploratory): server-side stream interval vs gateway batching, adaptive attacker."""
    model, rep = analysis.primary_model, analysis.primary_representation
    variants = [c for c in conditions if "/si" in c]
    out: dict[str, Any] = {}
    for level in levels:
        for variant in variants:
            for reference in ("a2", variant.split("/")[0]):
                if reference not in conditions or reference == variant:
                    continue
                key_a = (REGIME, "t1", rep, model, variant, "adaptive", level)
                key_b = (REGIME, "t1", rep, model, reference, "adaptive", level)
                if key_a not in results.replicates or key_b not in results.replicates:
                    continue
                a = results.value(
                    REGIME,
                    "t1",
                    rep,
                    model,
                    "balanced_accuracy",
                    arm=variant,
                    setting="adaptive",
                    concurrency=level,
                )
                b = results.value(
                    REGIME,
                    "t1",
                    rep,
                    model,
                    "balanced_accuracy",
                    arm=reference,
                    setting="adaptive",
                    concurrency=level,
                )
                out[f"C{level}: {variant} vs {reference}"] = delta(
                    a, b, results.replicates[key_a], results.replicates[key_b]
                )
    return out


def _probe_drift(traces: TraceSet) -> list[dict[str, object]]:
    """Determinism probes per concurrency block: residual drift between blocks."""
    indices = [R1_NAMES.index(name) for name in ("resp_count", "duration_ms", "ttft_ms")]
    groups: dict[tuple[str, int, int], list[FloatArray]] = defaultdict(list)
    for trace in traces.traces:
        key = (
            trace.session_id,
            int(str(trace.labels["cblock"])),
            int(str(trace.labels["concurrency"])),
        )
        groups[key].append(r1(trace)[indices])
    rows: list[dict[str, object]] = []
    for (session, cblock, level), values in sorted(groups.items()):
        stacked = np.vstack(values)
        rows.append(
            {
                "session_id": session,
                "cblock": cblock,
                "concurrency": level,
                "probes": len(values),
                "resp_count_median": float(np.median(stacked[:, 0])),
                "duration_ms_median": float(np.median(stacked[:, 1])),
                "ttft_ms_median": float(np.median(stacked[:, 2])),
            }
        )
    return rows


def _num(value: float, digits: int = 3) -> str:
    return "n/a" if np.isnan(value) else f"{value:.{digits}f}"


def _ci(item: dict[str, Any], digits: int = 3) -> str:
    if np.isnan(item["value"]):
        return "n/a"
    return f"{item['value']:.{digits}f} [{item['ci_lo']:.{digits}f}, {item['ci_hi']:.{digits}f}]"


def _report(results: Results, analysis: AnalysisConfig, figure: str | None) -> str:
    summary = results.summary
    lines = [f"# E2 report: `{results.experiment_id}`", ""]
    if not summary["reportable"]:
        lines += [
            "> **DEVELOPMENT RESULT, NOT REPORTABLE.** At least one session is not `VALID` or "
            "was collected from a dirty tree. These numbers exercise the pipeline; they are "
            "not evidence.",
            "",
        ]
    lines += [
        f"Feature schema v{FEATURE_SCHEMA_VERSION} · two-stage bootstrap B = {analysis.bootstrap} "
        f"(sessions, then families within class) · {len(summary['sessions'])} session(s) · "
        f"levels {summary['levels']} · base stream interval {summary['base_interval']}.",
        "",
    ]
    if len(summary["sessions"]) < 2:
        lines += [
            "> Only one session: intervals reflect family variation only, not day-to-day "
            "variation. The plan calls for two replicate sessions on different days.",
            "",
        ]
    lines += [
        "## Sessions and blocks",
        "",
        "| Session | Status | Dirty tree | Blocks (C x slots) |",
        "|---|---|---|---|",
    ]
    for session in summary["sessions"]:
        blocks = ", ".join(f"C{b['concurrency']}x{b['slots']}" for b in session["blocks"]) or "-"
        lines.append(
            f"| `{session['session_id']}` | {session['status']} | {session['git_dirty']} | {blocks} |"
        )
    lines += [
        "",
        "## Data",
        "",
        "| Concurrency | Condition | Traces | Families |",
        "|---|---|---|---|",
    ]
    for level, per_condition in summary["data"].items():
        for condition, info in per_condition.items():
            lines.append(f"| {level} | {condition} | {info['traces']} | {info['families']} |")
    lines += ["", f"Excluded traces: {summary['excluded'] or 'none'}.", ""]
    contrasts = summary["contrasts"]
    lines += [
        f"## Primary contrasts (C{contrasts['high']} vs C{contrasts['low']}; "
        f"{contrasts['model']}, {contrasts['representation']})",
        "",
        "Positive values mean more leakage under load. rho is normalised by D0 at the same "
        f"concurrency. Two-sided paired bootstrap p; Holm at alpha = {analysis.alpha} across "
        "the pre-declared family (tests with an undefined rho are reported, not counted).",
        "",
        "| Test | Low load | High load | Difference [95% CI] | p | p (Holm) | Reject H0 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, test in contrasts["tests"].items():
        lines.append(
            f"| {name} | {_num(test['low'])} | {_num(test['high'])} | {_ci(test)} | "
            f"{_num(test['p'])} | {_num(test['holm']['p_holm'])} | "
            f"{'yes' if test['holm']['reject'] else 'no'} |"
        )
    if figure:
        lines += ["", f"![Adaptive attacker balanced accuracy per condition and load]({figure})"]
    lines += [
        "",
        "## Leakage per load level",
        "",
        "| Concurrency | Condition | Attacker | Balanced accuracy | rho [95% CI] |",
        "|---|---|---|---|---|",
    ]
    for level, rows in summary["per_level"].items():
        for row in rows.values():
            lines.append(
                f"| {level} | {row['condition']} | {row['setting']} | "
                f"{fmt(row['balanced_accuracy'])} | {_ci(row['rho'])} |"
            )
    if summary["aggregation"]:
        lines += [
            "",
            "## Server-side aggregation vs gateway batching (exploratory, H5)",
            "",
            "Adaptive balanced accuracy difference (first minus second), paired.",
            "",
            "| Comparison | Difference [95% CI] | p |",
            "|---|---|---|",
        ]
        for name, item in summary["aggregation"].items():
            lines.append(f"| {name} | {_ci(item)} | {_num(item['p'])} |")
    lines += [
        "",
        "## Cost per load level",
        "",
        "| Concurrency | Condition | n | TTFT p50/p95/p99 ms | ITL p50/p95/p99 ms | Bytes / request | "
        "Bandwidth overhead % | Δ TTFT p50 ms | Dummy records | Timer lateness p99 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for level, per_condition in summary["costs"].items():
        for condition, cost in per_condition.items():

            def triple(name: str, cost: dict[str, Any] = cost) -> str:
                value = cost[name]
                return (
                    "n/a"
                    if value is None
                    else "/".join(f"{value[f'p{q}']:.1f}" for q in (50, 95, 99))
                )

            lateness = cost["timer_lateness_ms"]
            overhead = cost.get("bandwidth_overhead_pct")
            ttft = cost.get("ttft_p50_delta_ms")
            lines.append(
                f"| {level} | {condition} | {cost['n']} | {triple('ttft_ms')} | {triple('itl_ms')} | "
                f"{cost['wire_bytes_mean']:.0f} | {'-' if overhead is None else _ci(overhead, 1)} | "
                f"{'-' if ttft is None else _ci(ttft, 1)} | {cost['dummies_mean']:.1f} | "
                f"{'-' if lateness is None else f'{lateness["p99"]:.2f}'} |"
            )
    lines += [
        "",
        "## Leakage-cost frontier",
        "",
        "Written to `frontier/frontier.parquet`: one point per (concurrency, condition, attacker) "
        "with rho and its interval against bandwidth overhead, TTFT p95 and ITL p99.",
        "",
        "## Determinism probes per block",
        "",
        "| Session | Block | Concurrency | Probes | Records (median) | Duration ms (median) | TTFT ms (median) |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in summary["probes"]:
        lines.append(
            f"| `{row['session_id']}` | {row['cblock']} | {row['concurrency']} | {row['probes']} | "
            f"{row['resp_count_median']:.0f} | {row['duration_ms_median']:.1f} | "
            f"{row['ttft_ms_median']:.1f} |"
        )
    lines += [
        "",
        "## Reading this report",
        "",
        "The headline is whether an adaptive attacker's retained leakage changes between low "
        "and high load. Costs come from the same runs. A null difference with a tight interval "
        "is a result: the defense's effectiveness does not depend on load in this range.",
        "",
    ]
    return "\n".join(lines)


def run_e2(
    root: Path,
    config: ExperimentConfig,
    allow_unvalidated: bool = False,
    replace: bool = False,
) -> Path:
    analysis = config.analysis or AnalysisConfig()
    models: list[Model] = resolve(analysis.models)
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
    if not main.traces:
        raise ValidationError("No analysable traces")
    if {str(r) for r in main.column("regime")} != {REGIME}:
        raise ConfigError("E2 analyses the naturalistic regime only")
    base_interval = _base_interval(main)
    conditions_of = [condition_of(t, base_interval) for t in main.traces]
    conditions = sorted(set(conditions_of), key=lambda c: (c.split("/")[0], c))
    levels = sorted({int(str(c)) for c in main.column("concurrency")})
    if len(levels) < 2:
        raise ConfigError("E2 needs at least two concurrency levels")
    if BASELINE_ARM not in conditions:
        raise ConfigError("E2 needs the D0 arm at the base stream interval")
    missing = [
        (level, condition)
        for level in levels
        for condition in conditions
        if not any(
            c == condition and int(str(t.labels["concurrency"])) == level
            for t, c in zip(main.traces, conditions_of, strict=True)
        )
    ]
    if missing:
        raise ConfigError(f"Every condition must run at every level; missing {missing}")
    classes = sorted({str(c) for c in main.column("class")})
    chance = 1 / len(classes)
    families = [str(f) for f in main.column("family_id")]
    labels = [str(c) for c in main.column("class")]
    folds = family_folds(families, labels, analysis.outer_folds, analysis.seed)
    resamples = TwoStageResamples.draw(
        [t.session_id for t in main.traces], families, labels, analysis.bootstrap, analysis.seed
    )
    results = Results(config.name, analysis.bootstrap)
    costs = load_costs(root, session_ids)
    data: dict[str, Any] = {}
    cost_summary: dict[str, Any] = {}
    for level in levels:
        rows = np.asarray(
            [i for i, t in enumerate(main.traces) if int(str(t.labels["concurrency"])) == level],
            dtype=np.int64,
        )
        subset = TraceSet([main.traces[int(i)] for i in rows.tolist()])
        subset_conditions = [conditions_of[int(i)] for i in rows.tolist()]
        subset_families = [families[int(i)] for i in rows.tolist()]
        subset_labels = [labels[int(i)] for i in rows.tolist()]
        level_resamples = resamples.restrict(rows)
        data[str(level)] = {
            condition: {
                "traces": subset_conditions.count(condition),
                "families": len(
                    {
                        f
                        for f, c in zip(subset_families, subset_conditions, strict=True)
                        if c == condition
                    }
                ),
            }
            for condition in conditions
        }
        y = codes(subset_labels, classes)
        for rep in analysis.representations:
            x, _ = matrix(subset, rep)
            design = Design(
                x, y, subset_families, subset_labels, subset_conditions, folds, len(classes)
            )
            for condition in conditions:
                for setting in SETTINGS if condition != BASELINE_ARM else ("static",):
                    train = BASELINE_ARM if setting == "static" else condition
                    predictions = cross_validate(
                        design, models, analysis.seeds, analysis.inner_folds, train, condition
                    )
                    score(
                        results,
                        subset,
                        REGIME,
                        "t1",
                        rep,
                        predictions,
                        y,
                        classes,
                        level_resamples,
                        arm=condition,
                        setting=setting,
                        concurrency=level,
                    )
        cost_summary[str(level)] = summarize(
            costs, [t.request_id for t in subset.traces], subset_conditions, level_resamples
        )
    contrasts = _contrasts(results, conditions, levels, chance, analysis)
    per_level = _per_level(results, conditions, levels, chance, analysis)
    frontier: list[dict[str, object]] = []
    for level in levels:
        for row in per_level[str(level)].values():
            condition = row["condition"]
            if condition == BASELINE_ARM:
                continue
            cost: dict[str, Any] = cost_summary[str(level)].get(condition, {})
            overhead: dict[str, Any] = cost.get("bandwidth_overhead_pct") or {}
            ttft: dict[str, Any] = cost.get("ttft_ms") or {}
            itl: dict[str, Any] = cost.get("itl_ms") or {}
            frontier.append(
                {
                    "experiment_id": config.name,
                    "concurrency": level,
                    "condition": condition,
                    "setting": row["setting"],
                    "model": analysis.primary_model,
                    "representation": analysis.primary_representation,
                    "balanced_accuracy": float(str(row["balanced_accuracy"]["value"])),
                    "rho": row["rho"]["value"],
                    "rho_lo": row["rho"]["ci_lo"],
                    "rho_hi": row["rho"]["ci_hi"],
                    "bandwidth_overhead_pct": overhead.get("value", float("nan")),
                    "ttft_p95_ms": ttft.get("p95", float("nan")),
                    "itl_p99_ms": itl.get("p99", float("nan")),
                }
            )
    blocks_by_session = {str(m["session_id"]): m.get("blocks", []) for m in manifests}
    probes = TraceSet([t for t in traces.traces if t.labels["role"] == "determinism"])
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
                "blocks": blocks_by_session[str(m["session_id"])],
            }
            for m in manifests
        ],
        "levels": levels,
        "base_interval": base_interval,
        "conditions": conditions,
        "classes": classes,
        "data": data,
        "excluded": dict(Counter(e["reason"] for e in traces.excluded)),
        "contrasts": contrasts,
        "per_level": per_level,
        "aggregation": _aggregation(results, conditions, levels, analysis),
        "costs": cost_summary,
        "probes": _probe_drift(probes),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }
    with staged_output(root, config.name, replace) as staging:
        figure: str | None = None
        if len(levels) <= 6:
            figure = "figures/e2_adaptive_balanced_accuracy.png"
            points: list[tuple[str, str, float, float, float]] = []
            for level in levels:
                for condition in conditions:
                    setting = "static" if condition == BASELINE_ARM else "adaptive"
                    row = results.metric(
                        REGIME,
                        "t1",
                        analysis.primary_representation,
                        analysis.primary_model,
                        "balanced_accuracy",
                        arm=condition,
                        setting=setting,
                        concurrency=level,
                    )
                    value, lo, hi = (float(str(row[k])) for k in ("value", "ci_lo", "ci_hi"))
                    points.append((f"C{level}", condition, value, lo, hi))
            balanced_accuracy_by_level(points, chance, analysis.primary_model, staging / figure)
        write_rows(staging / "frontier" / "frontier.parquet", "frontier", frontier)
        write_results(staging, results, lambda: _report(results, analysis, figure))
    return target
