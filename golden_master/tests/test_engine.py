"""SignalEngine state-machine fidelity tests — each covers one rule from the
porting-fidelity list in golden_master/engine.py's module docstring."""
from datetime import date

import pytest

from golden_master.engine import SignalEngine
from .conftest import ha_bar

PREV_DAY = date(2024, 6, 3)   # Monday
TODAY = date(2024, 6, 4)      # Tuesday
LATER_DAY = date(2024, 6, 5)  # Wednesday


def _seed_yellow_lines(engine, day=TODAY, y1=100.0, y2=102.0):
    """prev-day 15:15 close = y1, today 9:15 close = y2 -> zone [y1, y2]."""
    engine.process_bar(ha_bar(PREV_DAY, 15, 15, 99.0, 100.5, 98.5, y1))
    engine.process_bar(ha_bar(day, 9, 15, 101.0, 102.5, 100.5, y2))


def _seed_blue_lines(engine, day=TODAY, b1=110.0, b2=108.0):
    """9:30 close = b1, 9:45 open = b2 -> zone [b2, b1]. Both bars have a
    close that stays inside the zone so they don't accidentally trigger a
    breakout themselves."""
    engine.process_bar(ha_bar(day, 9, 30, 109.0, 110.5, 108.5, b1))
    engine.process_bar(ha_bar(day, 9, 45, b2, 109.5, 107.5, 109.0))


def test_bull_rejection_fires_no_touch_bullish_candle_above_zone():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(ha_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert sig is not None
    assert (sig.signal_type, sig.direction) == ("REJECTION", "BUY")
    assert sig.entry_price == pytest.approx(106.0)  # bar's ha_high


def test_bear_rejection_fires_no_touch_bearish_candle_below_zone():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(ha_bar(TODAY, 9, 30, 99.0, 99.5, 96.0, 97.0))
    assert sig is not None
    assert (sig.signal_type, sig.direction) == ("REJECTION", "SELL")
    assert sig.entry_price == pytest.approx(96.0)  # bar's ha_low


def test_rejection_requires_no_wick_touch():
    engine = SignalEngine()
    _seed_yellow_lines(engine)  # zone [100, 102]
    # closes above the zone (105 > 102) but the low (101) wicks INTO the zone
    sig = engine.process_bar(ha_bar(TODAY, 9, 30, 103.0, 106.0, 101.0, 105.0))
    assert sig is None


def test_rejection_blocked_before_rejection_start_time():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    # would satisfy bull rejection, but fires at 9:00 (before the 9:30 gate)
    sig = engine.process_bar(ha_bar(TODAY, 9, 0, 103.0, 106.0, 103.5, 105.0))
    assert sig is None
    # the identical bar shape at 9:30 does fire, proving the gate was the blocker
    sig2 = engine.process_bar(ha_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert sig2 is not None


def test_rejection_is_exclusive_per_day_blocks_opposite_and_fallback():
    engine = SignalEngine()
    _seed_yellow_lines(engine)

    # The 9:30 bar does double duty in real data: it's checked for rejection
    # AND is the bar that sets blue1. Firing rejection here is the realistic
    # case, not a contrived one.
    bull = engine.process_bar(ha_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert (bull.signal_type, bull.direction) == ("REJECTION", "BUY")

    # 9:45 bar sets blue2 so blueReady becomes true; rejection_found should
    # block fallback regardless of blue1/blue2 being ready.
    engine.process_bar(ha_bar(TODAY, 9, 45, 104.0, 105.5, 103.5, 104.5))

    # a later bar that would otherwise satisfy bear rejection must NOT fire
    bear_attempt = engine.process_bar(ha_bar(TODAY, 10, 0, 99.0, 99.5, 96.0, 97.0))
    assert bear_attempt is None

    # a later bar that would otherwise satisfy a blue-line breakout must NOT fire either
    breakout_attempt = engine.process_bar(ha_bar(TODAY, 10, 15, 111.0, 113.0, 110.5, 112.0))
    assert breakout_attempt is None


def test_blue_breakout_whipsaw_both_directions_fire_without_rejection():
    """No rejection ever fires this day (yellow lines never seeded), so
    buy_done/sell_done gate independently: both a bull and a bear
    blue-breakout can legitimately fire on the same day."""
    engine = SignalEngine()
    _seed_blue_lines(engine)  # zone [108, 110], prev_close left at 109.0

    up = engine.process_bar(ha_bar(TODAY, 10, 0, 109.5, 112.5, 110.5, 112.0))
    assert up is not None
    assert (up.signal_type, up.direction) == ("BLUE_BREAKOUT", "BUY")
    assert up.entry_price == pytest.approx(113.0)  # upperBlue(110) + buffer(3)

    down = engine.process_bar(ha_bar(TODAY, 10, 15, 111.0, 107.5, 105.0, 105.0))
    assert down is not None
    assert (down.signal_type, down.direction) == ("BLUE_BREAKOUT", "SELL")
    assert down.entry_price == pytest.approx(105.0)  # lowerBlue(108) - buffer(3)


def test_blue_breakout_does_not_refire_while_price_stays_above_the_line():
    """At most one breakout per direction per day: once a bull breakout has
    fired, a later bar that's still above upperBlue must not fire again.
    (The engine's daily state can't distinguish "no fresh crossover" from
    "buy_done already true" here — both become true at the same moment a
    signal fires — so this test covers the combined, user-facing guarantee
    rather than isolating one specific internal cause.)"""
    engine = SignalEngine()
    _seed_blue_lines(engine)  # prev_close left at 109.0, zone [108, 110]
    first = engine.process_bar(ha_bar(TODAY, 10, 0, 109.5, 112.5, 110.5, 112.0))
    assert first is not None
    still_above = engine.process_bar(ha_bar(TODAY, 10, 15, 112.0, 113.5, 111.5, 113.0))
    assert still_above is None


def test_blue_breakout_blocked_before_fallback_start_time():
    engine = SignalEngine()
    _seed_blue_lines(engine)  # last processed bar was 9:45
    sig = engine.process_bar(ha_bar(TODAY, 9, 40, 109.5, 112.5, 110.5, 112.0))
    assert sig is None


def test_daily_reset_clears_done_flags_but_lines_persist():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    bull = engine.process_bar(ha_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert bull is not None
    assert engine.buy_done is True

    # next day: yellow1 should still hold TODAY's 15:15 value once set, and
    # buy_done should have reset. Seed tomorrow's own yellow2 and re-fire.
    engine.process_bar(ha_bar(TODAY, 15, 15, 100.0, 100.5, 99.5, 100.0))  # updates yellow1 for tomorrow
    engine.process_bar(ha_bar(LATER_DAY, 9, 15, 101.5, 102.5, 101.0, 102.0))  # updates yellow2
    assert engine.buy_done is False
    assert engine.rejection_found is False

    bull2 = engine.process_bar(ha_bar(LATER_DAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert bull2 is not None
    assert (bull2.signal_type, bull2.direction) == ("REJECTION", "BUY")
