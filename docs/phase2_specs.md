# Phase 2 specs — portfolio, snapshots, fear & greed

Concrete build specs for CLAUDE.md §2.3 (paper portfolio + snapshots) and §2.2
(India fear & greed). Written 2026-07-11. Honors the invariants in CLAUDE.md
(single `positions()` source of truth, no lookahead, SQLAlchemy-only, data via
`DataProvider`, long-only, idempotent jobs, Pi budget, secrets in env).

**Decisions (confirmed with owner):** position sizing = **% of current equity**;
snapshot history = **forward-only from go-live** (no synthetic backfill).

No schema migration needed pre-Phase 3: the two new tables are created by
`Base.metadata.create_all` (SQLite adds new tables, leaves existing ones
untouched). We reuse the existing `Position` table as-is; `Signal` is unchanged.

---

## 1. Paper-portfolio state machine (§2.3)

### Config (`config.py`, env-driven)
| Env | Default | Meaning |
|-----|---------|---------|
| `STARTING_CAPITAL` | `1000000` | Initial cash (₹), applied once on the first snapshot. |
| `ALLOC_PCT` | `0.10` | Fraction of **current equity** deployed per BUY. |
| `MAX_POSITIONS` | `10` | Cap on concurrent open positions (bounds 10% × N ≤ 100%). |
| `COST_PER_SIDE` | `0.001` | Reuse backtest cost (invariant 2). |

### State
- Cash: a `settings` row `paper_cash` (authoritative running cash), initialized
  to `STARTING_CAPITAL` on first run, rewritten after each apply. Also copied
  into every snapshot for history.
- Positions: existing `Position` table. **Open** = `closed_at IS NULL`.
  **At most one open position per symbol** (enforced by the guards below).

### Fill convention (matches the backtester — invariant 1/2)
Fill at the **signal's decision-bar close** = `Signal.price`. In the backtester
`pos = positions(df).shift(1)`, so a position decided on bar *t*'s close earns
the *t → t+1* move — i.e. the effective fill is bar *t*'s close. Applying paper
fills at `Signal.price` therefore makes paper equity track the backtest with no
extra lag. Costs of `COST_PER_SIDE` are charged on notional at both entry and
exit. Fractional quantities are allowed (paper only).

### Algorithm — `apply_signals(session, active_strategy)` (implemented in `app/portfolio.py`)
Runs in the nightly job **after** `run_signals()`. Processes only signals with
`id > paper_cursor` (a `settings` watermark — see below), in ascending
`(date, action)` order with SELL before BUY (so freed cash is redeployable):

```
cash   = float(get_setting("paper_cash", STARTING_CAPITAL))
cursor = int(get_setting("paper_cursor", "0"))
for sig in signals(active_strategy) where id > cursor, order by date asc, action desc:   # SELL<BUY
    cursor = max(cursor, sig.id)
    open_pos = Position(stock_id=sig.stock_id, closed_at IS NULL)
    if sig.action == "SELL":
        if not open_pos: continue                    # already flat → no-op
        proceeds = open_pos.qty * sig.price
        exit_fee = proceeds * COST_PER_SIDE
        cash += proceeds - exit_fee
        entry_cost = open_pos.qty * open_pos.avg_price
        entry_fee  = entry_cost * COST_PER_SIDE
        open_pos.closed_at   = now()
        open_pos.exit_price  = sig.price
        open_pos.realized_pnl = (proceeds - exit_fee) - (entry_cost + entry_fee)
    else:  # BUY
        if open_pos: continue                         # already holding → no-op
        if count(open positions) >= MAX_POSITIONS: log.info("skip: max positions"); continue
        equity = cash + sum(p.qty * latest_close(p) for p in open positions)   # mark-to-market
        gross  = ALLOC_PCT * equity
        fee    = gross * COST_PER_SIDE
        if cash < gross + fee: log.info("skip: insufficient cash"); continue
        cash -= gross + fee
        add Position(stock_id, qty=gross/sig.price, avg_price=sig.price, opened_at=now())
set_setting("paper_cash", cash)
set_setting("paper_cursor", cursor)
```

`latest_close(p)` = most recent `PriceBar.close` for that stock with
`date <= today` (invariant 4 — from the DB, no API call).

### Idempotency (invariant 6) — cursor watermark, not state guards alone
**Correction from the original draft of this spec:** position-state guards
(`if open_pos: skip`) are NOT sufficient alone. After a BUY→SELL round-trip
closes a position, a state guard sees "no open position" and would treat a
replayed BUY signal as fresh, re-opening it. The fix: a `paper_cursor` setting
(highest `Signal.id` already applied) means re-running the job only looks at
signals newer than the cursor — already-applied signals, whatever their
current position state, are never revisited. Still zero schema migration
(cursor lives in the existing `settings` table); the state guards remain as a
second line of defense (e.g. a hand-edited signal replay).

**Caveat (catch-up only):** for backfilled/gap signals, `equity` used for
sizing is *today's* mark-to-market, not the equity as of the signal's date. In
the normal forward-only path signals apply the same day, so this is exact;
revisit if gaps become common.

### Tests unskipped (`tests/test_phase2_todo.py`) — all passing
- `test_daily_snapshot_written_after_signals`, `test_period_stats_aggregate_correctly`,
  `test_realized_pnl_booked_on_sell`: BUY→SELL closes the position with
  `realized_pnl = (exit·qty − exit_fee) − (entry·qty + entry_fee)`.

---

## 2. `portfolio_snapshots` (§2.3 / §2.4)

### New table
```
portfolio_snapshots
  date              Date  PK           # one row per day, unique → idempotent
  cash              Float
  invested          Float              # Σ open positions cost basis (qty·avg_price)
  market_value      Float              # Σ qty · that-day close
  equity            Float              # cash + market_value
  realized_pnl_cum  Float              # Σ realized_pnl of all closed positions to date
  unrealized_pnl    Float              # market_value − invested
  created_at        DateTime
```

### Snapshot job — `write_snapshot(session, run_date)`
Runs nightly **after** `apply_signals()`. **Upsert by `date`** (update in place
if the row exists) so re-running the job never creates a second row.

- `cash` = `paper_cash` setting.
- For each open position, value at `latest_close(p) with date <= run_date`
  (carry-forward covers holidays / missing bars). `invested` = Σ cost basis.
- `equity = cash + market_value`; `unrealized = market_value − invested`.
- `realized_pnl_cum` = Σ `realized_pnl` over closed positions.

### Bootstrap (forward-only, per decision)
If no snapshot rows and no open positions exist, the first row is all cash:
`cash = equity = STARTING_CAPITAL`, everything else 0. **The 6-month evaluation
clock starts on this date.** No synthetic history is generated.

### Period stats (§2.4) — SQL over snapshots, no new state, no cache
Daily P&L = `equity[t] − equity[t-1]` (first day vs `STARTING_CAPITAL`). Because
equity differences telescope, **any period's P&L = sum of the daily P&L it
covers = `equity_end − equity_start`** — this is exactly what the skipped
aggregation test asserts. Expose per period (D/W/M/Q/Y): P&L (₹) and
return % = `equity_end / equity_start − 1`.

### Tests to unskip
- `test_daily_snapshot_written_after_signals`: one row/day; re-run is idempotent.
- `test_period_stats_aggregate_correctly`: period P&L == Σ covered daily P&L.

---

## 3. India fear & greed badge (§2.2)

**Do not use CNN's US index.** Composite computed for the Indian market, stored
daily so it is chartable. Range 0–100, higher = greedier. Bands (CLAUDE.md):
**Fear < 40, Neutral 40–60, Greed > 60.**

### Data (invariant 4 — via `DataProvider`, no ad-hoc yfinance)
Ingest `^NSEI` (Nifty 50) and `^INDIAVIX` into `stocks`/`price_bars` as a
separate **indices** list (not part of the tradable `get_universe()`), reusing
the existing full-refetch ingest. The mood job then reads everything from the DB
— zero extra API calls at compute time (also how gainers/losers work).

### New table
```
market_mood
  date               Date  PK
  vix_score          Float          # 0–100
  momentum_score     Float
  adv_decline_score  Float
  composite          Float
  label              String(8)      # Fear | Neutral | Greed
  created_at         DateTime
```

### Components (each → 0–100, higher = greed)
1. **VIX (inverted)** — `vix_t` = latest `^INDIAVIX` close; `[min,max]` over the
   trailing `VIX_WINDOW=90` sessions.
   `vix_score = clamp(100 · (1 − (vix_t − min)/(max − min)), 0, 100)`;
   if `max == min`, `50`. (Low VIX → high score.)
2. **Momentum** — `dev = (nifty_close − SMA(MOM_SMA=125)) / SMA`.
   `momentum_score = clamp(50 + (dev / MOM_BAND) · 50, 0, 100)`, `MOM_BAND=0.10`
   (±10% → 0/100, flat → 50).
3. **Advance/decline** — over the last `AD_WINDOW=5` sessions, fraction of the
   universe whose `close_t > close_{t−5}`.
   `adv_decline_score = 100 · advancers / (advancers + decliners)` (ignore
   unchanged / missing).

`composite = Σ wᵢ·scoreᵢ / Σ wᵢ`, weights `W_VIX = W_MOM = W_AD = 1` (equal,
env-tunable). **Missing data:** drop any component that can't be computed and
renormalize the remaining weights; if fewer than 2 components are available,
skip writing that day's row (log a warning).

### Job
Nightly **after** ingest (needs fresh index bars). Upsert by `date`
(idempotent). Store the three component scores too, for the history chart.

### Config env
`NIFTY_SYMBOL=^NSEI`, `VIX_SYMBOL=^INDIAVIX`, `VIX_WINDOW=90`, `MOM_SMA=125`,
`MOM_BAND=0.10`, `AD_WINDOW=5`, `W_VIX/W_MOM/W_AD=1`.

### Tests (add)
Each component is a pure function of a price series → unit-test on synthetic
data (rising series → high momentum; flat VIX range → 50; known adv/decline
counts → exact ratio); composite renormalization when a component is missing.

---

## Build order (unchanged from CLAUDE.md §"Suggested Phase 2 build order")
1. This spec's §1 + §2 (portfolio + snapshots), backend + CLI, unskip the 3
   Phase-2 tests. 2. FastAPI skeleton + auth. 3. Overview cards + equity chart.
4. This spec's §3 (fear & greed). 5. Stats tabs + gainers/losers. 6. Switcher /
watchlist / suggestions / signals page. 7. Pi deploy → start the 6-month clock.
