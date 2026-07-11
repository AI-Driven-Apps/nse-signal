"""Query + simple mutation helpers for the dashboard. Reads are all from
price_bars / portfolio_snapshots / market_mood / backtest_runs / signals /
watchlist — no extra API calls at request time (invariant 4). The few writes
here (watchlist CRUD) are plain single-row DB edits; Discord I/O (signal
resend) stays in app/web/app.py, next to the route that triggers it."""
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from ..backtest import load_prices
from ..config import STARTING_CAPITAL
from ..models import (
    BacktestRun, MarketMood, PortfolioSnapshot, PriceBar, Signal, Stock,
    WatchlistItem,
)
from ..portfolio import daily_pnl
from ..prices import closes_for_symbol
from ..strategies import STRATEGIES, get_strategy
from ..universe import get_universe

SUGGESTION_MOMENTUM_BARS = 126  # ~6 trading months


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


# ---- §2.4 period stats ----

_RESAMPLE_FREQ = {"weekly": "W", "monthly": "ME", "quarterly": "QE", "yearly": "YE"}


def period_stats(session, period: str) -> list[dict]:
    """Daily P&L telescopes (equity[t]-equity[t-1]), so any period's P&L is
    just the sum of the daily rows it covers — see docs/phase2_specs.md and
    app/portfolio.daily_pnl. period: 'daily'|'weekly'|'monthly'|'quarterly'|'yearly'.
    Most recent period first."""
    rows = session.execute(
        select(PortfolioSnapshot.date, PortfolioSnapshot.equity)
        .order_by(PortfolioSnapshot.date.asc())
    ).all()
    if not rows:
        return []
    idx = pd.to_datetime([r.date for r in rows])
    equity = pd.Series([r.equity for r in rows], index=idx)
    pnl = equity.diff()
    pnl.iloc[0] = equity.iloc[0] - STARTING_CAPITAL

    if period == "daily":
        end_equity, period_pnl = equity, pnl
    else:
        freq = _RESAMPLE_FREQ[period]
        end_equity = equity.resample(freq).last().dropna()
        period_pnl = pnl.resample(freq).sum().reindex(end_equity.index)

    start_equity = end_equity - period_pnl
    out = []
    for idx_val in end_equity.index:
        se, ee, p = float(start_equity[idx_val]), float(end_equity[idx_val]), float(period_pnl[idx_val])
        out.append({
            "period": idx_val.date().isoformat(),
            "start_equity": se, "end_equity": ee, "pnl": p,
            "return_pct": (p / se * 100.0) if se else 0.0,
        })
    out.reverse()
    return out


# ---- §2.5 strategy switcher ----

def strategy_metrics(session) -> dict[str, dict | None]:
    """Per strategy, aggregate metrics from its most recent backtest_universe()
    run — identified by the max end_date for that strategy (all symbols in one
    run share the same end_date, since they're backtested off the same day's
    price history). None if the strategy has never been backtested."""
    out: dict[str, dict | None] = {}
    for name in STRATEGIES:
        latest_end = session.execute(
            select(func.max(BacktestRun.end_date)).where(BacktestRun.strategy == name)
        ).scalar()
        if latest_end is None:
            out[name] = None
            continue
        rows = session.execute(
            select(BacktestRun).where(
                BacktestRun.strategy == name, BacktestRun.end_date == latest_end)
        ).scalars().all()
        n = len(rows)
        out[name] = {
            "as_of": latest_end.isoformat(),
            "n_symbols": n,
            "avg_cagr": sum(r.cagr for r in rows) / n,
            "avg_max_dd": sum(r.max_drawdown for r in rows) / n,
            "avg_sharpe": sum(r.sharpe for r in rows) / n,
            "avg_win_rate": sum(r.win_rate for r in rows) / n,
        }
    return out


# ---- §2.6 watchlist + suggestions ----

def known_symbols(session) -> list[str]:
    return list(session.execute(select(Stock.symbol).order_by(Stock.symbol)).scalars())


def _position_state(session, symbol: str, active_strategy: str) -> str:
    df = load_prices(session, symbol)
    if df.empty:
        return "unknown"
    pos = get_strategy(active_strategy).positions(df)
    return "holding" if float(pos.iloc[-1]) > 0 else "flat"


def watchlist_rows(session, active_strategy: str) -> list[dict]:
    items = session.execute(
        select(WatchlistItem).order_by(WatchlistItem.starred.desc(), WatchlistItem.added_at.desc())
    ).scalars().all()
    out = []
    for item in items:
        stock = session.get(Stock, item.stock_id)
        bars = session.execute(
            select(PriceBar.close).where(PriceBar.stock_id == item.stock_id)
            .order_by(PriceBar.date.desc()).limit(2)
        ).scalars().all()
        last_close = bars[0] if bars else None
        pct_change = (bars[0] - bars[1]) / bars[1] * 100.0 if len(bars) == 2 and bars[1] else None
        out.append({
            "id": item.id,
            "symbol": stock.symbol.replace(".NS", ""),
            "starred": item.starred,
            "last_close": last_close,
            "pct_change": pct_change,
            "position_state": _position_state(session, stock.symbol, active_strategy),
        })
    return out


def _minmax_normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def suggested_stocks(session, active_strategy: str, as_of: date | None = None,
                     top_n: int = 10) -> list[dict]:
    """Rank the universe by 0.5 * 6-month momentum + 0.5 * latest backtest
    Sharpe for the active strategy. Both terms are min-max normalized to
    [0,1] across the ranked set before blending — momentum is a % return and
    Sharpe a unitless ratio, so blending them raw would let whichever has the
    larger numeric range dominate. Needs a backtest to have been run for the
    active strategy; returns [] otherwise."""
    as_of = as_of or date.today()

    latest_end = session.execute(
        select(func.max(BacktestRun.end_date)).where(BacktestRun.strategy == active_strategy)
    ).scalar()
    if latest_end is None:
        return []
    sharpe_by_symbol = {
        r.symbol: r.sharpe for r in session.execute(
            select(BacktestRun).where(
                BacktestRun.strategy == active_strategy, BacktestRun.end_date == latest_end)
        ).scalars()
    }

    momentum_by_symbol = {}
    for symbol in get_universe():
        if symbol not in sharpe_by_symbol:
            continue
        closes = closes_for_symbol(session, symbol, as_of, SUGGESTION_MOMENTUM_BARS + 1)
        if len(closes) <= SUGGESTION_MOMENTUM_BARS or closes.iloc[0] == 0:
            continue
        momentum_by_symbol[symbol] = float(closes.iloc[-1] / closes.iloc[0] - 1)

    symbols = [s for s in momentum_by_symbol if s in sharpe_by_symbol]
    if not symbols:
        return []
    mom_norm = _minmax_normalize([momentum_by_symbol[s] for s in symbols])
    sharpe_norm = _minmax_normalize([sharpe_by_symbol[s] for s in symbols])

    scored = [{
        "symbol": sym.replace(".NS", ""),
        "momentum_pct": momentum_by_symbol[sym] * 100.0,
        "sharpe": sharpe_by_symbol[sym],
        "score": 0.5 * mom_norm[i] + 0.5 * sharpe_norm[i],
    } for i, sym in enumerate(symbols)]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_n]


def add_watchlist_item(session, symbol: str) -> WatchlistItem | None:
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        return None
    existing = session.execute(
        select(WatchlistItem).where(WatchlistItem.stock_id == stock.id)
    ).scalar_one_or_none()
    if existing:
        return existing
    item = WatchlistItem(stock_id=stock.id)
    session.add(item)
    session.flush()
    return item


def remove_watchlist_item(session, item_id: int) -> None:
    item = session.get(WatchlistItem, item_id)
    if item:
        session.delete(item)


def toggle_star(session, item_id: int) -> None:
    item = session.get(WatchlistItem, item_id)
    if item:
        item.starred = not item.starred


# ---- §2.7 signals page ----

def recent_signals(session, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(Signal, Stock.symbol).join(Stock, Signal.stock_id == Stock.id)
        .order_by(Signal.date.desc(), Signal.id.desc())
        .limit(limit)
    ).all()
    return [{
        "id": sig.id, "date": sig.date.isoformat(), "symbol": symbol.replace(".NS", ""),
        "action": sig.action, "price": sig.price, "strategy": sig.strategy,
        "sent_to_discord": sig.sent_to_discord,
    } for sig, symbol in rows]
