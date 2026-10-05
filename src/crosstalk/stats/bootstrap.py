"""Paired, class-stratified cluster bootstrap over prompt families (§16)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from crosstalk.attacks.splits import family_classes

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class ClusterResamples:
    """B resamples of families (with replacement, within each class), as row weights.

    Every metric of every (arm, setting, model) is computed on the same resamples, which
    makes differences between them paired.
    """

    counts: IntArray
    family_of_row: IntArray

    @classmethod
    def draw(
        cls, families: Sequence[str], classes: Sequence[str], b: int, seed: int
    ) -> "ClusterResamples":
        mapping = family_classes(families, classes)
        names = sorted(mapping)
        index = {name: position for position, name in enumerate(names)}
        rng = np.random.default_rng(seed)
        counts = np.zeros((b, len(names)), dtype=np.int64)
        for klass in sorted(set(mapping.values())):
            members = np.asarray([index[f] for f in names if mapping[f] == klass], dtype=np.int64)
            draws = rng.integers(0, members.size, size=(b, members.size))
            for column in range(members.size):
                np.add.at(counts, (np.arange(b), members[draws[:, column]]), 1)
        rows = np.asarray([index[f] for f in families], dtype=np.int64)
        return cls(counts, rows)

    @property
    def b(self) -> int:
        return int(self.counts.shape[0])

    def weights(self, replicate: int) -> FloatArray:
        return self.counts[replicate, self.family_of_row].astype(np.float64)

    def restrict(self, rows: IntArray) -> "ClusterResamples":
        return ClusterResamples(self.counts, self.family_of_row[rows])


def percentile_ci(values: FloatArray, level: float = 0.95) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan"), float("nan")
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(finite, [tail, 100 - tail])
    return float(low), float(high)


def bootstrap_metrics(
    compute: Callable[[FloatArray], dict[str, float]],
    n_rows: int,
    resamples: ClusterResamples,
) -> dict[str, tuple[float, float, float, FloatArray]]:
    """Point estimate, CI and replicate values for every metric ``compute`` returns."""
    point = compute(np.ones(n_rows))
    replicates = {name: np.empty(resamples.b) for name in point}
    for replicate in range(resamples.b):
        values = compute(resamples.weights(replicate))
        for name in point:
            replicates[name][replicate] = values[name]
    return {
        name: (point[name], *percentile_ci(replicates[name]), replicates[name]) for name in point
    }
