"""Paper portfolio (Phase 2 §2.3/§2.4) — spec: docs/phase2_specs.md.

Applies the active strategy's signals to a single-user paper book and snapshots
equity daily. Design decisions: % of current equity sizing; forward-only
snapshot history. Fills at Signal.price (the decision-bar close, which matches
the backtester's shift(1)); COST_PER_SIDE charged on both entry and exit;
long-only; at most one open position per symbol.

Idempotency (invariant 6): a settings watermark ('paper_cursor' = highest
Signal.id already applied) means each run processes only new signals, so
re-running never double-trades and a completed round-trip is never re-opened.
"""
import logging
from datetime import date, datetime

from sqlalchemy import func, select

from .backtest import COST_PER_SIDE
from .config import ALLOC_PCT, MAX_POSITIONS, STARTING_CAPITAL
from .db import get_setting, session_scope, set_setting
from .models import PortfolioSnapshot, Position, PriceBar, Signal

log = logging.getLogger("portfolio")

CASH_KEY = "paper_cash"
CURSOR_KEY = "paper_cursor"  # highest Signal.id applied to the book


def _get_cash(session) -> float:
    return float(get_setting(session, CASH_KEY, str(STARTING_CAPITAL)))


def _latest_close(session, stock_id: int, on_or_before: date) -> float | None:
    """Most recent stored close for a stock on/before a date (carry-forward
    over holidays/gaps). From the DB only — no API call (invariant 4)."""
    return session.execute(
        select(PriceBar.close)
        .where(PriceBar.stock_id == stock_id, PriceBar.date <= on_or_before)
        .order_by(PriceBar.date.desc())
        .limit(1)
    ).scalar()


def _open_positions(session) -> list[Position]:
    return session.execute(
        select(Position).where(Position.closed_at.is_(None))
    ).scalars().all()


def _open_position_for(session, stock_id: int) -> Position | None:
    return session.execute(
        select(Position).where(
            Position.stock_id == stock_id, Position.closed_at.is_(None)
        )
    ).scalar_one_or_none()


def _mark_to_market(session, positions, on_or_before: date) -> float:
    total = 0.0
    for p in positions:
        px = _latest_close(session, p.stock_id, on_or_before) or p.avg_price
        total += p.qty * px
    return total


def apply_signals(session, strategy_name: str, as_of: date | None = None) -> int:
    """Apply the strategy's not-yet-applied signals to the paper book in
    chronological order (SELL before BUY within a day so freed cash redeploys).
    Returns the number of fills. Idempotent via the paper_cursor watermark."""
    as_of = as_of or date.today()
    cash = _get_cash(session)
    cursor = int(get_setting(session, CURSOR_KEY, "0"))

    signals = session.execute(
        select(Signal)
        .where(Signal.strategy == strategy_name, Signal.id > cursor)
        .order_by(Signal.date.asc(), Signal.action.desc())  # SELL ('S') before BUY ('B')
    ).scalars().all()

    fills = 0
    max_id = cursor
    for sig in signals:
        max_id = max(max_id, sig.id)
        pos = _open_position_for(session, sig.stock_id)

        if sig.action == "SELL":
            if pos is None:
                continue  # already flat
            proceeds = pos.qty * sig.price
            exit_fee = proceeds * COST_PER_SIDE
            entry_cost = pos.qty * pos.avg_price
            entry_fee = entry_cost * COST_PER_SIDE
            cash += proceeds - exit_fee
            pos.closed_at = datetime.utcnow()
            pos.exit_price = sig.price
            pos.realized_pnl = (proceeds - exit_fee) - (entry_cost + entry_fee)
            fills += 1
        else:  # BUY
            if pos is not None:
                continue  # already holding this symbol
            open_pos = _open_positions(session)
            if len(open_pos) >= MAX_POSITIONS:
                log.info("skip BUY stock=%s: at MAX_POSITIONS (%d)",
                         sig.stock_id, MAX_POSITIONS)
                continue
            equity = cash + _mark_to_market(session, open_pos, as_of)
            gross = ALLOC_PCT * equity
            fee = gross * COST_PER_SIDE
            if cash < gross + fee:
                log.info("skip BUY stock=%s: insufficient cash", sig.stock_id)
                continue
            cash -= gross + fee
            session.add(Position(
                stock_id=sig.stock_id, qty=gross / sig.price,
                avg_price=sig.price, opened_at=datetime.utcnow(),
            ))
            fills += 1

    set_setting(session, CASH_KEY, repr(cash))
    set_setting(session, CURSOR_KEY, str(max_id))
    return fills


def write_snapshot(session, on: date | None = None) -> PortfolioSnapshot:
    """Upsert one snapshot for the given day. Idempotent: re-running rewrites
    the same-dated row rather than adding a second."""
    on = on or date.today()
    cash = _get_cash(session)
    open_pos = _open_positions(session)
    invested = sum(p.qty * p.avg_price for p in open_pos)
    market_value = _mark_to_market(session, open_pos, on)
    realized_cum = session.execute(
        select(func.coalesce(func.sum(Position.realized_pnl), 0.0))
    ).scalar() or 0.0

    snap = session.get(PortfolioSnapshot, on)
    if snap is None:
        snap = PortfolioSnapshot(date=on)
        session.add(snap)
    snap.cash = cash
    snap.invested = invested
    snap.market_value = market_value
    snap.equity = cash + market_value
    snap.realized_pnl_cum = float(realized_cum)
    snap.unrealized_pnl = market_value - invested
    return snap


def run_portfolio(as_of: date | None = None) -> None:
    """Nightly step after signals: apply new signals, then snapshot."""
    with session_scope() as s:
        strategy_name = get_setting(s, "active_strategy", "sma_cross")
        fills = apply_signals(s, strategy_name, as_of)
        snap = write_snapshot(s, as_of)
        log.info("Portfolio: %d fill(s); equity=%.2f cash=%.2f invested=%.2f "
                 "realized=%.2f", fills, snap.equity, snap.cash,
                 snap.invested, snap.realized_pnl_cum)


def daily_pnl(session) -> list[tuple[date, float]]:
    """Per-day P&L from snapshots (day 1 vs STARTING_CAPITAL, then equity
    diffs). Telescopes, so any period's P&L is the sum of its daily rows —
    the basis for the §2.4 period-stats tabs."""
    snaps = session.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.date.asc())
    ).scalars().all()
    out: list[tuple[date, float]] = []
    prev = STARTING_CAPITAL
    for snap in snaps:
        out.append((snap.date, snap.equity - prev))
        prev = snap.equity
    return out
