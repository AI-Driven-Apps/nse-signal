"""§2.4-2.7 dashboard query/mutation helpers (app/web/data.py)."""
from datetime import date, timedelta

import pytest

from app.config import STARTING_CAPITAL
from app.db import session_scope
from app.models import BacktestRun, PortfolioSnapshot, PriceBar, Stock
from app.web.data import (
    _minmax_normalize, add_watchlist_item, period_stats, remove_watchlist_item,
    strategy_metrics, suggested_stocks, toggle_star, watchlist_rows,
)


def _add_snapshot(session, d: date, equity: float) -> None:
    session.add(PortfolioSnapshot(date=d, cash=equity, invested=0.0, market_value=0.0,
                                  equity=equity, realized_pnl_cum=0.0, unrealized_pnl=0.0))


def test_period_stats_daily_matches_equity_diffs(db):
    equities = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_030_000.0]
    with session_scope() as s:
        for i, eq in enumerate(equities):
            _add_snapshot(s, date(2024, 6, 3) + timedelta(days=i), eq)

    with session_scope() as s:
        rows = period_stats(s, "daily")

    assert len(rows) == 4
    assert rows[0]["period"] == (date(2024, 6, 3) + timedelta(days=3)).isoformat()  # most recent first
    assert rows[0]["pnl"] == pytest.approx(equities[3] - equities[2])
    assert rows[-1]["pnl"] == pytest.approx(equities[0] - STARTING_CAPITAL)


def test_period_stats_weekly_sums_to_total(db):
    equities = [1_000_000.0 * (1.001 ** i) for i in range(40)]
    with session_scope() as s:
        for i, eq in enumerate(equities):
            _add_snapshot(s, date(2024, 6, 3) + timedelta(days=i), eq)

    with session_scope() as s:
        weekly = period_stats(s, "weekly")
        daily = period_stats(s, "daily")

    assert sum(r["pnl"] for r in weekly) == pytest.approx(sum(r["pnl"] for r in daily))
    assert sum(r["pnl"] for r in weekly) == pytest.approx(equities[-1] - STARTING_CAPITAL)


def test_strategy_metrics_uses_latest_run_only(db):
    with session_scope() as s:
        s.add(BacktestRun(strategy="sma_cross", symbol="AAA.NS",
                          start_date=date(2024, 1, 1), end_date=date(2024, 5, 1),
                          cagr=0.05, max_drawdown=-0.1, sharpe=0.5, win_rate=0.4,
                          n_trades=3, final_equity=1.05))
        s.add(BacktestRun(strategy="sma_cross", symbol="AAA.NS",
                          start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
                          cagr=0.10, max_drawdown=-0.2, sharpe=1.0, win_rate=0.6,
                          n_trades=5, final_equity=1.10))
        s.add(BacktestRun(strategy="sma_cross", symbol="BBB.NS",
                          start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
                          cagr=0.20, max_drawdown=-0.3, sharpe=2.0, win_rate=0.8,
                          n_trades=7, final_equity=1.20))

    with session_scope() as s:
        m = strategy_metrics(s)

    assert m["rsi_reversion"] is None
    sma = m["sma_cross"]
    assert sma["n_symbols"] == 2
    assert sma["as_of"] == date(2024, 6, 1).isoformat()
    assert sma["avg_cagr"] == pytest.approx((0.10 + 0.20) / 2)
    assert sma["avg_sharpe"] == pytest.approx((1.0 + 2.0) / 2)


def test_minmax_normalize():
    assert _minmax_normalize([1.0, 2.0, 3.0]) == pytest.approx([0.0, 0.5, 1.0])
    assert _minmax_normalize([5.0, 5.0]) == pytest.approx([0.5, 0.5])


def _seed_symbol_for_suggestions(session, symbol, start_px, end_px, sharpe, as_of):
    st = Stock(symbol=symbol)
    session.add(st)
    session.flush()
    for i in range(128):
        d = date(2024, 1, 1) + timedelta(days=i)
        px = start_px + (end_px - start_px) * i / 127
        session.add(PriceBar(stock_id=st.id, date=d, open=px, high=px, low=px, close=px, volume=1e6))
    session.add(BacktestRun(strategy="sma_cross", symbol=symbol, start_date=date(2024, 1, 1),
                            end_date=as_of, cagr=0.1, max_drawdown=-0.1, sharpe=sharpe,
                            win_rate=0.5, n_trades=2, final_equity=1.1))


def test_suggested_stocks_ranks_by_blended_score(db, monkeypatch):
    as_of = date(2024, 1, 1) + timedelta(days=127)
    monkeypatch.setattr("app.web.data.get_universe",
                        lambda: ["AAA.NS", "BBB.NS", "CCC.NS"])
    with session_scope() as s:
        _seed_symbol_for_suggestions(s, "AAA.NS", 100.0, 100.0, 0.0, as_of)   # worst both
        _seed_symbol_for_suggestions(s, "BBB.NS", 100.0, 115.0, 1.5, as_of)   # middle
        _seed_symbol_for_suggestions(s, "CCC.NS", 100.0, 140.0, 3.0, as_of)   # best both

    with session_scope() as s:
        result = suggested_stocks(s, "sma_cross", as_of=as_of)

    assert [r["symbol"] for r in result] == ["CCC", "BBB", "AAA"]
    assert result[0]["score"] == pytest.approx(1.0)
    assert result[-1]["score"] == pytest.approx(0.0)


def test_suggested_stocks_empty_without_backtest(db):
    with session_scope() as s:
        assert suggested_stocks(s, "sma_cross") == []


def test_watchlist_add_star_remove(db):
    with session_scope() as s:
        s.add(Stock(symbol="AAA.NS"))

    with session_scope() as s:
        item = add_watchlist_item(s, "AAA.NS")
        item_id = item.id

    with session_scope() as s:
        rows = watchlist_rows(s, "sma_cross")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["starred"] is False

    with session_scope() as s:
        toggle_star(s, item_id)
    with session_scope() as s:
        assert watchlist_rows(s, "sma_cross")[0]["starred"] is True

    with session_scope() as s:
        remove_watchlist_item(s, item_id)
    with session_scope() as s:
        assert watchlist_rows(s, "sma_cross") == []


def test_add_watchlist_item_unknown_symbol_returns_none(db):
    with session_scope() as s:
        assert add_watchlist_item(s, "NOPE.NS") is None
