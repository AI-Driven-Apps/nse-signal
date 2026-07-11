"""Entrypoint.

  python -m app.main init-db          create tables + default settings
  python -m app.main ingest           fetch EOD data now
  python -m app.main signals          scan + notify now
  python -m app.main portfolio        apply signals to the paper book + snapshot
  python -m app.main mood             compute + store today's fear & greed score
  python -m app.main backtest [name]  backtest strategy across universe
  python -m app.main set-strategy X   switch active strategy
  python -m app.main run              scheduler + dashboard (Docker default)

Schedule (IST): ingest+signals Mon–Fri 18:30 (after NSE close + data settle),
weekly full backtest Sunday 10:00. `run` also serves the dashboard on
WEB_PORT (8000) in the same process/container."""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("main")


def nightly_job():
    from .ingest import run_ingest, run_ingest_indices
    from .signals import run_signals
    from .portfolio import run_portfolio
    from .mood import compute_and_store_mood
    from .db import session_scope

    run_ingest()
    run_ingest_indices()
    run_signals()
    run_portfolio()
    with session_scope() as s:
        compute_and_store_mood(s)


def run_scheduler_background():
    """Non-blocking scheduler so it can share the process with the web
    server started by `run` (CLAUDE.md: same container, one new port)."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from .config import TZ

    sched = BackgroundScheduler(timezone=TZ)
    sched.add_job(nightly_job, "cron", day_of_week="mon-fri",
                  hour=18, minute=30, misfire_grace_time=3600)
    sched.add_job(lambda: __import__("app.backtest", fromlist=["x"]).backtest_universe(),
                  "cron", day_of_week="sun", hour=10, misfire_grace_time=3600)
    sched.start()
    log.info("Scheduler started (TZ=%s). Nightly 18:30 Mon-Fri, backtest Sun 10:00.", TZ)
    return sched


def run_server():
    import uvicorn
    from .config import WEB_PORT
    from .web.app import app as web_app

    run_scheduler_background()
    log.info("Dashboard starting on port %d", WEB_PORT)
    uvicorn.run(web_app, host="0.0.0.0", port=WEB_PORT, log_level="info")


def main():
    from .db import init_db, session_scope, set_setting

    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    init_db()

    if cmd == "init-db":
        log.info("Database initialised.")
    elif cmd == "ingest":
        from .ingest import run_ingest
        run_ingest()
    elif cmd == "signals":
        from .signals import run_signals
        run_signals()
    elif cmd == "portfolio":
        from .portfolio import run_portfolio
        run_portfolio()
    elif cmd == "mood":
        from .mood import compute_and_store_mood
        with session_scope() as s:
            compute_and_store_mood(s)
    elif cmd == "backtest":
        from .backtest import backtest_universe
        backtest_universe(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "set-strategy":
        from .strategies import STRATEGIES
        name = sys.argv[2]
        if name not in STRATEGIES:
            sys.exit(f"Unknown strategy. Available: {list(STRATEGIES)}")
        with session_scope() as s:
            set_setting(s, "active_strategy", name)
        log.info("Active strategy set to '%s'", name)
    elif cmd == "run":
        run_server()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
