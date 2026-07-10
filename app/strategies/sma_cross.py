import pandas as pd

from .base import Strategy


class SmaCross(Strategy):
    """Long when fast SMA is above slow SMA. Classic trend-following."""

    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 50):
        self.fast, self.slow = fast, slow

    def positions(self, df: pd.DataFrame) -> pd.Series:
        fast = df["close"].rolling(self.fast).mean()
        slow = df["close"].rolling(self.slow).mean()
        pos = (fast > slow).astype(float)
        pos[slow.isna()] = 0.0
        return pos
