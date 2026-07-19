"""Intraday ingest upsert logic, using a fake provider (no network) —
mirrors the DB-fixture pattern used elsewhere in this test suite. No HA
columns to verify here (2026-07-18 rework): five_min_bars stores raw OHLC
only, HA is derived at replay time from resampled 15-min buckets."""
import pandas as pd
import pytest
from sqlalchemy import select

from golden_master.db import session_scope
from golden_master.ingest import ingest_symbol
from golden_master.models import FiveMinBar


class FakeProvider:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def fetch(self, symbol: str, period: str = "5d", interval: str = "5m") -> pd.DataFrame:
        return self._df


def _bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({
        "open": [r[1] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
    }, index=idx)


def test_ingest_inserts_new_bars(db):
    df = _bars([
        ("2024-06-04 09:15", 100.0, 101.0, 99.0, 100.5),
        ("2024-06-04 09:20", 100.5, 102.0, 100.0, 101.5),
    ])
    with session_scope() as s:
        n = ingest_symbol(s, FakeProvider(df), "^NSEI")
    assert n == 2

    with session_scope() as s:
        rows = s.execute(select(FiveMinBar).order_by(FiveMinBar.timestamp)).scalars().all()
    assert len(rows) == 2
    assert rows[0].symbol == "^NSEI"
    assert rows[0].close == pytest.approx(100.5)


def test_ingest_is_idempotent_on_unchanged_refetch(db):
    df = _bars([("2024-06-04 09:15", 100.0, 101.0, 99.0, 100.5)])
    with session_scope() as s:
        ingest_symbol(s, FakeProvider(df), "^NSEI")
    with session_scope() as s:
        n = ingest_symbol(s, FakeProvider(df), "^NSEI")  # identical refetch
    assert n == 0
    with session_scope() as s:
        assert len(s.execute(select(FiveMinBar)).scalars().all()) == 1


def test_ingest_updates_changed_bars(db):
    df = _bars([("2024-06-04 09:15", 100.0, 101.0, 99.0, 100.5)])
    with session_scope() as s:
        ingest_symbol(s, FakeProvider(df), "^NSEI")

    revised = _bars([("2024-06-04 09:15", 100.0, 103.0, 99.0, 102.0)])  # close revised upward
    with session_scope() as s:
        n = ingest_symbol(s, FakeProvider(revised), "^NSEI")
    assert n == 1

    with session_scope() as s:
        row = s.execute(select(FiveMinBar)).scalar_one()
    assert row.close == pytest.approx(102.0)


def test_ingest_empty_provider_response(db):
    with session_scope() as s:
        n = ingest_symbol(s, FakeProvider(pd.DataFrame()), "^NSEI")
    assert n == 0
