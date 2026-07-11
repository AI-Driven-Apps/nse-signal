# nse-signal — NSE strategy backtesting + signal engine (Phase 1)

Nightly EOD ingestion → SQLite (SQLAlchemy) → strategy engine → Discord buy/sell alerts.
Long-only, paper-trading. Universe: Nifty 50 (edit `app/universe.py`).

> **Upgrading from the old "Backstage" name?** The container and service name
> changed and the default DB filename changed from `backstage.db` to
> `nse_signal.db`. On your existing deploy, before restarting, run:
> `mv data/backstage.db data/nse_signal.db` — otherwise the app starts with
> an empty database. `docker compose down` then `docker compose up -d --build`
> to pick up the new container/service name cleanly.

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
