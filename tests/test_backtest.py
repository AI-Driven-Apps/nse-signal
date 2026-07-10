"""Backtester sanity: known strategies on known data give sane metrics."""
import numpy as np
import pandas as pd

from app.backtest import run_backtest, COST_PER_SIDE
from app.strategies.base import Strategy


class AlwaysLong(Strategy):
    name = "always_long"

    def positions(self, df):
        pos = pd.Series(1.0, index=df.index)
        pos.iloc[0] = 0.0
        return pos


class NeverLong(Strategy):
    name = "never_long"

    def positions(self, df):
        return pd.Series(0.0, index=df.index)


def test_always_long_on_uptrend(trending_df):
    m = run_backtest(trending_df, AlwaysLong())
    assert m["final_equity"] > 1.0
    assert m["cagr"] > 0
    assert -1.0 <= m["max_drawdown"] <= 0.0
    assert m["n_trades"] == 1
    assert np.isfinite(m["sharpe"])


def test_never_long_is_flat(trending_df):
    m = run_backtest(trending_df, NeverLong())
    assert m["n_trades"] == 0
    assert m["final_equity"] == 1.0
    assert m["cagr"] == 0.0 or abs(m["cagr"]) < 1e-9


def test_costs_are_charged(trending_df):
    """Entering once must cost one side of fees vs a free run."""
    m = run_backtest(trending_df, AlwaysLong())
    gross = float((1 + trending_df["close"].pct_change().fillna(0)
                   .iloc[2:]).prod())  # position active from bar 2 (shifted)
    assert m["final_equity"] < gross, "transaction costs not applied"


def test_win_rate_bounds(trending_df):
    m = run_backtest(trending_df, AlwaysLong())
    assert 0.0 <= m["win_rate"] <= 1.0
