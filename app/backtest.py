"""Lightweight vectorized backtester.

Deliberately simple and dependency-free (pandas/numpy only) so it runs fast
on the Pi. Positions are lagged one bar (signal on today's close, filled at
tomorrow's close) to avoid lookahead bias. Costs default to 0.1% per side
to approximate NSE brokerage + STT + slippage."""
import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select

from .db import session_scope, get_setting
from .models import BacktestRun, PriceBar, Stock
from .strategies import get_strategy
from .universe import get_universe

log = logging.getLogger("backtest")

COST_PER_SIDE = 0.001  # 0.1%
TRADING_DAYS = 252


def load_prices(session, symbol: str) -> pd.DataFrame:
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        return pd.DataFrame()
    rows = session.execute(
        select(PriceBar).where(PriceBar.stock_id == stock.id).order_by(PriceBar.date)
    ).scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        {"open": r.open, "high": r.high, "low": r.low,
         "close": r.close, "volume": r.volume}
        for r in rows
    ).set_index(pd.Index([r.date for r in rows], name="date"))


def run_backtest(df: pd.DataFrame, strategy) -> dict:
    pos = strategy.positions(df).shift(1).fillna(0)  # trade next bar: no lookahead
    rets = df["close"].pct_change().fillna(0)
    turnover = pos.diff().abs().fillna(0)
    strat_rets = pos * rets - turnover * COST_PER_SIDE
    equity = (1 + strat_rets).cumprod()

    n_years = max(len(df) / TRADING_DAYS, 1e-9)
    final = float(equity.iloc[-1])
    cagr = final ** (1 / n_years) - 1
    dd = (equity / equity.cummax() - 1).min()
    vol = strat_rets.std()
    sharpe = float(strat_rets.mean() / vol * np.sqrt(TRADING_DAYS)) if vol > 0 else 0.0

    # Per-trade win rate from position transitions
    entries = pos[(pos.diff() > 0)].index
    exits = pos[(pos.diff() < 0)].index
    wins = total = 0
    closes = df["close"]
    for i, entry in enumerate(entries):
        exit_candidates = [e for e in exits if e > entry]
        exit_ = exit_candidates[0] if exit_candidates else df.index[-1]
        total += 1
        if closes.loc[exit_] > closes.loc[entry]:
            wins += 1

    return {
        "cagr": float(cagr), "max_drawdown": float(dd), "sharpe": sharpe,
        "win_rate": wins / total if total else 0.0,
        "n_trades": total, "final_equity": final,
        "start": df.index[0], "end": df.index[-1],
    }


def backtest_universe(strategy_name: str | None = None) -> None:
    """Backtest one strategy across the whole universe, store results."""
    with session_scope() as s:
        name = strategy_name or get_setting(s, "active_strategy", "sma_cross")
    strat = get_strategy(name)
    log.info("Backtesting '%s' across %d symbols", name, len(get_universe()))

    for symbol in get_universe():
        with session_scope() as s:
            df = load_prices(s, symbol)
            if len(df) < 200:
                continue
            m = run_backtest(df, strat)
            s.add(BacktestRun(
                strategy=name, symbol=symbol,
                start_date=m["start"], end_date=m["end"],
                cagr=m["cagr"], max_drawdown=m["max_drawdown"],
                sharpe=m["sharpe"], win_rate=m["win_rate"],
                n_trades=m["n_trades"], final_equity=m["final_equity"],
            ))
            log.info("%s  CAGR %.1f%%  DD %.1f%%  Sharpe %.2f  trades %d",
                     symbol, m["cagr"] * 100, m["max_drawdown"] * 100,
                     m["sharpe"], m["n_trades"])
