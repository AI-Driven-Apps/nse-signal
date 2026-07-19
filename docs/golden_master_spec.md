# Golden Master — intraday options strategy (dedicated subsystem)

Design spec for the `golden_master/` package. Written 2026-07-11, porting
`Nifty_Golden_Master_LipiScript.txt` (repo root — a first-draft Pine Script
→ GoCharting Lipi Script port of an intraday Nifty index options day-trading
strategy). See that file's own header comments for the original assumptions.

**Updated 2026-07-18** with a formal written requirement doc from the project
owner, more precise than the first-draft script above — see "Spec-alignment
rework" below. Where the two disagree, the formal requirement wins; the
Lipi/Pine draft remains useful for the parts it doesn't cover explicitly
(e.g. the persistence/no-reset semantics of the reference lines).

## Why this is a separate subsystem, not another `app/strategies/*.py`

The main nse-signal engine (CLAUDE.md invariant 5) is explicitly "NSE cash
market, long-only. No shorting, no F&O, no intraday." This strategy is F&O
(Nifty options) and intraday by definition — the opposite of what that
invariant excludes, not an edge case of it. It also needs 15-min bars (not
daily EOD), fires one-shot bracket trades with fixed SL/target (not a
continuous 0/1 position series `Strategy.positions()` expects), and the
existing vectorized `app/backtest.py` has no concept of intraday SL/TP exits.
Rather than compromise either engine, this is a fully separate container,
database, and backtester — see CLAUDE.md's invariant 5 note for the pointer
back from the main spec.

## Confirmed design decisions

- **Live yfinance intraday feed, added 2026-07-11, repointed to 5-min bars
  2026-07-18.** `intraday_provider.py` mirrors `app/data_provider.py`'s
  `DataProvider` ABC pattern (`IntradayDataProvider` / `YFinanceIntradayProvider`).
  Still carries the caveats that motivated deferring this in the initial
  build: yfinance intraday history caps around 60 days of lookback, and
  real-time index data can lag/be unreliable — `ingest.py` and `live.py`
  both fail soft (log a warning, skip the cycle) on any fetch error rather
  than crashing the poll loop, matching `app/ingest.py`'s "one bad fetch
  should not kill the run" convention. `golden_master/main.py backtest <csv>`
  still works unchanged for testing against data outside that 60-day window
  or a different source entirely.
- **Same Discord webhook** as the main app (`DISCORD_WEBHOOK_URL`), styled
  distinctly (`golden_master/notify.py`: purple embed, "GOLDEN MASTER" title)
  rather than a separate channel.
- **Own SQLite DB file**, own Docker volume (`./data-golden-master`), true
  isolation from `nse_signal.db`. The main dashboard reads it through a
  second, read-only SQLAlchemy engine over a read-only bind mount of the
  same host directory (`app/web/golden_master_data.py`) — using table
  reflection, not an import of `golden_master.models`, since the two
  container images are independently built and nse-signal's image never has
  the `golden_master` package installed.

## Porting fidelity — read this before changing `golden_master/engine.py`

Extracted from the Lipi/Pine draft and the formal written requirement
(2026-07-18); each rule is covered by a test in
`golden_master/tests/test_engine.py`:

1. **Rejection is exclusive per day, in either direction.** `rejection_found`
   gates both `bullRejection` and `bearRejection` — once any rejection fires,
   no further rejection *or* blue-line fallback can fire that day.
2. **Blue-line fallback is independent per direction.** It only checks its
   own `buy_done`/`sell_done` flag and never sets `rejection_found`. A day
   with *no* rejection can legitimately fire both a bull *and* a bear
   blue-breakout (a whipsaw day) — intentional, not a bug.
3. **Entry prices**: rejection = the signal candle's *raw* high (bull) / raw
   low (bear); blue-breakout = `upperBlue + 3` / `lowerBlue - 3` (a 3-point
   buffer past the line, `GM_BLUE_BREAKOUT_BUFFER`).
4. **"No touch" conditions**: a candle must not wick into the zone it's
   rejecting/breaking (raw `low > upper`, not just raw `close > upper`).
5. **Rejection is a HARD WINDOW, not just a start time**: `[rejection_start,
   fallback_start)` — 9:30 up to but not including 9:45 by default
   (`GM_REJECTION_START_HOUR/MINUTE`, `GM_FALLBACK_START_HOUR/MINUTE`). A
   rejection cannot fire before 9:30 *or at/after 9:45*, even with an
   otherwise perfect setup and even if `rejection_found` is still `False` —
   once 9:45 arrives with no rejection, blue-line is the *only* mechanism
   for the rest of the day, permanently. **This changed 2026-07-18**: the
   original port (from the ambiguous Lipi draft) allowed rejection any time
   after 9:30, all day; the formal written requirement is explicit about the
   9:45 cutoff ("if no valid rejection candle is found by 9:45 AM, switch to
   the blue-line strategy").
6. **Daily reset**: only `buy_done`/`sell_done`/`rejection_found` reset on a
   new day. The four reference lines (yellow1/yellow2/blue1/blue2) persist
   across day boundaries, overwritten only when a bar at their specific
   time-of-day arrives — this matches the source script's static-variable
   semantics and is correct given rejection/fallback start after those
   times have already updated same-day.

### Dual timeframe — reference lines vs. detection (2026-07-18 rework)

The formal written requirement is explicit where the Lipi/Pine draft was
ambiguous: **reference lines** (yellow 1/2, blue 1/2) come from **15-min
Heikin-Ashi** candles, but **rejection-candle and blue-breakout detection**
run on **5-minute *normal* (non-HA) candles** — different timeframe,
different candle type, from the lines that gate them.

`golden_master/engine.py`'s `SignalEngine` has two entry points reflecting
this split:
- `update_levels(HABar)` — 15-min HA bars, sets yellow1/yellow2/blue1/blue2
  only, never returns a signal.
- `process_bar(EntryBar)` — 5-min raw OHLC, does all rejection/breakout
  detection and entry pricing against whatever levels are currently known.

**The 15-min HA series is *derived*, not ingested.** Since any 3 consecutive
5-min candles aggregate exactly into one 15-min candle (open=first,
high=max, low=min, close=last), and 5-min is needed anyway for detection +
SL/target monitoring, the design ingests **5-min bars only**
(`five_min_bars`) and derives the 15-min series in-memory at replay time
(`heikin_ashi.resample_to_15m` → `heikin_ashi.to_heikin_ashi`) — one real
data source, no risk of two live feeds disagreeing. `resample_to_15m` uses
`label="left", closed="left"` to match yfinance's own interval-*start*
labeling convention (a bar timestamped 9:30 covers 9:30–9:45) — the same
convention the engine already assumes when it checks `hm == (9, 30)` for
"the 9:30 candle" — and drops any bucket with fewer than 3 bars, so a
still-filling-in bucket at the front of live-polled data never derives a
wrong, incomplete level.

`backtest_range`/`compute_daily_levels` (`golden_master/backtest.py`) merge
the two streams by advancing a pointer through the derived 15-min series and
calling `update_levels()` for every level bar at-or-before the current 5-min
bar's timestamp, before calling `process_bar()` on that 5-min bar — so a
level set at a given instant (relevant right at 9:30 and 9:45) is visible to
detection happening at that same instant.

**SL/target execution monitoring is also 5-min now** (was 15-min before this
rework) — tighter, more realistic, and consistent with detection running on
the same timeframe. Still checks *raw* OHLC, never HA — a real stop/target
order fills against actual market price, not a synthetic smoothed HA price
— and still resolves a same-bar SL+target tie to SL_HIT (conservative).

The pre-rework `intraday_bars` table (15-min bars + precomputed HA, ingested
directly) is now **vestigial** — left in the schema with whatever historical
rows it already has, but nothing writes to it anymore.

## Backtest resolution rules

- A single bar touching both SL and target resolves to **SL_HIT** first
  (conservative — checked before target in the resolution loop — consistent
  with this project's existing "backtest results overstate live
  performance" stance).
- Neither hit by the day's last bar: **EOD_SQUARE_OFF** at that bar's close.
  No overnight options carry.
- SL = target = 25 points by default (`GM_SL_POINTS`/`GM_TARGET_POINTS`),
  fixed 1:1 R:R — **win rate alone determines edge before costs**, surfaced
  as the headline metric on `/golden-master`.
- P&L is tracked in **Nifty index points**, not real option premium.
  `ce_strike`/`pe_strike` (computed from each day's session-open bar,
  rounded to the nearest `GM_STRIKE_OFFSET`) are stored for
  reference/labeling only. A real options trade's P&L will differ from the
  index-point P&L here due to premium/theta/IV effects, which are not
  modeled (no options-chain/Greeks data source).

## Schema (`golden_master/models.py`)

- `five_min_bars`: raw 5-min OHLC, unique on `(symbol, timestamp)`. The sole
  ingested intraday feed (`ingest.py`, full-window refetch + upsert,
  mirroring `app/ingest.py`'s pattern; default fetch period `5d` — no
  HA-seed-decay concern here since nothing recursive is persisted, HA is
  derived fresh at replay time).
- `intraday_bars`: **vestigial** — see the dual-timeframe section above.
- `golden_master_trades`: one row per bracket trade, `source` = `backtest`
  or `live` (same table for both, filterable). Live trades are upserted by
  `(trade_date, signal_type, direction)` — inserted once when a signal first
  fires (source of a single Discord alert), then updated in place once a
  later poll resolves the SL/target/EOD outcome. `exit_*`/`pnl_points` are
  `NULL` while a live trade is still open.
- `golden_master_backtest_runs`: one row per `backtest` CLI invocation,
  aggregate stats over the whole range (only ever populated by `backtest`,
  not by live polling).
- `golden_master_daily_levels`: one row per trading day — yellow1/yellow2/
  blue1/blue2 + ce_strike/pe_strike, written by `compute_daily_levels()` /
  `store_daily_levels()` as soon as they're known, independent of whether a
  trade fired that day. Satisfies the written requirement's "chart
  requirements" (show lines + strikes with labels) via the dashboard's
  "Today's levels" table rather than literal candlestick charting, which
  this text-based ops dashboard has never done and won't start doing here.

## Live trade lifecycle (`golden_master/live.py`)

Reuses `backtest.py::backtest_range()` — the same tested resolution logic —
called with `treat_last_day_as_incomplete=True` so a signal on "today" (the
last day present in the loaded window) that hasn't hit SL/target yet is left
open (`exit_reason=None`) instead of being EOD-squared-off, since the day
genuinely isn't over. Each poll cycle (`run_live_cycle`):

1. `ingest.py` fetches a fresh 5-day 5-min window for `GM_SYMBOL` (default
   `^NSEI`) and upserts into `five_min_bars`.
2. Loads the last `LOOKBACK_DAYS` (5) days of bars from the DB into a plain
   DataFrame and re-runs `backtest_range(..., treat_last_day_as_incomplete=True)`
   over the whole window from scratch (the 15-min HA levels are derived from
   this same DataFrame — see the dual-timeframe section above).
3. Upserts the resulting trades by `(trade_date, signal_type, direction)`.
   Brand-new rows trigger exactly one Discord alert; rows that flip from
   open to resolved are updated silently (no re-alert).
4. `compute_daily_levels()` / `store_daily_levels()` snapshot the day's
   reference lines + strikes for the dashboard, independent of step 3.

Re-deriving the whole window from scratch every poll — rather than holding
a `SignalEngine` in memory across polls — means a container restart loses
nothing: the DB is the source of truth, matching the rest of this project's
design (idempotent jobs, invariant 6 in `CLAUDE.md`).

## CLI (`golden_master/main.py`)

```
python -m golden_master.main init-db
python -m golden_master.main backtest <csv-path>   # columns: timestamp,open,high,low,close
python -m golden_master.main ingest                 # one manual intraday fetch
python -m golden_master.main live-once               # one manual poll cycle
python -m golden_master.main run                     # scheduler: polls ~every 5 min,
                                                       # 9:01-15:56 IST, Mon-Fri
```

Via Docker: `docker compose run --rm golden-master python -m golden_master.main backtest /data/bars.csv`
(mount or copy your CSV into the `./data-golden-master` host directory first).

## Follow-ups (not built yet)

- Backfilling `five_min_bars` from a CSV import too (currently `backtest`
  reads a CSV directly into `backtest_range()` without persisting it) — would
  let a manually-sourced deep history and the live yfinance feed share one
  table, the way `app/portfolio.py` and `app/backtest.py` share `price_bars`
  in the main engine.
- A second, longer-history intraday data source once/if yfinance's ~60-day
  cap proves limiting for backtesting recent strategy performance.
- The 4 trades recorded in production before the 2026-07-18 rework used the
  old 15-min-HA-detection logic — historical artifacts of the prior design,
  not wrong, but not directly comparable to trades produced after this
  change. No migration/relabeling planned; flagged here for anyone auditing
  `golden_master_trades` history later.


# Manual run
Force one poll cycle right now, regardless of schedule/market hours,
  #    to test the whole pipeline immediately without waiting:
  docker exec golden-master python -m golden_master.main live-once