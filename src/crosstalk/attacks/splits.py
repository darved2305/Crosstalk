"""Prompt-family-disjoint splits (IMPLEMENTATION_PLAN §15.3)."""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from crosstalk.common.errors import ConfigError

IntArray = npt.NDArray[np.int64]


def family_classes(families: Sequence[str], classes: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for family, klass in zip(families, classes, strict=True):
        if mapping.setdefault(family, klass) != klass:
            raise ConfigError(f"Family {family} carries more than one class label")
    return mapping


def family_folds(
    families: Sequence[str], classes: Sequence[str], n_splits: int, seed: int
) -> dict[str, int]:
    """Assign every family to one outer fold, stratified by class.

    Computed once per experiment and reused for every arm and setting, which makes all
    comparisons paired.
    """
    mapping = family_classes(families, classes)
    rng = np.random.default_rng(seed)
    folds: dict[str, int] = {}
    by_class: dict[str, list[str]] = {}
    for family, klass in sorted(mapping.items()):
        by_class.setdefault(klass, []).append(family)
    offset = 0
    for klass in sorted(by_class):
        members = by_class[klass]
        if len(members) < n_splits:
            raise ConfigError(
                f"Class {klass} has {len(members)} families; {n_splits} folds need at least as many"
            )
        order = rng.permutation(len(members))
        for rank, index in enumerate(order):
            # Rotating the start keeps fold sizes balanced across classes.
            folds[members[int(index)]] = (rank + offset) % n_splits
        offset += len(members)
    return folds


def inner_folds(
    families: Sequence[str], labels: IntArray, n_splits: int, seed: int
) -> list[tuple[IntArray, IntArray]]:
    """Family-grouped, class-stratified folds over training rows only."""
    unique = sorted(set(families))
    family_label = family_classes(families, [str(v) for v in labels.tolist()])
    assignment = family_folds(unique, [family_label[f] for f in unique], n_splits, seed)
    fold_of_row = np.asarray([assignment[f] for f in families], dtype=np.int64)
    result: list[tuple[IntArray, IntArray]] = []
    for fold in range(n_splits):
        validation = np.flatnonzero(fold_of_row == fold)
        training = np.flatnonzero(fold_of_row != fold)
        result.append((training, validation))
    return result


def subsample_families(
    families: Sequence[str], classes: Sequence[str], fraction: float, seed: int
) -> set[str]:
    """Class-stratified subset of families for learning curves."""
    mapping = family_classes(families, classes)
    rng = np.random.default_rng(seed)
    kept: set[str] = set()
    by_class: dict[str, list[str]] = {}
    for family, klass in sorted(mapping.items()):
        by_class.setdefault(klass, []).append(family)
    for members in by_class.values():
        count = max(1, round(fraction * len(members)))
        kept.update(members[int(i)] for i in rng.permutation(len(members))[:count])
    return kept
