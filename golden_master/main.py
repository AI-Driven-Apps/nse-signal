"""Golden Master entrypoint.

  python -m golden_master.main init-db              create tables
  python -m golden_master.main backtest <csv-path>   replay historical 15-min
                                                      bars through the engine
  python -m golden_master.main run                   idle placeholder — keeps
                                                      the container alive until
                                                      a live data feed exists

No live intraday data source is wired up yet (deliberately deferred — see
docs/golden_master_spec.md). `backtest` reads bars from a CSV you supply
(columns: timestamp,open,high,low,close — timestamp anything pandas can
parse, ascending, one or more trading days, each day's first row = that
day's session open) so the strategy engine is genuinely testable against
real historical data today, independent of that decision.
"""
import logging
import sys
import time

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("golden_master.main")


def run_backtest_csv(csv_path: str) -> None:
    from .backtest import backtest_range, store_backtest
    from .db import session_scope

    df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
    df = df.sort_index()
    log.info("Loaded %d bars from %s (%s to %s)", len(df), csv_path, df.index[0], df.index[-1])

    trades = backtest_range(df)
    with session_scope() as s:
        run = store_backtest(s, trades, df.index[0].date(), df.index[-1].date())
        log.info("Backtest complete: %d trades, win_rate=%.1f%%, net=%.1f points",
                 run.n_trades, run.win_rate * 100, run.net_points)


def run_idle() -> None:
    log.warning(
        "No live data source configured yet — idling. Use "
        "`docker compose run --rm golden-master python -m golden_master.main "
        "backtest <csv-path>` to backtest against historical data in the "
        "meantime. See docs/golden_master_spec.md."
    )
    while True:
        time.sleep(3600)


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
    elif cmd == "run":
        run_idle()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
