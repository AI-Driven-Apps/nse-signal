"""Heikin-Ashi transform, plus the 5-min -> 15-min resample that feeds it.
HA computation is standard, computed continuously over the whole input
series (matches how a charting platform's HA indicator behaves — it does
NOT reset at day boundaries; only the strategy's own state, in engine.py,
resets daily)."""
import pandas as pd


def resample_to_15m(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5-min OHLC bars into 15-min OHLC bars (3 bars/bucket:
    open=first, high=max, low=min, close=last). label="left"/closed="left"
    means a derived bar timestamped e.g. 9:30 aggregates the 9:30, 9:35,
    9:40 5-min bars — matching yfinance's own interval-START labeling
    convention (a bar timestamped 9:30 covers 9:30-9:45), which is what the
    engine already assumes when it checks e.g. hm == (9, 30) for "the 9:30
    candle". Drops any bucket with fewer than 3 bars: a partial bucket at
    the front of live-polled data (still filling in) would otherwise derive
    a wrong, incomplete level — better to have no value yet than a wrong one."""
    grouped = df_5m.resample("15min", label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    counts = grouped.size()
    out = out[counts >= 3]
    return out.dropna(subset=["open"])


def to_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """df: ascending-time DataFrame with open/high/low/close columns (any
    index). Returns a copy with ha_open/ha_high/ha_low/ha_close columns added.

    ha_close[i] = mean(O, H, L, C)[i]
    ha_open[i]  = mean(ha_open[i-1], ha_close[i-1]), seeded as mean(O, C)[0]
    ha_high[i]  = max(H[i], ha_open[i], ha_close[i])
    ha_low[i]   = min(L[i], ha_open[i], ha_close[i])
    """
    out = df.copy()
    ha_close = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0

    ha_open = pd.Series(index=out.index, dtype=float)
    if len(out) > 0:
        ha_open.iloc[0] = (out["open"].iloc[0] + out["close"].iloc[0]) / 2.0
        for i in range(1, len(out)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    out["ha_close"] = ha_close
    out["ha_open"] = ha_open
    out["ha_high"] = pd.concat([out["high"], ha_open, ha_close], axis=1).max(axis=1)
    out["ha_low"] = pd.concat([out["low"], ha_open, ha_close], axis=1).min(axis=1)
    return out
