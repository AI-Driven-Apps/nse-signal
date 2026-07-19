"""Live polling: the upsert/dedup logic directly, and an end-to-end cycle
(network ingestion monkeypatched to a no-op — bars are seeded straight into
five_min_bars, reusing the same hand-verified bull-rejection scenario from
test_backtest.py's _bull_rejection_df, at 5-min granularity)."""
from datetime import date, datetime

import golden_master.live as live_mod
import pytest
from sqlalchemy import select

from golden_master.backtest import Trade
from golden_master.db import session_scope
from golden_master.live import _upsert_live_trades, run_live_cycle
from golden_master.models import FiveMinBar, GoldenMasterDailyLevels, GoldenMasterTrade


def _trade(**overrides) -> Trade:
    base = dict(
        trade_date=date(2024, 6, 4), signal_type="REJECTION", direction="BUY",
        entry_time=datetime(2024, 6, 4, 9, 40), entry_price=108.0,
        sl_price=83.0, target_price=133.0,
        exit_time=None, exit_price=None, exit_reason=None, pnl_points=None,
        ce_strike=0.0, pe_strike=200.0,
    )
    base.update(overrides)
    return Trade(**base)


def test_upsert_inserts_new_open_trade(db):
    with session_scope() as s:
        new = _upsert_live_trades(s, [_trade()])
    assert len(new) == 1
    with session_scope() as s:
        row = s.execute(select(GoldenMasterTrade)).scalar_one()
    assert row.source == "live"
    assert row.exit_reason is None


def test_upsert_does_not_duplicate_or_realert_on_replay(db):
    with session_scope() as s:
        _upsert_live_trades(s, [_trade()])
    with session_scope() as s:
        new_again = _upsert_live_trades(s, [_trade()])  # same key, still open
    assert new_again == []
    with session_scope() as s:
        assert len(s.execute(select(GoldenMasterTrade)).scalars().all()) == 1


def test_upsert_resolves_a_previously_open_trade(db):
    with session_scope() as s:
        _upsert_live_trades(s, [_trade()])
    resolved = _trade(exit_time=datetime(2024, 6, 4, 10, 0), exit_price=133.0,
                      exit_reason="TARGET_HIT", pnl_points=25.0)
    with session_scope() as s:
        new = _upsert_live_trades(s, [resolved])
    assert new == []  # not "new" — an update to the existing row, no re-alert
    with session_scope() as s:
        row = s.execute(select(GoldenMasterTrade)).scalar_one()
    assert row.exit_reason == "TARGET_HIT"
    assert row.pnl_points == pytest.approx(25.0)


def _seed_bull_rejection_bars(session):
    """The same hand-verified 5-min sequence from test_backtest.py's
    _bull_rejection_df base rows, inserted directly as FiveMinBar rows (as
    if ingest had already run). Entry fires at 09:40 (raw high=108)."""
    bars = [
        ("2024-06-03 15:15", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-03 15:20", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-03 15:25", 100.0, 100.0, 100.0, 100.0),
        ("2024-06-04 09:15", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:20", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:25", 101.0, 101.0, 101.0, 101.0),
        ("2024-06-04 09:30", 101.0, 103.0, 100.0, 102.0),
        ("2024-06-04 09:35", 102.0, 106.0, 101.0, 104.0),
        ("2024-06-04 09:40", 104.0, 108.0, 103.0, 106.0),
    ]
    for ts, o, h, l, c in bars:
        session.add(FiveMinBar(symbol="^NSEI", timestamp=datetime.fromisoformat(ts),
                               open=o, high=h, low=l, close=c))


def test_run_live_cycle_opens_trade_and_alerts_once(db, monkeypatch):
    monkeypatch.setattr(live_mod, "run_ingest", lambda: 0)
    sent = []
    monkeypatch.setattr(live_mod, "send_golden_master_signal",
                        lambda *a, **k: sent.append(a) or True)

    with session_scope() as s:
        _seed_bull_rejection_bars(s)

    fixed_now = datetime(2024, 6, 4, 10, 0)
    run_live_cycle(now=fixed_now)

    with session_scope() as s:
        trades = s.execute(select(GoldenMasterTrade)).scalars().all()
        levels = s.execute(select(GoldenMasterDailyLevels)).scalars().all()
    assert len(trades) == 1
    assert trades[0].signal_type == "REJECTION"
    assert trades[0].exit_reason is None  # last day, not yet resolved
    assert len(sent) == 1
    assert len(levels) == 2  # prev day + today, snapshotted alongside the trade

    # Re-running with no new bars must not duplicate or re-alert.
    run_live_cycle(now=fixed_now)
    with session_scope() as s:
        assert len(s.execute(select(GoldenMasterTrade)).scalars().all()) == 1
    assert len(sent) == 1
