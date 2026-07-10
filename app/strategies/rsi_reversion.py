import pandas as pd

from .base import Strategy


class RsiReversion(Strategy):
    """Buy oversold (RSI < entry) in an uptrend (close > 200 SMA),
    exit when RSI recovers above the exit level. Mean reversion."""

    name = "rsi_reversion"

    def __init__(self, period: int = 14, entry: float = 30, exit_: float = 55,
                 trend_sma: int = 200):
        self.period, self.entry, self.exit_, self.trend_sma = period, entry, exit_, trend_sma

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, pd.NA)
        return (100 - 100 / (1 + rs)).fillna(50)

    def positions(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        rsi = self._rsi(close, self.period)
        trend = close.rolling(self.trend_sma).mean()
        in_uptrend = close > trend

        pos = pd.Series(0.0, index=df.index)
        holding = False
        for i in range(len(df)):
            if trend.iloc[:i + 1].isna().all():
                continue
            if not holding and in_uptrend.iloc[i] and rsi.iloc[i] < self.entry:
                holding = True
            elif holding and (rsi.iloc[i] > self.exit_ or not in_uptrend.iloc[i]):
                holding = False
            pos.iloc[i] = 1.0 if holding else 0.0
        return pos
