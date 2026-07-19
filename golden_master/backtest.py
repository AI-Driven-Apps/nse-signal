"""Backtest simulator. The existing app/backtest.py's vectorized approach
doesn't apply here (one-shot bracket trades, not a continuous position
series) — this replays 5-min bars through SignalEngine and, for each fired
signal, scans forward for the first bar touching SL or target.

Also reused for live trading (golden_master/live.py): the same replay,
called with treat_last_day_as_incomplete=True so a trade with no SL/target
hit yet within the LAST calendar day of the input is left open (exit_* =
None) rather than squared off — since for live polling that last day is
"now", not a finished trading day. live.py re-runs this on every poll as
more bars arrive and upserts the result, so the same tested resolution
logic (including the SL-wins-ties rule) drives both paths.

Dual timeframe (2026-07-11 spec-alignment rework, see docs/golden_master_spec.md):
input is 5-min OHLC only. The 15-min Heikin-Ashi reference-line series is
derived in-memory (heikin_ashi.resample_to_15m + to_heikin_ashi) and fed to
SignalEngine.update_levels() in lockstep with the 5-min bars going to
process_bar() — level bars for a given instant are always applied before a
detection bar at that same instant, so e.g. the 9:30 level (blue1) is
visible to a 9:30 detection bar in the same pass.

Resolution rules (see docs/golden_master_spec.md):
- A single bar touching both SL and target resolves to SL_HIT (conservative,
  matches the project's existing "backtest results overstate live
  performance" stance — checked first in the loop below, so it wins ties).
- Neither hit by the day's last bar: EOD_SQUARE_OFF at that bar's close
  (unless that day is the incomplete "today" in live mode — see above).
- CE/PE strikes are computed from each day's first bar (assumed to be the
  session open) and stored on the trade for reference/labeling only — pnl
  is in Nifty index points, not real option premium.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from .config import SL_POINTS, STRIKE_OFFSET, TARGET_POINTS
from .engine import EntryBar, HABar, SignalEngine
from .heikin_ashi import resample_to_15m, to_heikin_ashi
from .models import GoldenMasterBacktestRun, GoldenMasterDailyLevels, GoldenMasterTrade

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
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str | None  # SL_HIT | TARGET_HIT | EOD_SQUARE_OFF | None (still open)
    pnl_points: float | None
    ce_strike: float
    pe_strike: float


def _as_datetime(ts) -> datetime:
    return ts if isinstance(ts, datetime) else pd.Timestamp(ts).to_pydatetime()


def _as_date(ts) -> date:
    return _as_datetime(ts).date()


def _derive_levels(df_5m: pd.DataFrame) -> list[tuple]:
    """15-min HA bars derived from the 5-min input, as a plain row list for
    the lockstep advance in backtest_range/compute_daily_levels."""
    df_15m = resample_to_15m(df_5m)
    ha_15m = to_heikin_ashi(df_15m)
    return list(ha_15m.iterrows())


def _advance_levels(engine: SignalEngine, level_rows: list, level_idx: int, upto_ts: datetime) -> int:
    """Feed engine.update_levels() every level bar at-or-before upto_ts.
    Returns the new level_idx (so the caller resumes from where this left off)."""
    while level_idx < len(level_rows) and _as_datetime(level_rows[level_idx][0]) <= upto_ts:
        lts, lrow = level_rows[level_idx]
        engine.update_levels(HABar(_as_datetime(lts), lrow["ha_open"], lrow["ha_high"],
                                   lrow["ha_low"], lrow["ha_close"]))
        level_idx += 1
    return level_idx


def backtest_range(df_5m: pd.DataFrame, treat_last_day_as_incomplete: bool = False) -> list[Trade]:
    """df_5m: ascending-time 5-min OHLC bars (open/high/low/close columns),
    one or more trading days. Precondition: each day's first row is that
    day's session open (used for CE/PE strike selection).

    treat_last_day_as_incomplete: if True, a signal on the LAST calendar day
    present in df_5m that hasn't hit SL/target within df_5m's own bars is
    left open (exit_* = None) instead of EOD-squared-off — for live polling,
    where the last day is still in progress. Earlier days always resolve
    normally regardless of this flag."""
    engine = SignalEngine()
    trades: list[Trade] = []
    strikes_by_day: dict[date, tuple[float, float]] = {}
    level_rows = _derive_levels(df_5m)
    level_idx = 0

    rows = list(df_5m.iterrows())
    last_date = _as_date(rows[-1][0]) if rows else None

    for i, (ts, row) in enumerate(rows):
        ts_dt = _as_datetime(ts)
        d = _as_date(ts)
        if d not in strikes_by_day:
            rounded = round(row["open"] / STRIKE_OFFSET) * STRIKE_OFFSET
            strikes_by_day[d] = (rounded - STRIKE_OFFSET, rounded + STRIKE_OFFSET)
        ce_strike, pe_strike = strikes_by_day[d]

        level_idx = _advance_levels(engine, level_rows, level_idx, ts_dt)

        bar = EntryBar(ts_dt, row["open"], row["high"], row["low"], row["close"])
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

        still_open = False
        if exit_time is None:
            if treat_last_day_as_incomplete and d == last_date:
                still_open = True
            else:
                exit_time = last_same_day_ts
                exit_price = float(last_same_day_close)
                exit_reason = "EOD_SQUARE_OFF"

        if still_open:
            pnl = None
        else:
            pnl = ((exit_price - signal.entry_price) if signal.direction == "BUY"
                  else (signal.entry_price - exit_price))

        trades.append(Trade(
            trade_date=d, signal_type=signal.signal_type, direction=signal.direction,
            entry_time=signal.entry_time, entry_price=signal.entry_price,
            sl_price=sl_price, target_price=target_price,
            exit_time=None if still_open else _as_datetime(exit_time),
            exit_price=exit_price, exit_reason=exit_reason,
            pnl_points=pnl, ce_strike=ce_strike, pe_strike=pe_strike,
        ))
    return trades


def compute_daily_levels(df_5m: pd.DataFrame) -> dict[date, dict]:
    """Snapshot each trading day's yellow/blue levels + CE/PE strikes as of
    the last bar processed that day, independent of whether a trade fired —
    feeds the dashboard's daily-levels display (§ written requirement's
    "chart requirements": show lines + strikes with labels)."""
    engine = SignalEngine()  # only update_levels() is used here
    level_rows = _derive_levels(df_5m)
    level_idx = 0
    strikes_by_day: dict[date, tuple[float, float]] = {}
    out: dict[date, dict] = {}

    for ts, row in df_5m.iterrows():
        ts_dt = _as_datetime(ts)
        d = _as_date(ts)
        if d not in strikes_by_day:
            rounded = round(row["open"] / STRIKE_OFFSET) * STRIKE_OFFSET
            strikes_by_day[d] = (rounded - STRIKE_OFFSET, rounded + STRIKE_OFFSET)
        level_idx = _advance_levels(engine, level_rows, level_idx, ts_dt)
        ce_strike, pe_strike = strikes_by_day[d]
        out[d] = {
            "yellow1": engine.yellow1, "yellow2": engine.yellow2,
            "blue1": engine.blue1, "blue2": engine.blue2,
            "ce_strike": ce_strike, "pe_strike": pe_strike,
        }
    return out


def store_backtest(session, trades: list[Trade], start_date: date, end_date: date) -> GoldenMasterBacktestRun:
    """Persist trades + one aggregate run row. Fixed 1:1 SL:target means win
    rate alone determines edge before costs — it's the headline metric.
    Aggregates only count resolved trades (pnl_points is not None); with the
    default treat_last_day_as_incomplete=False every trade is resolved, but
    this stays defensive in case a caller passes an incomplete-day replay."""
    resolved = [t for t in trades if t.pnl_points is not None]
    for t in trades:
        session.add(GoldenMasterTrade(
            trade_date=t.trade_date, signal_type=t.signal_type, direction=t.direction,
            entry_time=t.entry_time, entry_price=t.entry_price,
            sl_price=t.sl_price, target_price=t.target_price,
            exit_time=t.exit_time, exit_price=t.exit_price, exit_reason=t.exit_reason,
            pnl_points=t.pnl_points, ce_strike=t.ce_strike, pe_strike=t.pe_strike,
            source="backtest",
        ))
    n = len(resolved)
    wins = sum(1 for t in resolved if t.pnl_points > 0)
    run = GoldenMasterBacktestRun(
        start_date=start_date, end_date=end_date, n_trades=n, n_wins=wins,
        win_rate=(wins / n if n else 0.0),
        net_points=sum(t.pnl_points for t in resolved),
        rejection_trades=sum(1 for t in resolved if t.signal_type == "REJECTION"),
        blue_breakout_trades=sum(1 for t in resolved if t.signal_type == "BLUE_BREAKOUT"),
    )
    session.add(run)
    log.info("Backtest: %d trades, win_rate=%.1f%%, net=%.1f points",
             n, run.win_rate * 100, run.net_points)
    return run


def store_daily_levels(session, daily_levels: dict[date, dict]) -> None:
    """Upsert one row per day from compute_daily_levels()'s output."""
    for d, levels in daily_levels.items():
        row = session.get(GoldenMasterDailyLevels, d)
        if row is None:
            row = GoldenMasterDailyLevels(trading_date=d)
            session.add(row)
        row.yellow1 = levels.get("yellow1")
        row.yellow2 = levels.get("yellow2")
        row.blue1 = levels.get("blue1")
        row.blue2 = levels.get("blue2")
        row.ce_strike = levels.get("ce_strike")
        row.pe_strike = levels.get("pe_strike")
