"""Incremental EOD ingestion: fetch only bars newer than what's in the DB."""
import logging
import time
from datetime import date, timedelta

from sqlalchemy import func, select

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
    last: date | None = session.execute(
        select(func.max(PriceBar.date)).where(PriceBar.stock_id == stock.id)
    ).scalar()
    start = (last + timedelta(days=1)) if last else date.today() - timedelta(days=BACKFILL_DAYS)
    if start > date.today():
        return 0

    df = provider.fetch(symbol, start=start)
    if df.empty:
        return 0

    existing = {
        d for (d,) in session.execute(
            select(PriceBar.date).where(
                PriceBar.stock_id == stock.id, PriceBar.date >= start
            )
        )
    }
    n = 0
    for d, row in df.iterrows():
        if d in existing:
            continue
        session.add(PriceBar(
            stock_id=stock.id, date=d,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"] or 0),
        ))
        n += 1
    return n


def run_ingest() -> None:
    provider = get_provider()
    total = 0
    for symbol in get_universe():
        try:
            with session_scope() as s:
                n = ingest_symbol(s, provider, symbol)
            total += n
            if n:
                log.info("%s: +%d bars", symbol, n)
        except Exception as e:  # one bad ticker shouldn't kill the run
            log.warning("%s failed: %s", symbol, e)
        time.sleep(0.5)  # be polite to Yahoo, avoids rate limiting
    log.info("Ingest complete: %d new bars", total)
