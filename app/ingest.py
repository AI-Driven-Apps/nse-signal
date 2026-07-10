"""EOD ingestion.

Re-fetches the full BACKFILL_DAYS window each run and upserts, rather than
appending only new bars. yfinance's auto_adjust rebases the *entire* history
after every split/dividend, so appending new adjusted bars onto older ones
fetched under a different baseline silently mixes price scales and corrupts
every indicator computed across the join. A full re-fetch keeps one consistent
baseline; upsert makes it idempotent and cheap on unchanged bars."""
import logging
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select

from .config import BACKFILL_DAYS
from .data_provider import get_provider
from .db import session_scope
from .models import PriceBar, Stock
from .universe import get_universe

log = logging.getLogger("ingest")


def _get_or_create_stock(session, symbol: str) -> Stock:
    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol)
    ).scalar_one_or_none()
    if not stock:
        stock = Stock(symbol=symbol)
        session.add(stock)
        session.flush()
    return stock


def ingest_symbol(session, provider, symbol: str) -> int:
    stock = _get_or_create_stock(session, symbol)
    start = date.today() - timedelta(days=BACKFILL_DAYS)

    df = provider.fetch(symbol, start=start)
    if df.empty:
        return 0

    existing = {
        bar.date: bar
        for bar in session.execute(
            select(PriceBar).where(PriceBar.stock_id == stock.id)
        ).scalars()
    }
    changed = 0
    for d, row in df.iterrows():
        o, h, l, c = (float(row["open"]), float(row["high"]),
                      float(row["low"]), float(row["close"]))
        v = 0.0 if pd.isna(row["volume"]) else float(row["volume"])
        bar = existing.get(d)
        if bar is None:
            session.add(PriceBar(stock_id=stock.id, date=d, open=o, high=h,
                                 low=l, close=c, volume=v))
            changed += 1
        elif (bar.open, bar.high, bar.low, bar.close, bar.volume) != (o, h, l, c, v):
            # Adjustment baseline shifted (split/dividend) — rewrite in place.
            bar.open, bar.high, bar.low, bar.close, bar.volume = o, h, l, c, v
            changed += 1
    return changed


def run_ingest() -> None:
    provider = get_provider()
    total = 0
    for symbol in get_universe():
        try:
            with session_scope() as s:
                n = ingest_symbol(s, provider, symbol)
            total += n
            if n:
                log.info("%s: %d bars written/updated", symbol, n)
        except Exception as e:  # one bad ticker shouldn't kill the run
            log.warning("%s failed: %s", symbol, e)
        time.sleep(0.5)  # be polite to Yahoo, avoids rate limiting
    log.info("Ingest complete: %d bars written/updated", total)
