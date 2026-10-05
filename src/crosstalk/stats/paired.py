"""Paired comparisons on shared cluster resamples (IMPLEMENTATION_PLAN §16).

Every input is a vector of bootstrap replicates computed on the *same* family resamples,
so differences are paired. Non-overlap of separate intervals is never used as a test.
"""

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from crosstalk.stats.bootstrap import percentile_ci

FloatArray = npt.NDArray[np.float64]


def bootstrap_p_value(replicates: FloatArray) -> float:
    """Two-sided p for H0: Δ = 0 from the bootstrap distribution of Δ."""
    finite = replicates[np.isfinite(replicates)]
    if finite.size == 0:
        return float("nan")
    below = float(np.mean(finite <= 0))
    above = float(np.mean(finite >= 0))
    return min(1.0, 2 * min(below, above))


def delta(
    point: float, baseline: float, replicates: FloatArray, baseline_replicates: FloatArray
) -> dict[str, float]:
    differences = replicates - baseline_replicates
    low, high = percentile_ci(differences)
    return {
        "value": point - baseline,
        "ci_lo": low,
        "ci_hi": high,
        "p": bootstrap_p_value(differences),
    }


def retained_leakage(
    point: float,
    baseline: float,
    replicates: FloatArray,
    baseline_replicates: FloatArray,
    chance: float,
) -> dict[str, float]:
    """rho = (m - chance) / (m_D0 - chance); undefined when D0 is not above chance."""
    base_low, _ = percentile_ci(baseline_replicates)
    if not base_low > chance:
        return {"value": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = (replicates - chance) / (baseline_replicates - chance)
    ratios[~np.isfinite(ratios)] = np.nan
    low, high = percentile_ci(ratios)
    return {"value": (point - chance) / (baseline - chance), "ci_lo": low, "ci_hi": high}


def holm(p_values: Mapping[str, float], alpha: float = 0.05) -> dict[str, dict[str, float | bool]]:
    """Holm-Bonferroni step-down adjustment within one family of tests."""
    finite = sorted(
        ((name, value) for name, value in p_values.items() if np.isfinite(value)),
        key=lambda item: item[1],
    )
    m = len(finite)
    result: dict[str, dict[str, float | bool]] = {}
    running = 0.0
    still_rejecting = True
    for rank, (name, value) in enumerate(finite):
        adjusted = min(1.0, max(running, (m - rank) * value))
        running = adjusted
        reject = still_rejecting and value <= alpha / (m - rank)
        still_rejecting = reject
        result[name] = {"p": value, "p_holm": adjusted, "reject": reject}
    for name, value in p_values.items():
        if name not in result:
            result[name] = {"p": value, "p_holm": float("nan"), "reject": False}
    return result
