"""Invariant 2: no lookahead. A strategy's position at bar t must not change
when future bars are appended. Runs against EVERY registered strategy, so a
new strategy added to the registry is automatically covered."""
import numpy as np
import pandas as pd
import pytest

from app.strategies import STRATEGIES, get_strategy

CUT = 400  # compare first 400 bars of a 500-bar series


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_no_lookahead(name, trending_df):
    strat = get_strategy(name)
    pos_partial = strat.positions(trending_df.iloc[:CUT])
    pos_full = strat.positions(trending_df)
    diff = (pos_partial.values != pos_full.values[:CUT]).sum()
    assert diff == 0, (
        f"{name}: {diff} positions changed when future data was appended - "
        "lookahead bias!"
    )


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_positions_are_valid(name, trending_df):
    strat = get_strategy(name)
    pos = strat.positions(trending_df)
    assert len(pos) == len(trending_df)
    assert not pos.isna().any(), f"{name}: NaN positions"
    assert set(np.unique(pos.values)) <= {0.0, 1.0}, f"{name}: positions must be 0/1"


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_flat_during_warmup(name, trending_df):
    """Strategies must not signal before their indicators have data."""
    strat = get_strategy(name)
    pos = strat.positions(trending_df)
    assert float(pos.iloc[0]) == 0.0, f"{name}: position on the very first bar"
