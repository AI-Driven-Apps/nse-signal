"""Signal-detection state machine, ported from Nifty_Golden_Master_LipiScript.txt
and aligned to the project owner's formal written requirement doc
(2026-07-11 spec-alignment rework — see docs/golden_master_spec.md).

Two input streams, two entry points:
- update_levels(HABar) — 15-min Heikin-Ashi bars (derived from 5-min data via
  heikin_ashi.resample_to_15m + to_heikin_ashi, not ingested directly). Only
  ever updates yellow1/yellow2/blue1/blue2 at their fixed times-of-day; never
  returns a signal.
- process_bar(EntryBar) — 5-min *normal* (non-HA) candles. This is where
  rejection/breakout detection, entry pricing, and the daily done-flags
  actually live.
Callers (backtest.py, live.py) must feed update_levels() for a given
timestamp before process_bar() for that same or a later timestamp, so a
level a bar-of-that-instant sets is visible to detection happening at the
same instant (relevant right at 9:30 and 9:45).

Fidelity notes (see docs/golden_master_spec.md for the full list):
- yellow1/yellow2/blue1/blue2 persist across day boundaries, updated only
  when a bar at their specific time-of-day arrives — NOT reset daily.
- Only buy_done/sell_done/rejection_found reset at day start. Day-boundary
  detection is checked from BOTH entry points (whichever stream's bar
  arrives first for a new day triggers the reset), so it can't be skipped
  by stream interleaving order.
- rejection_found is set by a rejection in EITHER direction and blocks
  every further rejection AND the blue-line fallback for the rest of the
  day. Blue-line fallback does not set rejection_found, and buy_done/
  sell_done gate independently, so a day with no rejection can still fire
  both a bull and a bear blue-breakout (a whipsaw day) — this is
  intentional, not a bug.
- Rejection search is a HARD WINDOW: [rejection_start, fallback_start), i.e.
  9:30-9:45 by default. A rejection cannot fire before 9:30 OR at/after
  9:45, even with an otherwise perfect setup and even if rejection_found is
  still False — once 9:45 arrives with no rejection, blue-line is the only
  mechanism for the rest of the day, permanently. (This is a change from
  the original Lipi script port, which allowed rejection any time after
  9:30 — the owner's formal spec is explicit about the 9:45 cutoff.)
"""
from dataclasses import dataclass
from datetime import datetime

from .config import (
    BLUE_BREAKOUT_BUFFER, FALLBACK_START_HOUR, FALLBACK_START_MINUTE,
    REJECTION_START_HOUR, REJECTION_START_MINUTE,
)


@dataclass(frozen=True)
class HABar:
    """A 15-min Heikin-Ashi bar — reference-line input only (update_levels)."""
    timestamp: datetime
    ha_open: float
    ha_high: float
    ha_low: float
    ha_close: float


@dataclass(frozen=True)
class EntryBar:
    """A 5-min normal (non-HA) candle — detection input only (process_bar)."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    signal_type: str  # REJECTION | BLUE_BREAKOUT
    direction: str     # BUY | SELL
    entry_time: datetime
    entry_price: float


class SignalEngine:
    """Feed 15-min HA bars via update_levels() and 5-min normal candles via
    process_bar(), both in ascending time order (interleaved as they occur).
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

    def _maybe_reset_for_new_day(self, d) -> None:
        if self._current_date != d:
            self._current_date = d
            self.buy_done = False
            self.sell_done = False
            self.rejection_found = False
            self._prev_close = None

    def update_levels(self, bar: HABar) -> None:
        """15-min HA bar: update yellow/blue reference lines at their fixed
        times-of-day. Never returns a signal, never touches done-flags."""
        self._maybe_reset_for_new_day(bar.timestamp.date())
        hm = (bar.timestamp.hour, bar.timestamp.minute)

        if hm == (15, 15):
            self.yellow1 = bar.ha_close
        if hm == (9, 15):
            self.yellow2 = bar.ha_close
        if hm == (9, 30):
            self.blue1 = bar.ha_close
        if hm == (9, 45):
            self.blue2 = bar.ha_open

    def process_bar(self, bar: EntryBar) -> Signal | None:
        """5-min normal candle: rejection/breakout detection, raw OHLC."""
        self._maybe_reset_for_new_day(bar.timestamp.date())
        hm = (bar.timestamp.hour, bar.timestamp.minute)

        signal = self._check_rejection(bar, hm)
        if signal is None:
            signal = self._check_blue_breakout(bar, hm)

        self._prev_close = bar.close
        return signal

    def _check_rejection(self, bar: EntryBar, hm: tuple[int, int]) -> Signal | None:
        if self.yellow1 is None or self.yellow2 is None:
            return None
        if hm < self.rejection_start or hm >= self.fallback_start:
            return None  # hard window: [rejection_start, fallback_start) only

        upper_yellow = max(self.yellow1, self.yellow2)
        lower_yellow = min(self.yellow1, self.yellow2)
        is_bull_candle = bar.close > bar.open
        is_bear_candle = bar.close < bar.open

        bull_rejection = (not self.buy_done and not self.rejection_found and
                          bar.close > upper_yellow and bar.low > upper_yellow and
                          is_bull_candle)
        if bull_rejection:
            self.buy_done = True
            self.rejection_found = True
            return Signal("REJECTION", "BUY", bar.timestamp, bar.high)

        bear_rejection = (not self.sell_done and not self.rejection_found and
                          bar.close < lower_yellow and bar.high < lower_yellow and
                          is_bear_candle)
        if bear_rejection:
            self.sell_done = True
            self.rejection_found = True
            return Signal("REJECTION", "SELL", bar.timestamp, bar.low)

        return None

    def _check_blue_breakout(self, bar: EntryBar, hm: tuple[int, int]) -> Signal | None:
        if self.blue1 is None or self.blue2 is None:
            return None
        if hm < self.fallback_start or self.rejection_found:
            return None
        if self._prev_close is None:
            return None  # need a previous bar to detect a crossover

        upper_blue = max(self.blue1, self.blue2)
        lower_blue = min(self.blue1, self.blue2)

        bull_cross = self._prev_close <= upper_blue and bar.close > upper_blue
        bull_no_touch = bar.low > upper_blue
        if not self.buy_done and bull_cross and bull_no_touch:
            self.buy_done = True
            return Signal("BLUE_BREAKOUT", "BUY", bar.timestamp,
                         upper_blue + self.blue_breakout_buffer)

        bear_cross = self._prev_close >= lower_blue and bar.close < lower_blue
        bear_no_touch = bar.high < lower_blue
        if not self.sell_done and bear_cross and bear_no_touch:
            self.sell_done = True
            return Signal("BLUE_BREAKOUT", "SELL", bar.timestamp,
                         lower_blue - self.blue_breakout_buffer)

        return None
