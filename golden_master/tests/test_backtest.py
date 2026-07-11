"""Backtest simulator tests. Builds a hand-verified bull-rejection scenario
(HA math worked out by hand, same approach as test_heikin_ashi.py) so entry
price is known exactly, then varies what happens afterward to exercise
SL_HIT, TARGET_HIT, the same-bar tie rule, and EOD square-off. SL/target
touch checks use RAW price (not HA) since that's what a real stop/target
order fills against — only signal detection is HA-based."""
from datetime import datetime

import pandas as pd
import pytest

from golden_master.backtest import backtest_range, store_backtest
from golden_master.models import GoldenMasterBacktestRun, GoldenMasterTrade
from sqlalchemy import select


def _bull_rejection_df(extra_rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Prior-day 15:15 + today 9:15/9:30/9:45 bars produce a bull REJECTION
    at 9:45 with entry_price = 118.0 (hand-verified: see module docstring
    reasoning in the commit/plan). extra_rows: list of
    (timestamp_str, open, high, low, close) appended after 9:45, ascending,
    same day, to drive SL/target/EOD resolution.
    """
    base = [
        ("2024-06-03 15:15", 100.0, 100.0, 100.0, 100.0),  # seeds yellow1=100
        ("2024-06-04 09:15", 101.0, 101.0, 101.0, 101.0),  # seeds yellow2=101, day open (strikes)
        ("2024-06-04 09:30", 101.0, 108.0, 100.0, 106.0),  # touches zone, no rejection; sets blue1
        ("2024-06-04 09:45", 110.0, 118.0, 109.0, 116.0),  # bull REJECTION fires, entry=ha_high=118
    ]
    rows = base + [(f"2024-06-04 {t}", o, h, l, c) for t, o, h, l, c in extra_rows]
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({
        "open": [r[1] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
    }, index=idx)


def test_rejection_entry_and_strikes_are_correct():
    df = _bull_rejection_df([("10:00", 118.0, 120.0, 116.0, 119.0)])
    trades = backtest_range(df)
    assert len(trades) == 1
    t = trades[0]
    assert t.signal_type == "REJECTION"
    assert t.direction == "BUY"
    assert t.entry_price == pytest.approx(118.0)
    assert t.entry_time == datetime(2024, 6, 4, 9, 45)
    # day open (9:15) = 101 -> rounded to 100 -> CE=0, PE=200
    assert t.ce_strike == pytest.approx(0.0)
    assert t.pe_strike == pytest.approx(200.0)


def test_target_hit_resolves_correctly():
    # SL=93, target=143
    df = _bull_rejection_df([("10:00", 130.0, 145.0, 128.0, 140.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "TARGET_HIT"
    assert t.exit_price == pytest.approx(143.0)
    assert t.pnl_points == pytest.approx(25.0)


def test_sl_hit_resolves_correctly():
    df = _bull_rejection_df([("10:00", 100.0, 105.0, 90.0, 95.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "SL_HIT"
    assert t.exit_price == pytest.approx(93.0)
    assert t.pnl_points == pytest.approx(-25.0)


def test_same_bar_touching_both_sl_and_target_resolves_to_sl():
    # a single wide bar touches both 93 (SL) and 143 (target)
    df = _bull_rejection_df([("10:00", 118.0, 145.0, 90.0, 120.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "SL_HIT"
    assert t.exit_price == pytest.approx(93.0)


def test_eod_square_off_when_neither_hit():
    df = _bull_rejection_df([
        ("10:00", 118.0, 125.0, 115.0, 120.0),
        ("10:15", 120.0, 124.0, 117.0, 121.5),  # last bar of the day
    ])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "EOD_SQUARE_OFF"
    assert t.exit_time == datetime(2024, 6, 4, 10, 15)
    assert t.exit_price == pytest.approx(121.5)
    assert t.pnl_points == pytest.approx(121.5 - 118.0)


def test_signal_on_the_last_bar_of_the_day_squares_off_at_its_own_close():
    df = _bull_rejection_df([])  # no bars after the 9:45 signal bar itself
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "EOD_SQUARE_OFF"
    assert t.exit_time == datetime(2024, 6, 4, 9, 45)
    assert t.exit_price == pytest.approx(116.0)  # the signal bar's own close


def test_store_backtest_persists_trades_and_aggregate_run(db):
    from golden_master.db import session_scope

    df = _bull_rejection_df([("10:00", 130.0, 145.0, 128.0, 140.0)])
    trades = backtest_range(df)

    with session_scope() as s:
        store_backtest(s, trades, df.index[0].date(), df.index[-1].date())

    with session_scope() as s:
        stored_trades = s.execute(select(GoldenMasterTrade)).scalars().all()
        run = s.execute(select(GoldenMasterBacktestRun)).scalar_one()

    assert len(stored_trades) == 1
    assert stored_trades[0].source == "backtest"
    assert run.n_trades == 1
    assert run.n_wins == 1
    assert run.win_rate == pytest.approx(1.0)
    assert run.net_points == pytest.approx(25.0)
    assert run.rejection_trades == 1
    assert run.blue_breakout_trades == 0
