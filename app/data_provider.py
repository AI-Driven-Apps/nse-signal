"""Data provider behind an interface so yfinance can be swapped out
(e.g. for an NSE bhavcopy loader or a paid API) without touching the rest."""
from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataProvider(ABC):
    @abstractmethod
    def fetch(self, symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
        """Return a DataFrame indexed by date with columns:
        open, high, low, close, volume. Empty DataFrame if nothing found."""


class YFinanceProvider(DataProvider):
    def fetch(self, symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
        import yfinance as yf

        df = yf.download(
            symbol, start=start, end=end,
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # yfinance sometimes returns MultiIndex columns even for one ticker
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index).date
        df = df.dropna(subset=["close"])
        return df


def get_provider() -> DataProvider:
    return YFinanceProvider()
