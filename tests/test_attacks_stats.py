from collections import Counter
from collections.abc import Sequence

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crosstalk.attacks.models import MODELS, FloatArray, resolve
from crosstalk.attacks.protocols import Design, cross_validate
from crosstalk.attacks.splits import IntArray, family_folds, inner_folds, subsample_families
from crosstalk.common.errors import ConfigError
from crosstalk.stats.bootstrap import ClusterResamples, bootstrap_metrics, percentile_ci
from crosstalk.stats.controls import (
    drift,
    icc,
    permutation_p_value,
    permute_family_classes,
    power_check,
)
from crosstalk.stats.metrics import RankedBinary, Scorer


def _table(classes: int, families: int, per_family: int) -> tuple[list[str], list[str]]:
    family_names: list[str] = []
    labels: list[str] = []
    for klass in range(classes):
        for family in range(families):
            for _ in range(per_family):
                family_names.append(f"c{klass}-f{family}")
                labels.append(f"c{klass}")
    return family_names, labels


def _design(
    x: FloatArray,
    labels: list[str],
    families: list[str],
    arms: list[str] | None = None,
    seed: int = 0,
) -> Design:
    classes = sorted(set(labels))
    y = np.asarray([classes.index(c) for c in labels], dtype=np.int64)
    return Design(
        x,
        y,
        families,
        labels,
        arms or ["a0"] * len(labels),
        family_folds(families, labels, 5, seed),
        len(classes),
    )


@settings(max_examples=60, deadline=None)
@given(
    classes=st.integers(2, 6),
    families=st.integers(5, 12),
    per_family=st.integers(1, 4),
    seed=st.integers(0, 1000),
)
def test_family_folds_are_disjoint_and_balanced(
    classes: int, families: int, per_family: int, seed: int
) -> None:
    family_names, labels = _table(classes, families, per_family)
    folds = family_folds(family_names, labels, 5, seed)
    assert set(folds) == set(family_names)
    assert set(folds.values()) == set(range(5))
    for klass in range(classes):
        sizes = Counter(fold for family, fold in folds.items() if family.startswith(f"c{klass}-"))
        assert max(sizes.values()) - min(sizes.values()) <= 1
    labels_array = np.asarray([int(c[1:]) for c in labels], dtype=np.int64)
    for training, validation in inner_folds(family_names, labels_array, 3, seed):
        assert not {family_names[i] for i in training} & {family_names[i] for i in validation}
        assert set(labels_array[training].tolist()) == set(range(classes))


def test_family_folds_reject_too_few_families_and_mixed_labels() -> None:
    families, labels = _table(2, 4, 2)
    with pytest.raises(ConfigError, match="families"):
        family_folds(families, labels, 5, 0)
    with pytest.raises(ConfigError, match="more than one class"):
        family_folds(["f1", "f1"], ["a", "b"], 2, 0)


class _Spy:
    """Records the training matrices it sees; column 0 carries a unique row id."""

    name = "spy"
    seeded = False

    def __init__(self) -> None:
        self.seen: list[tuple[set[int], set[int]]] = []

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
        self.seen.append(({int(v) for v in x_train[:, 0]}, {int(v) for v in x_test[:, 0]}))
        return np.full((x_test.shape[0], n_classes), 1 / n_classes)


def test_training_never_sees_test_rows_static_and_adaptive() -> None:
    families, labels = _table(3, 6, 2)
    n = len(labels)
    arms = ["a0" if index % 2 == 0 else "a3" for index in range(n)]
    x = np.column_stack([np.arange(n, dtype=np.float64), np.zeros(n)])
    design = _design(x, labels, families, arms)
    for train_arm in ("a0", "a3"):
        spy = _Spy()
        predictions = cross_validate(design, [spy], 1, 3, train_arm, "a3")
        assert sorted(p.row for p in predictions) == [i for i in range(n) if arms[i] == "a3"]
        for train_rows, test_rows in spy.seen:
            assert not train_rows & test_rows
            assert {arms[i] for i in train_rows} == {train_arm}
            assert {arms[i] for i in test_rows} == {"a3"}
            assert not {families[i] for i in train_rows} & {families[i] for i in test_rows}


def test_learning_curve_only_drops_training_families() -> None:
    families, labels = _table(3, 10, 2)
    x = np.column_stack([np.arange(len(labels), dtype=np.float64)])
    spy = _Spy()
    full = cross_validate(_design(x, labels, families), [spy], 1, 3, "a0", "a0")
    full_train = [len(train) for train, _ in spy.seen]
    spy.seen.clear()
    half = cross_validate(_design(x, labels, families), [spy], 1, 3, "a0", "a0", fraction=0.5)
    assert sorted(p.row for p in half) == sorted(p.row for p in full)
    assert all(len(t) < f for (t, _), f in zip(spy.seen, full_train, strict=True))
    kept = subsample_families(families, labels, 0.5, 1)
    assert Counter(f.split("-")[0] for f in kept) == {"c0": 5, "c1": 5, "c2": 5}


def _injected(
    signal: float, seed: int, classes: int = 4, families: int = 10, per_family: int = 4
) -> Design:
    family_names, labels = _table(classes, families, per_family)
    rng = np.random.default_rng(seed)
    y = np.asarray([int(c[1:]) for c in labels])
    family_effect = {f: rng.normal(0, 0.3, 6) for f in set(family_names)}
    x = rng.normal(0, 1, (len(labels), 6))
    x += np.asarray([family_effect[f] for f in family_names])
    x[:, 0] += signal * y
    x[:, 1] -= signal * (y % 2)
    return _design(x, labels, family_names, seed=seed)


def _balanced_accuracy(design: Design, model: str, seeds: int = 1) -> float:
    predictions = cross_validate(design, resolve([model]), seeds, 3, "a0", "a0")
    proba = np.zeros((design.y.size, design.n_classes))
    for prediction in predictions:
        proba[prediction.row] += prediction.proba
    return Scorer(design.y, [proba], design.n_classes).metrics(np.ones(design.y.size))[
        "balanced_accuracy"
    ]


@pytest.mark.parametrize("model", ["lr", "lgbm"])
def test_injected_signal_is_recovered(model: str) -> None:
    assert _balanced_accuracy(_injected(3.0, 0), model) > 0.85


@pytest.mark.parametrize("model", ["lr", "lgbm", "chance", "majority"])
def test_no_signal_stays_near_chance(model: str) -> None:
    values = [_balanced_accuracy(_injected(0.0, seed), model) for seed in range(3)]
    assert abs(float(np.mean(values)) - 0.25) < 0.1


def test_model_registry() -> None:
    assert set(MODELS) == {"chance", "majority", "lr", "lgbm"}
    with pytest.raises(ConfigError):
        resolve(["svm"])


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(0, 10_000), n=st.integers(8, 60), k=st.integers(2, 5))
def test_weighted_metrics_match_sklearn(seed: int, n: int, k: int) -> None:
    from sklearn.metrics import (
        average_precision_score,
        balanced_accuracy_score,
        f1_score,
        log_loss,
    )

    rng = np.random.default_rng(seed)
    y = np.concatenate([np.arange(k), rng.integers(0, k, n - k)]).astype(np.int64)
    proba = rng.dirichlet(np.ones(k), n)
    proba[:, 0] = np.round(proba[:, 0], 1)
    proba /= proba.sum(axis=1, keepdims=True)
    weights = rng.integers(0, 3, n).astype(np.float64)
    weights[:k] = 1
    rows = np.repeat(np.arange(n), weights.astype(int))
    scorer = Scorer(y, [proba], k)
    ours = scorer.metrics(weights)
    yr, pr = y[rows], proba[rows]
    predicted = pr.argmax(axis=1)
    assert ours["balanced_accuracy"] == pytest.approx(balanced_accuracy_score(yr, predicted))
    assert ours["macro_f1"] == pytest.approx(
        f1_score(yr, predicted, average="macro", labels=sorted(set(yr.tolist())), zero_division=0)
    )
    aps = [average_precision_score(yr == c, pr[:, c]) for c in sorted(set(yr.tolist()))]
    assert ours["macro_pr_auc"] == pytest.approx(float(np.mean(aps)))
    assert ours["log_loss"] == pytest.approx(log_loss(yr, pr, labels=list(range(k))))


def test_base_rate_reweighting() -> None:
    rng = np.random.default_rng(3)
    positive = rng.random(400) < 0.3
    score = rng.random(400) + positive * 0.5
    ranked = RankedBinary.build(positive, score)
    weights = np.ones(400)
    prevalence = float(positive.mean())
    auc, at_recall = ranked.at_base_rate(weights, prevalence)
    assert auc == pytest.approx(ranked.average_precision(weights))
    rare, rare_at_recall = ranked.at_base_rate(weights, 1 / 1001)
    assert rare < auc
    assert all(r <= a for r, a in zip(rare_at_recall, at_recall, strict=True))
    no_positives = ranked.at_base_rate(np.where(positive, 0.0, 1.0), 0.1)
    assert np.isnan(no_positives[0])


def test_cluster_bootstrap_shape_and_reproducibility() -> None:
    families, labels = _table(3, 7, 3)
    first = ClusterResamples.draw(families, labels, 50, 9)
    second = ClusterResamples.draw(families, labels, 50, 9)
    assert np.array_equal(first.counts, second.counts)
    per_class = first.counts.reshape(50, 3, 7).sum(axis=2)
    assert np.all(per_class == 7)
    weights = first.weights(0)
    assert weights.sum() == 63
    for family in set(families):
        rows = [i for i, f in enumerate(families) if f == family]
        assert len({weights[i] for i in rows}) == 1


def test_cluster_bootstrap_standard_error() -> None:
    families, labels = _table(1, 200, 5)
    rng = np.random.default_rng(1)
    family_value = {f: rng.normal(0, 1) for f in set(families)}
    values = np.asarray([family_value[f] + rng.normal(0, 0.1) for f in families])
    resamples = ClusterResamples.draw(families, labels, 2000, 2)
    result = bootstrap_metrics(
        lambda w: {"mean": float(np.sum(w * values) / np.sum(w))}, values.size, resamples
    )["mean"]
    se = float(np.std(result[3]))
    family_means = np.asarray(
        [values[[f == g for f in families]].mean() for g in sorted(set(families))]
    )
    expected = float(family_means.std(ddof=1) / np.sqrt(200))
    assert se == pytest.approx(expected, rel=0.15)
    assert result[1] < result[0] < result[2]


def test_percentile_ci_ignores_nan() -> None:
    assert percentile_ci(np.asarray([np.nan, np.nan])) != percentile_ci(np.arange(100.0))
    low, high = percentile_ci(np.arange(101.0))
    assert (low, high) == pytest.approx((2.5, 97.5))


def test_null_permutation_preserves_class_sizes() -> None:
    families, labels = _table(4, 8, 2)
    permuted = permute_family_classes(families, labels, 5)
    by_family = dict(zip(families, permuted, strict=True))
    assert Counter(by_family.values()) == Counter(dict(zip(families, labels, strict=True)).values())
    assert permuted != labels
    for family in set(families):
        assert len({p for f, p in zip(families, permuted, strict=True) if f == family}) == 1
    assert permutation_p_value(0.9, [0.2, 0.3]) == pytest.approx(1 / 3)
    assert permutation_p_value(0.1, [0.2, 0.3]) == 1.0


def test_icc_and_drift() -> None:
    groups = [f"p{i // 5}" for i in range(50)]
    repeated = np.asarray([float(i // 5) for i in range(50)])
    assert icc(repeated, groups)["icc"] == pytest.approx(1.0)
    noise = np.random.default_rng(0).normal(0, 1, 50)
    assert abs(icc(noise, groups)["icc"]) < 0.3
    hours = np.tile(np.linspace(0, 2, 5), 10)
    trend = repeated + 0.5 * hours + np.random.default_rng(1).normal(0, 0.01, 50)
    result = drift(trend, groups, hours, 300, 0)
    assert result["slope_per_hour"] == pytest.approx(0.5, abs=0.02)
    assert result["ci_lo"] < 0.5 < result["ci_hi"]
    assert np.isnan(icc(np.ones(2), ["a", "b"])["icc"])


def test_power_check() -> None:
    replicates = np.random.default_rng(0).normal(0.5, 0.02, 2000)
    result = power_check(replicates, 60, 200, 300, 0.05)
    assert result["meets_target_at_max"]
    # 1.96 * sqrt(2) * 0.02 * sqrt(60 / n) <= 0.05  =>  n >= 73.7
    assert result["recommended_per_class"] == 74
    assert float(result["planned_half_width"]) < float(result["half_width_at_max"]) * 1.3
    wide = power_check(np.random.default_rng(0).normal(0.5, 0.2, 2000), 60, 200, 300, 0.05)
    assert not wide["meets_target_at_max"]
    assert wide["recommended_per_class"] == 300


def test_two_stage_reduces_to_family_bootstrap_with_one_session() -> None:
    from crosstalk.stats.bootstrap import TwoStageResamples

    families, labels = _table(3, 6, 2)
    two = TwoStageResamples.draw(["s1"] * len(families), families, labels, 300, 4)
    assert two.b == 300
    for replicate in range(20):
        weights = two.weights(replicate)
        assert weights.sum() == len(families)
        per_class = Counter()
        for label, w in zip(labels, weights, strict=True):
            per_class[label] += w
        assert set(per_class.values()) == {12}


def test_two_stage_carries_session_variation() -> None:
    from crosstalk.stats.bootstrap import ClusterResamples, TwoStageResamples

    families, labels = _table(2, 20, 1)
    sessions = ["s1"] * len(families) + ["s2"] * len(families)
    families = families + families
    labels = labels + labels
    # A large day effect: session 2 sits 1.0 higher than session 1.
    rng = np.random.default_rng(0)
    values = np.asarray([0.0] * 40 + [1.0] * 40) + rng.normal(0, 0.05, 80)

    def mean(w: np.ndarray) -> dict[str, float]:
        return {"m": float(np.sum(w * values) / np.sum(w))}

    two = TwoStageResamples.draw(sessions, families, labels, 1000, 1)
    # Family-only bootstrap treating each session's families as distinct clusters.
    keyed_families = [f"{s}:{f}" for s, f in zip(sessions, families, strict=True)]
    keyed_classes = [f"{s}:{label}" for s, label in zip(sessions, labels, strict=True)]
    one = ClusterResamples.draw(
        keyed_families,
        keyed_classes,
        1000,
        1,
    )
    se_two = float(np.std(bootstrap_metrics(mean, 80, two)["m"][3]))
    se_one = float(np.std(bootstrap_metrics(mean, 80, one)["m"][3]))
    assert se_two > 5 * se_one
    # Restricting rows keeps replicates aligned (paired) with the full set.
    rows = np.arange(0, 80, 2, dtype=np.int64)
    sub = two.restrict(rows)
    assert np.array_equal(sub.weights(3), two.weights(3)[rows])


def test_two_stage_rejects_inconsistent_labels() -> None:
    from crosstalk.stats.bootstrap import TwoStageResamples

    with pytest.raises(ConfigError):
        TwoStageResamples.draw(["s", "s"], ["f", "f"], ["a", "b"], 10, 0)
