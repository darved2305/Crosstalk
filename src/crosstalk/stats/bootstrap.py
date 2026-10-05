"""Paired, class-stratified cluster bootstrap over prompt families (§16)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from crosstalk.attacks.splits import family_classes

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


class Resamples(Protocol):
    """B bootstrap replicates expressed as row weights over a fixed set of rows."""

    @property
    def b(self) -> int: ...

    def weights(self, replicate: int) -> FloatArray: ...

    def restrict(self, rows: IntArray) -> "Resamples": ...


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


@dataclass(frozen=True, slots=True)
class TwoStageResamples:
    """Sessions resampled with replacement, then families within class inside each draw.

    Used when replicate sessions were collected on different days (E2): session-to-session
    variation then enters every interval. One session reduces to the family bootstrap.
    """

    session_draws: IntArray  # (B, n_sessions): which session each draw picked
    counts: IntArray  # (B, n_sessions, n_families): family multiplicity per draw
    session_of_row: IntArray
    family_of_row: IntArray

    @classmethod
    def draw(
        cls,
        sessions: Sequence[str],
        families: Sequence[str],
        classes: Sequence[str],
        b: int,
        seed: int,
    ) -> "TwoStageResamples":
        family_classes(families, classes)
        session_names = sorted(set(sessions))
        family_names = sorted(set(families))
        session_index = {name: i for i, name in enumerate(session_names)}
        family_index = {name: i for i, name in enumerate(family_names)}
        session_of_row = np.asarray([session_index[s] for s in sessions], dtype=np.int64)
        family_of_row = np.asarray([family_index[f] for f in families], dtype=np.int64)
        rng = np.random.default_rng(seed)
        n_sessions = len(session_names)
        draws = rng.integers(0, n_sessions, size=(b, n_sessions))
        counts = np.zeros((b, n_sessions, len(family_names)), dtype=np.int64)
        # Families present in each session, grouped by class.
        members: list[list[IntArray]] = []
        for position in range(n_sessions):
            in_session = sorted(
                {
                    (classes[i], family_index[families[i]])
                    for i in np.flatnonzero(session_of_row == position).tolist()
                }
            )
            by_class: dict[str, list[int]] = {}
            for klass, family in in_session:
                by_class.setdefault(klass, []).append(family)
            members.append([np.asarray(v, dtype=np.int64) for _, v in sorted(by_class.items())])
        for replicate in range(b):
            for occurrence in range(n_sessions):
                for group in members[int(draws[replicate, occurrence])]:
                    picks = group[rng.integers(0, group.size, size=group.size)]
                    np.add.at(counts[replicate, occurrence], picks, 1)
        return cls(draws.astype(np.int64), counts, session_of_row, family_of_row)

    @property
    def b(self) -> int:
        return int(self.session_draws.shape[0])

    def weights(self, replicate: int) -> FloatArray:
        result = np.zeros(self.family_of_row.size)
        for occurrence in range(self.session_draws.shape[1]):
            session = self.session_draws[replicate, occurrence]
            mask = self.session_of_row == session
            result[mask] += self.counts[replicate, occurrence, self.family_of_row[mask]]
        return result

    def restrict(self, rows: IntArray) -> "TwoStageResamples":
        return TwoStageResamples(
            self.session_draws, self.counts, self.session_of_row[rows], self.family_of_row[rows]
        )


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
    resamples: Resamples,
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
