"""Test fixtures for the Golden Master subsystem. DATABASE_URL must be set
before any golden_master import (engine created at import time), mirroring
the main app's tests/conftest.py pattern — but this is a fully separate
package/DB, so it has its own fixture file rather than sharing that one."""
import os
import tempfile
from datetime import date, datetime, time

_fd, _path = tempfile.mkstemp(prefix="golden_master_test_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"
os.environ["DISCORD_WEBHOOK_URL"] = ""

import pytest

from golden_master.db import engine, init_db
from golden_master.engine import EntryBar, HABar
from golden_master.models import Base


@pytest.fixture()
def db():
    """Fresh schema per test."""
    Base.metadata.drop_all(engine)
    init_db()
    yield


def ha_bar(d: date, hour: int, minute: int, o: float, h: float, l: float, c: float) -> HABar:
    """Build an HABar (15-min level-setting input, fed via
    SignalEngine.update_levels) with hand-chosen HA values directly —
    engine.py tests drive the state machine independently of
    heikin_ashi.py's transform (that's covered separately in
    test_heikin_ashi.py)."""
    return HABar(datetime.combine(d, time(hour, minute)), o, h, l, c)


def entry_bar(d: date, hour: int, minute: int, o: float, h: float, l: float, c: float) -> EntryBar:
    """Build an EntryBar (5-min raw detection input, fed via
    SignalEngine.process_bar)."""
    return EntryBar(datetime.combine(d, time(hour, minute)), o, h, l, c)
