import numpy as np
import pytest

from crosstalk.stats.paired import bootstrap_p_value, delta, holm, retained_leakage


def test_holm_matches_hand_computation() -> None:
    # Sorted p: 0.01, 0.02, 0.03, 0.04 with m = 4 -> 0.04, 0.06, 0.06 (monotone), 0.06.
    result = holm({"a": 0.03, "b": 0.01, "c": 0.04, "d": 0.02}, alpha=0.05)
    assert result["b"]["p_holm"] == pytest.approx(0.04)
    assert result["d"]["p_holm"] == pytest.approx(0.06)
    assert result["a"]["p_holm"] == pytest.approx(0.06)
    assert result["c"]["p_holm"] == pytest.approx(0.06)
    assert result["b"]["reject"] is True
    # 0.02 > 0.05 / 3 stops the step-down: nothing after it is rejected.
    assert [result[k]["reject"] for k in "dac"] == [False, False, False]


def test_holm_handles_nan_and_caps_at_one() -> None:
    result = holm({"a": 0.5, "b": float("nan"), "c": 0.9})
    assert result["a"]["p_holm"] == 1.0 and result["c"]["p_holm"] == 1.0
    assert np.isnan(result["b"]["p_holm"]) and result["b"]["reject"] is False
    assert holm({}) == {}


def test_bootstrap_p_value() -> None:
    assert bootstrap_p_value(np.full(1000, 0.2)) == 0.0
    assert bootstrap_p_value(np.linspace(-1, 1, 1001)) == pytest.approx(1.0)
    shifted = np.random.default_rng(0).normal(0.1, 0.05, 4000)
    assert bootstrap_p_value(shifted) == pytest.approx(2 * np.mean(shifted <= 0), abs=1e-9)
    assert np.isnan(bootstrap_p_value(np.asarray([np.nan])))


def test_delta_is_paired() -> None:
    rng = np.random.default_rng(1)
    shared = rng.normal(0, 0.05, 2000)
    base = 0.6 + shared
    arm = 0.5 + shared + rng.normal(0, 0.005, 2000)
    paired = delta(0.5, 0.6, arm, base)
    assert paired["value"] == pytest.approx(-0.1)
    # Pairing removes the shared noise: the interval is far tighter than either arm's.
    assert paired["ci_hi"] - paired["ci_lo"] < 0.05
    assert paired["p"] == 0.0


def test_retained_leakage() -> None:
    base = np.random.default_rng(2).normal(0.7, 0.01, 1000)
    arm = base - 0.25
    rho = retained_leakage(0.45, 0.7, arm, base, chance=0.2)
    assert rho["value"] == pytest.approx(0.5)
    assert rho["ci_lo"] < 0.5 < rho["ci_hi"]
    at_chance = np.random.default_rng(3).normal(0.2, 0.02, 1000)
    undefined = retained_leakage(0.2, 0.2, at_chance, at_chance, chance=0.2)
    assert np.isnan(undefined["value"])
