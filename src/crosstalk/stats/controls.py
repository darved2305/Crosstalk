"""Null control, noise floor and power check (§15.4, §16)."""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from crosstalk.attacks.splits import family_classes
from crosstalk.stats.bootstrap import percentile_ci

FloatArray = npt.NDArray[np.float64]


def permute_family_classes(families: Sequence[str], classes: Sequence[str], seed: int) -> list[str]:
    """Shuffle the family → class mapping, preserving how many families each class has."""
    mapping = family_classes(families, classes)
    names = sorted(mapping)
    shuffled = np.random.default_rng(seed).permutation([mapping[name] for name in names])
    permuted = dict(zip(names, (str(v) for v in shuffled.tolist()), strict=True))
    return [permuted[family] for family in families]


def permutation_p_value(observed: float, null: Sequence[float]) -> float:
    return (1 + sum(value >= observed for value in null)) / (1 + len(null))


def icc(values: FloatArray, groups: Sequence[str]) -> dict[str, float]:
    """One-way random-effects ICC(1) with unequal group sizes."""
    labels = sorted(set(groups))
    members = [values[[g == label for g in groups]] for label in labels]
    members = [m for m in members if m.size >= 2]
    if len(members) < 2:
        return {"icc": float("nan"), "within_var": float("nan"), "between_var": float("nan")}
    sizes = np.asarray([m.size for m in members], dtype=np.float64)
    means = np.asarray([m.mean() for m in members])
    grand = float(np.concatenate(members).mean())
    n, k = float(sizes.sum()), len(members)
    msb = float(np.sum(sizes * (means - grand) ** 2) / (k - 1))
    msw = float(sum(float(np.sum((m - m.mean()) ** 2)) for m in members) / (n - k))
    k0 = (n - float(np.sum(sizes**2)) / n) / (k - 1)
    between = max((msb - msw) / k0, 0.0)
    return {
        "icc": float((msb - msw) / (msb + (k0 - 1) * msw)) if msb + (k0 - 1) * msw > 0 else 0.0,
        "within_var": msw,
        "between_var": between,
    }


def drift(
    values: FloatArray, groups: Sequence[str], hours: FloatArray, b: int, seed: int
) -> dict[str, float]:
    """Pooled within-prompt slope (units per hour) with a prompt-cluster bootstrap CI."""
    labels = sorted(set(groups))
    index = {label: position for position, label in enumerate(labels)}
    group = np.asarray([index[g] for g in groups], dtype=np.int64)
    centered_values = values.copy()
    centered_hours = hours.copy()
    for position in range(len(labels)):
        mask = group == position
        centered_values[mask] -= values[mask].mean()
        centered_hours[mask] -= hours[mask].mean()

    def slope(weights: FloatArray) -> float:
        denominator = float(np.sum(weights * centered_hours**2))
        if denominator <= 0:
            return float("nan")
        return float(np.sum(weights * centered_hours * centered_values) / denominator)

    point = slope(np.ones(values.size))
    rng = np.random.default_rng(seed)
    replicates = np.empty(b)
    for replicate in range(b):
        counts = np.bincount(rng.integers(0, len(labels), len(labels)), minlength=len(labels))
        replicates[replicate] = slope(counts[group].astype(np.float64))
    low, high = percentile_ci(replicates)
    return {"slope_per_hour": point, "ci_lo": low, "ci_hi": high}


def power_check(
    replicates: FloatArray,
    pilot_per_class: int,
    planned_per_class: int,
    max_per_class: int,
    target_half_width: float,
) -> dict[str, float | int | bool]:
    """Project the 95% half-width of a paired Δ from a pilot's bootstrap SE.

    Conservative: a paired difference of two arms with independent errors has
    SE_Δ = √2·SE; positive within-family correlation in the real paired design shrinks it.
    """
    se = float(np.nanstd(replicates, ddof=1))

    def half_width(per_class: int) -> float:
        return 1.96 * np.sqrt(2.0) * se * np.sqrt(pilot_per_class / per_class)

    recommended = next(
        (n for n in range(1, max_per_class + 1) if half_width(n) <= target_half_width),
        None,
    )
    return {
        "pilot_per_class": pilot_per_class,
        "bootstrap_se": se,
        "planned_per_class": planned_per_class,
        "planned_half_width": float(half_width(planned_per_class)),
        "recommended_per_class": recommended if recommended is not None else max_per_class,
        "meets_target_at_max": recommended is not None,
        "half_width_at_max": float(half_width(max_per_class)),
        "target_half_width": target_half_width,
    }
