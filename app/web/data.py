"""Read-only query helpers for the dashboard (§2.2). All from price_bars /
portfolio_snapshots / market_mood — no extra API calls at request time."""
from sqlalchemy import select

from ..models import MarketMood, PortfolioSnapshot, PriceBar, Stock
from ..universe import get_universe


def latest_snapshot(session) -> PortfolioSnapshot | None:
    return session.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.date.desc()).limit(1)
    ).scalar_one_or_none()


def latest_mood(session) -> MarketMood | None:
    return session.execute(
        select(MarketMood).order_by(MarketMood.date.desc()).limit(1)
    ).scalar_one_or_none()


def equity_curve(session, limit: int = 180) -> list[tuple[str, float]]:
    rows = session.execute(
        select(PortfolioSnapshot.date, PortfolioSnapshot.equity)
        .order_by(PortfolioSnapshot.date.desc())
        .limit(limit)
    ).all()
    rows.reverse()
    return [(r.date.isoformat(), r.equity) for r in rows]


def top_movers(session, n: int = 5) -> tuple[list[dict], list[dict]]:
    """Top N gainers and losers by 1-day % change across the universe."""
    movers = []
    for symbol in get_universe():
        stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        if not stock:
            continue
        bars = session.execute(
            select(PriceBar.close).where(PriceBar.stock_id == stock.id)
            .order_by(PriceBar.date.desc()).limit(2)
        ).scalars().all()
        if len(bars) < 2 or bars[1] == 0:
            continue
        last, prev = bars[0], bars[1]
        movers.append({
            "symbol": symbol.replace(".NS", ""),
            "last_close": last,
            "pct_change": (last - prev) / prev * 100.0,
        })
    movers.sort(key=lambda m: m["pct_change"], reverse=True)
    gainers = movers[:n]
    losers = sorted(movers[-n:], key=lambda m: m["pct_change"]) if movers else []
    return gainers, losers
