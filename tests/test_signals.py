"""Invariant 6: idempotent jobs. Signal scan must detect last-bar transitions,
record them once, and be safe to re-run. Discord is monkeypatched — no network."""
import pandas as pd
from sqlalchemy import select

import app.signals as signals_mod
from app.db import session_scope
from app.models import PriceBar, Signal, Stock
from app.strategies.base import Strategy


class FlipToLongOnLastBar(Strategy):
    name = "test_flip"

    def positions(self, df):
        pos = pd.Series(0.0, index=df.index)
        pos.iloc[-1] = 1.0
        return pos


def _seed_symbol(symbol: str, df: pd.DataFrame):
    with session_scope() as s:
        stock = Stock(symbol=symbol)
        s.add(stock)
        s.flush()
        for d, row in df.iterrows():
            s.add(PriceBar(stock_id=stock.id, date=d, open=row["open"],
                           high=row["high"], low=row["low"],
                           close=row["close"], volume=row["volume"]))


def test_buy_signal_detected_and_deduped(db, trending_df, monkeypatch):
    sent = []
    monkeypatch.setattr(signals_mod, "send_signal",
                        lambda *a, **k: sent.append(a) or True)
    _seed_symbol("FAKE.NS", trending_df)

    strat = FlipToLongOnLastBar()
    signals_mod._scan_symbol("FAKE.NS", strat, strat.name)
    signals_mod._scan_symbol("FAKE.NS", strat, strat.name)  # re-run: no dup

    with session_scope() as s:
        rows = s.execute(select(Signal)).scalars().all()
    assert len(rows) == 1, "re-running the scan must not duplicate signals"
    assert rows[0].action == "BUY"
    assert rows[0].sent_to_discord is True
    assert len(sent) == 1, "Discord must be notified exactly once"


def test_no_signal_without_transition(db, trending_df, monkeypatch):
    monkeypatch.setattr(signals_mod, "send_signal", lambda *a, **k: True)
    _seed_symbol("FLAT.NS", trending_df)

    class AlwaysFlat(Strategy):
        name = "flat"

        def positions(self, df):
            return pd.Series(0.0, index=df.index)

    signals_mod._scan_symbol("FLAT.NS", AlwaysFlat(), "flat")
    with session_scope() as s:
        assert s.execute(select(Signal)).scalars().first() is None
