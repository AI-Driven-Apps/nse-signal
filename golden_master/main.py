"""Golden Master entrypoint.

  python -m golden_master.main init-db              create tables
  python -m golden_master.main backtest <csv-path>   replay historical 5-min
                                                      bars through the engine
  python -m golden_master.main ingest                fetch recent 5-min bars
                                                      now (manual/debug)
  python -m golden_master.main live-once             one live poll cycle now
                                                      (manual/debug)
  python -m golden_master.main run                   scheduler mode (Docker
                                                      default) — polls every
                                                      5 min during NSE hours

`backtest` also still reads bars from a CSV you supply (columns:
timestamp,open,high,low,close — ascending, one or more trading days, each
day's first row = that day's session open) for testing against data outside
yfinance's ~60-day intraday lookback, or a different symbol/source entirely.
See docs/golden_master_spec.md for the yfinance reliability caveats, the
dual-timeframe design (5-min ingested, 15-min HA derived in-memory), and the
live trade lifecycle (backtest_range's treat_last_day_as_incomplete + upsert)."""
import logging
import sys

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("golden_master.main")


def run_backtest_csv(csv_path: str) -> None:
    from .backtest import backtest_range, compute_daily_levels, store_backtest, store_daily_levels
    from .db import session_scope

    df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
    df = df.sort_index()
    log.info("Loaded %d bars from %s (%s to %s)", len(df), csv_path, df.index[0], df.index[-1])

    trades = backtest_range(df)
    with session_scope() as s:
        run = store_backtest(s, trades, df.index[0].date(), df.index[-1].date())
        store_daily_levels(s, compute_daily_levels(df))
        log.info("Backtest complete: %d trades, win_rate=%.1f%%, net=%.1f points",
                 run.n_trades, run.win_rate * 100, run.net_points)


def run_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    from .config import TZ
    from .live import run_live_cycle

    sched = BlockingScheduler(timezone=TZ)
    # Shortly after each 5-min bar close (9:15-15:30 IST), Mon-Fri, with a
    # 1-minute buffer for yfinance to publish the just-closed bar. Matches
    # the 5-min entry-detection timeframe (was 15-min before the
    # 2026-07-11 spec-alignment rework) so live alerts don't lag behind it.
    sched.add_job(run_live_cycle, "cron", day_of_week="mon-fri",
                  hour="9-15", minute="1-56/5", misfire_grace_time=120)
    log.info("Scheduler started (TZ=%s). Polling ~every 5 min, 9:01-15:56 Mon-Fri.", TZ)
    sched.start()


def main():
    from .db import init_db

    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    init_db()

    if cmd == "init-db":
        log.info("Database initialised.")
    elif cmd == "backtest":
        if len(sys.argv) < 3:
            sys.exit("Usage: python -m golden_master.main backtest <csv-path>")
        run_backtest_csv(sys.argv[2])
    elif cmd == "ingest":
        from .ingest import run_ingest
        run_ingest()
    elif cmd == "live-once":
        from .live import run_live_cycle
        run_live_cycle()
    elif cmd == "run":
        run_scheduler()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
