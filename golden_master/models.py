"""SQLAlchemy models for the Golden Master subsystem — own DB, own metadata,
deliberately not sharing app/models.py's Base (separate deployable, see
docs/golden_master_spec.md)."""
from datetime import datetime, date

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IntradayBar(Base):
    """15-min OHLC bars plus their precomputed Heikin-Ashi transform, so
    backtest and (later) live signal detection share one source of truth
    instead of recomputing HA independently."""
    __tablename__ = "intraday_bars"
    __table_args__ = (UniqueConstraint("symbol", "timestamp", name="uq_symbol_ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)  # bar close time, IST-naive
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    ha_open: Mapped[float] = mapped_column(Float)
    ha_high: Mapped[float] = mapped_column(Float)
    ha_low: Mapped[float] = mapped_column(Float)
    ha_close: Mapped[float] = mapped_column(Float)


class GoldenMasterTrade(Base):
    """One bracket trade (entry + fixed SL + fixed target). P&L is in Nifty
    index points, not real option premium — see docs/golden_master_spec.md
    for why (no options-chain/Greeks data source). ce_strike/pe_strike are
    stored for reference/labeling only, not used in pnl_points."""
    __tablename__ = "golden_master_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    signal_type: Mapped[str] = mapped_column(String(16))    # REJECTION | BLUE_BREAKOUT
    direction: Mapped[str] = mapped_column(String(8))       # BUY | SELL
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float)
    sl_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # SL_HIT | TARGET_HIT | EOD_SQUARE_OFF
    pnl_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    ce_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="backtest")  # backtest | live
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GoldenMasterBacktestRun(Base):
    """One row per backtest invocation, aggregate stats over the whole range."""
    __tablename__ = "golden_master_backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    n_trades: Mapped[int] = mapped_column(Integer)
    n_wins: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    net_points: Mapped[float] = mapped_column(Float)
    rejection_trades: Mapped[int] = mapped_column(Integer)
    blue_breakout_trades: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
