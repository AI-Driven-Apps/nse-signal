"""Heikin-Ashi transform correctness, hand-computed against the standard
formula (see golden_master/heikin_ashi.py docstring)."""
import pandas as pd
import pytest

from golden_master.heikin_ashi import resample_to_15m, to_heikin_ashi


def test_ha_transform_matches_hand_computed_values():
    df = pd.DataFrame({
        "open":  [100.0, 102.0, 106.0],
        "high":  [105.0, 108.0, 107.0],
        "low":   [98.0, 101.0, 103.0],
        "close": [102.0, 106.0, 104.0],
    })
    out = to_heikin_ashi(df)

    assert out["ha_close"].tolist() == pytest.approx([101.25, 104.25, 105.0])
    assert out["ha_open"].tolist() == pytest.approx([101.0, 101.125, 102.6875])
    assert out["ha_high"].tolist() == pytest.approx([105.0, 108.0, 107.0])
    assert out["ha_low"].tolist() == pytest.approx([98.0, 101.0, 102.6875])


def test_ha_transform_does_not_mutate_input():
    df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5]})
    to_heikin_ashi(df)
    assert "ha_close" not in df.columns


def test_ha_transform_empty_input():
    df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
    out = to_heikin_ashi(df)
    assert out.empty
    assert "ha_close" in out.columns


def _five_min_bars(start: str, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    return pd.DataFrame({
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
    }, index=idx)


def test_resample_to_15m_aggregates_three_bars_per_bucket():
    # two full 15-min buckets: 09:30-09:45 and 09:45-10:00
    df = _five_min_bars("2024-06-04 09:30", [
        (100.0, 105.0, 99.0, 102.0),
        (102.0, 106.0, 101.0, 104.0),
        (104.0, 107.0, 103.0, 105.0),
        (105.0, 108.0, 104.0, 106.0),
        (106.0, 109.0, 105.0, 107.0),
        (107.0, 110.0, 106.0, 108.0),
    ])
    out = resample_to_15m(df)

    assert list(out.index) == [pd.Timestamp("2024-06-04 09:30"), pd.Timestamp("2024-06-04 09:45")]
    first, second = out.iloc[0], out.iloc[1]
    assert (first["open"], first["high"], first["low"], first["close"]) == pytest.approx((100.0, 107.0, 99.0, 105.0))
    assert (second["open"], second["high"], second["low"], second["close"]) == pytest.approx((105.0, 110.0, 104.0, 108.0))


def test_resample_to_15m_drops_incomplete_trailing_bucket():
    # only 2 of 3 bars for the second bucket (still filling in, e.g. live polling)
    df = _five_min_bars("2024-06-04 09:30", [
        (100.0, 105.0, 99.0, 102.0),
        (102.0, 106.0, 101.0, 104.0),
        (104.0, 107.0, 103.0, 105.0),
        (105.0, 108.0, 104.0, 106.0),
        (106.0, 109.0, 105.0, 107.0),
    ])
    out = resample_to_15m(df)
    assert list(out.index) == [pd.Timestamp("2024-06-04 09:30")]
