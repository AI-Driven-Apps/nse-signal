"""
Market breadth (advances/declines/unchanged), a 0-100 strength score, and
near-day-high / near-day-low counts — the numbers behind the reel's
"Live Cash Scanner" strip. Computed from Dhan's own quote batch, chunked to
respect the ~1000-instrument-per-call ceiling, so it stays one data source
rather than mixing in a second unverified feed for numbers this important.
"""

from dataclasses import dataclass

import pandas as pd

CHUNK_SIZE = 1000
NEAR_EXTREME_PCT = 0.5  # within 0.5% of day high/low counts as "near high/low"


@dataclass
class BreadthSnapshot:
    total: int
    advances: int
    declines: int
    unchanged: int
    near_high: int
    near_low: int

    @property
    def strength(self) -> int:
        """
        0-100 score, advances-weighted. 50 = flat market. Same rough shape as
        the reel's "Strength 74 / Strong bullish" gauge — not identical math
        (theirs isn't published), but same read: >65 bullish tilt, <35 bearish.
        """
        if self.total == 0:
            return 50
        return round((self.advances / self.total) * 100)

    @property
    def label(self) -> str:
        s = self.strength
        if s >= 65:
            return "Strong bullish"
        if s >= 55:
            return "Bullish"
        if s <= 35:
            return "Strong bearish"
        if s <= 45:
            return "Bearish"
        return "Neutral"


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def compute_breadth(dhan_client, security_ids: list[str], exchange_segment: str = "NSE_EQ") -> BreadthSnapshot:
    """
    security_ids: Dhan security IDs for the universe to screen (e.g. F&O
    underlyings, or the full NSE_EQ list from instrument_master).
    """
    advances = declines = unchanged = near_high = near_low = 0
    total = 0

    for chunk in _chunks(security_ids, CHUNK_SIZE):
        quote = dhan_client.get_quote({exchange_segment: chunk})
        rows = quote.get(exchange_segment, {})
        for _, row in rows.items():
            ltp = row.get("last_price")
            close = row.get("ohlc", {}).get("close")
            high = row.get("ohlc", {}).get("high")
            low = row.get("ohlc", {}).get("low")
            if ltp is None or close is None:
                continue
            total += 1
            if ltp > close:
                advances += 1
            elif ltp < close:
                declines += 1
            else:
                unchanged += 1
            if high and ltp >= high * (1 - NEAR_EXTREME_PCT / 100):
                near_high += 1
            if low and ltp <= low * (1 + NEAR_EXTREME_PCT / 100):
                near_low += 1

    return BreadthSnapshot(
        total=total, advances=advances, declines=declines, unchanged=unchanged,
        near_high=near_high, near_low=near_low,
    )


def quotes_to_dataframe(dhan_client, security_ids: list[str], exchange_segment: str = "NSE_EQ") -> pd.DataFrame:
    """Flat table (LTP, open, high, low, close, %chg) for the scanner grid."""
    records = []
    for chunk in _chunks(security_ids, CHUNK_SIZE):
        quote = dhan_client.get_quote({exchange_segment: chunk})
        rows = quote.get(exchange_segment, {})
        for sec_id, row in rows.items():
            ohlc = row.get("ohlc", {})
            ltp = row.get("last_price")
            close = ohlc.get("close")
            pct_chg = ((ltp - close) / close * 100) if (ltp and close) else None
            records.append({
                "security_id": sec_id,
                "ltp": ltp,
                "open": ohlc.get("open"),
                "high": ohlc.get("high"),
                "low": ohlc.get("low"),
                "close": close,
                "pct_change": pct_chg,
                "oi": row.get("oi"),
            })
    return pd.DataFrame.from_records(records)
