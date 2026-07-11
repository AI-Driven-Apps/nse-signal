import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL, DEFAULT_STRATEGY
from .models import Base, Setting

log = logging.getLogger("db")

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)  # creates missing TABLES only, never adds columns
    _sync_new_columns()
    with session_scope() as s:
        if not s.get(Setting, "active_strategy"):
            s.add(Setting(key="active_strategy", value=DEFAULT_STRATEGY))


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
    """Additive-only schema patch, run on every startup: add any model column
    missing from a table that already existed on disk (e.g. a table created
    by an earlier deploy, before a new column was added to its model).
    create_all() only creates missing tables — it never alters an existing
    one — so without this, an already-deployed SQLite file silently keeps
    the old schema and every query touching the new column 500s.

    Only ever ADDS nullable/defaulted columns; never drops, renames, or
    changes a type. That's real-migration territory — reserved for Alembic
    in Phase 3. If a NOT NULL column with no usable default is missing, this
    logs an error and leaves it for a manual fix rather than guessing."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand-new table — create_all() already made it in full
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                default_sql = _column_default_sql(column)
                if not column.nullable and default_sql is None:
                    log.error(
                        "%s.%s is missing and has no default — add it manually "
                        "(e.g. via sqlite3 ALTER TABLE).", table.name, column.name)
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


def get_setting(session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
