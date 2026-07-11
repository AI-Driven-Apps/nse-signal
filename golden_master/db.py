"""Own engine/session, separate from app/db.py (separate deployable, separate
DB file). init_db() includes the same additive column-sync patch as the main
app's app/db._sync_new_columns — copied rather than imported, since these two
packages deliberately don't depend on each other (see docs/golden_master_spec.md).
Small and self-contained; keep the two copies in sync if either changes."""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL
from .models import Base

log = logging.getLogger("golden_master.db")

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)  # creates missing TABLES only, never adds columns
    _sync_new_columns()


def _column_default_sql(column) -> str | None:
    if column.default is None or not getattr(column.default, "is_scalar", False):
        return None
    val = column.default.arg
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return "'{}'".format(val.replace("'", "''"))
    return None


def _sync_new_columns() -> None:
    """Additive-only: add any model column missing from a table that already
    existed on disk. Never drops, renames, or changes a type."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                default_sql = _column_default_sql(column)
                if not column.nullable and default_sql is None:
                    log.error(
                        "%s.%s is missing and has no default — add it manually.",
                        table.name, column.name)
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
                if default_sql is not None:
                    stmt += f" DEFAULT {default_sql}"
                conn.execute(text(stmt))
                log.warning("Schema patch: added column %s.%s", table.name, column.name)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
