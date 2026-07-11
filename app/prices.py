"""Shared price-series query helper — from price_bars only, no API calls
(invariant 4). Used by app/mood.py and app/web/data.py (watchlist, suggestions)."""
from datetime import date

import pandas as pd
from sqlalchemy import select

from .models import PriceBar, Stock


def closes_for_symbol(session, symbol: str, as_of: date, lookback_bars: int) -> pd.Series:
    """Ascending-date close series for `symbol`, up to `lookback_bars` bars
    ending on/before `as_of`. Empty Series if the symbol or bars don't exist."""
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        return pd.Series(dtype=float)
    rows = session.execute(
        select(PriceBar.date, PriceBar.close)
        .where(PriceBar.stock_id == stock.id, PriceBar.date <= as_of)
        .order_by(PriceBar.date.desc())
        .limit(lookback_bars)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    rows.reverse()  # ascending date
    return pd.Series([r.close for r in rows], index=[r.date for r in rows])
