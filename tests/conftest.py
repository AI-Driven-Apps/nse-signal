"""Test fixtures. DATABASE_URL must be set before any app import,
because app.db creates the engine at import time."""
import os
import tempfile

_fd, _path = tempfile.mkstemp(prefix="backstage_test_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"
os.environ["DISCORD_WEBHOOK_URL"] = ""  # never hit the network from tests
os.environ["SESSION_SECRET"] = "test-secret-not-for-prod"
os.environ["ADMIN_USER"] = "test_admin"
os.environ["ADMIN_PASSWORD"] = "test_password_123"

import numpy as np
import pandas as pd
import pytest

from app.db import engine, init_db
from app.models import Base


@pytest.fixture()
def db():
    """Fresh schema per test."""
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture()
def trending_df():
    """~2 years of synthetic daily bars with an upward drift."""
    rng = np.random.default_rng(7)
    n = 500
    rets = rng.normal(0.002, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2024-01-01", periods=n).date
    return pd.DataFrame({
        "open": close * 0.997,
        "high": close * 1.008,
        "low": close * 0.992,
        "close": close,
        "volume": np.full(n, 1e6),
    }, index=pd.Index(dates, name="date"))
