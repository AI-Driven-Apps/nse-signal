"""Signal-detection state machine, ported from Nifty_Golden_Master_LipiScript.txt.

Porting decision (documented since the source is an admitted first draft):
the script pulls 15-min Heikin-Ashi data explicitly for its reference lines
via request.security(..., heikinashi=true), but its rejection/breakout
*detection* conditions use bare open/high/low/close with no HA qualifier —
ambiguous whether that means the chart's own (possibly non-HA) candles or
the same HA series. Given the strategy is titled and documented entirely
around Heikin-Ashi, this port treats detection as HA-based throughout too,
for internal consistency. Revisit here first if live behavior needs to
differ from this.

Fidelity notes (see docs/golden_master_spec.md for the full list):
- yellow1/yellow2/blue1/blue2 persist across day boundaries, updated only
  when a bar at their specific time-of-day arrives — NOT reset daily.
- Only buy_done/sell_done/rejection_found reset at day start.
- rejection_found is set by a rejection in EITHER direction and blocks
  every further rejection AND the blue-line fallback for the rest of the
  day. Blue-line fallback does not set rejection_found, and buy_done/
  sell_done gate independently, so a day with no rejection can still fire
  both a bull and a bear blue-breakout (a whipsaw day) — this is
  intentional, not a bug.
"""
from dataclasses import dataclass
from datetime import datetime

from .config import (
    BLUE_BREAKOUT_BUFFER, FALLBACK_START_HOUR, FALLBACK_START_MINUTE,
    REJECTION_START_HOUR, REJECTION_START_MINUTE,
)


@dataclass(frozen=True)
class HABar:
    timestamp: datetime
    ha_open: float
    ha_high: float
    ha_low: float
    ha_close: float


@dataclass(frozen=True)
class Signal:
    signal_type: str  # REJECTION | BLUE_BREAKOUT
    direction: str     # BUY | SELL
    entry_time: datetime
    entry_price: float


class SignalEngine:
    """Feed 15-min Heikin-Ashi bars in ascending time order via process_bar().
    Stateful across bars/days by design (mirrors the source script's static
    variables) — create a fresh instance per independent backtest run."""

    def __init__(self, rejection_start: tuple[int, int] = (REJECTION_START_HOUR, REJECTION_START_MINUTE),
                fallback_start: tuple[int, int] = (FALLBACK_START_HOUR, FALLBACK_START_MINUTE),
                blue_breakout_buffer: float = BLUE_BREAKOUT_BUFFER):
        self.rejection_start = rejection_start
        self.fallback_start = fallback_start
        self.blue_breakout_buffer = blue_breakout_buffer

        self.yellow1: float | None = None  # previous day's 15:15 HA close
        self.yellow2: float | None = None  # today's 9:15 HA close
        self.blue1: float | None = None    # today's 9:30 HA close
        self.blue2: float | None = None    # today's 9:45 HA open

        self._current_date = None
        self.buy_done = False
        self.sell_done = False
        self.rejection_found = False
        self._prev_close: float | None = None

    def process_bar(self, bar: HABar) -> Signal | None:
        d = bar.timestamp.date()
        if self._current_date != d:
            self._current_date = d
            self.buy_done = False
            self.sell_done = False
            self.rejection_found = False

        hm = (bar.timestamp.hour, bar.timestamp.minute)

        if hm == (15, 15):
            self.yellow1 = bar.ha_close
        if hm == (9, 15):
            self.yellow2 = bar.ha_close
        if hm == (9, 30):
            self.blue1 = bar.ha_close
        if hm == (9, 45):
            self.blue2 = bar.ha_open

        signal = self._check_rejection(bar, hm)
        if signal is None:
            signal = self._check_blue_breakout(bar, hm)

        self._prev_close = bar.ha_close
        return signal

    def _check_rejection(self, bar: HABar, hm: tuple[int, int]) -> Signal | None:
        if self.yellow1 is None or self.yellow2 is None:
            return None
        if hm < self.rejection_start:
            return None

        upper_yellow = max(self.yellow1, self.yellow2)
        lower_yellow = min(self.yellow1, self.yellow2)
        is_bull_candle = bar.ha_close > bar.ha_open
        is_bear_candle = bar.ha_close < bar.ha_open

        bull_rejection = (not self.buy_done and not self.rejection_found and
                          bar.ha_close > upper_yellow and bar.ha_low > upper_yellow and
                          is_bull_candle)
        if bull_rejection:
            self.buy_done = True
            self.rejection_found = True
            return Signal("REJECTION", "BUY", bar.timestamp, bar.ha_high)

        bear_rejection = (not self.sell_done and not self.rejection_found and
                          bar.ha_close < lower_yellow and bar.ha_high < lower_yellow and
                          is_bear_candle)
        if bear_rejection:
            self.sell_done = True
            self.rejection_found = True
            return Signal("REJECTION", "SELL", bar.timestamp, bar.ha_low)

        return None

    def _check_blue_breakout(self, bar: HABar, hm: tuple[int, int]) -> Signal | None:
        if self.blue1 is None or self.blue2 is None:
            return None
        if hm < self.fallback_start or self.rejection_found:
            return None
        if self._prev_close is None:
            return None  # need a previous bar to detect a crossover

        upper_blue = max(self.blue1, self.blue2)
        lower_blue = min(self.blue1, self.blue2)

        bull_cross = self._prev_close <= upper_blue and bar.ha_close > upper_blue
        bull_no_touch = bar.ha_low > upper_blue
        if not self.buy_done and bull_cross and bull_no_touch:
            self.buy_done = True
            return Signal("BLUE_BREAKOUT", "BUY", bar.timestamp,
                         upper_blue + self.blue_breakout_buffer)

        bear_cross = self._prev_close >= lower_blue and bar.ha_close < lower_blue
        bear_no_touch = bar.ha_high < lower_blue
        if not self.sell_done and bear_cross and bear_no_touch:
            self.sell_done = True
            return Signal("BLUE_BREAKOUT", "SELL", bar.timestamp,
                         lower_blue - self.blue_breakout_buffer)

        return None
