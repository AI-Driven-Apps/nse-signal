"""15-min intraday data provider, behind an interface so yfinance can be
swapped out later — mirrors app/data_provider.py's DataProvider pattern in
the main app. yfinance intraday data caps around 60 days of lookback and
carries real delay/reliability caveats for live use (this was the reason
the data source was deferred in the initial build) — see
docs/golden_master_spec.md."""
from abc import ABC, abstractmethod

import pandas as pd


class IntradayDataProvider(ABC):
    @abstractmethod
    def fetch(self, symbol: str, period: str = "1mo", interval: str = "15m") -> pd.DataFrame:
        """Return a DataFrame indexed by naive local datetime (ascending)
        with columns: open, high, low, close, volume. Empty DataFrame if
        nothing found."""


class YFinanceIntradayProvider(IntradayDataProvider):
    def fetch(self, symbol: str, period: str = "1mo", interval: str = "15m") -> pd.DataFrame:
        import yfinance as yf

        df = yf.download(
            symbol, period=period, interval=interval,
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index)
        # yfinance intraday timestamps are tz-aware (exchange tz, IST for
        # NSE symbols) — normalize to naive local time so they compare
        # directly against the engine's plain hour/minute time gates.
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
        df = df.dropna(subset=["close"])
        return df


def get_provider() -> IntradayDataProvider:
    return YFinanceIntradayProvider()
