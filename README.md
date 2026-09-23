# nse-signal — NSE strategy backtesting + signal engine (Phase 1)

Nightly EOD ingestion → SQLite (SQLAlchemy) → strategy engine → Discord buy/sell alerts.
Long-only, paper-trading. Universe: Nifty 50 (edit `app/universe.py`).

> **Upgrading from the old "Backstage" name?** The container and service name
> changed and the default DB filename changed from `backstage.db` to
> `nse_signal.db`. On your existing deploy, before restarting:
> 1. `mv data/backstage.db data/nse_signal.db`
> 2. **Check your `.env` file for an explicit `DATABASE_URL=` line** — if you
>    set one up via `cp .env.example .env`, it has the old `backstage.db`
>    path hardcoded and will keep pointing there regardless of step 1 or any
>    code default change. Update it to `sqlite:////data/nse_signal.db` (or
>    remove the line entirely to fall back to the current default).
> 3. `docker compose down` then `docker compose up -d --build` to pick up
>    the new container/service name and the `.env` change cleanly.
>
> Skipping step 2 is the failure mode to watch for: the app starts fine, logs
> show no errors, but every page loads with no data — because it's silently
> reading a brand-new empty database at the old path while your real data
> sits untouched at the new one. Confirm with:
> `docker exec nse-signal-trader env | grep DATABASE_URL`.

## Deploy on the Pi

```bash
cp .env.example .env          # paste your Discord webhook URL
docker compose build
docker compose run --rm nse-signal python -m app.main ingest      # first backfill (~5 min)
docker compose run --rm nse-signal python -m app.main backtest    # baseline metrics
docker compose up -d                                             # scheduler mode
docker compose logs -f
```

Scheduler (IST): ingest + signal scan Mon–Fri 18:30, full universe backtest Sun 10:00.

## Commands

```bash
docker compose run --rm nse-signal python -m app.main signals               # scan now
docker compose run --rm nse-signal python -m app.main backtest rsi_reversion
docker compose run --rm nse-signal python -m app.main set-strategy rsi_reversion
```

## Get your Discord webhook

Discord → your server → channel settings → Integrations → Webhooks → New Webhook → Copy URL → paste into `.env`.

## Adding a strategy

1. New file in `app/strategies/` subclassing `Strategy`, implement `positions(df)` returning a 0/1 series (no lookahead!).
2. Register it in `app/strategies/__init__.py`.
3. `set-strategy <name>` — backtester, signal engine, and (later) the dashboard switcher all pick it up automatically.

## Compare strategies over your 6-month window

```sql
-- sqlite3 data/nse_signal.db
SELECT strategy, ROUND(AVG(cagr)*100,1) avg_cagr_pct,
       ROUND(AVG(max_drawdown)*100,1) avg_dd_pct,
       ROUND(AVG(sharpe),2) avg_sharpe, ROUND(AVG(win_rate)*100,1) win_pct
FROM backtest_runs GROUP BY strategy;
```

## Phase 3 migration note

Set `DATABASE_URL` to your Supabase Postgres connection string and add
`psycopg2-binary` to requirements — no code changes needed (SQLAlchemy).

**Paper trading only. Backtest results overstate live performance. Not financial advice.**

# Market Dashboard (Dhan + NSE/BSE monitor)

Read-only monitoring dashboard: NSE/BSE market status, live equity/F&O quotes
via Dhan, and buy/sell signals from your backstage strategy registry.
**No order placement** — execution stays manual by design.

## Install

Already in `requirements.txt` and baked into the Docker image — no separate
install step for `docker compose up -d`. For running it outside Docker:

```bash
pip install dhanhq streamlit streamlit-autorefresh nsepython pandas
```

## Get a Dhan access token

1. Log in at https://web.dhan.co
2. My Profile → **Access DhanHQ APIs** → Generate Access Token
3. Copy the token and your Client ID

Tokens expire **24 hours** after generation — there's no long-lived refresh
token for individual accounts, so budget for pasting a new one daily (the
sidebar has a field for this; `dhan_client.py` tracks the issue time and
tells you when it's stale rather than serving silently broken data).

## Configure

Add to your `.env` (see `.env.example`):

```
DHAN_CLIENT_ID=your_client_id
```

Then run the dashboard once and paste your access token into the sidebar —
it's cached to the path `DHAN_TOKEN_STATE` points at (the `dashboard-data`
volume in Docker; gitignore this if running it bare — it holds a live
credential).

## Strategy registry

`app/dashboard.py`'s Signals tab reads `app.strategies.STRATEGIES` and the
already-computed rows in the `signals` table via `app.web.data.recent_signals()`
— it displays what the nightly signal engine already ran, it never recomputes
`Strategy.positions(df)` itself, so that stays the single source of truth
(invariant 1) for both backtests and this view.

## Run — locally (outside Docker)

```bash
streamlit run app/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

Then open `http://<pi-ip>:8501` from any device on your network.

## Run — Docker (same compose file as the main site)

```bash
cp .env.example .env        # fill in DHAN_CLIENT_ID alongside the rest
docker compose up -d --build
```

This starts it as the `dashboard` service alongside `nse-signal` and
`golden-master`, on the same network, same `docker-compose.yml`. Open
`http://<pi-ip>:8501`, or click "Market Dashboard" from the main site's nav
(`http://<pi-ip>:8000`) — that link just opens :8501 in a new tab; the
Streamlit app has no login of its own. Token state persists in the
`dashboard-data` named volume, so a container restart doesn't force a fresh
token paste — only the 24h Dhan expiry does.

**Memory**: capped at 400MB in `docker-compose.yml` (the rest of this system
targets a 512MB ceiling per container) — Streamlit + pandas + dhanhq fits
comfortably under that at rest, but watch it if your watchlist or option-chain
pulls grow large. Running all three containers together means checking the
Pi's *total* free RAM, not just one container's cap — `docker stats` will
show if you're tight.

## Memory note (512MB Pi)

- Poll quotes on a timer (e.g. every 15–30s during market hours only), not a
  tight loop — Streamlit reruns the whole script on each interaction/refresh.
- Keep the watchlist to symbols you're actually tracking; Dhan's quote API
  accepts up to 1000 instruments per call but there's no reason to pull that
  many on a memory-capped box.
- If you outgrow Streamlit's memory footprint, the same `dhan_client.py` /
  `market_status.py` modules work fine behind a lighter Flask+HTMX front end.

## Scanner features (matching the reel you shared)

- **Overview tab**: advances/declines/unchanged, near-high/near-low counts,
  and a 0-100 strength gauge — computed from Dhan's own quotes across the
  F&O universe (~210 stocks) or your custom watchlist, chunked to respect
  the ~1000-instrument-per-call limit.
- **Full Scanner tab**: sortable grid with a day-range bar (where LTP sits
  between the day's high/low) and an OI-range bar (colored red/green,
  Indian lakh/crore formatting) per row, "Show N" and sort-by controls.
- **Option Chain tab**: ATM-tagged CE/PE rows for NIFTY/BANKNIFTY/FINNIFTY/
  MIDCPNIFTY/SENSEX, pulling OI, IV, volume and LTP per strike from Dhan's
  option-chain endpoint.
- **Auto-refresh**: sidebar slider (5-60s), same idea as the reel's "poll
  7849ms / next in 8s" — via `streamlit-autorefresh`.
- **Trend sparklines**: `components.sparkline_svg` renders real minute-candle
  data via `DhanMarketClient.get_intraday_minute_data` — not wired into the
  scanner grid by default since pulling a full intraday series per row for
  a couple hundred rows is expensive; wire it in per-row if you want it and
  watch the Dhan call volume.

**Instrument universe**: pulled once daily from Dhan's own scrip-master CSV
(`instrument_master.py`, cached 6h in Streamlit + 20h on disk), so "F&O
stocks" reflects Dhan's actual current F&O list rather than a hardcoded one.

## Next steps once Dhan is stable

- Swap in Zerodha Kite Connect the same way if you want a second broker's
  data as a cross-check (paid API access, ₹2000/mo).
- F&O option-chain calls (`get_option_chain`) need an expiry date — call
  `get_expiry_list` first for a given underlying to get valid dates.
