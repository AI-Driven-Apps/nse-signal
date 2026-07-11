"""Heikin-Ashi transform correctness, hand-computed against the standard
formula (see golden_master/heikin_ashi.py docstring)."""
import pandas as pd
import pytest

from golden_master.heikin_ashi import to_heikin_ashi


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
