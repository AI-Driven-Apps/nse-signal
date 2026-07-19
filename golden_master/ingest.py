"""Intraday ingestion: fetch recent 5-min bars, upsert into five_min_bars.
Mirrors app/ingest.py's full-refetch-and-upsert pattern (re-fetch a wide
window and upsert rather than append-only, so any provider-side revision is
picked up; cheap at 5-min/1-symbol scale).

As of the 2026-07-11 spec-alignment rework (see docs/golden_master_spec.md),
this is the ONLY ingested intraday feed — the 15-min Heikin-Ashi
reference-line series used to be ingested and stored directly (the now-
vestigial IntradayBar table), but is derived in-memory from these 5-min
bars instead (heikin_ashi.resample_to_15m + to_heikin_ashi), computed fresh
at replay time rather than persisted. No HA-seed-decay concern here (unlike
the old design) since nothing recursive is being stored — a short default
fetch period is fine."""
import logging

from sqlalchemy import select

from .config import SYMBOL
from .intraday_provider import get_provider
from .models import FiveMinBar

log = logging.getLogger("golden_master.ingest")


def ingest_symbol(session, provider, symbol: str, period: str = "5d") -> int:
    df = provider.fetch(symbol, period=period, interval="5m")
    if df.empty:
        return 0

    existing = {
        bar.timestamp: bar
        for bar in session.execute(
            select(FiveMinBar).where(FiveMinBar.symbol == symbol)
        ).scalars()
    }
    changed = 0
    for ts, row in df.iterrows():
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        bar = existing.get(ts)
        if bar is None:
            session.add(FiveMinBar(symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c))
            changed += 1
        elif (bar.open, bar.high, bar.low, bar.close) != (o, h, l, c):
            bar.open, bar.high, bar.low, bar.close = o, h, l, c
            changed += 1
    return changed


def run_ingest(period: str = "5d") -> int:
    from .db import session_scope

    provider = get_provider()
    try:
        with session_scope() as s:
            n = ingest_symbol(s, provider, SYMBOL, period=period)
    except Exception as e:  # yfinance is unofficial and breaks — do not crash the poll loop
        log.warning("%s intraday ingest failed: %s", SYMBOL, e)
        return 0
    if n:
        log.info("Ingest: %d bars written/updated for %s", n, SYMBOL)
    return n
