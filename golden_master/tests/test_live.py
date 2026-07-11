"""Live polling: the upsert/dedup logic directly, and an end-to-end cycle
(network ingestion monkeypatched to a no-op — bars are seeded straight into
intraday_bars, reusing the same hand-verified bull-rejection scenario from
test_backtest.py's _bull_rejection_df)."""
from datetime import date, datetime

import golden_master.live as live_mod
import pytest
from sqlalchemy import select

from golden_master.backtest import Trade
from golden_master.db import session_scope
from golden_master.live import _upsert_live_trades, run_live_cycle
from golden_master.models import GoldenMasterTrade, IntradayBar


def _trade(**overrides) -> Trade:
    base = dict(
        trade_date=date(2024, 6, 4), signal_type="REJECTION", direction="BUY",
        entry_time=datetime(2024, 6, 4, 9, 45), entry_price=118.0,
        sl_price=93.0, target_price=143.0,
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
    resolved = _trade(exit_time=datetime(2024, 6, 4, 10, 0), exit_price=143.0,
                      exit_reason="TARGET_HIT", pnl_points=25.0)
    with session_scope() as s:
        new = _upsert_live_trades(s, [resolved])
    assert new == []  # not "new" — an update to the existing row, no re-alert
    with session_scope() as s:
        row = s.execute(select(GoldenMasterTrade)).scalar_one()
    assert row.exit_reason == "TARGET_HIT"
    assert row.pnl_points == pytest.approx(25.0)


def _seed_bull_rejection_bars(session):
    """The same hand-verified 4-bar sequence from test_backtest.py, inserted
    directly as IntradayBar rows (as if ingest had already run)."""
    bars = [
        ("2024-06-03 15:15", 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0),
        ("2024-06-04 09:15", 101.0, 101.0, 101.0, 101.0, 100.0, 101.0, 100.0, 101.0),
        ("2024-06-04 09:30", 101.0, 108.0, 100.0, 106.0, 100.5, 108.0, 100.0, 103.75),
        ("2024-06-04 09:45", 110.0, 118.0, 109.0, 116.0, 102.125, 118.0, 102.125, 113.25),
    ]
    for ts, o, h, l, c, hao, hah, hal, hac in bars:
        session.add(IntradayBar(
            symbol="^NSEI", timestamp=datetime.fromisoformat(ts),
            open=o, high=h, low=l, close=c,
            ha_open=hao, ha_high=hah, ha_low=hal, ha_close=hac,
        ))


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
    assert len(trades) == 1
    assert trades[0].signal_type == "REJECTION"
    assert trades[0].exit_reason is None  # last day, not yet resolved
    assert len(sent) == 1

    # Re-running with no new bars must not duplicate or re-alert.
    run_live_cycle(now=fixed_now)
    with session_scope() as s:
        assert len(s.execute(select(GoldenMasterTrade)).scalars().all()) == 1
    assert len(sent) == 1
