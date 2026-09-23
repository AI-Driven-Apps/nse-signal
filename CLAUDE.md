# CLAUDE.md — nse-signal (NSE strategy backtesting + signal system)

## What this project is

A personal, single-user NSE equity **paper-trading** system running a 6-month
strategy evaluation. Nightly EOD ingestion → SQLite (SQLAlchemy) → strategy
engine → Discord buy/sell alerts. Phase 1 (pipeline core) is complete and
deployed via Docker Compose on a Raspberry Pi. Phases 2 (dashboard) and 3
(cloud migration) are described below.

**This is paper trading only. Never add real-order execution or broker API
integration. All user-facing surfaces (UI, Discord) must carry a
"not financial advice" note.**

## Current state (Phase 1 — done, do not regress)

```
app/
  config.py          env-driven config (DATABASE_URL, DISCORD_WEBHOOK_URL, ...)
  db.py              engine, session_scope(), settings helpers
  models.py          full schema incl. Phase 2 tables (positions, watchlist, users)
  universe.py        Nifty 50 (.NS) list — get_universe()
  data_provider.py   DataProvider ABC + YFinanceProvider
  ingest.py          incremental EOD ingestion
  backtest.py        vectorized backtester (CAGR, max DD, Sharpe, win rate)
  signals.py         live signal engine: last-bar 0→1 = BUY, 1→0 = SELL, deduped
  notify.py          Discord webhook embeds
  main.py            CLI (init-db | ingest | signals | backtest | set-strategy | run)
  strategies/        base.Strategy ABC, sma_cross, rsi_reversion, registry
docker-compose.yml   ./data volume, mem_limit 512m, TZ Asia/Kolkata

golden_master/       separate subsystem, own container/DB/backtester — an
                      intraday Nifty options strategy, deliberately outside
                      invariant 5 below. See docs/golden_master_spec.md.

app/dashboard.py     Market Dashboard — separate subsystem, own Streamlit
  app/dhan_client.py       process/container, deliberately outside invariant 7
  app/market_status.py     below. Read-only Dhan live quotes + NSE/BSE status,
  app/instrument_master.py NOT the Phase 2 dashboard above. See "Market
  app/scanner.py           Dashboard" section below.
  app/components.py
```

Scheduler (IST): ingest + signals Mon–Fri 18:30; universe backtest Sun 10:00.

## Non-negotiable invariants

1. **`Strategy.positions(df)` is the single source of truth.** The same 0/1
   position series drives backtests AND live signals. Never fork the logic.
   New strategies: subclass `Strategy`, return a position series with **no
   lookahead**, register in `strategies/__init__.py`. Nothing else changes.
2. **No lookahead bias.** Backtests trade on the *next* bar (`shift(1)`).
   Keep transaction costs (currently 0.1%/side) in every backtest.
3. **SQLAlchemy only — no raw SQL in app code.** The Phase 3 DB swap must
   remain a `DATABASE_URL` change plus Alembic migrations.
4. **Market data goes through `DataProvider`.** yfinance is unofficial and
   breaks; never call `yfinance` outside `data_provider.py`.
5. **NSE cash market, long-only.** No shorting, no F&O, no intraday. This
   applies to `app/` — the cash-equity engine — only. `golden_master/` is a
   deliberate, isolated exception (its own container, own DB, own
   backtester, no shared code with `app/`): an intraday Nifty options
   strategy, confirmed with the project owner. See docs/golden_master_spec.md.
6. **Idempotent jobs.** Ingest and signal runs must be safe to re-run
   (dedup exists — preserve it).
7. **Pi budget.** Single container, 512 MB cap, ARM64. No Node build step,
   no heavy deps (no bokeh/plotly server-side, no Celery/Redis). It coexists
   with a YouTube pipeline on the same Pi. `dashboard` (Market Dashboard,
   Streamlit) is a deliberate, isolated exception on its own 400MB container —
   same pattern as `golden_master` in invariant 5. Check total free Pi RAM
   across all three containers, not just one cap, before adding a fourth.
8. **Secrets in env only.** `.env` is gitignored; never hardcode webhook
   URLs, keys, or connection strings.

---

## Phase 2 requirements — dashboard (build next)

**Stack:** FastAPI + Jinja2 templates + HTMX + Chart.js (CDN). Server-rendered,
same container, one new port (8000) exposed on the Pi/LAN only.

### 2.1 Auth — implemented, see app/web/
- Single user. `users` table exists; hash passwords with **bcrypt directly**
  (not passlib — its CryptContext self-test breaks under bcrypt>=4.1, a known,
  unresolved compat bug in an effectively unmaintained library), signed
  session cookie (`itsdangerous`), HttpOnly + SameSite=Lax.
- First-boot bootstrap: create user from `ADMIN_USER` / `ADMIN_PASSWORD` env
  vars if the table is empty, then require login for every route.
- Rate-limit the login endpoint (simple in-memory counter is fine, e.g.
  5 attempts / 15 min per IP).

### 2.2 Overview page
- Metric cards: **total investment** (sum of open positions cost),
  **current value**, **unrealized P/L**, **realized P/L** (from `positions`).
- **Fear & greed badge** — implemented, see `app/mood.py` (0–100, Fear <40 /
  Neutral 40–60 / Greed >60). Composite of:
  - India VIX level vs its 90-day range (inverted),
  - Nifty 50 close vs its 125-day SMA (momentum),
  - universe advance/decline ratio over the last 5 sessions.
  `^NSEI`/`^INDIAVIX` ingested via `app/ingest.run_ingest_indices()` (a
  separate indices list, `app/universe.get_indices()` — not in the tradable
  universe); daily score stored in `market_mood`, upserted by date.
- **Top gainers and losers** — implemented (`app/web/data.top_movers`): top 5
  each by 1-day % change across the universe, from `price_bars`.
- Equity curve chart (Chart.js) from portfolio snapshots — implemented.

### 2.3 Paper portfolio + snapshots — implemented, see app/portfolio.py
- Nightly step after signals: apply signals to the paper portfolio. Sizing is
  **% of current equity** (`ALLOC_PCT`, not a fixed amount — a deliberate
  deviation from this doc's original "e.g. CAPITAL_PER_TRADE" suggestion,
  decided with the project owner), capped at `MAX_POSITIONS` concurrent
  positions. SELL closes the position and books `realized_pnl`. Idempotent via
  a `paper_cursor` settings watermark (highest applied `Signal.id`) — see
  docs/phase2_specs.md for why position-state guards alone aren't enough.
- `portfolio_snapshots` table: one row per day, upserted by date. Daily P&L =
  `equity[t]-equity[t-1]` telescopes, so any period's P&L is just
  `equity_end - equity_start` — the basis for §2.4.

### 2.4 Statistics tabs — implemented, see app/web/data.period_stats + /stats
- Daily / Weekly / Monthly / Quarterly / Yearly returns and P/L, computed in
  Python (pandas resample) over `portfolio_snapshots` rather than a raw SQL
  aggregation — no new state, no caching layer, and the telescoping identity
  above makes the aggregation trivial and exact regardless of gaps.

### 2.5 Strategy switcher — implemented, see /strategy
- Dropdown listing `STRATEGIES` keys; selecting writes
  `settings.active_strategy`. Shows, per strategy, aggregate backtest metrics
  from the **most recent** `backtest_universe()` run only (identified by max
  `end_date` per strategy — every symbol in one run shares the same end_date
  — not an all-time average, which would blur weekly re-runs together). A
  "run backtest now" button enqueues `backtest_universe()` on a plain daemon
  thread (`app/web/tasks.py`) — one at a time, never blocks the request.

### 2.6 Watchlist + suggestions — implemented, see /watchlist
- Watchlist CRUD (add by symbol, `.NS` auto-appended + `<datalist>`
  autocomplete from `stocks`, remove, star — `WatchlistItem.starred` added to
  the model). Rows show last close, 1-day change, and current strategy
  position state (holding / flat).
- "Suggested stocks": ranks the universe by 0.5 × 6-month momentum
  + 0.5 × latest backtest Sharpe for the active strategy — **both terms
  min-max normalized to [0,1] before blending** (a deviation from this doc's
  literal formula: raw momentum is a % return and raw Sharpe a unitless
  ratio on a very different scale, so blending them unnormalized would let
  whichever has the larger numeric range dominate the score). Needs a
  backtest to exist for the active strategy; empty otherwise. Labeled
  *"Ranked by your active strategy — not advice."*

### 2.7 Signals page — implemented, see /signals
- Table of recent signals (date, symbol, action, price, strategy,
  Discord-sent status) with a manual "re-send to Discord" per row.

### Phase 2 acceptance criteria
- All routes behind auth; `curl` without a session gets 302 → /login.
- Dashboard loads < 1 s on the Pi with 50 stocks × 2 years of bars.
- `pytest` passes: strategy no-lookahead test (positions at bar *t* must not
  change when future bars are appended), backtester metric sanity on
  synthetic data, snapshot aggregation correctness.
- `docker compose up -d` is still the only deploy step.

---

## Phase 3 requirements — cloud migration (later)

- **Railway**: deploy the same image; web process + scheduler process
  (or keep single process). Health-check endpoint `/healthz`.
- **Supabase Postgres**: add `psycopg2-binary` + **Alembic**; generate a
  baseline migration from current models; `DATABASE_URL` swap only.
- **Supabase Auth** replaces local login (JWT verification middleware);
  keep local auth behind a `AUTH_MODE=local|supabase` flag so the Pi still
  works standalone.
- **Security checklist (must all hold before exposing publicly):**
  HTTPS only; secrets in Railway env vars; Supabase RLS enabled on all
  tables; login + API rate limiting; Discord webhook never sent to the
  client; CSP + secure cookie flags; dependency pinning; daily DB backup
  (Supabase PITR or pg_dump cron); no CORS wildcard.

---

## Market Dashboard — Dhan + NSE/BSE monitor (separate subsystem)

**Not the same thing as the Phase 2 dashboard above.** Two different UIs
answer to "dashboard" in this repo:
- Phase 2 dashboard = the FastAPI + Jinja2 site on **:8000**, `docker compose
  up` service `nse-signal`, behind login, the "site" a user browses day to
  day (routes in `app/web/app.py`).
- **Market Dashboard** = a separate **Streamlit** app on **:8501**, service
  `dashboard` in `docker-compose.yml`, **no login of its own** — LAN only,
  same as the main site's own port. Live Dhan equity/F&O quotes, NSE/BSE
  market status, option chain. Read-only: no order placement, ever.

It was added without a compose service, without the `market_status.py`
module its own imports required, and without a nav link — so it never
actually ran. Fixed 2026-09-23: `dashboard` service added to
`docker-compose.yml` (mounts `./data:/data:ro` — same SQLite DB as
`nse-signal`, read-only; token + scrip-master cache persist in the
`dashboard-data` named volume so a restart doesn't force a fresh Dhan token
paste), `app/market_status.py` written (NSE status via `nsepython`; BSE has
no equivalent live source so it's approximated from NSE/BSE's shared cash
market hours — 09:15–15:30 IST Mon–Fri — swap in a real feed if one turns
up), `STRATEGY_REGISTRY` wired to the real `app.strategies.STRATEGIES`
registry, and the Signals tab now reads already-computed rows via
`app.web.data.recent_signals()` instead of recomputing positions itself
(keeps invariant 1 — `Strategy.positions(df)` stays the one call site that
matters, the dashboard only displays what the nightly engine already ran).
Nav link added to `base.html` (opens :8501 in a new tab).

- `DHAN_CLIENT_ID` env var (long-lived) goes in `.env`; the Dhan **access
  token** itself expires every 24h with no refresh token for individual
  accounts — paste a fresh one into the dashboard's own sidebar daily, it is
  never stored in `.env`.
- No auth on :8501 is a known, accepted gap for a LAN-only single-user box —
  don't add real-order execution or expose this port publicly without
  revisiting that.
- `app/dashboard.py`'s own sibling imports (`dhan_client`, `market_status`,
  `instrument_master`, `scanner`, `components`) are bare imports — they rely
  on being run via `python -m streamlit run app/dashboard.py` from the repo
  root (the compose service's `command`), which is why the file also does an
  explicit `sys.path.insert` for the `app.*` imports it needs (`app.db`,
  `app.strategies`, `app.web.data`) rather than assuming package context.

---

## Working conventions for Claude Code

- Python 3.12, PEP 8, type hints on public functions. Keep functions small.
- Logging via the stdlib `logging` module (pattern already in place) —
  no print statements.
- Add/keep tests under `tests/` (pytest). Every new strategy needs a
  no-lookahead test and a metrics smoke test on synthetic data.
- New deps: justify in the PR/commit message; prefer pure-Python wheels
  (ARM64 build times on the Pi are real). Pin versions in requirements.txt.
- Migrations: once Alembic is added, never edit applied migrations.
- Commit style: `phase2: <component>: <what>` (e.g. `phase2: portfolio: nightly snapshot job`).
- When touching `signals.py` or `backtest.py`, re-verify invariants 1, 2,
  and 6 explicitly and say so in the commit message.
- UI copy: sentence case, concise; every page footer includes
  "Paper trading · not financial advice".

## Suggested Phase 2 build order

1. `portfolio_snapshots` + paper-portfolio application of signals (backend only, test via CLI).
2. FastAPI skeleton + auth + base template.
3. Overview page (cards + equity chart).
4. Fear & greed job + badge.
5. Stats tabs, gainers/losers.
6. Strategy switcher, watchlist, suggestions, signals page.
7. Pi deploy, then start the 6-month evaluation clock.
