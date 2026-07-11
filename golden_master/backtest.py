"""Backtest simulator. The existing app/backtest.py's vectorized approach
doesn't apply here (one-shot bracket trades, not a continuous position
series) — this replays 15-min bars through SignalEngine and, for each fired
signal, scans forward for the first bar touching SL or target.

Resolution rules (see docs/golden_master_spec.md):
- A single bar touching both SL and target resolves to SL_HIT (conservative,
  matches the project's existing "backtest results overstate live
  performance" stance — checked first in the loop below, so it wins ties).
- Neither hit by the day's last bar: EOD_SQUARE_OFF at that bar's close.
- CE/PE strikes are computed from each day's first bar (assumed to be the
  session open) and stored on the trade for reference/labeling only — pnl
  is in Nifty index points, not real option premium.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from .config import SL_POINTS, STRIKE_OFFSET, TARGET_POINTS
from .engine import HABar, SignalEngine
from .heikin_ashi import to_heikin_ashi
from .models import GoldenMasterBacktestRun, GoldenMasterTrade

log = logging.getLogger("golden_master.backtest")


@dataclass
class Trade:
    trade_date: date
    signal_type: str
    direction: str
    entry_time: datetime
    entry_price: float
    sl_price: float
    target_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str  # SL_HIT | TARGET_HIT | EOD_SQUARE_OFF
    pnl_points: float
    ce_strike: float
    pe_strike: float


def _as_datetime(ts) -> datetime:
    return ts if isinstance(ts, datetime) else pd.Timestamp(ts).to_pydatetime()


def _as_date(ts) -> date:
    return _as_datetime(ts).date()


def backtest_range(df: pd.DataFrame) -> list[Trade]:
    """df: ascending-time 15-min OHLC bars (open/high/low/close columns),
    one or more trading days. Precondition: each day's first row is that
    day's session open (used for CE/PE strike selection)."""
    ha = to_heikin_ashi(df)
    engine = SignalEngine()
    trades: list[Trade] = []
    strikes_by_day: dict[date, tuple[float, float]] = {}

    rows = list(ha.iterrows())
    for i, (ts, row) in enumerate(rows):
        d = _as_date(ts)
        if d not in strikes_by_day:
            rounded = round(row["open"] / STRIKE_OFFSET) * STRIKE_OFFSET
            strikes_by_day[d] = (rounded - STRIKE_OFFSET, rounded + STRIKE_OFFSET)
        ce_strike, pe_strike = strikes_by_day[d]

        bar = HABar(_as_datetime(ts), row["ha_open"], row["ha_high"],
                    row["ha_low"], row["ha_close"])
        signal = engine.process_bar(bar)
        if signal is None:
            continue

        if signal.direction == "BUY":
            sl_price = signal.entry_price - SL_POINTS
            target_price = signal.entry_price + TARGET_POINTS
        else:
            sl_price = signal.entry_price + SL_POINTS
            target_price = signal.entry_price - TARGET_POINTS

        exit_time = exit_price = exit_reason = None
        last_same_day_ts, last_same_day_close = ts, row["close"]
        for j in range(i + 1, len(rows)):
            ts2, row2 = rows[j]
            if _as_date(ts2) != d:
                break
            last_same_day_ts, last_same_day_close = ts2, row2["close"]
            if signal.direction == "BUY":
                hit_sl = row2["low"] <= sl_price
                hit_target = row2["high"] >= target_price
            else:
                hit_sl = row2["high"] >= sl_price
                hit_target = row2["low"] <= target_price
            if hit_sl:  # checked first: same-bar tie resolves to SL
                exit_time, exit_price, exit_reason = ts2, sl_price, "SL_HIT"
                break
            if hit_target:
                exit_time, exit_price, exit_reason = ts2, target_price, "TARGET_HIT"
                break

        if exit_time is None:
            exit_time = last_same_day_ts
            exit_price = float(last_same_day_close)
            exit_reason = "EOD_SQUARE_OFF"

        pnl = ((exit_price - signal.entry_price) if signal.direction == "BUY"
              else (signal.entry_price - exit_price))

        trades.append(Trade(
            trade_date=d, signal_type=signal.signal_type, direction=signal.direction,
            entry_time=signal.entry_time, entry_price=signal.entry_price,
            sl_price=sl_price, target_price=target_price,
            exit_time=_as_datetime(exit_time), exit_price=exit_price, exit_reason=exit_reason,
            pnl_points=pnl, ce_strike=ce_strike, pe_strike=pe_strike,
        ))
    return trades


def store_backtest(session, trades: list[Trade], start_date: date, end_date: date) -> GoldenMasterBacktestRun:
    """Persist trades + one aggregate run row. Fixed 1:1 SL:target means win
    rate alone determines edge before costs — it's the headline metric."""
    for t in trades:
        session.add(GoldenMasterTrade(
            trade_date=t.trade_date, signal_type=t.signal_type, direction=t.direction,
            entry_time=t.entry_time, entry_price=t.entry_price,
            sl_price=t.sl_price, target_price=t.target_price,
            exit_time=t.exit_time, exit_price=t.exit_price, exit_reason=t.exit_reason,
            pnl_points=t.pnl_points, ce_strike=t.ce_strike, pe_strike=t.pe_strike,
            source="backtest",
        ))
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_points > 0)
    run = GoldenMasterBacktestRun(
        start_date=start_date, end_date=end_date, n_trades=n, n_wins=wins,
        win_rate=(wins / n if n else 0.0),
        net_points=sum(t.pnl_points for t in trades),
        rejection_trades=sum(1 for t in trades if t.signal_type == "REJECTION"),
        blue_breakout_trades=sum(1 for t in trades if t.signal_type == "BLUE_BREAKOUT"),
    )
    session.add(run)
    log.info("Backtest: %d trades, win_rate=%.1f%%, net=%.1f points",
             n, run.win_rate * 100, run.net_points)
    return run
