"""India fear & greed (§2.2) pure scoring functions — no DB needed."""
import pandas as pd
import pytest

from app.mood import adv_decline_score, composite_and_label, momentum_score, vix_score


def test_vix_score_low_vix_is_greedy():
    series = pd.Series([20, 18, 15, 12, 10])
    assert vix_score(series, window=5) == pytest.approx(100.0)


def test_vix_score_high_vix_is_fearful():
    series = pd.Series([10, 12, 15, 18, 20])
    assert vix_score(series, window=5) == pytest.approx(0.0)


def test_vix_score_flat_range_is_neutral():
    series = pd.Series([15.0] * 10)
    assert vix_score(series, window=10) == pytest.approx(50.0)


def test_vix_score_empty_is_none():
    assert vix_score(pd.Series(dtype=float)) is None


def test_momentum_score_uptrend_is_greedy():
    close = pd.Series(range(100, 230), dtype=float)  # steadily rising
    score = momentum_score(close, sma_period=125, band=0.10)
    assert score is not None and score > 50


def test_momentum_score_insufficient_history_is_none():
    close = pd.Series([100.0] * 10)
    assert momentum_score(close, sma_period=125) is None


def test_adv_decline_score_all_advancing():
    closes = {
        "A": pd.Series([100, 101, 102, 103, 104, 110]),
        "B": pd.Series([50, 51, 52, 53, 54, 60]),
    }
    assert adv_decline_score(closes, window=5) == pytest.approx(100.0)


def test_adv_decline_score_mixed():
    closes = {
        "A": pd.Series([100, 101, 102, 103, 104, 110]),  # up over the window
        "B": pd.Series([100, 99, 98, 97, 96, 90]),        # down over the window
    }
    assert adv_decline_score(closes, window=5) == pytest.approx(50.0)


def test_adv_decline_score_no_data_is_none():
    assert adv_decline_score({}, window=5) is None


def test_composite_needs_at_least_two_components():
    scores = {"vix": 80.0, "momentum": None, "adv_decline": None}
    weights = {"vix": 1, "momentum": 1, "adv_decline": 1}
    assert composite_and_label(scores, weights) is None


def test_composite_renormalizes_over_available():
    scores = {"vix": 80.0, "momentum": 80.0, "adv_decline": None}
    weights = {"vix": 1, "momentum": 1, "adv_decline": 1}
    comp, label = composite_and_label(scores, weights)
    assert comp == pytest.approx(80.0)
    assert label == "Greed"


def test_composite_bands():
    weights = {"vix": 1, "momentum": 1}
    assert composite_and_label({"vix": 20.0, "momentum": 20.0}, weights)[1] == "Fear"
    assert composite_and_label({"vix": 50.0, "momentum": 50.0}, weights)[1] == "Neutral"
    assert composite_and_label({"vix": 90.0, "momentum": 90.0}, weights)[1] == "Greed"
