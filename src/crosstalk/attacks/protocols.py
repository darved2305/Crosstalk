"""Static, adaptive and learning-curve protocols on shared family folds (§15.3)."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from crosstalk.attacks.models import FloatArray, Model
from crosstalk.attacks.splits import IntArray, subsample_families
from crosstalk.common.errors import ValidationError

BoolArray = npt.NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class Prediction:
    row: int
    fold: int
    model: str
    seed: int
    proba: FloatArray


@dataclass(frozen=True, slots=True)
class Design:
    """Rows of one feature matrix with labels, families and the experiment's fold map."""

    x: FloatArray
    y: IntArray
    families: Sequence[str]
    classes: Sequence[str]
    arms: Sequence[str]
    folds: dict[str, int]
    n_classes: int

    @property
    def fold_of_row(self) -> IntArray:
        return np.asarray([self.folds[f] for f in self.families], dtype=np.int64)


def cross_validate(
    design: Design,
    models: Sequence[Model],
    seeds: int,
    inner: int,
    train_arm: str,
    test_arm: str,
    fraction: float = 1.0,
    fraction_seed: int = 0,
) -> list[Prediction]:
    """Out-of-fold predictions for one (train arm, test arm) setting.

    Static: train on a0, test on arm k. Adaptive: train and test on arm k. Training rows
    never share a family with the fold's test rows.
    """
    arms = np.asarray(design.arms)
    fold_of_row = design.fold_of_row
    predictions: list[Prediction] = []
    for fold in sorted(set(design.folds.values())):
        train: BoolArray = (arms == train_arm) & (fold_of_row != fold)
        test: BoolArray = (arms == test_arm) & (fold_of_row == fold)
        if fraction < 1.0:
            rows = [int(i) for i in np.flatnonzero(train).tolist()]
            kept = subsample_families(
                [design.families[i] for i in rows],
                [design.classes[i] for i in rows],
                fraction,
                fraction_seed + fold,
            )
            train = train & np.asarray([f in kept for f in design.families], dtype=np.bool_)
        train_index = np.flatnonzero(train)
        test_index = np.flatnonzero(test)
        if test_index.size == 0:
            continue
        families = [design.families[int(i)] for i in train_index.tolist()]
        if set(families) & {design.families[int(i)] for i in test_index.tolist()}:
            raise ValidationError("Training and test rows share a prompt family")
        for model in models:
            for seed in range(seeds if model.seeded else 1):
                proba = model.fit_predict(
                    design.x[train_index],
                    design.y[train_index],
                    families,
                    design.x[test_index],
                    design.n_classes,
                    seed,
                    inner,
                )
                for position, row in enumerate(test_index.tolist()):
                    predictions.append(
                        Prediction(int(row), fold, model.name, seed, proba[position])
                    )
    return predictions
