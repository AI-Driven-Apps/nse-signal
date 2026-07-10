"""Phase 2 §2.3/§2.4 — paper portfolio + snapshots. Spec: docs/phase2_specs.md.
Discord is never touched here; the portfolio reads Signals straight from the DB."""
from datetime import date, timedelta

from sqlalchemy import select

import app.portfolio as pf
from app.config import STARTING_CAPITAL
from app.db import session_scope
from app.models import PortfolioSnapshot, Position, PriceBar, Signal, Stock


def _seed_stock(symbol: str) -> int:
    with session_scope() as s:
        stock = Stock(symbol=symbol)
        s.add(stock)
        s.flush()
        return stock.id


def _add_bar(stock_id: int, d: date, close: float) -> None:
    with session_scope() as s:
        s.add(PriceBar(stock_id=stock_id, date=d, open=close, high=close,
                       low=close, close=close, volume=1e6))


def _add_signal(stock_id: int, d: date, action: str, price: float,
                strategy: str = "sma_cross") -> None:
    with session_scope() as s:
        s.add(Signal(stock_id=stock_id, date=d, action=action,
                     price=price, strategy=strategy))


def test_daily_snapshot_written_after_signals(db):
    """Nightly job writes exactly one portfolio_snapshots row per day;
    re-running the job must not create a second row (idempotent)."""
    d = date(2024, 6, 3)
    sid = _seed_stock("AAA.NS")
    _add_bar(sid, d, 100.0)
    _add_signal(sid, d, "BUY", 100.0)

    pf.run_portfolio(as_of=d)
    pf.run_portfolio(as_of=d)  # re-run: watermark + upsert make it a no-op

    with session_scope() as s:
        snaps = s.execute(select(PortfolioSnapshot)).scalars().all()
        positions = s.execute(select(Position)).scalars().all()
    assert len(snaps) == 1, "exactly one snapshot per day"
    assert len(positions) == 1, "re-run must not open a second position"

    # % of equity sizing: 10% of ₹STARTING_CAPITAL deployed, minus one-side cost.
    snap = snaps[0]
    assert abs(snap.invested - 0.10 * STARTING_CAPITAL) < 1e-6
    assert abs(snap.equity - (STARTING_CAPITAL
                              - 0.10 * STARTING_CAPITAL * pf.COST_PER_SIDE)) < 1e-6


def test_period_stats_aggregate_correctly(db):
    """Weekly/monthly/quarterly/yearly P&L computed from snapshots must
    equal the sum of the daily P&L rows they cover (equity diffs telescope)."""
    equities = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_030_000.0]
    dates = [date(2024, 6, 3) + timedelta(days=i) for i in range(len(equities))]
    with session_scope() as s:
        for d, eq in zip(dates, equities):
            s.add(PortfolioSnapshot(
                date=d, cash=eq, invested=0.0, market_value=0.0, equity=eq,
                realized_pnl_cum=0.0, unrealized_pnl=0.0))

    with session_scope() as s:
        dp = pf.daily_pnl(s)

    # Whole-history P&L == final equity − starting capital.
    assert abs(sum(v for _, v in dp) - (equities[-1] - STARTING_CAPITAL)) < 1e-6
    # Any sub-period P&L == equity_end − equity_at_start_of_period.
    sub = sum(v for _, v in dp[2:])
    assert abs(sub - (equities[3] - equities[1])) < 1e-6


def test_realized_pnl_booked_on_sell(db):
    """A BUY then SELL signal pair opens and closes a Position with
    realized_pnl = (exit·qty − exit_fee) − (entry·qty + entry_fee)."""
    d1, d2 = date(2024, 6, 3), date(2024, 6, 4)
    sid = _seed_stock("BBB.NS")
    _add_bar(sid, d1, 100.0)
    _add_bar(sid, d2, 110.0)

    _add_signal(sid, d1, "BUY", 100.0)
    pf.run_portfolio(as_of=d1)
    _add_signal(sid, d2, "SELL", 110.0)
    pf.run_portfolio(as_of=d2)

    with session_scope() as s:
        pos = s.execute(select(Position)).scalars().one()

    qty = 0.10 * STARTING_CAPITAL / 100.0          # gross / entry_price
    entry_cost = qty * 100.0
    proceeds = qty * 110.0
    expected = (proceeds - proceeds * pf.COST_PER_SIDE) - (
        entry_cost + entry_cost * pf.COST_PER_SIDE)

    assert pos.closed_at is not None
    assert pos.exit_price == 110.0
    assert abs(pos.realized_pnl - expected) < 1e-6
