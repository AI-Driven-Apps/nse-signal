"""SignalEngine state-machine fidelity tests — each covers one rule from the
porting-fidelity list in golden_master/engine.py's module docstring.

Two input streams, two helpers: ha_bar() + engine.update_levels() sets the
15-min yellow/blue reference lines; entry_bar() + engine.process_bar() drives
5-min raw detection. This mirrors the real dual-timeframe split (see
docs/golden_master_spec.md) — a test that only calls process_bar() without
first seeding levels via update_levels() is testing something that can't
happen in real data."""
from datetime import date

import pytest

from golden_master.engine import SignalEngine
from .conftest import entry_bar, ha_bar

PREV_DAY = date(2024, 6, 3)   # Monday
TODAY = date(2024, 6, 4)      # Tuesday
LATER_DAY = date(2024, 6, 5)  # Wednesday


def _seed_yellow_lines(engine, day=TODAY, y1=100.0, y2=102.0):
    """prev-day 15:15 HA close = y1, today 9:15 HA close = y2 -> zone [y1, y2]."""
    engine.update_levels(ha_bar(PREV_DAY, 15, 15, 99.0, 100.5, 98.5, y1))
    engine.update_levels(ha_bar(day, 9, 15, 101.0, 102.5, 100.5, y2))


def _seed_blue_lines(engine, day=TODAY, b1=110.0, b2=108.0):
    """9:30 HA close = b1, 9:45 HA open = b2 -> zone [b2, b1]. Both HA bars
    have a close inside the zone so they don't matter for detection (they
    only feed update_levels, never process_bar, so this is purely cosmetic
    consistency, not a functional requirement)."""
    engine.update_levels(ha_bar(day, 9, 30, 109.0, 110.5, 108.5, b1))
    engine.update_levels(ha_bar(day, 9, 45, b2, 109.5, 107.5, 109.0))


def test_bull_rejection_fires_no_touch_bullish_candle_above_zone():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert sig is not None
    assert (sig.signal_type, sig.direction) == ("REJECTION", "BUY")
    assert sig.entry_price == pytest.approx(106.0)  # bar's raw high


def test_bear_rejection_fires_no_touch_bearish_candle_below_zone():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(entry_bar(TODAY, 9, 30, 99.0, 99.5, 96.0, 97.0))
    assert sig is not None
    assert (sig.signal_type, sig.direction) == ("REJECTION", "SELL")
    assert sig.entry_price == pytest.approx(96.0)  # bar's raw low


def test_rejection_requires_no_wick_touch():
    engine = SignalEngine()
    _seed_yellow_lines(engine)  # zone [100, 102]
    # closes above the zone (105 > 102) but the low (101) wicks INTO the zone
    sig = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 101.0, 105.0))
    assert sig is None


def test_rejection_blocked_before_rejection_start_time():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    # would satisfy bull rejection, but fires at 9:00 (before the 9:30 gate)
    sig = engine.process_bar(entry_bar(TODAY, 9, 0, 103.0, 106.0, 103.5, 105.0))
    assert sig is None
    # the identical bar shape at 9:30 does fire, proving the gate was the blocker
    sig2 = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert sig2 is not None


def test_rejection_cannot_fire_after_the_945_window_closes():
    """New 2026-07-18: rejection is a hard [9:30, 9:45) window, not just a
    9:30 start time. An otherwise-perfect setup at 10:00 must not fire —
    even though rejection_found is still False."""
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(entry_bar(TODAY, 10, 0, 103.0, 106.0, 103.5, 105.0))
    assert sig is None
    assert engine.rejection_found is False
    # identical shape within the window does fire, proving the window was the blocker
    sig2 = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert sig2 is not None


def test_rejection_blocked_at_exactly_945():
    """The window is [9:30, 9:45) — half-open, excluding 9:45 itself
    ("by 9:45 AM" reads as the cutoff instant, not the last eligible bar)."""
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    sig = engine.process_bar(entry_bar(TODAY, 9, 45, 103.0, 106.0, 103.5, 105.0))
    assert sig is None


def test_rejection_is_exclusive_per_day_blocks_opposite_and_fallback():
    engine = SignalEngine()
    _seed_yellow_lines(engine)

    # In real data, the 9:30 level bar (sets blue1) and the 9:30 detection
    # bar arrive at the same wall-clock instant but through different
    # streams/types — feed both explicitly, matching backtest.py's lockstep
    # (levels advanced up to a timestamp before that timestamp's detection bar).
    engine.update_levels(ha_bar(TODAY, 9, 30, 103.0, 106.0, 103.0, 105.0))
    bull = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert (bull.signal_type, bull.direction) == ("REJECTION", "BUY")

    engine.update_levels(ha_bar(TODAY, 9, 45, 104.0, 105.5, 103.5, 104.5))  # sets blue2

    # a later bar WITHIN the rejection window that would otherwise satisfy
    # bear rejection must not fire — isolates rejection_found as the
    # blocker (9:35 is still inside [9:30, 9:45), so the window isn't why).
    bear_attempt = engine.process_bar(entry_bar(TODAY, 9, 35, 99.0, 99.5, 96.0, 97.0))
    assert bear_attempt is None

    # a later bar that would otherwise satisfy a blue-line breakout must not fire either
    breakout_attempt = engine.process_bar(entry_bar(TODAY, 10, 15, 111.0, 113.0, 110.5, 112.0))
    assert breakout_attempt is None


def test_blue_breakout_whipsaw_both_directions_fire_without_rejection():
    """No rejection ever fires this day (yellow lines never seeded), so
    buy_done/sell_done gate independently: both a bull and a bear
    blue-breakout can legitimately fire on the same day."""
    engine = SignalEngine()
    _seed_blue_lines(engine)  # zone [108, 110]
    # a bar at/after 9:45 establishes _prev_close inside the zone before the
    # actual breakout bars (process_bar never ran yet this day)
    engine.process_bar(entry_bar(TODAY, 9, 45, 109.0, 109.5, 108.5, 109.0))

    up = engine.process_bar(entry_bar(TODAY, 10, 0, 109.5, 112.5, 110.5, 112.0))
    assert up is not None
    assert (up.signal_type, up.direction) == ("BLUE_BREAKOUT", "BUY")
    assert up.entry_price == pytest.approx(113.0)  # upperBlue(110) + buffer(3)

    down = engine.process_bar(entry_bar(TODAY, 10, 15, 111.0, 107.5, 105.0, 105.0))
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
    _seed_blue_lines(engine)  # zone [108, 110]
    engine.process_bar(entry_bar(TODAY, 9, 45, 109.0, 109.5, 108.5, 109.0))

    first = engine.process_bar(entry_bar(TODAY, 10, 0, 109.5, 112.5, 110.5, 112.0))
    assert first is not None
    still_above = engine.process_bar(entry_bar(TODAY, 10, 15, 112.0, 113.5, 111.5, 113.0))
    assert still_above is None


def test_blue_breakout_blocked_before_fallback_start_time():
    engine = SignalEngine()
    _seed_blue_lines(engine)  # levels set, but no detection bar processed yet
    sig = engine.process_bar(entry_bar(TODAY, 9, 40, 109.5, 112.5, 110.5, 112.0))
    assert sig is None


def test_daily_reset_clears_done_flags_but_lines_persist():
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    bull = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert bull is not None
    assert engine.buy_done is True

    # next day: yellow1 should still hold TODAY's 15:15 value once set, and
    # buy_done should have reset. Seed tomorrow's own yellow2 and re-fire.
    engine.update_levels(ha_bar(TODAY, 15, 15, 100.0, 100.5, 99.5, 100.0))  # updates yellow1 for tomorrow
    engine.update_levels(ha_bar(LATER_DAY, 9, 15, 101.5, 102.5, 101.0, 102.0))  # updates yellow2
    assert engine.buy_done is False
    assert engine.rejection_found is False

    bull2 = engine.process_bar(entry_bar(LATER_DAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert bull2 is not None
    assert (bull2.signal_type, bull2.direction) == ("REJECTION", "BUY")


def test_day_reset_triggered_by_either_stream_first():
    """The level stream and the detection stream can each be the first to
    see a new day's first bar (interleaving order isn't guaranteed by
    wall-clock alone) — the reset must fire from whichever arrives first."""
    engine = SignalEngine()
    _seed_yellow_lines(engine)
    bull = engine.process_bar(entry_bar(TODAY, 9, 30, 103.0, 106.0, 103.5, 105.0))
    assert bull is not None
    assert engine.buy_done is True

    # next day: feed only a LEVEL bar first, no detection bar yet — reset
    # must already have happened.
    engine.update_levels(ha_bar(LATER_DAY, 9, 15, 101.5, 102.5, 101.0, 102.0))
    assert engine.buy_done is False
    assert engine.rejection_found is False
