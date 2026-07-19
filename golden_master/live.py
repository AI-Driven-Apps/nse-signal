"""Live polling: ingest new 5-min bars, replay the (already fully tested)
backtest_range() over a recent window with treat_last_day_as_incomplete=True,
and upsert the result — new signals get inserted (and alerted on Discord
exactly once), already-open trades get their exit fields filled in once a
later poll resolves them. Re-deriving the whole window from scratch each
poll (rather than tracking engine state across polls in memory) means a
container restart loses nothing — the DB, not process memory, is the source
of truth, matching the rest of this project's design."""
import logging
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select

from .backtest import Trade, backtest_range, compute_daily_levels, store_daily_levels
from .config import SYMBOL
from .db import session_scope
from .ingest import run_ingest
from .models import FiveMinBar, GoldenMasterTrade
from .notify import send_golden_master_signal

log = logging.getLogger("golden_master.live")

# Enough for yellow1 (prev trading day's 15:15 close) plus today, with a
# safety margin for long weekends/holidays where "previous day" is further back.
LOOKBACK_DAYS = 5


def _load_recent_bars_df(session, symbol: str, lookback_days: int,
                         now: datetime | None = None) -> pd.DataFrame:
    cutoff = (now or datetime.utcnow()) - timedelta(days=lookback_days)
    rows = session.execute(
        select(FiveMinBar)
        .where(FiveMinBar.symbol == symbol, FiveMinBar.timestamp >= cutoff)
        .order_by(FiveMinBar.timestamp.asc())
    ).scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        {"open": r.open, "high": r.high, "low": r.low, "close": r.close}
        for r in rows
    ).set_index(pd.Index([r.timestamp for r in rows], name="timestamp"))


def _upsert_live_trades(session, trades: list[Trade]) -> list[Trade]:
    """Upsert by (trade_date, signal_type, direction). Returns only the
    trades that are brand new this cycle, so the caller alerts on Discord
    exactly once per trade rather than on every poll."""
    existing = {
        (row.trade_date, row.signal_type, row.direction): row
        for row in session.execute(
            select(GoldenMasterTrade).where(GoldenMasterTrade.source == "live")
        ).scalars()
    }
    new_trades = []
    for t in trades:
        key = (t.trade_date, t.signal_type, t.direction)
        row = existing.get(key)
        if row is None:
            session.add(GoldenMasterTrade(
                trade_date=t.trade_date, signal_type=t.signal_type, direction=t.direction,
                entry_time=t.entry_time, entry_price=t.entry_price,
                sl_price=t.sl_price, target_price=t.target_price,
                exit_time=t.exit_time, exit_price=t.exit_price, exit_reason=t.exit_reason,
                pnl_points=t.pnl_points, ce_strike=t.ce_strike, pe_strike=t.pe_strike,
                source="live",
            ))
            new_trades.append(t)
        elif row.exit_reason is None and t.exit_reason is not None:
            row.exit_time = t.exit_time
            row.exit_price = t.exit_price
            row.exit_reason = t.exit_reason
            row.pnl_points = t.pnl_points
            log.info("Live trade resolved: %s %s %s on %s -> %s (%.1f pts)",
                     t.trade_date, t.signal_type, t.direction, t.exit_time, t.exit_reason, t.pnl_points)
    return new_trades


def run_live_cycle(now: datetime | None = None) -> None:
    """One poll cycle. Safe to call repeatedly (idempotent) and safe to call
    outside market hours (yields no new bars/trades, a no-op). `now` is
    exposed for tests; production calls always use the real current time."""
    run_ingest()

    with session_scope() as s:
        bars_df = _load_recent_bars_df(s, SYMBOL, LOOKBACK_DAYS, now=now)
        if bars_df.empty:
            return
        trades = backtest_range(bars_df, treat_last_day_as_incomplete=True)
        new_trades = _upsert_live_trades(s, trades)
        store_daily_levels(s, compute_daily_levels(bars_df))

    for t in new_trades:
        sent = send_golden_master_signal(
            SYMBOL, t.signal_type, t.direction, t.entry_price, t.sl_price, t.target_price, t.entry_time)
        log.info("Live signal: %s %s %s @ %.2f (discord=%s)",
                 t.signal_type, t.direction, SYMBOL, t.entry_price, sent)
