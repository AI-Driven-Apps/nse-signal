"""Live signal engine. Runs after ingest: recompute positions per symbol
with the active strategy, and for every bar within SIGNAL_LOOKBACK_DAYS that
flipped 0→1 (BUY) or 1→0 (SELL), record a Signal and push it to Discord.

Scanning a window (not just the last bar) means a missed or failed nightly run
is caught up on the next run instead of dropping the intervening transitions.

Deduplicated: a (stock, date, action, strategy) signal is only ever sent once,
so re-running the job is safe."""
import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import select

from .backtest import load_prices
from .config import MIN_BARS, SIGNAL_LOOKBACK_DAYS
from .db import session_scope, get_setting
from .models import Signal, Stock
from .notify import send_signal
from .strategies import get_strategy
from .universe import get_universe

log = logging.getLogger("signals")


def run_signals() -> None:
    with session_scope() as s:
        strategy_name = get_setting(s, "active_strategy", "sma_cross")
    strat = get_strategy(strategy_name)
    log.info("Signal scan with strategy '%s'", strategy_name)

    for symbol in get_universe():
        try:
            _scan_symbol(symbol, strat, strategy_name)
        except Exception as e:
            log.warning("%s signal scan failed: %s", symbol, e)


def _scan_symbol(symbol: str, strat, strategy_name: str) -> None:
    with session_scope() as s:
        df = load_prices(s, symbol)
        if len(df) < MIN_BARS:
            return
        pos = strat.positions(df)
        flips = pos.diff()  # +1 = BUY (0→1), -1 = SELL (1→0), 0 = no change
        cutoff = df.index[-1] - timedelta(days=SIGNAL_LOOKBACK_DAYS)

        stock = s.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one()
        for i in range(1, len(df)):
            delta = flips.iloc[i]
            if pd.isna(delta) or delta == 0:
                continue
            bar_date = df.index[i]
            if bar_date < cutoff:
                continue
            action = "BUY" if delta > 0 else "SELL"
            price = float(df["close"].iloc[i])

            dup = s.execute(
                select(Signal).where(
                    Signal.stock_id == stock.id, Signal.date == bar_date,
                    Signal.action == action, Signal.strategy == strategy_name,
                )
            ).scalar_one_or_none()
            if dup:
                continue

            sig = Signal(stock_id=stock.id, date=bar_date, action=action,
                         price=price, strategy=strategy_name)
            sig.sent_to_discord = send_signal(symbol, action, price, strategy_name, bar_date)
            s.add(sig)
            log.info("%s %s @ %.2f on %s (discord=%s)",
                     action, symbol, price, bar_date, sig.sent_to_discord)
