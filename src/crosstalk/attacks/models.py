"""Attacker model ladder A0-A2 (IMPLEMENTATION_PLAN §15.2).

Every hyperparameter, scaler and early-stopping decision is fitted on training rows only:
the inner loop sees training families, and the test matrix is used once, for prediction.
"""

import warnings
from collections.abc import Callable, Sequence
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

from crosstalk.attacks.splits import IntArray, inner_folds
from crosstalk.common.errors import ConfigError

FloatArray = npt.NDArray[np.float64]
LR_GRID = (0.01, 0.1, 1.0, 10.0)
LGBM_LEAVES = (15, 63)
LGBM_MAX_ESTIMATORS = 2000
LGBM_EARLY_STOP = 50
EPSILON = float(np.finfo(np.float64).eps)  # matches scikit-learn's log_loss


class Model(Protocol):
    name: str
    seeded: bool

    def fit_predict(
        self,
        x_train: FloatArray,
        y_train: IntArray,
        families: Sequence[str],
        x_test: FloatArray,
        n_classes: int,
        seed: int,
        inner: int,
    ) -> FloatArray: ...


def log_loss(y: IntArray, proba: FloatArray) -> float:
    picked = proba[np.arange(y.size), y]
    return float(-np.mean(np.log(np.clip(picked, EPSILON, 1.0))))


def _aligned(proba: FloatArray, present: IntArray, n_classes: int) -> FloatArray:
    result = np.zeros((proba.shape[0], n_classes))
    result[:, present] = proba
    return result


class Chance:
    """A0 stratified: random one-hot draws from the training class distribution."""

    name = "chance"
    seeded = True

    def fit_predict(
        self,
        x_train: FloatArray,
        y_train: IntArray,
        families: Sequence[str],
        x_test: FloatArray,
        n_classes: int,
        seed: int,
        inner: int,
    ) -> FloatArray:
        prior = np.bincount(y_train, minlength=n_classes) / y_train.size
        cumulative = np.cumsum(prior)
        uniform = np.random.default_rng(seed).random(int(x_test.shape[0]))
        draws = np.minimum(np.searchsorted(cumulative, uniform, side="right"), n_classes - 1)
        return np.eye(n_classes)[draws]


class Majority:
    name = "majority"
    seeded = False

    def fit_predict(
        self,
        x_train: FloatArray,
        y_train: IntArray,
        families: Sequence[str],
        x_test: FloatArray,
        n_classes: int,
        seed: int,
        inner: int,
    ) -> FloatArray:
        counts = np.bincount(y_train, minlength=n_classes)
        return np.tile(np.eye(n_classes)[int(np.argmax(counts))], (x_test.shape[0], 1))


class Logistic:
    """A1: standardized logistic regression with C tuned on inner family folds."""

    name = "lr"
    seeded = False

    def _fit(self, x: FloatArray, y: IntArray, c: float, seed: int) -> "_Fitted":
        import sklearn.linear_model as linear
        import sklearn.pipeline as pipelines
        import sklearn.preprocessing as preprocessing

        # scikit-learn ships no type information; construct through explicit callables.
        make_pipeline = cast(Callable[..., object], vars(pipelines)["make_pipeline"])
        scaler = cast(Callable[[], object], vars(preprocessing)["StandardScaler"])
        logistic = cast(Callable[..., object], vars(linear)["LogisticRegression"])
        pipeline = make_pipeline(
            scaler(),
            logistic(C=c, class_weight="balanced", max_iter=5000, random_state=seed),
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            getattr(pipeline, "fit")(x, y)  # noqa: B009
        return _Fitted(pipeline)

    def fit_predict(
        self,
        x_train: FloatArray,
        y_train: IntArray,
        families: Sequence[str],
        x_test: FloatArray,
        n_classes: int,
        seed: int,
        inner: int,
    ) -> FloatArray:
        scores: dict[float, float] = {}
        for c in LR_GRID:
            losses: list[float] = []
            for training, validation in inner_folds(families, y_train, inner, seed):
                fitted = self._fit(x_train[training], y_train[training], c, seed)
                proba = fitted.predict(x_train[validation], n_classes)
                losses.append(log_loss(y_train[validation], proba))
            scores[c] = float(np.mean(losses))
        best = min(LR_GRID, key=lambda c: (scores[c], c))
        return self._fit(x_train, y_train, best, seed).predict(x_test, n_classes)


class _Fitted:
    def __init__(self, estimator: object) -> None:
        self.estimator = estimator

    def predict(self, x: FloatArray, n_classes: int) -> FloatArray:
        classes = np.asarray(getattr(self.estimator, "classes_"), dtype=np.int64)  # noqa: B009
        predict = getattr(self.estimator, "predict_proba")  # noqa: B009
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            proba = np.asarray(predict(x), dtype=np.float64)
        return _aligned(proba, classes, n_classes)


class LightGBM:
    """A2: gradient-boosted trees; leaves and rounds chosen by inner-fold early stopping."""

    name = "lgbm"
    seeded = True

    def _estimator(self, leaves: int, rounds: int, seed: int) -> object:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=rounds,
            learning_rate=0.05,
            num_leaves=leaves,
            min_child_samples=20,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            class_weight="balanced",
            random_state=seed,
            deterministic=True,
            force_row_wise=True,
            n_jobs=1,
            verbose=-1,
        )

    def fit_predict(
        self,
        x_train: FloatArray,
        y_train: IntArray,
        families: Sequence[str],
        x_test: FloatArray,
        n_classes: int,
        seed: int,
        inner: int,
    ) -> FloatArray:
        from lightgbm import early_stopping

        results: dict[int, tuple[float, int]] = {}
        for leaves in LGBM_LEAVES:
            losses: list[float] = []
            rounds: list[int] = []
            for training, validation in inner_folds(families, y_train, inner, seed):
                estimator = self._estimator(leaves, LGBM_MAX_ESTIMATORS, seed)
                fit = getattr(estimator, "fit")  # noqa: B009
                fit(
                    x_train[training],
                    y_train[training],
                    eval_X=(x_train[validation],),
                    eval_y=(y_train[validation],),
                    callbacks=[early_stopping(LGBM_EARLY_STOP, verbose=False)],
                )
                best = int(getattr(estimator, "best_iteration_", 0) or LGBM_MAX_ESTIMATORS)
                proba = _Fitted(estimator).predict(x_train[validation], n_classes)
                losses.append(log_loss(y_train[validation], proba))
                rounds.append(best)
            results[leaves] = (float(np.mean(losses)), max(1, round(float(np.mean(rounds)))))
        leaves = min(LGBM_LEAVES, key=lambda value: (results[value][0], value))
        final = self._estimator(leaves, results[leaves][1], seed)
        getattr(final, "fit")(x_train, y_train)  # noqa: B009
        return _Fitted(final).predict(x_test, n_classes)


MODELS: dict[str, Model] = {
    model.name: model for model in (Chance(), Majority(), Logistic(), LightGBM())
}


def resolve(names: Sequence[str]) -> list[Model]:
    unknown = [name for name in names if name not in MODELS]
    if unknown:
        raise ConfigError(f"Unknown attacker models: {unknown}")
    return [MODELS[name] for name in names]
