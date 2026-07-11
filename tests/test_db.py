"""Regression test for the additive schema-patch fallback (app/db._sync_new_columns).
create_all() only creates missing tables, never adds columns to a table that
already exists on disk — so a column added to a model after a table was
first deployed (e.g. WatchlistItem.starred) needs this patch, or every query
touching it 500s in production. See CLAUDE.md invariant 3 / docs/phase2_specs.md."""
from sqlalchemy import select, text

from app.db import engine, init_db, session_scope
from app.models import Stock, WatchlistItem


def test_init_db_patches_missing_column_on_existing_table(db):
    # Simulate a table deployed before `starred` existed on the model: drop
    # the fixture's fresh copy and recreate the old, narrower shape with a
    # row already in it (as a real Pi deploy would have).
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE watchlist"))
        conn.execute(text(
            "CREATE TABLE watchlist ("
            "id INTEGER PRIMARY KEY, stock_id INTEGER UNIQUE NOT NULL, "
            "added_at DATETIME)"
        ))
    with session_scope() as s:
        stock = Stock(symbol="AAA.NS")
        s.add(stock)
        s.flush()
        s.execute(
            text("INSERT INTO watchlist (stock_id, added_at) VALUES (:sid, '2024-01-01')"),
            {"sid": stock.id},
        )

    init_db()  # must patch the missing 'starred' column in place, not crash

    with session_scope() as s:
        rows = s.execute(select(WatchlistItem)).scalars().all()
    assert len(rows) == 1
    assert rows[0].starred is False  # pre-existing row backfilled to the column default

    # Re-running init_db() (every process start) must be a no-op, not error
    # on "duplicate column".
    init_db()
