"""Intraday ingestion: fetch recent 15-min bars, compute Heikin-Ashi, upsert
into intraday_bars. Mirrors app/ingest.py's full-refetch-and-upsert pattern
(same reasoning: re-fetch a wide window and upsert rather than append-only,
so any provider-side revision is picked up; cheap at 15-min/1-symbol scale).

Heikin-Ashi is a recursive, continuous transform (see heikin_ashi.py) — its
"seed" bar is always the first bar of whatever window got fetched. Fetching
a full month (not just the last few days) each time keeps that seed well
before the bars that actually matter for live trading, so its influence has
decayed to negligible by the time it reaches today's data (each step halves
the seed's weight). A short fetch window right before "today" would put the
seed too close to the bars we care about."""
import logging

from sqlalchemy import select

from .config import SYMBOL
from .heikin_ashi import to_heikin_ashi
from .intraday_provider import get_provider
from .models import IntradayBar

log = logging.getLogger("golden_master.ingest")


def ingest_symbol(session, provider, symbol: str, period: str = "1mo") -> int:
    df = provider.fetch(symbol, period=period, interval="15m")
    if df.empty:
        return 0
    ha = to_heikin_ashi(df)

    existing = {
        bar.timestamp: bar
        for bar in session.execute(
            select(IntradayBar).where(IntradayBar.symbol == symbol)
        ).scalars()
    }
    changed = 0
    for ts, row in ha.iterrows():
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        hao, hah, hal, hac = (float(row["ha_open"]), float(row["ha_high"]),
                              float(row["ha_low"]), float(row["ha_close"]))
        bar = existing.get(ts)
        if bar is None:
            session.add(IntradayBar(
                symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c,
                ha_open=hao, ha_high=hah, ha_low=hal, ha_close=hac,
            ))
            changed += 1
        elif (bar.open, bar.high, bar.low, bar.close) != (o, h, l, c):
            bar.open, bar.high, bar.low, bar.close = o, h, l, c
            bar.ha_open, bar.ha_high, bar.ha_low, bar.ha_close = hao, hah, hal, hac
            changed += 1
    return changed


def run_ingest(period: str = "1mo") -> int:
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
