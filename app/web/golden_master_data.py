"""Read-only queries against the Golden Master subsystem's own SQLite DB —
a second, independent SQLAlchemy engine over a read-only bind mount (see
docker-compose.yml). Uses table reflection rather than importing
golden_master.models: the two containers/images are deliberately
independent (this container's image never has the golden_master package
installed), so there is nothing to import — see docs/golden_master_spec.md.
This module never writes; golden-master's own container owns all writes."""
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import sessionmaker

from ..config import GOLDEN_MASTER_DATABASE_URL

_engine = create_engine(GOLDEN_MASTER_DATABASE_URL, future=True) if GOLDEN_MASTER_DATABASE_URL else None
_SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True) if _engine else None


def _reflect() -> tuple[Table, Table]:
    metadata = MetaData()
    trades = Table("golden_master_trades", metadata, autoload_with=_engine)
    runs = Table("golden_master_backtest_runs", metadata, autoload_with=_engine)
    return trades, runs


def available() -> bool:
    """False if GOLDEN_MASTER_DATABASE_URL isn't set, or the file/tables
    don't exist yet (container never started, or init-db/backtest never run)."""
    if _engine is None:
        return False
    try:
        _reflect()
        return True
    except Exception:
        return False


def recent_trades(limit: int = 30) -> list[dict]:
    if not available():
        return []
    trades, _ = _reflect()
    with _SessionLocal() as s:
        rows = s.execute(
            select(trades).order_by(trades.c.trade_date.desc(), trades.c.id.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def latest_backtest_summary() -> dict | None:
    if not available():
        return None
    _, runs = _reflect()
    with _SessionLocal() as s:
        row = s.execute(
            select(runs).order_by(runs.c.created_at.desc()).limit(1)
        ).mappings().first()
    return dict(row) if row else None


def latest_daily_levels() -> dict | None:
    """Most recent day's yellow/blue reference lines + CE/PE strikes —
    written independent of whether a trade fired that day (see
    GoldenMasterDailyLevels). Reflected separately from available()'s two
    tables so an older golden-master deployment that hasn't created this
    table yet degrades to "no data" here rather than breaking the page."""
    if _engine is None:
        return None
    try:
        metadata = MetaData()
        levels = Table("golden_master_daily_levels", metadata, autoload_with=_engine)
    except Exception:
        return None
    with _SessionLocal() as s:
        row = s.execute(
            select(levels).order_by(levels.c.trading_date.desc()).limit(1)
        ).mappings().first()
    return dict(row) if row else None
