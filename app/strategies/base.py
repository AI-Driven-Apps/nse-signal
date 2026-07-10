from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """A strategy converts an OHLCV DataFrame into a position series:
    1.0 = long, 0.0 = flat. Long-only for Phase 1 (matches NSE cash market).

    The SAME position series drives both backtesting (historical) and live
    signals (last-bar transition), so backtest and live can never disagree."""

    name: str = "base"

    @abstractmethod
    def positions(self, df: pd.DataFrame) -> pd.Series:
        """df: columns open/high/low/close/volume, ascending date index.
        Return a 0/1 series aligned to df.index (NaN-safe, no lookahead)."""
