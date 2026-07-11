# Golden Master — intraday options strategy (dedicated subsystem)

Design spec for the `golden_master/` package. Written 2026-07-11, porting
`Nifty_Golden_Master_LipiScript.txt` (repo root — a first-draft Pine Script
→ GoCharting Lipi Script port of an intraday Nifty index options day-trading
strategy). See that file's own header comments for the original assumptions.

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

- **Live yfinance intraday feed, added 2026-07-11.** `intraday_provider.py`
  mirrors `app/data_provider.py`'s `DataProvider` ABC pattern
  (`IntradayDataProvider` / `YFinanceIntradayProvider`, 15m interval). Still
  carries the caveats that motivated deferring this in the initial build:
  yfinance intraday history caps around 60 days of lookback, and real-time
  index data can lag/be unreliable — `ingest.py` and `live.py` both fail soft
  (log a warning, skip the cycle) on any fetch error rather than crashing the
  poll loop, matching `app/ingest.py`'s "one bad fetch should not kill the
  run" convention. `golden_master/main.py backtest <csv>` still works
  unchanged for testing against data outside that 60-day window or a
  different source entirely.
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

The script is an admitted first draft; these rules were extracted carefully
and are each covered by a test in `golden_master/tests/test_engine.py`:

1. **Rejection is exclusive per day, in either direction.** `rejection_found`
   gates both `bullRejection` and `bearRejection` — once any rejection fires,
   no further rejection *or* blue-line fallback can fire that day.
2. **Blue-line fallback is independent per direction.** It only checks its
   own `buy_done`/`sell_done` flag and never sets `rejection_found`. A day
   with *no* rejection can legitimately fire both a bull *and* a bear
   blue-breakout (a whipsaw day) — intentional, not a bug.
3. **Entry prices**: rejection = the signal candle's HA high (bull) / HA low
   (bear); blue-breakout = `upperBlue + 3` / `lowerBlue - 3` (a 3-point
   buffer past the line, `GM_BLUE_BREAKOUT_BUFFER`).
4. **"No touch" conditions**: a candle must not wick into the zone it's
   rejecting/breaking (`ha_low > upper`, not just `ha_close > upper`).
5. **Time gates**: rejection search starts 9:30 IST by default
   (`GM_REJECTION_START_HOUR/MINUTE`), blue-line fallback only activates
   9:45 onward (`GM_FALLBACK_START_HOUR/MINUTE`) *and only if no rejection
   has fired*.
6. **Daily reset**: only `buy_done`/`sell_done`/`rejection_found` reset on a
   new day. The four reference lines (yellow1/yellow2/blue1/blue2) persist
   across day boundaries, overwritten only when a bar at their specific
   time-of-day arrives — this matches the source script's static-variable
   semantics and is correct given rejection/fallback start after those
   times have already updated same-day.

### Porting decision: detection is HA-based throughout

The script pulls 15-min Heikin-Ashi data explicitly for its reference lines
(`request.security(..., heikinashi=true)`), but its rejection/breakout
*detection* conditions use bare `open`/`high`/`low`/`close` with no HA
qualifier — ambiguous whether that means the chart's own (possibly non-HA)
candles or the same HA series. Given the strategy is titled and documented
entirely around Heikin-Ashi, this port treats detection as HA-based
throughout too, for internal consistency (`golden_master/engine.py`
operates on a single stream of `HABar`s for both the lines and detection).
Revisit here first if live behavior needs to differ from this.

One place this port intentionally does NOT use HA: **SL/target execution**
(`golden_master/backtest.py::backtest_range`) checks the *raw* bar's
high/low, not the HA high/low — a real stop/target order fills against
actual market price, not a synthetic smoothed HA price.

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

- `intraday_bars`: raw OHLC + precomputed HA columns, unique on
  `(symbol, timestamp)`. Populated by `ingest.py` (full-window refetch +
  upsert, mirroring `app/ingest.py`'s pattern) — see the HA-seed note below
  for why the fetch window is a full month, not just the last few days.
- `golden_master_trades`: one row per bracket trade, `source` = `backtest`
  or `live` (same table for both, filterable). Live trades are upserted by
  `(trade_date, signal_type, direction)` — inserted once when a signal first
  fires (source of a single Discord alert), then updated in place once a
  later poll resolves the SL/target/EOD outcome. `exit_*`/`pnl_points` are
  `NULL` while a live trade is still open.
- `golden_master_backtest_runs`: one row per `backtest` CLI invocation,
  aggregate stats over the whole range (only ever populated by `backtest`,
  not by live polling).

## Live trade lifecycle (`golden_master/live.py`)

Reuses `backtest.py::backtest_range()` — the same tested resolution logic —
called with `treat_last_day_as_incomplete=True` so a signal on "today" (the
last day present in the loaded window) that hasn't hit SL/target yet is left
open (`exit_reason=None`) instead of being EOD-squared-off, since the day
genuinely isn't over. Each poll cycle (`run_live_cycle`):

1. `ingest.py` fetches a fresh 1-month 15-min window for `GM_SYMBOL`
   (default `^NSEI`) and upserts into `intraday_bars`.
2. Loads the last `LOOKBACK_DAYS` (5) days of bars from the DB into a plain
   DataFrame and re-runs `backtest_range(..., treat_last_day_as_incomplete=True)`
   over the whole window from scratch.
3. Upserts the resulting trades by `(trade_date, signal_type, direction)`.
   Brand-new rows trigger exactly one Discord alert; rows that flip from
   open to resolved are updated silently (no re-alert).

Re-deriving the whole window from scratch every poll — rather than holding
a `SignalEngine` in memory across polls — means a container restart loses
nothing: the DB is the source of truth, matching the rest of this project's
design (idempotent jobs, invariant 6 in `CLAUDE.md`).

**Heikin-Ashi seed note:** HA is a recursive, continuous transform (see
`heikin_ashi.py`) whose "seed" bar is the first bar of whatever window got
fetched. `ingest.py` always fetches a full month (not just a few days)
specifically so that seed sits well before the bars that matter for trading
— each step halves the seed's influence, so by the time the window reaches
"today" it has decayed to negligible. A short fetch window right before
today would put the seed too close to the bars that actually drive signals.

## CLI (`golden_master/main.py`)

```
python -m golden_master.main init-db
python -m golden_master.main backtest <csv-path>   # columns: timestamp,open,high,low,close
python -m golden_master.main ingest                 # one manual intraday fetch
python -m golden_master.main live-once               # one manual poll cycle
python -m golden_master.main run                     # scheduler: polls ~every 15 min,
                                                       # 9:01-15:46 IST, Mon-Fri
```

Via Docker: `docker compose run --rm golden-master python -m golden_master.main backtest /data/bars.csv`
(mount or copy your CSV into the `./data-golden-master` host directory first).

## Follow-ups (not built yet)

- Backfilling `intraday_bars` from a CSV import too (currently `backtest`
  reads a CSV directly into `backtest_range()` without persisting it) — would
  let a manually-sourced deep history and the live yfinance feed share one
  table, the way `app/portfolio.py` and `app/backtest.py` share `price_bars`
  in the main engine.
- A second, longer-history intraday data source once/if yfinance's ~60-day
  cap proves limiting for backtesting recent strategy performance.
