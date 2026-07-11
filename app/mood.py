"""India fear & greed composite (§2.2) — spec: docs/phase2_specs.md.

Composite of three components, each normalized to 0-100 (higher = greedier):
  - VIX (inverted) vs its trailing VIX_WINDOW-session range
  - Nifty close vs its MOM_SMA-day SMA (momentum)
  - universe advance/decline ratio over the last AD_WINDOW sessions
Equal-weighted mean of whichever components have enough data; if fewer than
two are available that day, no row is written. Bands: Fear <40, Neutral
40-60, Greed >60. Pure scoring functions take plain pandas Series so they're
unit-testable without a DB; compute_and_store_mood() is the DB-facing wrapper
that reads price_bars and upserts market_mood (idempotent by date)."""
import logging
from datetime import date

import pandas as pd
from sqlalchemy import select

from .config import (
    AD_WINDOW, MOM_BAND, MOM_SMA, NIFTY_SYMBOL, VIX_SYMBOL, VIX_WINDOW,
    W_AD, W_MOM, W_VIX,
)
from .models import MarketMood, PriceBar, Stock
from .universe import get_universe

log = logging.getLogger("mood")


def vix_score(vix_closes: pd.Series, window: int = VIX_WINDOW) -> float | None:
    """Lower VIX vs its trailing range = higher (greedier) score."""
    if vix_closes.empty:
        return None
    window_series = vix_closes.iloc[-window:] if len(vix_closes) >= window else vix_closes
    vix_t = float(vix_closes.iloc[-1])
    lo, hi = float(window_series.min()), float(window_series.max())
    if hi == lo:
        return 50.0
    return float(min(100.0, max(0.0, 100.0 * (1 - (vix_t - lo) / (hi - lo)))))


def momentum_score(nifty_closes: pd.Series, sma_period: int = MOM_SMA,
                    band: float = MOM_BAND) -> float | None:
    """Nifty close vs its SMA; +/-band deviation maps to the 0/100 ends."""
    if len(nifty_closes) < sma_period:
        return None
    sma = float(nifty_closes.rolling(sma_period).mean().iloc[-1])
    if pd.isna(sma) or sma == 0:
        return None
    dev = (float(nifty_closes.iloc[-1]) - sma) / sma
    return float(min(100.0, max(0.0, 50.0 + (dev / band) * 50.0)))


def adv_decline_score(universe_closes: dict[str, pd.Series],
                       window: int = AD_WINDOW) -> float | None:
    """Fraction of the universe up over the trailing `window` sessions,
    scaled to 0-100. Symbols with insufficient history are skipped."""
    advancers = decliners = 0
    for closes in universe_closes.values():
        if len(closes) < window + 1:
            continue
        now, then = float(closes.iloc[-1]), float(closes.iloc[-1 - window])
        if now > then:
            advancers += 1
        elif now < then:
            decliners += 1
    total = advancers + decliners
    if total == 0:
        return None
    return 100.0 * advancers / total


def composite_and_label(scores: dict[str, float | None],
                        weights: dict[str, float]) -> tuple[float, str] | None:
    """Equal/weighted mean over available (non-None) components, renormalized.
    None if fewer than 2 components are available."""
    available = {k: v for k, v in scores.items() if v is not None}
    if len(available) < 2:
        return None
    wsum = sum(weights[k] for k in available)
    comp = sum(available[k] * weights[k] for k in available) / wsum
    label = "Fear" if comp < 40 else ("Greed" if comp > 60 else "Neutral")
    return comp, label


def _closes_for_symbol(session, symbol: str, as_of: date, lookback_bars: int) -> pd.Series:
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


def compute_and_store_mood(session, as_of: date | None = None) -> MarketMood | None:
    as_of = as_of or date.today()

    nifty = _closes_for_symbol(session, NIFTY_SYMBOL, as_of, MOM_SMA + 5)
    vix = _closes_for_symbol(session, VIX_SYMBOL, as_of, VIX_WINDOW + 5)
    universe_closes = {
        sym: _closes_for_symbol(session, sym, as_of, AD_WINDOW + 1)
        for sym in get_universe()
    }

    scores = {
        "vix": vix_score(vix),
        "momentum": momentum_score(nifty),
        "adv_decline": adv_decline_score(universe_closes),
    }
    weights = {"vix": W_VIX, "momentum": W_MOM, "adv_decline": W_AD}
    result = composite_and_label(scores, weights)
    if result is None:
        log.warning("Fewer than 2 mood components available for %s; skipping.", as_of)
        return None
    comp, label = result

    mood = session.get(MarketMood, as_of)
    if mood is None:
        mood = MarketMood(date=as_of)
        session.add(mood)
    mood.vix_score = scores["vix"]
    mood.momentum_score = scores["momentum"]
    mood.adv_decline_score = scores["adv_decline"]
    mood.composite = comp
    mood.label = label
    log.info("Mood %s: composite=%.1f (%s) vix=%s mom=%s ad=%s",
             as_of, comp, label, scores["vix"], scores["momentum"], scores["adv_decline"])
    return mood
