"""Backtest simulator tests, at 5-min granularity (2026-07-18 spec-alignment
rework). Builds a hand-verified bull-rejection scenario — resample-then-HA
math verified against the real code before being hard-coded here, same
approach as test_heikin_ashi.py's hand-computed values — so entry price is
known exactly, then varies what happens afterward to exercise SL_HIT,
TARGET_HIT, the same-bar tie rule, and EOD square-off. SL/target touch
checks use RAW 5-min price (not HA) since that's what a real stop/target
order fills against — only reference-line levels are HA-derived."""
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import select

from golden_master.backtest import (
    backtest_range, compute_daily_levels, store_backtest, store_daily_levels,
)
from golden_master.models import GoldenMasterBacktestRun, GoldenMasterDailyLevels, GoldenMasterTrade


def _bull_rejection_df(extra_rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Prior-day 15:15-15:25 (flat, ->yellow1=100) + today 9:15-9:25 (flat,
    ->yellow2=101) + today 9:30-9:40 produce a bull REJECTION at 9:40 with
    entry_price = 108.0 (the raw high of the 9:40 5-min bar — hand-verified:
    close=106>101 upperYellow, low=103>101 no-touch, bullish). extra_rows:
    list of (timestamp_str, open, high, low, close) appended after 9:40,
    ascending, same day, to drive SL/target/EOD resolution.
    """
    base = [
        ("2024-06-03 15:15", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-03 15:20", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-03 15:25", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-04 09:15", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:20", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:25", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:30", 101.0, 103.0, 100.0, 102.0),  # touches zone, no rejection yet
        ("2024-06-04 09:35", 102.0, 106.0, 101.0, 104.0),  # touches zone (low==101), no rejection
        ("2024-06-04 09:40", 104.0, 108.0, 103.0, 106.0),  # REJECTION fires, entry=high=108
    ]
    rows = base + [(f"2024-06-04 {t}", o, h, l, c) for t, o, h, l, c in extra_rows]
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({
        "open": [r[1] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
    }, index=idx)


def test_rejection_entry_and_strikes_are_correct():
    df = _bull_rejection_df([("09:45", 108.0, 110.0, 106.0, 109.0)])
    trades = backtest_range(df)
    assert len(trades) == 1
    t = trades[0]
    assert t.signal_type == "REJECTION"
    assert t.direction == "BUY"
    assert t.entry_price == pytest.approx(108.0)
    assert t.entry_time == datetime(2024, 6, 4, 9, 40)
    # day open (9:15) = 101 -> rounded to 100 -> CE=0, PE=200
    assert t.ce_strike == pytest.approx(0.0)
    assert t.pe_strike == pytest.approx(200.0)


def test_target_hit_resolves_correctly():
    # SL=83, target=133
    df = _bull_rejection_df([("09:45", 120.0, 135.0, 118.0, 130.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "TARGET_HIT"
    assert t.exit_price == pytest.approx(133.0)
    assert t.pnl_points == pytest.approx(25.0)


def test_sl_hit_resolves_correctly():
    df = _bull_rejection_df([("09:45", 100.0, 105.0, 80.0, 90.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "SL_HIT"
    assert t.exit_price == pytest.approx(83.0)
    assert t.pnl_points == pytest.approx(-25.0)


def test_same_bar_touching_both_sl_and_target_resolves_to_sl():
    df = _bull_rejection_df([("09:45", 108.0, 135.0, 80.0, 110.0)])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "SL_HIT"
    assert t.exit_price == pytest.approx(83.0)


def test_eod_square_off_when_neither_hit():
    df = _bull_rejection_df([
        ("09:45", 108.0, 115.0, 105.0, 110.0),
        ("09:50", 110.0, 114.0, 107.0, 111.5),  # last bar of the day
    ])
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "EOD_SQUARE_OFF"
    assert t.exit_time == datetime(2024, 6, 4, 9, 50)
    assert t.exit_price == pytest.approx(111.5)
    assert t.pnl_points == pytest.approx(111.5 - 108.0)


def test_signal_on_the_last_bar_of_the_day_squares_off_at_its_own_close():
    df = _bull_rejection_df([])  # no bars after the 9:40 signal bar itself
    trades = backtest_range(df)
    t = trades[0]
    assert t.exit_reason == "EOD_SQUARE_OFF"
    assert t.exit_time == datetime(2024, 6, 4, 9, 40)
    assert t.exit_price == pytest.approx(106.0)  # the signal bar's own close


def test_treat_last_day_as_incomplete_leaves_last_day_signal_open():
    df = _bull_rejection_df([("09:45", 108.0, 115.0, 105.0, 110.0)])  # no SL/target hit
    trades = backtest_range(df, treat_last_day_as_incomplete=True)
    t = trades[0]
    assert t.exit_reason is None
    assert t.exit_time is None
    assert t.exit_price is None
    assert t.pnl_points is None


def test_treat_last_day_as_incomplete_still_resolves_earlier_days():
    df = _bull_rejection_df([("09:45", 108.0, 115.0, 105.0, 110.0)])
    later_day = pd.DataFrame({
        "open": [120.0], "high": [121.0], "low": [119.0], "close": [120.5],
    }, index=pd.to_datetime(["2024-06-05 09:15"]))
    df = pd.concat([df, later_day])

    trades = backtest_range(df, treat_last_day_as_incomplete=True)
    t = trades[0]
    assert t.trade_date == date(2024, 6, 4)
    assert t.exit_reason == "EOD_SQUARE_OFF"
    assert t.exit_price == pytest.approx(110.0)  # last 2024-06-04 bar's close


def test_store_backtest_persists_trades_and_aggregate_run(db):
    from golden_master.db import session_scope

    df = _bull_rejection_df([("09:45", 120.0, 135.0, 118.0, 130.0)])  # target hit
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


def test_compute_daily_levels_matches_hand_verified_values():
    df = _bull_rejection_df([
        ("09:45", 110.0, 113.0, 109.0, 112.0),
        ("09:50", 112.0, 116.0, 110.0, 114.0),
        ("09:55", 114.0, 118.0, 112.0, 116.0),
    ])
    levels = compute_daily_levels(df)

    prev_day = levels[date(2024, 6, 3)]
    assert prev_day["yellow1"] == pytest.approx(100.0)

    today = levels[date(2024, 6, 4)]
    assert today["yellow1"] == pytest.approx(100.0)
    assert today["yellow2"] == pytest.approx(101.0)
    assert today["blue1"] == pytest.approx(103.75)
    assert today["blue2"] == pytest.approx(102.125)
    assert today["ce_strike"] == pytest.approx(0.0)
    assert today["pe_strike"] == pytest.approx(200.0)


def test_store_daily_levels_upserts_by_date(db):
    from golden_master.db import session_scope

    df = _bull_rejection_df([])
    levels = compute_daily_levels(df)

    with session_scope() as s:
        store_daily_levels(s, levels)
    with session_scope() as s:
        rows = s.execute(select(GoldenMasterDailyLevels)).scalars().all()
    assert len(rows) == 2  # prev day + today

    # re-store (as a later poll cycle would) must upsert, not duplicate
    with session_scope() as s:
        store_daily_levels(s, levels)
    with session_scope() as s:
        rows2 = s.execute(select(GoldenMasterDailyLevels)).scalars().all()
    assert len(rows2) == 2
