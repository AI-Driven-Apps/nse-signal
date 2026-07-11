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

- **Data source deferred.** No live intraday feed is wired up yet (yfinance
  15-min bars cap around 60 days of lookback and carry real delay/reliability
  caveats for live use — worse than the daily EOD case elsewhere in this
  repo). `golden_master/main.py backtest <csv>` accepts externally-sourced
  15-min OHLC bars via CSV in the meantime, so the engine is genuinely
  exercisable against real historical data today. Wiring up a live feed is a
  later, isolated follow-up (add an `IntradayDataProvider`-style module,
  mirroring `app/data_provider.py`'s `DataProvider` ABC pattern).
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
  `(symbol, timestamp)`. Not currently populated by anything (data source
  deferred) — reserved for when a live/backfill provider is added.
- `golden_master_trades`: one row per bracket trade, `source` = `backtest`
  or `live` (same table for both, filterable).
- `golden_master_backtest_runs`: one row per `backtest` CLI invocation,
  aggregate stats over the whole range.

## CLI (`golden_master/main.py`)

```
python -m golden_master.main init-db
python -m golden_master.main backtest <csv-path>   # columns: timestamp,open,high,low,close
python -m golden_master.main run                    # idle placeholder until a live feed exists
```

Via Docker: `docker compose run --rm golden-master python -m golden_master.main backtest /data/bars.csv`
(mount or copy your CSV into the `./data-golden-master` host directory first).

## Follow-ups (not built yet)

- A live intraday data provider (yfinance 15m or otherwise) + a market-hours
  polling scheduler in `golden_master/main.py run`, writing to
  `intraday_bars` and firing live Discord alerts via `notify.py`.
- Populating `intraday_bars` from CSV imports too (currently `backtest`
  reads the CSV directly into `backtest_range()` without persisting the raw
  bars) — worth doing once a live source exists, so backtest and live share
  one bar history the way `app/portfolio.py` and `app/backtest.py` share
  `price_bars` in the main engine.
