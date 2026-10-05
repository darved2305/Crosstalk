"""Weighted classification metrics.

A cluster-bootstrap resample is expressed as integer row weights (family multiplicities),
so each metric takes a weight vector instead of materialising resampled rows. Sort orders
do not depend on weights and are computed once per scorer.
"""

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]
EPSILON = float(np.finfo(np.float64).eps)  # matches scikit-learn's log_loss
RECALL_LEVELS = (0.05, 0.10, 0.25)


@dataclass(frozen=True, slots=True)
class RankedBinary:
    """Precomputed ranking for weighted average precision of one binary problem."""

    order: IntArray
    positive: BoolArray
    group_ends: IntArray

    @classmethod
    def build(cls, positive: BoolArray, score: FloatArray) -> "RankedBinary":
        order = np.argsort(-score, kind="stable")
        ranked = score[order]
        # Last index of each run of tied scores: thresholds are distinct score values.
        ends = np.flatnonzero(np.concatenate((ranked[1:] != ranked[:-1], [True])))
        return cls(order.astype(np.int64), positive[order], ends.astype(np.int64))

    def curve(self, weights: FloatArray) -> tuple[FloatArray, FloatArray]:
        w = weights[self.order]
        tp = np.cumsum(w * self.positive)[self.group_ends]
        fp = np.cumsum(w * ~self.positive)[self.group_ends]
        total = tp[-1] if tp.size else 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            precision = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
            recall = tp / total if total > 0 else np.zeros_like(tp)
        return precision, recall

    def average_precision(self, weights: FloatArray) -> float:
        precision, recall = self.curve(weights)
        if recall.size == 0 or recall[-1] == 0:
            return float("nan")
        return float(np.sum(np.diff(np.concatenate(([0.0], recall))) * precision))

    def at_base_rate(self, weights: FloatArray, prevalence: float) -> tuple[float, list[float]]:
        """PR-AUC and precision at fixed recall after reweighting to ``prevalence``."""
        positives = float(np.sum(weights * (self.positive_unsorted)))
        negatives = float(np.sum(weights)) - positives
        if positives <= 0 or negatives <= 0:
            return float("nan"), [float("nan")] * len(RECALL_LEVELS)
        scaled = weights * np.where(
            self.positive_unsorted, prevalence / positives, (1 - prevalence) / negatives
        )
        precision, recall = self.curve(scaled)
        auc = float(np.sum(np.diff(np.concatenate(([0.0], recall))) * precision))
        at_recall = [
            float(precision[recall >= level].max()) if np.any(recall >= level) else 0.0
            for level in RECALL_LEVELS
        ]
        return auc, at_recall

    @property
    def positive_unsorted(self) -> BoolArray:
        result = np.empty_like(self.positive)
        result[self.order] = self.positive
        return result


@dataclass(slots=True)
class Scorer:
    """All metrics of one prediction set (several seeds) under arbitrary row weights."""

    y: IntArray
    probas: list[FloatArray]
    n_classes: int
    sensitive: int | None = None
    base_rates: tuple[int, ...] = ()
    _predicted: list[IntArray] = field(default_factory=lambda: [])
    _ranked: list[list[RankedBinary]] = field(default_factory=lambda: [])
    _picked: list[FloatArray] = field(default_factory=lambda: [])

    def __post_init__(self) -> None:
        for proba in self.probas:
            self._predicted.append(np.argmax(proba, axis=1).astype(np.int64))
            self._ranked.append(
                [RankedBinary.build(self.y == k, proba[:, k]) for k in range(self.n_classes)]
            )
            picked = proba[np.arange(self.y.size), self.y]
            self._picked.append(-np.log(np.clip(picked, EPSILON, 1.0)))

    def metrics(self, weights: FloatArray) -> dict[str, float]:
        per_seed = [self._one(index, weights) for index in range(len(self.probas))]
        return {name: float(np.nanmean([m[name] for m in per_seed])) for name in per_seed[0]}

    def _one(self, index: int, weights: FloatArray) -> dict[str, float]:
        predicted = self._predicted[index]
        recalls: list[float] = []
        f1s: list[float] = []
        aps: list[float] = []
        for k in range(self.n_classes):
            actual = self.y == k
            support = float(np.sum(weights[actual]))
            if support <= 0:
                continue
            hit = float(np.sum(weights[actual & (predicted == k)]))
            claimed = float(np.sum(weights[predicted == k]))
            recall = hit / support
            precision = hit / claimed if claimed > 0 else 0.0
            recalls.append(recall)
            f1s.append(2 * precision * recall / (precision + recall) if hit > 0 else 0.0)
            aps.append(self._ranked[index][k].average_precision(weights))
        total = float(np.sum(weights))
        result = {
            "balanced_accuracy": float(np.mean(recalls)),
            "macro_f1": float(np.mean(f1s)),
            "macro_pr_auc": float(np.nanmean(aps)),
            "log_loss": float(np.sum(weights * self._picked[index]) / total),
        }
        if self.sensitive is not None:
            ranked = self._ranked[index][self.sensitive]
            for rate in self.base_rates:
                auc, at_recall = ranked.at_base_rate(weights, 1.0 / (rate + 1))
                result[f"pr_auc_1to{rate}"] = auc
                for level, value in zip(RECALL_LEVELS, at_recall, strict=True):
                    result[f"precision_at_recall{level:.2f}_1to{rate}"] = value
        return result
